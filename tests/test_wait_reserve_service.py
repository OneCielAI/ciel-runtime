from __future__ import annotations

import copy
import json

try:
    import pytest
except ImportError:  # unittest group runner without pytest installed
    import unittest
    raise unittest.SkipTest("pytest is not installed")

from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_wait_boost import (
    ATTEMPT_FIELDS, COMPLETION_FIELDS, LAUNCH_FIELDS, TERMINAL_FIELDS,
    WaitBoostStore, boost_key,
)
from ciel_runtime_support.work_wait_reserve_service import (
    WaitReserveConflict, WaitReserveService, reserve_request_digest,
)


ATTEMPT = "1" * 32
SCOPE = "scope"
ITEM = "item"


def candidate(**changes):
    value = {
        "item_id": ITEM, "priority": "P2", "created_at": 1.0,
        "approved": True, "focused": False, "dependencies_done": True,
        "plan_version": 7, "usage": 0.1, "role": "worker", "mode": "fresh",
    }
    value.update(changes)
    return value


def consuming(cand, ledger_version=1):
    row = {
        "scope": SCOPE, "item_id": ITEM, "root_id": "root",
        "root_created_at": 1.0, "release_receipt": "b" * 64,
        "release_at": 2.0, "wait_digest": "c" * 64,
        "parked_lease_id": None, "release_store_before_version": 0,
        "state": "consuming", "outcome": None, "consumed_at": None,
        "updated_at": 2.0,
    }
    row.update({name: None for name in ATTEMPT_FIELDS + LAUNCH_FIELDS + COMPLETION_FIELDS + TERMINAL_FIELDS})
    row.update(
        attempt_id=ATTEMPT, attempt_kind="reserve",
        ledger_before_version=ledger_version, lease_id="prospective",
        pre_state="absent", pre_slot=False, pre_lease_digest="0" * 64,
        expected_post_state="reserved", expected_post_slot=True,
        expected_post_role="worker", expected_dispatch_name="work-wait-boost-" + ATTEMPT,
        send_receipt=None, send_state=None, slot_acquired_version=None,
        slot_acquire_digest=None,
        attempt_request_digest=reserve_request_digest(
            scope=SCOPE, item_id=ITEM, attempt_id=ATTEMPT,
            ledger_version=ledger_version, candidate=cand),
    )
    return row


def setup(tmp_path, *, ledger_class=WorkParallelStore, cand=None):
    cand = candidate() if cand is None else cand
    ledger = ledger_class(tmp_path / "work-parallel.json")
    state, _ = ledger.configure({"launches_per_minute": 59}, capability_ready=True, expected_version=0)
    assert state["version"] == 1
    wait = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    wait_state, _ = wait.change(0, lambda d: d["rows"].update(
        {boost_key(SCOPE, ITEM): consuming(cand)}), now=2.0)
    return ledger, wait, wait_state, cand


def call(service, wait_version, cand):
    return service.reserve(
        scope=SCOPE, item_id=ITEM, attempt_id=ATTEMPT, candidate=cand,
        ledger_expected_version=1, wait_expected_version=wait_version, now=3.0,
    )


class CommitThenThrowStore(WorkParallelStore):
    def reserve(self, *args, **kwargs):
        super().reserve(*args, **kwargs)
        raise OSError("response lost after durable replace")


class ThrowBeforeCommitStore(WorkParallelStore):
    def reserve(self, *args, **kwargs):
        raise OSError("response lost before commit")


class CommitAdvanceThenThrowStore(WorkParallelStore):
    def reserve(self, *args, **kwargs):
        super().reserve(*args, **kwargs)
        current = self.read()
        self.configure(
            {"paused": True}, capability_ready=True,
            expected_version=current["version"],
        )
        raise OSError("response lost after later commit")


def test_commit_then_throw_recovers_exact_ledger_reservation_and_persists_receipt(tmp_path):
    ledger, wait, before, cand = setup(tmp_path, ledger_class=CommitThenThrowStore)
    after, proof = call(WaitReserveService(ledger, wait), before["version"], cand)
    ledger_after = ledger.read()
    assert ledger_after["version"] == 2
    assert len(ledger_after["leases"]) == 1
    lease = next(iter(ledger_after["leases"].values()))
    assert proof["launch_lease_id"] == lease["id"]
    assert after["rows"][boost_key(SCOPE, ITEM)]["reserve_receipt_digest"] == proof["reserve_receipt_digest"]


def test_commit_then_later_commit_never_forges_reserve_after_version(tmp_path):
    ledger, wait, before, cand = setup(tmp_path, ledger_class=CommitAdvanceThenThrowStore)
    with pytest.raises(WaitReserveConflict, match="unexpected ledger version"):
        call(WaitReserveService(ledger, wait), before["version"], cand)
    assert ledger.read()["version"] == 3
    assert wait.read()["rows"][boost_key(SCOPE, ITEM)]["launch_state"] is None


def test_changed_candidate_conflicts_before_touching_ledger(tmp_path):
    ledger, wait, before, cand = setup(tmp_path)
    with pytest.raises(WaitReserveConflict, match="persisted reserve request changed"):
        call(WaitReserveService(ledger, wait), before["version"], candidate(plan_version=8))
    assert ledger.read()["version"] == 1
    assert ledger.read()["leases"] == {}


def test_response_loss_with_zero_match_refuses_and_persists_nothing(tmp_path):
    ledger, wait, before, cand = setup(tmp_path, ledger_class=ThrowBeforeCommitStore)
    with pytest.raises(WaitReserveConflict, match="no exact match"):
        call(WaitReserveService(ledger, wait), before["version"], cand)
    assert ledger.read()["leases"] == {}
    assert wait.read()["rows"][boost_key(SCOPE, ITEM)]["launch_state"] is None


def test_response_loss_with_multiple_exact_matches_refuses(tmp_path):
    ledger, wait, before, cand = setup(tmp_path, ledger_class=ThrowBeforeCommitStore)
    base_store = WorkParallelStore(ledger.path)
    state, lease_id = base_store.reserve(
        [cand], scope=SCOPE, expected_version=1, now=3.0,
        dispatch_name="work-wait-boost-" + ATTEMPT)
    document = json.loads(ledger.path.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(document["leases"][lease_id])
    duplicate["id"] = "duplicate-lease"
    document["leases"][duplicate["id"]] = duplicate
    ledger.path.write_text(json.dumps(document), encoding="utf-8")
    # Pin recovery to the pre-dispatch version as a real lost-response retry does.
    with pytest.raises(WaitReserveConflict, match="multiple matching reservations"):
        call(WaitReserveService(ledger, wait), before["version"], cand)
    assert len(ledger.read()["leases"]) == 2
    assert wait.read()["rows"][boost_key(SCOPE, ITEM)]["launch_state"] is None


def test_replay_returns_same_receipt_and_never_creates_duplicate_lease(tmp_path):
    ledger, wait, before, cand = setup(tmp_path)
    service = WaitReserveService(ledger, wait)
    after, first = call(service, before["version"], cand)
    replay_state, second = call(service, after["version"], cand)
    assert second == first
    assert replay_state == after
    assert ledger.read()["version"] == 2
    assert len(ledger.read()["leases"]) == 1


def test_stale_wait_cas_replays_only_same_persisted_receipt(tmp_path):
    ledger, wait, before, cand = setup(tmp_path)
    service = WaitReserveService(ledger, wait)
    after, first = call(service, before["version"], cand)
    replay_state, second = call(service, before["version"], cand)
    assert replay_state == after
    assert second == first
    assert len(ledger.read()["leases"]) == 1
