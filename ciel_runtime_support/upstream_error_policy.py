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


_TERMINAL_ERROR_TYPES = {
    "invalid_request_error": "invalid_request",
    "invalid_request": "invalid_request",
    "invalid_parameter_error": "invalid_request",
    "authentication_error": "authentication",
    "invalid_api_key": "authentication",
    "permission_error": "permission",
    "permission_denied": "permission",
    "not_found_error": "not_found",
    "model_not_found": "not_found",
    "request_too_large": "request_too_large",
    "context_length_exceeded": "request_too_large",
}

_CAPACITY_ERROR_TYPES = {
    "rate_limit_error": "rate_limit",
    "rate_limit_exceeded": "rate_limit",
    "insufficient_quota": "rate_limit",
    "quota_exceeded": "rate_limit",
    "overloaded_error": "overloaded",
    "server_is_overloaded": "overloaded",
    "slow_down": "overloaded",
}

# Statuses whose meaning a provider label may not override.
_TERMINAL_STATUS_CATEGORIES = {
    400: "invalid_request",
    401: "authentication",
    403: "permission",
    404: "not_found",
    409: "conflict",
    413: "request_too_large",
    422: "invalid_request",
}

_OVERLOAD_MESSAGE_MARKERS = (
    "overload",
    "capacity",
    "high demand",
    "slow down",
    "server is busy",
    "temporarily unavailable",
    "temporary errors",
    "try again later",
)

_CATEGORY_DOWNSTREAM_STATUS = {
    "invalid_request": 400,
    "authentication": 401,
    "permission": 403,
    "not_found": 404,
    "conflict": 409,
    "request_too_large": 413,
    "rate_limit": 429,
    "overloaded": 503,
    "timeout": 504,
    "upstream_error": 502,
}

_CATEGORY_ANTHROPIC_TYPES = {
    "invalid_request": "invalid_request_error",
    "authentication": "authentication_error",
    "permission": "permission_error",
    "not_found": "not_found_error",
    "conflict": "invalid_request_error",
    "request_too_large": "request_too_large",
    "rate_limit": "rate_limit_error",
    "overloaded": "overloaded_error",
    "timeout": "api_error",
    "upstream_error": "api_error",
}

# A provider must never relabel these away from what Ciel observed.
_PROTECTED_STATUS_TYPES = frozenset({401, 403, 413})

_RETRYABLE_CATEGORIES = frozenset({"overloaded", "timeout"})


def classify_upstream_failure(
    status: int | None,
    error_type: str | None,
    message: str | None,
) -> str:
    """Name what actually went wrong upstream.

    A terminal 4xx status is what Ciel itself observed on the wire, so it
    outranks the label in the body: providers routinely answer 413 while
    calling the failure an invalid request, and the size limit is the part the
    CLI has to act on.  Where the status is not decisive, a declared terminal
    type wins instead -- a provider answering HTTP 500 while naming an invalid
    request is reporting a defect that no retry can fix.  Only then does the
    message text get a vote, and only to recognize capacity pressure.
    """

    declared = str(error_type or "").strip().casefold()
    text = str(message or "").casefold()
    if status is not None and status in _TERMINAL_STATUS_CATEGORIES:
        return _TERMINAL_STATUS_CATEGORIES[status]
    terminal = _TERMINAL_ERROR_TYPES.get(declared)
    if terminal:
        return terminal
    capacity = _CAPACITY_ERROR_TYPES.get(declared)
    if capacity:
        return capacity
    if status == 429:
        return "rate_limit"
    if ollama_request_validation_error(text):
        return "invalid_request"
    if any(marker in text for marker in _OVERLOAD_MESSAGE_MARKERS):
        return "overloaded"
    if status in (502, 503, 504):
        return "overloaded"
    if status == 408:
        return "timeout"
    if status is not None and 400 <= status < 500:
        return "invalid_request"
    return "upstream_error"


def anthropic_error_type_for_status(status: int) -> str:
    """The Anthropic error type that matches one upstream HTTP status."""

    category = classify_upstream_failure(status, "", "")
    return _CATEGORY_ANTHROPIC_TYPES.get(category, "api_error")


def _error_fields(payload: Any) -> tuple[str, str]:
    """Pull (type, message) out of any provider error shape Ciel receives."""

    if isinstance(payload, str):
        return "", payload
    if not isinstance(payload, Mapping):
        return "", "" if payload is None else str(payload)
    detail = payload.get("error")
    if detail and isinstance(detail, (Mapping, str)):
        error_type, message = _error_fields(detail)
        if error_type or message:
            return error_type, message
    error_type = ""
    for key in ("type", "code", "error_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            error_type = value.strip()
            break
    message = ""
    for key in ("message", "detail", "error_message", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            message = value.strip()
            break
    if not message and error_type:
        message = error_type
    return error_type, message


def _decoded(raw: Any) -> str:
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw or "")


def upstream_failure_in_payload(
    provider: str,
    model: str,
    payload: Any,
    *,
    status: int | None = None,
    source: str = "json_body",
) -> "UpstreamFailure | None":
    """Recognize an error object a provider returned with a success status.

    OpenAI-compatible servers answer HTTP 200 with ``{"error": {...}}`` when a
    quota or a request check fails.  A decoder that only looks for choices
    reads that as a finished turn with no content, so the CLI shows an empty
    ``end_turn`` instead of the reason it stopped.
    """

    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not error:
        return None
    return UpstreamFailure.from_payload(
        provider, model, payload, status=status, source=source
    )


class UpstreamFailure(RuntimeError):
    """One faithful description of an upstream provider failure.

    Ciel speaks three wires and reads errors out of four places: an HTTP
    status, a JSON body returned with HTTP 200, an SSE error event, and a
    transport fault.  Every conversion between those used to drop something --
    the real status, the provider error type, or the original message -- and a
    request defect then reached the CLI as provider overload or as an empty
    successful turn.  Each error path builds this instead, so the same facts
    survive to whichever protocol answers the client.

    It stays a ``RuntimeError`` because the protocol boundaries already catch
    that: handlers that know this type read the preserved fields, and handlers
    that do not keep working unchanged.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        status: int | None = None,
        error_type: str = "",
        message: str = "",
        source: str = "http_status",
        body: Any = "",
        output_started: bool = False,
        response_id: str | None = None,
    ) -> None:
        self.provider = str(provider or "upstream")
        self.model = str(model or "unknown")
        self.upstream_status = int(status) if status is not None else None
        self.error_type = str(error_type or "").strip()
        self.message = (
            str(message or "").strip() or self.error_type or "upstream request failed"
        )
        self.source = str(source or "http_status")
        self.body = _decoded(body)
        self.output_started = bool(output_started)
        self.response_id = str(response_id or "").strip() or None
        self.category = classify_upstream_failure(
            self.upstream_status, self.error_type, self.message
        )
        super().__init__(self.detail())

    @classmethod
    def from_http_error(
        cls,
        provider: str,
        model: str,
        error: Any,
        raw: Any = None,
        *,
        output_started: bool = False,
    ) -> "UpstreamFailure":
        if raw is None:
            raw = getattr(error, "ciel_runtime_body", None)
        if raw is None:
            try:
                raw = error.read()
            except (AttributeError, OSError, ValueError):
                raw = b""
        body = _decoded(raw)
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None
        error_type, message = _error_fields(payload)
        if not message:
            message = body.strip() or str(getattr(error, "reason", "") or error)
        status = getattr(error, "code", None)
        return cls(
            provider,
            model,
            status=status if isinstance(status, int) else None,
            error_type=error_type,
            message=message,
            source="http_status",
            body=body,
            output_started=output_started,
        )

    @classmethod
    def from_payload(
        cls,
        provider: str,
        model: str,
        payload: Any,
        *,
        status: int | None = None,
        source: str = "json_body",
        output_started: bool = False,
        response_id: str | None = None,
    ) -> "UpstreamFailure":
        error_type, message = _error_fields(payload)
        try:
            body = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            body = str(payload)
        return cls(
            provider,
            model,
            status=status,
            error_type=error_type,
            message=message,
            source=source,
            body=body,
            output_started=output_started,
            response_id=response_id,
        )

    @property
    def retryable(self) -> bool:
        """Whether replaying the same request can plausibly succeed."""

        return self.category in _RETRYABLE_CATEGORIES and not self.output_started

    @property
    def rate_limited(self) -> bool:
        return self.category == "rate_limit"

    @property
    def status_code(self) -> int:
        """The status Ciel answers with when nothing was sent downstream yet.

        The observed status is kept verbatim whenever it agrees with what the
        failure means, so a 404 or a 422 reaches the CLI as itself.  It is
        replaced only when the provider contradicted itself -- Ollama reports
        a rejected prompt template as HTTP 500 -- because then the status
        class is the part that is wrong.
        """

        mapped = _CATEGORY_DOWNSTREAM_STATUS.get(self.category, 502)
        status = self.upstream_status
        if status is None:
            return mapped
        if 400 <= status < 500 and 400 <= mapped < 500:
            return status
        if 500 <= status < 600 and mapped >= 500:
            return status
        return mapped

    @property
    def anthropic_error_type(self) -> str:
        mapped = _CATEGORY_ANTHROPIC_TYPES.get(self.category, "api_error")
        if self.upstream_status in _PROTECTED_STATUS_TYPES:
            return mapped
        return self.error_type or mapped

    def detail(self) -> str:
        status = (
            f"HTTP {self.upstream_status}"
            if self.upstream_status is not None
            else f"{self.source} error"
        )
        label = f"{self.error_type}: " if self.error_type else ""
        return (
            f"Upstream provider '{self.provider}' returned {status} "
            f"({label}{self.message}; model={self.model})"
        )

    def anthropic_payload(self) -> dict[str, Any]:
        return {
            "type": "error",
            "error": {"type": self.anthropic_error_type, "message": self.message},
        }

    def openai_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "type": self.error_type or self.anthropic_error_type,
                "message": self.message,
                "code": self.error_type or self.category,
            }
        }


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
