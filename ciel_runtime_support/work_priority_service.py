"""One production service for Work root priority across board and P0 sidecar."""
from __future__ import annotations

import copy
import hashlib

from . import work_plan_tree as tree
from .work_parallel import WorkParallelError, require, text
from .work_parallel_p0 import p0_store


def _frame(*values):
    parts = []
    for value in values:
        encoded = str(value).encode("utf-8")
        parts.append(len(encoded).to_bytes(8, "big") + encoded)
    return b"".join(parts)


def _operation_id(external_id, scope):
    require(text(external_id, 200) and text(scope, 200),
            "P0 priority operation identity required")
    return hashlib.sha256(_frame(
        "ciel.p0.priority-operation-id.v1", scope, external_id)).hexdigest()


def _operation_digest(external_id, scope, root_id, root_created_at, target, set_by, manifest_digest=""):
    values = ["ciel.p0.priority-operation.v1", external_id, scope, root_id,
              repr(float(root_created_at)), target, set_by]
    # Preserve the deployed digest byte-for-byte for the original priority-only
    # API; combined commands append their full board-manifest digest.
    if manifest_digest:
        values.append(manifest_digest)
    return hashlib.sha256(_frame(*values)).hexdigest()


def _root(board, root_id):
    root = next((row for row in board.get("nodes", [])
                 if row.get("id") == root_id and row.get("parent_id") is None), None)
    require(root is not None, "unknown Work root")
    return root


def set_root_priority(board_store, ledger_store, scope, root_id, target, *,
                      expected_board_version, external_operation_id, set_by="user"):
    """Marker/op first, board CAS second, done/prune last; safe to replay."""
    require(target in ("P0", "", "P1", "P2", "P3", "P4", "P5"), "priority")
    require(set_by in ("user", "side_decision", "operator"), "priority source")
    board = board_store.read()
    root = _root(board, root_id)
    side = p0_store(ledger_store)
    op_id = _operation_id(external_operation_id, scope)
    request_digest = _operation_digest(
        external_operation_id, scope, root_id, root["created_at"], target, set_by)
    prior = side.read()["clears"].get(op_id)
    expected_target = "P1" if target == "P0" else target
    if prior is not None:
        require(prior["scope"] == scope and prior["root_id"] == root_id
                and prior["root_created_at"] == root["created_at"]
                and prior["target_priority"] == expected_target
                and prior["set_by"] == set_by
                and prior["operation_digest"] == request_digest,
                "priority operation id was already used for a different request")
        side.roll_forward_priority(op_id, board_store)
        result = _root(board_store.read(), root_id)
        return {"root_id": root_id,
                "effective_priority": side.effective_priority(scope, result, result.get("priority", "")),
                "canonical_priority": result.get("priority", ""),
                "version": board_store.read()["version"], "operation_id": op_id,
                "replayed": True}
    require(board["version"] == expected_board_version, "the plan changed; refetch and retry")
    if target == "P0":
        _, operation = side.prepare_set(
            scope, root_id, root["created_at"], board["version"], set_by=set_by,
            expected_version=side.read()["version"], operation_id=op_id,
            operation_digest=request_digest)
    else:
        # Setting an ordinary priority on a non-P0 root is just the canonical CAS.
        if side.effective_priority(scope, root, root.get("priority", "")) != "P0":
            doc, _ = board_store.transaction(
                lambda value: tree.update_node(value, root_id, priority=target),
                expected_version=board["version"])
            return {"root_id": root_id, "effective_priority": target,
                    "canonical_priority": target, "version": doc["version"],
                    "operation_id": op_id, "replayed": False}
        _, operation = side.prepare_clear(
            scope, root_id, root["created_at"], target, board["version"],
            expected_version=side.read()["version"], clear_id=op_id, set_by=set_by,
            operation_digest=request_digest)
    side.roll_forward_priority(operation["clear_id"], board_store)
    result = _root(board_store.read(), root_id)
    return {"root_id": root_id,
            "effective_priority": side.effective_priority(scope, result, result.get("priority", "")),
            "canonical_priority": result.get("priority", ""),
            "version": board_store.read()["version"], "operation_id": op_id,
            "replayed": False}


def recover(board_store, ledger_store, scope):
    side = p0_store(ledger_store)
    operations = side.recover_priority_operations(board_store)
    side.sweep_roots(scope, board_store.read(), expected_version=side.read()["version"])
    return operations


def mutate_existing_priority(board_store, ledger_store, scope, root_id, target, *,
                             expected_board_version, external_operation_id, mutate, satisfied,
                             manifest_digest="", set_by="user"):
    """Apply one existing-root command plus priority with a recoverable marker.

    The marker/op is durable first. The board has not committed unless its one
    CAS succeeds; that CAS applies all non-priority fields and canonical priority
    together. Exact replay rolls a prepared operation forward, then returns the
    current root without repeating the other fields.
    """
    require(target in ("P0", "", "P1", "P2", "P3", "P4", "P5"), "priority")
    require(callable(mutate) and callable(satisfied), "priority mutation")
    board = board_store.read()
    root = _root(board, root_id)
    side = p0_store(ledger_store)
    op_id = _operation_id(external_operation_id, scope)
    request_digest = _operation_digest(
        external_operation_id, scope, root_id, root["created_at"], target, set_by,
        manifest_digest)
    expected_target = "P1" if target == "P0" else target
    prior = side.read()["clears"].get(op_id)
    if prior is not None:
        require(prior["scope"] == scope and prior["root_id"] == root_id
                and prior["root_created_at"] == root["created_at"]
                and prior["target_priority"] == expected_target
                and prior["set_by"] == set_by
                and prior["operation_digest"] == request_digest,
                "priority operation id was already used for a different request")
        side.roll_forward_priority(op_id, board_store)
        current = board_store.read()
        result = _root(current, root_id)
        require(satisfied(current), "priority operation board payload changed after an unknown outcome")
        return {"root": result, "effective_priority": side.effective_priority(scope, result),
                "canonical_priority": result.get("priority", ""),
                "version": board_store.read()["version"], "operation_id": op_id,
                "replayed": True}
    require(board["version"] == expected_board_version, "the plan changed; refetch and retry")
    effective = side.effective_priority(scope, root, root.get("priority", ""))
    if target == "P0":
        _, operation = side.prepare_set(
            scope, root_id, root["created_at"], board["version"], set_by=set_by,
            expected_version=side.read()["version"], operation_id=op_id,
            operation_digest=request_digest)
    elif effective == "P0":
        _, operation = side.prepare_clear(
            scope, root_id, root["created_at"], target, board["version"],
            expected_version=side.read()["version"], clear_id=op_id, set_by=set_by,
            operation_digest=request_digest)
    else:
        operation = None
    def apply(document):
        result = mutate(document)
        tree.update_node(document, root_id, priority=expected_target)
        return result
    document, value = board_store.transaction(apply, expected_version=board["version"])
    if operation is not None:
        # Board CAS already committed the target. Mark the prepared operation
        # forward without applying a second semantic board write.
        state = side.read()
        _, operation = side.mark_clear(operation["clear_id"], "board_committed",
                                       expected_version=state["version"])
        state = side.read()
        side.mark_clear(operation["clear_id"], "done", expected_version=state["version"])
        side.prune_done(expected_version=side.read()["version"])
    result = _root(document, root_id)
    return {"root": copy.deepcopy(result), "value": value,
            "effective_priority": side.effective_priority(scope, result),
            "canonical_priority": result.get("priority", ""),
            "version": document["version"], "operation_id": op_id, "replayed": False}


def create_p0_root(*_args, **_kwargs):
    """New-root P0 stays fail-closed in this mixed-version beta.

    An existing root can be activated immediately through ``set_root_priority``
    or ``mutate_existing_priority``. A caller creates a new goal canonically at
    P1 first, then activates that returned identity as P0.
    """
    raise WorkParallelError(
        "p0_new_root_unsupported_mixed_version: New P0 goal creation is unavailable in this beta because "
        "concurrent older Work writers cannot be safely excluded. Create the goal at P1-P5, then use "
        "supported existing-root P0 activation.")
