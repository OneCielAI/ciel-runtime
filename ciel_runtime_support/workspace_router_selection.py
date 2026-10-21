"""Select an isolated router port for the current working directory."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping


def workspace_identity(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(str(Path(text).resolve(strict=False)))
    except Exception:
        return os.path.normcase(text)


def workspace_digest(value: Any) -> str:
    import hashlib

    return hashlib.sha256(
        workspace_identity(value).encode("utf-8", errors="replace")
    ).hexdigest()[:12]


def probe_router_health(port: int, timeout: float = 0.15) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        return payload if isinstance(payload, dict) and payload.get("ok") is True else None
    except Exception:
        return None


def port_is_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


# Every workspace keeps its registry reservation between launches, so the
# range fills with reservations rather than live routers: on 2026-09-23 all 32
# ports (9464-9495) were reserved while only 6 were listening, and a new
# workspace could not start.
ROUTER_PORT_SCAN_SIZE = 50
ROUTER_LAUNCH_LEASE_SECONDS = 30.0


def _process_identity(pid: int) -> str | None:
    """Return a live creation token, empty for dead, or None for unknown.

    Do not use pid_is_running's false result here: inspection failures must
    preserve reservations, rather than look like a verified dead process.
    """

    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetProcessTimes.argtypes = (ctypes.c_void_p,) + (ctypes.POINTER(ctypes.c_ulonglong),) * 4
            kernel32.GetProcessTimes.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                return "" if error in (2, 3, 87) else None
            try:
                times = [ctypes.c_ulonglong() for _ in range(4)]
                if not kernel32.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                    return None
                return "" if times[1].value else str(times[0].value)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    try:
        # The command in parentheses may itself contain spaces or parentheses.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        return fields[19]
    except FileNotFoundError:
        if Path("/proc/self/stat").exists():
            return ""
    except (OSError, ValueError, IndexError):
        pass
    return None


def _launch_record(target: str, port: int, reservation: str = "auto") -> dict[str, Any]:
    pid = os.getpid()
    return {
        "workspace": target,
        "port": port,
        "reservation": reservation,
        "launch_lease": {
            "pid": pid,
            "identity": _process_identity(pid),
            "expires_at": time.time() + ROUTER_LAUNCH_LEASE_SECONDS,
        },
    }


def _inactive_reservation(record: dict[str, Any], registry_path: Path) -> bool:
    """Prove inactivity without modifying instance files or processes."""

    if record.get("reservation", "auto") != "auto":
        return False
    workspace = workspace_identity(record.get("workspace"))
    if not workspace:
        return False
    try:
        lease = record.get("launch_lease")
        if lease is not None:
            if not isinstance(lease, dict):
                return False
            # A live/reused/unknown PID remains protected even after expiry.
            if _process_identity(int(lease["pid"])) != "":
                return False
            expires = float(lease["expires_at"])
            if not 0 < expires <= time.time():
                return False
        # Keep all recorded live owners, including PIDs reused by other
        # processes. Legacy instance files only carry PIDs, not birth tokens.
        if "owner_pid" in record and _process_identity(int(record["owner_pid"])) != "":
            return False
        root = registry_path.parent / "router-instances"
        instance = root / f"{int(record['port'])}-{workspace_digest(workspace)}"
        if instance.exists() and not instance.is_dir():
            return False
        pid_path = instance / "router.pid"
        if pid_path.exists() and _process_identity(int(pid_path.read_text().strip())) != "":
            return False
        clients = instance / "router-clients"
        if clients.exists():
            for path in clients.iterdir():
                if path.suffix != ".json":
                    continue
                client = json.loads(path.read_text(encoding="utf-8"))
                if _process_identity(int(client["pid"])) != "":
                    return False
        # stat, unlike exists(), keeps permission/IO failures explicit.
        for path in (root, instance, pid_path, clients):
            try:
                path.stat()
            except FileNotFoundError:
                pass
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def select_workspace_router_port(
    base_port: int,
    workspace: Path,
    environ: Mapping[str, str],
    *,
    health: Callable[[int], dict[str, Any] | None] = probe_router_health,
    available: Callable[[int], bool] = port_is_free,
    scan_size: int = ROUTER_PORT_SCAN_SIZE,
    registry_path: Path | None = None,
) -> int:
    """Select one stable, isolated port for a workspace.

    A complete health scan happens before any free port is accepted.  This is
    important when a lower port becomes free while this workspace's router is
    still alive on a later port.  An optional registry also reserves the port
    between launches and closes the race where two workspaces start together.
    """

    explicit_port = bool(str(environ.get("CIEL_RUNTIME_ROUTER_PORT") or "").strip())
    target = workspace_identity(
        environ.get("CIEL_RUNTIME_LAUNCH_CWD") or workspace
    )
    if registry_path is None:
        if explicit_port:
            return base_port
        return _select_port(base_port, target, health, available, scan_size, {})

    registry_path = Path(registry_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
    deadline = time.monotonic() + 8.0
    lock_fd: int | None = None
    while lock_fd is None:
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, f"{os.getpid()}\n".encode("ascii", errors="replace"))
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > 30.0
                if stale:
                    lock_pid = int(lock_path.read_text(encoding="ascii").strip())
                    stale = _process_identity(lock_pid) == ""
            except (OSError, ValueError):
                stale = False
            if stale:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeError(f"timed out locking workspace router registry: {registry_path}")
            time.sleep(0.05)
    try:
        registry = _load_registry(registry_path)
        records = registry.setdefault("workspaces", {})
        key = workspace_digest(target)
        if explicit_port:
            _claim_explicit_port(
                base_port,
                target,
                key,
                records,
                registry_path,
                registry,
                health,
                available,
                environ,
            )
            return base_port
        # Scan live health before honoring even our own offline mapping.
        maximum = min(65535, base_port + max(1, scan_size) - 1)
        observed_ports = {port: health(port) for port in range(base_port, maximum + 1)}
        own_record = records.get(key)
        mapped_port = _record_port(own_record)
        if mapped_port and mapped_port not in observed_ports:
            observed_ports[mapped_port] = health(mapped_port)
        for port, observed in observed_ports.items():
            if isinstance(observed, dict) and workspace_identity(observed.get("workspace")) == target:
                if port != mapped_port:
                    records[key] = {"workspace": target, "port": port, "reservation": "auto"}
                    _save_registry(registry_path, registry)
                return port

        if mapped_port and observed_ports[mapped_port] is None and available(mapped_port):
            _check_own_launch_lease(own_record)
            reservation = own_record.get("reservation", "auto")
            records[key] = _launch_record(target, mapped_port, reservation)
            _save_registry(registry_path, registry)
            return mapped_port

        reserved = {
            _record_port(record)
            for record_key, record in records.items()
            if record_key != key
        }
        try:
            selected = _select_port(
                base_port, target, observed_ports.get, available, scan_size, reserved
            )
        except RuntimeError:
            selected = 0
            for port in range(base_port, maximum + 1):
                owners = [record for record in records.values() if _record_port(record) == port]
                if not owners or observed_ports[port] is not None or not available(port):
                    continue
                if (
                    all(_inactive_reservation(record, registry_path) for record in owners)
                    and health(port) is None
                    and available(port)
                ):
                    selected = port
                    break
            if not selected:
                raise
            for record_key in list(records):
                if _record_port(records[record_key]) == selected:
                    del records[record_key]
        records[key] = _launch_record(target, selected)
        _save_registry(registry_path, registry)
        return selected
    finally:
        try:
            if lock_fd is not None:
                os.close(lock_fd)
        finally:
            try:
                lock_path.unlink()
            except OSError:
                pass


def _record_port(record: Any) -> int:
    try:
        port = int(record.get("port") or 0) if isinstance(record, dict) else 0
        return port if 1 <= port <= 65535 else 0
    except (TypeError, ValueError):
        return 0


def _check_own_launch_lease(record: dict[str, Any]) -> None:
    lease = record.get("launch_lease")
    if lease is None:
        return
    try:
        pid = int(lease["pid"])
        current = _process_identity(pid)
        if pid == os.getpid() and current and current == lease.get("identity"):
            return
        float(lease["expires_at"])  # Validate the stored lease shape.
        # A verified-dead owner cannot still be launching, even if its short
        # lease had not elapsed when the process disappeared.
        if current == "":
            return
    except (TypeError, ValueError, KeyError):
        pass
    raise RuntimeError("workspace router launch already reserved by another or unknown process")


def _select_port(
    base_port: int,
    target: str,
    health: Callable[[int], dict[str, Any] | None],
    available: Callable[[int], bool],
    scan_size: int,
    reserved: set[int],
) -> int:
    free_ports: list[int] = []
    unknown_base_health = False
    maximum = min(65535, base_port + max(1, scan_size) - 1)
    for port in range(base_port, maximum + 1):
        observed = health(port)
        if observed is not None:
            running_workspace = workspace_identity(observed.get("workspace"))
            if running_workspace and running_workspace == target:
                return port
            if port == base_port and not running_workspace:
                unknown_base_health = True
            continue
        if port not in reserved and available(port):
            free_ports.append(port)
    for port in free_ports:
        if port != base_port or not unknown_base_health:
            return port
    raise RuntimeError(
        f"no free ciel-runtime router port found in {base_port}-{maximum}"
    )


def _claim_explicit_port(
    port: int,
    target: str,
    key: str,
    records: dict[str, Any],
    registry_path: Path,
    registry: dict[str, Any],
    health: Callable[[int], dict[str, Any] | None],
    available: Callable[[int], bool],
    environ: Mapping[str, str],
) -> None:
    for record_key, record in records.items():
        if record_key == key or not isinstance(record, dict):
            continue
        try:
            reserved_port = int(record.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if reserved_port == port:
            owner = workspace_identity(record.get("workspace")) or record_key
            raise RuntimeError(
                f"ciel-runtime port {port} is reserved by workspace {owner}; "
                f"choose a different CIEL_RUNTIME_ROUTER_PORT for {target}"
            )
    observed = health(port)
    if observed is not None:
        running_workspace = workspace_identity(observed.get("workspace"))
        if running_workspace != target:
            owner = running_workspace or "an unidentified local service"
            raise RuntimeError(
                f"ciel-runtime port {port} is already used by {owner}; "
                f"choose a different CIEL_RUNTIME_ROUTER_PORT for {target}"
            )
    elif not available(port):
        raise RuntimeError(
            f"ciel-runtime port {port} is already used by another local service; "
            f"choose a different CIEL_RUNTIME_ROUTER_PORT for {target}"
        )
    own_record = records.get(key)
    # The router child inherits an explicit endpoint from its launcher. It
    # must neither turn that automatic claim into an explicit one nor steal
    # the launcher's lease while it imports runtime_paths.
    inherited = False
    if isinstance(own_record, dict) and _record_port(own_record) == port:
        lease = own_record.get("launch_lease")
        if isinstance(lease, dict):
            try:
                owner_pid = int(environ.get("CIEL_RUNTIME_ROUTER_OWNER_PID") or 0)
                inherited = (
                    environ.get("CIEL_RUNTIME_MANAGED_ROUTER") == "1"
                    and owner_pid == os.getppid() == int(lease["pid"])
                    and bool(lease.get("identity"))
                    and _process_identity(owner_pid) == lease["identity"]
                )
            except (TypeError, ValueError, KeyError):
                pass
        if observed is None and not inherited:
            _check_own_launch_lease(own_record)
    if not inherited:
        records[key] = _launch_record(target, port, "explicit")
        _save_registry(registry_path, registry)


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": 1, "workspaces": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("workspaces"), dict):
        return {"version": 1, "workspaces": {}}
    return payload


def _save_registry(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


__all__ = [
    "port_is_free",
    "probe_router_health",
    "select_workspace_router_port",
    "workspace_digest",
    "workspace_identity",
]
