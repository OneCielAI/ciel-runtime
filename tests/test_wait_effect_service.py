from __future__ import annotations

import copy

import pytest
from ciel_runtime_support.work_parallel_wait_boost import domain_digest
from ciel_runtime_support.work_wait_effect_service import WaitEffectService
from test_wait_boost_sidecar import bind, workspace, H32


class MemoryStore:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.calls = []

    def read(self):
        return copy.deepcopy(self.state)

    def attach_workspace(self, ident, manifest, *, expected_version):
        self.calls.append(("attach", ident))
        assert expected_version == self.state["version"]
        self.state["version"] += 1
        container = self.state.get("leases") or self.state.get("runs")
        container[ident]["workspace"] = copy.deepcopy(manifest)
        row = container[ident]
        return copy.deepcopy(self.state) if "leases" in self.state else (copy.deepcopy(self.state), copy.deepcopy(row))

    def bind(self, ident, agent_id, *, expected_version, agent_name=None):
        self.calls.append(("bind", ident))
        assert expected_version == self.state["version"]
        self.state["version"] += 1
        self.state["leases"][ident].update(host_id=agent_id, agent_name=agent_name, state="running")
        return copy.deepcopy(self.state)

    def bind_run(self, ident, agent_id, agent_name, *, expected_version):
        self.calls.append(("bind_run", ident))
        assert expected_version == self.state["version"]
        self.state["version"] += 1
        self.state["runs"][ident].update(agent_id=agent_id, agent_name=agent_name, state="bound")
        return copy.deepcopy(self.state), copy.deepcopy(self.state["runs"][ident])


class PhaseHarness:
    def __init__(self, child, saga=None):
        self.version = 1
        self.child = copy.deepcopy(child)
        self.saga = copy.deepcopy(saga)
        self.calls = []

    def read(self):
        return {"version": self.version, "workspaces": {H32: copy.deepcopy(self.child)}, "bind_sagas": ({H32: copy.deepcopy(self.saga)} if self.saga else {})}

    def advance_workspace(self, attempt, **kwargs):
        self.calls.append(kwargs["target_state"])
        self.version += 1
        self.child["state"] = kwargs["target_state"]
        self.child[kwargs["receipt_field"]] = kwargs["receipt_digest"]
        return self.read(), copy.deepcopy(self.child)

    def record_ledger_bound(self, attempt, **kwargs):
        self.calls.append("ledger_bound")
        self.version += 1
        self.saga.update(state="ledger_bound", ledger_after_version=kwargs["after_version"], ledger_bound_digest=kwargs["bound_digest"])
        return self.read(), copy.deepcopy(self.saga)

    def record_coord_bound(self, attempt, **kwargs):
        self.calls.append("coord_bound")
        self.version += 1
        self.saga.update(state="coord_bound", coord_after_version=kwargs["after_version"], coord_bound_digest=kwargs["bound_digest"])
        return self.read(), copy.deepcopy(self.saga)

    def finish_bind(self, attempt, **kwargs):
        self.calls.append("done")
        self.version += 1
        self.saga.update(state="done", operation_digest=kwargs["operation_digest"])
        return self.read(), copy.deepcopy(self.saga)


def test_workspace_effects_roll_forward_and_do_not_repeat_committed_effects():
    child = workspace(state="ready")
    manifest = {"path": "p", "repo": "r"}
    child["manifest_digest"] = domain_digest(
        "ciel.wait-boost.workspace-manifest.v1", manifest
    )
    ledger = MemoryStore({"version": 2, "leases": {"lease": {"id": "lease", "workspace": None}}})
    coord = MemoryStore({"version": 3, "runs": {"lease": {"lease_id": "lease", "workspace": None}}})
    phases = PhaseHarness(child)
    service = WaitEffectService(phases, ledger, coord)
    service.phases = phases
    result = service.attach_workspace_effects(H32, manifest, now=4.0)
    assert result["state"] == "coord_attached"
    assert ledger.calls == [("attach", "lease")]
    assert coord.calls == [("attach", "lease")]
    service.attach_workspace_effects(H32, manifest, now=5.0)
    assert ledger.calls == [("attach", "lease")]
    assert coord.calls == [("attach", "lease")]
    with pytest.raises(Exception, match="manifest identity"):
        service.attach_workspace_effects(H32, {"path": "other", "repo": "r"}, now=6.0)


def test_workspace_response_loss_reconciles_without_second_external_call():
    child = workspace(state="ready")
    manifest = {"path": "p", "repo": "r"}
    child["manifest_digest"] = domain_digest(
        "ciel.wait-boost.workspace-manifest.v1", manifest
    )
    ledger = MemoryStore({"version": 3, "leases": {"lease": {"id": "lease", "workspace": manifest}}})
    coord = MemoryStore({"version": 4, "runs": {"lease": {"lease_id": "lease", "workspace": manifest}}})
    phases = PhaseHarness(child)
    service = WaitEffectService(phases, ledger, coord)
    service.phases = phases
    result = service.attach_workspace_effects(H32, manifest, now=7.0)
    assert result["state"] == "coord_attached"
    assert ledger.calls == [] and coord.calls == []


def test_bind_effects_roll_forward_and_do_not_repeat_committed_effects():
    saga = bind(state="prepared")
    child = workspace(state="coord_attached")
    ledger = MemoryStore({"version": 2, "leases": {"lease": {"id": "lease", "state": "reserved"}}})
    coord = MemoryStore({"version": 3, "runs": {"lease": {"lease_id": "lease", "state": "reserved"}}})
    phases = PhaseHarness(child, saga)
    service = WaitEffectService(phases, ledger, coord)
    service.phases = phases
    result = service.bind_effects(H32, agent_id="agent", agent_name="name", now=8.0)
    assert result["state"] == "done"
    assert ledger.calls == [("bind", "lease")]
    assert coord.calls == [("bind_run", "lease")]
    service.bind_effects(H32, agent_id="agent", agent_name="name", now=9.0)
    assert ledger.calls == [("bind", "lease")]
    assert coord.calls == [("bind_run", "lease")]
    with pytest.raises(Exception, match="agent identity"):
        service.bind_effects(H32, agent_id="other", agent_name="name", now=10.0)


def test_bind_response_loss_reconciles_without_second_external_call():
    saga = bind(state="prepared")
    child = workspace(state="coord_attached")
    ledger = MemoryStore({"version": 3, "leases": {"lease": {
        "id": "lease", "state": "running", "host_id": "agent", "agent_name": "name"
    }}})
    coord = MemoryStore({"version": 4, "runs": {"lease": {
        "lease_id": "lease", "state": "bound", "agent_id": "agent", "agent_name": "name"
    }}})
    phases = PhaseHarness(child, saga)
    service = WaitEffectService(phases, ledger, coord)
    service.phases = phases
    result = service.bind_effects(H32, agent_id="agent", agent_name="name", now=8.0)
    assert result["state"] == "done"
    assert ledger.calls == [] and coord.calls == []
