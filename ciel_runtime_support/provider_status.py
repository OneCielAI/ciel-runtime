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
        data = catalog.http_json(
            catalog.join_url(probe_base, policy.catalog_path),
            headers=catalog.model_headers(provider, pcfg),
            timeout=2.5,
            provider=provider,
            pcfg=pcfg,
        )
        count = len(catalog.model_ids(data))
        return f"Base URL: {policy.label} {scope} reachable ({policy.catalog_path}, {count} {policy.catalog_count_label})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"Base URL: {policy.label} reachable, auth rejected ({exc.code})"
        return f"Base URL: {policy.label} HTTP {exc.code}"
    except Exception as exc:
        return f"Base URL: {policy.label} {scope} unreachable ({type(exc).__name__})"


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
    try:
        request = urllib.request.Request(join_url(base, path), headers=headers)
        with generic.provider_urlopen(request, timeout=2.5, provider=provider, pcfg=pcfg) as response:
            body = response.read(131072).decode("utf-8", errors="ignore")
        count = ""
        try:
            data = json.loads(body)
        except (TypeError, ValueError):
            data = {}
        models = data.get(policy.catalog_count_key) if isinstance(data, dict) else None
        if isinstance(models, list):
            count = f", {len(models)} models"
            if policy.catalog_count_key == "data":
                limit = generic.model_context_limit(provider, pcfg, timeout=1.0)
                if limit:
                    count += f", max_model_len {limit}"
        return f"Base URL: model list reachable ({path}{count})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return f"Base URL: model list reachable, auth rejected ({exc.code})"
        return f"Base URL: HTTP {exc.code}"
    except Exception as exc:
        return f"Base URL: unreachable ({type(exc).__name__})"
