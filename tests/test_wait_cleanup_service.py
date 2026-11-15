from __future__ import annotations

import copy
try:
    import pytest
except ImportError:  # unittest group runner without pytest installed
    import unittest
    raise unittest.SkipTest("pytest is not installed")

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostError, WaitBoostStore, validate
from ciel_runtime_support.work_wait_cleanup_service import WaitCleanupService
from test_wait_boost_sidecar import cleanup, linked_document


def seed(store):
    document = linked_document(with_bind=False)
    return store.change(
        0,
        lambda value: value.update(copy.deepcopy(document)),
        now=5.0,
    )[0]


def test_cleanup_phases_are_monotonic_and_replayable(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed(store)
    service = WaitCleanupService(store)
    source = cleanup(state="prepared", disposition="not_created", bind_digest=None)
    # Prepare input carries immutable no-bind proof only after the first edge.
    source["bind_disposition"] = None
    source["no_bind_proof_digest"] = None
    source["cleanup_id"] = "0" * 64
    source["cleanup_manifest_digest"] = "0" * 64
    state, row = service.prepare(source, expected_version=state["version"], now=5.0)
    key = row["cleanup_id"]
    delayed, replay = service.prepare(source, expected_version=state["version"], now=99.0)
    assert delayed == state and replay == row
    state, _ = service.bind_disposition(
        key, disposition="not_created", proof={"no_bind_proof_digest": "9" * 64},
        expected_version=state["version"], now=6.0,
    )
    state, _ = service.coord_detached(
        key, before_version=1, receipt_digest="a" * 64,
        expected_version=state["version"], now=7.0,
    )
    prior = state["version"]
    state, _ = service.ledger_detached(
        key, before_version=2, receipt_digest="b" * 64,
        expected_version=prior, now=8.0,
    )
    descendant, replay = service.coord_detached(
        key, before_version=1, receipt_digest="a" * 64,
        expected_version=prior, now=100.0,
    )
    assert descendant == state and replay["state"] == "ledger_detached"
    state, _ = service.workspace_removed(
        key, receipt_digest="c" * 64, expected_version=state["version"], now=9.0
    )
    state, final = service.lease_released(
        key, receipt_digest="d" * 64, expected_version=state["version"], now=10.0
    )
    assert final["state"] == "lease_released"
    validate(state)


def test_changed_cleanup_replay_and_out_of_order_are_byte_safe(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    state = seed(store)
    service = WaitCleanupService(store)
    source = cleanup(state="prepared", disposition="not_created", bind_digest=None)
    source.update(bind_disposition=None, no_bind_proof_digest=None,
                  cleanup_id="0" * 64, cleanup_manifest_digest="0" * 64)
    state, row = service.prepare(source, expected_version=state["version"], now=5.0)
    key = row["cleanup_id"]
    raw = store.path.read_bytes()
    with pytest.raises(WaitBoostError):
        service.workspace_removed(
            key, receipt_digest="c" * 64, expected_version=state["version"], now=6.0
        )
    assert store.path.read_bytes() == raw
    state, _ = service.bind_disposition(
        key, disposition="not_created", proof={"no_bind_proof_digest": "9" * 64},
        expected_version=state["version"], now=6.0,
    )
    with pytest.raises(WaitBoostError, match="replay conflict"):
        service.bind_disposition(
            key, disposition="not_created", proof={"no_bind_proof_digest": "8" * 64},
            expected_version=state["version"], now=7.0,
        )
