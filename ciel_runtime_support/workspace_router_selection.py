"""Select an isolated router port for the current working directory."""

from __future__ import annotations

import json
import os
import socket
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
) -> int:
    """Reuse the same workspace port, otherwise select the first free port."""

    if str(environ.get("CIEL_RUNTIME_ROUTER_PORT") or "").strip():
        return base_port
    target = workspace_identity(
        environ.get("CIEL_RUNTIME_LAUNCH_CWD") or workspace
    )
    unknown_base_health: dict[str, Any] | None = None
    for offset in range(max(1, scan_size)):
        port = base_port + offset
        if port > 65535:
            break
        observed = health(port)
        if observed is not None:
            running_workspace = workspace_identity(observed.get("workspace"))
            if running_workspace and running_workspace == target:
                return port
            if offset == 0 and not running_workspace:
                unknown_base_health = observed
            continue
        if available(port):
            if offset == 0 and unknown_base_health is not None:
                continue
            return port
    raise RuntimeError(
        f"no free ciel-runtime router port found in {base_port}-{min(65535, base_port + scan_size - 1)}"
    )


__all__ = [
    "port_is_free",
    "probe_router_health",
    "select_workspace_router_port",
    "workspace_identity",
]
