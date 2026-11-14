from __future__ import annotations

from ciel_runtime_support.work_parallel_wait_boost import WaitBoostStore, validate
from ciel_runtime_support.work_wait_cleanup_finalizer import WaitCleanupFinalizer
from test_wait_boost_sidecar import make_cleanup_restore_document


def seed(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    source, row = make_cleanup_restore_document()
    state, _ = store.change(
        0,
        lambda document: document.update(source),
        now=5.0,
    )
    return store, state, row


def test_cleanup_done_and_parent_restore_one_atomic_cas(tmp_path):
    store, state, row = seed(tmp_path)
    after, result = WaitCleanupFinalizer(store).finalize(
        row["cleanup_id"], expected_version=state["version"], now=10.0
    )
    assert result["cleanup"]["state"] == "done"
    assert result["boost"]["state"] == "boosted"
    assert result["boost"]["terminal_outcome"] == "restored_boosted"
    validate(after)


def test_cleanup_finalizer_stale_retry_is_byte_safe(tmp_path):
    store, state, row = seed(tmp_path)
    finalizer = WaitCleanupFinalizer(store)
    finalizer.finalize(row["cleanup_id"], expected_version=state["version"], now=10.0)
    raw = store.path.read_bytes()
    try:
        finalizer.finalize(row["cleanup_id"], expected_version=state["version"], now=10.0)
    except Exception:
        pass
    assert store.path.read_bytes() == raw
