try:
    import pytest
except ImportError:  # unittest group runner without pytest installed
    import unittest
    raise unittest.SkipTest("pytest is not installed")

from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_wait_orchestrator import WaitOrchestrator
from ciel_runtime_support.work_wait_product_adapter import (
    configure_wait_factory, dispatch_wait_event, production_wait_factory,
    project_wait_for_status,
)


class Orchestrator:
    def reserve(self, **kwargs):
        return ({"version": 2}, {"secret": "token"})


RESERVE_ARGS = {
    "scope": "scope", "item_id": "item", "attempt_id": "1" * 32,
    "candidate": {}, "ledger_expected_version": 1,
    "wait_expected_version": 1, "now": 1.0,
}


def test_product_adapter_rejects_missing_args_and_dispatches_redacted_result():
    try:
        with pytest.raises(WorkParallelError):
            dispatch_wait_event(None, None, "scope", {"internal": True}, {})
        configure_wait_factory(
            lambda store, board, identity, host: (Orchestrator(), lambda row, reason: None)
        )
        result = dispatch_wait_event(None, None, "scope", {"internal": True}, {
            "event": "wait_boost_reserve", "wait_args": RESERVE_ARGS,
        })
        assert result["ok"] is True
        assert "secret" not in repr(result)
        with pytest.raises(Exception, match="scope"):
            dispatch_wait_event(None, None, "other-scope", {"internal": True}, {
                "event": "wait_boost_reserve", "wait_args": RESERVE_ARGS,
            })
    finally:
        configure_wait_factory(None)


def test_production_factory_builds_real_backend_beside_ledger(tmp_path):
    store = WorkParallelStore(tmp_path / "work-parallel.json")
    orchestrator, cancel = production_wait_factory(store, None, "scope", {"internal": True})
    assert isinstance(orchestrator, WaitOrchestrator)
    assert orchestrator.sidecar.path.name == "work-parallel-wait-boost-v1.json"
    assert orchestrator.sidecar.path.parent == tmp_path.resolve()
    assert cancel({"item_id": "x"}, "terminal") is None


def test_product_status_uses_shared_projection():
    projected = project_wait_for_status({"eligible": True, "worker_token": "secret"})
    assert projected["eligible"] is True
    assert "secret" not in repr(projected)
