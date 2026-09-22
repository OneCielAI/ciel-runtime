"""Relay Muse Code's model catalog when Muse points its base at the router.

Muse asks its base host for ``GET /muse-code/models`` before a session. A
routed Muse points that base at the Ciel Router, which has no such route, so
Muse reported ``failed to fetch model catalog: API error 404:
/muse-code/models`` (live 2026-09-21 on a pool machine, Muse Code
1.3.0-R3401.1). Meta serves the same catalog at ``<meta host>/muse-code/models``
for the workspace's Model API key, so the router relays that answer; when the
upstream cannot be reached the relay keeps Muse starting with the configured
model instead of failing the launch.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

MUSE_CATALOG_PATH = "/muse-code/models"
MUSE_CATALOG_TIMEOUT_SECONDS = 20.0
MUSE_FALLBACK_MODEL = "muse-spark-1.3"
META_DEFAULT_BASE_URL = "https://api.meta.ai/v1"


def meta_api_key(meta_config: Mapping[str, Any] | None) -> str:
    """The first meaningful Model API key of the ``meta`` provider config."""

    config = meta_config if isinstance(meta_config, Mapping) else {}
    keys = config.get("api_keys")
    if isinstance(keys, (list, tuple)):
        for value in keys:
            text = str(value or "").strip()
            if text:
                return text
    return str(config.get("api_key") or "").strip()


def muse_catalog_url(base_url: str) -> str:
    """``<scheme>://<host>/muse-code/models`` for a provider base URL."""

    text = str(base_url or "").strip() or META_DEFAULT_BASE_URL
    parts = urlsplit(text)
    scheme = parts.scheme or "https"
    host = parts.netloc or urlsplit(META_DEFAULT_BASE_URL).netloc
    return urlunsplit((scheme, host, MUSE_CATALOG_PATH, "", ""))


def _fallback(meta_config: Mapping[str, Any] | None, reason: str) -> dict[str, Any]:
    config = meta_config if isinstance(meta_config, Mapping) else {}
    model = str(config.get("current_model") or "").strip() or MUSE_FALLBACK_MODEL
    return {
        "object": "list",
        "data": [{"id": model, "object": "model", "owned_by": "meta"}],
        "muse_catalog_fallback": reason,
    }


def muse_model_catalog(
    config: Mapping[str, Any],
    provider: str,
    provider_config: Mapping[str, Any],
    *,
    headers: Any = None,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
    log: Callable[[str, str], Any] = lambda _level, _message: None,
) -> dict[str, Any]:
    """Meta's Muse catalog, relayed for a Muse client pinned to this router."""

    del provider, provider_config, headers
    providers = config.get("providers") if isinstance(config, Mapping) else None
    meta_config = providers.get("meta") if isinstance(providers, Mapping) else None
    key = meta_api_key(meta_config)
    if not key:
        log("WARN", "muse_model_catalog_fallback reason=missing_meta_api_key")
        return _fallback(meta_config, "missing_meta_api_key")
    base = str((meta_config or {}).get("base_url") or "").strip()
    request = urllib.request.Request(
        muse_catalog_url(base),
        headers={"authorization": f"Bearer {key}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=MUSE_CATALOG_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 - the launch must not fail on a catalog
        log("WARN", f"muse_model_catalog_fallback reason=upstream_{type(exc).__name__}")
        return _fallback(meta_config, f"upstream_{type(exc).__name__}")
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
        return dict(payload)
    log("WARN", "muse_model_catalog_fallback reason=unexpected_payload")
    return _fallback(meta_config, "unexpected_payload")


__all__ = [
    "MUSE_CATALOG_PATH",
    "MUSE_FALLBACK_MODEL",
    "meta_api_key",
    "muse_catalog_url",
    "muse_model_catalog",
]
