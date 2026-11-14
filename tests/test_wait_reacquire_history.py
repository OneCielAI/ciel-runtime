from __future__ import annotations

import copy
import pytest
from ciel_runtime_support.work_parallel_wait_boost import (
    WaitBoostStore, boost_key, history_id, validate,
)
from ciel_runtime_support.work_wait_reacquire_history import WaitReacquireHistoryService
from test_wait_boost_sidecar import consuming


def seed(tmp_path):
    store = WaitBoostStore(tmp_path / "wait.json")
    row = consuming(kind="reacquire")
    row.update(send_state="send_pending", slot_acquired_version=2, slot_acquire_digest="a" * 64)
    state, _ = store.change(
        0, lambda document: document["rows"].update({boost_key("scope", "item"): row}), now=2.0
    )
    return store, state, row


def test_reacquire_completion_persists_row_and_history_atomically(tmp_path):
    store, state, row = seed(tmp_path)
    after, result = WaitReacquireHistoryService(store).complete(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        ledger_after_version=2, receipt_digest="e" * 64,
        expected_version=state["version"], now=5.0,
    )
    assert result["row"]["state"] == "consumed"
    assert result["row"]["outcome"] == "reacquired"
    assert result["history"]["transition"] == "complete_consume"
    assert len(after["history"]) == 1
    assert result["history"]["history_id"] == history_id(result["history"])
    validate(after)


def test_reacquire_rollback_restores_boost_and_retains_history(tmp_path):
    store, state, row = seed(tmp_path)
    after, result = WaitReacquireHistoryService(store).rollback(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        rollback_receipt_digest="f" * 64,
        expected_version=state["version"], now=5.0,
    )
    assert result["row"]["state"] == "boosted"
    assert result["row"]["terminal_transition"] == "reacquire_send_failed_rolled_back"
    assert result["row"]["terminal_outcome"] == "delivery_failed_rolled_back"
    assert result["history"]["kind"] == "send_rollback"
    assert result["history"]["history_id"] == history_id(result["history"])
    validate(after)


def test_stale_terminal_retry_and_replay_leave_bytes_unchanged(tmp_path):
    store, state, row = seed(tmp_path)
    service = WaitReacquireHistoryService(store)
    service.complete(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        ledger_after_version=2, receipt_digest="e" * 64,
        expected_version=state["version"], now=5.0,
    )
    raw = store.path.read_bytes()
    with pytest.raises(Exception):
        service.complete(
            scope="scope", item_id="item", attempt_id=row["attempt_id"],
            ledger_after_version=2, receipt_digest="e" * 64,
            expected_version=state["version"], now=5.0,
        )
    assert store.path.read_bytes() == raw


def test_history_id_changes_when_terminal_operation_changes(tmp_path):
    store, state, row = seed(tmp_path)
    service = WaitReacquireHistoryService(store)
    _, first = service.complete(
        scope="scope", item_id="item", attempt_id=row["attempt_id"],
        ledger_after_version=2, receipt_digest="e" * 64,
        expected_version=state["version"], now=5.0,
    )
    altered = copy.deepcopy(first["history"])
    altered["operation_digest"] = "f" * 64
    altered["history_id"] = history_id(altered)
    assert altered["history_id"] != first["history"]["history_id"]
