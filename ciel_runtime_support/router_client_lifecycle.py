"""Managed router client leases, supervision, and launch diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable


Log = Callable[[str, str], None]


@dataclass(frozen=True)
class RouterClientRegistryPorts:
    pid_is_running: Callable[[int], bool]
    log: Log


class RouterClientRegistry:
    def __init__(self, clients_dir: Path, router_port: int, ports: RouterClientRegistryPorts) -> None:
        self._clients_dir = clients_dir
        self._router_port = router_port
        self._ports = ports

    def register(self, pid: int | None = None) -> Path:
        client_pid = int(pid or os.getpid())
        self._clients_dir.mkdir(parents=True, exist_ok=True)
        path = self._clients_dir / f"{client_pid}.json"
        payload = {
            "pid": client_pid,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "router_port": self._router_port,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._ports.log("INFO", f"router_client_registered pid={client_pid} path={path}")
        return path

    def release(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
            self._ports.log("INFO", f"router_client_released path={path}")
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._ports.log("WARN", f"router_client_release_failed path={path} error={type(exc).__name__}: {exc}")

    def active_pids(self) -> list[int]:
        if not self._clients_dir.exists():
            return []
        active: list[int] = []
        for path in self._clients_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = int(data.get("pid") or path.stem)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try:
                    pid = int(path.stem)
                except ValueError:
                    pid = 0
            if self._ports.pid_is_running(pid):
                active.append(pid)
                continue
            try:
                path.unlink()
                self._ports.log("INFO", f"router_client_stale_removed pid={pid or '-'} path={path}")
            except OSError:
                pass
        return sorted(set(active))


@dataclass(frozen=True)
class ManagedRouterLifetimePorts:
    active_client_pids: Callable[[], list[int]]
    pid_is_running: Callable[[int], bool]
    stop_router: Callable[..., bool]
    log: Log


class ManagedRouterLifetime:
    def __init__(self, ports: ManagedRouterLifetimePorts) -> None:
        self._ports = ports

    @staticmethod
    def idle_exit_seconds() -> float:
        try:
            value = float(os.environ.get("CIEL_RUNTIME_ROUTER_IDLE_EXIT_SECONDS", "90"))
        except (TypeError, ValueError):
            value = 90.0
        return max(0.0, value)

    def stop_reason(self, started_at: float, owner_pid: int, idle_seconds: float) -> str | None:
        if os.environ.get("CIEL_RUNTIME_MANAGED_ROUTER") != "1":
            return None
        if self._ports.active_client_pids():
            return None
        if owner_pid > 0 and not self._ports.pid_is_running(owner_pid):
            return "owner_dead_no_clients"
        if idle_seconds > 0 and time.time() - started_at >= idle_seconds:
            return "idle_no_clients"
        return None

    def start_watchdog(self, server: Any) -> None:
        if os.environ.get("CIEL_RUNTIME_MANAGED_ROUTER") != "1":
            return
        try:
            owner_pid = int(os.environ.get("CIEL_RUNTIME_ROUTER_OWNER_PID") or "0")
        except ValueError:
            owner_pid = 0
        idle_seconds = self.idle_exit_seconds()
        started_at = time.time()

        def watch() -> None:
            interval = min(5.0, max(0.5, idle_seconds / 3.0 if idle_seconds else 5.0))
            while True:
                time.sleep(interval)
                reason = self.stop_reason(started_at, owner_pid, idle_seconds)
                if not reason:
                    continue
                self._ports.log("INFO", f"router_managed_lifetime_shutdown reason={reason} owner_pid={owner_pid or '-'}")
                try:
                    server.shutdown()
                except Exception as exc:
                    self._ports.log("ERROR", f"router_managed_lifetime_shutdown_failed error={type(exc).__name__}: {exc}")
                return

        threading.Thread(target=watch, daemon=True, name="ca-router-lifetime-watchdog").start()

    def stop_if_idle(self, reason: str, quiet: bool = True) -> bool:
        active = self._ports.active_client_pids()
        if active:
            self._ports.log("INFO", f"router_lifetime_keep_alive reason={reason} active_clients={','.join(map(str, active))}")
            return False
        try:
            stopped = self._ports.stop_router(reason, quiet=quiet)
            self._ports.log("INFO", f"router_lifetime_stopped reason={reason} stopped={stopped}")
            return stopped
        except Exception as exc:
            self._ports.log("ERROR", f"router_lifetime_stop_failed reason={reason} error={type(exc).__name__}: {exc}")
            return False


@dataclass(frozen=True)
class RouterClientSupervisorPorts:
    router_health: Callable[[], Any]
    health_matches_current: Callable[[Any], bool]
    health_summary: Callable[[Any], str]
    start_router: Callable[..., bool]
    log: Log


class RouterClientSupervisor:
    def __init__(self, router_base: str, ports: RouterClientSupervisorPorts) -> None:
        self._router_base = router_base
        self._ports = ports

    @staticmethod
    def interval_seconds() -> float:
        try:
            value = float(os.environ.get("CIEL_RUNTIME_ROUTER_SUPERVISOR_SECONDS", "0.5"))
        except (TypeError, ValueError):
            value = 0.5
        return max(0.5, min(30.0, value))

    def ensure_running(self) -> bool:
        health = self._ports.router_health()
        if self._ports.health_matches_current(health):
            return True
        if health is not None:
            self._ports.log("WARN", f"router_lifetime_health_mismatch_active_client {self._ports.health_summary(health)}")
            return self._restart()
        for attempt in range(2):
            time.sleep(0.5)
            health = self._ports.router_health()
            if self._ports.health_matches_current(health):
                self._ports.log(
                    "INFO",
                    f"router_lifetime_keep_alive reason=transient_health_miss retry={attempt + 1} {self._ports.health_summary(health)}",
                )
                return True
        self._ports.log("WARN", f"router_lifetime_restart reason=router_down_active_client base={self._router_base}")
        return self._restart()

    def _restart(self) -> bool:
        try:
            return bool(self._ports.start_router(replace_active_clients=False))
        except Exception as exc:
            self._ports.log("ERROR", f"router_lifetime_restart_failed error={type(exc).__name__}: {exc}")
            return False

    def start(self, stop_event: threading.Event) -> threading.Thread:
        def watch() -> None:
            interval = self.interval_seconds()
            while not stop_event.wait(interval):
                self.ensure_running()

        thread = threading.Thread(target=watch, daemon=True, name="ca-router-client-supervisor")
        thread.start()
        return thread


@dataclass(frozen=True)
class RouterLifetimeRunnerPorts:
    register_client: Callable[[], Path]
    release_client: Callable[[Path | None], None]
    start_supervisor: Callable[[threading.Event], threading.Thread]
    stop_if_idle: Callable[..., bool]
    log: Log


class RouterLifetimeRunner:
    def __init__(self, ports: RouterLifetimeRunnerPorts) -> None:
        self._ports = ports

    def run(self, runner: Callable[[], int], manage_router: bool) -> int:
        client_path: Path | None = None
        supervisor_stop: threading.Event | None = None
        if manage_router:
            try:
                client_path = self._ports.register_client()
                supervisor_stop = threading.Event()
                self._ports.start_supervisor(supervisor_stop)
            except Exception as exc:
                self._ports.log("WARN", f"router_client_register_failed error={type(exc).__name__}: {exc}")
        try:
            return runner()
        finally:
            if supervisor_stop is not None:
                supervisor_stop.set()
            if manage_router:
                self._ports.release_client(client_path)
                self._ports.stop_if_idle("claude_exit", quiet=True)


@dataclass(frozen=True)
class RoutedLaunchDiagnosticPorts:
    router_health: Callable[[], Any]
    health_summary: Callable[[Any], str]
    provider_summary: Callable[[str, dict[str, Any]], str]
    log: Log


class RoutedLaunchDiagnostics:
    MARKERS = (
        "connectionrefused",
        "connection refused",
        "urlerror",
        "router_lifetime_restart_failed",
        "router_lifetime_health_mismatch",
        "anthropic_sse_forward_error",
    )
    LOG_MARKERS = (
        "[ERROR]", "[WARN]", "ConnectionRefused", "connection refused", "URLError",
        "router_lifetime", "router_spawned", "router_check_state", "claude_exit",
        "upstream_", "ollama_", "anthropic_sse_forward_error",
    )

    def __init__(self, router_base: str, log_path: Path, ports: RoutedLaunchDiagnosticPorts) -> None:
        self._router_base = router_base
        self._log_path = log_path
        self._ports = ports

    @staticmethod
    def file_size(path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    @staticmethod
    def read_from_offset(path: Path, offset: int = 0, max_bytes: int = 262_144) -> str:
        try:
            size = path.stat().st_size
            start = max(0, min(int(offset or 0), int(size)))
            if size - start > max_bytes:
                start = max(0, size - max_bytes)
            with path.open("rb") as stream:
                stream.seek(start)
                return stream.read(max_bytes).decode("utf-8", errors="replace")
        except OSError:
            return ""

    def recent_lines(self, since_offset: int = 0, limit: int = 8) -> list[str]:
        text = self.read_from_offset(self._log_path, since_offset)
        lines = [
            line.strip() for line in text.splitlines()
            if line.strip() and any(marker in line for marker in self.LOG_MARKERS)
        ]
        return lines[-max(1, int(limit or 8)):]

    @classmethod
    def should_print(cls, return_code: int, recent_lines: list[str]) -> bool:
        return return_code != 0 or any(marker in "\n".join(recent_lines).lower() for marker in cls.MARKERS)

    def print_exit(self, return_code: int, provider: str, config: dict[str, Any], *, log_offset: int = 0) -> None:
        recent = self.recent_lines(log_offset)
        if not self.should_print(return_code, recent):
            return
        lines = [
            f"Ciel Runtime diagnostic: Claude Code exited with code {return_code} while routed through {self._router_base}.",
            f"Router: {self._ports.health_summary(self._ports.router_health())}",
            f"Provider: {provider} {self._ports.provider_summary(provider, config)}",
            f"Router log: {self._log_path}",
        ]
        if recent:
            lines.append("Recent router events:")
            lines.extend(f"  {line}" for line in recent)
        for line in lines:
            print(line, flush=True)
        self._ports.log("WARN", "claude_routed_exit_diagnostic " + " | ".join(lines[:4]))


__all__ = [
    "ManagedRouterLifetime", "ManagedRouterLifetimePorts", "RoutedLaunchDiagnosticPorts",
    "RoutedLaunchDiagnostics", "RouterClientRegistry", "RouterClientRegistryPorts",
    "RouterClientSupervisor", "RouterClientSupervisorPorts", "RouterLifetimeRunner",
    "RouterLifetimeRunnerPorts",
]
