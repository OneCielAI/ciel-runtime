"""Project explicit CLI error envelopes; never infer errors from conversation text."""

from typing import Any


def project_runtime_errors(record: dict[str, Any], runtime: str) -> list[dict[str, Any]]:
    envelope = record
    if record.get("type") == "event_msg":
        envelope = record.get("payload") or {}
    if not isinstance(envelope, dict):
        return []
    kind = envelope.get("type")
    retrying = kind == "stream_error" or (
        kind == "system" and envelope.get("subtype") in {"api_error", "api_retry"}
    )
    explicit = kind in {"error", "stream_error", "turn.failed", "response.failed"}
    explicit = explicit or retrying or (
        kind == "assistant" and (envelope.get("isApiErrorMessage") is True or bool(envelope.get("error")))
    ) or (kind == "result" and envelope.get("is_error") is True)
    if not explicit:
        return []
    error = envelope.get("error")
    if kind == "response.failed":
        response = envelope.get("response")
        if isinstance(response, dict):
            error = response.get("error")
    details = error if isinstance(error, dict) else {}
    message = details.get("message") or envelope.get("message") or envelope.get("result")
    if isinstance(message, dict):
        blocks = message.get("content")
        message = "\n".join(str(b.get("text") or "") for b in blocks
                            if isinstance(b, dict) and b.get("type") == "text") if isinstance(blocks, list) else ""
    if not isinstance(message, str) or not message:
        errors = envelope.get("errors")
        message = "\n".join(e for e in errors if isinstance(e, str)) if isinstance(errors, list) else ""
    code = details.get("code") or details.get("type") or envelope.get("errorStatus") or (
        error if isinstance(error, str) else envelope.get("subtype")
    ) or kind
    return [{
        "runtime": runtime, "phase": "error", "error_code": str(code),
        "message": (message or str(code))[:8192], "retrying": retrying,
        "status": details.get("status") or envelope.get("error_status") or envelope.get("status"),
        "turn_id": envelope.get("turn_id") or record.get("turn_id"),
        "retry_after_ms": envelope.get("retry_delay_ms") or envelope.get("retryInMs"),
    }]
