"""Compose runtime command assets, paths, and tool-guard policy."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .command_asset_installer import CommandAsset
from .runtime_asset_context import (
    RuntimeAssetCompatibilityPorts,
    RuntimeAssetContext,
    RuntimeAssetEffects,
    RuntimeAssetPaths,
    RuntimeCommandAssetCatalog,
    RuntimeExecutablePaths,
    RuntimeToolGuardPolicy,
)
from .settings_repository import JsonSettingsRepository, SettingsFileEffects
from .slash_command_assets import (
    ADVISOR_SLASH_COMMAND,
    API_KEYS_SLASH_COMMAND,
    CHANNEL_CLEAR_SLASH_COMMAND,
    CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS,
    CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS,
    CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS,
    CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS,
    CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
    CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS,
    CIEL_RUNTIME_VERSION_COMMAND_MARKERS,
    IMPORT_SESSION_SLASH_COMMAND,
    LLM_OPTIONS_SLASH_COMMAND,
    LLM_RESTORE_SLASH_COMMAND,
    LLM_SLIDER_SLASH_COMMAND,
    ROUTER_DEBUG_SLASH_COMMAND,
    VERSION_SLASH_COMMAND,
)
from .statusline_script import STATUSLINE_SCRIPT
from .tool_guard_hooks import DEFAULT_TOOL_GUARD_HOOK_POLICY


@dataclass(frozen=True, slots=True)
class RuntimeAssetPathBindings:
    home: Path
    source_file: Path
    settings_path: Path
    statusline_path: Path
    commands_dir: Path
    codex_prompts_dir_name: str
    platform_path: Callable[..., Path]
    runtime_user_bin_dir: Callable[..., Path]
    agy_user_bin_dir: Callable[..., Path]


@dataclass(frozen=True, slots=True)
class RuntimeAssetAssemblyPorts:
    paths: RuntimeAssetPathBindings
    python_executable: str
    chmod: Callable[..., Any]
    environ: Mapping[str, str]
    log: Callable[..., Any]
    warning: Callable[[str], Any]
    compatibility: RuntimeAssetCompatibilityPorts


def build_runtime_asset_context(ports: RuntimeAssetAssemblyPorts) -> RuntimeAssetContext:
    def settings_repository() -> JsonSettingsRepository:
        return JsonSettingsRepository(
            path=ports.paths.settings_path,
            effects=SettingsFileEffects(log=ports.log),
        )

    standard_assets = {
        "router-debug.md": CommandAsset(
            ROUTER_DEBUG_SLASH_COMMAND, CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS
        ),
        "ciel-version.md": CommandAsset(
            VERSION_SLASH_COMMAND, CIEL_RUNTIME_VERSION_COMMAND_MARKERS
        ),
        "llm.md": CommandAsset(
            LLM_SLIDER_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS
        ),
        "llm-options.md": CommandAsset(
            LLM_OPTIONS_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS
        ),
        "llm-restore.md": CommandAsset(
            LLM_RESTORE_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS
        ),
        "channel-clear.md": CommandAsset(
            CHANNEL_CLEAR_SLASH_COMMAND, CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS
        ),
        "api-key.md": CommandAsset(
            API_KEYS_SLASH_COMMAND, CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS
        ),
        "api-keys.md": CommandAsset(
            API_KEYS_SLASH_COMMAND, CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS
        ),
        "ImportSession.md": CommandAsset(
            IMPORT_SESSION_SLASH_COMMAND, CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS
        ),
    }
    return RuntimeAssetContext(
        executable=RuntimeExecutablePaths(
            ports.paths.home,
            ports.paths.source_file,
            ports.paths.platform_path,
            ports.paths.runtime_user_bin_dir,
            ports.paths.agy_user_bin_dir,
            ports.python_executable,
        ),
        paths=RuntimeAssetPaths(
            ports.paths.source_file.resolve().parent,
            ports.paths.statusline_path,
            STATUSLINE_SCRIPT,
            ports.paths.commands_dir,
            ports.paths.codex_prompts_dir_name,
        ),
        effects=RuntimeAssetEffects(
            settings_repository,
            ports.chmod,
            ports.log,
            ports.warning,
            ports.environ,
        ),
        catalog=RuntimeCommandAssetCatalog(
            standard_assets,
            CommandAsset(
                ADVISOR_SLASH_COMMAND, CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS
            ),
            CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS,
            CommandAsset(
                IMPORT_SESSION_SLASH_COMMAND,
                CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS,
            ),
            CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS,
            CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
        ),
        tool_guard=RuntimeToolGuardPolicy(DEFAULT_TOOL_GUARD_HOOK_POLICY),
        compatibility=ports.compatibility,
    )


__all__ = [
    "RuntimeAssetAssemblyPorts",
    "RuntimeAssetPathBindings",
    "build_runtime_asset_context",
]
