from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ModelCatalogStorage:
    read_model_list_cache: Callable[..., Any]
    write_model_list_cache: Callable[..., Any]
    write_model_registry: Callable[..., Any]
    router_log: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelCatalogHttp:
    http_json: Callable[..., Any]
    join_url: Callable[..., Any]
    with_upstream_user_agent: Callable[..., Any]
    lm_studio_api_base: Callable[..., Any]
    nvidia_hosted_list_headers: Callable[..., Any]
    nvidia_upstream_base_url: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProviderCatalogSources:
    ANTHROPIC_MODEL_DOCS_URLS: Any
    fetch_anthropic_api_model_ids: Callable[..., Any]
    fetch_anthropic_public_model_ids: Callable[..., Any]
    fetch_fireworks_model_ids: Callable[..., Any]
    fireworks_account_id: Callable[..., Any]
    fireworks_management_base_url: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelCatalogResponseCodec:
    model_ids_from_response: Callable[..., Any]
    model_info_from_response: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ModelCatalogPolicy:
    normalize_model_id: Callable[..., Any]
    ollama_catalog_model_ids: Callable[..., Any]
    provider_has_api_key: Callable[..., Any]
    provider_model_catalog_policy: Callable[..., Any]
    provider_model_paths: Callable[..., Any]
    provider_model_list_headers: Callable[..., Any]
    provider_upstream_request_base: Callable[..., Any]
    sorted_model_ids: Callable[..., Any]
    unique_model_ids: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ProviderModelServices:
    storage: ModelCatalogStorage
    http: ModelCatalogHttp
    sources: ProviderCatalogSources
    response_codec: ModelCatalogResponseCodec
    policy: ModelCatalogPolicy


def fetch_upstream_model_ids(provider: str, pcfg: dict[str, Any], force_refresh: bool = False,
    *,
    services: ProviderModelServices,
) -> list[str]:
    ANTHROPIC_MODEL_DOCS_URLS = services.sources.ANTHROPIC_MODEL_DOCS_URLS
    fetch_anthropic_api_model_ids = services.sources.fetch_anthropic_api_model_ids
    fetch_anthropic_public_model_ids = services.sources.fetch_anthropic_public_model_ids
    fetch_fireworks_model_ids = services.sources.fetch_fireworks_model_ids
    fireworks_account_id = services.sources.fireworks_account_id
    fireworks_management_base_url = services.sources.fireworks_management_base_url
    http_json = services.http.http_json
    join_url = services.http.join_url
    lm_studio_api_base = services.http.lm_studio_api_base
    model_ids_from_response = services.response_codec.model_ids_from_response
    model_info_from_response = services.response_codec.model_info_from_response
    normalize_model_id = services.policy.normalize_model_id
    nvidia_hosted_list_headers = services.http.nvidia_hosted_list_headers
    nvidia_upstream_base_url = services.http.nvidia_upstream_base_url
    ollama_catalog_model_ids = services.policy.ollama_catalog_model_ids
    provider_has_api_key = services.policy.provider_has_api_key
    provider_model_catalog_policy = services.policy.provider_model_catalog_policy
    provider_model_paths = services.policy.provider_model_paths
    provider_model_list_headers = services.policy.provider_model_list_headers
    provider_upstream_request_base = services.policy.provider_upstream_request_base
    read_model_list_cache = services.storage.read_model_list_cache
    router_log = services.storage.router_log
    sorted_model_ids = services.policy.sorted_model_ids
    unique_model_ids = services.policy.unique_model_ids
    with_upstream_user_agent = services.http.with_upstream_user_agent
    write_model_list_cache = services.storage.write_model_list_cache
    write_model_registry = services.storage.write_model_registry
    cached = None if force_refresh else read_model_list_cache(provider, pcfg)
    if cached is not None:
        return cached
    catalog_policy = provider_model_catalog_policy(provider, pcfg)
    if catalog_policy.kind == "configured":
        ids = unique_model_ids(provider, [
            *catalog_policy.fallback_models,
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        if sorted_ids:
            write_model_list_cache(provider, pcfg, sorted_ids)
        return sorted_ids
    if catalog_policy.kind == "anthropic":
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
    if catalog_policy.kind == "fireworks":
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
    if catalog_policy.kind == "nvidia":
        base = (pcfg.get("base_url") or nvidia_upstream_base_url()).rstrip("/")
    else:
        base = provider_upstream_request_base(provider, pcfg)
    ids: list[str] = []
    model_info: dict[str, dict[str, Any]] = {}
    fetched = False
    try:
        if catalog_policy.kind == "nvidia":
            data = http_json(join_url(base, "/v1/models"), headers=nvidia_hosted_list_headers(), timeout=8.0, provider=provider, pcfg=pcfg)
            ids = model_ids_from_response(data)
            model_info.update(model_info_from_response(provider, data))
            fetched = True
        else:
            headers = provider_model_list_headers(provider, pcfg)
            for path in provider_model_paths(provider, pcfg):
                try:
                    request_base = lm_studio_api_base(pcfg) if catalog_policy.kind == "lm_studio" and path.startswith("/api/") else base
                    timeout = 2.0 if catalog_policy.kind == "lm_studio" else (4.0 if catalog_policy.kind == "ollama" else 6.0)
                    data = http_json(join_url(request_base, path), headers=headers, timeout=timeout, provider=provider, pcfg=pcfg)
                    ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                    if ids:
                        break
                except Exception:
                    continue
            if not fetched and catalog_policy.allow_public_without_auth:
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
    if catalog_policy.use_bundled_catalog_fallback and not ids:
        ids = ollama_catalog_model_ids(provider)
        fetched = bool(ids)
    if not fetched and catalog_policy.allow_configured_fallback:
        ids = unique_model_ids(provider, [
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        if sorted_ids:
            write_model_list_cache(provider, pcfg, sorted_ids)
        return sorted_ids
    if not fetched and catalog_policy.fallback_models:
        ids = list(catalog_policy.fallback_models)
        fetched = True
    if not fetched:
        return []
    for mid in catalog_policy.fallback_models:
        mid = normalize_model_id(provider, mid)
        if mid and mid not in ids:
            ids.append(mid)
    for mid in pcfg.get("custom_models", []) or []:
        mid = normalize_model_id(provider, mid)
        if mid and mid not in ids:
            ids.append(mid)
    cur = normalize_model_id(provider, pcfg.get("current_model") or "")
    if cur and catalog_policy.kind != "nvidia" and cur.startswith(f"ciel-runtime-{provider}-"):
        pass
    elif cur and cur not in ids and not (catalog_policy.kind == "nvidia" and cur.startswith("claude-")):
        ids.insert(0, cur)
    if catalog_policy.kind == "nvidia" and cur and cur not in ids:
        ids.insert(0, cur)
    sorted_ids = unique_model_ids(provider, ids)
    if catalog_policy.kind != "anthropic":
        sorted_ids = sorted_model_ids(sorted_ids)
    metadata = {"model_info": model_info} if model_info else None
    write_model_list_cache(provider, pcfg, sorted_ids, metadata)
    return sorted_ids


__all__ = [
    "ModelCatalogHttp",
    "ModelCatalogPolicy",
    "ModelCatalogResponseCodec",
    "ModelCatalogStorage",
    "ProviderCatalogSources",
    "ProviderModelServices",
    "fetch_upstream_model_ids",
]
