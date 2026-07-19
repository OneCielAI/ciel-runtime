"""Router process shutdown and port-replacement application policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RouterProcessConfig:
    pid_path: Path
    router_port: int
    router_base: str
    config_dir: Path


@dataclass(frozen=True, slots=True)
class RouterStatePorts:
    health: Callable[[], dict[str, Any] | None]
    foreign_config: Callable[[dict[str, Any] | None], bool]
    current_config: Callable[[dict[str, Any] | None], bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class RouterTerminationPorts:
    terminate_pid: Callable[[int, str, bool], bool]
    terminate_pid_file: Callable[[Path, str, bool], bool]
    terminate_health: Callable[[dict[str, Any] | None, bool], bool]
    stop_processes: Callable[[bool], bool]
    listener_pids: Callable[[], list[int]]


@dataclass(frozen=True, slots=True)
class ClockPorts:
    now: Callable[[], float]
    sleep: Callable[[float], None]


def terminate_pid_file(
    path: Path,
    label: str,
    quiet: bool,
    *,
    terminate_pid: Callable[[int, str, bool], bool],
    pid_is_running: Callable[[int], bool],
) -> bool:
    if not path.exists():
        return False
    try:
        pid = int(path.read_text().strip())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return False
    stopped = terminate_pid(pid, label, quiet)
    if stopped or not pid_is_running(pid):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return stopped


def terminate_health_pid(
    health: dict[str, Any] | None,
    quiet: bool,
    *,
    config: RouterProcessConfig,
    state: RouterStatePorts,
    terminate_pid: Callable[[int, str, bool], bool],
    protected_pids: tuple[int, int],
) -> bool:
    if not isinstance(health, dict):
        return False
    if not state.current_config(health):
        state.log(
            "INFO",
            "router_kill_skipped_foreign_config "
            f"running_config={health.get('config_dir') or '-'} "
            f"current_config={config.config_dir}",
        )
        return False
    try:
        pid = int(health.get("pid") or 0)
    except Exception:
        return False
    if pid in protected_pids:
        return False
    return terminate_pid(pid, "ciel-runtime router", quiet)


def stop_router_processes(
    quiet: bool,
    *,
    config: RouterProcessConfig,
    state: RouterStatePorts,
    termination: RouterTerminationPorts,
) -> bool:
    stopped = termination.terminate_pid_file(config.pid_path, "ciel-runtime router", quiet)
    health = state.health()
    if state.foreign_config(health):
        state.log(
            "INFO",
            "router_stop_skipped_foreign_config "
            f"running_config={health.get('config_dir') or '-'} "
            f"current_config={config.config_dir}",
        )
        return stopped
    if state.current_config(health):
        stopped = termination.terminate_health(health, True) or stopped
    return stopped


def ensure_port_available(
    reason: str,
    health: dict[str, Any] | None,
    max_wait_seconds: float,
    *,
    config: RouterProcessConfig,
    state: RouterStatePorts,
    termination: RouterTerminationPorts,
    clock: ClockPorts,
) -> None:
    if state.foreign_config(health):
        raise RuntimeError(_foreign_port_message(config, health))
    stopped = termination.terminate_health(health, True)
    stopped = termination.stop_processes(True) or stopped
    deadline = clock.now() + max(0.1, max_wait_seconds)
    while clock.now() < deadline:
        current_health = state.health()
        if state.foreign_config(current_health):
            raise RuntimeError(_foreign_port_message(config, current_health))
        if current_health is None and not termination.listener_pids():
            state.log(
                "INFO",
                f"router_port_clear reason={reason} port={config.router_port} "
                f"stopped={str(stopped).lower()}",
            )
            return
        if current_health is not None:
            termination.terminate_health(current_health, True)
        termination.stop_processes(True)
        clock.sleep(0.1)
    pids = termination.listener_pids()
    current_health = state.health()
    description = ""
    if isinstance(current_health, dict):
        description = (
            f" version={current_health.get('version') or '-'}"
            f" source={current_health.get('source_fingerprint') or '-'}"
            f" pid={current_health.get('pid') or '-'}"
        )
    raise RuntimeError(
        f"stale ciel-runtime router is still serving on {config.router_base}; "
        f"port {config.router_port} listener_pids={pids or '-'}{description}; "
        "run `ciel-runtime stop` and launch again."
    )


def stop_with_guarantee(
    reason: str,
    max_wait_seconds: float,
    quiet: bool,
    *,
    config: RouterProcessConfig,
    state: RouterStatePorts,
    termination: RouterTerminationPorts,
    clock: ClockPorts,
) -> bool:
    initial_health = state.health()
    if initial_health is None:
        state.log("INFO", f"router_kill_guarantee reason={reason} state=already_down")
        return False
    if state.foreign_config(initial_health):
        state.log(
            "INFO",
            "router_kill_guarantee_skipped_foreign_config "
            f"reason={reason} running_config={initial_health.get('config_dir') or '-'} "
            f"current_config={config.config_dir}",
        )
        return False
    termination.stop_processes(quiet)
    deadline = clock.now() + max(0.1, max_wait_seconds)
    while clock.now() < deadline:
        health = state.health()
        if state.foreign_config(health):
            state.log(
                "INFO",
                "router_kill_guarantee_skipped_foreign_config "
                f"reason={reason} running_config={health.get('config_dir') or '-'} "
                f"current_config={config.config_dir}",
            )
            return False
        if health is None:
            elapsed_ms = int((max_wait_seconds - (deadline - clock.now())) * 1000)
            state.log(
                "INFO",
                f"router_kill_guarantee reason={reason} state=killed elapsed_ms={elapsed_ms}",
            )
            return True
        clock.sleep(0.1)
    state.log(
        "ERROR",
        f"router_kill_guarantee reason={reason} state=still_up_after_{max_wait_seconds:.1f}s",
    )
    raise RuntimeError(
        f"ciel-runtime router is still serving on {config.router_base} after a "
        f"{max_wait_seconds:.1f}s shutdown attempt for '{reason}'. Aborting to prevent "
        "the subsequent claude launch from accidentally routing through it. "
        f"Investigate the PID at {config.pid_path} or use `ciel-runtime stop` manually."
    )


def _foreign_port_message(
    config: RouterProcessConfig, health: dict[str, Any] | None
) -> str:
    foreign_config = health.get("config_dir") if isinstance(health, dict) else "-"
    return (
        f"ciel-runtime router port {config.router_port} is already used by another "
        f"ciel-runtime config ({foreign_config}). Set CIEL_RUNTIME_ROUTER_PORT or "
        "CIEL_RUNTIME_CONFIG_DIR for this instance instead of killing the other router."
    )
