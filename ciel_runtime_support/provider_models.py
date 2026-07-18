from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProviderModelServices:
    ANTHROPIC_MODEL_DOCS_URLS: Any
    OPENCODE_PROVIDER_NAMES: Any
    ZAI_MODEL_FALLBACK_IDS: Any
    fetch_anthropic_api_model_ids: Callable[..., Any]
    fetch_anthropic_public_model_ids: Callable[..., Any]
    fetch_fireworks_model_ids: Callable[..., Any]
    fireworks_account_id: Callable[..., Any]
    fireworks_management_base_url: Callable[..., Any]
    http_json: Callable[..., Any]
    join_url: Callable[..., Any]
    lm_studio_api_base: Callable[..., Any]
    model_ids_from_response: Callable[..., Any]
    model_info_from_response: Callable[..., Any]
    normalize_model_id: Callable[..., Any]
    nvidia_hosted_list_headers: Callable[..., Any]
    nvidia_upstream_base_url: Callable[..., Any]
    ollama_catalog_model_ids: Callable[..., Any]
    provider_has_api_key: Callable[..., Any]
    provider_model_list_headers: Callable[..., Any]
    provider_upstream_request_base: Callable[..., Any]
    read_model_list_cache: Callable[..., Any]
    router_log: Callable[..., Any]
    sorted_model_ids: Callable[..., Any]
    unique_model_ids: Callable[..., Any]
    with_upstream_user_agent: Callable[..., Any]
    write_model_list_cache: Callable[..., Any]
    write_model_registry: Callable[..., Any]


def fetch_upstream_model_ids(provider: str, pcfg: dict[str, Any], force_refresh: bool = False,
    *,
    services: ProviderModelServices,
) -> list[str]:
    ANTHROPIC_MODEL_DOCS_URLS = services.ANTHROPIC_MODEL_DOCS_URLS
    OPENCODE_PROVIDER_NAMES = services.OPENCODE_PROVIDER_NAMES
    ZAI_MODEL_FALLBACK_IDS = services.ZAI_MODEL_FALLBACK_IDS
    fetch_anthropic_api_model_ids = services.fetch_anthropic_api_model_ids
    fetch_anthropic_public_model_ids = services.fetch_anthropic_public_model_ids
    fetch_fireworks_model_ids = services.fetch_fireworks_model_ids
    fireworks_account_id = services.fireworks_account_id
    fireworks_management_base_url = services.fireworks_management_base_url
    http_json = services.http_json
    join_url = services.join_url
    lm_studio_api_base = services.lm_studio_api_base
    model_ids_from_response = services.model_ids_from_response
    model_info_from_response = services.model_info_from_response
    normalize_model_id = services.normalize_model_id
    nvidia_hosted_list_headers = services.nvidia_hosted_list_headers
    nvidia_upstream_base_url = services.nvidia_upstream_base_url
    ollama_catalog_model_ids = services.ollama_catalog_model_ids
    provider_has_api_key = services.provider_has_api_key
    provider_model_list_headers = services.provider_model_list_headers
    provider_upstream_request_base = services.provider_upstream_request_base
    read_model_list_cache = services.read_model_list_cache
    router_log = services.router_log
    sorted_model_ids = services.sorted_model_ids
    unique_model_ids = services.unique_model_ids
    with_upstream_user_agent = services.with_upstream_user_agent
    write_model_list_cache = services.write_model_list_cache
    write_model_registry = services.write_model_registry
    cached = None if force_refresh else read_model_list_cache(provider, pcfg)
    if cached is not None:
        return cached
    if provider == "agy":
        ids = unique_model_ids(provider, [*(pcfg.get("custom_models", []) or []), pcfg.get("current_model") or ""])
        if ids:
            write_model_list_cache(provider, pcfg, ids)
        return ids
    if provider == "deepseek":
        ids = unique_model_ids(provider, [
            "deepseek-v4-pro[1m]",
            "deepseek-v4-flash",
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        write_model_list_cache(provider, pcfg, sorted_ids)
        return sorted_ids
    if provider == "zai":
        ids: list[str] = []
        model_info: dict[str, dict[str, Any]] = {}
        base = provider_upstream_request_base(provider, pcfg)
        headers = provider_model_list_headers(provider, pcfg)
        try:
            data = http_json(join_url(base, "/v1/models"), headers=headers, timeout=6.0, provider=provider, pcfg=pcfg)
            ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
            model_info.update(model_info_from_response(provider, data))
        except Exception as exc:
            router_log("DEBUG", f"zai model list fetch failed: {type(exc).__name__}: {exc}")
        ids = unique_model_ids(provider, [
            *ids,
            *ZAI_MODEL_FALLBACK_IDS,
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        metadata = {"model_info": model_info, "source": "zai:/v1/models+docs"} if model_info else {"source": "zai:docs"}
        write_model_list_cache(provider, pcfg, sorted_ids, metadata)
        return sorted_ids
    if provider == "anthropic":
        ids: list[str] = []
        source = ""
        if provider_has_api_key(provider, pcfg):
            ids, source = fetch_anthropic_api_model_ids(pcfg, provider_model_list_headers(provider, pcfg), timeout=6.0)
        if not ids:
            ids = fetch_anthropic_public_model_ids()
            source = "anthropic-docs"
        if not ids:
            return []
        for mid in pcfg.get("custom_models", []) or []:
            mid = normalize_model_id(provider, mid)
            if mid and mid not in ids:
                ids.append(mid)
        cur = normalize_model_id(provider, pcfg.get("current_model") or "")
        if cur and cur not in ids:
            ids.insert(0, cur)
        sorted_ids = unique_model_ids(provider, ids)
        write_model_list_cache(provider, pcfg, sorted_ids)
        write_model_registry(provider, pcfg, sorted_ids, source, {"urls": list(ANTHROPIC_MODEL_DOCS_URLS) if source == "anthropic-docs" else []})
        return sorted_ids
    if provider == "fireworks":
        headers = provider_model_list_headers(provider, pcfg)
        model_info: dict[str, dict[str, Any]] = {}
        source = ""
        try:
            ids, model_info, source = fetch_fireworks_model_ids(pcfg, headers, timeout=8.0)
            fetched = bool(ids)
        except Exception as exc:
            router_log("DEBUG", f"fireworks model API fetch failed: {type(exc).__name__}: {exc}")
            ids = []
            fetched = False
        if not fetched:
            base = provider_upstream_request_base(provider, pcfg)
            try:
                data = http_json(join_url(base, "/v1/models"), headers=headers, timeout=6.0, provider=provider, pcfg=pcfg)
                ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                model_info.update(model_info_from_response(provider, data))
                fetched = bool(ids)
                source = "fireworks:/v1/models"
            except Exception as exc:
                router_log("DEBUG", f"fireworks inference model list fetch failed: {type(exc).__name__}: {exc}")
        if not fetched:
            ids = unique_model_ids(provider, [
                *(pcfg.get("custom_models", []) or []),
                pcfg.get("current_model") or "",
            ])
            sorted_ids = sorted_model_ids(ids)
            if sorted_ids:
                write_model_list_cache(provider, pcfg, sorted_ids)
            return sorted_ids
        for mid in pcfg.get("custom_models", []) or []:
            mid = normalize_model_id(provider, mid)
            if mid and mid not in ids:
                ids.append(mid)
        cur = normalize_model_id(provider, pcfg.get("current_model") or "")
        if cur and cur not in ids:
            ids.insert(0, cur)
        sorted_ids = sorted_model_ids(unique_model_ids(provider, ids))
        metadata = {
            "model_info": model_info,
            "source": source,
            "account_id": fireworks_account_id(pcfg),
            "model_api_base_url": fireworks_management_base_url(pcfg),
        }
        write_model_list_cache(provider, pcfg, sorted_ids, metadata)
        return sorted_ids
    if provider == "nvidia-hosted":
        base = (pcfg.get("base_url") or nvidia_upstream_base_url()).rstrip("/")
    else:
        base = provider_upstream_request_base(provider, pcfg)
    ids: list[str] = []
    model_info: dict[str, dict[str, Any]] = {}
    fetched = False
    try:
        if provider in ("ollama", "ollama-cloud"):
            try:
                data = http_json(join_url(base, "/api/tags"), headers=provider_model_list_headers(provider, pcfg), timeout=4.0, provider=provider, pcfg=pcfg)
                ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                model_info.update(model_info_from_response(provider, data))
                fetched = True
            except Exception:
                data = http_json(join_url(base, "/v1/models"), headers=provider_model_list_headers(provider, pcfg), timeout=4.0, provider=provider, pcfg=pcfg)
                ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                model_info.update(model_info_from_response(provider, data))
                fetched = True
        elif provider == "nvidia-hosted":
            data = http_json(join_url(base, "/v1/models"), headers=nvidia_hosted_list_headers(), timeout=8.0, provider=provider, pcfg=pcfg)
            ids = model_ids_from_response(data)
            model_info.update(model_info_from_response(provider, data))
            fetched = True
        elif provider == "lm-studio":
            headers = provider_model_list_headers(provider, pcfg)
            for path in ("/api/v0/models", "/api/v1/models", "/v1/models", "/models"):
                try:
                    data = http_json(join_url(lm_studio_api_base(pcfg) if path.startswith("/api/") else base, path), headers=headers, timeout=2.0, provider=provider, pcfg=pcfg)
                    ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                    if ids:
                        break
                except Exception:
                    continue
        else:
            headers = provider_model_list_headers(provider, pcfg)
            for path in ("/v1/models", "/models"):
                try:
                    data = http_json(join_url(base, path), headers=headers, timeout=6.0, provider=provider, pcfg=pcfg)
                    ids = model_ids_from_response(data)
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                    if ids:
                        break
                except Exception:
                    continue
            if not fetched and provider in OPENCODE_PROVIDER_NAMES:
                # OpenCode publishes the model catalog at /v1/models. Keep the
                # picker independent from key-specific auth/rate-limit failures.
                try:
                    public_headers = with_upstream_user_agent({"content-type": "application/json"})
                    data = http_json(join_url(base, "/v1/models"), headers=public_headers, timeout=6.0, provider=provider, pcfg=pcfg)
                    ids = model_ids_from_response(data)
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                except Exception as exc:
                    router_log("DEBUG", f"{provider} public model catalog fetch failed: {type(exc).__name__}: {exc}")
    except Exception:
        ids = []
    if provider == "ollama-cloud" and not ids:
        ids = ollama_catalog_model_ids(provider)
        fetched = bool(ids)
    if not fetched and provider in (*OPENCODE_PROVIDER_NAMES, "kimi"):
        ids = unique_model_ids(provider, [
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        if sorted_ids:
            write_model_list_cache(provider, pcfg, sorted_ids)
        return sorted_ids
    if not fetched:
        return []
    for mid in pcfg.get("custom_models", []) or []:
        mid = normalize_model_id(provider, mid)
        if mid and mid not in ids:
            ids.append(mid)
    cur = normalize_model_id(provider, pcfg.get("current_model") or "")
    if cur and provider != "nvidia-hosted" and cur.startswith(f"ciel-runtime-{provider}-"):
        pass
    elif cur and cur not in ids and not (provider == "nvidia-hosted" and cur.startswith("claude-")):
        ids.insert(0, cur)
    if provider == "nvidia-hosted" and cur and cur not in ids:
        ids.insert(0, cur)
    sorted_ids = unique_model_ids(provider, ids)
    if provider != "anthropic":
        sorted_ids = sorted_model_ids(sorted_ids)
    metadata = {"model_info": model_info} if model_info else None
    write_model_list_cache(provider, pcfg, sorted_ids, metadata)
    return sorted_ids


__all__ = ["ProviderModelServices", "fetch_upstream_model_ids"]
