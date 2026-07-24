"""Pure classification and presentation policy for upstream failures."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any


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
    text = f"{type(error).__name__}: {error}".lower()
    markers = (
        "timed out",
        "timeout",
        "connection aborted",
        "connection was aborted",
        "connection reset",
        "connection refused",
        "remote end closed connection",
        "remote disconnected",
        "eof occurred in violation of protocol",
        "temporarily unavailable",
        "broken pipe",
    )
    return any(marker in text for marker in markers)


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
