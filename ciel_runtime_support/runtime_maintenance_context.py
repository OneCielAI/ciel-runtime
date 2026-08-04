"""Runtime installation, update, diagnostics, and upgrade bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .agy_installer import AgyInstaller
from .install_diagnostics import InstallDiagnosticsService
from .package_lifecycle import NpmPackageLifecycle, SelfUpdateLifecycle
from .runtime_restart import RuntimeRestartService
from .runtime_upgrade import RuntimeUpgradeService


@dataclass(frozen=True, slots=True)
class RuntimePackagePorts:
    lifecycle: Callable[[], NpmPackageLifecycle]
    environment: Mapping[str, str]
    claude_version: Callable[[str], str]
    codex_version: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class RuntimeLifecyclePorts:
    diagnostics: Callable[[], InstallDiagnosticsService]
    restart: Callable[[], RuntimeRestartService]
    self_update: Callable[[], SelfUpdateLifecycle]
    upgrade: Callable[[], RuntimeUpgradeService]


@dataclass(frozen=True, slots=True)
class RuntimeAgyPorts:
    installer: Callable[[], AgyInstaller]


@dataclass(frozen=True, slots=True)
class RuntimeUpgradeCommandPorts:
    ciel_runtime: Callable[[], int]
    claude: Callable[[], int]
    codex: Callable[[], int]
    agy: Callable[[], int]


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceContext:
    packages: RuntimePackagePorts
    lifecycle: RuntimeLifecyclePorts
    agy: RuntimeAgyPorts
    upgrade_commands: RuntimeUpgradeCommandPorts

    def install_runtime_package_if_missing(
        self,
        *,
        executable_name: str,
        label: str,
        package_spec: str,
        skip_env: str,
    ) -> str | None:
        return self.packages.lifecycle().install_if_missing(
            executable_name=executable_name,
            label=label,
            package_spec=package_spec,
            skip_env=skip_env,
        )

    def run_runtime_npm_update_check(
        self,
        executable: str,
        *,
        executable_name: str,
        label: str,
        package_spec: str,
        skip_env: str,
        current_version: Callable[[str], str],
        enabled: bool = True,
    ) -> str:
        return self.packages.lifecycle().update_check(
            executable,
            executable_name=executable_name,
            label=label,
            package_spec=package_spec,
            skip_env=skip_env,
            current_version=current_version,
            enabled=enabled,
        )

    def run_claude_update_check(self, executable: str, enabled: bool = True) -> str:
        package = self.packages.environment.get(
            "CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest"
        )
        return self.run_runtime_npm_update_check(
            executable,
            executable_name="claude",
            label="Claude Code",
            package_spec=package,
            skip_env="CIEL_RUNTIME_SKIP_CLAUDE_UPDATE",
            current_version=self.packages.claude_version,
            enabled=enabled,
        )

    def run_codex_update_check(self, executable: str, enabled: bool = True) -> str:
        package = self.packages.environment.get(
            "CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest"
        )
        return self.run_runtime_npm_update_check(
            executable,
            executable_name="codex",
            label="Codex",
            package_spec=package,
            skip_env="CIEL_RUNTIME_SKIP_CODEX_UPDATE",
            current_version=self.packages.codex_version,
            enabled=enabled,
        )

    def install_claude_code_if_missing(self) -> str | None:
        package = self.packages.environment.get(
            "CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest"
        )
        return self.install_runtime_package_if_missing(
            executable_name="claude",
            label="Claude Code",
            package_spec=package,
            skip_env="CIEL_RUNTIME_SKIP_CLAUDE_INSTALL",
        )

    def install_codex_if_missing(self) -> str | None:
        package = self.packages.environment.get(
            "CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest"
        )
        return self.install_runtime_package_if_missing(
            executable_name="codex",
            label="Codex",
            package_spec=package,
            skip_env="CIEL_RUNTIME_SKIP_CODEX_INSTALL",
        )

    def launcher_candidate_dirs(self) -> list[Path]:
        return self.lifecycle.diagnostics().candidate_dirs()

    def launcher_candidates(self) -> list[Path]:
        return self.lifecycle.diagnostics().candidates()

    def launcher_version(self, path: Path, timeout: float = 5.0) -> str:
        return self.lifecycle.diagnostics().launcher_version(path, timeout)

    def install_diagnostics(self) -> list[dict[str, str]]:
        return self.lifecycle.diagnostics().diagnostics()

    def warn_if_multiple_installs(self) -> None:
        self.lifecycle.diagnostics().warn_if_multiple()

    def restart_user_args(self) -> list[str]:
        return self.lifecycle.restart().user_args()

    def restart_after_update(self, npm: str, package_root: Path | None = None) -> None:
        self.lifecycle.restart().restart(npm, package_root)

    def run_self_update_check(self, enabled: bool = True) -> bool:
        return self.lifecycle.self_update().run(enabled)

    def quiet_upgrade_ciel_runtime(self) -> int:
        return self.lifecycle.upgrade().ciel_runtime()

    def quiet_upgrade_claude_code(self) -> int:
        return self.lifecycle.upgrade().claude()

    def quiet_upgrade_codex(self) -> int:
        return self.lifecycle.upgrade().codex()

    def quiet_upgrade_agy(self) -> int:
        return self.lifecycle.upgrade().agy()

    def agy_manifest_name(self) -> str:
        return self.agy.installer().manifest_name()

    def agy_manifest_url(self) -> str:
        return self.agy.installer().manifest_url()

    def agy_latest_manifest(self, timeout: float = 15.0) -> dict[str, Any] | None:
        return self.agy.installer().latest_manifest(timeout)

    def install_agy_from_manifest(self, manifest: dict[str, Any]) -> str | None:
        return self.agy.installer().install_from_manifest(manifest)

    def install_agy_if_missing(self) -> str | None:
        return self.agy.installer().install_if_missing()

    def run_agy_update_check(self, executable: str, enabled: bool = True) -> str:
        return self.agy.installer().update_check(executable, enabled)

    def run_quiet_upgrade_and_exit(self) -> int:
        results = (
            self.upgrade_commands.ciel_runtime(),
            self.upgrade_commands.claude(),
            self.upgrade_commands.codex(),
            self.upgrade_commands.agy(),
        )
        return 0 if all(result == 0 for result in results) else 1


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceCompatibilityApi:
    """Late-bound compatibility API for the thin runtime facade."""

    context: Callable[[], RuntimeMaintenanceContext]

    def install_runtime_package_if_missing(self, **options: str) -> str | None:
        return self.context().install_runtime_package_if_missing(**options)

    def run_runtime_npm_update_check(
        self,
        executable: str,
        *,
        executable_name: str,
        label: str,
        package_spec: str,
        skip_env: str,
        current_version: Callable[[str], str],
        enabled: bool = True,
    ) -> str:
        return self.context().run_runtime_npm_update_check(
            executable,
            executable_name=executable_name,
            label=label,
            package_spec=package_spec,
            skip_env=skip_env,
            current_version=current_version,
            enabled=enabled,
        )

    def run_claude_update_check(self, executable: str, enabled: bool = True) -> str:
        return self.context().run_claude_update_check(executable, enabled)

    def run_codex_update_check(self, executable: str, enabled: bool = True) -> str:
        return self.context().run_codex_update_check(executable, enabled)

    def install_claude_code_if_missing(self) -> str | None:
        return self.context().install_claude_code_if_missing()

    def install_codex_if_missing(self) -> str | None:
        return self.context().install_codex_if_missing()

    def launcher_candidate_dirs(self) -> list[Path]:
        return self.context().launcher_candidate_dirs()

    def launcher_candidates(self) -> list[Path]:
        return self.context().launcher_candidates()

    def launcher_version(self, path: Path, timeout: float = 5.0) -> str:
        return self.context().launcher_version(path, timeout)

    def install_diagnostics(self) -> list[dict[str, str]]:
        return self.context().install_diagnostics()

    def warn_if_multiple_installs(self) -> None:
        self.context().warn_if_multiple_installs()

    def restart_user_args(self) -> list[str]:
        return self.context().restart_user_args()

    def restart_after_update(self, npm: str, package_root: Path | None = None) -> None:
        self.context().restart_after_update(npm, package_root)

    def run_self_update_check(self, enabled: bool = True) -> bool:
        return self.context().run_self_update_check(enabled)

    def quiet_upgrade_ciel_runtime(self) -> int:
        return self.context().quiet_upgrade_ciel_runtime()

    def quiet_upgrade_claude_code(self) -> int:
        return self.context().quiet_upgrade_claude_code()

    def quiet_upgrade_codex(self) -> int:
        return self.context().quiet_upgrade_codex()

    def quiet_upgrade_agy(self) -> int:
        return self.context().quiet_upgrade_agy()

    def agy_manifest_name(self) -> str:
        return self.context().agy_manifest_name()

    def agy_manifest_url(self) -> str:
        return self.context().agy_manifest_url()

    def agy_latest_manifest(self, timeout: float = 15.0) -> dict[str, Any] | None:
        return self.context().agy_latest_manifest(timeout)

    def install_agy_from_manifest(self, manifest: dict[str, Any]) -> str | None:
        return self.context().install_agy_from_manifest(manifest)

    def install_agy_if_missing(self) -> str | None:
        return self.context().install_agy_if_missing()

    def run_agy_update_check(self, executable: str, enabled: bool = True) -> str:
        return self.context().run_agy_update_check(executable, enabled)

    def run_quiet_upgrade_and_exit(self) -> int:
        return self.context().run_quiet_upgrade_and_exit()


__all__ = [
    "RuntimeAgyPorts",
    "RuntimeLifecyclePorts",
    "RuntimeMaintenanceCompatibilityApi",
    "RuntimeMaintenanceContext",
    "RuntimePackagePorts",
    "RuntimeUpgradeCommandPorts",
]
