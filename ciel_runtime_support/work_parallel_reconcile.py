"""Retire the halves of reservations that never launched (Celly, 2026-11-17).

A reservation has three durable halves: the ledger lease, its coordination run
and, for P0, a launch intent. A lease is deleted only while it is an unbound
reservation (``release_unbound``, or ``orphan_release`` of an unbound one), and
a run or intent can bind only through that lease. So:

- a coordination run still ``reserved`` with no agent whose lease is absent
  from the ledger can never launch; it is cancelled into the archive;
- a P0 intent still ``reserved`` whose lease is absent can never launch (the
  ``spawning`` transition precedes every native spawn); it is cancelled.

Lease ids are random and never reused, so an absent lease never returns. The
files are read in creation order (coordination, then P0, then the ledger): a
row seen first had its lease created before that read, so absence in the later
ledger read is a deletion, never a lease not yet written. Age is never used.

A ``spawning`` or ``bound`` intent whose lease is absent may have started a
fork; it is listed as ambiguous and left unchanged.

Before any write the three files are copied to a backup directory beside the
ledger; the newest ``BACKUP_KEEP`` backups are kept. Restoring one means
copying its files back while no router runs.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .work_parallel import WorkParallelConflict, WorkParallelError

RUN_OUTCOME = "reconciled: ledger lease absent, never launched"
SAMPLE = 20
BACKUP_DIR = "work-parallel-backups"
BACKUP_KEEP = 10


def _stores(store):
    from .work_parallel_mcp import coordination, p0_state
    return coordination(store), p0_state(store)


def preview(store) -> dict[str, Any]:
    """What reconciliation would retire now, and what it would leave."""
    coord, side = _stores(store)
    runs = coord.read()["runs"]
    intents = side.read()["intents"]
    leases = store.read()["leases"]
    orphan_runs = sorted(lease_id for lease_id, run in runs.items()
                         if run["state"] == "reserved" and run["agent_id"] is None and lease_id not in leases)
    orphan_intents = sorted(intent_id for intent_id, row in intents.items()
                            if row["state"] == "reserved" and row["lease_id"] not in leases)
    ambiguous = sorted(intent_id for intent_id, row in intents.items()
                       if row["state"] in ("spawning", "bound") and row["lease_id"] not in leases)
    body = {"orphan_runs": orphan_runs, "orphan_p0_intents": orphan_intents,
            "ambiguous_p0_intents": ambiguous}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"digest": digest, "orphan_run_count": len(orphan_runs),
            "orphan_p0_intent_count": len(orphan_intents), "ambiguous_p0_intent_count": len(ambiguous),
            "orphan_runs": orphan_runs[:SAMPLE], "orphan_p0_intents": orphan_intents[:SAMPLE],
            "ambiguous_p0_intents": ambiguous[:SAMPLE],
            "truncated": max(len(orphan_runs), len(orphan_intents), len(ambiguous)) > SAMPLE,
            "_all": body}


def public(view: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in view.items() if key != "_all"}


def _backup(store) -> str:
    coord, side = _stores(store)
    base = Path(store.path).with_name(BACKUP_DIR)
    target = base / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
    target.mkdir(parents=True, exist_ok=False)
    for path in (Path(store.path), Path(coord.path), Path(side.path)):
        if path.exists():
            # Each store replaces its file atomically, so a copy is one whole version.
            shutil.copy2(path, target / path.name)
    backups = sorted((row for row in base.iterdir() if row.is_dir()), key=lambda row: row.name)
    for old in backups[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return str(target)


def apply(store, *, expected_digest: str | None = None) -> dict[str, Any]:
    """Retire the previewed orphans. ``expected_digest`` pins an operator's preview."""
    view = preview(store)
    if expected_digest is not None and view["digest"] != expected_digest:
        raise WorkParallelConflict("Work reservations changed since the preview; preview again")
    rows = view["_all"]
    result = {"cancelled_runs": 0, "cancelled_p0_intents": 0, "backup": None,
              "ambiguous_p0_intent_count": len(rows["ambiguous_p0_intents"])}
    if not rows["orphan_runs"] and not rows["orphan_p0_intents"]:
        return result
    result["backup"] = _backup(store)
    coord, side = _stores(store)
    if rows["orphan_runs"]:
        for attempt in range(3):
            try:
                _, done = coord.cancel_unbound_runs(rows["orphan_runs"], outcome=RUN_OUTCOME,
                                                    expected_version=coord.read()["version"])
                result["cancelled_runs"] = len(done)
                break
            except WorkParallelConflict:
                if attempt == 2:
                    raise
    for intent_id in rows["orphan_p0_intents"]:
        for attempt in range(3):
            try:
                state = side.read()
                row = state["intents"].get(intent_id)
                if row is None or row["state"] != "reserved":
                    break
                side.transition_intent(intent_id, "cancelled", expected_version=state["version"])
                result["cancelled_p0_intents"] += 1
                break
            except WorkParallelConflict:
                if attempt == 2:
                    raise
    return result


def reconcile_quietly(store, log=None) -> dict[str, Any] | None:
    """The sweep's call: never raises; logs one line when it retired anything."""
    try:
        result = apply(store)
    except (OSError, ValueError, WorkParallelError) as error:
        if log is not None:
            log("WARN", f"work_reservation_reconcile_failed error={type(error).__name__}")
        return None
    # Ambiguous rows are left as they are and shown by the orphan preview; they
    # are named here only beside a write, so a repeat sweep logs nothing.
    if log is not None and (result["cancelled_runs"] or result["cancelled_p0_intents"]):
        log("INFO", f"work_reservation_reconciled runs={result['cancelled_runs']} "
                    f"p0_intents={result['cancelled_p0_intents']} "
                    f"ambiguous_p0_intents={result['ambiguous_p0_intent_count']} "
                    f"backup={json.dumps(result['backup'])}")
    return result
