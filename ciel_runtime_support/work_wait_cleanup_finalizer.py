"""Construct and commit the exact CleanupSaga done + restored parent edge."""
from __future__ import annotations

import copy

from .work_parallel_wait_boost import (
    ATTEMPT_FIELDS, COMPLETION_FIELDS, LAUNCH_FIELDS,
    WaitBoostError, WaitBoostStore, boost_key, domain_digest,
)


class WaitCleanupFinalizer:
    def __init__(self, store: WaitBoostStore):
        self.store = store

    def finalize(self, cleanup_key: str, *, expected_version: int, now: float):
        document = self.store.read()
        if document["version"] != expected_version:
            raise WaitBoostError("cleanup finalizer state changed; reread")
        cleanup = document["cleanups"].get(cleanup_key)
        if cleanup is None or cleanup["state"] != "lease_released":
            raise WaitBoostError("cleanup is not lease_released")
        key = boost_key(cleanup["scope"], cleanup["item_id"])
        parent = document["rows"].get(key)
        if parent is None or parent["state"] != "consuming":
            raise WaitBoostError("cleanup parent is not consuming")
        manifest = {
            name: parent[name]
            for name in (
                "scope", "item_id", "root_id", "root_created_at", "release_receipt",
                "release_at", "wait_digest", "parked_lease_id",
                "release_store_before_version", *ATTEMPT_FIELDS, *LAUNCH_FIELDS,
            )
        }
        manifest.update(
            transition=("launch_denied" if cleanup["cleanup_trigger"] == "spawn_denied" else "native_absent"),
            outcome="restored_boosted",
        )
        restore_receipt = domain_digest(
            "ciel.wait-boost.cleanup-restore-receipt.v1",
            {"cleanup_id": cleanup_key, "manifest": manifest},
        )
        restore_operation = domain_digest(
            "ciel.wait-boost.cleanup-restore-operation.v1",
            {"receipt_digest": restore_receipt, "attempt_id": cleanup["attempt_id"]},
        )
        cleanup_operation = domain_digest(
            "ciel.wait-boost.cleanup-operation.v1",
            {"cleanup_id": cleanup_key, "restore_operation": restore_operation},
        )
        restored = copy.deepcopy(parent)
        for name in (*ATTEMPT_FIELDS, *LAUNCH_FIELDS, *COMPLETION_FIELDS):
            restored[name] = None
        restored.update(
            state="boosted", outcome=None, consumed_at=None,
            terminal_attempt_id=parent["attempt_id"],
            terminal_transition=manifest["transition"],
            terminal_outcome="restored_boosted",
            terminal_receipt_digest=restore_receipt,
            terminal_operation_digest=restore_operation,
            terminal_at=now, terminal_manifest=manifest, updated_at=now,
        )
        done = copy.deepcopy(cleanup)
        done.update(
            state="done", boost_sidecar_before_version=expected_version,
            boost_sidecar_after_version=expected_version + 1,
            boost_restore_receipt_digest=restore_receipt,
            boost_restore_operation_digest=restore_operation,
            cleanup_operation_digest=cleanup_operation,
            updated_at=now, terminal_at=now,
        )
        replay_digest = domain_digest(
            "ciel.wait-boost.cleanup-done-replay.v1",
            {"cleanup": done, "boost": restored},
        )
        return self.store.atomic_cleanup_done(
            cleanup_id_value=cleanup_key, restored_boost=restored,
            done_cleanup=done, expected_version=expected_version,
            replay_manifest_digest=replay_digest,
        )
