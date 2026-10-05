"""Retry an upstream that answers "overloaded" before the CLI sees the error.

Celly (2026-10-05): chatgpt.com answered Codex routed requests with HTTP 500
"We're currently experiencing high demand, which may cause temporary errors."
The router passed each one straight back; Codex retried five times within a
few seconds and then failed the turn, so a short overload stopped the agent.
Nothing has been written to the client at that point, so the router waits and
retries for up to ``budget`` seconds, honouring ``Retry-After``.  A 429 is
retried only when it reads as overload: a usage limit or exhausted quota does
not go away by waiting a minute.

When the budget runs out the upstream error is passed on with a note of what
was retried, so the CLI shows why the turn stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

OVERLOAD_STATUSES = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524, 529})
DEFAULT_BUDGET_SECONDS = 60.0
WAIT_SCHEDULE_SECONDS = (2.0, 4.0, 8.0, 16.0, 30.0)
MAX_RETRY_AFTER_SECONDS = 60.0
_OVERLOAD_MARKERS = (
    "high demand",
    "overload",
    "capacity",
    "temporarily unavailable",
    "temporary errors",
    "try again later",
    "server is busy",
    "slow down",
)
_NOT_TRANSIENT_MARKERS = (
    "usage_limit",
    "usage limit",
    "insufficient_quota",
    "quota",
    "billing",
)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").lower()


def is_overload(status: int, raw: bytes) -> bool:
    text = _text(raw)
    if any(marker in text for marker in _NOT_TRANSIENT_MARKERS):
        return False
    if status in OVERLOAD_STATUSES:
        return True
    return status == 429 and any(marker in text for marker in _OVERLOAD_MARKERS)


def _retry_after(headers: Mapping[str, Any] | None) -> float | None:
    if headers is None:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return max(0.0, min(MAX_RETRY_AFTER_SECONDS, seconds))


@dataclass(slots=True)
class OverloadRetryState:
    """One request's retries: how many and how long the router has waited."""

    budget_seconds: float = DEFAULT_BUDGET_SECONDS
    attempts: int = 0
    waited_seconds: float = 0.0

    def next_wait(self, status: int, raw: bytes, headers: Mapping[str, Any] | None = None) -> float | None:
        """Seconds to wait before the next try, or None to pass the error on."""

        if not is_overload(status, raw):
            return None
        remaining = self.budget_seconds - self.waited_seconds
        if remaining <= 0:
            return None
        hinted = _retry_after(headers)
        scheduled = WAIT_SCHEDULE_SECONDS[min(self.attempts, len(WAIT_SCHEDULE_SECONDS) - 1)]
        wait = min(hinted if hinted is not None else scheduled, remaining)
        self.attempts += 1
        self.waited_seconds += wait
        return wait

    def note(self) -> str:
        return (
            f"Ciel Runtime retried this request {self.attempts} time(s) over "
            f"{self.waited_seconds:.0f}s because the upstream reported overload; giving up."
        )


def annotate(raw: bytes, note: str) -> bytes:
    """The error body with ``note`` in front of its message, same shape otherwise."""

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        text = raw.decode("utf-8", errors="replace").strip()
        return f"{note} Upstream: {text}".encode("utf-8") if text else note.encode("utf-8")
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            error["message"] = f"{note} Upstream: {error['message']}"
            return json.dumps(value).encode("utf-8")
        if isinstance(value.get("detail"), str):
            value["detail"] = f"{note} Upstream: {value['detail']}"
            return json.dumps(value).encode("utf-8")
        if isinstance(value.get("message"), str):
            value["message"] = f"{note} Upstream: {value['message']}"
            return json.dumps(value).encode("utf-8")
    return json.dumps({"error": {"message": note, "upstream": value}}).encode("utf-8")


def message_excerpt(raw: bytes, limit: int = 160) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            error = value.get("error")
            if isinstance(error, dict) and error.get("message"):
                text = str(error["message"])
            elif value.get("detail"):
                text = str(value["detail"])
    except ValueError:
        pass
    return " ".join(text.split())[:limit]


__all__ = [
    "DEFAULT_BUDGET_SECONDS",
    "OVERLOAD_STATUSES",
    "OverloadRetryState",
    "annotate",
    "is_overload",
    "message_excerpt",
]
