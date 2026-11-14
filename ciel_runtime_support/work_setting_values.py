"""One definition of the values every Work/SSE settings surface accepts.

The prelaunch menu, ``/work-mode settings``, the web Settings tab (REST
settings_update), ``ciel-runtimectl work set`` and the LLM-options path all
parse switches here, so a word accepted by one surface is accepted by all.
"""
from __future__ import annotations

TRUE_WORDS = ("on", "true", "yes", "1", "enable", "enabled")
FALSE_WORDS = ("off", "false", "no", "0", "disable", "disabled")
# Removes the stored value so the documented default applies again.
RESET_WORDS = ("default", "unset", "none", "null")
# work_plan_approval_ask is shown as ask/auto; on/off stay accepted.
APPROVAL_WORDS = {"ask": True, "ask user": True, "auto": False}

# SSE injection: one effective mode, written identically by every surface
# (menu, ctl, REST/web settings_update, console /btw_with_mcp on|off).
INJECTION_MODES = ("session", "btw_with_mcp")


class SettingValueError(ValueError):
    """A value outside the shared accepted set; nothing was saved."""


def switch(value, *, approval=False, reset=True):
    """Return True, False, or None for a reset word; raise for anything else."""
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower() if isinstance(value, str) else None
    if word is not None:
        if approval and word in APPROVAL_WORDS:
            return APPROVAL_WORDS[word]
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        if reset and word in RESET_WORDS:
            return None
    raise SettingValueError(switch_hint(approval=approval, reset=reset))


def switch_hint(*, approval=False, reset=True):
    words = ("ask", "auto", "on", "off") if approval else ("on", "off", "true", "false")
    tail = " (default resets)" if reset else ""
    return "use " + "/".join(words) + tail


def injection_mode(config):
    """The effective SSE injection mode.

    btw_with_mcp only while the side lane is on and its default lane is the
    durable inbox; every other stored combination injects as session input.
    """
    side = (config or {}).get("work_side_intake") or {}
    return ("btw_with_mcp" if side.get("enabled") is True
            and side.get("type", "btw_with_mcp") == "btw_with_mcp" else "session")


def injection_settings(config, value):
    """The canonical stored side-intake record for one injection mode.

    session turns the side lane off; btw_with_mcp turns it on. Both keep the
    lane type at its default (btw_with_mcp), which is exactly what the console
    ``/btw_with_mcp on|off`` writes, so a later ``work_side_intake_enabled on``
    from any surface restores the durable inbox.
    """
    if value not in INJECTION_MODES:
        raise SettingValueError("use session or btw_with_mcp")
    return {**((config or {}).get("work_side_intake") or {}),
            "enabled": value == "btw_with_mcp", "type": "btw_with_mcp"}


def integer_in(value, low, high, name):
    """Parse an integer setting inside the validator's own inclusive range."""
    try:
        parsed = int(str(value).strip()) if not isinstance(value, bool) else None
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or not low <= parsed <= high:
        raise SettingValueError(f"{name} must be an integer from {low} to {high}")
    return parsed
