from __future__ import annotations

import socket
import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from .cielairouter_gateway_overlay import discovery_provider_config


# GET <base>/v1/models timeout.  A cold hosted provider (CielAiRouter) takes
# 2-6 s to give its first answer, which the former 6 s limit cut off
# (Walkie 49376); the same limit as MUSE_CATALOG_TIMEOUT_SECONDS.
MODEL_LIST_TIMEOUT_SECONDS = 20.0
# Pause before the single retry after a timeout or a dropped connection.
MODEL_LIST_RETRY_BACKOFF_SECONDS = 1.0

_T = TypeVar("_T")


class ModelListUnavailableError(RuntimeError):
    """The provider's model list could not be fetched, so no model was checked.

    The message names the provider and the cause class only (a timeout, an
    HTTP status or a connection error type), never URLs, headers or keys.
    """

    def __init__(self, provider: str, cause: str) -> None:
        self.provider = provider
        self.cause = cause
        super().__init__(f"could not fetch the model list from {provider} ({cause})")


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, (TimeoutError, socket.timeout))


def is_transient_model_list_error(error: BaseException) -> bool:
    """A timeout or a connection failure; an HTTP status is never transient here."""

    if isinstance(error, urllib.error.HTTPError):
        return False
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, OSError)
    return _is_timeout(error) or isinstance(error, ConnectionError)


def fetch_model_list_with_retry(fetch: Callable[[], _T]) -> _T:
    """Run ``fetch``; after a timeout or connection error, wait and try once more."""

    try:
        return fetch()
    except Exception as error:
        if not is_transient_model_list_error(error):
            raise
    time.sleep(MODEL_LIST_RETRY_BACKOFF_SECONDS)
    return fetch()


def model_list_failure_cause(error: BaseException | None, timeout: float) -> str:
    """Cause class for a failed model-list fetch: no URL, header or key text."""

    if error is None:
        return "no successful response"
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, BaseException) and _is_timeout(reason):
        return f"timeout after {timeout:g} s"
    if isinstance(reason, OSError):
        return f"connection error: {type(reason).__name__}"
    return type(error).__name__


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
    select_entries: Callable[[str, dict[str, Any], Any], Any] = lambda _provider, _pcfg, data: data


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
    require_catalog: bool = False,
) -> list[str]:
    """Return the provider's model ids.

    When an authoritative catalog could not be fetched, ``require_catalog``
    raises ModelListUnavailableError; otherwise the configured ids are returned
    but not cached, since they are not the provider's catalog.
    """
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
    # A per-request CielAiRouter gateway copy reads the central catalog.
    pcfg = discovery_provider_config(pcfg)
    catalog_policy = provider_model_catalog_policy(provider, pcfg)

    def supplement_model_aliases(
        model_ids: list[str], model_info: dict[str, dict[str, Any]] | None = None
    ) -> list[str]:
        """Add provider-declared wire aliases without weakening catalog authority."""
        supplemented = list(model_ids)
        for source_id, alias_id in catalog_policy.supplemental_model_aliases:
            source = normalize_model_id(provider, source_id)
            alias = normalize_model_id(provider, alias_id)
            if not source or not alias or source not in supplemented:
                continue
            if alias not in supplemented:
                supplemented.append(alias)
            if model_info is not None and source in model_info and alias not in model_info:
                model_info[alias] = dict(model_info[source])
        return supplemented

    cached = None if force_refresh else read_model_list_cache(provider, pcfg)
    if cached is not None:
        supplemented = sorted_model_ids(
            unique_model_ids(provider, supplement_model_aliases(cached))
        )
        cached = supplemented
        if (
            catalog_policy.authoritative_upstream_catalog
            or not catalog_policy.allow_configured_fallback
        ):
            return cached
        ids = unique_model_ids(provider, [
            *cached,
            *catalog_policy.fallback_models,
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ])
        sorted_ids = sorted_model_ids(ids)
        if sorted_ids != cached:
            write_model_list_cache(provider, pcfg, sorted_ids)
        return sorted_ids
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
            ids, source = fetch_anthropic_api_model_ids(pcfg, provider_model_list_headers(provider, pcfg), timeout=MODEL_LIST_TIMEOUT_SECONDS)
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
                data = fetch_model_list_with_retry(lambda: http_json(
                    join_url(base, "/v1/models"), headers=headers, timeout=MODEL_LIST_TIMEOUT_SECONDS, provider=provider, pcfg=pcfg
                ))
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
    failure: BaseException | None = None
    timeout = MODEL_LIST_TIMEOUT_SECONDS
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
                    timeout = 2.0 if catalog_policy.kind == "lm_studio" else (4.0 if catalog_policy.kind == "ollama" else MODEL_LIST_TIMEOUT_SECONDS)
                    url = join_url(request_base, path)
                    data = fetch_model_list_with_retry(
                        lambda: http_json(url, headers=headers, timeout=timeout, provider=provider, pcfg=pcfg)
                    )
                    data = services.response_codec.select_entries(provider, pcfg, data)
                    ids = [normalize_model_id(provider, mid) for mid in model_ids_from_response(data)]
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                    if ids:
                        break
                except Exception as error:
                    failure = error
                    continue
            if not fetched and catalog_policy.allow_public_without_auth:
                # OpenCode publishes the model catalog at /v1/models. Keep the
                # picker independent from key-specific auth/rate-limit failures.
                try:
                    public_headers = with_upstream_user_agent({"content-type": "application/json"})
                    data = fetch_model_list_with_retry(lambda: http_json(
                        join_url(base, "/v1/models"), headers=public_headers, timeout=MODEL_LIST_TIMEOUT_SECONDS, provider=provider, pcfg=pcfg
                    ))
                    ids = model_ids_from_response(data)
                    model_info.update(model_info_from_response(provider, data))
                    fetched = True
                except Exception as exc:
                    router_log("DEBUG", f"{provider} public model catalog fetch failed: {type(exc).__name__}: {exc}")
    except Exception as error:
        failure = error
        ids = []
    if fetched and catalog_policy.authoritative_upstream_catalog:
        ids = supplement_model_aliases(ids, model_info)
        sorted_ids = sorted_model_ids(unique_model_ids(provider, ids))
        metadata = {"model_info": model_info} if model_info else None
        write_model_list_cache(provider, pcfg, sorted_ids, metadata)
        return sorted_ids
    if not fetched and catalog_policy.authoritative_upstream_catalog:
        # A failed lookup is not the provider's catalog.  Caching the
        # configured ids here made later selections report a listed model as
        # "not in the provider catalog" until a forced refresh (Walkie 49376).
        cause = model_list_failure_cause(failure, timeout)
        router_log("WARN", f"model_list_unavailable provider={provider} cause={cause}")
        if require_catalog:
            raise ModelListUnavailableError(provider, cause)
        return sorted_model_ids(unique_model_ids(provider, [
            *catalog_policy.fallback_models,
            *(pcfg.get("custom_models", []) or []),
            pcfg.get("current_model") or "",
        ]))
    if catalog_policy.use_bundled_catalog_fallback and not ids:
        ids = ollama_catalog_model_ids(provider)
        fetched = bool(ids)
    if not fetched and catalog_policy.allow_configured_fallback:
        ids = unique_model_ids(provider, [
            *catalog_policy.fallback_models,
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
    ids = supplement_model_aliases(ids, model_info)
    sorted_ids = unique_model_ids(provider, ids)
    if catalog_policy.kind != "anthropic":
        sorted_ids = sorted_model_ids(sorted_ids)
    metadata = {"model_info": model_info} if model_info else None
    write_model_list_cache(provider, pcfg, sorted_ids, metadata)
    return sorted_ids


__all__ = [
    "MODEL_LIST_RETRY_BACKOFF_SECONDS",
    "MODEL_LIST_TIMEOUT_SECONDS",
    "ModelCatalogHttp",
    "ModelCatalogPolicy",
    "ModelCatalogResponseCodec",
    "ModelCatalogStorage",
    "ModelListUnavailableError",
    "ProviderCatalogSources",
    "ProviderModelServices",
    "fetch_model_list_with_retry",
    "fetch_upstream_model_ids",
    "is_transient_model_list_error",
    "model_list_failure_cause",
]
