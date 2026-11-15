"""Side-released waits enter the scheduling sidecar after the board commits.

Order (design "Board and sidecar order"): the board release and its bounded
``wait_boost`` receipt commit first; the sidecar row is created afterwards by
strict CAS, only for the first commit of that decision. A crash between the
two files loses the boost but never doubles it; the released item stays
runnable in ordinary order. The sidecar never holds wait text, tokens or host
ids.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from .work_parallel_wait_boost import (
    ATTEMPT_FIELDS, COMPLETION_FIELDS, FILE_NAME, LAUNCH_FIELDS, TERMINAL_FIELDS,
    WaitBoostConflict, WaitBoostStore, boost_key,
)

RECEIPT_FIELDS = frozenset(("item_id", "root_id", "root_created_at", "release_receipt",
                            "release_at", "wait_digest", "parked_lease_id"))
MAX_RECEIPTS = 16


def _hex(value: Any, length: int) -> bool:
    return type(value) is str and len(value) == length and all(c in "0123456789abcdef" for c in value)


def _text(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= 200 and "\x00" not in value


def _time(value: Any) -> bool:
    return type(value) in (int, float) and value == value and 0 <= value < 1e15


def valid_receipt(value: Any, receipt_id: str) -> bool:
    """Exact recovery receipt grammar; anything else creates no boost."""
    return (type(value) is dict and set(value) == RECEIPT_FIELDS
            and _text(value["item_id"]) and _text(value["root_id"])
            and _time(value["root_created_at"]) and value["release_receipt"] == receipt_id
            and _hex(value["release_receipt"], 64) and _time(value["release_at"])
            and _hex(value["wait_digest"], 64)
            and (value["parked_lease_id"] is None or _text(value["parked_lease_id"])))


def sidecar_for(ledger_path: str | Path) -> WaitBoostStore:
    return WaitBoostStore(Path(ledger_path).with_name(FILE_NAME))


def parked_projection(ledger_document: Mapping[str, Any], board: Mapping[str, Any], scope: str) -> dict:
    """Bounded pre-read of slot-free parked leases: ids and states only."""
    by_id = {node["id"]: node for node in board.get("nodes", [])}

    def root_of(item_id):
        node = by_id.get(item_id)
        seen = set()
        while node is not None and node.get("parent_id") is not None and node["id"] not in seen:
            seen.add(node["id"])
            node = by_id.get(node["parent_id"])
        return None if node is None else node["id"]

    rows = [{"item_id": lease["item_id"], "root_id": root_of(lease["item_id"]), "lease_id": lease["id"],
             "state": lease["state"], "slot": lease["slot"]}
            for lease in ledger_document.get("leases", {}).values()
            if lease.get("scope") == scope and lease.get("state") == "waiting_input"
            and lease.get("slot") is False and lease.get("host_id")]
    return {"ledger_version": ledger_document.get("version"), "rows": rows[:MAX_RECEIPTS]}


def boosted_row(scope: str, receipt: Mapping[str, Any], *, board_version_before: int,
                now: float) -> dict[str, Any]:
    row = {
        "scope": scope, "item_id": receipt["item_id"], "root_id": receipt["root_id"],
        "root_created_at": receipt["root_created_at"], "release_receipt": receipt["release_receipt"],
        "release_at": receipt["release_at"], "wait_digest": receipt["wait_digest"],
        "parked_lease_id": receipt["parked_lease_id"],
        "release_store_before_version": board_version_before, "state": "boosted",
        "outcome": None, "consumed_at": None, "updated_at": now,
    }
    for name in (*ATTEMPT_FIELDS, *LAUNCH_FIELDS, *COMPLETION_FIELDS, *TERMINAL_FIELDS):
        row[name] = None
    return row


def record_released(ledger_path: str | Path, scope: str, receipt_id: str, result: Mapping[str, Any],
                    *, now: float | None = None) -> dict[str, Any]:
    """Create or replace boosted rows from one committed decision's receipts.

    The same receipt is idempotent. A row whose attempt is consuming is never
    replaced: that attempt owns it until it completes or restores.
    """
    if result.get("duplicate"):
        # Only the first commit creates a row. A replay could otherwise boost an
        # item a second time after its launch already consumed the row; losing a
        # boost in the crash window only leaves the released item in ordinary order.
        return {"created": [], "kept": [], "consuming": [], "invalid": 0, "replay": True}
    receipts = result.get("wait_boost") or []
    queue = result.get("queue") if isinstance(result.get("queue"), dict) else {}
    version = queue.get("version")
    board_version_before = version - 1 if type(version) is int and version > 0 else 0
    valid = [r for r in receipts[:MAX_RECEIPTS] if valid_receipt(r, receipt_id)]
    outcome = {"created": [], "kept": [], "consuming": [], "invalid": len(receipts) - len(valid)}
    if not valid:
        return outcome
    clock = time.time() if now is None else now
    store = sidecar_for(ledger_path)

    def mutate(document):
        for receipt in valid:
            key = boost_key(scope, receipt["item_id"])
            prior = document["rows"].get(key)
            if prior is not None and prior["release_receipt"] == receipt["release_receipt"]:
                outcome["kept"].append(receipt["item_id"])
            elif prior is not None and prior["state"] == "consuming":
                outcome["consuming"].append(receipt["item_id"])
            else:
                document["rows"][key] = boosted_row(scope, receipt,
                                                    board_version_before=board_version_before, now=clock)
                outcome["created"].append(receipt["item_id"])

    for _ in range(5):
        try:
            store.change(store.read()["version"], mutate, now=clock)
            return outcome
        except WaitBoostConflict:
            for name in ("created", "kept", "consuming"):
                outcome[name].clear()
    raise WaitBoostConflict("wait boost sidecar kept changing; the board receipt will recover it")
