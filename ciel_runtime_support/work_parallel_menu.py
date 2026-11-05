"""Shared menu controls for the global Work parallel ledger and idle discovery."""
from .work_parallel import WorkParallelError

KEYS = ("work_parallel_enabled", "work_parallel_paused", "work_parallel_max_active",
        "work_discovery_enabled", "work_discovery_test_default_override", "work_discovery_instruction",
        "work_parallel_p1_reserved", "work_parallel_launches_per_minute", "work_parallel_max_rework")


def rows(config, language=None):
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = language or config.get("language", "en")
    try:
        p = ledger().read()["policy"]
    except (ValueError, OSError):
        return [(menu_text("work_parallel_unavailable", language), "__info__", "unavailable")]
    d = config.get("work_discovery", {})
    values = (str(p["enabled"]).lower(), str(p["paused"]).lower(), str(p["max_active"]),
              str(d.get("enabled", True)).lower(), str(d.get("test_default_override", True)).lower(),
              d.get("instruction", menu_text("default_instruction", language)),
              str(p["p1_reserved"]), str(p["launches_per_minute"]), str(p["max_rework"]))
    return [(menu_text(key, language), key, value) for key, value in zip(KEYS, values)]


def apply(config, key, value, save_config):
    from .work_parallel_mcp import ledger
    from .recent_menu_text import menu_text
    language = config.get("language", "en")
    if key not in KEYS:
        raise WorkParallelError(menu_text("work_parallel_unknown", language))
    token = str(value).strip()
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
        elif field == "max_active":
            cap = int(token)
            changes = {field: cap, "p1_reserved": min(cap, state["policy"]["p1_reserved"])}
        elif field in ("p1_reserved", "launches_per_minute", "max_rework"):
            changes = {field: int(token)}
        else:
            raise WorkParallelError("unknown Work parallel option")
        state, _ = store.configure(changes, capability_ready=True, expected_version=state["version"])
        config["work_parallel"] = state["policy"]
    save_config(config)
    return [f"{menu_text(key, language)}: {token}", menu_text("work_parallel_saved", language)]
