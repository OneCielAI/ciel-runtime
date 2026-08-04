"""Router shutdown and listener-replacement bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .router_process_lifecycle import (
    ClockPorts,
    RouterProcessConfig,
    RouterStatePorts,
    RouterTerminationPorts,
    ensure_port_available,
    stop_router_processes,
    stop_with_guarantee,
    terminate_health_pid,
    terminate_pid_file,
)


@dataclass(frozen=True, slots=True)
class RouterListenerPorts:
    platform_name: str
    bind_host: Callable[[], str]
    windows_pids: Callable[[int, str], list[int]]
    posix_pids: Callable[[int, str], list[int]]


@dataclass(frozen=True, slots=True)
class RouterProcessEffects:
    terminate_pid: Callable[[int, str, bool], bool]
    pid_is_running: Callable[[int], bool]
    protected_pids: Callable[[], tuple[int, int]]
    now: Callable[[], float]
    sleep: Callable[[float], None]


@dataclass(frozen=True, slots=True)
class RouterProcessCompatibilityPorts:
    terminate_pid_file: Callable[[Path, str, bool], bool]
    terminate_health: Callable[[dict[str, Any] | None, bool], bool]
    stop_processes: Callable[[bool], bool]
    listener_pids: Callable[[], list[int]]


@dataclass(frozen=True, slots=True)
class RouterProcessContext:
    config: RouterProcessConfig
    state: RouterStatePorts
    listener: RouterListenerPorts
    effects: RouterProcessEffects
    compatibility: RouterProcessCompatibilityPorts

    def listener_pids(self) -> list[int]:
        host = self.listener.bind_host()
        if self.listener.platform_name == "nt":
            return self.listener.windows_pids(self.config.router_port, host)
        return self.listener.posix_pids(self.config.router_port, host)

    def termination_ports(self) -> RouterTerminationPorts:
        return RouterTerminationPorts(
            terminate_pid=self.effects.terminate_pid,
            terminate_pid_file=self.compatibility.terminate_pid_file,
            terminate_health=self.compatibility.terminate_health,
            stop_processes=self.compatibility.stop_processes,
            listener_pids=self.compatibility.listener_pids,
        )

    def terminate_pid_file(
        self,
        path: Path,
        label: str,
        quiet: bool = False,
    ) -> bool:
        return terminate_pid_file(
            path,
            label,
            quiet,
            terminate_pid=self.effects.terminate_pid,
            pid_is_running=self.effects.pid_is_running,
        )

    def terminate_health_pid(
        self,
        health: dict[str, Any] | None,
        quiet: bool = True,
    ) -> bool:
        return terminate_health_pid(
            health,
            quiet,
            config=self.config,
            state=self.state,
            terminate_pid=self.effects.terminate_pid,
            protected_pids=self.effects.protected_pids(),
        )

    def stop_processes(self, quiet: bool = False) -> bool:
        return stop_router_processes(
            quiet,
            config=self.config,
            state=self.state,
            termination=self.termination_ports(),
        )

    def ensure_port_available(
        self,
        reason: str,
        health: dict[str, Any] | None = None,
        max_wait_seconds: float = 5.0,
    ) -> None:
        ensure_port_available(
            reason,
            health,
            max_wait_seconds,
            config=self.config,
            state=self.state,
            termination=self.termination_ports(),
            clock=ClockPorts(now=self.effects.now, sleep=self.effects.sleep),
        )

    def stop_with_guarantee(
        self,
        reason: str,
        max_wait_seconds: float = 5.0,
        quiet: bool = True,
    ) -> bool:
        return stop_with_guarantee(
            reason,
            max_wait_seconds,
            quiet,
            config=self.config,
            state=self.state,
            termination=self.termination_ports(),
            clock=ClockPorts(now=self.effects.now, sleep=self.effects.sleep),
        )


@dataclass(frozen=True, slots=True)
class RouterProcessCompatibilityApi:
    context: Callable[[], RouterProcessContext]

    def config(self) -> RouterProcessConfig:
        return self.context().config

    def state_ports(self) -> RouterStatePorts:
        return self.context().state

    def termination_ports(self) -> RouterTerminationPorts:
        return self.context().termination_ports()

    def listener_pids(self) -> list[int]:
        return self.context().listener_pids()

    def terminate_pid_file(
        self,
        path: Path,
        label: str,
        quiet: bool = False,
    ) -> bool:
        return self.context().terminate_pid_file(path, label, quiet)

    def terminate_health_pid(
        self,
        health: dict[str, Any] | None,
        quiet: bool = True,
    ) -> bool:
        return self.context().terminate_health_pid(health, quiet)

    def stop_processes(self, quiet: bool = False) -> bool:
        return self.context().stop_processes(quiet)

    def ensure_port_available(
        self,
        reason: str,
        health: dict[str, Any] | None = None,
        max_wait_seconds: float = 5.0,
    ) -> None:
        self.context().ensure_port_available(reason, health, max_wait_seconds)

    def stop_with_guarantee(
        self,
        reason: str,
        max_wait_seconds: float = 5.0,
        quiet: bool = True,
    ) -> bool:
        return self.context().stop_with_guarantee(
            reason,
            max_wait_seconds,
            quiet,
        )


__all__ = [
    "RouterListenerPorts",
    "RouterProcessCompatibilityApi",
    "RouterProcessCompatibilityPorts",
    "RouterProcessContext",
    "RouterProcessEffects",
]
