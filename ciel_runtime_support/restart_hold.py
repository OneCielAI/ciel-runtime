"""Inputs that arrive while a CLI restart is pending wait for the relaunched session.

User requirement 2026-11-17: SSE messages that come in during a restart are not
consumed; they are kept and picked up after the restart. User decision: the
hold starts the moment the restart request is received (also while the restart
waits for the running turn).

Two parts:

- **Gate (in memory).** While the launcher's restart control holds a claimed
  request (``request`` or ``deferred``), the pending-delivery poll of every CLI
  transport delivers nothing: no terminal typing, no session-socket admission
  (side intake included), no router hand-off. Rows stay queued behind the
  cursor. Inputs already submitted keep blocking the restart until confirmed,
  as before.
- **Marker (on disk).** ``restart-hold-<launcher pid>.json`` in the router
  instance folder records when the hold began. At the relaunch, the launch
  fast-forward (rows older than the replay window are skipped) never passes
  a row queued after that moment; then the marker is removed. A marker older
  than ``MAX_AGE_SECONDS`` is ignored and removed, so a crashed launcher
  cannot shape a later launch.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PREFIX = "restart-hold-"
MAX_AGE_SECONDS = 3600.0


def holds(restart_state: Any) -> bool:
    """Whether a claimed restart is pending for this transport's CLI child."""
    return restart_state is not None and (getattr(restart_state, "request", None) is not None
                                          or getattr(restart_state, "deferred", None) is not None)


def _path(state_dir: Path, pid: int) -> Path:
    return Path(state_dir) / f"{PREFIX}{int(pid)}.json"


def begin(state_dir: Path, pid: int, request_id: str, *, now: float | None = None) -> dict[str, Any]:
    """Record the hold for one claimed request; the first claim's time is kept."""
    target = _path(state_dir, pid)
    current = _read(target)
    if current is not None and current.get("request_id") == request_id:
        return current
    row = {"request_id": str(request_id), "pid": int(pid), "started_at": time.time() if now is None else now}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(row) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return row


def _read(target: Path) -> dict[str, Any] | None:
    try:
        row = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or not isinstance(row.get("started_at"), (int, float)):
        return None
    return row


def current(state_dir: Path, pid: int, *, now: float | None = None) -> dict[str, Any] | None:
    target = _path(state_dir, pid)
    row = _read(target)
    stamp = time.time() if now is None else now
    if row is not None and stamp - float(row["started_at"]) > MAX_AGE_SECONDS:
        target.unlink(missing_ok=True)
        return None
    return row


def end(state_dir: Path, pid: int, *, now: float | None = None) -> dict[str, Any] | None:
    """The hold that applied to this relaunch (None when none), removed."""
    row = current(state_dir, pid, now=now)
    _path(state_dir, pid).unlink(missing_ok=True)
    return row


def listing(state_dir: Path, *, now: float | None = None) -> list[dict[str, Any]]:
    """Every launcher's pending hold in this router instance (operator surfaces)."""
    stamp = time.time() if now is None else now
    rows = []
    for path in sorted(Path(state_dir).glob(PREFIX + "*.json")):
        row = _read(path)
        if row is not None and stamp - float(row["started_at"]) <= MAX_AGE_SECONDS:
            rows.append({"pid": row.get("pid"), "request_id": str(row.get("request_id") or "")[:12],
                         "started_at": row["started_at"]})
    return rows


__all__ = ["MAX_AGE_SECONDS", "begin", "current", "end", "holds", "listing"]
