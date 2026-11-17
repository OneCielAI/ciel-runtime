"""Shared menu controls for the global Work parallel ledger and idle discovery."""
from .work_parallel import MAX_PRIORITY_LIMIT, WorkParallelError, default_policy
from .work_setting_values import SettingValueError, integer_in, switch

PRIORITY_KEYS = tuple(f"work_parallel_p{n}_limit" for n in range(1, 6))
ORPHANS_KEY = "work_parallel_orphans"
KEYS = ("work_parallel_enabled", "work_parallel_paused", *PRIORITY_KEYS,
        "work_discovery_enabled", "work_discovery_test_default_override", "work_discovery_instruction",
        "work_parallel_launches_per_minute", "work_parallel_max_rework", ORPHANS_KEY)
# Keys whose value lives in the device ledger; the discovery keys live in the
# workspace configuration and stay readable when the ledger is not.
LEDGER_KEYS = ("work_parallel_enabled", "work_parallel_paused", *PRIORITY_KEYS,
               "work_parallel_launches_per_minute", "work_parallel_max_rework", ORPHANS_KEY)
BOOLEAN_KEYS = ("work_parallel_enabled", "work_parallel_paused", "work_discovery_enabled",
                "work_discovery_test_default_override")
# The inclusive ranges work_parallel.validate_policy accepts (the store stays
# the authority; these only phrase the hint and refuse before a write).
RANGES = {**{key: (1, MAX_PRIORITY_LIMIT) for key in PRIORITY_KEYS},
          "work_parallel_launches_per_minute": (1, 60), "work_parallel_max_rework": (0, 10)}
UNREADABLE = "unreadable"


def reconcile_text(view):
    """One line for never-launched reservation records (work_parallel_reconcile)."""
    if not view:
        return ""
    parts = []
    if view["orphan_run_count"]:
        parts.append(f"never-launched runs={view['orphan_run_count']}")
    if view["orphan_p0_intent_count"]:
        parts.append(f"never-launched P0 intents={view['orphan_p0_intent_count']}")
    if view["ambiguous_p0_intent_count"]:
        parts.append(f"P0 intents left for review={view['ambiguous_p0_intent_count']}")
    return " ".join(parts)


def orphan_summary(preview):
    counts = {}
    for row in preview["leases"]:
        counts[row["owner_state"]] = counts.get(row["owner_state"], 0) + 1
    rows = ", ".join(f"{row['lease_id'][:8]} {row['item_id']} {row['priority']} {row['owner_state']}"
                     for row in preview["leases"] if row["owner_state"] != "live")
    text = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) + (f" [{rows}]" if rows else "")
    extra = reconcile_text(preview.get("reconcile"))
    return " ".join(part for part in (text, extra) if part) or "none"


def reconcile_previewed(store, digest):
    """Retire never-launched runs and P0 intents of the exact preview the operator saw."""
    from .work_parallel import WorkParallelConflict
    from .work_parallel_reconcile import apply as reconcile
    if not isinstance(digest, str) or len(digest) != 64:
        raise WorkParallelError("reconcile needs the digest of the preview you saw")
    try:
        return reconcile(store, expected_digest=digest)
    except WorkParallelConflict as error:
        raise WorkParallelError(str(error)) from None


def orphan_preview(store=None):
    from .work_parallel_mcp import _orphan_preview, ledger
    return _orphan_preview(store or ledger())


def release_previewed(store, preview_digest, lease_id, *, proof, legacy_confirmed=False):
    """Release exactly one lease of the preview the operator saw.

    The preview digest pins the decision: if any lease, owner observation or the
    ledger version changed since, nothing is released. A dead owner is released
    on its observation; a lease from a build without owner records needs the
    person's confirmation that its session is shut down. Live or unknown owners
    are never released.
    """
    preview = orphan_preview(store)
    if not isinstance(preview_digest, str) or preview_digest != preview["digest"]:
        raise WorkParallelError("stale or invalid orphan preview; preview again")
    row = next((item for item in preview["leases"] if item["lease_id"] == lease_id), None)
    if row is None:
        raise WorkParallelError("lease is not in the previewed orphan list")
    if row["owner_state"] == "dead":
        observation = {"owner_state": "dead", "observed_identity": row["observed_identity"]}
    elif row["owner_state"] == "legacy_no_owner" and legacy_confirmed is True:
        observation = {"owner_state": "legacy_no_owner", "host_dead": True,
                       "scope_board_missing": True, "pid_absent": True}
    elif row["owner_state"] == "legacy_no_owner":
        raise WorkParallelError("a lease without an owner record needs confirmation that its session is shut down")
    else:
        raise WorkParallelError("only a positively dead owner can be released")
    state, _ = store.orphan_release(row["lease_id"], proof=proof, observation=observation,
                                    expected_version=preview["version"])
    lease = state["leases"].get(row["lease_id"])
    return {"lease_id": row["lease_id"], "item_id": row["item_id"], "priority": row["priority"],
            "owner_state": row["owner_state"], "version": state["version"],
            # A never-bound legacy reservation is removed outright.
            "state": lease["state"] if lease else "removed"}


def release_orphans(store, value, *, proof):
    """Menu entry point: ``release|legacy <lease id> <preview digest>``.

    Every choice the menu offers carries the digest of the preview it was built
    from, so the release is pinned exactly like the web operator's.
    """
    parts = str(value).split()
    if len(parts) == 2 and parts[0] == "reconcile":
        return [{"reconcile": reconcile_previewed(store, parts[1])}]
    if len(parts) != 3 or parts[0] not in ("release", "legacy"):
        raise WorkParallelError("choose a lease from the orphan list (it pins the preview you saw)")
    return [release_previewed(store, parts[2], parts[1], proof=proof, legacy_confirmed=parts[0] == "legacy")]


def orphan_choices(store=None):
    """(label, value) per releasable lease of one fresh preview."""
    preview = orphan_preview(store)
    choices = []
    view = preview.get("reconcile")
    if view and (view["orphan_run_count"] or view["orphan_p0_intent_count"]):
        choices.append((f"Retire never-launched records ({reconcile_text(view)}); a backup is written first",
                        f"reconcile {view['digest']}"))
    for row in preview["leases"]:
        if row["owner_state"] == "dead":
            choices.append((f"Release {row['lease_id'][:8]} {row['item_id']} {row['priority']} (owner gone)",
                            f"release {row['lease_id']} {preview['digest']}"))
        elif row["owner_state"] == "legacy_no_owner":
            choices.append((f"Legacy {row['lease_id'][:8]} {row['item_id']} {row['priority']}"
                            " (confirm its session is shut down)",
                            f"legacy {row['lease_id']} {preview['digest']}"))
    return preview, choices


def values(config, language=None):
    """Typed values per key. Ledger keys carry status unreadable, never vanish."""
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = language or config.get("language", "en")
    d = config.get("work_discovery", {})
    result = {
        "work_discovery_enabled": {"value": d.get("enabled", True) is not False},
        "work_discovery_test_default_override": {"value": d.get("test_default_override", True) is not False},
        "work_discovery_instruction": {"value": d.get("instruction", menu_text("default_instruction", language))},
    }
    try:
        store = ledger()
        p = store.read()["policy"]
        orphans = orphan_summary(orphan_preview(store))
    except (ValueError, OSError):
        result.update({key: {"value": None, "status": UNREADABLE} for key in LEDGER_KEYS})
    else:
        result.update({"work_parallel_enabled": {"value": p["enabled"]},
                       "work_parallel_paused": {"value": p["paused"]},
                       **{key: {"value": p["priority_limits"][f"P{n}"]} for n, key in enumerate(PRIORITY_KEYS, 1)},
                       "work_parallel_launches_per_minute": {"value": p["launches_per_minute"]},
                       "work_parallel_max_rework": {"value": p["max_rework"]},
                       ORPHANS_KEY: {"value": orphans}})
    return {key: result[key] for key in KEYS}


def rows(config, language=None):
    """Menu rows (label, key, display text); every key is always listed."""
    from .recent_menu_text import menu_text
    language = language or config.get("language", "en")
    shown = []
    for key, entry in values(config, language).items():
        value = entry["value"]
        text = (UNREADABLE if entry.get("status") == UNREADABLE else
                str(value).lower() if isinstance(value, bool) else str(value))
        shown.append((menu_text(key, language), key, text))
    return shown


def apply(config, key, value, save_config):
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = config.get("language", "en")
    if key not in KEYS:
        raise WorkParallelError(menu_text("work_parallel_unknown", language))
    token = str(value).strip()
    if key == ORPHANS_KEY:
        released = release_orphans(ledger(), token, proof="menu cleanup: " + token.split()[0][:20])
        lines = []
        for row in released:
            if "reconcile" in row:
                done = row["reconcile"]
                lines.append(f"  retired runs={done['cancelled_runs']} P0 intents={done['cancelled_p0_intents']}"
                             f" · backup {done['backup'] or 'none (nothing to retire)'}")
            else:
                lines.append(f"  released {row['lease_id']} ({row['state']})")
        return [f"{menu_text(key, language)}: {len(released)}", *lines,
                menu_text("work_parallel_saved", language)]

    def boolean(default):
        try:
            parsed = switch(token)
        except SettingValueError:
            raise WorkParallelError(menu_text("enter_bool", language)) from None
        return default if parsed is None else parsed

    if key.startswith("work_discovery_"):
        d = dict(config.get("work_discovery", {}))
        field = key[len("work_discovery_"):]
        if field == "instruction":
            if not token or len(token) > 4000:
                raise WorkParallelError(menu_text("instruction_length", language))
            d[field] = token
        else:
            d[field] = boolean(True)
        config["work_discovery"] = d
    else:
        store = ledger()
        state = store.read()
        field = key[len("work_parallel_"):]
        defaults = default_policy()
        if field in ("enabled", "paused"):
            changes = {field: boolean(defaults[field])}
        else:
            low, high = RANGES[key]
            try:
                number = integer_in(token, low, high, key)
            except SettingValueError as error:
                raise WorkParallelError(str(error)) from None
            # Concurrent fork sessions for one priority; the others are unchanged.
            changes = ({"priority_limits": {"P" + field[1]: number}} if key in PRIORITY_KEYS
                       else {field: number})
        state, _ = store.configure(changes, capability_ready=True, expected_version=state["version"])
        config["work_parallel"] = state["policy"]
    save_config(config)
    return [f"{menu_text(key, language)}: {token}", menu_text("work_parallel_saved", language)]
