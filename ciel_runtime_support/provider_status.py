"""Provider base URL status projection application service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable
import urllib.error
import urllib.request

from .architecture import ProviderStatusPolicy


@dataclass(frozen=True, slots=True)
class ProviderStatusRouting:
    codex_routed: Callable[..., bool]
    agy_routed: Callable[..., bool]
    nvidia_native: Callable[..., bool]
    native_anthropic_base: Callable[..., str]
    router_up: Callable[[], bool]
    router_base: str


@dataclass(frozen=True, slots=True)
class ProviderStatusCatalog:
    model_headers: Callable[..., dict[str, str]]
    http_json: Callable[..., Any]
    join_url: Callable[..., str]
    management_base: Callable[..., str]
    model_ids: Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class ProviderStatusGeneric:
    primary_api_key: Callable[..., str]
    meaningful_key: Callable[..., bool]
    with_user_agent: Callable[..., dict[str, str]]
    provider_urlopen: Callable[..., Any]
    model_context_limit: Callable[..., int | None]


@dataclass(frozen=True, slots=True)
class ProviderStatusServices:
    routing: ProviderStatusRouting
    catalog: ProviderStatusCatalog
    generic: ProviderStatusGeneric


def base_url_status_line(
    provider: str,
    pcfg: dict[str, Any],
    policy: ProviderStatusPolicy,
    *,
    services: ProviderStatusServices,
) -> str:
    base = str(pcfg.get("base_url") or "").rstrip("/")
    if not base:
        return "Base URL: missing"
    if "your-" in base:
        return f"Base URL: placeholder ({base})"
    routing = services.routing
    if policy.kind == "native_codex":
        if routing.codex_routed(provider, pcfg):
            return f"Base URL: Codex routed through local router ({routing.router_base}/backend-api/codex)"
        return "Base URL: native Codex config (ciel-runtime does not override it)"
    if policy.kind == "native_agy":
        if routing.agy_routed(provider, pcfg):
            return "Base URL: AGY routed uses native Antigravity model upstream; Ciel routes channel/PTY wake only"
        return "Base URL: native AGY config (ciel-runtime does not override it)"
    if policy.kind == "nvidia":
        if routing.nvidia_native(provider, pcfg):
            return f"Base URL: NVIDIA hosted native ({routing.native_anthropic_base(provider, pcfg)}/v1/messages)"
        state = "ready" if routing.router_up() else "starts on launch"
        return f"Base URL: NVIDIA hosted ({base}); local router {routing.router_base} {state}"
    if policy.kind == "configured":
        return f"Base URL: {policy.configured_description} ({base})"
    if policy.kind == "catalog":
        return _catalog_status(provider, pcfg, base, policy, services.catalog)
    return _generic_status(provider, pcfg, base, policy, services.generic, services.catalog.join_url)


def _is_probe_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError)
        and not isinstance(exc, urllib.error.HTTPError)
        and isinstance(exc.reason, TimeoutError)
    )


def _probe_with_retry(
    probe: Callable[[float], Any],
    policy: ProviderStatusPolicy,
) -> tuple[Any, str]:
    try:
        return probe(policy.probe_timeout_seconds), ""
    except Exception as exc:
        if not _is_probe_timeout(exc) or policy.probe_retry_timeout_seconds <= 0:
            raise
    data = probe(policy.probe_retry_timeout_seconds)
    return data, f"; retry succeeded ({policy.probe_retry_timeout_seconds:g}s timeout)"


def _probe_timeout_detail(policy: ProviderStatusPolicy) -> str:
    detail = f"model-list probe timeout ({policy.probe_timeout_seconds:g}s"
    if policy.probe_retry_timeout_seconds > 0:
        detail += f" + {policy.probe_retry_timeout_seconds:g}s retry"
    return detail + ")"


def _catalog_status(
    provider: str,
    pcfg: dict[str, Any],
    base: str,
    policy: ProviderStatusPolicy,
    catalog: ProviderStatusCatalog,
) -> str:
    probe_base = catalog.management_base(pcfg) if policy.catalog_scope == "fireworks_management" else base
    scope = "model API" if policy.catalog_scope == "fireworks_management" else "model list"
    try:
        data, retry = _probe_with_retry(
            lambda timeout: catalog.http_json(
                catalog.join_url(probe_base, policy.catalog_path),
                headers=catalog.model_headers(provider, pcfg),
                timeout=timeout,
                provider=provider,
                pcfg=pcfg,
            ),
            policy,
        )
        count = len(catalog.model_ids(data))
        return f"Base URL: {policy.label} {scope} reachable ({policy.catalog_path}, {count} {policy.catalog_count_label}{retry})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"Base URL: {policy.label} reachable, auth rejected ({exc.code})"
        return f"Base URL: {policy.label} HTTP {exc.code}"
    except Exception as exc:
        detail = _probe_timeout_detail(policy) if _is_probe_timeout(exc) else type(exc).__name__
        return f"Base URL: {policy.label} {scope} unreachable ({detail})"


def _generic_status(
    provider: str,
    pcfg: dict[str, Any],
    base: str,
    policy: ProviderStatusPolicy,
    generic: ProviderStatusGeneric,
    join_url: Callable[..., str],
) -> str:
    headers: dict[str, str] = {}
    key = generic.primary_api_key(provider, pcfg)
    if generic.meaningful_key(key):
        headers = {"x-api-key": key, "authorization": f"Bearer {key}"}
    headers = generic.with_user_agent(headers)
    path = policy.catalog_path or "/v1/models"
    reading_body = False

    def probe(timeout: float) -> str:
        nonlocal reading_body
        reading_body = False
        request = urllib.request.Request(join_url(base, path), headers=headers)
        with generic.provider_urlopen(request, timeout=timeout, provider=provider, pcfg=pcfg) as response:
            reading_body = True
            # The filtered catalog can exceed the legacy generic preview limit.
            limit = 1048576 if policy.probe_retry_timeout_seconds > 0 else 131072
            body = response.read(limit + 1) if policy.probe_retry_timeout_seconds > 0 else response.read(limit)
            if policy.probe_retry_timeout_seconds > 0 and len(body) > limit:
                raise ValueError("model catalog exceeds probe limit")
            return body.decode("utf-8", errors="ignore")

    try:
        body, retry = _probe_with_retry(probe, policy)
        try:
            data = json.loads(body)
        except (TypeError, ValueError):
            if policy.probe_retry_timeout_seconds > 0:
                raise
            data = {}
        models = data.get(policy.catalog_count_key) if isinstance(data, dict) else None
        if policy.probe_retry_timeout_seconds > 0 and not isinstance(models, list):
            return f"Base URL: model list unreachable ({path}; incomplete model catalog)"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"Base URL: model list reachable, auth rejected ({exc.code})"
        return f"Base URL: HTTP {exc.code}"
    except Exception as exc:
        if _is_probe_timeout(exc):
            phase = "HTTP response headers received; model-list body read timed out" if reading_body else "before response headers"
            return f"Base URL: model list unreachable ({_probe_timeout_detail(policy)}; {phase})"
        return f"Base URL: unreachable ({type(exc).__name__})"
    count = ""
    if isinstance(models, list):
        count = f", {len(models)} models"
        if policy.catalog_count_key == "data":
            try:
                limit = generic.model_context_limit(provider, pcfg, timeout=1.0)
                if limit:
                    count += f", max_model_len {limit}"
            except Exception as exc:
                count += f", context unavailable ({type(exc).__name__})"
    return f"Base URL: model list reachable ({path}{count}{retry})"
