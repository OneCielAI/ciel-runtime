from __future__ import annotations

import getpass
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ProcessQueryServices:
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    username: Callable[[], str] = getpass.getuser
    current_pid: Callable[[], int] = os.getpid
    parent_pid: Callable[[], int] = os.getppid


@dataclass(frozen=True)
class ProcessSignalServices:
    kill: Callable[[int, int], None]
    pid_is_running: Callable[[int], bool]
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class ProcessControlServices:
    query: ProcessQueryServices
    signals: ProcessSignalServices
    log: Callable[[str, str], None]
    output: Callable[[str], None] = print


@dataclass(frozen=True)
class ProcessInspectionServices:
    run: Callable[..., subprocess.CompletedProcess[str]]
    read_bytes: Callable[[Path], bytes]
    readlink: Callable[[Path], str]
    username: Callable[[], str]
    log: Callable[[str, str], None]


def process_command_line(
    pid: int,
    services: ProcessInspectionServices,
    *,
    platform_name: str = os.name,
) -> str:
    if pid <= 0:
        return ""
    if platform_name == "nt":
        script = (
            "Get-CimInstance Win32_Process -Filter "
            f'"ProcessId = {int(pid)}" | Select-Object -ExpandProperty CommandLine'
        )
        command = ["powershell", "-NoProfile", "-Command", script]
    else:
        command = ["ps", "-p", str(pid), "-o", "command="]
    try:
        result = services.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        services.log(
            "WARN",
            f"process_command_line_query_failed pid={pid} platform={platform_name} "
            f"error={type(exc).__name__}: {exc}",
        )
        return ""
    return result.stdout.strip()


def process_environ_contains(
    pid: int,
    key: str,
    value: str | None,
    services: ProcessInspectionServices,
    *,
    platform_name: str = os.name,
) -> bool:
    if platform_name == "nt" or pid <= 0:
        return False
    path = Path("/proc") / str(pid) / "environ"
    try:
        data = services.read_bytes(path)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    except OSError as exc:
        services.log(
            "WARN",
            f"process_environ_query_failed pid={pid} error={type(exc).__name__}: {exc}",
        )
        return False
    needle = f"{key}=".encode()
    for item in data.split(b"\0"):
        if not item.startswith(needle):
            continue
        if value is None:
            return True
        return item[len(needle) :].decode("utf-8", errors="replace") == value
    return False


def process_cwd(
    pid: int,
    services: ProcessInspectionServices,
    *,
    platform_name: str = os.name,
) -> Path | None:
    if platform_name == "nt" or pid <= 0:
        return None
    path = Path("/proc") / str(pid) / "cwd"
    try:
        return Path(services.readlink(path)).resolve()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    except OSError as exc:
        services.log(
            "WARN",
            f"process_cwd_query_failed pid={pid} error={type(exc).__name__}: {exc}",
        )
        return None


def posix_process_rows(services: ProcessInspectionServices) -> list[tuple[int, str, str]]:
    try:
        result = services.run(
            ["ps", "-u", services.username(), "-o", "pid=,stat=,command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        services.log(
            "WARN",
            f"process_list_query_failed platform=posix error={type(exc).__name__}: {exc}",
        )
        return []
    rows: list[tuple[int, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        rows.append((pid, parts[1], parts[2]))
    return rows


def terminate_matching_processes(
    needles: list[str],
    label: str,
    services: ProcessControlServices,
    *,
    quiet: bool = False,
    platform_name: str = os.name,
) -> bool:
    if platform_name == "nt":
        matched = _windows_matching_processes(needles, services)
        if matched is None:
            return False
        stopped = _terminate_windows_processes(matched, label, services)
    else:
        matched = _posix_matching_processes(needles, services)
        if matched is None:
            return False
        stopped = _terminate_posix_processes(matched, label, services)
    if stopped and not quiet:
        services.output(f"Stopped existing {label} session(s): {', '.join(map(str, matched))}.")
    return stopped


def _windows_matching_processes(
    needles: list[str], services: ProcessControlServices
) -> list[int] | None:
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        result = services.query.run(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        rows = json.loads(result.stdout or "[]")
    except Exception as exc:
        services.log("WARN", f"process_query_failed platform=windows error={type(exc).__name__}: {exc}")
        return None
    if isinstance(rows, dict):
        rows = [rows]
    matched: list[int] = []
    for row in rows if isinstance(rows, list) else []:
        try:
            pid = int(row.get("ProcessId"))
        except (AttributeError, TypeError, ValueError):
            continue
        command = str(row.get("CommandLine") or "")
        if pid in {services.query.current_pid(), services.query.parent_pid()} or not command:
            continue
        if all(needle in command for needle in needles):
            matched.append(pid)
    return matched


def _terminate_windows_processes(
    matched: list[int], label: str, services: ProcessControlServices
) -> bool:
    stopped = False
    for pid in matched:
        try:
            services.query.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            stopped = True
        except Exception as exc:
            services.log(
                "WARN",
                f"process_terminate_failed platform=windows label={label!r} pid={pid} "
                f"error={type(exc).__name__}: {exc}",
            )
    return stopped


def _posix_matching_processes(
    needles: list[str], services: ProcessControlServices
) -> list[int] | None:
    try:
        result = services.query.run(
            ["ps", "-u", services.query.username(), "-o", "pid=,stat=,command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception as exc:
        services.log("WARN", f"process_query_failed platform=posix error={type(exc).__name__}: {exc}")
        return None
    matched: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        stat, command = parts[1], parts[2]
        if pid in {services.query.current_pid(), services.query.parent_pid()} or stat.startswith("Z"):
            continue
        if all(needle in command for needle in needles):
            matched.append(pid)
    return matched


def _terminate_posix_processes(
    matched: list[int], label: str, services: ProcessControlServices
) -> bool:
    stopped = False
    for pid in matched:
        try:
            services.signals.kill(pid, signal.SIGTERM)
            stopped = True
        except Exception as exc:
            services.log(
                "WARN",
                f"process_terminate_failed platform=posix signal=TERM label={label!r} pid={pid} "
                f"error={type(exc).__name__}: {exc}",
            )
    deadline = services.signals.now() + 3
    while services.signals.now() < deadline:
        if not any(services.signals.pid_is_running(pid) for pid in matched):
            break
        services.signals.sleep(0.1)
    for pid in matched:
        if not services.signals.pid_is_running(pid):
            continue
        try:
            services.signals.kill(pid, signal.SIGKILL)
        except Exception as exc:
            services.log(
                "WARN",
                f"process_terminate_failed platform=posix signal=KILL label={label!r} pid={pid} "
                f"error={type(exc).__name__}: {exc}",
            )
    return stopped
