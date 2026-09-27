"""Usage and limit signals that Codex and Claude upstreams attach to responses.

Header names come from the installed CLIs (codex.exe, claude.exe; checked
2026-09-27): Codex sends ``x-codex-{primary,secondary}-{used-percent,
window-minutes,reset-at}`` (percent 0-100, epoch seconds) and a 429 usage
error body ``{"error": {"type": "usage_limit_reached", "resets_at": ...}}``;
Claude sends ``anthropic-ratelimit-unified-{5h,7d}-{utilization,reset}``
(fraction 0-1, epoch seconds), ``-status`` (allowed, allowed_warning,
rejected) and ``-reset``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Mapping

# A 429 without any reset hint still takes the token out of rotation briefly.
DEFAULT_LIMIT_SECONDS = 3600.0
TRANSIENT_429_SECONDS = 60.0
_CODEX_WINDOW = re.compile(r"^x-codex(?:-(?P<limit>[a-z0-9_]+))?-(?P<window>primary|secondary)-used-percent$")


@dataclass(frozen=True, slots=True)
class UsageObservation:
    # window name -> (used percent 0-100, reset epoch seconds or 0)
    windows: dict[str, tuple[float, float]] = field(default_factory=dict)
    # The upstream refused because a usage limit is reached.
    limited: bool = False
    # When the limit lifts (epoch seconds); 0 when the upstream gave no hint.
    reset_at: float = 0.0
    # A throttle that is not a plan limit (short 429).
    transient: bool = False

    @property
    def empty(self) -> bool:
        return not self.windows and not self.limited and not self.transient

    def peak_percent(self) -> float:
        return max((used for used, _reset in self.windows.values()), default=0.0)


def _header(headers: Mapping[str, Any] | None, name: str) -> str:
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    value = getter(name) if callable(getter) else None
    if value is None:
        lowered = name.lower()
        for key, candidate in dict(headers).items():
            if str(key).lower() == lowered:
                value = candidate
                break
    return str(value).strip() if value is not None else ""


def _number(text: Any) -> float | None:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _epoch(value: Any, now: float) -> float:
    number = _number(value)
    if number is None or number <= 0:
        return 0.0
    if number > 1e12:  # milliseconds
        return number / 1000.0
    if number < 1e9:  # a delay in seconds
        return now + number
    return number


def _json(body: bytes | str | None) -> dict[str, Any]:
    if not body:
        return {}
    try:
        value = json.loads(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _retry_after(headers: Mapping[str, Any] | None, now: float) -> float:
    return _epoch(_header(headers, "retry-after"), now)


def codex_observation(
    headers: Mapping[str, Any] | None,
    *,
    status: int = 200,
    body: bytes | str | None = None,
    now: float,
) -> UsageObservation:
    active = _header(headers, "x-codex-active-limit").lower()
    windows: dict[str, tuple[float, float]] = {}
    names = [str(key).lower() for key in dict(headers or {}).keys()]
    for name in names:
        match = _CODEX_WINDOW.match(name)
        if not match:
            continue
        limit = match.group("limit") or ""
        if limit and limit != active:
            continue  # another model's limit does not constrain this request
        prefix = f"x-codex-{limit}-" if limit else "x-codex-"
        used = _number(_header(headers, name))
        if used is None:
            continue
        window = match.group("window")
        reset = _epoch(_header(headers, f"{prefix}{window}-reset-at"), now) or _epoch(
            _header(headers, f"{prefix}{window}-reset-after-seconds"), now
        )
        windows[f"{limit + ':' if limit else ''}{window}"] = (used, reset)
    if status != 429:
        return UsageObservation(windows=windows)
    error = _json(body).get("error")
    error = error if isinstance(error, dict) else {}
    kind = f"{error.get('type') or ''} {error.get('code') or ''}".lower()
    reset = _epoch(error.get("resets_at"), now) or _epoch(error.get("resets_in_seconds"), now)
    if "usage_limit" in kind or "rate_limit_reached" in kind or _header(headers, "x-codex-rate-limit-reached-type"):
        exhausted = [reset_at for used, reset_at in windows.values() if used >= 100.0 and reset_at]
        reset = reset or max(exhausted, default=0.0) or _retry_after(headers, now) or now + DEFAULT_LIMIT_SECONDS
        return UsageObservation(windows=windows, limited=True, reset_at=reset)
    return UsageObservation(
        windows=windows, transient=True, reset_at=_retry_after(headers, now) or now + TRANSIENT_429_SECONDS
    )


def anthropic_observation(
    headers: Mapping[str, Any] | None,
    *,
    status: int = 200,
    body: bytes | str | None = None,
    now: float,
) -> UsageObservation:
    windows: dict[str, tuple[float, float]] = {}
    for window in ("5h", "7d"):
        utilization = _number(_header(headers, f"anthropic-ratelimit-unified-{window}-utilization"))
        if utilization is None:
            continue
        reset = _epoch(_header(headers, f"anthropic-ratelimit-unified-{window}-reset"), now)
        windows[window] = (utilization * 100.0, reset)
    rejected = _header(headers, "anthropic-ratelimit-unified-status").lower() == "rejected"
    unified_reset = _epoch(_header(headers, "anthropic-ratelimit-unified-reset"), now)
    if rejected:
        return UsageObservation(
            windows=windows,
            limited=True,
            reset_at=unified_reset or _retry_after(headers, now) or now + DEFAULT_LIMIT_SECONDS,
        )
    if status != 429:
        return UsageObservation(windows=windows)
    error = _json(body).get("error")
    message = str((error or {}).get("message") or "").lower() if isinstance(error, dict) else ""
    if unified_reset or "usage limit" in message or "limit reached" in message:
        return UsageObservation(
            windows=windows,
            limited=True,
            reset_at=unified_reset or _retry_after(headers, now) or now + DEFAULT_LIMIT_SECONDS,
        )
    return UsageObservation(
        windows=windows, transient=True, reset_at=_retry_after(headers, now) or now + TRANSIENT_429_SECONDS
    )


__all__ = [
    "DEFAULT_LIMIT_SECONDS",
    "TRANSIENT_429_SECONDS",
    "UsageObservation",
    "anthropic_observation",
    "codex_observation",
]
