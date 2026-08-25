"""Opt-in capture of the exact bytes sent to an upstream provider.

Set ``CIEL_RUNTIME_DUMP_UPSTREAM`` to a directory to record every upstream
request body the router is about to send on its provider-wire paths. Each
request produces two files sharing one stem:

``upstream-<utc>-<seq>-body.json``
    The request body exactly as encoded for the wire, byte for byte.
``upstream-<utc>-<seq>-meta.json``
    Where it was going: URL, byte count, and capture time.

The variable is read per request, capture failures only log, and nothing is
written when the variable is unset, so the feature is inert in normal runs.
This exists to diagnose upstream rejections from evidence instead of from
reconstructions of what the router "should" have sent.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

DUMP_ENV_VAR = "CIEL_RUNTIME_DUMP_UPSTREAM"

_sequence = itertools.count(1)


def _sanitized_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    """Preserve wire header names while never persisting credentials."""

    sanitized: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name)
        value = str(raw_value)
        folded = name.casefold()
        sensitive = (
            folded in {"authorization", "proxy-authorization", "cookie", "set-cookie"}
            or any(
                marker in folded
                for marker in ("api-key", "apikey", "token", "secret", "credential", "captcha")
            )
        )
        sanitized[name] = f"<redacted len={len(value)}>" if sensitive else value
    return sanitized


def upstream_dump_dir(env: Callable[[str], str | None] = os.environ.get) -> Path | None:
    configured = (env(DUMP_ENV_VAR) or "").strip()
    return Path(configured) if configured else None


def dump_upstream_request(
    url: str,
    data: bytes,
    log: Callable[[str, str], Any],
    env: Callable[[str], str | None] = os.environ.get,
    *,
    headers: Mapping[str, Any] | None = None,
) -> Path | None:
    """Record ``data`` exactly as it will be sent to ``url``; never raise."""

    target = upstream_dump_dir(env)
    if target is None:
        return None
    try:
        target.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        stem = f"upstream-{stamp}-{next(_sequence):04d}"
        body_path = target / f"{stem}-body.json"
        body_path.write_bytes(data)
        metadata: dict[str, Any] = {
            "time": stamp,
            "url": url,
            "body_bytes": len(data),
        }
        if headers is not None:
            metadata["headers"] = _sanitized_headers(headers)
        (target / f"{stem}-meta.json").write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        log("INFO", f"upstream_dump wrote {body_path.name} bytes={len(data)} url={url}")
        return body_path
    except Exception as exc:
        log("WARN", f"upstream_dump_failed error={type(exc).__name__}: {exc}")
        return None


__all__ = ["DUMP_ENV_VAR", "dump_upstream_request", "upstream_dump_dir"]
