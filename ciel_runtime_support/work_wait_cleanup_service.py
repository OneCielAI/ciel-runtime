"""Strict-CAS CleanupSaga phase persistence."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .work_parallel_wait_boost import (
    CLEANUP_FIELDS,
    WaitBoostError,
    WaitBoostStore,
    cleanup_id,
    cleanup_manifest,
    validate_cleanup,
)


class WaitCleanupService:
    def __init__(self, store: WaitBoostStore):
        self.store = store

    def prepare(self, row: Mapping[str, Any], *, expected_version: int, now: float):
        if type(row) is not dict or set(row) != set(CLEANUP_FIELDS):
            raise WaitBoostError("invalid cleanup fields")
        candidate = copy.deepcopy(row)
        for name in CLEANUP_FIELDS - frozenset((
            "cleanup_id", "cleanup_trigger", "cleanup_manifest_digest", "release_receipt",
            "attempt_id", "scope", "root_id", "root_created_at", "item_id", "lease_id",
            "workspace_operation_id", "bind_manifest_digest", "native_absence_receipt_digest",
            "state", "created_at", "updated_at",
        )):
            candidate[name] = None
        candidate["created_at"] = candidate["updated_at"] = now
        candidate["cleanup_id"] = cleanup_id(candidate)
        candidate["cleanup_manifest_digest"] = cleanup_manifest(candidate)
        key = candidate["cleanup_id"]
        validate_cleanup(key, candidate)
        immutable = CLEANUP_FIELDS - frozenset(("created_at", "updated_at"))
        def replay(document):
            existing = document["cleanups"].get(key)
            if existing is None or any(existing[name] != candidate[name] for name in immutable):
                raise WaitBoostError("cleanup prepare conflict")
            return existing
        def apply(document):
            if key in document["cleanups"]:
                return replay(document)
            document["cleanups"][key] = copy.deepcopy(candidate)
            return candidate
        return self.store.change(expected_version, apply, replay=replay, now=now)

    def bind_disposition(
        self, key: str, *, disposition: str, proof: Mapping[str, str],
        expected_version: int, now: float,
    ):
        if disposition not in ("rolled_back", "not_created") or type(proof) is not dict:
            raise WaitBoostError("invalid cleanup bind request")
        if disposition == "rolled_back":
            required = {"receipt_digest", "operation_digest"}
        else:
            required = {"no_bind_proof_digest"}
        if set(proof) != required or not all(type(value) is str and len(value) == 64 for value in proof.values()):
            raise WaitBoostError("invalid cleanup bind proof")
        def mutation(row):
            row["bind_disposition"] = disposition
            if disposition == "rolled_back":
                row["bind_rollback_receipt_digest"] = proof["receipt_digest"]
                row["bind_rollback_operation_digest"] = proof["operation_digest"]
            else:
                row["bind_manifest_digest"] = None
                row["no_bind_proof_digest"] = proof["no_bind_proof_digest"]
        return self._advance(key, "prepared", "bind_rolled_back", mutation, expected_version, now)

    def coord_detached(self, key, *, before_version, receipt_digest, expected_version, now):
        return self._version_edge(
            key, "bind_rolled_back", "coord_detached", "coord", before_version,
            receipt_digest, expected_version, now,
        )

    def ledger_detached(self, key, *, before_version, receipt_digest, expected_version, now):
        return self._version_edge(
            key, "coord_detached", "ledger_detached", "ledger", before_version,
            receipt_digest, expected_version, now,
        )

    def workspace_removed(self, key, *, receipt_digest, expected_version, now):
        return self._scalar_edge(
            key, "ledger_detached", "workspace_removed",
            "workspace_cleanup_receipt_digest", receipt_digest, expected_version, now,
        )

    def lease_released(self, key, *, receipt_digest, expected_version, now):
        return self._scalar_edge(
            key, "workspace_removed", "lease_released",
            "lease_release_receipt_digest", receipt_digest, expected_version, now,
        )

    def _version_edge(self, key, expected, target, prefix, before, receipt, version, now):
        if type(before) is not int or type(receipt) is not str or len(receipt) != 64:
            raise WaitBoostError("invalid cleanup detach request")
        def mutation(row):
            row[prefix + "_before_version"] = before
            row[prefix + "_after_version"] = before + 1
            row[prefix + "_detach_receipt_digest"] = receipt
        return self._advance(key, expected, target, mutation, version, now)

    def _scalar_edge(self, key, expected, target, field, receipt, version, now):
        if type(receipt) is not str or len(receipt) != 64:
            raise WaitBoostError("invalid cleanup scalar request")
        return self._advance(
            key, expected, target, lambda row: row.update({field: receipt}), version, now
        )

    def _advance(self, key, expected, target, mutate, version, now):
        order = {
            "prepared": 0, "bind_rolled_back": 1, "coord_detached": 2,
            "ledger_detached": 3, "workspace_removed": 4, "lease_released": 5,
        }
        def replay(document):
            row = document["cleanups"].get(key)
            if row is None or order.get(row["state"], -1) < order[target]:
                raise WaitBoostError("cleanup phase conflict")
            probe = copy.deepcopy(row)
            mutate(probe)
            changed = {name for name in CLEANUP_FIELDS if probe[name] != row[name]}
            if changed:
                raise WaitBoostError("cleanup replay conflict")
            return row
        def apply(document):
            row = document["cleanups"].get(key)
            if row is None:
                raise WaitBoostError("unknown cleanup")
            if order.get(row["state"], -1) >= order[target]:
                return replay(document)
            if row["state"] != expected:
                raise WaitBoostError("cleanup phase conflict")
            mutate(row)
            row["state"] = target
            row["updated_at"] = now
            return row
        return self.store.change(version, apply, replay=replay, now=now)
