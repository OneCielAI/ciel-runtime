"""Router process shutdown and port-replacement application policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
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


@dataclass(frozen=True, slots=True)
class RouterStartupIdentity:
    version: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class RouterStartupStatePorts:
    health: Callable[[], dict[str, Any] | None]
    active_client_pids: Callable[[], list[int]]
    health_matches_current: Callable[[dict[str, Any] | None], bool]
    health_config_matches_current: Callable[[dict[str, Any] | None], bool]
    terminate_active_clients: Callable[..., bool]
    ensure_port_available: Callable[..., None]
    reuse_enabled: Callable[[], bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class RouterSpawnPorts:
    popen: Callable[..., Any]
    router_up: Callable[[], bool]
    now: Callable[[], float]
    sleep: Callable[[float], None]
    process_id: Callable[[], int]
    environment: Callable[[], dict[str, str]]


def start_router_if_needed(
    *,
    replace_active_clients: bool,
    config: RouterProcessConfig,
    identity: RouterStartupIdentity,
    state: RouterStartupStatePorts,
    spawn: RouterSpawnPorts,
    executable: str,
    entrypoint: Path,
    log_path: Path,
    platform_name: str,
) -> bool:
    health = state.health()
    if health is not None:
        active_clients = state.active_client_pids()
        if state.health_matches_current(health):
            if active_clients:
                if replace_active_clients:
                    state.log(
                        "WARN",
                        "router_prelaunch_replace_active_clients "
                        f"base={config.router_base} active_clients={','.join(map(str, active_clients))}",
                    )
                    state.terminate_active_clients("prelaunch_active_clients", active_clients, quiet=True)
                    state.ensure_port_available("prelaunch_active_clients", health)
                else:
                    state.log(
                        "INFO",
                        "router_check_state running=True spawn=False "
                        f"base={config.router_base} active_clients={','.join(map(str, active_clients))}",
                    )
                    return True
            elif state.reuse_enabled():
                state.log("INFO", f"router_check_state running=True spawn=False base={config.router_base} reuse=env")
                return True
            else:
                state.log(
                    "INFO",
                    "router_prelaunch_replace "
                    f"running_version={health.get('version') or '-'} current_version={identity.version} "
                    f"running_source={health.get('source_fingerprint') or '-'} current_source={identity.source_fingerprint} "
                    f"pid={health.get('pid') or '-'}",
                )
                state.ensure_port_available("prelaunch_replace", health)
        elif state.health_config_matches_current(health) and active_clients:
            if replace_active_clients:
                state.log(
                    "WARN",
                    "router_version_mismatch_replace_active_clients "
                    f"running_version={health.get('version') or '-'} current_version={identity.version} "
                    f"active_clients={','.join(map(str, active_clients))}",
                )
                state.terminate_active_clients("version_mismatch_active_clients", active_clients, quiet=True)
                state.ensure_port_available("version_mismatch_active_clients", health)
            else:
                raise RuntimeError(
                    f"ciel-runtime router on {config.router_base} belongs to this config but has active clients "
                    f"({','.join(map(str, active_clients))}) and differs from this launch "
                    f"(running_version={health.get('version') or '-'}, current_version={identity.version}). "
                    "Stop the other Claude Code session or launch this instance with a different "
                    "CIEL_RUNTIME_ROUTER_PORT."
                )
        else:
            state.log(
                "WARN",
                "router_version_mismatch_restart "
                f"running_version={health.get('version') or '-'} current_version={identity.version} "
                f"running_source={health.get('source_fingerprint') or '-'} current_source={identity.source_fingerprint}",
            )
            state.ensure_port_available("version_mismatch", health)
    else:
        state.ensure_port_available("pre_spawn", None)
    config.config_dir.mkdir(parents=True, exist_ok=True)
    command = [executable, str(entrypoint), "serve"]
    kwargs: dict[str, Any] = {}
    if platform_name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    state.log("INFO", f"router_check_state running=False spawn=True base={config.router_base}")
    environment = spawn.environment()
    environment["CIEL_RUNTIME_MANAGED_ROUTER"] = "1"
    environment["CIEL_RUNTIME_ROUTER_OWNER_PID"] = str(spawn.process_id())
    with log_path.open("ab", buffering=0) as log:
        spawn.popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=environment,
            **kwargs,
        )
    started_at = spawn.now()
    deadline = started_at + 30
    while spawn.now() < deadline:
        if spawn.router_up():
            state.log("INFO", f"router_spawned running=True base={config.router_base} elapsed={spawn.now()-started_at:.1f}s")
            return True
        spawn.sleep(0.5)
    raise RuntimeError(f"ciel-runtime router did not start. See {log_path}")


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
