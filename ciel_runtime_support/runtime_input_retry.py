"""Resend of an injected runtime input whose turn failed on a transient upstream error.

User decision 2026-10-24: a turn that dies on a transient upstream error is
retried by resending the same original message, up to 10 attempts, 10 seconds
apart.  Everything else (bad requests, context or usage limits, auth, policy,
user interrupts and anything unclassified) fails at once with its typed reason
so delivery moves on to the next input.

The classifiers read only typed fields, never conversation text:

* Codex app-server ``TurnError.codexErrorInfo`` (camelCase variants, with
  ``httpStatusCode`` on the connection variants), measured from
  ``codex app-server generate-json-schema`` on codex-cli 0.158.0.  Without that
  field the only fallback is Codex's own ``unexpected status NNN`` prefix,
  observed verbatim on .14 (2026-10-23, 53 rows "unexpected status 504").
* Claude Code API error records (``isApiErrorMessage``) with ``error``,
  ``apiErrorStatus``, ``apiErrorIsTransient``, ``resendCannotCure`` and
  ``truncatedAfterOutput``, observed in local transcripts of 2.1.278-2.1.310.

The resend prompt starts with one marker line.  Claude delivery tracks prompts
by exact text, so an identical resend would read as already delivered; the
marker also tells the model this is a retry.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

CONFIG_KEY = "runtime_input_retry"
DEFAULT_LIMIT = 10
DEFAULT_INTERVAL_SECONDS = 10
LIMIT_MAX = 30
INTERVAL_MIN_SECONDS = 1
INTERVAL_MAX_SECONDS = 600
MARKER_PREFIX = "[ciel-runtime 재전송"
FAILED_STATE_PREFIX = "turn_failed:"

TRANSIENT_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504, 529})
CODEX_TRANSIENT_KINDS = frozenset({
    "serverOverloaded",
    "httpConnectionFailed",
    "responseStreamConnectionFailed",
    "responseStreamDisconnected",
    "responseTooManyFailedAttempts",
})
# Codex thread items that act on the workspace; a resend after one of these
# may repeat that work, so the record says so.
CODEX_TOOL_ITEM_TYPES = frozenset({
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "collabAgentToolCall",
    "functionCallOutput",
    "webSearch",
    "imageGeneration",
})
CLAUDE_TRANSIENT_ERRORS = frozenset({"server_error", "overloaded_error", "api_error", "timeout_error"})
_UNEXPECTED_STATUS = re.compile(r"^unexpected status (\d{3})\b")


@dataclass(frozen=True, slots=True)
class RetrySettings:
    limit: int = DEFAULT_LIMIT
    interval_seconds: float = float(DEFAULT_INTERVAL_SECONDS)

    @property
    def enabled(self) -> bool:
        return self.limit > 0


@dataclass(frozen=True, slots=True)
class TurnFailure:
    """A payload-free description of why a turn failed."""

    kind: str
    http_status: int | None = None
    transient: bool = False

    @property
    def label(self) -> str:
        return f"{self.kind} {self.http_status}" if self.http_status else self.kind


def _section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get(CONFIG_KEY) if isinstance(config, dict) else None
    return section if isinstance(section, dict) else {}


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def settings(config: dict[str, Any]) -> RetrySettings:
    """Attempt limit (0 disables resending) and seconds between attempts."""

    section = _section(config)
    return RetrySettings(
        limit=_bounded_int(section.get("limit"), DEFAULT_LIMIT, 0, LIMIT_MAX),
        interval_seconds=float(_bounded_int(
            section.get("interval_seconds"), DEFAULT_INTERVAL_SECONDS, INTERVAL_MIN_SECONDS, INTERVAL_MAX_SECONDS
        )),
    )


def _http_status(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def classify_codex_turn(turn: dict[str, Any], error: dict[str, Any] | None = None) -> TurnFailure:
    """Typed failure of a Codex turn; ``error`` is a remembered error notification."""

    if str(turn.get("status") or "") == "interrupted":
        return TurnFailure("turn_interrupted")
    turn_error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
    if not turn_error.get("codexErrorInfo") and isinstance(error, dict):
        turn_error = error
    info = turn_error.get("codexErrorInfo")
    kind, http_status = "", None
    if isinstance(info, str):
        kind = info
    elif isinstance(info, dict) and len(info) == 1:
        kind, body = next(iter(info.items()))
        if isinstance(body, dict):
            http_status = _http_status(body.get("httpStatusCode"))
    if not kind:
        match = _UNEXPECTED_STATUS.match(str(turn_error.get("message") or ""))
        if match is None:
            return TurnFailure("turn_failed")
        kind, http_status = "httpStatus", int(match.group(1))
    if kind == "serverOverloaded":
        transient = True
    elif http_status is not None:
        transient = http_status in TRANSIENT_HTTP_STATUSES and kind in CODEX_TRANSIENT_KINDS | {"httpStatus"}
    else:
        transient = kind in CODEX_TRANSIENT_KINDS
    return TurnFailure(kind, http_status, transient)


def classify_claude_api_error(record: dict[str, Any]) -> TurnFailure:
    """Typed failure from one Claude Code ``isApiErrorMessage`` record."""

    error = str(record.get("error") or "") or "api_error"
    http_status = _http_status(record.get("apiErrorStatus"))
    if record.get("truncatedAfterOutput") is True:
        # Output already reached the screen: stream recovery owns that turn.
        return TurnFailure("stream_cut_after_output", http_status)
    if record.get("resendCannotCure") is True or record.get("apiErrorIsTransient") is False:
        return TurnFailure(error, http_status)
    if error == "rate_limit":
        # 429 is resent only when Claude Code itself marks it transient
        # ("Server is temporarily limiting requests (not your usage limit)").
        return TurnFailure(error, http_status, record.get("apiErrorIsTransient") is True)
    if error in CLAUDE_TRANSIENT_ERRORS:
        return TurnFailure(error, http_status, http_status is None or http_status in TRANSIENT_HTTP_STATUSES)
    return TurnFailure(error, http_status, http_status in TRANSIENT_HTTP_STATUSES)


def encode_failed_state(failure: TurnFailure, at_epoch: float | None = None) -> str:
    """Wake-state string for a failed turn: ``turn_failed:<0|1>:<epoch|->:<label>``.

    The epoch is when the error record was written; it tells a failure of the
    attempt being watched from one left by an earlier attempt.
    """

    stamp = f"{at_epoch:.3f}" if isinstance(at_epoch, (int, float)) else "-"
    return f"{FAILED_STATE_PREFIX}{int(failure.transient)}:{stamp}:{failure.label}"


def _failed_parts(state: Any) -> tuple[str, str, str] | None:
    if not isinstance(state, str) or not state.startswith(FAILED_STATE_PREFIX):
        return None
    parts = state.split(":", 3)
    return (parts[1], parts[2], parts[3]) if len(parts) == 4 else ("0", "-", "turn_failed")


def decode_failed_state(state: str) -> TurnFailure | None:
    parts = _failed_parts(state)
    if parts is None:
        return None
    transient, _stamp, label = parts
    kind, _, status = label.partition(" ")
    return TurnFailure(kind or "turn_failed", int(status) if status.isdigit() else None, transient == "1")


def failed_state_epoch(state: str) -> float | None:
    parts = _failed_parts(state)
    try:
        return float(parts[1]) if parts is not None and parts[1] != "-" else None
    except ValueError:
        return None


def is_failed_state(state: Any) -> bool:
    return isinstance(state, str) and state.startswith(FAILED_STATE_PREFIX)


def marker(attempt: int, limit: int, failure_label: str) -> str:
    return f"{MARKER_PREFIX} {attempt}/{limit} · 이전 시도 업스트림 오류: {failure_label}]"


def resend_message(message: dict[str, Any], retry: dict[str, Any]) -> dict[str, Any]:
    """A copy of the original input whose body starts with the marker line.

    The marker goes into the body, never ahead of the delivery prompt: the
    prompt's own first line carries the message id that delivery
    confirmation reads (``[ciel-wake]`` prompts are read from that line only)
    and the prefix that marks the turn as automatic.
    """

    line = marker(int(retry.get("attempt") or 1), int(retry.get("limit") or DEFAULT_LIMIT),
                  str(retry.get("last_failure") or "turn_failed"))
    return {**message, "message": f"{line}\n{message.get('message') or ''}"}


def pending_retry(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The scheduled resend a queued status row carries, if any."""

    if not isinstance(row, dict) or row.get("status") != "queued":
        return None
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    retry = data.get("retry")
    return retry if isinstance(retry, dict) and int(retry.get("attempt") or 0) > 0 else None


def next_retry(
    previous: dict[str, Any] | None, failure: TurnFailure, retry_settings: RetrySettings, now: float, **extra: Any
) -> dict[str, Any] | None:
    """The next resend record, or None when the failure is terminal."""

    done = int((previous or {}).get("attempt") or 0)
    if not failure.transient or not retry_settings.enabled or done >= retry_settings.limit:
        return None
    return {
        "attempt": done + 1,
        "limit": retry_settings.limit,
        "interval_seconds": retry_settings.interval_seconds,
        "due_epoch": now + retry_settings.interval_seconds,
        "last_failure": failure.label,
        "http_status": failure.http_status,
        "claim": "",
        **extra,
    }


def terminal_reason(previous: dict[str, Any] | None, failure: TurnFailure, retry_settings: RetrySettings) -> str:
    if failure.transient and retry_settings.enabled and int((previous or {}).get("attempt") or 0) >= retry_settings.limit:
        return f"retry_exhausted:{failure.label}"
    if failure.kind in {"turn_failed", "turn_interrupted"}:
        return failure.kind
    return f"turn_failed:{failure.label}"


VALUE_LIMIT = "input-retry:limit"
VALUE_INTERVAL = "input-retry:interval"


def short_summary(config: dict[str, Any]) -> str:
    current = settings(config)
    if not current.enabled:
        return "resend off"
    return f"resend {current.limit}x/{current.interval_seconds:g}s"


def panel_rows(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    current = settings(config)
    state = f"{current.limit} attempts" if current.enabled else "off"
    return (
        [
            f"Resend after upstream error  [{state}]  (0 disables, max {LIMIT_MAX})",
            f"Resend interval  [{current.interval_seconds:g}s]  ({INTERVAL_MIN_SECONDS}-{INTERVAL_MAX_SECONDS}s)",
            "  Transient errors only (overload, 5xx, connection, stream cut); the original message is resent"
            " with a marker line. Others fail at once and delivery moves to the next input.",
        ],
        [VALUE_LIMIT, VALUE_INTERVAL, "__info__"],
    )


def with_panel_rows(rows: list[str], values: list[str], config: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Insert the resend rows ahead of a trailing Back row."""

    extra_rows, extra_values = panel_rows(config)
    cut = len(values) - 1 if values and values[-1] == "back" else len(values)
    return [*rows[:cut], *extra_rows, *rows[cut:]], [*values[:cut], *extra_values, *values[cut:]]


def handles(value: str) -> bool:
    return value in {VALUE_LIMIT, VALUE_INTERVAL}


def prompt_label(value: str) -> str:
    if value == VALUE_LIMIT:
        return f"Resend attempts after a transient upstream error (0-{LIMIT_MAX}; 0 disables; blank keeps)"
    return f"Seconds between resends ({INTERVAL_MIN_SECONDS}-{INTERVAL_MAX_SECONDS}; blank keeps)"


def apply(config: dict[str, Any], value: str, entered: Any) -> list[str]:
    text = str(entered or "").strip()
    if not text:
        return ["Resend setting unchanged."]
    try:
        number = int(text)
    except ValueError:
        return ["Enter a whole number."]
    section = config.get(CONFIG_KEY)
    if not isinstance(section, dict):
        section = config[CONFIG_KEY] = {}
    if value == VALUE_LIMIT:
        section["limit"] = max(0, min(LIMIT_MAX, number))
        current = settings(config)
        return [f"Resend after upstream error {'off' if not current.enabled else f'set to {current.limit} attempts'}."]
    section["interval_seconds"] = max(INTERVAL_MIN_SECONDS, min(INTERVAL_MAX_SECONDS, number))
    return [f"Resend interval set to {settings(config).interval_seconds:g}s."]


__all__ = [
    "CODEX_TOOL_ITEM_TYPES",
    "CONFIG_KEY",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_LIMIT",
    "MARKER_PREFIX",
    "RetrySettings",
    "TurnFailure",
    "classify_claude_api_error",
    "classify_codex_turn",
    "decode_failed_state",
    "encode_failed_state",
    "is_failed_state",
    "marker",
    "next_retry",
    "pending_retry",
    "resend_message",
    "settings",
    "terminal_reason",
]
