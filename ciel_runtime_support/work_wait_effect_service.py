"""Roll-forward orchestration for workspace and bind external effects."""
from __future__ import annotations

from typing import Any, Mapping

from .work_parallel_wait_boost import WaitBoostError, WaitBoostStore, domain_digest
from .work_wait_phase_service import WaitPhaseService


class WaitEffectService:
    def __init__(self, sidecar: WaitBoostStore, ledger: Any, coordination: Any):
        self.sidecar = sidecar
        self.ledger = ledger
        self.coordination = coordination
        self.phases = WaitPhaseService(sidecar)

    def attach_workspace_effects(
        self, attempt_id: str, workspace_manifest: Mapping[str, Any], *, now: float
    ) -> dict[str, Any]:
        """Roll forward ready -> ledger attach -> coordination attach exactly once."""
        document = self.sidecar.read()
        child = document["workspaces"].get(attempt_id)
        if child is None:
            raise WaitBoostError("unknown workspace")
        manifest = dict(workspace_manifest)
        manifest_digest = domain_digest(
            "ciel.wait-boost.workspace-manifest.v1", manifest
        )
        if child["manifest_digest"] != manifest_digest:
            raise WaitBoostError("workspace manifest identity changed")
        if child["state"] == "prepared":
            receipt = domain_digest(
                "ciel.wait-boost.workspace-ready.v1",
                {"attempt_id": attempt_id, "workspace": manifest},
            )
            document, child = self.phases.advance_workspace(
                attempt_id, expected_state="prepared", target_state="ready",
                receipt_field="ready_receipt_digest", receipt_digest=receipt,
                expected_version=document["version"], now=now,
            )
        if child["state"] == "ready":
            before = self.ledger.read()
            lease = before["leases"].get(child["lease_id"])
            if lease is None:
                raise WaitBoostError("workspace lease disappeared")
            if lease.get("workspace") is None:
                after = self.ledger.attach_workspace(
                    child["lease_id"], manifest, expected_version=before["version"]
                )
                lease = after["leases"][child["lease_id"]]
            elif lease.get("workspace") == manifest:
                after = before
            else:
                raise WaitBoostError("ledger workspace identity changed")
            receipt = domain_digest(
                "ciel.wait-boost.workspace-lease-attach.v1",
                {"attempt_id": attempt_id, "manifest_digest": manifest_digest,
                 "lease": lease},
            )
            document, child = self.phases.advance_workspace(
                attempt_id, expected_state="ready", target_state="lease_attached",
                receipt_field="lease_attach_receipt_digest", receipt_digest=receipt,
                expected_version=document["version"], now=now,
            )
        if child["state"] == "lease_attached":
            before = self.coordination.read()
            run = before["runs"].get(child["lease_id"])
            if run is None:
                raise WaitBoostError("workspace coordination run disappeared")
            if run.get("workspace") is None:
                after, run = self.coordination.attach_workspace(
                    child["lease_id"], manifest, expected_version=before["version"]
                )
            elif run.get("workspace") == manifest:
                after = before
            else:
                raise WaitBoostError("coord workspace identity changed")
            receipt = domain_digest(
                "ciel.wait-boost.workspace-coord-attach.v1",
                {"attempt_id": attempt_id, "manifest_digest": manifest_digest,
                 "run": run},
            )
            document, child = self.phases.advance_workspace(
                attempt_id, expected_state="lease_attached", target_state="coord_attached",
                receipt_field="coord_attach_receipt_digest", receipt_digest=receipt,
                expected_version=document["version"], now=now,
            )
        return child

    def bind_effects(
        self, attempt_id: str, *, agent_id: str, agent_name: str, now: float
    ) -> dict[str, Any]:
        """Roll forward prepared -> ledger_bound -> coord_bound -> done."""
        document = self.sidecar.read()
        saga = document["bind_sagas"].get(attempt_id)
        if saga is None:
            raise WaitBoostError("unknown bind saga")
        if agent_id != saga["agent_id"] or agent_name != saga["agent_name"]:
            raise WaitBoostError("bind agent identity changed")
        if saga["state"] == "prepared":
            before = self.ledger.read()
            lease = before["leases"].get(saga["lease_id"])
            if lease is None:
                raise WaitBoostError("bind lease disappeared")
            if lease.get("state") == "reserved" and lease.get("host_id") is None:
                after = self.ledger.bind(
                    saga["lease_id"], saga["agent_id"],
                    expected_version=before["version"], agent_name=saga["agent_name"],
                )
                lease = after["leases"][saga["lease_id"]]
            elif (
                lease.get("state") == "running"
                and lease.get("host_id") == saga["agent_id"]
                and lease.get("agent_name") == saga["agent_name"]
            ):
                after = before
            else:
                raise WaitBoostError("ledger bind identity changed")
            receipt = domain_digest(
                "ciel.wait-boost.bind-ledger.v1",
                {"attempt_id": attempt_id, "bind_manifest_digest": saga["bind_manifest_digest"],
                 "lease": lease},
            )
            if after["version"] < saga["ledger_before_version"] + 1:
                raise WaitBoostError("ledger bind version not committed")
            document, saga = self.phases.record_ledger_bound(
                attempt_id, after_version=saga["ledger_before_version"] + 1,
                bound_digest=receipt, expected_version=document["version"], now=now,
            )
        if saga["state"] == "ledger_bound":
            before = self.coordination.read()
            run = before["runs"].get(saga["lease_id"])
            if run is None:
                raise WaitBoostError("bind coordination run disappeared")
            if run.get("state") == "reserved" and run.get("agent_id") is None:
                after, run = self.coordination.bind_run(
                    saga["lease_id"], saga["agent_id"], saga["agent_name"],
                    expected_version=before["version"],
                )
            elif (
                run.get("state") == "bound"
                and run.get("agent_id") == saga["agent_id"]
                and run.get("agent_name") == saga["agent_name"]
            ):
                after = before
            else:
                raise WaitBoostError("coord bind identity changed")
            receipt = domain_digest(
                "ciel.wait-boost.bind-coordination.v1",
                {"attempt_id": attempt_id, "bind_manifest_digest": saga["bind_manifest_digest"],
                 "run": run},
            )
            if after["version"] < saga["coord_before_version"] + 1:
                raise WaitBoostError("coord bind version not committed")
            document, saga = self.phases.record_coord_bound(
                attempt_id, after_version=saga["coord_before_version"] + 1,
                bound_digest=receipt, expected_version=document["version"], now=now,
            )
        if saga["state"] == "coord_bound":
            operation = domain_digest(
                "ciel.wait-boost.bind-operation.v1",
                {name: saga[name] for name in (
                    "attempt_id", "lease_id", "dispatch_name", "agent_id", "agent_name",
                    "ledger_bound_digest", "coord_bound_digest",
                )},
            )
            _, saga = self.phases.finish_bind(
                attempt_id, operation_digest=operation,
                expected_version=document["version"], now=now,
            )
        return saga
