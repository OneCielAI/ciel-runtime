"""Pure rate-limit configuration, window, and HTTP header policy."""

from __future__ import annotations

import math
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable


def configured_rpm(config: dict[str, Any], positive_int: Callable[[Any], int | None]) -> int | None:
    raw = config.get("rate_limit_rpm")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in (
        "0", "false", "off", "disable", "disabled", "none", "unset",
    ):
        return 0
    try:
        if int(raw) == 0:
            return 0
    except Exception:
        pass
    rpm = positive_int(raw)
    return rpm if rpm and rpm > 0 else None


def capacity(rpm: int) -> int:
    if rpm <= 1:
        return 1
    reserve = 1 if rpm <= 20 else max(1, math.ceil(rpm * 0.05))
    return max(1, rpm - reserve)


def recent_timestamps(
    timestamps: Any,
    now: float,
    window: float,
    *,
    include_future: bool,
) -> list[float]:
    result: list[float] = []
    for timestamp in timestamps or []:
        if not isinstance(timestamp, (int, float)):
            continue
        value = float(timestamp)
        age = now - value
        if age < window and (include_future or age >= 0.0):
            result.append(value)
    return sorted(result)


def retry_after_seconds(value: str | None, now: Callable[[], float] = time.time) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except Exception:
        pass
    try:
        return max(0.0, parsedate_to_datetime(text).timestamp() - now())
    except Exception:
        return None


def format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def first_header(headers: Any, names: list[str]) -> str | None:
    for name in names:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value:
            return str(value)
    return None


def first_integer(value: str | None) -> int | None:
    match = re.search(r"\d+", value) if value else None
    try:
        return int(match.group(0)) if match else None
    except Exception:
        return None


def reset_seconds(value: str | None, now: Callable[[], float] = time.time) -> float | None:
    if not value:
        return None
    text = value.strip()
    try:
        numeric = float(text)
        current = now()
        if numeric > 1e12:
            return max(0.0, numeric / 1000.0 - current)
        if numeric > current + 60.0:
            return max(0.0, numeric - current)
        return max(0.0, numeric)
    except Exception:
        return retry_after_seconds(text, now)
