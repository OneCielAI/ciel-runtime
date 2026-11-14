from __future__ import annotations

import copy

import pytest

from ciel_runtime_support.work_assignment_key_coordinator import (
    AssignmentKeyCoordinator,
    AssignmentKeyCoordinatorError,
    P0StoreDependencyReader,
    WaitStoreDependencyReader,
)
from ciel_runtime_support.work_parallel_p0 import WorkParallelP0Store
from ciel_runtime_support.work_parallel_wait_boost import (
    ATTEMPT_FIELDS,
    COMPLETION_FIELDS,
    LAUNCH_FIELDS,
    TERMINAL_FIELDS,
    WaitBoostStore,
    boost_key,
)


def nulls(fields):
    return {name: None for name in fields}


def boosted():
    return {
        "scope": "scope", "item_id": "item", "root_id": "root",
        "root_created_at": 1.0, "release_receipt": "b" * 64,
        "release_at": 2.0, "wait_digest": "c" * 64,
        "parked_lease_id": None, "release_store_before_version": 0,
        "state": "boosted", **nulls(ATTEMPT_FIELDS), **nulls(LAUNCH_FIELDS),
        "outcome": None, "consumed_at": None, **nulls(COMPLETION_FIELDS),
        **nulls(TERMINAL_FIELDS), "updated_at": 2.0,
    }


def consuming():
    row = boosted()
    row.update(
        state="consuming", attempt_id="1" * 32, attempt_kind="reserve",
        ledger_before_version=0, lease_id="lease", pre_state="absent",
        pre_slot=False, pre_lease_digest="0" * 64,
        expected_post_state="reserved", expected_post_slot=True,
        expected_post_role="worker",
        expected_dispatch_name="work-wait-boost-" + "1" * 32,
        send_receipt=None, send_state=None, slot_acquired_version=None,
        slot_acquire_digest=None, attempt_request_digest="d" * 64,
    )
    return row


def coordinator(tmp_path, wait_store):
    ledger = tmp_path / "work-parallel.json"
    p0 = WorkParallelP0Store.beside(ledger)
    return AssignmentKeyCoordinator(
        ledger,
        p0_reader=P0StoreDependencyReader(p0),
        wait_reader=WaitStoreDependencyReader(wait_store),
    )


def seed_boost(store):
    return store.change(
        0,
        lambda document: document["rows"].update(
            {boost_key("scope", "item"): boosted()}
        ),
        now=2.0,
    )[0]


def begin_mutation(document):
    key = boost_key("scope", "item")
    document["rows"][key] = consuming()
    return copy.deepcopy(document["rows"][key])


def test_concrete_wait_creator_publishes_key_and_consuming_in_one_fence(tmp_path):
    store = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    before = seed_boost(store)
    after, row = coordinator(tmp_path, store).create_wait_dependency(
        store, before["version"], begin_mutation,
        target_key=boost_key("scope", "item"), attempt_id="1" * 32, now=3.0
    )
    assert row["state"] == "consuming"
    assert after["rows"][boost_key("scope", "item")]["attempt_id"] == "1" * 32
    assert len((tmp_path / "work-p0-assignment.key").read_text()) == 64


def test_missing_key_with_prior_p0_authority_refuses_wait_mutation(tmp_path):
    ledger = tmp_path / "work-parallel.json"
    p0 = WorkParallelP0Store.beside(ledger)
    state, _ = p0.activate_root(
        "scope", "root", 1.0, set_by="user", expected_version=0, now=1.0
    )
    facts = {
        "scope": "scope", "root_id": "root", "root_created_at": 1.0,
        "item_id": "p0", "continuation": "initial", "predecessor_lease_id": None,
    }
    p0.create_intent(facts, expected_version=state["version"], now=2.0)
    (tmp_path / "work-p0-assignment.key").unlink()
    wait = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    before = seed_boost(wait)
    raw = wait.path.read_bytes()
    with pytest.raises(AssignmentKeyCoordinatorError, match="recovery required"):
        coordinator(tmp_path, wait).create_wait_dependency(
            wait, before["version"], begin_mutation,
            target_key=boost_key("scope", "item"), attempt_id="1" * 32, now=3.0
        )
    assert wait.path.read_bytes() == raw
    assert not (tmp_path / "work-p0-assignment.key").exists()


def test_unrelated_existing_dependency_cannot_mask_noop_target_mutation(tmp_path, monkeypatch):
    store = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    before = seed_boost(store)
    unrelated = consuming()
    unrelated.update(scope="other", item_id="other", release_receipt="f" * 64)
    before, _ = store.change(
        before["version"],
        lambda document: document["rows"].update(
            {boost_key("other", "other"): unrelated}
        ),
        now=2.5,
    )
    key = tmp_path / "work-p0-assignment.key"
    key.write_text("a" * 64, encoding="ascii")
    if __import__("os").name != "nt":
        key.chmod(0o600)
    else:
        from ciel_runtime_support.work_p0_assignment_key import P0AssignmentKeyStore
        monkeypatch.setattr(
            P0AssignmentKeyStore,
            "_validate_windows_acl",
            classmethod(lambda cls, path: None),
        )
    with pytest.raises(AssignmentKeyCoordinatorError, match="exact target"):
        coordinator(tmp_path, store).create_wait_dependency(
            store, before["version"], lambda document: None,
            target_key=boost_key("scope", "item"), attempt_id="1" * 32, now=3.0,
        )


def test_wrong_attempt_is_rejected_before_wait_bytes_change(tmp_path):
    store = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    before = seed_boost(store)
    raw = store.path.read_bytes()
    def wrong(document):
        row = consuming()
        row["attempt_id"] = "2" * 32
        row["expected_dispatch_name"] = "work-wait-boost-" + "2" * 32
        document["rows"][boost_key("scope", "item")] = row
    with pytest.raises(AssignmentKeyCoordinatorError, match="exact target"):
        coordinator(tmp_path, store).create_wait_dependency(
            store, before["version"], wrong,
            target_key=boost_key("scope", "item"), attempt_id="1" * 32, now=3.0,
        )
    assert store.path.read_bytes() == raw


def test_wait_creator_mutation_callback_runs_exactly_once(tmp_path):
    store = WaitBoostStore(tmp_path / "work-parallel-wait-boost-v1.json")
    before = seed_boost(store)
    calls = []
    def mutation(document):
        calls.append(1)
        return begin_mutation(document)
    coordinator(tmp_path, store).create_wait_dependency(
        store, before["version"], mutation,
        target_key=boost_key("scope", "item"), attempt_id="1" * 32, now=3.0
    )
    assert calls == [1]
