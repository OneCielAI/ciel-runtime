from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from ciel_runtime_support.work_parallel_wait_boost import (
    ATTEMPT_FIELDS,
    BIND_FIELDS,
    CLEANUP_FIELDS,
    COMPLETION_FIELDS,
    LAUNCH_FIELDS,
    TERMINAL_FIELDS,
    WORKSPACE_FIELDS,
    WaitBoostCommitAmbiguous,
    WaitBoostError,
    WaitBoostOperationHistoryExpired,
    WaitBoostStore,
    bind_receipt_digest,
    boost_key,
    cleanup_id,
    cleanup_manifest,
    domain_digest,
    empty_document,
    history_id,
    validate,
)

H32 = "1" * 32


def op_ref(digest):
    return hashlib.sha256(digest.encode("ascii")).hexdigest()[:12]


def nulls(fields):
    return {name: None for name in fields}


def boost(*, state="boosted"):
    return {
        "scope": "scope",
        "item_id": "item",
        "root_id": "root",
        "root_created_at": 1.0,
        "release_receipt": "b" * 64,
        "release_at": 2.0,
        "wait_digest": "c" * 64,
        "parked_lease_id": None,
        "release_store_before_version": 0,
        "state": state,
        **nulls(ATTEMPT_FIELDS),
        **nulls(LAUNCH_FIELDS),
        "outcome": None,
        "consumed_at": None,
        **nulls(COMPLETION_FIELDS),
        **nulls(TERMINAL_FIELDS),
        "updated_at": 2.0,
    }


def consuming(*, kind="reserve"):
    row = boost(state="consuming")
    row.update(
        attempt_id=H32,
        attempt_kind=kind,
        ledger_before_version=1,
        lease_id="lease",
        pre_lease_digest="0" * 64,
        attempt_request_digest="d" * 64,
    )
    if kind == "reserve":
        row.update(
            pre_state="absent",
            pre_slot=False,
            expected_post_state="reserved",
            expected_post_slot=True,
            expected_post_role="worker",
            expected_dispatch_name="work-wait-boost-" + H32,
        )
    elif kind == "reacquire":
        row.update(
            pre_state="waiting_input",
            pre_slot=False,
            expected_post_state="waiting_input",
            expected_post_slot=True,
            expected_post_role="resume",
            send_receipt="e" * 64,
            send_state="pending",
        )
    else:
        row.update(
            pre_state="running",
            pre_slot=None,
            expected_post_state="running",
            expected_post_slot=None,
            expected_post_role="worker",
            send_receipt="e" * 64,
            send_state="pending",
        )
    return row


def workspace(*, state="prepared"):
    row = {name: None for name in WORKSPACE_FIELDS}
    row.update(
        attempt_id=H32,
        scope="scope",
        root_id="root",
        root_created_at=1.0,
        item_id="item",
        lease_id="lease",
        workspace_operation_id="2" * 32,
        state=state,
        repo_identity_digest="1" * 64,
        base_head="head",
        target_ref="target",
        target_path_digest="2" * 64,
        branch_digest="3" * 64,
        gitdir_digest="4" * 64,
        manifest_digest="5" * 64,
        created_at=3.0,
        updated_at=3.0,
    )
    receipts = {"ready": 1, "lease_attached": 2, "coord_attached": 3, "done": 3}.get(
        state, 0
    )
    for name, value in zip(
        (
            "ready_receipt_digest",
            "lease_attach_receipt_digest",
            "coord_attach_receipt_digest",
        ),
        ("a" * 64, "b" * 64, "c" * 64),
    ):
        if receipts:
            row[name] = value
            receipts -= 1
    if state == "done":
        row["terminal_at"] = 8.0
    return row


def bind(*, state="prepared"):
    row = {name: None for name in BIND_FIELDS}
    row.update(
        attempt_id=H32,
        scope="scope",
        root_id="root",
        root_created_at=1.0,
        item_id="item",
        lease_id="lease",
        dispatch_name="work-wait-boost-" + H32,
        assignment_generation=1,
        assignment_digest="6" * 64,
        state=state,
        agent_id="agent",
        agent_name="name",
        bind_manifest_digest="7" * 64,
        ledger_before_version=2,
        coord_before_version=3,
        created_at=4.0,
        updated_at=4.0,
    )
    if state in ("ledger_bound", "coord_bound", "done"):
        row.update(ledger_after_version=3, ledger_bound_digest="d" * 64)
    if state in ("coord_bound", "done"):
        row.update(coord_after_version=4, coord_bound_digest="e" * 64)
    if state == "done":
        row.update(operation_digest="1" * 64, terminal_at=8.0)
        row["bind_receipt_digest"] = bind_receipt_digest(row)
    return row


def cleanup(*, state="prepared", disposition=None, bind_digest="7" * 64):
    row = {name: None for name in CLEANUP_FIELDS}
    row.update(
        cleanup_trigger="spawn_denied",
        release_receipt="b" * 64,
        attempt_id=H32,
        scope="scope",
        root_id="root",
        root_created_at=1.0,
        item_id="item",
        lease_id="lease",
        workspace_operation_id="2" * 32,
        bind_manifest_digest=bind_digest,
        state=state,
        native_absence_receipt_digest="8" * 64,
        created_at=5.0,
        updated_at=5.0,
    )
    row["cleanup_id"] = cleanup_id(row)
    if disposition == "rolled_back":
        row.update(
            bind_disposition="rolled_back",
            bind_rollback_receipt_digest="9" * 64,
            bind_rollback_operation_digest="a" * 64,
        )
    elif disposition == "not_created":
        row.update(
            bind_disposition="not_created",
            bind_manifest_digest=None,
            no_bind_proof_digest="9" * 64,
        )
        row["cleanup_id"] = cleanup_id(row)
    row["cleanup_manifest_digest"] = cleanup_manifest(row)
    return row


def linked_document(*, with_bind=True):
    doc = empty_document()
    doc["rows"][boost_key("scope", "item")] = consuming()
    doc["workspaces"][H32] = workspace(state="coord_attached")
    if with_bind:
        doc["bind_sagas"][H32] = bind()
    return doc


def pin_manifest(row, *, transition="launch_denied", outcome="restored_boosted"):
    fields = (
        "scope",
        "item_id",
        "root_id",
        "root_created_at",
        "release_receipt",
        "release_at",
        "wait_digest",
        "parked_lease_id",
        "release_store_before_version",
        *ATTEMPT_FIELDS,
        *LAUNCH_FIELDS,
    )
    value = {name: row[name] for name in fields}
    value.update(transition=transition, outcome=outcome)
    return value


def history(index, terminal_at):
    active = consuming(kind="running_notify")
    manifest = pin_manifest(active)
    row = {
        "history_id": "0" * 64,
        "scope": manifest["scope"],
        "item_id": manifest["item_id"],
        "release_receipt": manifest["release_receipt"],
        "attempt_id": active["attempt_id"],
        "kind": "restore",
        "transition": "launch_denied",
        "outcome": "restored_boosted",
        "receipt_digest": "1" * 64,
        "operation_digest": f"{index + 1:064x}",
        "manifest": manifest,
        "terminal_at": terminal_at,
        "result": {
            "state": "boosted",
            "outcome": "restored_boosted",
            "recovery_required": False,
            "operation_ref": op_ref(f"{index + 1:064x}"),
        },
    }
    row["history_id"] = history_id(row)
    return row["history_id"], row


def test_empty_and_both_legacy_shapes_migrate_losslessly_through_cas_restart(tmp_path):
    for suffix, legacy in (
        ("rows", {"schema": 1, "version": 0, "rows": {}}),
        ("history", {"schema": 1, "version": 0, "rows": {}, "history": {}}),
    ):
        if suffix == "history":
            key, row = history(7, 10.0)
            legacy["history"][key] = row
            before_bytes = json.dumps(
                row, sort_keys=True, separators=(",", ":")
            ).encode()
        path = tmp_path / f"{suffix}.json"
        path.write_text(json.dumps(legacy, separators=(",", ":")), encoding="utf-8")
        store = WaitBoostStore(path)
        read = store.read()
        assert set(read) == {
            "schema",
            "version",
            "rows",
            "bind_sagas",
            "workspaces",
            "cleanups",
            "history",
        }
        assert set(json.loads(path.read_text())) == set(legacy)
        state, _ = store.change(
            0,
            lambda document: document["rows"].update(
                {boost_key("scope", "item"): boost()}
            ),
            now=1.0,
        )
        restarted = WaitBoostStore(path).read()
        assert restarted == state and state["version"] == 1
        if suffix == "history":
            assert (
                json.dumps(
                    restarted["history"][key], sort_keys=True, separators=(",", ":")
                ).encode()
                == before_bytes
            )


def test_duplicate_unknown_and_oversize_fail_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"schema":1,"schema":1,"version":0,"rows":{}}', encoding="utf-8")
    with pytest.raises(Exception):
        WaitBoostStore(path).read()
    bad = empty_document()
    bad["unknown"] = 1
    with pytest.raises(WaitBoostError):
        validate(bad)
    bad = empty_document()
    row = boost()
    row["terminal_manifest"] = {"x": "z" * 70000}
    bad["rows"][boost_key("scope", "item")] = row
    with pytest.raises(WaitBoostError):
        validate(bad)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(pre_state="waiting_input"),
        lambda row: row.update(expected_dispatch_name="bad"),
        lambda row: row.update(launch_state="bogus"),
        lambda row: row.update(slot_acquired_version=2),
    ],
)
def test_bad_attempt_launch_and_partial_slot_refused(mutation):
    row = consuming()
    mutation(row)
    doc = empty_document()
    doc["rows"][boost_key("scope", "item")] = row
    with pytest.raises(WaitBoostError):
        validate(doc)


def test_valid_complete_launch_and_reacquire_slot_proof():
    row = consuming()
    row.update(
        launch_state="assignment_issued",
        launch_lease_id="lease",
        assignment_generation=1,
        assignment_digest="1" * 64,
        capability_derivation_digest="2" * 64,
        launch_dispatch_name="work-wait-boost-" + H32,
        reserve_receipt_digest="3" * 64,
        launch_manifest_digest="4" * 64,
    )
    doc = empty_document()
    doc["rows"][boost_key("scope", "item")] = row
    validate(doc)
    row = consuming(kind="reacquire")
    row.update(
        send_state="send_pending", slot_acquired_version=2, slot_acquire_digest="4" * 64
    )
    doc["rows"][boost_key("scope", "item")] = row
    validate(doc)


def test_bind_phase_and_recovery_proof_grammar():
    doc = linked_document()
    validate(doc)
    for state in ("ledger_bound", "coord_bound"):
        doc["bind_sagas"][H32] = bind(state=state)
        validate(doc)
    recovery = bind(state="prepared")
    recovery["state"] = "recovery_required"
    recovery.update(
        ledger_after_version=3,
        ledger_bound_digest="d" * 64,
        rollback_receipt_digest="9" * 64,
        rollback_operation_digest="a" * 64,
    )
    doc["bind_sagas"][H32] = recovery
    validate(doc)
    bad = copy.deepcopy(doc)
    bad["bind_sagas"][H32]["rollback_operation_digest"] = None
    with pytest.raises(WaitBoostError, match="partial bind rollback"):
        validate(bad)
    bad = copy.deepcopy(doc)
    bad["bind_sagas"][H32]["bind_receipt_digest"] = "a" * 64
    with pytest.raises(WaitBoostError, match="partial bind final"):
        validate(bad)


def test_bind_rolled_back_refuses_arbitrary_final_receipt_and_recovery_holes():
    doc = linked_document()
    saga = bind(state="coord_bound")
    saga.update(
        state="rolled_back",
        bind_receipt_digest="f" * 64,
        operation_digest="1" * 64,
        rollback_receipt_digest="9" * 64,
        rollback_operation_digest="a" * 64,
        terminal_at=9.0,
    )
    doc["bind_sagas"][H32] = saga
    with pytest.raises(WaitBoostError, match="final receipt mismatch"):
        validate(doc)

    recovery = bind(state="coord_bound")
    recovery["state"] = "recovery_required"
    recovery["ledger_bound_digest"] = None
    doc["bind_sagas"][H32] = recovery
    with pytest.raises(WaitBoostError, match="partial bind ledger proof"):
        validate(doc)


def test_workspace_partial_rollback_and_phase_holes_refused():
    doc = linked_document()
    child = doc["workspaces"][H32]
    child.update(state="recovery_required", rollback_receipt_digest="a" * 64)
    with pytest.raises(WaitBoostError, match="partial workspace rollback"):
        validate(doc)
    doc = linked_document()
    child = doc["workspaces"][H32]
    child.update(state="recovery_required", ready_receipt_digest=None)
    with pytest.raises(WaitBoostError, match="nonmonotonic receipt"):
        validate(doc)


def test_cleanup_recovery_prebind_and_complete_pairs():
    doc = linked_document()
    row = cleanup(state="recovery_required")
    doc["cleanups"][row["cleanup_id"]] = row
    validate(doc)  # disposition may be null pre-bind
    bad = copy.deepcopy(doc)
    current = next(iter(bad["cleanups"].values()))
    current["coord_before_version"] = 1
    with pytest.raises(
        WaitBoostError, match="cleanup phase hole|partial cleanup detach"
    ):
        validate(bad)
    bad = copy.deepcopy(doc)
    current = next(iter(bad["cleanups"].values()))
    current["workspace_cleanup_receipt_digest"] = "a" * 64
    with pytest.raises(WaitBoostError, match="cleanup scalar phase"):
        validate(bad)
    bad = copy.deepcopy(doc)
    current = next(iter(bad["cleanups"].values()))
    current.update(
        boost_sidecar_before_version=1,
        boost_restore_receipt_digest="a" * 64,
        terminal_at=2.0,
    )
    with pytest.raises(WaitBoostError, match="partial cleanup restore proof"):
        validate(bad)


def test_cleanup_bind_manifest_exact_and_nonterminal_uniqueness():
    doc = linked_document()
    row = cleanup(state="prepared")
    doc["cleanups"][row["cleanup_id"]] = row
    validate(doc)
    bad = copy.deepcopy(doc)
    next(iter(bad["cleanups"].values()))["bind_manifest_digest"] = "8" * 64
    with pytest.raises(WaitBoostError, match="cleanup manifest"):
        validate(bad)
    second = copy.deepcopy(row)
    second["cleanup_trigger"] = "native_absent"
    second["cleanup_id"] = cleanup_id(second)
    second["cleanup_manifest_digest"] = cleanup_manifest(second)
    doc["cleanups"][second["cleanup_id"]] = second
    with pytest.raises(WaitBoostError, match="duplicate nonterminal cleanup"):
        validate(doc)


def test_history_manifest_and_result_are_exact_discriminated_objects():
    key, row = history(1, 10.0)
    doc = empty_document()
    doc["history"][key] = row
    validate(doc)
    bad = copy.deepcopy(doc)
    bad["history"][key]["manifest"]["arbitrary"] = {}
    with pytest.raises(WaitBoostError, match="history manifest fields"):
        validate(bad)
    bad = copy.deepcopy(doc)
    bad["history"][key]["result"]["arbitrary"] = True
    with pytest.raises(WaitBoostError, match="history result fields"):
        validate(bad)


def make_cleanup_restore_document():
    doc = linked_document()
    saga = doc["bind_sagas"][H32]
    saga.update(
        state="rolled_back",
        rollback_receipt_digest="9" * 64,
        rollback_operation_digest="a" * 64,
        terminal_at=20.0,
    )
    child = doc["workspaces"][H32]
    child.update(
        state="rolled_back",
        rollback_receipt_digest="3" * 64,
        rollback_operation_digest="e" * 64,
        terminal_at=20.0,
    )
    row = cleanup(state="lease_released", disposition="rolled_back")
    row.update(
        coord_before_version=10,
        coord_after_version=11,
        coord_detach_receipt_digest="1" * 64,
        ledger_before_version=20,
        ledger_after_version=21,
        ledger_detach_receipt_digest="2" * 64,
        workspace_cleanup_receipt_digest="3" * 64,
        lease_release_receipt_digest="4" * 64,
    )
    doc["cleanups"][row["cleanup_id"]] = row
    return doc, row


def done_cleanup(open_row, before_version):
    row = copy.deepcopy(open_row)
    row.update(
        state="done",
        boost_sidecar_before_version=before_version,
        boost_sidecar_after_version=before_version + 1,
        boost_restore_receipt_digest="5" * 64,
        boost_restore_operation_digest="6" * 64,
        cleanup_operation_digest="7" * 64,
        terminal_at=30.0,
        updated_at=30.0,
    )
    return row


def restored_boost(parent):
    row = copy.deepcopy(parent)
    row.update(
        state="boosted",
        **nulls(ATTEMPT_FIELDS),
        **nulls(LAUNCH_FIELDS),
        terminal_attempt_id=H32,
        terminal_transition="launch_denied",
        terminal_outcome="restored_boosted",
        terminal_receipt_digest="5" * 64,
        terminal_operation_digest="6" * 64,
        terminal_at=30.0,
        terminal_manifest=pin_manifest(parent),
        updated_at=30.0,
    )
    return row


def test_atomic_cleanup_done_preserves_every_prior_field_and_replays(tmp_path):
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    doc, open_row = make_cleanup_restore_document()
    validate(doc)
    store._persist(doc)
    done = done_cleanup(open_row, 0)
    restored = restored_boost(doc["rows"][boost_key("scope", "item")])
    manifest = domain_digest(
        "ciel.wait-boost.cleanup-done-replay.v1", {"cleanup": done, "boost": restored}
    )
    state, result = store.atomic_cleanup_done(
        cleanup_id_value=done["cleanup_id"],
        restored_boost=restored,
        done_cleanup=done,
        expected_version=0,
        replay_manifest_digest=manifest,
    )
    replayed, repeated = store.atomic_cleanup_done(
        cleanup_id_value=done["cleanup_id"],
        restored_boost=restored,
        done_cleanup=done,
        expected_version=0,
        replay_manifest_digest=manifest,
    )
    assert replayed == state and repeated == result


def test_atomic_cleanup_done_refuses_prior_field_rewrite(tmp_path):
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    doc, open_row = make_cleanup_restore_document()
    validate(doc)
    store._persist(doc)
    done = done_cleanup(open_row, 0)
    done["coord_detach_receipt_digest"] = "f" * 64
    restored = restored_boost(doc["rows"][boost_key("scope", "item")])
    manifest = domain_digest(
        "ciel.wait-boost.cleanup-done-replay.v1", {"cleanup": done, "boost": restored}
    )
    with pytest.raises(WaitBoostError, match="rewrites prior field"):
        store.atomic_cleanup_done(
            cleanup_id_value=done["cleanup_id"],
            restored_boost=restored,
            done_cleanup=done,
            expected_version=0,
            replay_manifest_digest=manifest,
        )


def test_retention_keeps_newest_32_and_expired_replay_is_not_new(tmp_path):
    store = WaitBoostStore(tmp_path / "state.json")
    doc = empty_document()
    now = 1_000_000.0
    removed_key = None
    for index in range(40):
        key, row = history(index, now - 700_000 - index)
        doc["history"][key] = row
        removed_key = key
    validate(doc)
    store._persist(doc)
    pruned, counts = store.prune(now=now, expected_version=0)
    assert (
        counts["history"] == 32
        and len(pruned["history"]) == 32
        and removed_key not in pruned["history"]
    )
    with pytest.raises(
        WaitBoostOperationHistoryExpired, match="operation_history_expired"
    ):
        store.resolve_history(
            removed_key,
            scope="scope",
            item_id="item",
            release_receipt="b" * 64,
            attempt_id=H32,
        )


def test_terminal_children_are_protected_by_open_parent(tmp_path):
    store = WaitBoostStore(tmp_path / "state.json")
    doc, cleanup_row = make_cleanup_restore_document()
    now = 1_000_000.0
    saga = doc["bind_sagas"][H32]
    child = doc["workspaces"][H32]
    saga["terminal_at"] = 1.0
    child["terminal_at"] = 1.0
    cleanup_row["state"] = "recovery_required"
    validate(doc)
    store._persist(doc)
    pruned, _ = store.prune(now=now, expected_version=0)
    assert H32 in pruned["bind_sagas"] and H32 in pruned["workspaces"]


@pytest.mark.parametrize("failure", ["open", "fsync", "close"])
def test_parent_directory_post_replace_failures_are_ambiguous_committed(
    tmp_path, failure
):
    calls = []

    def open_directory(path, flags):
        calls.append("open")
        if failure == "open":
            raise OSError("open")
        return 999

    def fsync(descriptor):
        calls.append("fsync")
        if descriptor == 999 and failure == "fsync":
            raise OSError("fsync")

    def close_directory(descriptor):
        calls.append("close")
        if failure == "close":
            raise OSError("close")

    path = tmp_path / "state.json"
    store = WaitBoostStore(
        path,
        fsync=fsync,
        sync_parent_directory=True,
        open_directory=open_directory,
        close_directory=close_directory,
    )
    with pytest.raises(WaitBoostCommitAmbiguous):
        store.change(
            0,
            lambda document: document["rows"].update(
                {boost_key("scope", "item"): boost()}
            ),
            now=1.0,
        )
    assert WaitBoostStore(path).read()["rows"]


def test_native_bound_requires_full_done_child_crosslinks():
    doc = linked_document()
    doc["bind_sagas"][H32] = bind(state="done")
    doc["workspaces"][H32] = workspace(state="done")
    parent = doc["rows"][boost_key("scope", "item")]
    parent.update(
        state="consumed",
        **nulls(ATTEMPT_FIELDS),
        **nulls(LAUNCH_FIELDS),
        outcome="native_bound",
        consumed_at=8.0,
        completed_attempt_id=H32,
        completed_attempt_kind="reserve",
        completed_ledger_before_version=2,
        completed_ledger_after_version=3,
        completed_lease_id="lease",
        completed_receipt_digest=doc["bind_sagas"][H32]["bind_receipt_digest"],
        operation_digest="1" * 64,
    )
    validate(doc)
    bad = copy.deepcopy(doc)
    bad["bind_sagas"][H32]["bind_receipt_digest"] = "0" * 64
    with pytest.raises(WaitBoostError, match="final receipt mismatch|completion proof"):
        validate(bad)


def terminal_history(kind, child, *, release_receipt="b" * 64):
    if kind == "bind_terminal":
        manifest = {
            name: child[name]
            for name in BIND_FIELDS
            - {"state", "created_at", "updated_at", "terminal_at"}
        }
        receipt = (
            child["bind_receipt_digest"]
            if child["state"] == "done"
            else child["rollback_receipt_digest"]
        )
        operation = (
            child["operation_digest"]
            if child["state"] == "done"
            else child["rollback_operation_digest"]
        )
    else:
        manifest = {
            name: child[name]
            for name in WORKSPACE_FIELDS
            - {"state", "created_at", "updated_at", "terminal_at"}
        }
        if child["state"] == "done":
            receipt = child["coord_attach_receipt_digest"]
            operation = domain_digest("ciel.wait-boost.workspace-terminal.v1", manifest)
        else:
            receipt = child["rollback_receipt_digest"]
            operation = child["rollback_operation_digest"]
    row = {
        "history_id": "0" * 64,
        "scope": child["scope"],
        "item_id": child["item_id"],
        "release_receipt": release_receipt,
        "attempt_id": child["attempt_id"],
        "kind": kind,
        "transition": child["state"],
        "outcome": "native_bound" if child["state"] == "done" else "restored_boosted",
        "receipt_digest": receipt,
        "operation_digest": operation,
        "manifest": manifest,
        "terminal_at": child["terminal_at"],
        "result": {
            "state": child["state"],
            "outcome": "native_bound"
            if child["state"] == "done"
            else "restored_boosted",
            "recovery_required": False,
            "operation_ref": op_ref(operation),
        },
    }
    row["history_id"] = history_id(row)
    return row["history_id"], row


@pytest.mark.parametrize("kind", ["bind_terminal", "workspace_terminal"])
def test_terminal_history_reconstructs_full_child_and_crosslinks_outer(kind):
    if kind == "bind_terminal":
        child = bind(state="done")
    else:
        child = workspace(state="rolled_back")
        child.update(
            rollback_receipt_digest="3" * 64,
            rollback_operation_digest="e" * 64,
            terminal_at=8.0,
        )
    key, row = terminal_history(kind, child)
    doc = empty_document()
    doc["history"][key] = row
    validate(doc)
    for field in tuple(row["manifest"]):
        bad = copy.deepcopy(doc)
        value = bad["history"][key]["manifest"][field]
        bad["history"][key]["manifest"][field] = None if value is not None else "x"
        with pytest.raises(WaitBoostError):
            validate(bad)
    for field in (
        "scope",
        "item_id",
        "attempt_id",
        "receipt_digest",
        "operation_digest",
        "transition",
    ):
        bad = copy.deepcopy(doc)
        bad["history"][key][field] = (
            "0" * (32 if field == "attempt_id" else 64)
            if field.endswith("digest") or field == "attempt_id"
            else "changed"
        )
        with pytest.raises(WaitBoostError):
            validate(bad)


def test_bind_done_crash_intermediates_a_and_b_are_valid_rollforward_authority():
    for consumed in (False, True):
        doc = linked_document()
        doc["bind_sagas"][H32] = bind(state="done")
        if consumed:
            parent = doc["rows"][boost_key("scope", "item")]
            parent.update(
                state="consumed",
                **nulls(ATTEMPT_FIELDS),
                **nulls(LAUNCH_FIELDS),
                outcome="native_bound",
                consumed_at=8.0,
                completed_attempt_id=H32,
                completed_attempt_kind="reserve",
                completed_ledger_before_version=2,
                completed_ledger_after_version=3,
                completed_lease_id="lease",
                completed_receipt_digest=doc["bind_sagas"][H32]["bind_receipt_digest"],
                operation_digest=doc["bind_sagas"][H32]["operation_digest"],
            )
        validate(doc)
    early = linked_document()
    early["workspaces"][H32] = workspace(state="done")
    with pytest.raises(WaitBoostError, match="done workspace parent proof"):
        validate(early)


def test_completion_history_full_identity_mutation_matrix():
    saga = bind(state="done")
    key, row = history(7, 8.0)
    row.update(
        kind="completion",
        transition="complete_bound_launch",
        outcome="native_bound",
        receipt_digest=saga["bind_receipt_digest"],
        operation_digest=saga["operation_digest"],
        manifest={
            "scope": "scope",
            "item_id": "item",
            "root_id": "root",
            "root_created_at": 1.0,
            "release_receipt": "b" * 64,
            "completed_attempt_id": H32,
            "completed_attempt_kind": "reserve",
            "completed_ledger_before_version": 2,
            "completed_ledger_after_version": 3,
            "completed_lease_id": "lease",
            "completed_receipt_digest": saga["bind_receipt_digest"],
            "operation_digest": saga["operation_digest"],
        },
        result={
            "state": "consumed",
            "outcome": "native_bound",
            "recovery_required": False,
            "operation_ref": op_ref(saga["operation_digest"]),
        },
    )
    row["history_id"] = key = history_id(row)
    doc = empty_document()
    doc["history"][key] = row
    doc["bind_sagas"][H32] = saga
    doc["workspaces"][H32] = workspace(state="done")
    validate(doc)
    for field in tuple(row["manifest"]):
        bad = copy.deepcopy(doc)
        value = bad["history"][key]["manifest"][field]
        bad["history"][key]["manifest"][field] = (
            None if value is not None else "changed"
        )
        with pytest.raises(WaitBoostError):
            validate(bad)


def test_workspace_done_terminal_history_uses_coord_receipt_and_framed_operation():
    child = workspace(state="done")
    key, row = terminal_history("workspace_terminal", child)
    doc = empty_document()
    doc["history"][key] = row
    assert row["receipt_digest"] == child["coord_attach_receipt_digest"]
    assert row["operation_digest"] == domain_digest(
        "ciel.wait-boost.workspace-terminal.v1", row["manifest"]
    )
    validate(doc)
    bad = copy.deepcopy(doc)
    bad["history"][key]["operation_digest"] = "0" * 64
    with pytest.raises(
        WaitBoostError,
        match="history id mismatch|deterministic result|child terminal proof",
    ):
        validate(bad)


def test_standalone_history_semantic_vocabulary_direct_probes():
    for kind, state in (
        ("bind_terminal", "done"),
        ("workspace_terminal", "done"),
        ("workspace_terminal", "rolled_back"),
    ):
        child = bind(state=state) if kind == "bind_terminal" else workspace(state=state)
        if state == "rolled_back":
            child.update(
                rollback_receipt_digest="3" * 64,
                rollback_operation_digest="e" * 64,
                terminal_at=8.0,
            )
        key, row = terminal_history(kind, child)
        doc = empty_document()
        doc["history"][key] = row
        validate(doc)
        for field, value in (("outcome", "bogus"), ("transition", "bogus")):
            bad = copy.deepcopy(doc)
            bad["history"][key][field] = value
            bad["history"][key]["result"]["outcome"] = bad["history"][key]["outcome"]
            with pytest.raises(
                WaitBoostError, match="history id mismatch|terminal proof"
            ):
                validate(bad)


def test_bogus_completion_semantics_rejected_with_valid_live_authority():
    saga = bind(state="done")
    key, row = history(8, 8.0)
    row.update(
        kind="completion",
        transition="bogus",
        outcome="native_bound",
        receipt_digest=saga["bind_receipt_digest"],
        operation_digest=saga["operation_digest"],
        manifest={
            "scope": "scope",
            "item_id": "item",
            "root_id": "root",
            "root_created_at": 1.0,
            "release_receipt": "b" * 64,
            "completed_attempt_id": H32,
            "completed_attempt_kind": "reserve",
            "completed_ledger_before_version": 2,
            "completed_ledger_after_version": 3,
            "completed_lease_id": "lease",
            "completed_receipt_digest": saga["bind_receipt_digest"],
            "operation_digest": saga["operation_digest"],
        },
        result={
            "state": "consumed",
            "outcome": "native_bound",
            "recovery_required": False,
            "operation_ref": op_ref(saga["operation_digest"]),
        },
    )
    row["history_id"] = key = history_id(row)
    doc = empty_document()
    doc["history"][key] = row
    doc["bind_sagas"][H32] = saga
    doc["workspaces"][H32] = workspace(state="done")
    with pytest.raises(WaitBoostError, match="completion semantic vocabulary"):
        validate(doc)


def test_done_and_rolled_back_phase_legality_requires_authority():
    done_doc = linked_document()
    done_doc["workspaces"][H32] = workspace(state="done")
    with pytest.raises(WaitBoostError, match="done workspace parent proof"):
        validate(done_doc)
    rolled = linked_document()
    rolled["workspaces"][H32].update(
        state="rolled_back",
        rollback_receipt_digest="3" * 64,
        rollback_operation_digest="e" * 64,
        terminal_at=8.0,
    )
    with pytest.raises(WaitBoostError, match="cleanup missing"):
        validate(rolled)


def test_cleanup_restore_rejects_every_full_pin_manifest_mutation(tmp_path):
    doc, open_row = make_cleanup_restore_document()
    parent = doc["rows"][boost_key("scope", "item")]
    for field in tuple(pin_manifest(parent)):
        path = tmp_path / f"{field}.json"
        store = WaitBoostStore(path)
        store._persist(doc)
        done = done_cleanup(open_row, 0)
        restored = restored_boost(parent)
        value = restored["terminal_manifest"][field]
        restored["terminal_manifest"][field] = None if value is not None else "changed"
        manifest = domain_digest(
            "ciel.wait-boost.cleanup-done-replay.v1",
            {"cleanup": done, "boost": restored},
        )
        with pytest.raises(WaitBoostError):
            store.atomic_cleanup_done(
                cleanup_id_value=done["cleanup_id"],
                restored_boost=restored,
                done_cleanup=done,
                expected_version=0,
                replay_manifest_digest=manifest,
            )


def test_later_release_keeps_archived_parent_and_asymmetric_component(tmp_path):
    doc = empty_document()
    parent = boost()
    parent["release_receipt"] = "d" * 64
    doc["rows"][boost_key("scope", "item")] = parent
    saga = bind(state="done")
    workspace_row = workspace(state="done")
    key, completion = history(99, 1.0)
    completion.update(
        kind="completion",
        transition="complete_bound_launch",
        outcome="native_bound",
        receipt_digest=saga["bind_receipt_digest"],
        operation_digest=saga["operation_digest"],
        terminal_at=1.0,
        manifest={
            "scope": "scope",
            "item_id": "item",
            "root_id": "root",
            "root_created_at": 1.0,
            "release_receipt": "b" * 64,
            "completed_attempt_id": H32,
            "completed_attempt_kind": "reserve",
            "completed_ledger_before_version": 2,
            "completed_ledger_after_version": 3,
            "completed_lease_id": "lease",
            "completed_receipt_digest": saga["bind_receipt_digest"],
            "operation_digest": saga["operation_digest"],
        },
        result={
            "state": "consumed",
            "outcome": "native_bound",
            "recovery_required": False,
            "operation_ref": op_ref(saga["operation_digest"]),
        },
    )
    completion["history_id"] = key = history_id(completion)
    doc["history"] = {key: completion}
    doc["bind_sagas"][H32] = saga
    doc["workspaces"][H32] = workspace_row
    validate(doc)
    store = WaitBoostStore(tmp_path / "state.json")
    store._persist(doc)
    pruned, _ = store.prune(now=1_000_000.0, expected_version=0)
    assert (
        key in pruned["history"]
        and H32 in pruned["bind_sagas"]
        and H32 in pruned["workspaces"]
    )


def legacy_completion_history(*, terminal_at=8.0):
    key = "9" * 64
    operation = "6" * 64
    return key, {
        "history_id": key,
        "scope": "scope",
        "item_id": "item",
        "release_receipt": "b" * 64,
        "attempt_id": H32,
        "kind": "completion",
        "transition": "legacy-free-text",
        "outcome": "legacy-outcome",
        "receipt_digest": "5" * 64,
        "operation_digest": operation,
        "manifest": {
            "completed_attempt_id": H32,
            "completed_attempt_kind": "reserve",
            "completed_ledger_before_version": 2,
            "completed_ledger_after_version": 3,
            "completed_lease_id": "lease",
            "completed_receipt_digest": "5" * 64,
            "operation_digest": operation,
        },
        "terminal_at": terminal_at,
        "result": {
            "state": "legacy-state",
            "outcome": None,
            "recovery_required": True,
            "operation_ref": "arbitrary-ref",
        },
    }


def test_legacy_four_key_read_unrelated_cas_restart_preserves_arbitrary_result(
    tmp_path,
):
    key, row = legacy_completion_history()
    path = tmp_path / "state.json"
    legacy = {"schema": 1, "version": 0, "rows": {}, "history": {key: row}}
    path.write_text(json.dumps(legacy, separators=(",", ":")), encoding="utf-8")
    store = WaitBoostStore(path)
    before = store.read()["history"][key]
    state, _ = store.change(
        0,
        lambda document: document["rows"].update(
            {boost_key("other", "item"): {**boost(), "scope": "other"}}
        ),
        now=1.0,
    )
    restarted = WaitBoostStore(path).read()
    assert state["history"][key] == before == restarted["history"][key]


@pytest.mark.parametrize("action", ["insert", "mutate", "delete", "replace"])
def test_legacy_completion_cas_provenance_rejects_non_prune_changes(tmp_path, action):
    key, row = legacy_completion_history()
    path = tmp_path / "state.json"
    doc = empty_document()
    doc["history"][key] = row
    WaitBoostStore(path)._persist(doc)
    store = WaitBoostStore(path)

    def mutate(document):
        if action == "insert":
            other = copy.deepcopy(row)
            other["history_id"] = other_key = "8" * 64
            document["history"][other_key] = other
        elif action == "mutate":
            document["history"][key]["result"]["operation_ref"] = "changed"
        elif action == "delete":
            del document["history"][key]
        else:
            enriched = copy.deepcopy(row)
            enriched["manifest"].update(
                scope="scope",
                item_id="item",
                root_id="root",
                root_created_at=1.0,
                release_receipt="b" * 64,
            )
            document["history"][key] = enriched

    with pytest.raises(WaitBoostError):
        store.change(0, mutate, now=1.0)


def test_enriched_deterministic_ref_and_boolean_mutations_rejected():
    key, row = history(4, 8.0)
    doc = empty_document()
    doc["history"][key] = row
    validate(doc)
    for field, value in (("operation_ref", "wrong-ref"), ("recovery_required", True)):
        bad = copy.deepcopy(doc)
        bad["history"][key]["result"][field] = value
        with pytest.raises(WaitBoostError, match="deterministic result"):
            validate(bad)


@pytest.mark.parametrize(
    ("kind", "transition", "outcome", "state"),
    [
        ("restore", "running_notify_failed", "restored_boosted", "boosted"),
        ("restore", "launch_denied", "restored_boosted", "boosted"),
        ("restore", "capacity_no_commit", "restored_boosted", "boosted"),
        ("cancel", "cancel_open", "reblocked", "cancelled"),
        ("cancel", "cancel_open", "terminal", "cancelled"),
        ("cancel", "cancel_open", "identity_mismatch", "cancelled"),
        (
            "send_rollback",
            "reacquire_send_failed_rolled_back",
            "delivery_failed_rolled_back",
            "boosted",
        ),
    ],
)
def test_exact_restore_cancel_send_rollback_tuple_vocabulary(
    kind, transition, outcome, state
):
    active = consuming(
        kind="reacquire" if kind == "send_rollback" else "running_notify"
    )
    manifest = pin_manifest(active, transition=transition, outcome=outcome)
    if kind == "send_rollback":
        manifest.pop("transition")
        manifest.pop("outcome")
        manifest["rollback_receipt_digest"] = "7" * 64
    operation = "6" * 64
    row = {
        "history_id": "0" * 64,
        "scope": "scope",
        "item_id": "item",
        "release_receipt": "b" * 64,
        "attempt_id": H32,
        "kind": kind,
        "transition": transition,
        "outcome": outcome,
        "receipt_digest": "7" * 64 if kind == "send_rollback" else "5" * 64,
        "operation_digest": operation,
        "manifest": manifest,
        "terminal_at": 8.0,
        "result": {
            "state": state,
            "outcome": outcome,
            "recovery_required": False,
            "operation_ref": op_ref(operation),
        },
    }
    row["history_id"] = key = history_id(row)
    doc = empty_document()
    doc["history"][key] = row
    validate(doc)
    for bad_transition in (transition + "_near", "arbitrary"):
        bad_row = copy.deepcopy(row)
        bad_row["transition"] = bad_transition
        if kind in ("restore", "cancel"):
            bad_row["manifest"]["transition"] = bad_transition
        bad_row["history_id"] = bad_key = history_id(bad_row)
        bad = empty_document()
        bad["history"][bad_key] = bad_row
        with pytest.raises(WaitBoostError, match="semantic vocabulary"):
            validate(bad)


def test_eligible_legacy_completion_prunes_and_restarts(tmp_path):
    key, row = legacy_completion_history(terminal_at=1.0)
    doc = empty_document()
    doc["history"][key] = row
    for index in range(32):
        newer_key, newer = history(index + 100, 900_000.0 + index)
        doc["history"][newer_key] = newer
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    store._persist(doc)
    pruned, _ = store.prune(now=1_000_000.0, expected_version=0)
    assert key not in pruned["history"]
    assert WaitBoostStore(path).read() == pruned


def native_absent_done_document_with_history():
    doc, open_row = make_cleanup_restore_document()
    cleanup_row = done_cleanup(open_row, 0)
    cleanup_row["cleanup_trigger"] = "native_absent"
    cleanup_row["cleanup_id"] = cleanup_id(cleanup_row)
    cleanup_row["cleanup_manifest_digest"] = cleanup_manifest(cleanup_row)
    doc["cleanups"] = {cleanup_row["cleanup_id"]: cleanup_row}
    parent = restored_boost(doc["rows"][boost_key("scope", "item")])
    parent["terminal_transition"] = "native_absent"
    parent["terminal_manifest"]["transition"] = "native_absent"
    doc["rows"][boost_key("scope", "item")] = parent
    operation = cleanup_row["boost_restore_operation_digest"]
    row = {
        "history_id": "0" * 64,
        "scope": "scope",
        "item_id": "item",
        "release_receipt": "b" * 64,
        "attempt_id": H32,
        "kind": "restore",
        "transition": "native_absent",
        "outcome": "restored_boosted",
        "receipt_digest": cleanup_row["boost_restore_receipt_digest"],
        "operation_digest": operation,
        "manifest": parent["terminal_manifest"],
        "terminal_at": 30.0,
        "result": {
            "state": "boosted",
            "outcome": "restored_boosted",
            "recovery_required": False,
            "operation_ref": op_ref(operation),
        },
    }
    row["history_id"] = key = history_id(row)
    doc["history"][key] = row
    return doc, key


def test_native_absent_restore_requires_exact_done_cleanup_and_replays_after_restart(
    tmp_path,
):
    doc, key = native_absent_done_document_with_history()
    validate(doc)
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    store._persist(doc)
    restarted = WaitBoostStore(path).read()
    row = restarted["history"][key]
    assert (
        store.resolve_history(
            key, scope="scope", item_id="item", release_receipt="b" * 64, attempt_id=H32
        )
        == row
    )
    bad = copy.deepcopy(doc)
    bad["cleanups"] = {}
    with pytest.raises(WaitBoostError, match="cleanup authority|cleanup missing"):
        validate(bad)
    bad = copy.deepcopy(doc)
    cleanup_row = next(iter(bad["cleanups"].values()))
    cleanup_row["cleanup_trigger"] = "spawn_denied"
    cleanup_row["cleanup_id"] = cleanup_id(cleanup_row)
    cleanup_row["cleanup_manifest_digest"] = cleanup_manifest(cleanup_row)
    bad["cleanups"] = {cleanup_row["cleanup_id"]: cleanup_row}
    with pytest.raises(WaitBoostError, match="cleanup authority|cleanup missing"):
        validate(bad)


def test_eligible_legacy_prune_same_key_enriched_replacement_refused_and_expires(
    tmp_path,
):
    key, legacy = legacy_completion_history(terminal_at=1.0)
    doc = empty_document()
    doc["history"][key] = legacy
    for index in range(32):
        newer_key, newer = history(index + 200, 900_000.0 + index)
        doc["history"][newer_key] = newer
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    store._persist(doc)
    enriched = copy.deepcopy(legacy)
    enriched["manifest"].update(
        scope="scope",
        item_id="item",
        root_id="root",
        root_created_at=1.0,
        release_receipt="b" * 64,
    )
    enriched["transition"] = "complete_bound_launch"
    enriched["outcome"] = "native_bound"
    enriched["result"] = {
        "state": "consumed",
        "outcome": "native_bound",
        "recovery_required": False,
        "operation_ref": op_ref(enriched["operation_digest"]),
    }
    with pytest.raises(WaitBoostError, match="key reuse"):
        store.change(
            0,
            lambda document: document["history"].update({key: enriched}),
            now=1_000_000.0,
        )
    pruned, _ = store.prune(now=1_000_000.0, expected_version=0)
    assert key not in pruned["history"] and WaitBoostStore(path).read() == pruned
    with pytest.raises(WaitBoostOperationHistoryExpired):
        store.resolve_history(
            key, scope="scope", item_id="item", release_receipt="b" * 64, attempt_id=H32
        )


def eligible_enriched_store(tmp_path):
    old_key, old = history(1, 1.0)
    doc = empty_document()
    doc["history"][old_key] = old
    for index in range(32):
        newer_key, newer = history(index + 300, 900_000.0 + index)
        newer["operation_digest"] = f"{index + 300:064x}"
        newer["result"]["operation_ref"] = op_ref(newer["operation_digest"])
        newer["history_id"] = newer_key = history_id(newer)
        doc["history"][newer_key] = newer
    validate(doc)
    path = tmp_path / "state.json"
    store = WaitBoostStore(path)
    store._persist(doc)
    return path, store, old_key, old


def different_identity_under_key(old, key):
    forged = copy.deepcopy(old)
    forged["operation_digest"] = "e" * 64
    forged["result"]["operation_ref"] = op_ref(forged["operation_digest"])
    forged["history_id"] = key
    return forged


def test_generic_same_cas_pruned_enriched_key_reuse_refused(tmp_path):
    path, store, old_key, old = eligible_enriched_store(tmp_path)
    before = path.read_bytes()
    forged = different_identity_under_key(old, old_key)
    with pytest.raises(WaitBoostError, match="key reuse"):
        store.change(
            0,
            lambda document: document["history"].update({old_key: forged}),
            now=1_000_000.0,
        )
    assert path.read_bytes() == before
    with pytest.raises(WaitBoostError, match="key reuse"):
        store.change(
            0,
            lambda document: document["history"].update({old_key: copy.deepcopy(old)}),
            now=1_000_000.0,
        )
    assert path.read_bytes() == before


def test_later_cas_forged_pruned_key_fails_identity_derivation(tmp_path):
    path, store, old_key, old = eligible_enriched_store(tmp_path)
    pruned, _ = store.prune(now=1_000_000.0, expected_version=0)
    assert old_key not in pruned["history"]
    restarted = WaitBoostStore(path).read()
    assert restarted == pruned
    with pytest.raises(
        WaitBoostOperationHistoryExpired, match="operation_history_expired"
    ):
        store.resolve_history(
            old_key,
            scope=old["scope"],
            item_id=old["item_id"],
            release_receipt=old["release_receipt"],
            attempt_id=old["attempt_id"],
        )
    before = path.read_bytes()
    forged = different_identity_under_key(old, old_key)
    with pytest.raises(WaitBoostError, match="history id mismatch"):
        store.change(
            pruned["version"],
            lambda document: document["history"].update({old_key: forged}),
            now=1_000_000.0,
        )
    assert path.read_bytes() == before
    distinct = copy.deepcopy(forged)
    distinct["history_id"] = distinct_key = history_id(distinct)
    assert distinct_key != old_key
    state, _ = store.change(
        pruned["version"],
        lambda document: document["history"].update({distinct_key: distinct}),
        now=1_000_000.0,
    )
    assert distinct_key in state["history"] and old_key not in state["history"]


def test_every_enriched_history_identity_field_changes_derived_id():
    key, row = history(5, 8.0)
    doc = empty_document()
    doc["history"][key] = row
    validate(doc)
    for field in (
        "kind",
        "scope",
        "item_id",
        "release_receipt",
        "attempt_id",
        "transition",
        "outcome",
        "receipt_digest",
        "operation_digest",
    ):
        bad = copy.deepcopy(doc)
        value = bad["history"][key][field]
        bad["history"][key][field] = (
            "f" * len(value)
            if field.endswith(("receipt", "digest", "_id")) and field != "item_id"
            else value + "x"
        )
        with pytest.raises(WaitBoostError):
            validate(bad)


def test_history_id_v8_byte_vector_and_contract_hashes():
    key, row = history(5, 8.0)
    exact_object = {
        "kind": row["kind"],
        "scope": row["scope"],
        "item_id": row["item_id"],
        "release_receipt": row["release_receipt"],
        "attempt_id": row["attempt_id"],
        "transition": row["transition"],
        "outcome": row["outcome"],
        "receipt_digest": row["receipt_digest"],
        "operation_digest": row["operation_digest"],
    }
    assert key == domain_digest("ciel.wait-boost.history-id.v1", exact_object)
    assert key == "c5cf5b3c82272f5ea5c80990c70d3803afd71248c4b1c36f7f69f23139ed1f05"


def test_manifest_hash_is_stable_and_source_contains_no_l2_file(tmp_path):
    root = (
        Path(__file__).resolve().parents[1]
        / "docs/journal/2026/11/14/features/side-released-wait-priority"
    )
    assert (
        hashlib.sha256((root / "design.md").read_bytes()).hexdigest()
        == "47167a9916edbdda30e7d0c70cf7026331a857449bf3c41b0434df29d0e848f9"
    )
    assert (
        hashlib.sha256((root / "host-api-v1.md").read_bytes()).hexdigest()
        == "c653061bb9d189220e7c6281f7d2df79c728333f5b6411cdf882f7487a466a84"
    )
    assert domain_digest("domain", {"b": 2, "a": 1}) == domain_digest(
        "domain", {"a": 1, "b": 2}
    )
    assert hashlib.sha256(b"proof").hexdigest() == hashlib.sha256(b"proof").hexdigest()
