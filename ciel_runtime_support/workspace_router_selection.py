"""Select an isolated router port for the current working directory."""

from __future__ import annotations

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
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def select_workspace_router_port(
    base_port: int,
    workspace: Path,
    environ: Mapping[str, str],
    *,
    health: Callable[[int], dict[str, Any] | None] = probe_router_health,
    available: Callable[[int], bool] = port_is_free,
    scan_size: int = 32,
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
            except OSError:
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
            )
            return base_port
        own_record = records.get(key) if isinstance(records, dict) else None
        if isinstance(own_record, dict):
            try:
                mapped_port = int(own_record.get("port") or 0)
            except (TypeError, ValueError):
                mapped_port = 0
            if 1 <= mapped_port <= 65535:
                observed = health(mapped_port)
                running_workspace = workspace_identity(
                    observed.get("workspace") if isinstance(observed, dict) else ""
                )
                if running_workspace == target or (observed is None and available(mapped_port)):
                    return mapped_port

        reserved = {
            int(record.get("port") or 0)
            for record_key, record in records.items()
            if record_key != key and isinstance(record, dict)
            and str(record.get("port") or "").isdigit()
        }
        selected = _select_port(base_port, target, health, available, scan_size, reserved)
        records[key] = {"workspace": target, "port": selected}
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
    records[key] = {"workspace": target, "port": port}
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
