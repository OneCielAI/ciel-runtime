"""Atomic terminal persistence for wait reacquire send outcomes."""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from .work_parallel_wait_boost import (
    COMPLETION_FIELDS,
    FULL_PIN_FIELDS,
    WaitBoostError,
    WaitBoostStore,
    boost_key,
    domain_digest,
    history_id,
)


def _op_ref(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()[:12]


class WaitReacquireHistoryService:
    def __init__(self, store: WaitBoostStore):
        self.store = store

    def complete(
        self, *, scope: str, item_id: str, attempt_id: str,
        ledger_after_version: int, receipt_digest: str,
        expected_version: int, now: float,
    ):
        key = boost_key(scope, item_id)
        def apply(document):
            row = document["rows"].get(key)
            self._pin(row, attempt_id)
            operation = domain_digest(
                "ciel.wait-boost.complete-consume.v1",
                {"scope": scope, "item_id": item_id, "attempt_id": attempt_id,
                 "lease_id": row["lease_id"], "receipt_digest": receipt_digest,
                 "ledger_after_version": ledger_after_version},
            )
            manifest = {
                "scope": scope, "item_id": item_id, "root_id": row["root_id"],
                "root_created_at": row["root_created_at"],
                "release_receipt": row["release_receipt"],
                "completed_attempt_id": attempt_id,
                "completed_attempt_kind": "reacquire",
                "completed_ledger_before_version": row["ledger_before_version"],
                "completed_ledger_after_version": ledger_after_version,
                "completed_lease_id": row["lease_id"],
                "completed_receipt_digest": receipt_digest,
                "operation_digest": operation,
            }
            history = self._history(
                row, kind="completion", transition="complete_consume",
                outcome="reacquired", receipt=receipt_digest,
                operation=operation, manifest=manifest, result_state="consumed", now=now,
            )
            hid = history["history_id"]
            existing = document["history"].get(hid)
            if existing is not None and existing != history:
                raise WaitBoostError("completion history conflict")
            document["history"][hid] = history
            row.update(
                state="consumed", outcome="reacquired", consumed_at=now,
                completed_attempt_id=attempt_id, completed_attempt_kind="reacquire",
                completed_ledger_before_version=row["ledger_before_version"],
                completed_ledger_after_version=ledger_after_version,
                completed_lease_id=row["lease_id"],
                completed_receipt_digest=receipt_digest, operation_digest=operation,
                updated_at=now,
            )
            for name in (*FULL_PIN_FIELDS - {"scope", "item_id", "root_id", "root_created_at", "release_receipt", "release_at", "wait_digest", "parked_lease_id", "release_store_before_version"},):
                if name in row and name not in COMPLETION_FIELDS:
                    row[name] = None
            return {"row": copy.deepcopy(row), "history": history}
        return self.store.change(expected_version, apply, now=now)

    def rollback(
        self, *, scope: str, item_id: str, attempt_id: str,
        rollback_receipt_digest: str, expected_version: int, now: float,
    ):
        key = boost_key(scope, item_id)
        def apply(document):
            row = document["rows"].get(key)
            self._pin(row, attempt_id)
            manifest = {name: row[name] for name in FULL_PIN_FIELDS}
            manifest["rollback_receipt_digest"] = rollback_receipt_digest
            operation = domain_digest(
                "ciel.wait-boost.send-rollback.v1", manifest
            )
            history = self._history(
                row, kind="send_rollback",
                transition="reacquire_send_failed_rolled_back",
                outcome="delivery_failed_rolled_back", receipt=rollback_receipt_digest,
                operation=operation, manifest=manifest, result_state="boosted", now=now,
            )
            hid = history["history_id"]
            existing = document["history"].get(hid)
            if existing is not None and existing != history:
                raise WaitBoostError("rollback history conflict")
            document["history"][hid] = history
            for name in (*COMPLETION_FIELDS,):
                row[name] = None
            row.update(
                state="boosted", outcome=None, consumed_at=None,
                terminal_attempt_id=attempt_id,
                terminal_transition="reacquire_send_failed_rolled_back",
                terminal_outcome="delivery_failed_rolled_back",
                terminal_receipt_digest=rollback_receipt_digest,
                terminal_operation_digest=operation, terminal_at=now,
                terminal_manifest=manifest, updated_at=now,
            )
            for name in FULL_PIN_FIELDS:
                if name in row and name not in {
                    "scope", "item_id", "root_id", "root_created_at", "release_receipt",
                    "release_at", "wait_digest", "parked_lease_id", "release_store_before_version",
                }:
                    row[name] = None
            return {"row": copy.deepcopy(row), "history": history}
        return self.store.change(expected_version, apply, now=now)

    @staticmethod
    def _pin(row: Mapping[str, Any] | None, attempt_id: str):
        if row is None or row.get("state") != "consuming" or row.get("attempt_id") != attempt_id:
            raise WaitBoostError("reacquire terminal identity changed")
        if row.get("attempt_kind") != "reacquire":
            raise WaitBoostError("terminal attempt is not reacquire")

    @staticmethod
    def _history(row, *, kind, transition, outcome, receipt, operation, manifest, result_state, now):
        value = {
            "history_id": "0" * 64, "scope": row["scope"], "item_id": row["item_id"],
            "release_receipt": row["release_receipt"], "attempt_id": row["attempt_id"],
            "kind": kind, "transition": transition, "outcome": outcome,
            "receipt_digest": receipt, "operation_digest": operation,
            "manifest": manifest, "terminal_at": now,
            "result": {"state": result_state, "outcome": outcome,
                       "recovery_required": False, "operation_ref": _op_ref(operation)},
        }
        value["history_id"] = history_id(value)
        return value
