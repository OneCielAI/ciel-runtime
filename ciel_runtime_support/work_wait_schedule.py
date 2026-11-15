"""Released-wait scheduling lane over the existing reserve and reacquire paths.

Order (design "Scheduling order"): effective P0 first, then a boosted item
whose exact parked ``waiting_input`` worker must reacquire, then other boosted
items newest release first, then ordinary P1-P5. The stored root priority and
queue order never change, a boost never takes an occupied slot, and each
priority keeps its own capacity.

A parked worker is resumed by main through SendMessage (the side actor tells
main which worker). Until then its priority keeps one free slot for it, so a
new ordinary launch cannot take the slot it needs.

The boost is consumed once, after the ledger commit that used it: a new
reservation, a parked reacquire, or an already active lease found for the
item. A capacity refusal changes nothing. A row whose board item is gone,
done, waiting again, blocked again or under a different root is removed before
selection; a later exact release creates a fresh row.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Mapping

from .work_parallel_wait_boost import (
    ATTEMPT_FIELDS, WaitBoostConflict, WaitBoostError, WaitBoostStore, boost_key, domain_digest,
)
from .work_wait_release import sidecar_for

ACTIVE = frozenset(("reserved", "running", "waiting_input", "submitted", "verifying",
                    "reviewing", "accepted", "cleanup_pending"))


def _nodes(board: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {node["id"]: node for node in board.get("nodes", [])}


def _root(by_id, item_id):
    node, seen = by_id.get(item_id), set()
    while node is not None and node.get("parent_id") is not None and node["id"] not in seen:
        seen.add(node["id"])
        node = by_id.get(node["parent_id"])
    return node


def stale_reason(row: Mapping[str, Any], board: Mapping[str, Any]) -> str | None:
    """Why a boosted row may no longer schedule, from the board alone."""
    by_id = _nodes(board)
    node = by_id.get(row["item_id"])
    if node is None or node.get("status") == "done":
        return "terminal"
    root = _root(by_id, row["item_id"])
    if root is None or root["id"] != row["root_id"] or root.get("created_at", 0) != row["root_created_at"]:
        return "identity_mismatch"
    if node.get("waiting_on") or node.get("status") == "blocked":
        return "reblocked"
    return None


def _item_leases(ledger: Mapping[str, Any], scope: str, item_id: str):
    return [lease for lease in ledger.get("leases", {}).values()
            if lease.get("scope") == scope and lease.get("item_id") == item_id
            and lease.get("state") in ACTIVE and lease.get("role") in ("worker", "resume")]


def parked_lease(row: Mapping[str, Any], ledger: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The exact pinned parked lease, still slot-free and bound to its host."""
    ident = row.get("parked_lease_id")
    lease = ledger.get("leases", {}).get(ident) if ident else None
    if (lease is None or lease.get("scope") != row["scope"] or lease.get("item_id") != row["item_id"]
            or lease.get("state") != "waiting_input" or lease.get("slot") is not False
            or not lease.get("host_id")):
        return None
    return lease


def _consumed(row: Mapping[str, Any], *, kind: str, outcome: str, lease: Mapping[str, Any],
              ledger_before: int, ledger_after: int, now: float) -> dict[str, Any]:
    attempt = uuid.uuid4().hex
    proof = {"scope": row["scope"], "item_id": row["item_id"], "release_receipt": row["release_receipt"],
             "attempt_id": attempt, "kind": kind, "lease_id": lease["id"], "state": lease["state"],
             "ledger_before_version": ledger_before, "ledger_after_version": ledger_after}
    done = dict(row)
    for name in ATTEMPT_FIELDS:
        done[name] = None
    done.update(
        state="consumed", outcome=outcome, consumed_at=now, updated_at=now,
        completed_attempt_id=attempt, completed_attempt_kind=kind,
        completed_ledger_before_version=ledger_before, completed_ledger_after_version=ledger_after,
        completed_lease_id=lease["id"],
        completed_receipt_digest=domain_digest("ciel.wait-boost.lane-receipt.v1", proof),
        operation_digest=domain_digest("ciel.wait-boost.lane-operation.v1", {**proof, "outcome": outcome}),
    )
    return done


def _finish(document, key, row, *, kind, outcome, lease, ledger_before, ledger_after, now):
    """Consume one boost. A parked reacquire or running notification keeps a
    consumed row with its completion identity. A new launch removes the row:
    the store's ``native_bound`` completion is reserved for the full
    BindSaga/WorkspaceChild launch, which this lane does not use, and the
    ledger lease is the durable launch record."""
    if kind == "reserve":
        del document["rows"][key]
        return
    document["rows"][key] = _consumed(row, kind=kind, outcome=outcome, lease=lease,
                                      ledger_before=ledger_before, ledger_after=ledger_after, now=now)


def _outcome_for(lease: Mapping[str, Any]) -> tuple[str, str]:
    if lease.get("state") == "waiting_input":
        return "reacquire", "reacquired"
    if lease.get("state") == "running" and lease.get("host_id"):
        return "running_notify", "running_notified"
    return "reserve", "native_bound"


class WaitLane:
    """One pass over the sidecar for a scope: reconcile, then order."""

    def __init__(self, ledger_path: str | Path):
        self.sidecar: WaitBoostStore = sidecar_for(ledger_path)

    def _change(self, mutate, now):
        for _ in range(5):
            try:
                return self.sidecar.change(self.sidecar.read()["version"], mutate, now=now)
            except WaitBoostConflict:
                continue
        raise WaitBoostConflict("wait boost sidecar kept changing")

    def reconcile(self, board: Mapping[str, Any], ledger: Mapping[str, Any], scope: str,
                  *, now: float | None = None) -> dict[str, Any]:
        """Remove stale rows and consume rows whose item already has an active lease."""
        clock = time.time() if now is None else now
        report = {"removed": [], "consumed": []}

        def mutate(document):
            report["removed"].clear()
            report["consumed"].clear()
            for key, row in list(document["rows"].items()):
                if row["scope"] != scope or row["state"] != "boosted":
                    continue
                reason = stale_reason(row, board)
                if reason is not None:
                    del document["rows"][key]
                    report["removed"].append((row["item_id"], reason))
                    continue
                if parked_lease(row, ledger) is not None:
                    continue
                active = _item_leases(ledger, scope, row["item_id"])
                if active:
                    lease = max(active, key=lambda item: item.get("id", ""))
                    kind, outcome = _outcome_for(lease)
                    _finish(document, key, row, kind=kind, outcome=outcome, lease=lease,
                            ledger_before=ledger.get("version", 0), ledger_after=ledger.get("version", 0),
                            now=clock)
                    report["consumed"].append(row["item_id"])

        try:
            self._change(mutate, clock)
        except (WaitBoostError, OSError, ValueError):
            report["unavailable"] = True
        return report

    def boosted(self, scope: str) -> list[dict[str, Any]]:
        try:
            rows = self.sidecar.read()["rows"].values()
        except (WaitBoostError, OSError, ValueError):
            return []
        return sorted((dict(row) for row in rows if row["scope"] == scope and row["state"] == "boosted"),
                      key=lambda row: (-row["release_at"], row["release_receipt"], row["item_id"]))

    def order(self, rows: list[dict[str, Any]], scope: str, ledger: Mapping[str, Any],
              board: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
        """Runnable rows with boosted launches first, the parked slot holds by
        priority, and the parked rows themselves (for status)."""
        boosted = self.boosted(scope)
        by_id = _nodes(board)
        parked = [row for row in boosted if parked_lease(row, ledger) is not None]
        holds: dict[str, int] = {}
        for row in parked:
            lease = parked_lease(row, ledger)
            holds[lease["priority"]] = holds.get(lease["priority"], 0) + 1
        launch_ids = [row["item_id"] for row in boosted
                      if row not in parked and row["item_id"] in by_id]
        rank = {item_id: index for index, item_id in enumerate(launch_ids)}
        first = sorted((row for row in rows if row["item_id"] in rank), key=lambda row: rank[row["item_id"]])
        ordered = first + [row for row in rows if row["item_id"] not in rank]
        return ordered, holds, parked

    def consume(self, scope: str, item_id: str, lease: Mapping[str, Any], *, kind: str, outcome: str,
                ledger_before: int, ledger_after: int, parked_only: bool = False,
                now: float | None = None) -> bool:
        """Consume the row for this item after its ledger commit; False if none."""
        clock = time.time() if now is None else now
        hit = {"value": False}

        def mutate(document):
            hit["value"] = False
            key = boost_key(scope, item_id)
            row = document["rows"].get(key)
            if row is None or row["state"] != "boosted":
                return
            if parked_only and row.get("parked_lease_id") != lease["id"]:
                return
            _finish(document, key, row, kind=kind, outcome=outcome, lease=lease,
                    ledger_before=ledger_before, ledger_after=ledger_after, now=clock)
            hit["value"] = True

        try:
            self._change(mutate, clock)
        except (WaitBoostError, OSError, ValueError):
            return False
        return hit["value"]


def held_out(rows: list[dict[str, Any]], holds: Mapping[str, int], counts: Mapping[str, int],
             limits: Mapping[str, int]) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop candidates whose priority has no free slot beyond parked holds."""
    kept, held = [], []
    for row in rows:
        priority = row["priority"]
        if holds.get(priority) and counts.get(priority, 0) + holds[priority] >= limits.get(priority, 0):
            held.append(priority)
            continue
        kept.append(row)
    return kept, sorted(set(held))
