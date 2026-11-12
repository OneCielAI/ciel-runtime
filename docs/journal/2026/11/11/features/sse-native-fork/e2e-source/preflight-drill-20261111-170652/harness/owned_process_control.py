"""Windows owned-process discovery and cleanup for E2E drivers.

Safety contract:
- ownership is an exact (PID, CreationDate) identity, never a bare PID;
- a child is accepted only while its exact parent identity is still present and
  the child's CreationDate is strictly later than the parent's;
- every identity is re-read from CIM immediately before an explicit leaf kill;
- taskkill is invoked for one PID without /T; stdout, stderr and exit code are
  retained in the JSONL log;
- discovery repeats after every kill until no owned identity remains.

The caller supplies protected identities. Every ready-tree identity, dynamically
accepted child and explicit kill is checked against them and recorded.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

NO_WINDOW = 0x08000000


def creation_epoch(value: Any) -> float | None:
    match = re.search(r"Date\((\d+)", str(value or ""))
    return int(match.group(1)) / 1000 if match else None


def identity(row: dict[str, Any]) -> tuple[int, str]:
    return int(row["ProcessId"]), str(row.get("CreationDate") or "")


def snapshot_all() -> list[dict[str, Any]]:
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CreationDate,CommandLine | "
        "ConvertTo-Json -Depth 3"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("CIM process snapshot failed: " + completed.stderr[:500])
    value = json.loads(completed.stdout) if completed.stdout.strip() else []
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        row["CreationDate"] = str(row.get("CreationDate") or "")
    return rows


def row_for(ident: tuple[int, str], rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in rows if identity(row) == ident), None)


def discover_owned(
    root: tuple[int, str],
    rows: list[dict[str, Any]],
    accepted: dict[tuple[int, str], dict[str, Any]] | None = None,
) -> tuple[dict[tuple[int, str], dict[str, Any]], list[dict[str, Any]]]:
    """Extend one owned identity ledger from a current full process snapshot.

    Previously accepted identities stay in the ledger as history. New children
    are admitted only under an accepted identity that is present in *this*
    snapshot, and only with a strictly later CreationDate.
    """
    ledger = {} if accepted is None else dict(accepted)
    events: list[dict[str, Any]] = []
    current = {identity(row): row for row in rows}
    if root not in ledger:
        root_row = current.get(root)
        if root_row is None:
            raise RuntimeError("owned root identity is absent")
        ledger[root] = dict(root_row)
        events.append({"event": "accept_root", "identity": list(root)})
    changed = True
    while changed:
        changed = False
        current_parents = {ident: current[ident] for ident in ledger if ident in current}
        for row in rows:
            child = identity(row)
            if child in ledger:
                continue
            parent_pid = int(row.get("ParentProcessId") or 0)
            candidates = [
                (ident, parent)
                for ident, parent in current_parents.items()
                if ident[0] == parent_pid
            ]
            for parent_ident, parent in candidates:
                child_time = creation_epoch(row.get("CreationDate"))
                parent_time = creation_epoch(parent.get("CreationDate"))
                if child_time is None or parent_time is None or child_time <= parent_time:
                    events.append({
                        "event": "reject_child_time",
                        "identity": list(child),
                        "parent_identity": list(parent_ident),
                    })
                    continue
                ledger[child] = dict(row)
                events.append({
                    "event": "accept_child",
                    "identity": list(child),
                    "parent_identity": list(parent_ident),
                })
                changed = True
                break
    return ledger, events


def _children(ident: tuple[int, str], live: dict[tuple[int, str], dict[str, Any]]) -> list[tuple[int, str]]:
    return [child for child, row in live.items() if int(row.get("ParentProcessId") or 0) == ident[0]]


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def kill_owned_leaf_first(
    root: tuple[int, str],
    protected: set[tuple[int, str]],
    log_path: Path,
    *,
    initial_rows: list[dict[str, Any]] | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    """Kill one exact owned tree by explicit leaf PIDs, never taskkill /T."""
    started = time.time()
    rows = snapshot_all() if initial_rows is None else initial_rows
    ledger, discovered = discover_owned(root, rows)
    for event in discovered:
        _append_jsonl(log_path, {"at": time.time(), **event})
    intersections: list[dict[str, Any]] = []
    killed: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    while time.time() - started < timeout:
        rows = snapshot_all()
        ledger, events = discover_owned(root, rows, ledger)
        for event in events:
            _append_jsonl(log_path, {"at": time.time(), **event})
        live = {ident: row for ident, row in ((identity(r), r) for r in rows) if ident in ledger}
        current_intersections = sorted(set(live) & protected)
        if current_intersections:
            intersections.extend({"identity": list(item), "phase": "before_kill"} for item in current_intersections)
            for item in current_intersections:
                _append_jsonl(log_path, {"at": time.time(), "event": "protected_intersection", "identity": list(item)})
            raise RuntimeError("owned process ledger intersects protected identities")
        if not live:
            break

        leaves = [ident for ident in live if not _children(ident, live)]
        # Stable ordering makes the log reproducible. The root is killed only when it is a leaf.
        leaves.sort(key=lambda item: (item == root, item[0]))
        if not leaves:
            raise RuntimeError("owned process graph has no leaf")
        for ident in leaves:
            # Re-read immediately before the kill. A reused PID cannot match both fields.
            prekill_rows = snapshot_all()
            exact = row_for(ident, prekill_rows)
            if exact is None:
                row = {"at": time.time(), "event": "skip_identity_gone", "identity": list(ident)}
                attempts.append(row); _append_jsonl(log_path, row)
                continue
            # Reconfirm that any newly appeared child is admitted before killing the parent.
            ledger, new_events = discover_owned(root, prekill_rows, ledger)
            for event in new_events:
                _append_jsonl(log_path, {"at": time.time(), **event})
            current = {identity(r): r for r in prekill_rows}
            owned_live = {item: current[item] for item in ledger if item in current}
            if _children(ident, owned_live):
                row = {"at": time.time(), "event": "skip_not_leaf", "identity": list(ident)}
                attempts.append(row); _append_jsonl(log_path, row)
                continue
            if ident in protected:
                intersections.append({"identity": list(ident), "phase": "explicit_kill"})
                _append_jsonl(log_path, {"at": time.time(), "event": "protected_intersection", "identity": list(ident), "phase": "explicit_kill"})
                raise RuntimeError("explicit kill identity is protected")
            completed = subprocess.run(
                ["taskkill", "/PID", str(ident[0]), "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
                check=False,
            )
            attempt = {
                "at": time.time(),
                "event": "taskkill",
                "identity": list(ident),
                "name": exact.get("Name"),
                "parent_pid": exact.get("ParentProcessId"),
                "argv": ["taskkill", "/PID", str(ident[0]), "/F"],
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
            attempts.append(attempt); _append_jsonl(log_path, attempt)
            time.sleep(0.15)
            post = snapshot_all()
            if row_for(ident, post) is None:
                killed.append({
                    "identity": list(ident),
                    "name": exact.get("Name"),
                    "parent_pid": exact.get("ParentProcessId"),
                })
            else:
                rejected.append({"identity": list(ident), "reason": "identity still live after taskkill"})
        time.sleep(0.1)
    else:
        raise TimeoutError("owned cleanup exceeded deadline")

    final_rows = snapshot_all()
    final_live = [list(ident) for ident in ledger if row_for(ident, final_rows) is not None]
    report = {
        "root": list(root),
        "owned_identities": [list(item) for item in sorted(ledger)],
        "dynamic_identities": [list(item) for item in sorted(set(ledger) - {root})],
        "killed": killed,
        "attempts": attempts,
        "rejected": rejected,
        "protected_intersections": intersections,
        "final_live": final_live,
        "completed": not final_live and not intersections,
        "duration_s": time.time() - started,
        "log": str(log_path),
    }
    _append_jsonl(log_path, {"at": time.time(), "event": "cleanup_result", "report": report})
    return report
