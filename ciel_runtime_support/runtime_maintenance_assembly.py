"""Composition boundary for runtime installation and maintenance services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .runtime_maintenance_context import (
    RuntimeAgyPorts,
    RuntimeLifecyclePorts,
    RuntimeMaintenanceContext,
    RuntimePackagePorts,
    RuntimeUpgradeCommandPorts,
)
from .runtime_maintenance_services import RuntimeMaintenanceServices


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceCommandPorts:
    environment: Mapping[str, str]
    claude_version: Callable[[str], str]
    codex_version: Callable[[str], str]
    upgrade_runtime: Callable[[], int]
    upgrade_claude: Callable[[], int]
    upgrade_codex: Callable[[], int]
    upgrade_agy: Callable[[], int]


@dataclass(frozen=True, slots=True)
class RuntimeMaintenanceAssembly:
    service_graph: RuntimeMaintenanceServices
    commands: RuntimeMaintenanceCommandPorts

    def context(self) -> RuntimeMaintenanceContext:
        services = self.service_graph
        commands = self.commands
        return RuntimeMaintenanceContext(
            packages=RuntimePackagePorts(
                services.npm_lifecycle,
                commands.environment,
                commands.claude_version,
                commands.codex_version,
            ),
            lifecycle=RuntimeLifecyclePorts(
                services.install_diagnostics,
                services.restart_service,
                services.self_update,
                services.upgrade,
            ),
            agy=RuntimeAgyPorts(services.agy_installer),
            upgrade_commands=RuntimeUpgradeCommandPorts(
                commands.upgrade_runtime,
                commands.upgrade_claude,
                commands.upgrade_codex,
                commands.upgrade_agy,
            ),
        )


__all__ = ["RuntimeMaintenanceAssembly", "RuntimeMaintenanceCommandPorts"]
