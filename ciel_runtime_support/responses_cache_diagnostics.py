"""Privacy-safe request fingerprints for native Responses cache diagnosis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def request_cache_profile(
    body: Mapping[str, Any], request_bytes: int
) -> dict[str, Any]:
    raw_input = body.get("input")
    input_items = raw_input if isinstance(raw_input, list) else [raw_input]
    input_head = input_items[:8]
    cache_key = str(body.get("prompt_cache_key") or "")
    return {
        "cache_hit_percent": 0.0,
        "prompt_cache_key_fingerprint": (
            hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
            if cache_key
            else ""
        ),
        "prompt_cache_retention": str(body.get("prompt_cache_retention") or ""),
        "request_instructions_fingerprint": _fingerprint(body.get("instructions")),
        "request_tools_fingerprint": _fingerprint(body.get("tools")),
        "request_input_head_fingerprint": _fingerprint(input_head),
        "request_input_items": len(input_items) if raw_input is not None else 0,
        "request_tools": len(body.get("tools") or []),
        "request_bytes": max(0, int(request_bytes)),
        "request_uses_previous_response_id": bool(body.get("previous_response_id")),
    }


def usage_with_cache_profile(
    usage: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    observed = dict(usage)
    input_tokens = max(0, int(observed.get("input_tokens") or 0))
    cache_read = max(0, int(observed.get("cache_read_tokens") or 0))
    observed.update(profile)
    observed["cache_hit_percent"] = (
        round(cache_read * 100.0 / input_tokens, 2) if input_tokens else 0.0
    )
    return observed


def cache_trace(
    provider: str, model: str, observation: Mapping[str, Any]
) -> tuple[str, str]:
    hit = float(observation.get("cache_hit_percent") or 0.0)
    level = "WARN" if int(observation.get("input_tokens") or 0) and hit < 90.0 else "INFO"
    fields = (
        "input_tokens",
        "cache_read_tokens",
        "uncached_input_tokens",
        "cache_hit_percent",
        "prompt_cache_key_fingerprint",
        "prompt_cache_retention",
        "request_instructions_fingerprint",
        "request_tools_fingerprint",
        "request_input_head_fingerprint",
        "request_input_items",
        "request_tools",
        "request_bytes",
        "request_uses_previous_response_id",
    )
    detail = " ".join(f"{name}={observation.get(name)}" for name in fields)
    return level, f"provider_responses_cache provider={provider} model={model} {detail}"


__all__ = ["cache_trace", "request_cache_profile", "usage_with_cache_profile"]
