"""Typed service composition for runtime installation and maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .agy_installer import AgyInstaller, AgyInstallerPorts
from .install_diagnostics import (
    InstallDiagnosticsPorts,
    InstallDiagnosticsService,
    InstallDiagnosticsSettings,
)
from .package_lifecycle import (
    NpmPackageLifecycle,
    NpmPackageLifecyclePorts,
    SelfUpdateLifecycle,
    SelfUpdatePorts,
)
from .runtime_restart import RuntimeRestartPorts, RuntimeRestartService, RuntimeRestartSettings
from .runtime_upgrade import (
    RuntimeUpgradeNpmPorts,
    RuntimeUpgradeService,
    RuntimeUpgradeSettings,
    RuntimeUpgradeToolPorts,
)


@dataclass(frozen=True, slots=True)
class MaintenanceNpmPorts:
    find_executable: Callable[[str], str | None]
    install_command: Callable[..., list[str]]
    run_upgrade: Callable[..., tuple[int, str]]
    add_prefix_bin: Callable[[Path], None]
    latest_version: Callable[..., str | None]
    version_newer: Callable[[str, str], bool]
    output: Callable[..., None]
    current_prefix: Callable[[], Path | None]


@dataclass(frozen=True, slots=True)
class MaintenancePackagePorts:
    entrypoint: Path
    environment: Mapping[str, str]
    package_root: Callable[[Path], Path | None]
    prefix_from_root: Callable[[Path], Path | None]
    running_from_package: Callable[[Path, Mapping[str, str]], bool]
    global_package_root: Callable[..., Path | None]
    global_install_command: Callable[..., list[str]]
    current_root: Callable[[], Path | None]
    running_from_npm: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class MaintenanceDiagnosticPorts:
    home: Path
    platform_name: str
    extra_dirs: Callable[[], list[Path]]
    parse_version: Callable[[str], tuple[int, ...]]
    diagnostics: Callable[[], list[dict[str, str]]]
    stdin_isatty: Callable[[], bool]
    stdout_isatty: Callable[[], bool]
    write_error: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class MaintenanceRestartPorts:
    argv: list[str]
    executable: str
    environment: dict[str, str]
    execv: Callable[..., Any]
    call: Callable[..., int]


@dataclass(frozen=True, slots=True)
class MaintenanceUpdatePorts:
    version: str
    forced_environment: Callable[[], dict[str, str]]
    restart_after_update: Callable[..., None]
    claude_version: Callable[[str], str]
    codex_version: Callable[[str], str]
    install_claude: Callable[[], str | None]
    install_codex: Callable[[], str | None]
    install_agy: Callable[[], str | None]
    update_agy: Callable[[str, bool], str]


@dataclass(frozen=True, slots=True)
class MaintenanceAgyPorts:
    manifest_base_url: str
    user_bin_dir: Callable[[], Path]


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceServices:
    npm: MaintenanceNpmPorts
    package: MaintenancePackagePorts
    diagnostics: MaintenanceDiagnosticPorts
    restart: MaintenanceRestartPorts
    update: MaintenanceUpdatePorts
    agy: MaintenanceAgyPorts

    def current_package_root(self) -> Path | None:
        return self.package.package_root(self.package.entrypoint)

    def current_prefix(self) -> Path | None:
        root = self.current_package_root()
        return self.package.prefix_from_root(root) if root else None

    def running_from_npm_package(self) -> bool:
        return self.package.running_from_package(
            self.package.entrypoint, self.package.environment
        )

    def npm_lifecycle(self) -> NpmPackageLifecycle:
        return NpmPackageLifecycle(
            NpmPackageLifecyclePorts(
                self.npm.find_executable,
                self.npm.current_prefix,
                self.npm.install_command,
                self.npm.run_upgrade,
                self.npm.add_prefix_bin,
                self.npm.latest_version,
                self.npm.version_newer,
                self.npm.output,
            )
        )

    def install_diagnostics(self) -> InstallDiagnosticsService:
        return InstallDiagnosticsService(
            settings=InstallDiagnosticsSettings(
                self.diagnostics.home,
                self.package.environment,
                self.diagnostics.platform_name == "nt",
            ),
            ports=InstallDiagnosticsPorts(
                extra_dirs=self.diagnostics.extra_dirs,
                package_root=self.package.package_root,
                current_root=self.current_package_root,
                parse_version=self.diagnostics.parse_version,
                diagnostics=self.diagnostics.diagnostics,
                stdin_isatty=self.diagnostics.stdin_isatty,
                stdout_isatty=self.diagnostics.stdout_isatty,
                write_error=self.diagnostics.write_error,
            ),
        )

    def restart_service(self) -> RuntimeRestartService:
        return RuntimeRestartService(
            settings=RuntimeRestartSettings(
                self.restart.argv,
                self.restart.executable,
                self.restart.environment,
                platform_name=self.diagnostics.platform_name,
            ),
            ports=RuntimeRestartPorts(
                current_package_root=self.current_package_root,
                global_package_root=self.package.global_package_root,
                find_executable=self.npm.find_executable,
                execv=self.restart.execv,
                call=self.restart.call,
            ),
        )

    def self_update(self) -> SelfUpdateLifecycle:
        return SelfUpdateLifecycle(
            self.update.version,
            SelfUpdatePorts(
                self.package.running_from_npm,
                self.npm.find_executable,
                self.npm.latest_version,
                self.npm.version_newer,
                self.package.current_root,
                self.package.prefix_from_root,
                self.package.global_install_command,
                self.update.forced_environment,
                self.update.restart_after_update,
                self.npm.output,
            ),
        )

    def upgrade(self) -> RuntimeUpgradeService:
        return RuntimeUpgradeService(
            settings=RuntimeUpgradeSettings(
                self.update.version, self.package.environment
            ),
            npm=RuntimeUpgradeNpmPorts(
                find_executable=self.npm.find_executable,
                latest_version=self.npm.latest_version,
                version_newer=self.npm.version_newer,
                current_package_root=self.package.current_root,
                package_prefix=self.package.prefix_from_root,
                current_prefix=self.npm.current_prefix,
                global_install_command=self.package.global_install_command,
                runtime_install_command=self.npm.install_command,
                run_command=self.npm.run_upgrade,
            ),
            tools=RuntimeUpgradeToolPorts(
                claude_version=self.update.claude_version,
                codex_version=self.update.codex_version,
                install_claude=self.update.install_claude,
                install_codex=self.update.install_codex,
                install_agy=self.update.install_agy,
                update_agy=self.update.update_agy,
            ),
            output=lambda message: self.npm.output(message, flush=True),
        )

    def agy_installer(self) -> AgyInstaller:
        return AgyInstaller(
            self.agy.manifest_base_url,
            AgyInstallerPorts(
                self.agy.user_bin_dir,
                self.update.forced_environment,
                self.npm.find_executable,
                self.npm.version_newer,
                self.npm.run_upgrade,
                self.npm.output,
            ),
        )


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceServicesCompatibilityApi:
    services: Callable[[], RuntimeMaintenanceServices]

    def npm_lifecycle(self) -> NpmPackageLifecycle:
        return self.services().npm_lifecycle()

    def current_prefix(self) -> Path | None:
        return self.services().current_prefix()

    def running_from_npm_package(self) -> bool:
        return self.services().running_from_npm_package()

    def current_package_root(self) -> Path | None:
        return self.services().current_package_root()

    def install_diagnostics(self) -> InstallDiagnosticsService:
        return self.services().install_diagnostics()

    def restart_service(self) -> RuntimeRestartService:
        return self.services().restart_service()

    def self_update(self) -> SelfUpdateLifecycle:
        return self.services().self_update()

    def upgrade(self) -> RuntimeUpgradeService:
        return self.services().upgrade()

    def agy_installer(self) -> AgyInstaller:
        return self.services().agy_installer()


__all__ = [
    "MaintenanceAgyPorts",
    "MaintenanceDiagnosticPorts",
    "MaintenanceNpmPorts",
    "MaintenancePackagePorts",
    "MaintenanceRestartPorts",
    "MaintenanceUpdatePorts",
    "RuntimeMaintenanceServices",
    "RuntimeMaintenanceServicesCompatibilityApi",
]
