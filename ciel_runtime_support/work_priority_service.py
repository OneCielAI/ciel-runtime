"""One production service for Work root priority across board and P0 sidecar."""
from __future__ import annotations

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


def _operation_digest(external_id, scope, root_id, root_created_at, target, set_by):
    return hashlib.sha256(_frame(
        "ciel.p0.priority-operation.v1", external_id, scope, root_id,
        repr(float(root_created_at)), target, set_by)).hexdigest()


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


def create_p0_root(*_args, **_kwargs):
    """Current MCP create APIs cannot premint root identity and operation id."""
    raise WorkParallelError(
        "P0 root creation requires a pre-minted root id/created_at and idempotency key; "
        "the current Work create API cannot carry that identity")
