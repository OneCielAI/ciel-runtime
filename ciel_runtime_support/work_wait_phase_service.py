"""Strict-CAS sidecar phase transitions for wait workspace and bind sagas."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .work_parallel_wait_boost import (
    BIND_FIELDS,
    WORKSPACE_FIELDS,
    WaitBoostError,
    WaitBoostStore,
    bind_receipt_digest,
    domain_digest,
    validate_bind,
    validate_workspace,
)


def _exact(value: Mapping[str, Any], fields, name: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(fields):
        raise WaitBoostError("invalid " + name + " fields")
    return copy.deepcopy(value)


class WaitPhaseService:
    def __init__(self, store: WaitBoostStore):
        self.store = store

    def prepare_workspace(self, row, *, expected_version, now):
        candidate = _exact(row, WORKSPACE_FIELDS, "workspace")
        candidate["state"] = "prepared"
        candidate["ready_receipt_digest"] = None
        candidate["lease_attach_receipt_digest"] = None
        candidate["coord_attach_receipt_digest"] = None
        candidate["rollback_receipt_digest"] = None
        candidate["rollback_operation_digest"] = None
        candidate["created_at"] = candidate["updated_at"] = now
        candidate["terminal_at"] = None
        attempt = candidate["attempt_id"]
        validate_workspace(attempt, candidate)
        identity_fields = WORKSPACE_FIELDS - frozenset(("created_at", "updated_at"))
        def replay(document):
            existing = document["workspaces"].get(attempt)
            if existing is None or any(existing[name] != candidate[name] for name in identity_fields):
                raise WaitBoostError("workspace prepare conflict")
            return existing
        def apply(document):
            existing = document["workspaces"].get(attempt)
            if existing is not None:
                return replay(document)
            document["workspaces"][attempt] = copy.deepcopy(candidate)
            return candidate
        return self.store.change(expected_version, apply, replay=replay, now=now)

    def advance_workspace(
        self, attempt_id, *, expected_state, target_state, receipt_field,
        receipt_digest, expected_version, now,
    ):
        allowed = {
            ("prepared", "ready", "ready_receipt_digest"),
            ("ready", "lease_attached", "lease_attach_receipt_digest"),
            ("lease_attached", "coord_attached", "coord_attach_receipt_digest"),
        }
        if (expected_state, target_state, receipt_field) not in allowed:
            raise WaitBoostError("invalid workspace edge")
        if type(attempt_id) is not str or type(receipt_digest) is not str or len(receipt_digest) != 64:
            raise WaitBoostError("invalid workspace edge request")
        phase_order = {"prepared": 0, "ready": 1, "lease_attached": 2, "coord_attached": 3, "done": 4}
        def replay(document):
            row = document["workspaces"].get(attempt_id)
            if row is None or phase_order.get(row["state"], -1) < phase_order[target_state]:
                raise WaitBoostError("workspace phase conflict")
            if row[receipt_field] != receipt_digest:
                raise WaitBoostError("workspace edge replay conflict")
            return row
        def apply(document):
            row = document["workspaces"].get(attempt_id)
            if row is None:
                raise WaitBoostError("unknown workspace")
            if phase_order.get(row["state"], -1) >= phase_order[target_state]:
                return replay(document)
            if row["state"] != expected_state:
                raise WaitBoostError("workspace phase conflict")
            row["state"] = target_state
            row[receipt_field] = receipt_digest
            row["updated_at"] = now
            return row
        return self.store.change(expected_version, apply, replay=replay, now=now)

    def prepare_bind(self, row, *, expected_version, now):
        candidate = _exact(row, BIND_FIELDS, "bind")
        candidate["state"] = "prepared"
        for name in (
            "ledger_after_version", "ledger_bound_digest", "coord_after_version",
            "coord_bound_digest", "bind_receipt_digest", "operation_digest",
            "rollback_receipt_digest", "rollback_operation_digest", "terminal_at",
        ):
            candidate[name] = None
        candidate["created_at"] = candidate["updated_at"] = now
        attempt = candidate["attempt_id"]
        validate_bind(attempt, candidate)
        identity_fields = BIND_FIELDS - frozenset(("created_at", "updated_at"))
        def replay(document):
            existing = document["bind_sagas"].get(attempt)
            if existing is None or any(existing[name] != candidate[name] for name in identity_fields):
                raise WaitBoostError("bind prepare conflict")
            return existing
        def apply(document):
            existing = document["bind_sagas"].get(attempt)
            if existing is not None:
                return replay(document)
            document["bind_sagas"][attempt] = copy.deepcopy(candidate)
            return candidate
        return self.store.change(expected_version, apply, replay=replay, now=now)

    def record_ledger_bound(
        self, attempt_id, *, after_version, bound_digest, expected_version, now,
    ):
        return self._bind_edge(
            attempt_id, "prepared", "ledger_bound", expected_version, now,
            ledger_after_version=after_version, ledger_bound_digest=bound_digest,
        )

    def record_coord_bound(
        self, attempt_id, *, after_version, bound_digest, expected_version, now,
    ):
        return self._bind_edge(
            attempt_id, "ledger_bound", "coord_bound", expected_version, now,
            coord_after_version=after_version, coord_bound_digest=bound_digest,
        )

    def finish_bind(self, attempt_id, *, operation_digest, expected_version, now):
        if type(operation_digest) is not str or len(operation_digest) != 64:
            raise WaitBoostError("invalid bind done request")
        def replay(document):
            row = document["bind_sagas"].get(attempt_id)
            if row is None or row["state"] != "done" or row["operation_digest"] != operation_digest:
                raise WaitBoostError("bind done replay conflict")
            return row
        def apply(document):
            row = document["bind_sagas"].get(attempt_id)
            if row is None:
                raise WaitBoostError("unknown bind saga")
            if row["state"] == "done":
                return replay(document)
            if row["state"] != "coord_bound":
                raise WaitBoostError("bind done phase conflict")
            row["state"] = "done"
            row["operation_digest"] = operation_digest
            row["updated_at"] = row["terminal_at"] = now
            row["bind_receipt_digest"] = bind_receipt_digest(row)
            return row
        return self.store.change(expected_version, apply, replay=replay, now=now)

    def _bind_edge(self, attempt_id, expected, target, expected_version, now, **proof):
        if type(attempt_id) is not str or any(type(value) not in (str, int) for value in proof.values()):
            raise WaitBoostError("invalid bind edge request")
        phase_order = {"prepared": 0, "ledger_bound": 1, "coord_bound": 2, "done": 3}
        def replay(document):
            row = document["bind_sagas"].get(attempt_id)
            if row is None or phase_order.get(row["state"], -1) < phase_order[target]:
                raise WaitBoostError("bind phase conflict")
            if any(row[name] != value for name, value in proof.items()):
                raise WaitBoostError("bind edge replay conflict")
            return row
        def apply(document):
            row = document["bind_sagas"].get(attempt_id)
            if row is None:
                raise WaitBoostError("unknown bind saga")
            if phase_order.get(row["state"], -1) >= phase_order[target]:
                return replay(document)
            if row["state"] != expected:
                raise WaitBoostError("bind phase conflict")
            row.update(proof)
            row["state"] = target
            row["updated_at"] = now
            return row
        return self.store.change(expected_version, apply, replay=replay, now=now)


def workspace_receipt(domain: str, manifest: Mapping[str, Any]) -> str:
    return domain_digest(domain, dict(manifest))
