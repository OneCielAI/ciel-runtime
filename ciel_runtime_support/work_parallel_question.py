"""Fork question reports with an optional recorded external wait (question_wait).

A worker that asks main a question may also record the exact external state it
waits for (``waiting_on``) and how the side/gate actor verifies it
(``release_hint``) on its own assigned leaf, before its question receipt is
written. Moved out of work_parallel_mcp (file budget); behaviour unchanged.
"""
from __future__ import annotations

import copy
import time

from . import work_plan_tree as tree
from .work_parallel import WorkParallelConflict, WorkParallelError


def _question_detail(lease, args, evidence):
    """Return the unvalidated question body for the receipt validator."""
    detail = args.get("detail")
    if isinstance(detail, dict):
        return copy.deepcopy(detail)
    return {
        "question": evidence,
        "blocking_todos": [lease["item_id"]],
        "decision_needed": evidence,
    }


def _validate_question_detail_before_write(detail):
    """Validate a question body before any durable write; return its hint."""
    from .work_parallel_receipts import _validate_detail
    _validate_detail("question", detail)
    if "release_hint" not in detail:
        return None
    return tree.normalize_release_hint(detail["release_hint"])


def _record_question_wait(board_store, lease, detail):
    """Record question_wait on the assigned leaf before any receipt is written.

    ``waiting_on`` is the exact external answer/decision state this fork waits
    for. ``release_hint.lookup`` is the read-only evidence target by which the
    side/gate actor verifies it; neither value is an answer, approval, or
    authorization to execute. The lease's own item is the only writable target,
    even if caller arguments name another item (no item_id is consulted here).
    Only the exact leaf is changed; status and ancestors stay untouched.
    """
    item_id = lease["item_id"]
    waiting_on = detail["waiting_on"]
    try:
        release_hint = tree.normalize_release_hint(detail["release_hint"])
    except tree.WorkPlanError as error:
        raise WorkParallelError(str(error)) from None

    def mutate(doc):
        nodes = doc["nodes"]
        by_id = {node["id"]: node for node in nodes}
        if item_id not in by_id:
            raise WorkParallelError("unknown assigned Work item")
        assigned = detail.get("blocking_todos")
        if (type(assigned) is not list or len(assigned) != 1
                or type(assigned[0]) is not str or assigned[0] != item_id):
            raise WorkParallelError(
                "question_wait blocking_todos must be exactly the one assigned Work item; "
                "a duplicate or extra entry is refused before parking")
        if any(node["parent_id"] == item_id for node in nodes):
            raise WorkParallelError("question_wait must be recorded on a leaf Work item")
        index = next(i for i, row in enumerate(nodes) if row["id"] == item_id)
        node = nodes[index]
        if node["status"] == "done":
            raise WorkParallelError("question_wait requires an unfinished assigned Work item")
        if str(node.get("waiting_on") or "").strip():
            raise WorkParallelError("assigned Work item already has a recorded wait")
        replacement = dict(node)
        replacement["waiting_on"] = waiting_on
        replacement["release_hint"] = copy.deepcopy(release_hint)
        replacement["updated_at"] = time.time()
        nodes[index] = replacement

    from .work_plan_store import WorkPlanConflict

    for _ in range(4):
        try:
            version = board_store.read()["version"]
            board_store.transaction(mutate, expected_version=version)
            return
        except (WorkParallelError, tree.WorkPlanError):
            raise
        except Exception as error:
            if isinstance(error, WorkPlanConflict) or (
                    "plan changed" in str(error) and "version" in str(error)):
                continue
            raise WorkParallelError(str(error)) from error
    raise WorkParallelConflict("Work plan changed; question_wait not recorded")


def _worker_question(store, board_store, lease, coord, assignment, args, evidence):
    """Persist a question, recording a paired external wait before parking."""
    detail = _question_detail(lease, args, evidence)
    raw = args.get("detail")
    provided = raw if isinstance(raw, dict) else {}
    top_level_wait = args.get("waiting_on")
    top_level_hint = args.get("release_hint")
    if "waiting_on" in detail or "release_hint" in detail:
        supplied_wait = ((top_level_wait is not None
                          and provided.get("waiting_on") != top_level_wait)
                         or (top_level_hint is not None
                             and provided.get("release_hint") != top_level_hint))
        if supplied_wait:
            raise WorkParallelError(
                "question_wait requires detail.waiting_on/release_hint; "
                "top-level wait fields are not recorded")
        if "waiting_on" not in detail or "release_hint" not in detail:
            raise WorkParallelError(
                "question_wait requires waiting_on and a structured release_hint before parking")
        detail["release_hint"] = _validate_question_detail_before_write(detail)
    elif top_level_wait is not None or top_level_hint is not None:
        raise WorkParallelError(
            "question_wait requires waiting_on and a structured release_hint before parking; "
            "put both in detail because top-level wait fields are not recorded")

    # A missing coordination assignment cannot durably bind a structured
    # question_wait back to this exact lease, so decline rather than fabricate
    # a board wait or park a receipt that the side cannot trace.
    if "waiting_on" in detail and assignment is None:
        raise WorkParallelError(
            "question_wait assignment is missing; structured question_wait refused before parking")

    has_wait = "waiting_on" in detail and "release_hint" in detail
    # Validate the receipt body before any board, coordination, or ledger write.
    _validate_question_detail_before_write(detail)
    if has_wait:
        _record_question_wait(board_store, lease, detail)
    if assignment is not None:
        cstate = coord.read()
        _, question_receipt_digest = coord.persist_receipt(
            lease["id"], "question", detail, expected_version=cstate["version"])
    else:
        question_receipt_digest = None
    try:
        d, _ = store.report_question(
            lease["id"], plan_version=lease["plan_version"], evidence=evidence,
            expected_version=store.read()["version"])
    except WorkParallelConflict:
        current = store.read()["leases"].get(lease["id"])
        if current is None or current["state"] != "running" or not current["slot"]:
            raise
        d, _ = store.report_question(
            lease["id"], plan_version=lease["plan_version"], evidence=evidence,
            expected_version=store.read()["version"])
    result = {"lease_id": lease["id"], "version": d["version"], "item_id": lease["item_id"],
              "state": d["leases"][lease["id"]]["state"], "slot_released": True}
    if question_receipt_digest:
        result["receipt_digest"] = question_receipt_digest
    return result
