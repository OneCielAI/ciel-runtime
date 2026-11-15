from __future__ import annotations

try:
    import pytest
except ImportError:  # unittest group runner without pytest installed
    import unittest
    raise unittest.SkipTest("pytest is not installed")

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostError, WaitBoostStore, validate
from ciel_runtime_support.work_wait_phase_service import WaitPhaseService
from test_wait_boost_sidecar import H32, bind, consuming, workspace


def seed_parent(store):
    return store.change(
        0,
        lambda document: document["rows"].update(
            {"scope\0item": consuming()}
        ),
        now=2.0,
    )[0]


def test_workspace_all_edges_replay_and_changed_receipt_conflict(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed_parent(store)
    service = WaitPhaseService(store)
    row = workspace(state="prepared")
    state, first = service.prepare_workspace(row, expected_version=state["version"], now=3.0)
    state2, replay = service.prepare_workspace(row, expected_version=state["version"], now=3.0)
    assert state2 == state and replay == first
    for expected, target, field, receipt in (
        ("prepared", "ready", "ready_receipt_digest", "a" * 64),
        ("ready", "lease_attached", "lease_attach_receipt_digest", "b" * 64),
        ("lease_attached", "coord_attached", "coord_attach_receipt_digest", "c" * 64),
    ):
        state, result = service.advance_workspace(
            H32, expected_state=expected, target_state=target,
            receipt_field=field, receipt_digest=receipt,
            expected_version=state["version"], now=state["version"] + 4.0,
        )
        prior = state["version"]
        same, replayed = service.advance_workspace(
            H32, expected_state=expected, target_state=target,
            receipt_field=field, receipt_digest=receipt,
            expected_version=prior, now=state["version"] + 4.0,
        )
        assert same == state and replayed == result
        with pytest.raises(WaitBoostError, match="replay conflict"):
            service.advance_workspace(
                H32, expected_state=expected, target_state=target,
                receipt_field=field, receipt_digest="f" * 64,
                expected_version=prior, now=9.0,
            )
    assert state["workspaces"][H32]["state"] == "coord_attached"
    validate(state)


def test_bind_all_edges_replay_and_changed_proof_conflict(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed_parent(store)
    service = WaitPhaseService(store)
    state, _ = service.prepare_workspace(
        workspace(state="prepared"), expected_version=state["version"], now=3.0
    )
    for expected, target, field, receipt in (
        ("prepared", "ready", "ready_receipt_digest", "a" * 64),
        ("ready", "lease_attached", "lease_attach_receipt_digest", "b" * 64),
        ("lease_attached", "coord_attached", "coord_attach_receipt_digest", "c" * 64),
    ):
        state, _ = service.advance_workspace(
            H32, expected_state=expected, target_state=target,
            receipt_field=field, receipt_digest=receipt,
            expected_version=state["version"], now=4.0,
        )
    state, _ = service.prepare_bind(
        bind(state="prepared"), expected_version=state["version"], now=5.0
    )
    state, ledger = service.record_ledger_bound(
        H32, after_version=3, bound_digest="d" * 64,
        expected_version=state["version"], now=6.0,
    )
    same, replay = service.record_ledger_bound(
        H32, after_version=3, bound_digest="d" * 64,
        expected_version=state["version"], now=6.0,
    )
    assert same == state and replay == ledger
    with pytest.raises(WaitBoostError, match="replay conflict"):
        service.record_ledger_bound(
            H32, after_version=3, bound_digest="f" * 64,
            expected_version=state["version"], now=6.0,
        )
    state, _ = service.record_coord_bound(
        H32, after_version=4, bound_digest="e" * 64,
        expected_version=state["version"], now=7.0,
    )
    state, done = service.finish_bind(
        H32, operation_digest="1" * 64,
        expected_version=state["version"], now=8.0,
    )
    same, replay = service.finish_bind(
        H32, operation_digest="1" * 64,
        expected_version=state["version"], now=8.0,
    )
    assert same == state and replay == done
    assert done["state"] == "done" and done["bind_receipt_digest"]
    validate(state)


def test_delayed_prepare_and_descendant_edge_replays(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed_parent(store)
    service = WaitPhaseService(store)
    row = workspace(state="prepared")
    state, prepared = service.prepare_workspace(row, expected_version=state["version"], now=3.0)
    replay_state, replay = service.prepare_workspace(row, expected_version=state["version"], now=99.0)
    assert replay_state == state and replay == prepared
    state, _ = service.advance_workspace(
        H32, expected_state="prepared", target_state="ready",
        receipt_field="ready_receipt_digest", receipt_digest="a" * 64,
        expected_version=state["version"], now=4.0,
    )
    old_version = state["version"]
    state, _ = service.advance_workspace(
        H32, expected_state="ready", target_state="lease_attached",
        receipt_field="lease_attach_receipt_digest", receipt_digest="b" * 64,
        expected_version=old_version, now=5.0,
    )
    descendant, replay = service.advance_workspace(
        H32, expected_state="prepared", target_state="ready",
        receipt_field="ready_receipt_digest", receipt_digest="a" * 64,
        expected_version=old_version, now=100.0,
    )
    assert descendant == state and replay["state"] == "lease_attached"


def test_malformed_request_precedes_stale_version(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed_parent(store)
    service = WaitPhaseService(store)
    with pytest.raises(WaitBoostError, match="invalid workspace edge request"):
        service.advance_workspace(
            H32, expected_state="prepared", target_state="ready",
            receipt_field="ready_receipt_digest", receipt_digest="bad",
            expected_version=state["version"] + 99, now=4.0,
        )


def test_phase_failure_before_persist_keeps_bytes(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed_parent(store)
    service = WaitPhaseService(store)
    state, _ = service.prepare_workspace(
        workspace(state="prepared"), expected_version=state["version"], now=3.0
    )
    raw = store.path.read_bytes()
    with pytest.raises(WaitBoostError):
        service.advance_workspace(
            H32, expected_state="ready", target_state="lease_attached",
            receipt_field="lease_attach_receipt_digest", receipt_digest="b" * 64,
            expected_version=state["version"], now=4.0,
        )
    assert store.path.read_bytes() == raw
