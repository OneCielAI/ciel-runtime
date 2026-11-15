"""Per-priority Work-fork models (P0-P5) and the SSE side-fork model.

User requirement 2026-11-14: each priority P0-P5 has its own LLM model, the
side actor has its own model, choices come from the current provider's models
only, and the settings appear on the menu, web, REST and ciel-runtimectl.

Settings live in config.json per provider (never in the device ledger):
``work_parallel_models[provider] = {"P0".."P5": id, "side": id}``; a missing
key or ``inherit`` keeps main's model.

The main session model (user requirement 2026-11-15: change the model main
runs on, remotely, per workspace, without touching the P0-P5/side choices) is
stored apart, in ``work_main_models[provider] = id`` of the same workspace
config.json; ``inherit`` keeps the model the session was launched with. The gate
applies it to main's own requests from the next main turn. The ciel-work-gate plugin applies them in
its ``turn.step`` hook, which sees every request of a ciel-work fork, the first
one included, and names the request's model there.
"""
from __future__ import annotations

import json
from typing import Any

PRIORITIES = ("P0", "P1", "P2", "P3", "P4", "P5")
PRIORITY_KEYS = tuple(f"work_parallel_model_{p.lower()}" for p in PRIORITIES)
SIDE_KEY = "work_side_intake_model"
MAIN_KEY = "work_main_model"
MAIN_TABLE = "work_main_models"
KEYS = (MAIN_KEY, *PRIORITY_KEYS, SIDE_KEY)
INHERIT = "inherit"
SLOT = {**{key: p for key, p in zip(PRIORITY_KEYS, PRIORITIES)}, SIDE_KEY: "side"}


def _provider(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider = str(config.get("current_provider") or "")
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    pcfg = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
    return provider, pcfg


def _cached_ids(provider: str) -> list[str]:
    """Model ids the provider catalog already listed (registry, then list cache)."""
    from pathlib import Path
    from . import runtime_paths
    root = Path(runtime_paths.CONFIG_DIR)
    ids: list[str] = []
    try:
        registry = json.loads((root / "model-registry.json").read_text(encoding="utf-8"))
        entry = (registry.get("providers") or {}).get(provider) or {}
        ids.extend(str(item) for item in entry.get("models") or [] if str(item).strip())
    except (OSError, ValueError, AttributeError):
        pass
    if not ids:
        try:
            cache = json.loads((root / "model-list-cache.json").read_text(encoding="utf-8"))
            # The cache key is a JSON object naming the provider it was listed for.
            key = json.loads(cache.get("key") or "{}")
            if isinstance(key, dict) and key.get("provider") == provider:
                ids.extend(str(item) for item in cache.get("models") or [] if str(item).strip())
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    return ids


def _identity():
    from .provider_adapters import PROVIDER_ADAPTERS, PROVIDER_ALIASES, PROVIDER_LABELS
    from .provider_model_identity import ProviderModelIdentityService
    return ProviderModelIdentityService(adapters=PROVIDER_ADAPTERS, aliases=PROVIDER_ALIASES, labels=PROVIDER_LABELS)


def request_names(provider: str, model_id: str) -> tuple[str, str]:
    """The two names a Claude request can carry for ``model_id``.

    Through the router, main sends its launch alias (``ciel-runtime-<provider>-
    <slug>``) and the router resolves it; on a direct gateway path (and for
    providers that launch with plain ids) main sends the upstream id. The gate
    sends the form main's own request uses.
    """
    identity = _identity()
    return (identity.alias_for(provider, model_id),
            identity.upstream_api_model_id(provider, identity.strip_claude_context_suffix(model_id)))


def model_ids(config: dict[str, Any]) -> list[str]:
    """The current provider's models: catalog cache, custom models, the current model."""
    provider, pcfg = _provider(config)
    seen: set[str] = set()
    result: list[str] = []
    current = str(pcfg.get("current_model") or "").strip()
    for value in [current, *(str(v) for v in pcfg.get("custom_models") or []), *_cached_ids(provider)]:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def stored(config: dict[str, Any]) -> dict[str, str]:
    provider, _ = _provider(config)
    table = config.get("work_parallel_models") if isinstance(config.get("work_parallel_models"), dict) else {}
    row = table.get(provider) if isinstance(table.get(provider), dict) else {}
    return {slot: str(row.get(slot) or INHERIT) for slot in (*PRIORITIES, "side")}


def main_stored(config: dict[str, Any]) -> str:
    provider, _ = _provider(config)
    table = config.get(MAIN_TABLE) if isinstance(config.get(MAIN_TABLE), dict) else {}
    return str(table.get(provider) or INHERIT)


def value(config: dict[str, Any], key: str) -> str:
    if key == MAIN_KEY:
        return main_stored(config)
    return stored(config)[SLOT[key]]


def forced(config: dict[str, Any]) -> bool:
    """A remotely forced model (model_override) replaces every request's model."""
    from .model_override import forced_model
    return bool(forced_model(_provider(config)[1]))


def status(config: dict[str, Any], key: str, known: list[str] | None = None) -> str:
    chosen = value(config, key)
    if chosen == INHERIT:
        return "inherit"
    ids = model_ids(config) if known is None else known
    if chosen not in ids:
        return "unavailable_inherits"
    return "forced_model_overrides" if forced(config) else "active"


def set_value(config: dict[str, Any], key: str, raw: str) -> None:
    if key not in SLOT and key != MAIN_KEY:
        raise ValueError("unknown model setting: " + key)
    chosen = str(raw or "").strip()
    if chosen in ("", "default", "reset", "off"):
        chosen = INHERIT
    provider, _ = _provider(config)
    if not provider:
        raise ValueError("no current provider; choose a provider first")
    if chosen != INHERIT and chosen not in model_ids(config):
        raise ValueError("model is not in the current provider's model list: " + chosen[:200])
    if key == MAIN_KEY:
        # Its own table: the P0-P5 and side choices are never read or rewritten.
        mains = dict(config.get(MAIN_TABLE) or {})
        if chosen == INHERIT:
            mains.pop(provider, None)
        else:
            mains[provider] = chosen
        if mains:
            config[MAIN_TABLE] = mains
        else:
            config.pop(MAIN_TABLE, None)
        return
    table = dict(config.get("work_parallel_models") or {})
    row = dict(table.get(provider) or {})
    if chosen == INHERIT:
        row.pop(SLOT[key], None)
    else:
        row[SLOT[key]] = chosen
    if row:
        table[provider] = row
    else:
        table.pop(provider, None)
    if table:
        config["work_parallel_models"] = table
    else:
        config.pop("work_parallel_models", None)


def projection(config: dict[str, Any]) -> dict[str, Any]:
    """What the ciel-work-gate plugin applies: request aliases, or None for inherit.

    ``models`` holds the router alias of each slot's model, ``upstream`` the id
    a direct gateway takes, and ``ids`` the provider model id the user chose. A
    stored id no longer in the provider's list resolves to inherit (status
    ``unavailable_inherits``) instead of sending a model the provider lacks.
    """
    provider, _ = _provider(config)
    known = model_ids(config)
    chosen = stored(config)
    ids = {slot: (chosen[slot] if chosen[slot] != INHERIT and chosen[slot] in known else None) for slot in chosen}
    models: dict[str, str | None] = {}
    upstream: dict[str, str | None] = {}
    for slot, model_id in ids.items():
        try:
            models[slot], upstream[slot] = request_names(provider, model_id) if model_id else (None, None)
        except Exception:  # an unknown provider adapter: send the id itself
            models[slot] = upstream[slot] = model_id
    main_id = main_stored(config)
    main_id = main_id if main_id != INHERIT and main_id in known else None
    try:
        main_alias, main_upstream = request_names(provider, main_id) if main_id else (None, None)
    except Exception:  # an unknown provider adapter: send the id itself
        main_alias = main_upstream = main_id
    return {"provider": provider, "models": models, "upstream": upstream, "ids": ids, "forced_model": forced(config),
            # Main's own requests (gate turn.step, agentId absent); None keeps the launch model.
            "main": {"model": main_alias, "upstream": main_upstream, "id": main_id},
            "status": {key: status(config, key, known) for key in KEYS}}


__all__ = ["INHERIT", "KEYS", "MAIN_KEY", "MAIN_TABLE", "PRIORITIES", "PRIORITY_KEYS", "SIDE_KEY", "forced",
           "main_stored", "model_ids", "projection",
           "request_names", "set_value", "status", "stored", "value"]
