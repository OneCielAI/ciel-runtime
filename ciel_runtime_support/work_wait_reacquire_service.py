"""Narrow parked-wait reacquire slot and send-result orchestration."""
from __future__ import annotations

from typing import Any, Mapping

from .work_parallel_wait_boost import WaitBoostError, WaitBoostStore, boost_key, domain_digest


class WaitReacquireError(WaitBoostError):
    pass


class WaitReacquireService:
    def __init__(self, ledger: Any, wait: WaitBoostStore):
        self.ledger = ledger
        self.wait = wait

    def acquire_slot(
        self, *, scope: str, item_id: str, attempt_id: str,
        wait_expected_version: int, now: float,
    ):
        key = boost_key(scope, item_id)
        document = self.wait.read()
        if document["version"] != wait_expected_version:
            raise WaitReacquireError("wait state changed; reread")
        row = document["rows"].get(key)
        self._pin(row, attempt_id)
        ledger = self.ledger.read()
        lease = ledger["leases"].get(row["lease_id"])
        if lease is None:
            raise WaitReacquireError("parked lease missing")
        committed = (
            lease.get("state") == "waiting_input"
            and lease.get("slot") is True
            and ledger["version"] == row["ledger_before_version"] + 1
        )
        if not committed:
            if ledger["version"] != row["ledger_before_version"]:
                raise WaitReacquireError("ledger changed without exact reacquire")
            if lease.get("state") != "waiting_input" or lease.get("slot") is not False:
                raise WaitReacquireError("lease is not parked")
            try:
                ledger = self.ledger.reacquire(
                    row["lease_id"], expected_version=row["ledger_before_version"]
                )
            except BaseException as error:
                ledger = self.ledger.read()
                lease = ledger["leases"].get(row["lease_id"])
                if not (
                    lease is not None
                    and lease.get("state") == "waiting_input"
                    and lease.get("slot") is True
                    and ledger["version"] == row["ledger_before_version"] + 1
                ):
                    raise WaitReacquireError("reacquire response lost without exact commit") from error
            lease = ledger["leases"][row["lease_id"]]
        digest = domain_digest(
            "ciel.wait-boost.reacquire-slot.v1",
            {"attempt_id": attempt_id, "lease": lease,
             "version": row["ledger_before_version"] + 1},
        )
        def apply(state):
            current = state["rows"].get(key)
            self._pin(current, attempt_id)
            if current["send_state"] in ("send_pending", "uncertain"):
                if current["slot_acquire_digest"] != digest:
                    raise WaitReacquireError("reacquire slot replay conflict")
                return current
            if current["send_state"] != "pending":
                raise WaitReacquireError("invalid reacquire send state")
            current["slot_acquired_version"] = row["ledger_before_version"] + 1
            current["slot_acquire_digest"] = digest
            current["send_state"] = "send_pending"
            current["updated_at"] = now
            return current
        return self.wait.change(wait_expected_version, apply, now=now)

    def classify_send(
        self, *, scope: str, item_id: str, attempt_id: str,
        delivered: bool | None, wait_expected_version: int, now: float,
    ):
        key = boost_key(scope, item_id)
        document = self.wait.read()
        if document["version"] != wait_expected_version:
            raise WaitReacquireError("wait state changed; reread")
        row = document["rows"].get(key)
        self._pin(row, attempt_id)
        if row["send_state"] not in ("send_pending", "uncertain"):
            raise WaitReacquireError("send result before slot proof")
        if delivered is None:
            def uncertain(state):
                current = state["rows"][key]
                self._pin(current, attempt_id)
                current["send_state"] = "uncertain"
                current["updated_at"] = now
                return current
            return "uncertain", self.wait.change(wait_expected_version, uncertain, now=now)
        if delivered is True:
            return "delivered", {
                "transition": "complete_consume", "outcome": "reacquired",
                "attempt_id": attempt_id, "send_receipt": row["send_receipt"],
                "lease_id": row["lease_id"],
            }
        ledger = self.ledger.read()
        lease = ledger["leases"].get(row["lease_id"])
        if lease is None:
            raise WaitReacquireError("rollback lease missing")
        if ledger["version"] != row["slot_acquired_version"]:
            raise WaitReacquireError("rollback ledger version changed")
        if lease.get("slot") is True:
            ledger = self.ledger.rollback_wait_reacquire(
                row["lease_id"], attempt_id=attempt_id,
                slot_acquired_version=row["slot_acquired_version"],
                expected_version=ledger["version"]
            )
            lease = ledger["leases"][row["lease_id"]]
        if lease.get("slot") is not False or lease.get("state") != "waiting_input":
            raise WaitReacquireError("rollback did not restore parked lease")
        receipt = domain_digest(
            "ciel.wait-boost.reacquire-rollback.v1",
            {"attempt_id": attempt_id, "send_receipt": row["send_receipt"],
             "lease": lease},
        )
        return "rolled_back", {
            "transition": "reacquire_send_failed_rolled_back",
            "outcome": "restored_boosted", "receipt_digest": receipt,
            "attempt_id": attempt_id, "lease_id": row["lease_id"],
        }

    @staticmethod
    def _pin(row: Mapping[str, Any] | None, attempt_id: str):
        if row is None or row.get("state") != "consuming":
            raise WaitReacquireError("reacquire row missing")
        if row.get("attempt_id") != attempt_id or row.get("attempt_kind") != "reacquire":
            raise WaitReacquireError("reacquire attempt identity changed")
