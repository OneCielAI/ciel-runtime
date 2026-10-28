"""Bounded SSE shape diagnostics. No arbitrary upstream strings are retained."""
from __future__ import annotations

import json
from typing import Any

_FIELDS = frozenset({"type", "error", "request_id", "message", "code", "param", "status", "detail"})
_EVENTS = frozenset({"error", "ping", "message_start", "message_delta", "message_stop",
                     "content_block_start", "content_block_delta", "content_block_stop"})
_ERROR_TYPES = frozenset({"api_error", "server_error", "timeout_error", "overloaded_error",
                         "gateway_timeout", "upstream_error", "provider_error", "error", "timeout",
                         "invalid_request_error", "not_found_error", "authentication_error",
                         "permission_error", "rate_limit_error", "billing_error"})


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "other"


def _fields(value: dict) -> dict:
    return {"fields": {k: _kind(value[k]) for k in sorted(_FIELDS.intersection(value))},
            "other_fields": len(value.keys() - _FIELDS)}


def payload_shape(data: str, event: str | None = None) -> dict:
    """Retain only allowlisted field names, value types, tokens and lengths."""
    result = {"event": event if event in _EVENTS else "other" if event else "absent",
              "data_chars": len(data)}
    if len(data) > 65536:
        return {**result, "json": "over_bound"}
    try:
        value = json.loads(data)
    except (ValueError, TypeError, RecursionError):
        return {**result, "json": "invalid"}
    result["json"] = _kind(value)
    if not isinstance(value, dict):
        return result
    result.update(_fields(value))
    detail = value.get("error")
    if isinstance(detail, dict):
        error_type = detail.get("type")
        result["error"] = {**_fields(detail),
                           "type_token": error_type if isinstance(error_type, str) and error_type in _ERROR_TYPES else "other",
                           "message_chars": len(detail["message"]) if isinstance(detail.get("message"), str) else None}
    return result


def prefix_shape(raw: bytes) -> dict:
    """Summarize at most four complete frames of a bounded held prefix."""
    lf, cr = bytes([10]), bytes([13])
    normalized = raw[:65536].replace(cr + lf, lf).replace(cr, lf)
    frames = normalized.split(lf + lf)
    result = {"complete_frames": len(frames) - 1, "tail_bytes": len(frames[-1]), "frames": []}
    complete = frames[:-1]
    selected = complete if len(complete) <= 4 else complete[:2] + complete[-2:]
    for frame in selected:
        event, data, other = None, [], 0
        for line in frame.split(lf):
            if line.startswith(b"event:"):
                event = line[6:].strip().decode("utf-8", "replace")
            elif line.startswith(b"data:"):
                data.append(line[5:].lstrip().decode("utf-8", "replace"))
            elif line and not line.startswith(b":"):
                other += 1
        result["frames"].append({**payload_shape(chr(10).join(data), event), "other_lines": other})
    return result


def response_shape(response: Any) -> dict:
    """Classify the relay's actual response, never class names or raw headers."""
    headers = getattr(response, "headers", {}) or {}
    content_type = str(headers.get("Content-Type", headers.get("content-type", ""))).lower()
    media = content_type.split(";", 1)[0].strip()
    result = {"media": media if media in {"text/event-stream", "application/json"} else "other" if media else "absent"}
    wrapper = type(response).__name__
    result["wrapper"] = {"_PreOutputSSE": "preoutput_guard", "_ReplayResponse": "replay",
                         "HTTPResponse": "http"}.get(wrapper, "other")
    diagnostic = getattr(response, "_ciel_preoutput_diagnostic", None)
    if isinstance(diagnostic, dict):
        states = {"armed", "committed", "reopened"}
        reasons = {"accepted", "read_failure_with_prefix", "eof", "multiple_frames", "trailing_bytes",
                   "unrecognized_frame", "terminal_verdict", "budget_exhausted", "reopen_http_error", "byte_bound"}
        state, reason = diagnostic.get("state"), diagnostic.get("reason")
        result["preoutput"] = {
            "state": state if isinstance(state, str) and state in states else "other",
            "reason": reason if isinstance(reason, str) and reason in reasons else "other",
        }
        operation_id = diagnostic.get("operation_id")
        if isinstance(operation_id, str) and len(operation_id) == 32 and all(c in "0123456789abcdef" for c in operation_id):
            result["preoutput"]["operation_id"] = operation_id
    return result
