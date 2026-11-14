"""Resumable integration glue over accepted Gate-5 L3 primitives."""
from __future__ import annotations

from typing import Any

from .work_parallel_wait_boost import WaitBoostError, WaitBoostStore, boost_key
from .work_wait_cleanup_finalizer import WaitCleanupFinalizer
from .work_wait_effect_service import WaitEffectService
from .work_wait_parent_completion import WaitParentCompletionService
from .work_wait_reacquire_history import WaitReacquireHistoryService
from .work_wait_reacquire_service import WaitReacquireService
from .work_wait_reserve_service import WaitReserveService


class WaitOrchestrator:
    def __init__(
        self, *, sidecar: WaitBoostStore, ledger: Any, coordination: Any,
        cleanup_effects: Any | None = None,
    ):
        self.sidecar = sidecar
        self.ledger = ledger
        self.coordination = coordination
        self.reserve_service = WaitReserveService(ledger, sidecar)
        self.effects = WaitEffectService(sidecar, ledger, coordination)
        self.reacquire_service = WaitReacquireService(ledger, sidecar)
        self.reacquire_history = WaitReacquireHistoryService(sidecar)
        self.parent_completion = WaitParentCompletionService(sidecar)
        self.cleanup_effects = cleanup_effects
        self.cleanup_finalizer = WaitCleanupFinalizer(sidecar)

    def reserve(self, **request):
        return self.reserve_service.reserve(**request)

    def workspace_and_bind(
        self, *, attempt_id: str, workspace_manifest: dict,
        agent_id: str, agent_name: str, now: float,
    ):
        child = self.effects.attach_workspace_effects(
            attempt_id, workspace_manifest, now=now
        )
        if child["state"] != "coord_attached":
            raise WaitBoostError("workspace did not reach coord_attached")
        saga = self.effects.bind_effects(
            attempt_id, agent_id=agent_id, agent_name=agent_name, now=now
        )
        if saga["state"] != "done":
            raise WaitBoostError("bind did not reach done")
        state = self.sidecar.read()
        return self.parent_completion.complete_native_bound(
            attempt_id, expected_version=state["version"], now=now
        )

    def reacquire_slot(self, **request):
        return self.reacquire_service.acquire_slot(**request)

    def reacquire_result(
        self, *, scope: str, item_id: str, attempt_id: str,
        delivered: bool | None, wait_expected_version: int, now: float,
    ):
        outcome, proof = self.reacquire_service.classify_send(
            scope=scope, item_id=item_id, attempt_id=attempt_id,
            delivered=delivered, wait_expected_version=wait_expected_version, now=now,
        )
        if outcome == "uncertain":
            return outcome, proof
        state = self.sidecar.read()
        row = state["rows"][boost_key(scope, item_id)]
        if outcome == "delivered":
            return outcome, self.reacquire_history.complete(
                scope=scope, item_id=item_id, attempt_id=attempt_id,
                ledger_after_version=row["slot_acquired_version"],
                receipt_digest=row["send_receipt"],
                expected_version=state["version"], now=now,
            )
        return outcome, self.reacquire_history.rollback(
            scope=scope, item_id=item_id, attempt_id=attempt_id,
            rollback_receipt_digest=proof["receipt_digest"],
            expected_version=state["version"], now=now,
        )

    def cleanup_to_done(self, cleanup_key: str, *, now: float):
        if self.cleanup_effects is None:
            raise WaitBoostError("cleanup effects not configured")
        row = self.cleanup_effects.run(cleanup_key, now=now)
        if row["state"] != "lease_released":
            raise WaitBoostError("cleanup did not reach lease_released")
        state = self.sidecar.read()
        return self.cleanup_finalizer.finalize(
            cleanup_key, expected_version=state["version"], now=now
        )
