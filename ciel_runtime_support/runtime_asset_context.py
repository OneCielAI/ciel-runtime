"""Executable discovery and runtime-owned command asset installation context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .command_asset_installer import CommandAsset, CommandAssetInstaller
from .executable_discovery import ExecutableDiscovery
from .statusline_settings import StatusLineServices, install_statusline_settings
from .tool_guard_hooks import (
    LegacyToolGuardShimInstaller,
    LegacyToolGuardShimServices,
    ToolGuardHookServices,
    install_tool_guard_hook_settings,
)


@dataclass(frozen=True, slots=True)
class RuntimeExecutablePaths:
    home: Path
    source_file: Path
    platform_path: Callable[[], str]
    runtime_bin_dir: Callable[[], Path]
    agy_bin_dir: Callable[[], Path]
    python_executable: str


@dataclass(frozen=True, slots=True)
class RuntimeAssetPaths:
    package_root: Path
    statusline_path: Path
    statusline_script: Path
    claude_commands_dir: Path
    codex_prompts_dir_name: str


@dataclass(frozen=True, slots=True)
class RuntimeAssetEffects:
    settings_repository: Callable[[], Any]
    chmod: Callable[[Path, int], None]
    log: Callable[[str, str], None]
    warn: Callable[[str], None]
    environ: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RuntimeCommandAssetCatalog:
    standard: dict[str, CommandAsset]
    advisor: CommandAsset
    advisor_markers: tuple[str, ...]
    import_session: CommandAsset
    import_markers: tuple[str, ...]
    stale_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeToolGuardPolicy:
    default_hook_policy: Any


@dataclass(frozen=True, slots=True)
class RuntimeAssetCompatibilityPorts:
    find_executable: Callable[[str], str | None]
    find_tool_guard: Callable[[], Path | None]
    tool_guard_command: Callable[[], str | None]
    install_legacy_shim: Callable[[], None]
    command_asset_installer: Callable[[Path], CommandAssetInstaller]
    remove_advisor_command: Callable[[], None]
    codex_prompts_dir: Callable[[Mapping[str, str] | None], Path]
    command_assets: Callable[[bool], dict[str, CommandAsset]]


@dataclass(frozen=True, slots=True)
class RuntimeAssetContext:
    executable: RuntimeExecutablePaths
    paths: RuntimeAssetPaths
    effects: RuntimeAssetEffects
    catalog: RuntimeCommandAssetCatalog
    tool_guard: RuntimeToolGuardPolicy
    compatibility: RuntimeAssetCompatibilityPorts

    def executable_discovery(self) -> ExecutableDiscovery:
        return ExecutableDiscovery(
            self.executable.home,
            self.executable.source_file,
            self.executable.platform_path,
            self.executable.runtime_bin_dir,
            self.executable.agy_bin_dir,
        )

    def executable_extra_dirs(self) -> list[Path]:
        return self.executable_discovery().extra_dirs()

    def find_executable(self, name: str) -> str | None:
        return self.executable_discovery().find(name)

    def resolve_executable(self, command: str) -> str:
        return self.executable_discovery().resolve(command)

    def resolve_mcp_process(
        self, command: str, args: list[str]
    ) -> tuple[str, list[str]]:
        return self.executable_discovery().resolve_mcp_process(
            command, args, self.compatibility.find_executable
        )

    @staticmethod
    def shell_command(args: list[str]) -> str:
        return ExecutableDiscovery.shell_command(args)

    def find_tool_guard(self) -> Path | None:
        return self.executable_discovery().find_tool_guard(self.find_executable)

    def tool_guard_command(self) -> str | None:
        script = self.compatibility.find_tool_guard()
        if script is None:
            return None
        command = (
            [self.executable.python_executable, str(script)]
            if script.suffix == ".py"
            else [str(script)]
        )
        return self.shell_command(command)

    def install_legacy_tool_guard_shim(self) -> None:
        LegacyToolGuardShimInstaller(
            LegacyToolGuardShimServices(
                package_root=self.paths.package_root,
                find_target=self.compatibility.find_tool_guard,
                chmod=self.effects.chmod,
                log=self.effects.log,
            )
        ).install()

    def install_tool_guard_hooks(self) -> None:
        install_tool_guard_hook_settings(
            self.compatibility.tool_guard_command(),
            self.tool_guard.default_hook_policy,
            ToolGuardHookServices(
                repository=self.effects.settings_repository(),
                install_legacy_shim=self.compatibility.install_legacy_shim,
                warn=self.effects.warn,
            ),
        )

    def install_statusline(self) -> None:
        install_statusline_settings(
            self.paths.statusline_path,
            self.paths.statusline_script,
            self.executable.python_executable,
            StatusLineServices(
                repository=self.effects.settings_repository(),
                warn=self.effects.warn,
            ),
        )

    def command_asset_installer(self, directory: Path) -> CommandAssetInstaller:
        return CommandAssetInstaller(directory, self.effects.warn)

    def remove_advisor_command(self) -> None:
        self.compatibility.command_asset_installer(
            self.paths.claude_commands_dir
        ).remove_one(
            "advisor.md", self.catalog.advisor_markers
        )

    def codex_prompts_dir(
        self, env: Mapping[str, str] | None = None
    ) -> Path:
        environment = env or self.effects.environ
        raw_home = environment.get("CODEX_HOME")
        home = (
            Path(raw_home).expanduser()
            if raw_home
            else self.executable.home / ".codex"
        )
        return home / self.paths.codex_prompts_dir_name

    def install_codex_prompts(
        self, env: Mapping[str, str] | None = None
    ) -> None:
        self.compatibility.command_asset_installer(
            self.compatibility.codex_prompts_dir(env)
        ).install_one(
            "ImportSession.md", self.catalog.import_session
        )

    def disable_codex_prompts(
        self, env: Mapping[str, str] | None = None
    ) -> None:
        self.compatibility.command_asset_installer(
            self.compatibility.codex_prompts_dir(env)
        ).remove_one(
            "ImportSession.md", self.catalog.import_markers
        )

    def command_assets(self, include_advisor: bool = True) -> dict[str, CommandAsset]:
        assets = dict(self.catalog.standard)
        if include_advisor:
            assets["advisor.md"] = self.catalog.advisor
        return assets

    def install_slash_commands(self, include_advisor: bool = True) -> None:
        if not include_advisor:
            self.compatibility.remove_advisor_command()
        self.compatibility.command_asset_installer(
            self.paths.claude_commands_dir
        ).install_all(
            self.compatibility.command_assets(include_advisor),
            stale_glob="llm-*.md",
            stale_markers=self.catalog.stale_markers,
        )

    def disable_slash_commands(self) -> None:
        self.compatibility.command_asset_installer(
            self.paths.claude_commands_dir
        ).remove_all(
            self.compatibility.command_assets(True),
            stale_glob="llm-*.md",
            stale_markers=self.catalog.stale_markers,
        )


@dataclass(frozen=True, slots=True)
class RuntimeAssetCompatibilityApi:
    context: Callable[[], RuntimeAssetContext]

    def executable_discovery(self) -> ExecutableDiscovery:
        return self.context().executable_discovery()

    def executable_extra_dirs(self) -> list[Path]:
        return self.context().executable_extra_dirs()

    def find_executable(self, name: str) -> str | None:
        return self.context().find_executable(name)

    def resolve_executable(self, command: str) -> str:
        return self.context().resolve_executable(command)

    def resolve_mcp_process(self, *args: Any, **kwargs: Any) -> tuple[str, list[str]]:
        return self.context().resolve_mcp_process(*args, **kwargs)

    def shell_command(self, args: list[str]) -> str:
        return self.context().shell_command(args)

    def find_tool_guard(self) -> Path | None:
        return self.context().find_tool_guard()

    def tool_guard_command(self) -> str | None:
        return self.context().tool_guard_command()

    def install_legacy_tool_guard_shim(self) -> None:
        self.context().install_legacy_tool_guard_shim()

    def install_tool_guard_hooks(self) -> None:
        self.context().install_tool_guard_hooks()

    def install_statusline(self) -> None:
        self.context().install_statusline()

    def command_asset_installer(self, directory: Path) -> CommandAssetInstaller:
        return self.context().command_asset_installer(directory)

    def remove_advisor_command(self) -> None:
        self.context().remove_advisor_command()

    def codex_prompts_dir(self, *args: Any, **kwargs: Any) -> Path:
        return self.context().codex_prompts_dir(*args, **kwargs)

    def install_codex_prompts(self, *args: Any, **kwargs: Any) -> None:
        self.context().install_codex_prompts(*args, **kwargs)

    def disable_codex_prompts(self, *args: Any, **kwargs: Any) -> None:
        self.context().disable_codex_prompts(*args, **kwargs)

    def command_assets(self, include_advisor: bool = True) -> dict[str, CommandAsset]:
        return self.context().command_assets(include_advisor)

    def install_slash_commands(self, include_advisor: bool = True) -> None:
        self.context().install_slash_commands(include_advisor)

    def disable_slash_commands(self) -> None:
        self.context().disable_slash_commands()


__all__ = [
    "RuntimeAssetCompatibilityApi",
    "RuntimeAssetCompatibilityPorts",
    "RuntimeAssetContext",
    "RuntimeAssetEffects",
    "RuntimeAssetPaths",
    "RuntimeCommandAssetCatalog",
    "RuntimeExecutablePaths",
    "RuntimeToolGuardPolicy",
]
