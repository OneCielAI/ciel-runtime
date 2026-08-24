"""Runtime launch chooser projection."""

from __future__ import annotations

from typing import Any, Callable


def launch_panel_rows(
    config: dict[str, Any],
    *,
    current_provider: Callable[..., tuple[str, dict[str, Any]]],
    provider_label: Callable[..., str],
    claude_enabled: Callable[[str], bool],
    codex_enabled: Callable[[str], bool],
    agy_enabled: Callable[[str], bool],
) -> tuple[list[str], list[str]]:
    provider, provider_config = current_provider(config)
    family = provider_label(provider, provider_config)
    claude_suffix = "" if claude_enabled(provider) else f" [disabled: {family} provider selected]"
    codex_suffix = "" if codex_enabled(provider) else f" [disabled: {family} provider selected]"
    agy_suffix = "" if agy_enabled(provider) else " [disabled: select AGY provider]"
    kimi_suffix = "" if provider == "kimi" else " [disabled: select Kimi provider]"
    return (
        [
            f"Claude{claude_suffix}",
            f"Codex{codex_suffix}",
            f"AGY{agy_suffix}",
            f"Kimi{kimi_suffix}",
            "Grok Build",
            "ZCode",
            f"Codex app server{codex_suffix}",
            "Back",
        ],
        ["launch", "launch-codex", "launch-agy", "launch-kimi", "launch-grok", "launch-zcode", "launch-codex-app-server", "back"],
    )


__all__ = ["launch_panel_rows"]
