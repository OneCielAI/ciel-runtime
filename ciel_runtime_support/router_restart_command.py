"""Explicit, local, single-process router recovery without a working HTTP server."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import time
from typing import Any, Callable

from .channel_message_repository import exclusive_file_lock
from .workspace_router_selection import _process_identity


@dataclass(frozen=True)
class RestartPorts:
    listeners: Callable[[], list[int]]
    identity: Callable[[int], str | None]
    owned: Callable[[int], bool]
    terminate: Callable[[int, str], bool]
    health: Callable[[], dict[str, Any] | None]
    matches: Callable[[dict[str, Any] | None], bool]
    log: Callable[[str, str], None]


def terminate_generation(pid: int, generation: str) -> bool:
    """Signal a pinned process handle, never a process tree or recycled PID."""
    if os.name == 'nt':
        import ctypes as c
        api = c.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = (c.c_uint32, c.c_int, c.c_uint32)
        api.OpenProcess.restype = c.c_void_p
        api.CloseHandle.argtypes = (c.c_void_p,)
        api.TerminateProcess.argtypes = (c.c_void_p, c.c_uint32)
        api.GetProcessTimes.argtypes = (c.c_void_p,) + (c.POINTER(c.c_ulonglong),) * 4
        handle = api.OpenProcess(0x1001, False, pid)
        if not handle:
            return False
        try:
            times = [c.c_ulonglong() for _ in range(4)]
            return bool(api.GetProcessTimes(handle, *(c.byref(t) for t in times))
                        and not times[1].value and str(times[0].value) == generation
                        and api.TerminateProcess(handle, 1))
        finally:
            api.CloseHandle(handle)
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        return False
    try:
        fd = os.pidfd_open(pid)
        try:
            if _process_identity(pid) != generation:
                return False
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            return True
        finally:
            os.close(fd)
    except OSError:
        return False


def router_parent(pid: int) -> int:
    if os.name == 'nt':
        import subprocess
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}").ParentProcessId'],
            capture_output=True, text=True, timeout=5, check=True)
        return int(result.stdout.strip())
    fields = Path(f'/proc/{pid}/stat').read_text(encoding='ascii').rsplit(')', 1)[1].split()
    return int(fields[1])


def owned_by_registered_launcher(pid: int, clients_dir: Path, port: int, workspace: str,
                                 command_line: Callable[[int], str]) -> bool:
    import json
    import re
    import subprocess
    from .workspace_router_selection import workspace_identity
    try:
        parent = router_parent(pid)
        row = json.loads((clients_dir / f'{parent}.json').read_bytes())
        command = command_line(pid)
        return (type(row.get('pid')) is int and row['pid'] == parent and row.get('router_port') == port
                and workspace_identity(row.get('workspace')) == workspace_identity(workspace)
                and bool(_process_identity(parent))
                and bool(re.search(r'[/\\\\]ciel_runtime\.py["\']?\s+serve\s*$', command, re.I)))
    except (OSError, ValueError, TypeError, IndexError, AttributeError, subprocess.SubprocessError):
        return False


def restart_owned_router(pid_path: Path, reason: str, ports: RestartPorts, *, wait: float = 35) -> dict:
    if not reason.strip() or len(reason) > 200 or not reason.isprintable():
        raise RuntimeError('a single-line reason (1–200 characters) is required')
    stamp = pid_path.with_name('router-restart.cooldown')
    with exclusive_file_lock(stamp):
        try:
            if time.time() - float(stamp.read_text(encoding='ascii')) < 120:
                raise RuntimeError('router restart cooldown (120 seconds)')
        except FileNotFoundError:
            pass
        except ValueError as exc:
            raise RuntimeError('invalid router restart cooldown record') from exc
        def read_pid():
            try:
                return int(pid_path.read_text(encoding='ascii').strip())
            except (OSError, ValueError):
                return 0
        pid = read_pid()
        generation = ports.identity(pid) if pid > 1 else None
        if not generation or ports.listeners() != [pid] or not ports.owned(pid):
            raise RuntimeError('managed router ownership could not be verified')
        health = ports.health()
        if health is not None and (not ports.matches(health) or health.get('pid') != pid):
            raise RuntimeError('router health disagrees with local ownership')
        if (read_pid() != pid or sorted(set(ports.listeners())) != [pid]
                or not ports.owned(pid) or ports.identity(pid) != generation):
            raise RuntimeError('router identity changed before restart')
        stamp.write_text(str(time.time()), encoding='ascii')
        ports.log('WARN', f'router_recovery_requested pid={pid} http_ready={health is not None} reason={reason!r}')
        if not ports.terminate(pid, generation):
            ports.log('ERROR', f'router_recovery_signal_refused pid={pid}')
            raise RuntimeError('could not safely signal the managed router')
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            health = ports.health()
            new_pid = (health or {}).get('pid')
            if type(new_pid) is int and new_pid > 1 and new_pid != pid and ports.matches(health):
                ports.log('INFO', f'router_recovery_ready previous_pid={pid} pid={new_pid}')
                return {'previous_pid': pid, 'pid': new_pid, 'version': health.get('version')}
            time.sleep(0.3)
        ports.log('ERROR', f'router_recovery_health_timeout previous_pid={pid}')
        raise RuntimeError('router was signalled; replacement health was not confirmed')
