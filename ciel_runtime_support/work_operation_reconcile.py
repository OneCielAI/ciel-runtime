"""Read-only observation of whether an outcome-unknown operator command took effect.

An operator receipt is prepared before its effect and committed after it. When
the commit was lost, the receipt stays ``prepared`` (outcome unknown). A person
may ask the server to look: each command below names the one stored fact that
proves its effect applied or not applied, read from the store the command
writes, using only the receipt's text-free request summary. Nothing is re-run.
A command without such a fact answers ``not_observable``; the person may then
release the receipt as ``released_unverified`` (the effect may already have
happened, so retrying may duplicate it).

Proof fields, per command:
  parallel_item_review   finalization row (or stranded-v1 row) of lease_id:
                         decision equal to the request -> applied; undecided, a
                         different decision, or approve of a stranded row
                         (always refused) -> not_applied; no row -> not_observable.
  parallel_legacy_accept ledger lease_id: reviewing -> not_applied; accepted,
                         cleanup_pending or done -> applied.
  parallel_orphans_apply ledger lease_id: orphaned -> applied; still holding an
                         active slot -> not_applied.
  parallel_launch_resolve launch of the review row of lease_id with the same
                         launch_ref: manual_requested -> applied; another
                         unresolved state -> not_applied; a different ref or a
                         resolved launch -> not_observable.
  side_handoff           board side_handoffs[event_id]: requested -> not_applied;
                         answered or acknowledged -> applied.
  every other command    not_observable (settings and policy values, side review
                         receipt ids and side/review delivery retries are not
                         kept in the text-free summary, or have no stored proof).
"""
from __future__ import annotations

from typing import Any, Callable

APPLIED, NOT_APPLIED, NOT_OBSERVABLE = "applied", "not_applied", "not_observable"


def _ledger_lease(parallel_store: Callable[[], Any] | None, lease_id: Any) -> dict[str, Any] | None:
    if parallel_store is None or not isinstance(lease_id, str):
        return None
    return parallel_store().read()["leases"].get(lease_id)


def _review_row(parallel_store, identity, lease_id):
    """(journal, row) of the review that owns lease_id in this conversation, or (None, None)."""
    if parallel_store is None or not isinstance(lease_id, str):
        return None, None
    from .work_parallel_mcp import finalization, stranded
    store = parallel_store()
    side = stranded(store).read()["rows"].get(lease_id)
    if side is not None and side["scope"] == identity:
        return "stranded", side
    row = finalization(store).read()["reviews"].get(lease_id)
    if row is not None and row["scope"] == identity:
        return "finalization", row
    return None, None


def observe(command: str, request: dict[str, Any], *, identity: str, board: dict[str, Any],
            parallel_store: Callable[[], Any] | None) -> tuple[str, str]:
    """(result, observed word) for one outcome-unknown operator command. Read-only."""
    lease_id = request.get("lease_id")
    if command == "parallel_item_review":
        decision = request.get("decision")
        journal, row = _review_row(parallel_store, identity, lease_id)
        if row is None or decision not in ("approve", "rework"):
            return NOT_OBSERVABLE, "review_absent"
        if journal == "stranded":
            if decision == "approve":
                return NOT_APPLIED, "stranded_approve_refused"
            return (APPLIED, "decision_recorded") if row["decision_id"] is not None else (
                NOT_APPLIED, "decision_absent")
        if row["decision"] is None:
            return NOT_APPLIED, "decision_absent"
        return (APPLIED, "decision_recorded") if row["decision"] == decision else (NOT_APPLIED, "other_decision")
    if command == "parallel_legacy_accept":
        lease = _ledger_lease(parallel_store, lease_id)
        if lease is None:
            return NOT_OBSERVABLE, "lease_absent"
        if lease["state"] == "reviewing":
            return NOT_APPLIED, "lease_still_reviewing"
        if lease["state"] in ("accepted", "cleanup_pending", "done"):
            return APPLIED, "lease_accepted"
        return NOT_OBSERVABLE, "lease_" + lease["state"]
    if command == "parallel_orphans_apply":
        lease = _ledger_lease(parallel_store, lease_id)
        if lease is None:
            return NOT_OBSERVABLE, "lease_absent"
        if lease["state"] == "orphaned":
            return APPLIED, "lease_released"
        if lease.get("slot"):
            return NOT_APPLIED, "lease_still_active"
        return NOT_OBSERVABLE, "lease_" + lease["state"]
    if command == "parallel_launch_resolve":
        _, row = _review_row(parallel_store, identity, lease_id)
        launch = (row or {}).get("launch") or {}
        if not launch or launch.get("launch_ref") != request.get("launch_ref"):
            return NOT_OBSERVABLE, "launch_moved_on"
        if launch.get("state") == "manual_requested":
            return APPLIED, "launch_requested"
        from .work_parallel_finalization import UNRESOLVED_LAUNCH_STATES
        if launch.get("state") in UNRESOLVED_LAUNCH_STATES:
            return NOT_APPLIED, "launch_unrequested"
        return NOT_OBSERVABLE, "launch_resolved"
    if command == "side_handoff":
        row = (board.get("side_handoffs") or {}).get(request.get("event_id"))
        if not isinstance(row, dict):
            return NOT_OBSERVABLE, "handoff_absent"
        if row.get("state") == "requested":
            return NOT_APPLIED, "handoff_requested"
        if row.get("state") in ("answered", "acknowledged"):
            return APPLIED, "handoff_acknowledged"
        return NOT_OBSERVABLE, "handoff_" + str(row.get("state"))[:40]
    return NOT_OBSERVABLE, "no_stored_proof"
