"""Claude stream recovery setting and its prelaunch panel.

The ``claude_stream_recovery`` config section holds whether Claude stream
recovery is on and how many recovery attempts it may make. It is on by default
with one attempt; the attempt count is bounded to 1..2 however the file was
edited. The recovery policy reads ``enabled(config)`` and ``max_attempts(config)``.
"""

from __future__ import annotations

import time
from typing import Any

CONFIG_KEY = "claude_stream_recovery"
DEFAULT_ENABLED = True
DEFAULT_MAX_ATTEMPTS = 1
MIN_ATTEMPTS = 1
MAX_ATTEMPTS_LIMIT = 2
SAFETY_TEXT = (
    "  Continues only a text-only reply cut by a server error (never one with a tool call); "
    f"attempts per chain {MIN_ATTEMPTS}-{MAX_ATTEMPTS_LIMIT}."
)
VALUE_TOGGLE = "stream-recovery:toggle"
VALUE_ATTEMPTS = "stream-recovery:attempts"
VALUE_INFO = "__info__"


def default_config() -> dict[str, Any]:
    return {"enabled": DEFAULT_ENABLED, "max_attempts": DEFAULT_MAX_ATTEMPTS}


def _section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get(CONFIG_KEY)
    return section if isinstance(section, dict) else {}


def enabled(config: dict[str, Any]) -> bool:
    value = _section(config).get("enabled")
    return value if isinstance(value, bool) else DEFAULT_ENABLED


def max_attempts(config: dict[str, Any]) -> int:
    value = _section(config).get("max_attempts")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_MAX_ATTEMPTS
    return max(MIN_ATTEMPTS, min(MAX_ATTEMPTS_LIMIT, value))


def _attempts_text(count: int) -> str:
    return f"{count} attempt{'s' if count != 1 else ''}"


def summary(config: dict[str, Any]) -> str:
    if not enabled(config):
        return "Claude · recovery off"
    return f"Claude · recovery on · {_attempts_text(max_attempts(config))}"


def short_summary(config: dict[str, Any]) -> str:
    """The main-menu fragment, beside the dangerous delete summary."""

    return f"stream recovery {'on' if enabled(config) else 'off'}"


def recent_text(recent: list[dict[str, Any]]) -> str:
    """One row describing the last recovery decisions this router instance kept."""

    if not recent:
        return "  Recent: no mid-response server error seen"
    admitted = sum(1 for entry in recent if entry.get("status") == "admitted")
    last = recent[-1]
    reason = f" ({last.get('reason')})" if last.get("reason") else ""
    when = time.strftime("%H:%M", time.localtime(float(last.get("at") or 0)))
    return f"  Recent: last {last.get('status')}{reason} at {when} · {admitted} continued of {len(recent)}"


def _store(config: dict[str, Any], *, is_enabled: bool, attempts: int) -> None:
    section = config.get(CONFIG_KEY)
    if not isinstance(section, dict):
        section = {}
        config[CONFIG_KEY] = section
    section["enabled"] = is_enabled
    section["max_attempts"] = attempts


def panel_rows(config: dict[str, Any], recent: list[dict[str, Any]] | None = None) -> tuple[list[str], list[str]]:
    state = "on" if enabled(config) else "off"
    return (
        [
            f"Claude stream recovery  [{state}]",
            f"Claude stream recovery attempts  [{max_attempts(config)}]  (toggle 1/2)",
            SAFETY_TEXT,
            recent_text(recent or []),
        ],
        [VALUE_TOGGLE, VALUE_ATTEMPTS, VALUE_INFO, VALUE_INFO],
    )


def with_panel_rows(
    rows: list[str], values: list[str], config: dict[str, Any], recent: list[dict[str, Any]] | None = None
) -> tuple[list[str], list[str]]:
    """Insert the recovery rows ahead of a trailing Back row."""
    extra_rows, extra_values = panel_rows(config, recent)
    cut = len(values) - 1 if values and values[-1] == "back" else len(values)
    return (
        [*rows[:cut], *extra_rows, *rows[cut:]],
        [*values[:cut], *extra_values, *values[cut:]],
    )


def handles(value: str) -> bool:
    """Values this panel owns; the information rows do nothing when chosen."""

    return value in {VALUE_TOGGLE, VALUE_ATTEMPTS, VALUE_INFO}


def toggle_enabled(config: dict[str, Any]) -> list[str]:
    is_enabled = not enabled(config)
    attempts = max_attempts(config)
    _store(config, is_enabled=is_enabled, attempts=attempts)
    if is_enabled:
        return [f"Claude stream recovery on ({_attempts_text(attempts)})."]
    return ["Claude stream recovery off."]


def toggle_attempts(config: dict[str, Any]) -> list[str]:
    attempts = MAX_ATTEMPTS_LIMIT if max_attempts(config) == MIN_ATTEMPTS else MIN_ATTEMPTS
    _store(config, is_enabled=enabled(config), attempts=attempts)
    return [f"Claude stream recovery attempts set to {attempts}."]


def apply(config: dict[str, Any], value: str) -> list[str]:
    if value == VALUE_TOGGLE:
        return toggle_enabled(config)
    if value == VALUE_ATTEMPTS:
        return toggle_attempts(config)
    return []


__all__ = [
    "CONFIG_KEY",
    "DEFAULT_ENABLED",
    "DEFAULT_MAX_ATTEMPTS",
    "MAX_ATTEMPTS_LIMIT",
    "MIN_ATTEMPTS",
    "SAFETY_TEXT",
    "apply",
    "default_config",
    "enabled",
    "handles",
    "max_attempts",
    "panel_rows",
    "recent_text",
    "short_summary",
    "summary",
    "toggle_attempts",
    "toggle_enabled",
    "with_panel_rows",
]
