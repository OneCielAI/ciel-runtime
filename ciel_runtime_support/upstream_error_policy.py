"""Pure classification and presentation policy for upstream failures."""

from __future__ import annotations

import json
import re
from http.client import IncompleteRead
from collections.abc import Callable, Mapping
from typing import Any


_TERMINAL_USAGE_LIMIT_MARKERS = (
    "session usage limit",
    "reached your usage limit",
    "reached your session limit",
    "add extra usage",
    "purchase extra usage",
)

_OLLAMA_REQUEST_VALIDATION_MARKERS = (
    "system message must be at the beginning",
)


def ollama_request_validation_error(raw: str | bytes | None) -> bool:
    """Recognize deterministic model-template request rejection from Ollama.

    Ollama currently reports some template validation failures as HTTP 500.
    Retrying such a request cannot succeed and presenting it as provider
    overload hides the actionable error.  Keep this deliberately narrow:
    genuine Ollama runner failures must remain server errors.
    """

    if isinstance(raw, bytes):
        value = raw.decode("utf-8", errors="ignore")
    else:
        value = str(raw or "")
    folded = value.casefold()
    return any(marker in folded for marker in _OLLAMA_REQUEST_VALIDATION_MARKERS)


def terminal_usage_limit_error(raw: str | bytes | None) -> bool:
    """Return whether a rate-limit response requires account action.

    These limits do not recover by replaying the same generation request.  The
    upstream status and body must instead reach the CLI so it can show the
    account name, limit reason, and recovery URL.
    """

    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="ignore")
    else:
        text = str(raw or "")
    folded = text.casefold()
    return any(marker in folded for marker in _TERMINAL_USAGE_LIMIT_MARKERS)


class UpstreamStreamReadError(RuntimeError):
    """A provider response stream ended before Ciel could collect it.

    This is deliberately distinct from a downstream client disconnect.  The
    router can therefore return a Bad Gateway response instead of silently
    treating an upstream socket failure as Codex closing its connection.
    """

    status_code = 502

    def __init__(
        self,
        provider: str,
        model: str,
        error: BaseException,
        *,
        attempts: int,
        downstream_started: bool = False,
        response_id: str | None = None,
        received_bytes: int | None = None,
    ) -> None:
        self.provider = str(provider or "upstream")
        self.model = str(model or "unknown")
        self.error = error
        self.attempts = max(1, int(attempts))
        self.downstream_started = bool(downstream_started)
        self.response_id = str(response_id or "").strip() or None
        if isinstance(error, IncompleteRead):
            received = max(0, int(received_bytes)) if received_bytes is not None else len(error.partial or b"")
            detail = f"response ended early after {received} bytes"
        else:
            detail = f"{type(error).__name__}: {error}"
        super().__init__(
            f"Upstream provider '{self.provider}' response stream was truncated "
            f"({detail}; model={self.model}; attempts={self.attempts})"
        )


def http_error_message(
    error: Any,
    raw: str | None,
    *,
    first_header: Callable[[Mapping[str, str], list[str]], str | None],
    parse_retry_after: Callable[[str], float | None],
    format_duration: Callable[[float], str],
) -> str:
    if raw is None:
        raw = error.read().decode("utf-8", errors="ignore")
    message = raw.strip() or str(error)
    error_type = ""
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            if isinstance(payload.get("error"), dict):
                error_object = payload["error"]
                error_type = str(error_object.get("type") or "").strip()
                message = str(error_object.get("message") or error_object)
            elif payload.get("error"):
                message = str(payload["error"])
            elif payload.get("message"):
                message = str(payload["message"])
                error_type = str(payload.get("type") or "").strip()
    except (TypeError, ValueError):
        pass
    if error_type and error_type not in message:
        message = f"{error_type}: {message}"
    retry_after = first_header(error.headers, ["Retry-After", "retry-after"])
    if not retry_after:
        return message
    retry_text = retry_after.strip()
    seconds = parse_retry_after(retry_text)
    if seconds is None:
        return f"{message} Retry-After: {retry_text}"
    display = format_duration(seconds)
    if retry_text and re.fullmatch(r"\d+(?:\.\d+)?", retry_text):
        return f"{message} Retry-After: {display} ({retry_text}s)"
    return f"{message} Retry-After: {display}"


def retry_message(language: str, attempt: int, total: int, *, rate_limit: bool = False) -> str:
    language = str(language or "en")
    if rate_limit:
        messages = {
            "ko": f"Upstream rate limit에 도달해 대기 후 재시도합니다 ({attempt}/{total}).",
            "ja": f"Upstream rate limit に達したため、待機して再試行します ({attempt}/{total})。",
            "zh": f"已达到 upstream rate limit，等待后重试 ({attempt}/{total})。",
            "en": f"Upstream rate limit reached; waiting before retry ({attempt}/{total}).",
        }
    else:
        messages = {
            "ko": f"서버가 응답하지 않아 재시도합니다 ({attempt}/{total}).",
            "ja": f"サーバーが応答しないため再試行します ({attempt}/{total})。",
            "zh": f"服务器未响应，正在重试 ({attempt}/{total})。",
            "en": f"Upstream server did not respond; retrying ({attempt}/{total}).",
        }
    return messages.get(language, messages["en"])


def retry_wait_seconds(attempt: int) -> float:
    return min(20.0, 2.0 * max(1, attempt))


def retryable_exception(error: BaseException) -> bool:
    if isinstance(
        error,
        (
            TimeoutError,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            IncompleteRead,
        ),
    ):
        return True
    text = f"{type(error).__name__}: {error}".lower()
    markers = (
        "timed out",
        "timeout",
        "connection aborted",
        "connection was aborted",
        "connection reset",
        "connection was forcibly closed by the remote host",
        "existing connection was forcibly closed by the remote host",
        "winerror 10054",
        "connection refused",
        "network is unreachable",
        "network unreachable",
        "no route to host",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
        "remote end closed connection",
        "remote disconnected",
        "eof occurred in violation of protocol",
        "bad record mac",
        "sslv3 alert",
        "decryption failed or bad record mac",
        "temporarily unavailable",
        "broken pipe",
        "upstream stream ended before its first byte",
        "incomplete read",
    )
    return any(marker in text for marker in markers)


def initial_stream_retries(config: dict[str, Any]) -> int:
    """Bound reconnects before an upstream stream yields its first byte."""

    value = config.get("stream_initial_retries", 2)
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 2


def configured_gateway_retries(config: dict[str, Any]) -> int:
    value = config.get("gateway_retries")
    if value is None:
        # Generation requests are not idempotent.  If an upstream completed a
        # request but its response was lost, an automatic retry spends the
        # entire prompt again.  Providers/clients may still apply their own
        # retry policy, and users can opt in here explicitly when desired.
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0
