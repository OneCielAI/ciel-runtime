"""Claude stream recovery setting and its prelaunch panel.

The ``claude_stream_recovery`` config section holds whether Claude stream
recovery is on and how many recovery attempts it may make. It is on by default
with one attempt; the attempt count is bounded to 1..2 however the file was
edited. Independent ``compact_resume`` defaults on and allows at most one
continuation of an observed unfinished task after a proven automatic compact.
The recovery policy reads these values without rewriting existing config files.
"""

from __future__ import annotations

import time
from typing import Any

CONFIG_KEY = "claude_stream_recovery"
DEFAULT_ENABLED = True
DEFAULT_MAX_ATTEMPTS = 1
DEFAULT_COMPACT_RESUME = True
MIN_ATTEMPTS = 1
MAX_ATTEMPTS_LIMIT = 2
SAFETY_TEXT = (
    "  Continues only a text-only reply cut by a server error (never one with a tool call); "
    f"attempts per chain {MIN_ATTEMPTS}-{MAX_ATTEMPTS_LIMIT}."
)
VALUE_TOGGLE = "stream-recovery:toggle"
VALUE_ATTEMPTS = "stream-recovery:attempts"
VALUE_INFO = "__info__"
VALUE_COMPACT = "stream-recovery:compact"


def default_config() -> dict[str, Any]:
    return {"enabled": DEFAULT_ENABLED, "max_attempts": DEFAULT_MAX_ATTEMPTS, "compact_resume": DEFAULT_COMPACT_RESUME}


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


def compact_resume(config: dict[str, Any]) -> bool:
    value = _section(config).get("compact_resume")
    return value if isinstance(value, bool) else DEFAULT_COMPACT_RESUME


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


def _store(config: dict[str, Any], *, is_enabled: bool, attempts: int, compact: bool | None = None) -> None:
    section = config.get(CONFIG_KEY)
    if not isinstance(section, dict):
        section = {}
        config[CONFIG_KEY] = section
    section["enabled"] = is_enabled
    section["max_attempts"] = attempts
    section["compact_resume"] = compact_resume(config) if compact is None else compact


def panel_rows(config: dict[str, Any], recent: list[dict[str, Any]] | None = None) -> tuple[list[str], list[str]]:
    state = "on" if enabled(config) else "off"
    return (
        [
            f"Claude stream recovery  [{state}]",
            f"Claude stream recovery attempts  [{max_attempts(config)}]  (toggle 1/2)",
            SAFETY_TEXT,
            recent_text(recent or []),
            f"Claude compact continuation  [{'on' if compact_resume(config) else 'off'}]",
            "  Auto compact only; one continuation of observed unfinished work, never manual /compact.",
        ],
        [VALUE_TOGGLE, VALUE_ATTEMPTS, VALUE_INFO, VALUE_INFO, VALUE_COMPACT, VALUE_INFO],
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

    return value in {VALUE_TOGGLE, VALUE_ATTEMPTS, VALUE_COMPACT, VALUE_INFO}


def toggle_enabled(config: dict[str, Any]) -> list[str]:
    is_enabled = not enabled(config)
    attempts = max_attempts(config)
    _store(config, is_enabled=is_enabled, attempts=attempts, compact=compact_resume(config))
    if is_enabled:
        return [f"Claude stream recovery on ({_attempts_text(attempts)})."]
    return ["Claude stream recovery off."]


def toggle_attempts(config: dict[str, Any]) -> list[str]:
    attempts = MAX_ATTEMPTS_LIMIT if max_attempts(config) == MIN_ATTEMPTS else MIN_ATTEMPTS
    _store(config, is_enabled=enabled(config), attempts=attempts)
    return [f"Claude stream recovery attempts set to {attempts}."]


def toggle_compact_resume(config: dict[str, Any]) -> list[str]:
    value = not compact_resume(config)
    _store(config, is_enabled=enabled(config), attempts=max_attempts(config), compact=value)
    return [f"Claude compact continuation {'on' if value else 'off'}."]


def apply(config: dict[str, Any], value: str) -> list[str]:
    if value == VALUE_COMPACT:
        return toggle_compact_resume(config)
    if value == VALUE_TOGGLE:
        return toggle_enabled(config)
    if value == VALUE_ATTEMPTS:
        return toggle_attempts(config)
    return []


__all__ = [
    "CONFIG_KEY",
    "DEFAULT_ENABLED",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_COMPACT_RESUME",
    "MAX_ATTEMPTS_LIMIT",
    "MIN_ATTEMPTS",
    "SAFETY_TEXT",
    "apply",
    "default_config",
    "enabled",
    "compact_resume",
    "handles",
    "max_attempts",
    "panel_rows",
    "recent_text",
    "short_summary",
    "summary",
    "toggle_attempts",
    "toggle_compact_resume",
    "toggle_enabled",
    "with_panel_rows",
]
