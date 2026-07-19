from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ciel_runtime_support.settings_repository import JsonSettingsRepository


@dataclass(frozen=True)
class ToolGuardHookPolicy:
    events_with_matcher: tuple[str, ...]
    events_without_matcher: tuple[str, ...]
    legacy_markers: tuple[str, ...] = (
        "claude-any-tool-guard",
        "@oneciel-ai/claude-any",
        "/claude-any/",
        "\\claude-any\\",
    )


@dataclass(frozen=True)
class ToolGuardHookServices:
    repository: JsonSettingsRepository
    install_legacy_shim: Callable[[], None]
    warn: Callable[[str], None]


def install_tool_guard_hook_settings(
    command: str | None,
    policy: ToolGuardHookPolicy,
    services: ToolGuardHookServices,
) -> None:
    if not command:
        services.warn("tool guard hook was not installed; ciel-runtime-tool-guard was not found.")
        return
    services.install_legacy_shim()
    settings_path = services.repository.path
    settings = services.repository.load("tool_guard")
    if settings is None:
        services.warn(f"could not read {settings_path}; tool guard hook was not installed.")
        return
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        services.warn(f"{settings_path} has non-object hooks; tool guard hook was not installed.")
        return

    changed = False
    all_events = tuple((event, True) for event in policy.events_with_matcher) + tuple(
        (event, False) for event in policy.events_without_matcher
    )
    for event, with_matcher in all_events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            services.warn(f"{settings_path} hooks.{event} is not a list; tool guard hook was not installed.")
            return
        event_changed, existing = _normalize_event_groups(
            groups,
            command,
            with_matcher,
            policy.legacy_markers,
        )
        changed = changed or event_changed
        if not existing:
            group: dict[str, Any] = {"hooks": [{"type": "command", "command": command}]}
            if with_matcher:
                group["matcher"] = "*"
            groups.append(group)
            changed = True

    if changed:
        services.repository.save(settings, "tool_guard")


def _normalize_event_groups(
    groups: list[Any],
    command: str,
    with_matcher: bool,
    legacy_markers: tuple[str, ...],
) -> tuple[bool, bool]:
    changed = False
    existing = False
    cleaned_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            cleaned_groups.append(group)
            continue
        handlers = group["hooks"]
        cleaned_handlers: list[Any] = []
        group_has_current = False
        for handler in handlers:
            if not isinstance(handler, dict):
                cleaned_handlers.append(handler)
                continue
            handler_command = str(handler.get("command", ""))
            if any(marker in handler_command for marker in legacy_markers):
                changed = True
                continue
            if "ciel-runtime-tool-guard" in handler_command:
                if existing or group_has_current:
                    changed = True
                    continue
                existing = True
                group_has_current = True
                if handler.get("command") != command:
                    handler["command"] = command
                    changed = True
            cleaned_handlers.append(handler)
        if cleaned_handlers != handlers:
            group["hooks"] = cleaned_handlers
            changed = True
        if not cleaned_handlers:
            changed = True
            continue
        if group_has_current and with_matcher and group.get("matcher") != "*":
            group["matcher"] = "*"
            changed = True
        cleaned_groups.append(group)
    if cleaned_groups != groups:
        groups[:] = cleaned_groups
    return changed, existing
