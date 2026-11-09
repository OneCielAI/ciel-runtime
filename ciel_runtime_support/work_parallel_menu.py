"""Shared menu controls for the global Work parallel ledger and idle discovery."""
from .work_parallel import WorkParallelError

PRIORITY_KEYS = tuple(f"work_parallel_p{n}_limit" for n in range(1, 6))
ORPHANS_KEY = "work_parallel_orphans"
KEYS = ("work_parallel_enabled", "work_parallel_paused", *PRIORITY_KEYS,
        "work_discovery_enabled", "work_discovery_test_default_override", "work_discovery_instruction",
        "work_parallel_launches_per_minute", "work_parallel_max_rework", ORPHANS_KEY)


def orphan_summary(preview):
    counts = {}
    for row in preview["leases"]:
        counts[row["owner_state"]] = counts.get(row["owner_state"], 0) + 1
    rows = ", ".join(f"{row['lease_id'][:8]} {row['item_id']} {row['priority']} {row['owner_state']}"
                     for row in preview["leases"] if row["owner_state"] != "live")
    return " ".join(f"{k}={v}" for k, v in sorted(counts.items())) + (f" [{rows}]" if rows else "") or "none"


def release_orphans(store, value, *, proof):
    """Release dead-owner slots, or one confirmed legacy lease; live/unknown stay."""
    from .work_parallel_mcp import _orphan_preview
    preview = _orphan_preview(store)
    token = value.strip()
    if token == "apply":
        targets = [(row, {"owner_state": "dead", "observed_identity": row["observed_identity"]})
                   for row in preview["leases"] if row["owner_state"] == "dead"]
    elif token.startswith("legacy "):
        prefix = token[len("legacy "):].strip()
        matches = [row for row in preview["leases"]
                   if row["owner_state"] == "legacy_no_owner" and len(prefix) >= 8 and row["lease_id"].startswith(prefix)]
        if len(matches) != 1:
            raise WorkParallelError("legacy cleanup needs one exact lease id (at least 8 characters)")
        targets = [(matches[0], {"owner_state": "legacy_no_owner", "host_dead": True,
                                 "scope_board_missing": True, "pid_absent": True})]
    else:
        raise WorkParallelError("type apply or legacy <lease id>")
    released = []
    for row, observation in targets:
        current = store.read()
        store.orphan_release(row["lease_id"], proof=proof, observation=observation,
                             expected_version=current["version"])
        released.append(row["lease_id"])
    return released


def rows(config, language=None):
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = language or config.get("language", "en")
    try:
        store = ledger()
        p = store.read()["policy"]
        from .work_parallel_mcp import _orphan_preview
        orphans = orphan_summary(_orphan_preview(store))
    except (ValueError, OSError):
        return [(menu_text("work_parallel_unavailable", language), "__info__", "unavailable")]
    d = config.get("work_discovery", {})
    values = (str(p["enabled"]).lower(), str(p["paused"]).lower(),
              *(str(p["priority_limits"][f"P{n}"]) for n in range(1, 6)),
              str(d.get("enabled", True)).lower(), str(d.get("test_default_override", True)).lower(),
              d.get("instruction", menu_text("default_instruction", language)),
              str(p["launches_per_minute"]), str(p["max_rework"]), orphans)
    return [(menu_text(key, language), key, value) for key, value in zip(KEYS, values)]


def apply(config, key, value, save_config):
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = config.get("language", "en")
    if key not in KEYS:
        raise WorkParallelError(menu_text("work_parallel_unknown", language))
    token = str(value).strip()
    if key == ORPHANS_KEY:
        released = release_orphans(ledger(), token, proof="menu cleanup: " + token[:100])
        return [f"{menu_text(key, language)}: {len(released)}",
                *(f"  released {ident}" for ident in released), menu_text("work_parallel_saved", language)]
    def boolean():
        if token.lower() not in ("on", "off", "true", "false"):
            raise WorkParallelError(menu_text("enter_bool", language))
        return token.lower() in ("on", "true")
    if key.startswith("work_discovery_"):
        d = dict(config.get("work_discovery", {}))
        field = key[len("work_discovery_"):]
        d[field] = token if field == "instruction" else boolean()
        if field == "instruction" and (not token or len(token) > 4000):
            raise WorkParallelError(menu_text("instruction_length", language))
        config["work_discovery"] = d
    else:
        store = ledger()
        state = store.read()
        field = key[len("work_parallel_"):]
        if field in ("enabled", "paused"):
            changes = {field: boolean()}
        elif key in PRIORITY_KEYS:
            # Concurrent fork sessions for one priority; the others are unchanged.
            changes = {"priority_limits": {"P" + field[1]: int(token)}}
        elif field in ("launches_per_minute", "max_rework"):
            changes = {field: int(token)}
        else:
            raise WorkParallelError("unknown Work parallel option")
        state, _ = store.configure(changes, capability_ready=True, expected_version=state["version"])
        config["work_parallel"] = state["policy"]
    save_config(config)
    return [f"{menu_text(key, language)}: {token}", menu_text("work_parallel_saved", language)]
