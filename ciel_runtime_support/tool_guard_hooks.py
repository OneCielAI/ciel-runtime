from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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
    install_legacy_shim: Callable[[], None]
    warn: Callable[[str], None]
    chmod: Callable[[Path, int], None] = os.chmod
    process_id: Callable[[], int] = os.getpid
    time_ns: Callable[[], int] = time.time_ns


def install_tool_guard_hook_settings(
    settings_path: Path,
    command: str | None,
    policy: ToolGuardHookPolicy,
    services: ToolGuardHookServices,
) -> None:
    if not command:
        services.warn("tool guard hook was not installed; ciel-runtime-tool-guard was not found.")
        return
    services.install_legacy_shim()
    settings = _read_settings(settings_path, services)
    if settings is None:
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
        _write_settings(settings_path, settings, services)


def _read_settings(settings_path: Path, services: ToolGuardHookServices) -> dict[str, Any] | None:
    if not settings_path.exists():
        return {}
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as exc:
        services.warn(
            f"could not read {settings_path} ({type(exc).__name__}); tool guard hook was not installed."
        )
        return None
    return settings if isinstance(settings, dict) else {}


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


def _write_settings(
    settings_path: Path,
    settings: dict[str, Any],
    services: ToolGuardHookServices,
) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = settings_path.with_name(
        f"{settings_path.name}.{services.process_id()}.{services.time_ns()}.tmp"
    )
    temporary_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        services.chmod(temporary_path, 0o600)
    except Exception as exc:
        services.warn(f"could not restrict {temporary_path} permissions ({type(exc).__name__}).")
    temporary_path.replace(settings_path)
