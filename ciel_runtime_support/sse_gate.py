"""User-controlled compact admission preference; never clears delivery ownership."""
from __future__ import annotations

from typing import Any, Mapping

SSE_GATE_KEY = "sse_gate_enabled"
SSE_GATE_MARKER = "CIEL_RUNTIME_SSE_GATE"
SSE_GATE_COMMAND = """---
description: Show SSE identity guards; legacy compact gate removed
argument-hint: [on|off|status]
---

CIEL_RUNTIME_SSE_GATE

Arguments: $ARGUMENTS

Handle locally, never forward upstream. The pre-compact lifecycle gate is removed.
Legacy on/off values do not enable a compact hold. Before pending SSE injection,
measured context >=90% requests one compact. Identity and delivery guards remain.
"""


def enabled(config: Mapping[str, Any]) -> bool:
    return config.get(SSE_GATE_KEY) is not False


def arguments(body: Mapping[str, Any]) -> str | None:
    # Historical markers and tool results must never mutate configuration.
    from .provider_failover import _last_user_text
    text = _last_user_text(body)
    if not any(line.strip() == SSE_GATE_MARKER for line in text.splitlines()):
        return None
    return next((line.split(":", 1)[1].strip() for line in text.splitlines()
                 if line.strip().lower().startswith("arguments:")), "status")


def command(value: str, load, save, status) -> list[str]:
    value = value.strip().lower()
    if value not in {"", "status", "on", "off"}:
        return ["Usage: /sse-gate on|off|status"]
    config = load()
    if value in {"on", "off"}:
        config[SSE_GATE_KEY] = value == "on"
        save(config)
    state = status()
    mode = "on" if enabled(config) else "off"
    effective = state.get("state") or "unknown"
    reason = state.get("reason") or "none"
    admission = {
        "held": "External-message injection: HELD by this gate; messages are not discarded.",
        "open": "External-message injection: not held by this gate; other delivery checks still apply.",
        "bypassed": "External-message injection: compact hold bypassed; other delivery checks still apply.",
    }.get(effective, "External-message injection: UNKNOWN; no release is confirmed.")
    return [f"SSE gate {mode}; {effective} ({reason}).",
            "Legacy pre-compact gate removed. Pending injection at measured context >=90% compacts once; boundary or bounded failure releases it.",
            "On/off controls the compact guard policy, not whether SSE reception is enabled.",
            admission,
            "Off bypasses compact recovery admission only. Identity, handoff, unreadable state, "
            "delivery ownership and uncertain-write protections remain active. "
            "No queue, cursor or compact evidence is cleared."]
