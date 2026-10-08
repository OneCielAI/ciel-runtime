"""Opt-in answer to Claude Code's dangerous-removal prompt.

Claude Code asks before ``rm``-style removals of critical paths (drive roots
and their top-level folders, the working directory and its parents) even in
bypassPermissions mode, and denies the call itself after two minutes. With
this option on, the launcher exports ``CIEL_RUNTIME_AUTO_ALLOW_DANGEROUS_RM=1``
and ciel-runtime-tool-guard answers that PermissionRequest with ``allow``.
Codex has no such prompt under Ciel's launch policy (approval ``never``
forbids the command outright), so the option is Claude-only.
"""

from __future__ import annotations

from typing import Any, MutableMapping

CONFIG_KEY = "claude_dangerous_rm_auto_allow"
ENV_NAME = "CIEL_RUNTIME_AUTO_ALLOW_DANGEROUS_RM"


def enabled(config: dict[str, Any]) -> bool:
    return config.get(CONFIG_KEY) is True


def summary(config: dict[str, Any]) -> str:
    return f"Claude · auto-allow {'on' if enabled(config) else 'off'}"


def apply_launch_env(config: dict[str, Any], env: MutableMapping[str, str]) -> bool:
    """Export the switch for this launch only; never inherit a stale value."""
    if enabled(config):
        env[ENV_NAME] = "1"
        return True
    env.pop(ENV_NAME, None)
    return False


def panel_rows(config: dict[str, Any]) -> tuple[list[str], list[str]]:
    state = "on" if enabled(config) else "off"
    return (
        [
            f"Auto-allow Claude's critical-path removal prompt  [{state}]",
            "Back",
        ],
        ["toggle", "back"],
    )


def toggle(config: dict[str, Any]) -> list[str]:
    config[CONFIG_KEY] = not enabled(config)
    if config[CONFIG_KEY]:
        return [
            "Critical-path removal prompts will be answered allow (bypass-mode Claude sessions).",
            "Applies to Claude sessions launched after this change.",
        ]
    return [
        "Critical-path removal prompts will wait for an answer again.",
        "Applies to Claude sessions launched after this change.",
    ]


__all__ = ["CONFIG_KEY", "ENV_NAME", "apply_launch_env", "enabled", "panel_rows", "summary", "toggle"]
