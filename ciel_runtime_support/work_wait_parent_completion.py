"""Atomic parent native_bound completion and workspace finalization roll-forward."""
from __future__ import annotations

import copy
import hashlib

from .work_parallel_wait_boost import WaitBoostError, WaitBoostStore, boost_key, history_id


def _ref(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()[:12]


class WaitParentCompletionService:
    def __init__(self, store: WaitBoostStore):
        self.store = store

    def complete_native_bound(self, attempt_id: str, *, expected_version: int, now: float):
        def apply(document):
            bind = document["bind_sagas"].get(attempt_id)
            workspace = document["workspaces"].get(attempt_id)
            if bind is None or bind["state"] != "done":
                raise WaitBoostError("bind is not done")
            if workspace is None or workspace["state"] not in ("coord_attached", "done"):
                raise WaitBoostError("workspace is not attached")
            key = boost_key(bind["scope"], bind["item_id"])
            parent = document["rows"].get(key)
            if parent is None:
                raise WaitBoostError("parent missing")
            receipt = bind["bind_receipt_digest"]
            operation = bind["operation_digest"]
            if parent["state"] == "consuming":
                if parent["attempt_id"] != attempt_id or parent["lease_id"] != bind["lease_id"]:
                    raise WaitBoostError("parent completion identity changed")
                before = parent["ledger_before_version"]
                parent.update(
                    state="consumed", outcome="native_bound", consumed_at=now,
                    completed_attempt_id=attempt_id, completed_attempt_kind="reserve",
                    completed_ledger_before_version=before,
                    completed_ledger_after_version=bind["ledger_after_version"],
                    completed_lease_id=bind["lease_id"],
                    completed_receipt_digest=receipt, operation_digest=operation,
                    updated_at=now,
                )
                for name in (
                    "attempt_id", "attempt_kind", "ledger_before_version", "lease_id",
                    "pre_state", "pre_slot", "pre_lease_digest", "expected_post_state",
                    "expected_post_slot", "expected_post_role", "expected_dispatch_name",
                    "send_receipt", "send_state", "slot_acquired_version",
                    "slot_acquire_digest", "attempt_request_digest", "launch_state",
                    "launch_lease_id", "assignment_generation", "assignment_digest",
                    "capability_derivation_digest", "launch_dispatch_name",
                    "reserve_receipt_digest", "launch_manifest_digest",
                ):
                    parent[name] = None
            elif not (
                parent["state"] == "consumed"
                and parent["outcome"] == "native_bound"
                and parent["completed_attempt_id"] == attempt_id
                and parent["completed_receipt_digest"] == receipt
            ):
                raise WaitBoostError("parent completion replay conflict")
            if workspace["state"] == "coord_attached":
                workspace["state"] = "done"
                workspace["updated_at"] = workspace["terminal_at"] = now
            completion_manifest = {
                "scope": parent["scope"], "item_id": parent["item_id"],
                "root_id": parent["root_id"], "root_created_at": parent["root_created_at"],
                "release_receipt": parent["release_receipt"],
                "completed_attempt_id": attempt_id, "completed_attempt_kind": "reserve",
                "completed_ledger_before_version": parent["completed_ledger_before_version"],
                "completed_ledger_after_version": parent["completed_ledger_after_version"],
                "completed_lease_id": parent["completed_lease_id"],
                "completed_receipt_digest": receipt, "operation_digest": operation,
            }
            history = {
                "history_id": "0" * 64, "scope": parent["scope"],
                "item_id": parent["item_id"], "release_receipt": parent["release_receipt"],
                "attempt_id": attempt_id, "kind": "completion",
                "transition": "complete_bound_launch", "outcome": "native_bound",
                "receipt_digest": receipt, "operation_digest": operation,
                "manifest": completion_manifest, "terminal_at": now,
                "result": {"state": "consumed", "outcome": "native_bound",
                           "recovery_required": False, "operation_ref": _ref(operation)},
            }
            history["history_id"] = history_id(history)
            existing = document["history"].get(history["history_id"])
            if existing is not None and existing != history:
                raise WaitBoostError("parent completion history conflict")
            document["history"][history["history_id"]] = history
            return {"parent": copy.deepcopy(parent), "workspace": copy.deepcopy(workspace),
                    "bind": copy.deepcopy(bind), "history": history}
        return self.store.change(expected_version, apply, now=now)
