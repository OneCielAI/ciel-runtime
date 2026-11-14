from __future__ import annotations

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostError
from ciel_runtime_support.work_wait_orchestrator import WaitOrchestrator


class Stub:
    pass


def service():
    value = object.__new__(WaitOrchestrator)
    value.sidecar = Stub()
    value.ledger = Stub()
    value.coordination = Stub()
    return value


def test_workspace_bind_parent_completion_call_order():
    value = service()
    calls = []
    value.effects = Stub()
    value.effects.attach_workspace_effects = lambda attempt, manifest, now: calls.append("workspace") or {"state": "coord_attached"}
    value.effects.bind_effects = lambda attempt, agent_id, agent_name, now: calls.append("bind") or {"state": "done"}
    value.sidecar.read = lambda: {"version": 7}
    value.parent_completion = Stub()
    value.parent_completion.complete_native_bound = lambda attempt, expected_version, now: calls.append("parent") or {"done": True}
    assert value.workspace_and_bind(
        attempt_id="a", workspace_manifest={"path": "p"},
        agent_id="agent", agent_name="name", now=1.0,
    ) == {"done": True}
    assert calls == ["workspace", "bind", "parent"]


def test_reacquire_results_persist_terminal_or_leave_uncertain():
    for outcome in ("uncertain", "delivered", "rolled_back"):
        value = service()
        value.reacquire_service = Stub()
        proof = {"receipt_digest": "f" * 64}
        value.reacquire_service.classify_send = lambda **kwargs: (outcome, proof)
        value.sidecar.read = lambda: {"version": 3, "rows": {"scope\0item": {
            "slot_acquired_version": 2, "send_receipt": "e" * 64,
        }}}
        value.reacquire_history = Stub()
        terminal = []
        value.reacquire_history.complete = lambda **kwargs: terminal.append("complete") or {"complete": True}
        value.reacquire_history.rollback = lambda **kwargs: terminal.append("rollback") or {"rollback": True}
        result, _ = value.reacquire_result(
            scope="scope", item_id="item", attempt_id="1" * 32,
            delivered=None if outcome == "uncertain" else outcome == "delivered",
            wait_expected_version=2, now=4.0,
        )
        assert result == outcome
        assert terminal == ([] if outcome == "uncertain" else ["complete" if outcome == "delivered" else "rollback"])


def test_cleanup_effects_then_atomic_finalizer():
    value = service()
    calls = []
    value.cleanup_effects = Stub()
    value.cleanup_effects.run = lambda key, now: calls.append("effects") or {"state": "lease_released"}
    value.sidecar.read = lambda: {"version": 8}
    value.cleanup_finalizer = Stub()
    value.cleanup_finalizer.finalize = lambda key, expected_version, now: calls.append("finalize") or {"done": True}
    assert value.cleanup_to_done("cleanup", now=9.0) == {"done": True}
    assert calls == ["effects", "finalize"]


def test_phase_refusal_stops_downstream_effects():
    value = service()
    value.effects = Stub()
    value.effects.attach_workspace_effects = lambda *args, **kwargs: {"state": "ready"}
    value.effects.bind_effects = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not bind"))
    try:
        value.workspace_and_bind(
            attempt_id="a", workspace_manifest={}, agent_id="x", agent_name="n", now=1.0
        )
    except WaitBoostError:
        pass
    else:
        raise AssertionError("expected refusal")
