from __future__ import annotations

import getpass
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def pid_is_running(pid: int) -> bool:
    """Check process liveness using the host platform's stable primitive."""

    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            process = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except Exception:
            return False
        output = process.stdout or ""
        return str(pid) in output and "No tasks" not in output and "INFO:" not in output
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def windows_pids_on_port(port: int) -> list[int]:
    if os.name != "nt":
        return []
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return []
    pids: set[int] = set()
    marker = f":{port}"
    for line in proc.stdout.splitlines():
        if marker not in line or "LISTENING" not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            pids.add(int(parts[-1]))
        except ValueError:
            continue
    return sorted(pids)


def linux_procfs_pids_on_port(port: int) -> list[int]:
    if os.name == "nt":
        return []
    wanted_port = f"{int(port):04X}"
    inodes: set[str] = set()
    for table in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = table.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        except Exception:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_addr = parts[1]
            state = parts[3]
            inode = parts[9]
            if state != "0A" or ":" not in local_addr:
                continue
            if local_addr.rsplit(":", 1)[1].upper() == wanted_port and inode and inode != "0":
                inodes.add(inode)
    if not inodes:
        return []
    pids: set[int] = set()
    current = os.getpid()
    parent = os.getppid()
    try:
        proc_entries = list(Path("/proc").iterdir())
    except Exception:
        return []
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
        except ValueError:
            continue
        if pid in (current, parent):
            continue
        fd_dir = entry / "fd"
        try:
            fds = list(fd_dir.iterdir())
        except Exception:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except Exception:
                continue
            match = re.match(r"socket:\[(\d+)\]$", target)
            if match and match.group(1) in inodes:
                pids.add(pid)
                break
    return sorted(pids)


def posix_pids_on_port(
    port: int,
    procfs_lookup: Callable[[int], list[int]] = linux_procfs_pids_on_port,
) -> list[int]:
    if os.name == "nt":
        return []
    pids: set[int] = set(procfs_lookup(port))

    def add_ints(text: str, *, skip_port: bool = False) -> None:
        for value in re.findall(r"\b\d+\b", text or ""):
            try:
                pid = int(value)
            except ValueError:
                continue
            if skip_port and pid == port:
                continue
            pids.add(pid)

    commands: list[tuple[list[str], str]] = [
        (["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], "plain"),
        (["fuser", "-n", "tcp", str(port)], "fuser"),
        (["ss", "-ltnp", f"sport = :{port}"], "ss"),
        (["netstat", "-ltnp"], "netstat"),
    ]
    for cmd, kind in commands:
        if not shutil.which(cmd[0]):
            continue
        try:
            proc = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except Exception:
            continue
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if kind == "plain":
            add_ints(text)
        elif kind == "fuser":
            add_ints(text.split(":", 1)[1] if ":" in text else text, skip_port=True)
        elif kind == "ss":
            for value in re.findall(r"pid=(\d+)", text):
                add_ints(value)
        else:
            for line in text.splitlines():
                if "LISTEN" not in line or f":{port}" not in line:
                    continue
                match = re.search(r"\s(\d+)/", line)
                if match:
                    add_ints(match.group(1))
    return sorted(pid for pid in pids if pid not in (os.getpid(), os.getppid()))


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


class ProcessTreeController:
    """Inspect and terminate one process tree through explicit query/signal ports."""

    def __init__(self, services: ProcessControlServices, *, platform_name: str = os.name) -> None:
        self._services = services
        self._platform_name = platform_name

    def terminate_pid(self, pid: int, label: str, *, quiet: bool = False) -> bool:
        if not self._services.signals.pid_is_running(pid):
            return False
        try:
            if self._platform_name == "nt":
                self._services.query.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                )
            else:
                self._services.signals.kill(pid, signal.SIGTERM)
                deadline = self._services.signals.now() + 4
                while deadline > self._services.signals.now() and self._services.signals.pid_is_running(pid):
                    self._services.signals.sleep(0.1)
                if self._services.signals.pid_is_running(pid):
                    self._services.signals.kill(pid, signal.SIGKILL)
            if not quiet:
                self._services.output(f"Stopped existing {label} session (pid {pid}).")
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            self._services.log(
                "WARN",
                f"process_tree_terminate_failed label={label!r} pid={pid} error={type(exc).__name__}: {exc}",
            )
            if not quiet:
                self._services.output(f"Could not stop existing {label} session ({type(exc).__name__}).")
            return False

    def descendant_pids(self, pid: int) -> list[int]:
        if pid <= 0 or self._platform_name == "nt":
            return []
        try:
            result = self._services.query.run(
                ["ps", "-eo", "pid=,ppid="],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._services.log("WARN", f"process_tree_query_failed pid={pid} error={type(exc).__name__}: {exc}")
            return []
        children: dict[int, list[int]] = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            try:
                child, parent = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            children.setdefault(parent, []).append(child)
        descendants: list[int] = []
        stack = list(children.get(pid, []))
        while stack:
            child = stack.pop()
            if child in descendants:
                continue
            descendants.append(child)
            stack.extend(children.get(child, []))
        return descendants

    def parent_pid_and_command(self, pid: int) -> tuple[int, str] | None:
        if pid <= 0 or self._platform_name == "nt":
            return None
        try:
            result = self._services.query.run(
                ["ps", "-p", str(pid), "-o", "ppid=,command="],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._services.log("WARN", f"process_parent_query_failed pid={pid} error={type(exc).__name__}: {exc}")
            return None
        parts = result.stdout.strip().split(maxsplit=1)
        if not parts:
            return None
        try:
            parent = int(parts[0])
        except ValueError:
            return None
        return parent, parts[1] if len(parts) > 1 else ""

    def client_wrapper_parent_pids(self, pid: int) -> list[int]:
        wrappers: list[int] = []
        current = pid
        protected = {self._services.query.current_pid(), self._services.query.parent_pid()}
        for _ in range(4):
            parent_info = self.parent_pid_and_command(current)
            if parent_info is None:
                break
            parent, command = parent_info
            if parent <= 0 or parent in protected:
                break
            if "ciel-runtime" not in command and "ciel_runtime.py" not in command:
                break
            if " serve" in command or " mcp-proxy" in command:
                break
            wrappers.append(parent)
            current = parent
        return wrappers

    def terminate_tree(self, pid: int, label: str, *, quiet: bool = False) -> bool:
        if pid <= 0:
            return False
        if self._platform_name == "nt":
            return self.terminate_pid(pid, label, quiet=quiet)
        protected = {self._services.query.current_pid(), self._services.query.parent_pid()}
        targets = [item for item in [pid, *self.descendant_pids(pid)] if item > 0 and item not in protected]
        if not targets:
            return False
        stopped = False
        for target in targets:
            try:
                if self._services.signals.pid_is_running(target):
                    self._services.signals.kill(target, signal.SIGTERM)
                    stopped = True
            except OSError as exc:
                self._services.log(
                    "WARN",
                    f"process_tree_signal_failed signal=TERM pid={target} error={type(exc).__name__}: {exc}",
                )
        deadline = self._services.signals.now() + 4
        while self._services.signals.now() < deadline:
            if not any(self._services.signals.pid_is_running(target) for target in targets):
                break
            self._services.signals.sleep(0.1)
        for target in targets:
            if not self._services.signals.pid_is_running(target):
                continue
            try:
                self._services.signals.kill(target, signal.SIGKILL)
                stopped = True
            except OSError as exc:
                self._services.log(
                    "WARN",
                    f"process_tree_signal_failed signal=KILL pid={target} error={type(exc).__name__}: {exc}",
                )
        if stopped and not quiet:
            self._services.output(f"Stopped existing {label} session(s): {', '.join(map(str, targets))}.")
        return stopped


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
