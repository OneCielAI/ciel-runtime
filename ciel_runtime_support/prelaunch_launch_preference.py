"""Persistence policy for the prelaunch menu's runtime choice."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


LAST_LAUNCH_ACTION_KEY = "last_launch_action"
REMEMBERED_LAUNCH_ACTIONS = frozenset(
    {
        "launch",
        "launch-codex",
        "launch-codex-app-server",
    }
)


def preferred_launch_action(
    config: dict[str, Any],
    provider: str,
    *,
    fallback: Callable[[str], str],
    supports_claude: Callable[[str], bool],
    supports_codex: Callable[[str], bool],
) -> str:
    """Return the remembered launch action when the provider can run it."""
    remembered = str(config.get(LAST_LAUNCH_ACTION_KEY) or "").strip()
    if remembered == "launch" and supports_claude(provider):
        return remembered
    if remembered in {"launch-codex", "launch-codex-app-server"} and supports_codex(provider):
        return remembered
    return fallback(provider)


def preferred_provider_launch_action(
    config: dict[str, Any],
    provider: str,
    supports_agy: Callable[[str], bool],
    supports_claude: Callable[[str], bool],
    supports_codex: Callable[[str], bool],
) -> str:
    """Resolve a remembered action with the provider's natural fallback."""

    def fallback(selected_provider: str) -> str:
        if supports_agy(selected_provider):
            return "launch-agy"
        return "launch-codex" if supports_codex(selected_provider) else "launch"

    return preferred_launch_action(
        config,
        provider,
        fallback=fallback,
        supports_claude=supports_claude,
        supports_codex=supports_codex,
    )


def remember_launch_action(config: dict[str, Any], action: str) -> bool:
    """Record a concrete menu launch choice and report whether it changed."""
    if action not in REMEMBERED_LAUNCH_ACTIONS:
        return False
    if config.get(LAST_LAUNCH_ACTION_KEY) == action:
        return False
    config[LAST_LAUNCH_ACTION_KEY] = action
    return True


__all__ = [
    "LAST_LAUNCH_ACTION_KEY",
    "REMEMBERED_LAUNCH_ACTIONS",
    "preferred_launch_action",
    "preferred_provider_launch_action",
    "remember_launch_action",
]
