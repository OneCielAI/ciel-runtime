from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LmStudioRuntimeServices:
    api_base: Callable[[dict[str, Any]], str | None]
    current_model: Callable[[str, dict[str, Any]], str]
    http_json: Callable[..., Any]
    join_url: Callable[[str, str], str]
    model_list_headers: Callable[[str, dict[str, Any]], dict[str, str]]
    model_id_matches: Callable[[str, str], bool]
    positive_int: Callable[[Any], int | None]
    model_context: Callable[[dict[str, Any]], int | None]
    log: Callable[[str, str], None]


def discover_lm_studio_runtime(
    provider_config: dict[str, Any],
    services: LmStudioRuntimeServices,
    *,
    timeout: float = 3.0,
) -> dict[str, Any] | None:
    base = services.api_base(provider_config)
    if not base:
        return None
    current = services.current_model("lm-studio", provider_config)
    headers = services.model_list_headers("lm-studio", provider_config)
    v0_url = services.join_url(base, "/api/v0/models")
    try:
        data = services.http_json(v0_url, headers=headers, timeout=timeout)
        runtime = _runtime_from_v0(data, v0_url, current, services)
        if runtime is not None:
            return runtime
    except Exception as exc:
        services.log(
            "WARN",
            f"lm_studio_runtime_discovery_failed api=v0 error={type(exc).__name__}: {exc}",
        )
    v1_url = services.join_url(base, "/api/v1/models")
    try:
        data = services.http_json(v1_url, headers=headers, timeout=timeout)
        return _runtime_from_v1(data, v1_url, current, services)
    except Exception as exc:
        services.log(
            "WARN",
            f"lm_studio_runtime_discovery_failed api=v1 error={type(exc).__name__}: {exc}",
        )
        return None


def _select_model(
    items: Any,
    current: str,
    id_key: str,
    matches: Callable[[str, str], bool],
) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    fallback: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if fallback is None:
            fallback = item
        if matches(str(item.get(id_key) or ""), current):
            return item
    return fallback if not current else None


def _runtime_from_v0(
    data: Any,
    models_url: str,
    current: str,
    services: LmStudioRuntimeServices,
) -> dict[str, Any] | None:
    items = data.get("data") if isinstance(data, dict) else None
    selected = _select_model(items, current, "id", services.model_id_matches)
    if selected is None:
        return None
    return {
        "models_url": models_url,
        "requested_model": current,
        "runtime_model": str(selected.get("id") or ""),
        "max_model_len": services.positive_int(selected.get("max_context_length"))
        or services.model_context(selected),
        "loaded_context_len": services.positive_int(selected.get("loaded_context_length")),
        "state": selected.get("state"),
        "capabilities": selected.get("capabilities"),
        "type": selected.get("type"),
        "root": selected.get("arch"),
    }


def _runtime_from_v1(
    data: Any,
    models_url: str,
    current: str,
    services: LmStudioRuntimeServices,
) -> dict[str, Any] | None:
    items = data.get("models") if isinstance(data, dict) else None
    selected = _select_model(items, current, "key", services.model_id_matches)
    if selected is None:
        return None
    loaded_context = None
    instance_ids: list[str] = []
    instances = selected.get("loaded_instances")
    if isinstance(instances, list) and instances:
        for instance in instances:
            if isinstance(instance, dict) and instance.get("id"):
                instance_ids.append(str(instance["id"]))
        config = instances[0].get("config") if isinstance(instances[0], dict) else None
        if isinstance(config, dict):
            loaded_context = services.positive_int(config.get("context_length"))
    return {
        "models_url": models_url,
        "requested_model": current,
        "runtime_model": str(selected.get("key") or ""),
        "max_model_len": services.positive_int(selected.get("max_context_length"))
        or services.model_context(selected),
        "loaded_context_len": loaded_context,
        "state": "loaded" if loaded_context else "not-loaded",
        "instance_ids": instance_ids,
        "capabilities": selected.get("capabilities"),
        "type": selected.get("type"),
        "root": selected.get("architecture"),
    }
