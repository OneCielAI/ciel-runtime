"""Narrow crash-recoverable wait reservation boundary.

This service performs only the ledger reservation and publication of its receipt
into the already-persisted ``consuming`` wait row.  Worker launch and binding are
outside this boundary.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .work_parallel import WorkParallelStore
from .work_parallel_wait_boost import (
    LAUNCH_FIELDS,
    WaitBoostConflict,
    WaitBoostStore,
    boost_key,
    domain_digest,
)


class WaitReserveError(ValueError):
    pass


class WaitReserveConflict(WaitReserveError):
    pass


def reserve_request_digest(
    *, scope: str, item_id: str, attempt_id: str, ledger_version: int,
    candidate: Mapping[str, Any]
) -> str:
    """Digest the complete caller projection pinned by a consuming row."""
    return domain_digest("work.wait.reserve.request.v1", {
        "scope": scope,
        "item_id": item_id,
        "attempt_id": attempt_id,
        "ledger_version": ledger_version,
        "candidate": copy.deepcopy(dict(candidate)),
    })


def _lease_matches(row: Mapping[str, Any], *, scope: str, item_id: str,
                   dispatch_name: str) -> bool:
    return (
        row.get("scope") == scope
        and row.get("item_id") == item_id
        and row.get("dispatch_name") == dispatch_name
        and row.get("state") == "reserved"
        and row.get("host_id") is None
        and row.get("slot") is True
        and row.get("role") == "worker"
    )


def _exact_matches(ledger: Mapping[str, Any], *, scope: str, item_id: str,
                   dispatch_name: str) -> list[Mapping[str, Any]]:
    return [row for row in ledger["leases"].values()
            if _lease_matches(row, scope=scope, item_id=item_id,
                              dispatch_name=dispatch_name)]


def _launch_proof(*, row: Mapping[str, Any], lease: Mapping[str, Any],
                  ledger_after_version: int) -> dict[str, Any]:
    assignment = {
        "scope": row["scope"], "root_id": row["root_id"],
        "item_id": row["item_id"], "attempt_id": row["attempt_id"],
        "lease_id": lease["id"], "dispatch_name": row["expected_dispatch_name"],
        "generation": 1,
    }
    assignment_digest = domain_digest("work.wait.reserve.assignment.v1", assignment)
    capability_digest = domain_digest("work.wait.reserve.capability.v1", {
        "assignment_digest": assignment_digest,
        "attempt_request_digest": row["attempt_request_digest"],
    })
    receipt_digest = domain_digest("work.wait.reserve.receipt.v1", {
        "assignment_digest": assignment_digest,
        "ledger_after_version": ledger_after_version,
        "lease": copy.deepcopy(dict(lease)),
    })
    manifest_digest = domain_digest("work.wait.reserve.manifest.v1", {
        "assignment": assignment,
        "capability_derivation_digest": capability_digest,
        "reserve_receipt_digest": receipt_digest,
    })
    return {
        "launch_state": "reserved",
        "launch_lease_id": lease["id"],
        "assignment_generation": 1,
        "assignment_digest": assignment_digest,
        "capability_derivation_digest": capability_digest,
        "launch_dispatch_name": row["expected_dispatch_name"],
        "reserve_receipt_digest": receipt_digest,
        "launch_manifest_digest": manifest_digest,
    }


class WaitReserveService:
    def __init__(self, ledger: WorkParallelStore, wait: WaitBoostStore):
        self.ledger = ledger
        self.wait = wait

    @staticmethod
    def _pin(row: Mapping[str, Any], *, scope: str, item_id: str,
             attempt_id: str, ledger_version: int,
             candidate: Mapping[str, Any]) -> None:
        if not (
            row.get("state") == "consuming"
            and row.get("attempt_kind") == "reserve"
            and row.get("scope") == scope
            and row.get("item_id") == item_id
            and row.get("attempt_id") == attempt_id
            and row.get("ledger_before_version") == ledger_version
            and row.get("expected_dispatch_name") == "work-wait-boost-" + attempt_id
            and row.get("attempt_request_digest") == reserve_request_digest(
                scope=scope, item_id=item_id, attempt_id=attempt_id,
                ledger_version=ledger_version, candidate=candidate)
        ):
            raise WaitReserveConflict("persisted reserve request changed")
        if candidate.get("item_id") != item_id or candidate.get("role") != "worker":
            raise WaitReserveConflict("candidate identity changed")

    def reserve(self, *, scope: str, item_id: str, attempt_id: str,
                candidate: Mapping[str, Any], ledger_expected_version: int,
                wait_expected_version: int, now: float) -> tuple[dict[str, Any], dict[str, Any]]:
        key = boost_key(scope, item_id)
        wait_before = self.wait.read()
        row = wait_before["rows"].get(key)
        if row is None:
            raise WaitReserveConflict("persisted reserve row missing")
        self._pin(row, scope=scope, item_id=item_id, attempt_id=attempt_id,
                  ledger_version=ledger_expected_version, candidate=candidate)

        launch_values = {name: row[name] for name in LAUNCH_FIELDS}
        if row["launch_state"] is not None:
            if not all(value is not None for value in launch_values.values()):
                raise WaitReserveConflict("partial persisted reserve receipt")
            return copy.deepcopy(wait_before), copy.deepcopy(launch_values)

        dispatch = row["expected_dispatch_name"]
        ledger = self.ledger.read()
        matches = _exact_matches(ledger, scope=scope, item_id=item_id,
                                 dispatch_name=dispatch)
        if len(matches) > 1:
            raise WaitReserveConflict("multiple matching reservations")
        if not matches:
            if ledger["version"] != ledger_expected_version:
                raise WaitReserveConflict("ledger changed without matching reservation")
            try:
                ledger, lease_id = self.ledger.reserve(
                    [copy.deepcopy(dict(candidate))], scope=scope,
                    expected_version=ledger_expected_version, now=now,
                    dispatch_name=dispatch)
            except BaseException as reserve_error:
                ledger = self.ledger.read()
                matches = _exact_matches(ledger, scope=scope, item_id=item_id,
                                         dispatch_name=dispatch)
                if len(matches) != 1:
                    if len(matches) > 1:
                        raise WaitReserveConflict("multiple matching reservations") from reserve_error
                    raise WaitReserveConflict("reservation response lost with no exact match") from reserve_error
                lease = matches[0]
                if ledger["version"] != ledger_expected_version + 1:
                    raise WaitReserveConflict(
                        "recovered reservation has unexpected ledger version"
                    ) from reserve_error
            else:
                lease = ledger["leases"][lease_id]
        else:
            lease = matches[0]
            if ledger["version"] != ledger_expected_version + 1:
                raise WaitReserveConflict("matching reservation has unexpected ledger version")

        if any(lease.get(name) != candidate.get(name) for name in
               ("item_id", "priority", "created_at", "plan_version", "usage", "role", "mode")):
            raise WaitReserveConflict("matching reservation candidate changed")
        proof = _launch_proof(row=row, lease=lease,
                              ledger_after_version=ledger["version"])

        def mutate(document: dict[str, Any]) -> dict[str, Any]:
            current = document["rows"].get(key)
            if current is None:
                raise WaitReserveConflict("persisted reserve row missing")
            self._pin(current, scope=scope, item_id=item_id, attempt_id=attempt_id,
                      ledger_version=ledger_expected_version, candidate=candidate)
            if current["launch_state"] is not None:
                current_proof = {name: current[name] for name in LAUNCH_FIELDS}
                if current_proof != proof:
                    raise WaitReserveConflict("different reserve receipt already persisted")
                return current_proof
            current["lease_id"] = lease["id"]
            current.update(proof)
            current["updated_at"] = now
            return copy.deepcopy(proof)

        def replay(document: dict[str, Any]) -> dict[str, Any]:
            current = document["rows"].get(key)
            if current is None:
                raise WaitReserveConflict("reserve receipt CAS lost")
            self._pin(current, scope=scope, item_id=item_id, attempt_id=attempt_id,
                      ledger_version=ledger_expected_version, candidate=candidate)
            current_proof = {name: current[name] for name in LAUNCH_FIELDS}
            if current_proof != proof or current.get("lease_id") != lease["id"]:
                raise WaitReserveConflict("reserve receipt CAS lost")
            return current_proof

        try:
            return self.wait.change(wait_expected_version, mutate, replay=replay, now=now)
        except WaitBoostConflict as error:
            raise WaitReserveConflict("reserve receipt CAS lost") from error
