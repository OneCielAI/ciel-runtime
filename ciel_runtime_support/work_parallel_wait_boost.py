"""Strict-CAS Gate-5 released-wait authority store.

The store deliberately contains no scheduler policy.  It validates the complete
persisted grammar, exact parent/child relations, bounded retention and durable
atomic replacement used by the host/backend layers.
"""

from __future__ import annotations

import copy
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .durable_file_lock import exclusive_file_lock

SCHEMA = 1
FILE_NAME = "work-parallel-wait-boost-v1.json"
MAX_BYTES = 2 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
MAX_ENTRIES = 2_000
KEEP_NEWEST = 32
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_TEXT = 200
MAX_SAFE_INTEGER = 2**53 - 1
MAX_TIME = 1e15
TOP_FIELDS = frozenset(
    ("schema", "version", "rows", "bind_sagas", "workspaces", "cleanups", "history")
)
LEGACY_TOP_FIELDS = (
    frozenset(("schema", "version", "rows")),
    frozenset(("schema", "version", "rows", "history")),
)

ATTEMPT_FIELDS = (
    "attempt_id",
    "attempt_kind",
    "ledger_before_version",
    "lease_id",
    "pre_state",
    "pre_slot",
    "pre_lease_digest",
    "expected_post_state",
    "expected_post_slot",
    "expected_post_role",
    "expected_dispatch_name",
    "send_receipt",
    "send_state",
    "slot_acquired_version",
    "slot_acquire_digest",
    "attempt_request_digest",
)
LAUNCH_FIELDS = (
    "launch_state",
    "launch_lease_id",
    "assignment_generation",
    "assignment_digest",
    "capability_derivation_digest",
    "launch_dispatch_name",
    "reserve_receipt_digest",
    "launch_manifest_digest",
)
COMPLETION_FIELDS = (
    "completed_attempt_id",
    "completed_attempt_kind",
    "completed_ledger_before_version",
    "completed_ledger_after_version",
    "completed_lease_id",
    "completed_receipt_digest",
    "operation_digest",
)
TERMINAL_FIELDS = (
    "terminal_attempt_id",
    "terminal_transition",
    "terminal_outcome",
    "terminal_receipt_digest",
    "terminal_operation_digest",
    "terminal_at",
    "terminal_manifest",
)
BOOST_FIELDS = frozenset(
    (
        "scope",
        "item_id",
        "root_id",
        "root_created_at",
        "release_receipt",
        "release_at",
        "wait_digest",
        "parked_lease_id",
        "release_store_before_version",
        "state",
        *ATTEMPT_FIELDS,
        *LAUNCH_FIELDS,
        "outcome",
        "consumed_at",
        *COMPLETION_FIELDS,
        *TERMINAL_FIELDS,
        "updated_at",
    )
)
BIND_FIELDS = frozenset(
    (
        "attempt_id",
        "scope",
        "root_id",
        "root_created_at",
        "item_id",
        "lease_id",
        "dispatch_name",
        "assignment_generation",
        "assignment_digest",
        "state",
        "agent_id",
        "agent_name",
        "bind_manifest_digest",
        "ledger_before_version",
        "ledger_after_version",
        "ledger_bound_digest",
        "coord_before_version",
        "coord_after_version",
        "coord_bound_digest",
        "bind_receipt_digest",
        "operation_digest",
        "rollback_receipt_digest",
        "rollback_operation_digest",
        "created_at",
        "updated_at",
        "terminal_at",
    )
)
WORKSPACE_FIELDS = frozenset(
    (
        "attempt_id",
        "scope",
        "root_id",
        "root_created_at",
        "item_id",
        "lease_id",
        "workspace_operation_id",
        "state",
        "repo_identity_digest",
        "base_head",
        "target_ref",
        "target_path_digest",
        "branch_digest",
        "gitdir_digest",
        "manifest_digest",
        "ready_receipt_digest",
        "lease_attach_receipt_digest",
        "coord_attach_receipt_digest",
        "rollback_receipt_digest",
        "rollback_operation_digest",
        "created_at",
        "updated_at",
        "terminal_at",
    )
)
CLEANUP_FIELDS = frozenset(
    (
        "cleanup_id",
        "cleanup_trigger",
        "cleanup_manifest_digest",
        "release_receipt",
        "attempt_id",
        "scope",
        "root_id",
        "root_created_at",
        "item_id",
        "lease_id",
        "workspace_operation_id",
        "bind_manifest_digest",
        "state",
        "native_absence_receipt_digest",
        "bind_disposition",
        "no_bind_proof_digest",
        "bind_rollback_receipt_digest",
        "bind_rollback_operation_digest",
        "coord_before_version",
        "coord_after_version",
        "coord_detach_receipt_digest",
        "ledger_before_version",
        "ledger_after_version",
        "ledger_detach_receipt_digest",
        "workspace_cleanup_receipt_digest",
        "lease_release_receipt_digest",
        "boost_sidecar_before_version",
        "boost_sidecar_after_version",
        "boost_restore_receipt_digest",
        "boost_restore_operation_digest",
        "cleanup_operation_digest",
        "created_at",
        "updated_at",
        "terminal_at",
    )
)
HISTORY_FIELDS = frozenset(
    (
        "history_id",
        "scope",
        "item_id",
        "release_receipt",
        "attempt_id",
        "kind",
        "transition",
        "outcome",
        "receipt_digest",
        "operation_digest",
        "manifest",
        "terminal_at",
        "result",
    )
)
HISTORY_RESULT_FIELDS = frozenset(
    ("state", "outcome", "recovery_required", "operation_ref")
)

# Exact discriminated history manifests.  These are persisted proof projections,
# not extensible dictionaries.
FULL_PIN_FIELDS = frozenset(
    (
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
)
LEGACY_COMPLETION_MANIFEST_FIELDS = frozenset(COMPLETION_FIELDS)
COMPLETION_MANIFEST_FIELDS = frozenset(
    (
        "scope",
        "item_id",
        "root_id",
        "root_created_at",
        "release_receipt",
        *COMPLETION_FIELDS,
    )
)
RESTORE_CANCEL_MANIFEST_FIELDS = frozenset((*FULL_PIN_FIELDS, "transition", "outcome"))
SEND_ROLLBACK_MANIFEST_FIELDS = frozenset((*FULL_PIN_FIELDS, "rollback_receipt_digest"))
BIND_TERMINAL_MANIFEST_FIELDS = BIND_FIELDS - {
    "state",
    "created_at",
    "updated_at",
    "terminal_at",
}
WORKSPACE_TERMINAL_MANIFEST_FIELDS = WORKSPACE_FIELDS - {
    "state",
    "created_at",
    "updated_at",
    "terminal_at",
}

BOOST_STATES = frozenset(("boosted", "consuming", "consumed", "cancelled"))
BIND_STATES = frozenset(
    (
        "prepared",
        "ledger_bound",
        "coord_bound",
        "done",
        "recovery_required",
        "rolled_back",
    )
)
WORKSPACE_STATES = frozenset(
    (
        "prepared",
        "ready",
        "lease_attached",
        "coord_attached",
        "recovery_required",
        "done",
        "rolled_back",
    )
)
CLEANUP_STATES = frozenset(
    (
        "prepared",
        "bind_rolled_back",
        "coord_detached",
        "ledger_detached",
        "workspace_removed",
        "lease_released",
        "done",
        "recovery_required",
    )
)
HISTORY_KINDS = frozenset(
    (
        "completion",
        "restore",
        "send_rollback",
        "cancel",
        "bind_terminal",
        "workspace_terminal",
    )
)
ATTEMPT_KINDS = frozenset(("reacquire", "reserve", "running_notify"))
LEASE_OUTCOMES = frozenset(("reacquired", "native_bound", "running_notified"))
CANCEL_OUTCOMES = frozenset(("reblocked", "terminal", "identity_mismatch"))
LAUNCH_STATES = frozenset(("reserved", "assignment_issued", "spawning", "uncertain"))
SEND_STATES = frozenset(("pending", "send_pending", "uncertain"))


class WaitBoostError(ValueError):
    pass


class WaitBoostConflict(WaitBoostError):
    pass


class WaitBoostStateUnavailable(WaitBoostError):
    pass


class WaitBoostCommitAmbiguous(WaitBoostStateUnavailable):
    pass


class WaitBoostOperationHistoryExpired(WaitBoostError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WaitBoostError(message)


def _safe_int(value: Any) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def _time(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= MAX_TIME
    )


def _text(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= MAX_TEXT and "\0" not in value


def _hex(value: Any, length: int) -> bool:
    return (
        type(value) is str
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _frame(data: bytes) -> bytes:
    return len(data).to_bytes(8, "big") + data


def domain_digest(domain: str, value: Mapping[str, Any]) -> str:
    import hashlib

    _require(_text(domain) and type(value) is dict, "invalid digest input")
    return hashlib.sha256(
        _frame(domain.encode("utf-8")) + _frame(_canonical(value))
    ).hexdigest()


def boost_key(scope: str, item_id: str) -> str:
    _require(_text(scope) and _text(item_id), "invalid boost identity")
    return scope + "\0" + item_id


def _exact(row: Any, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    _require(type(row) is dict and set(row) == fields, f"invalid {name} fields")
    _require(len(_canonical(row)) <= MAX_ROW_BYTES, f"{name} row byte bound")
    return row


def _all_null(row: Mapping[str, Any], fields: Iterable[str]) -> bool:
    return all(row[name] is None for name in fields)


def _pair(row: Mapping[str, Any], first: str, second: str) -> bool:
    return (row[first] is None) == (row[second] is None)


def _identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row[name]
        for name in (
            "attempt_id",
            "scope",
            "root_id",
            "root_created_at",
            "item_id",
            "lease_id",
        )
    )


def _validate_common_identity(row: Mapping[str, Any]) -> None:
    _require(_hex(row["attempt_id"], 32), "invalid attempt id")
    _require(
        all(_text(row[name]) for name in ("scope", "root_id", "item_id", "lease_id")),
        "invalid linked identity",
    )
    _require(_time(row["root_created_at"]), "invalid root time")


def _validate_launch(row: Mapping[str, Any]) -> None:
    present = row["launch_state"] is not None
    if not present:
        _require(_all_null(row, LAUNCH_FIELDS), "partial launch proof")
        return
    _require(
        row["attempt_kind"] == "reserve" and row["launch_state"] in LAUNCH_STATES,
        "invalid launch state",
    )
    _require(
        _text(row["launch_lease_id"]) and row["launch_lease_id"] == row["lease_id"],
        "invalid launch lease",
    )
    _require(
        _safe_int(row["assignment_generation"]) and row["assignment_generation"] >= 1,
        "invalid launch generation",
    )
    _require(
        all(
            _hex(row[name], 64)
            for name in (
                "assignment_digest",
                "capability_derivation_digest",
                "reserve_receipt_digest",
                "launch_manifest_digest",
            )
        ),
        "invalid launch digest",
    )
    _require(
        _text(row["launch_dispatch_name"])
        and row["launch_dispatch_name"] == row["expected_dispatch_name"],
        "invalid launch dispatch",
    )


def _validate_attempt(row: Mapping[str, Any]) -> None:
    _require(
        _hex(row["attempt_id"], 32) and row["attempt_kind"] in ATTEMPT_KINDS,
        "invalid open attempt",
    )
    _require(
        _safe_int(row["ledger_before_version"])
        and _hex(row["attempt_request_digest"], 64),
        "invalid attempt pin",
    )
    _require(
        _text(row["lease_id"])
        and _text(row["pre_state"])
        and _text(row["expected_post_state"]),
        "invalid attempt state",
    )
    kind = row["attempt_kind"]
    if kind == "reserve":
        _require(
            row["pre_state"] == "absent" and row["expected_post_state"] == "reserved",
            "invalid reserve transition",
        )
        _require(
            row["pre_slot"] is False and _hex(row["pre_lease_digest"], 64),
            "invalid absent-state proof",
        )
        _require(
            type(row["expected_post_slot"]) is bool
            and row["expected_post_role"] == "worker",
            "invalid reserve post state",
        )
        _require(
            _text(row["expected_dispatch_name"])
            and row["expected_dispatch_name"] == "work-wait-boost-" + row["attempt_id"],
            "invalid reserve dispatch",
        )
        _require(
            row["send_receipt"] is None and row["send_state"] is None,
            "reserve carries send",
        )
        _require(
            row["slot_acquired_version"] is None and row["slot_acquire_digest"] is None,
            "reserve carries slot proof",
        )
        _validate_launch(row)
        return
    _require(
        row["expected_dispatch_name"] is None and _hex(row["send_receipt"], 64),
        "send attempt identity",
    )
    _require(row["send_state"] in SEND_STATES, "invalid send state")
    _require(_all_null(row, LAUNCH_FIELDS), "non-reserve carries launch")
    _require(_hex(row["pre_lease_digest"], 64), "send attempt lease proof")
    if kind == "reacquire":
        _require(
            row["pre_state"] == row["expected_post_state"] == "waiting_input",
            "invalid reacquire state",
        )
        _require(
            row["pre_slot"] is False
            and row["expected_post_slot"] is True
            and row["expected_post_role"] == "resume",
            "invalid reacquire slot",
        )
        acquired = (
            row["slot_acquired_version"] is not None
            or row["slot_acquire_digest"] is not None
        )
        _require(
            (row["slot_acquired_version"] is None)
            == (row["slot_acquire_digest"] is None),
            "partial slot proof",
        )
        if acquired:
            _require(
                _safe_int(row["slot_acquired_version"])
                and row["slot_acquired_version"] == row["ledger_before_version"] + 1,
                "invalid slot version",
            )
            _require(
                _hex(row["slot_acquire_digest"], 64)
                and row["send_state"] in ("send_pending", "uncertain"),
                "invalid slot proof",
            )
        else:
            _require(
                row["send_state"] in ("pending", "uncertain"),
                "slot-free reacquire phase",
            )
    else:
        _require(
            row["pre_state"] == row["expected_post_state"] == "running",
            "invalid running notify state",
        )
        _require(
            row["pre_slot"] is None
            and row["expected_post_slot"] is None
            and row["expected_post_role"] == "worker",
            "running notify has slot transition",
        )
        _require(
            row["slot_acquired_version"] is None and row["slot_acquire_digest"] is None,
            "running notify slot proof",
        )


def _validate_pin_manifest(pin: Mapping[str, Any]) -> None:
    _require(
        _text(pin["scope"]) and _text(pin["item_id"]) and _text(pin["root_id"]),
        "invalid manifest release identity",
    )
    _require(
        _time(pin["root_created_at"]) and _time(pin["release_at"]),
        "invalid manifest release time",
    )
    _require(
        _hex(pin["release_receipt"], 64) and _hex(pin["wait_digest"], 64),
        "invalid manifest release proof",
    )
    _require(
        pin["parked_lease_id"] is None or _text(pin["parked_lease_id"]),
        "invalid manifest parked lease",
    )
    _require(
        _safe_int(pin["release_store_before_version"]),
        "invalid manifest release version",
    )
    _validate_attempt(pin)


def _validate_terminal_manifest(
    value: Any, *, allowed: tuple[frozenset[str], ...]
) -> None:
    _require(
        type(value) is dict and any(set(value) == fields for fields in allowed),
        "invalid terminal manifest fields",
    )
    _require(len(_canonical(value)) <= MAX_ROW_BYTES, "terminal manifest byte bound")
    _validate_pin_manifest(value)
    if set(value) == SEND_ROLLBACK_MANIFEST_FIELDS:
        _require(
            _hex(value["rollback_receipt_digest"], 64),
            "invalid terminal rollback receipt",
        )
    else:
        _require(
            _text(value["transition"]) and _text(value["outcome"]),
            "invalid terminal transition",
        )


def validate_boost(key: str, value: Any) -> None:
    row = _exact(value, BOOST_FIELDS, "boost")
    _require(key == boost_key(row["scope"], row["item_id"]), "boost map key mismatch")
    _require(
        _text(row["root_id"]) and _time(row["root_created_at"]), "invalid boost root"
    )
    _require(
        _hex(row["release_receipt"], 64) and _hex(row["wait_digest"], 64),
        "invalid boost receipt",
    )
    _require(
        _time(row["release_at"]) and _time(row["updated_at"]), "invalid boost time"
    )
    _require(
        _safe_int(row["release_store_before_version"]), "invalid release store version"
    )
    _require(
        row["parked_lease_id"] is None or _text(row["parked_lease_id"]),
        "invalid parked lease",
    )
    _require(row["state"] in BOOST_STATES, "invalid boost state")

    if row["state"] == "consuming":
        _validate_attempt(row)
        _require(
            row["outcome"] is None and row["consumed_at"] is None,
            "consuming terminal outcome",
        )
        _require(
            _all_null(row, COMPLETION_FIELDS + TERMINAL_FIELDS),
            "consuming terminal proof",
        )
    else:
        _require(
            _all_null(row, ATTEMPT_FIELDS + LAUNCH_FIELDS),
            "inactive boost retains attempt",
        )

    if row["state"] == "boosted":
        _require(
            row["outcome"] is None
            and row["consumed_at"] is None
            and _all_null(row, COMPLETION_FIELDS),
            "boosted completion data",
        )
        if row["terminal_attempt_id"] is None:
            _require(_all_null(row, TERMINAL_FIELDS), "partial boosted terminal audit")
        else:
            _require(
                _hex(row["terminal_attempt_id"], 32)
                and _text(row["terminal_transition"])
                and _text(row["terminal_outcome"]),
                "boosted terminal identity",
            )
            _require(
                _hex(row["terminal_receipt_digest"], 64)
                and _hex(row["terminal_operation_digest"], 64)
                and _time(row["terminal_at"]),
                "boosted terminal proof",
            )
            _validate_terminal_manifest(
                row["terminal_manifest"],
                allowed=(RESTORE_CANCEL_MANIFEST_FIELDS, SEND_ROLLBACK_MANIFEST_FIELDS),
            )
    elif row["state"] == "consumed":
        _require(
            row["outcome"] in (*LEASE_OUTCOMES, "p0") and _time(row["consumed_at"]),
            "consumed identity",
        )
        _require(_all_null(row, TERMINAL_FIELDS), "consumed terminal audit")
        if row["outcome"] == "p0":
            _require(_all_null(row, COMPLETION_FIELDS), "p0 completion identity")
        else:
            _require(
                _hex(row["completed_attempt_id"], 32)
                and row["completed_attempt_kind"] in ATTEMPT_KINDS,
                "completion attempt",
            )
            _require(
                _safe_int(row["completed_ledger_before_version"])
                and _safe_int(row["completed_ledger_after_version"]),
                "completion versions",
            )
            _require(
                row["completed_ledger_after_version"]
                >= row["completed_ledger_before_version"],
                "completion version order",
            )
            _require(
                _text(row["completed_lease_id"])
                and _hex(row["completed_receipt_digest"], 64)
                and _hex(row["operation_digest"], 64),
                "completion proof",
            )
    elif row["state"] == "cancelled":
        _require(
            row["outcome"] in CANCEL_OUTCOMES
            and row["consumed_at"] is None
            and _all_null(row, COMPLETION_FIELDS),
            "cancel outcome",
        )
        _require(
            _hex(row["terminal_attempt_id"], 32)
            and _text(row["terminal_transition"])
            and row["terminal_outcome"] == row["outcome"],
            "cancel identity",
        )
        _require(
            _hex(row["terminal_receipt_digest"], 64)
            and _hex(row["terminal_operation_digest"], 64)
            and _time(row["terminal_at"]),
            "cancel proof",
        )
        _validate_terminal_manifest(
            row["terminal_manifest"], allowed=(RESTORE_CANCEL_MANIFEST_FIELDS,)
        )


def bind_receipt_digest(row: Mapping[str, Any]) -> str:
    """Compute the exact final bind-commit receipt digest from persisted facts."""
    payload = {
        "schema": "wait-boost-bind-commit/1",
        "attempt_id": row["attempt_id"],
        "lease_id": row["lease_id"],
        "dispatch_name": row["dispatch_name"],
        "ledger_before_version": row["ledger_before_version"],
        "ledger_after_version": row["ledger_after_version"],
        "ledger_bound_digest": row["ledger_bound_digest"],
        "coord_before_version": row["coord_before_version"],
        "coord_after_version": row["coord_after_version"],
        "coord_bound_digest": row["coord_bound_digest"],
        "agent_id": row["agent_id"],
        "agent_name": row["agent_name"],
        "assignment_generation": row["assignment_generation"],
        "assignment_digest": row["assignment_digest"],
        "operation_digest": row["operation_digest"],
    }
    return domain_digest("ciel.wait-boost.bind-receipt.v1", payload)


def validate_bind(key: str, value: Any) -> None:
    row = _exact(value, BIND_FIELDS, "bind saga")
    _validate_common_identity(row)
    _require(
        key == row["attempt_id"]
        and _text(row["dispatch_name"])
        and _text(row["agent_id"])
        and _text(row["agent_name"]),
        "bind key/agent",
    )
    _require(
        row["dispatch_name"] == "work-wait-boost-" + row["attempt_id"], "bind dispatch"
    )
    _require(
        _safe_int(row["assignment_generation"]) and row["assignment_generation"] >= 1,
        "bind generation",
    )
    _require(
        _hex(row["assignment_digest"], 64) and _hex(row["bind_manifest_digest"], 64),
        "bind manifest",
    )
    _require(
        row["state"] in BIND_STATES
        and _safe_int(row["ledger_before_version"])
        and _safe_int(row["coord_before_version"]),
        "bind state/version",
    )
    _require(_time(row["created_at"]) and _time(row["updated_at"]), "bind time")

    ledger_pair = (
        row["ledger_after_version"] is not None
        or row["ledger_bound_digest"] is not None
    )
    coord_pair = (
        row["coord_after_version"] is not None or row["coord_bound_digest"] is not None
    )
    _require(
        (row["ledger_after_version"] is None) == (row["ledger_bound_digest"] is None),
        "partial bind ledger proof",
    )
    _require(
        (row["coord_after_version"] is None) == (row["coord_bound_digest"] is None),
        "partial bind coord proof",
    )
    if ledger_pair:
        _require(
            row["ledger_after_version"] == row["ledger_before_version"] + 1
            and _hex(row["ledger_bound_digest"], 64),
            "bind ledger proof",
        )
    if coord_pair:
        _require(
            ledger_pair
            and row["coord_after_version"] == row["coord_before_version"] + 1
            and _hex(row["coord_bound_digest"], 64),
            "bind coord proof",
        )
    final_pair = (
        row["bind_receipt_digest"] is not None or row["operation_digest"] is not None
    )
    _require(
        (row["bind_receipt_digest"] is None) == (row["operation_digest"] is None),
        "partial bind final proof",
    )
    if final_pair:
        _require(
            coord_pair
            and _hex(row["bind_receipt_digest"], 64)
            and _hex(row["operation_digest"], 64),
            "bind final proof",
        )
        _require(
            row["bind_receipt_digest"] == bind_receipt_digest(row),
            "bind final receipt mismatch",
        )
    rollback_pair = (
        row["rollback_receipt_digest"] is not None
        or row["rollback_operation_digest"] is not None
    )
    _require(
        (row["rollback_receipt_digest"] is None)
        == (row["rollback_operation_digest"] is None),
        "partial bind rollback proof",
    )
    if rollback_pair:
        _require(
            _hex(row["rollback_receipt_digest"], 64)
            and _hex(row["rollback_operation_digest"], 64),
            "bind rollback proof",
        )

    phase = row["state"]
    exact = {
        "prepared": (False, False, False),
        "ledger_bound": (True, False, False),
        "coord_bound": (True, True, False),
        "done": (True, True, True),
    }
    if phase in exact:
        _require(
            (ledger_pair, coord_pair, final_pair) == exact[phase],
            "bind phase proof mismatch",
        )
    if phase == "done":
        _require(
            not rollback_pair and _time(row["terminal_at"]), "bind done terminal proof"
        )
    elif phase == "rolled_back":
        _require(
            rollback_pair and _time(row["terminal_at"]), "bind rollback terminal proof"
        )
        # A final bind receipt may survive rollback only when the entire final
        # committed proof is present and byte-derived from the same saga.
        _require(
            not final_pair or (ledger_pair and coord_pair),
            "rolled back bind final proof",
        )
    else:
        _require(row["terminal_at"] is None, "nonterminal bind terminal time")
        if phase != "recovery_required":
            _require(not rollback_pair, "open bind rollback proof")


def _receipt_prefix(row: Mapping[str, Any], names: tuple[str, ...]) -> int:
    count = 0
    missing = False
    for name in names:
        value = row[name]
        if value is None:
            missing = True
        else:
            _require(not missing and _hex(value, 64), "nonmonotonic receipt sequence")
            count += 1
    return count


def validate_workspace(key: str, value: Any) -> None:
    row = _exact(value, WORKSPACE_FIELDS, "workspace")
    _validate_common_identity(row)
    _require(
        key == row["attempt_id"] and _hex(row["workspace_operation_id"], 32),
        "workspace key/operation",
    )
    _require(
        all(
            _hex(row[name], 64)
            for name in (
                "repo_identity_digest",
                "target_path_digest",
                "branch_digest",
                "gitdir_digest",
                "manifest_digest",
            )
        ),
        "workspace digests",
    )
    _require(_text(row["base_head"]) and _text(row["target_ref"]), "workspace refs")
    _require(
        row["state"] in WORKSPACE_STATES
        and _time(row["created_at"])
        and _time(row["updated_at"]),
        "workspace state/time",
    )
    count = _receipt_prefix(
        row,
        (
            "ready_receipt_digest",
            "lease_attach_receipt_digest",
            "coord_attach_receipt_digest",
        ),
    )
    exact = {
        "prepared": 0,
        "ready": 1,
        "lease_attached": 2,
        "coord_attached": 3,
        "done": 3,
    }
    if row["state"] in exact:
        _require(count == exact[row["state"]], "workspace phase receipt mismatch")
    rollback_pair = (
        row["rollback_receipt_digest"] is not None
        or row["rollback_operation_digest"] is not None
    )
    _require(
        (row["rollback_receipt_digest"] is None)
        == (row["rollback_operation_digest"] is None),
        "partial workspace rollback proof",
    )
    if rollback_pair:
        _require(
            _hex(row["rollback_receipt_digest"], 64)
            and _hex(row["rollback_operation_digest"], 64),
            "workspace rollback proof",
        )
    if row["state"] == "done":
        _require(not rollback_pair and _time(row["terminal_at"]), "workspace done")
    elif row["state"] == "rolled_back":
        _require(rollback_pair and _time(row["terminal_at"]), "workspace rollback")
    else:
        _require(row["terminal_at"] is None, "open workspace terminal time")
        if row["state"] != "recovery_required":
            _require(not rollback_pair, "open workspace rollback")


def cleanup_id(row: Mapping[str, Any]) -> str:
    return domain_digest(
        "ciel.wait-boost.cleanup-id.v1",
        {
            name: row[name]
            for name in (
                "scope",
                "item_id",
                "release_receipt",
                "attempt_id",
                "cleanup_trigger",
            )
        },
    )


def cleanup_manifest(row: Mapping[str, Any]) -> str:
    names = (
        "cleanup_id",
        "cleanup_trigger",
        "release_receipt",
        "attempt_id",
        "scope",
        "root_id",
        "root_created_at",
        "item_id",
        "lease_id",
        "workspace_operation_id",
        "bind_manifest_digest",
    )
    return domain_digest(
        "ciel.wait-boost.cleanup-manifest.v1", {name: row[name] for name in names}
    )


def _cleanup_progress(row: Mapping[str, Any]) -> int:
    """Return the completely proven cleanup phase index; reject holes/partials."""
    disposition = row["bind_disposition"]
    bind_any = any(
        row[name] is not None
        for name in (
            "bind_disposition",
            "no_bind_proof_digest",
            "bind_rollback_receipt_digest",
            "bind_rollback_operation_digest",
        )
    )
    if not bind_any:
        bind_index = 0
    else:
        _require(
            disposition in ("rolled_back", "not_created"), "cleanup bind disposition"
        )
        if disposition == "rolled_back":
            _require(
                _hex(row["bind_manifest_digest"], 64)
                and row["no_bind_proof_digest"] is None,
                "cleanup rollback branch",
            )
            _require(
                _hex(row["bind_rollback_receipt_digest"], 64)
                and _hex(row["bind_rollback_operation_digest"], 64),
                "cleanup bind rollback",
            )
        else:
            _require(
                row["bind_manifest_digest"] is None
                and _hex(row["no_bind_proof_digest"], 64),
                "cleanup no-bind branch",
            )
            _require(
                row["bind_rollback_receipt_digest"] is None
                and row["bind_rollback_operation_digest"] is None,
                "cleanup no-bind rollback",
            )
        bind_index = 1

    progress = bind_index
    for expected, before, after, receipt in (
        (
            2,
            "coord_before_version",
            "coord_after_version",
            "coord_detach_receipt_digest",
        ),
        (
            3,
            "ledger_before_version",
            "ledger_after_version",
            "ledger_detach_receipt_digest",
        ),
    ):
        values = (row[before], row[after], row[receipt])
        present = any(value is not None for value in values)
        if present:
            _require(progress == expected - 1, "cleanup phase hole")
            _require(
                _safe_int(row[before])
                and row[after] == row[before] + 1
                and _hex(row[receipt], 64),
                "partial cleanup detach proof",
            )
            progress = expected
    for expected, name in (
        (4, "workspace_cleanup_receipt_digest"),
        (5, "lease_release_receipt_digest"),
    ):
        if row[name] is not None:
            _require(
                progress == expected - 1 and _hex(row[name], 64),
                "cleanup scalar phase proof",
            )
            progress = expected
    restore_names = (
        "boost_sidecar_before_version",
        "boost_sidecar_after_version",
        "boost_restore_receipt_digest",
        "boost_restore_operation_digest",
        "cleanup_operation_digest",
    )
    restore_any = any(row[name] is not None for name in restore_names)
    if restore_any:
        _require(
            progress == 5 and all(row[name] is not None for name in restore_names),
            "partial cleanup restore proof",
        )
        _require(
            _safe_int(row["boost_sidecar_before_version"])
            and row["boost_sidecar_after_version"]
            == row["boost_sidecar_before_version"] + 1,
            "cleanup restore versions",
        )
        _require(
            all(_hex(row[name], 64) for name in restore_names[2:]),
            "cleanup restore digests",
        )
        progress = 6
    return progress


def validate_cleanup(key: str, value: Any) -> None:
    row = _exact(value, CLEANUP_FIELDS, "cleanup")
    _validate_common_identity(row)
    _require(
        row["cleanup_trigger"] in ("spawn_denied", "native_absent")
        and _hex(row["release_receipt"], 64),
        "cleanup trigger",
    )
    _require(key == row["cleanup_id"] == cleanup_id(row), "cleanup key")
    _require(
        _hex(row["cleanup_manifest_digest"], 64)
        and row["cleanup_manifest_digest"] == cleanup_manifest(row),
        "cleanup manifest",
    )
    _require(
        _hex(row["workspace_operation_id"], 32) and row["state"] in CLEANUP_STATES,
        "cleanup workspace/state",
    )
    _require(
        _hex(row["native_absence_receipt_digest"], 64)
        and _time(row["created_at"])
        and _time(row["updated_at"]),
        "cleanup prepared proof",
    )
    progress = _cleanup_progress(row)
    exact = {
        "prepared": 0,
        "bind_rolled_back": 1,
        "coord_detached": 2,
        "ledger_detached": 3,
        "workspace_removed": 4,
        "lease_released": 5,
        "done": 6,
    }
    if row["state"] in exact:
        _require(progress == exact[row["state"]], "cleanup state proof mismatch")
    if row["state"] == "done":
        _require(_time(row["terminal_at"]), "cleanup done time")
    else:
        _require(row["terminal_at"] is None, "nonterminal cleanup time")


def _terminal_child_from_history(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Reconstruct a complete terminal child and run its normal validator."""
    kind = row["kind"]
    manifest = row["manifest"]
    if kind == "bind_terminal":
        state = row["result"]["state"]
        _require(state in ("done", "rolled_back"), "history bind terminal state")
        candidate = dict(manifest)
        candidate.update(
            state=state,
            created_at=row["terminal_at"],
            updated_at=row["terminal_at"],
            terminal_at=row["terminal_at"],
        )
        validate_bind(candidate["attempt_id"], candidate)
        return candidate
    if kind == "workspace_terminal":
        state = row["result"]["state"]
        _require(state in ("done", "rolled_back"), "history workspace terminal state")
        candidate = dict(manifest)
        candidate.update(
            state=state,
            created_at=row["terminal_at"],
            updated_at=row["terminal_at"],
            terminal_at=row["terminal_at"],
        )
        validate_workspace(candidate["attempt_id"], candidate)
        return candidate
    return None


def _validate_manifest_values(kind: str, manifest: Mapping[str, Any]) -> None:
    expected = {
        "completion": COMPLETION_MANIFEST_FIELDS,
        "restore": RESTORE_CANCEL_MANIFEST_FIELDS,
        "cancel": RESTORE_CANCEL_MANIFEST_FIELDS,
        "send_rollback": SEND_ROLLBACK_MANIFEST_FIELDS,
        "bind_terminal": BIND_TERMINAL_MANIFEST_FIELDS,
        "workspace_terminal": WORKSPACE_TERMINAL_MANIFEST_FIELDS,
    }[kind]
    if kind == "completion":
        _require(
            type(manifest) is dict
            and set(manifest)
            in (COMPLETION_MANIFEST_FIELDS, LEGACY_COMPLETION_MANIFEST_FIELDS),
            "invalid completion history manifest fields",
        )
        _require(
            len(_canonical(manifest)) <= MAX_ROW_BYTES,
            "completion history manifest row byte bound",
        )
    else:
        _exact(manifest, expected, f"{kind} history manifest")
    if kind == "completion" and set(manifest) == COMPLETION_MANIFEST_FIELDS:
        _require(
            all(_text(manifest[name]) for name in ("scope", "item_id", "root_id"))
            and _time(manifest["root_created_at"])
            and _hex(manifest["release_receipt"], 64),
            "history completion immutable identity",
        )
        _require(
            _hex(manifest["completed_attempt_id"], 32)
            and manifest["completed_attempt_kind"] in ATTEMPT_KINDS,
            "history completion attempt",
        )
        _require(
            _safe_int(manifest["completed_ledger_before_version"])
            and _safe_int(manifest["completed_ledger_after_version"])
            and manifest["completed_ledger_after_version"]
            >= manifest["completed_ledger_before_version"],
            "history completion versions",
        )
        _require(
            _text(manifest["completed_lease_id"])
            and _hex(manifest["completed_receipt_digest"], 64)
            and _hex(manifest["operation_digest"], 64),
            "history completion proof",
        )
    elif kind == "completion":
        _require(
            _hex(manifest["completed_attempt_id"], 32)
            and manifest["completed_attempt_kind"] in ATTEMPT_KINDS
            and _safe_int(manifest["completed_ledger_before_version"])
            and _safe_int(manifest["completed_ledger_after_version"])
            and manifest["completed_ledger_after_version"]
            >= manifest["completed_ledger_before_version"]
            and _text(manifest["completed_lease_id"])
            and _hex(manifest["completed_receipt_digest"], 64)
            and _hex(manifest["operation_digest"], 64),
            "legacy history completion proof",
        )
    elif kind in ("restore", "cancel", "send_rollback"):
        _validate_pin_manifest(manifest)
        if kind == "send_rollback":
            _require(
                _hex(manifest["rollback_receipt_digest"], 64),
                "history rollback receipt",
            )
        else:
            _require(
                _text(manifest["transition"]) and _text(manifest["outcome"]),
                "history terminal transition",
            )


def history_id(row: Mapping[str, Any]) -> str:
    return domain_digest(
        "ciel.wait-boost.history-id.v1",
        {
            "kind": row["kind"],
            "scope": row["scope"],
            "item_id": row["item_id"],
            "release_receipt": row["release_receipt"],
            "attempt_id": row["attempt_id"],
            "transition": row["transition"],
            "outcome": row["outcome"],
            "receipt_digest": row["receipt_digest"],
            "operation_digest": row["operation_digest"],
        },
    )


def _operation_ref(operation_digest: str) -> str:
    import hashlib

    return hashlib.sha256(operation_digest.encode("ascii")).hexdigest()[:12]


def _is_legacy_completion(row: Mapping[str, Any]) -> bool:
    return (
        row.get("kind") == "completion"
        and type(row.get("manifest")) is dict
        and set(row["manifest"]) == LEGACY_COMPLETION_MANIFEST_FIELDS
    )


def _legacy_history(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(row)
        for key, row in document["history"].items()
        if _is_legacy_completion(row)
    }


def validate_history(key: str, value: Any) -> None:
    row = _exact(value, HISTORY_FIELDS, "history")
    _require(key == row["history_id"] and _hex(key, 64), "history key")
    _require(
        _is_legacy_completion(row) or key == history_id(row), "history id mismatch"
    )
    _require(
        all(_text(row[name]) for name in ("scope", "item_id", "transition", "outcome")),
        "history text",
    )
    _require(
        _hex(row["release_receipt"], 64) and _hex(row["attempt_id"], 32),
        "history identity",
    )
    _require(
        row["kind"] in HISTORY_KINDS
        and _hex(row["receipt_digest"], 64)
        and _hex(row["operation_digest"], 64),
        "history proof",
    )
    result = _exact(row["result"], HISTORY_RESULT_FIELDS, "history result")
    legacy = _is_legacy_completion(row)
    _require(
        type(result["recovery_required"]) is bool and _text(result["state"]),
        "history result state",
    )
    _require(
        result["outcome"] is None or _text(result["outcome"]),
        "history result outcome grammar",
    )
    _require(_text(result["operation_ref"]), "history result operation ref")
    if not legacy:
        _require(
            result["recovery_required"] is False
            and result["outcome"] == row["outcome"]
            and result["operation_ref"] == _operation_ref(row["operation_digest"]),
            "enriched history deterministic result",
        )
    _require(_time(row["terminal_at"]), "history terminal time")
    manifest = row["manifest"]
    _validate_manifest_values(row["kind"], manifest)
    kind = row["kind"]
    if kind == "completion" and legacy:
        _require(
            row["attempt_id"] == manifest["completed_attempt_id"]
            and row["receipt_digest"] == manifest["completed_receipt_digest"]
            and row["operation_digest"] == manifest["operation_digest"],
            "legacy history completion outer identity",
        )
    elif kind == "completion":
        expected_kind = {
            "reacquired": "reacquire",
            "running_notified": "running_notify",
            "native_bound": "reserve",
        }
        _require(row["outcome"] in LEASE_OUTCOMES, "history completion outcome")
        _require(
            manifest["completed_attempt_kind"] == expected_kind[row["outcome"]],
            "history completion kind/outcome",
        )
        expected_transition = (
            "complete_bound_launch"
            if row["outcome"] == "native_bound"
            else "complete_consume"
        )
        _require(
            row["transition"] == expected_transition
            and result["state"] == "consumed"
            and result["outcome"] == row["outcome"],
            "history completion semantic vocabulary",
        )
        _require(
            row["scope"] == manifest["scope"]
            and row["item_id"] == manifest["item_id"]
            and row["release_receipt"] == manifest["release_receipt"]
            and row["attempt_id"] == manifest["completed_attempt_id"]
            and row["receipt_digest"] == manifest["completed_receipt_digest"]
            and row["operation_digest"] == manifest["operation_digest"],
            "history completion outer identity",
        )
    elif kind in ("restore", "cancel"):
        _require(
            row["scope"] == manifest["scope"]
            and row["item_id"] == manifest["item_id"]
            and row["release_receipt"] == manifest["release_receipt"]
            and row["attempt_id"] == manifest["attempt_id"]
            and row["transition"] == manifest["transition"]
            and row["outcome"] == manifest["outcome"],
            "history pin outer identity",
        )
        expected_state = "boosted" if kind == "restore" else "cancelled"
        if kind == "restore":
            _require(
                row["transition"]
                in (
                    "running_notify_failed",
                    "launch_denied",
                    "capacity_no_commit",
                    "native_absent",
                )
                and row["outcome"] == "restored_boosted",
                "history restore semantic vocabulary",
            )
        else:
            _require(
                row["transition"] == "cancel_open"
                and row["outcome"] in CANCEL_OUTCOMES,
                "history cancel semantic vocabulary",
            )
        _require(
            result["state"] == expected_state and result["outcome"] == row["outcome"],
            "history pin result state",
        )
    elif kind == "send_rollback":
        _require(
            row["scope"] == manifest["scope"]
            and row["item_id"] == manifest["item_id"]
            and row["release_receipt"] == manifest["release_receipt"]
            and row["attempt_id"] == manifest["attempt_id"]
            and row["receipt_digest"] == manifest["rollback_receipt_digest"],
            "history send rollback outer identity",
        )
        _require(
            row["transition"] == "reacquire_send_failed_rolled_back"
            and row["outcome"] == "delivery_failed_rolled_back"
            and result["state"] == "boosted"
            and result["outcome"] == row["outcome"],
            "history send rollback semantic vocabulary",
        )
    else:
        child = _terminal_child_from_history(row)
        assert child is not None
        _require(
            row["scope"] == child["scope"]
            and row["item_id"] == child["item_id"]
            and row["attempt_id"] == child["attempt_id"],
            "history child outer identity",
        )
        if child["state"] == "done" and row["kind"] == "bind_terminal":
            receipt = child["bind_receipt_digest"]
            operation = child["operation_digest"]
        elif child["state"] == "done":
            receipt = child["coord_attach_receipt_digest"]
            operation = domain_digest(
                "ciel.wait-boost.workspace-terminal.v1", dict(manifest)
            )
        else:
            receipt = child["rollback_receipt_digest"]
            operation = child["rollback_operation_digest"]
        expected_outcome = (
            "native_bound" if child["state"] == "done" else "restored_boosted"
        )
        _require(
            row["receipt_digest"] == receipt
            and row["operation_digest"] == operation
            and row["transition"] == child["state"]
            and row["outcome"] == expected_outcome
            and result["state"] == child["state"]
            and result["outcome"] == expected_outcome,
            "history child terminal proof",
        )


def _validate_collection(
    collection: Any, validator: Callable[[str, Any], None], name: str
) -> None:
    _require(
        type(collection) is dict and len(collection) <= MAX_ENTRIES, f"{name} bound"
    )
    for key, row in collection.items():
        _require(type(key) is str, f"invalid {name} key")
        validator(key, row)


def _same_identity(parent: Mapping[str, Any], child: Mapping[str, Any]) -> bool:
    return all(
        parent[name] == child[name]
        for name in ("scope", "root_id", "root_created_at", "item_id")
    )


def _history_for_attempt(
    document: Mapping[str, Any], attempt_id: str, kinds: tuple[str, ...]
) -> list[Mapping[str, Any]]:
    return [
        row
        for row in document["history"].values()
        if row["attempt_id"] == attempt_id and row["kind"] in kinds
    ]


def _parent_proof(
    document: Mapping[str, Any], child: Mapping[str, Any], *, outcome: str
) -> Mapping[str, Any] | None:
    """Resolve exact active or retained terminal parent proof for one child."""
    parent = document["rows"].get(boost_key(child["scope"], child["item_id"]))
    if parent is not None and _same_identity(parent, child):
        if outcome == "native_bound":
            if (
                parent["state"] == "consumed"
                and parent["outcome"] == outcome
                and parent["completed_attempt_id"] == child["attempt_id"]
                and parent["completed_lease_id"] == child["lease_id"]
            ):
                return {
                    "receipt_digest": parent["completed_receipt_digest"],
                    "operation_digest": parent["operation_digest"],
                    "release_receipt": parent["release_receipt"],
                }
        elif (
            parent["state"] == "boosted"
            and parent["terminal_outcome"] == outcome
            and parent["terminal_attempt_id"] == child["attempt_id"]
        ):
            return {
                "receipt_digest": parent["terminal_receipt_digest"],
                "operation_digest": parent["terminal_operation_digest"],
                "release_receipt": parent["release_receipt"],
            }
    for history in _history_for_attempt(
        document, child["attempt_id"], ("completion", "restore")
    ):
        if _is_legacy_completion(history):
            continue
        if (
            history["scope"] == child["scope"]
            and history["item_id"] == child["item_id"]
            and history["outcome"] == outcome
        ):
            manifest = history["manifest"]
            lease = (
                manifest.get("completed_lease_id")
                if history["kind"] == "completion"
                else manifest.get("lease_id")
            )
            if (
                lease == child["lease_id"]
                and manifest["root_id"] == child["root_id"]
                and manifest["root_created_at"] == child["root_created_at"]
            ):
                return history
    return None


def _cleanup_for_attempt(
    document: Mapping[str, Any], attempt_id: str
) -> Mapping[str, Any] | None:
    matches = [
        row for row in document["cleanups"].values() if row["attempt_id"] == attempt_id
    ]
    _require(len(matches) <= 1, "multiple cleanup authority rows")
    return matches[0] if matches else None


def validate(document: Any) -> None:
    _require(
        type(document) is dict and set(document) == TOP_FIELDS, "invalid sidecar fields"
    )
    _require(
        document["schema"] == SCHEMA and _safe_int(document["version"]),
        "invalid schema/version",
    )
    _validate_collection(document["rows"], validate_boost, "rows")
    _validate_collection(document["bind_sagas"], validate_bind, "bind_sagas")
    _validate_collection(document["workspaces"], validate_workspace, "workspaces")
    _validate_collection(document["cleanups"], validate_cleanup, "cleanups")
    _validate_collection(document["history"], validate_history, "history")

    # At most one open cleanup may claim one immutable source identity.
    open_sources: set[tuple[Any, ...]] = set()
    for cleanup in document["cleanups"].values():
        if cleanup["state"] != "done":
            source = tuple(
                cleanup[name]
                for name in ("scope", "item_id", "release_receipt", "attempt_id")
            )
            _require(source not in open_sources, "duplicate nonterminal cleanup")
            open_sources.add(source)

    for attempt_id, workspace in document["workspaces"].items():
        cleanup = _cleanup_for_attempt(document, attempt_id)
        if workspace["state"] == "done":
            proof = _parent_proof(document, workspace, outcome="native_bound")
            saga = document["bind_sagas"].get(attempt_id)
            _require(proof is not None, "done workspace parent proof")
            _require(
                saga is not None and saga["state"] == "done",
                "done workspace bind proof",
            )
        elif workspace["state"] == "rolled_back":
            _require(cleanup is not None, "rolled back workspace cleanup missing")
            _require(
                cleanup["state"]
                in ("workspace_removed", "lease_released", "done", "recovery_required")
                and cleanup["workspace_cleanup_receipt_digest"] is not None,
                "rolled back workspace cleanup phase",
            )
            _require(
                workspace["rollback_receipt_digest"]
                == cleanup["workspace_cleanup_receipt_digest"],
                "workspace cleanup rollback receipt",
            )
        else:
            parent = document["rows"].get(
                boost_key(workspace["scope"], workspace["item_id"])
            )
            _require(
                parent is not None and _same_identity(parent, workspace),
                "workspace parent mismatch",
            )
            saga = document["bind_sagas"].get(attempt_id)
            crash_b = (
                workspace["state"] == "coord_attached"
                and saga is not None
                and saga["state"] == "done"
                and parent["state"] == "consumed"
                and parent["outcome"] == "native_bound"
                and parent["completed_attempt_id"] == attempt_id
                and parent["completed_lease_id"] == workspace["lease_id"]
            )
            _require(
                parent["state"] == "consuming" or crash_b,
                "open workspace parent state",
            )
            if not crash_b:
                _require(
                    parent["attempt_id"] == attempt_id,
                    "workspace parent attempt mismatch",
                )
                _require(
                    parent["lease_id"] == workspace["lease_id"],
                    "workspace lease mismatch",
                )

    for attempt_id, saga in document["bind_sagas"].items():
        workspace = document["workspaces"].get(attempt_id)
        _require(
            workspace is not None and _identity(saga) == _identity(workspace),
            "bind workspace identity",
        )
        _require(
            workspace["state"]
            in ("coord_attached", "done", "recovery_required", "rolled_back"),
            "bind workspace phase",
        )
        if saga["state"] == "done":
            proof = _parent_proof(document, saga, outcome="native_bound")
            parent = document["rows"].get(boost_key(saga["scope"], saga["item_id"]))
            intermediate_a = (
                parent is not None
                and parent["state"] == "consuming"
                and parent["attempt_id"] == attempt_id
                and workspace["state"] == "coord_attached"
            )
            intermediate_b = (
                proof is not None and workspace["state"] == "coord_attached"
            )
            completed = proof is not None and workspace["state"] == "done"
            _require(
                intermediate_a or intermediate_b or completed,
                "done bind recovery state table",
            )
            if proof is not None:
                _require(
                    proof["receipt_digest"] == saga["bind_receipt_digest"]
                    and proof["operation_digest"] == saga["operation_digest"],
                    "done bind parent receipt proof",
                )
        elif saga["state"] == "rolled_back":
            cleanup = _cleanup_for_attempt(document, attempt_id)
            _require(cleanup is not None, "rolled back bind cleanup missing")
            _require(
                cleanup["state"]
                in (
                    "bind_rolled_back",
                    "coord_detached",
                    "ledger_detached",
                    "workspace_removed",
                    "lease_released",
                    "done",
                    "recovery_required",
                )
                and cleanup["bind_disposition"] == "rolled_back",
                "rolled back bind cleanup phase",
            )
            _require(
                saga["rollback_receipt_digest"]
                == cleanup["bind_rollback_receipt_digest"]
                and saga["rollback_operation_digest"]
                == cleanup["bind_rollback_operation_digest"],
                "rolled back bind cleanup proof",
            )

    for cleanup in document["cleanups"].values():
        workspace = document["workspaces"].get(cleanup["attempt_id"])
        _require(
            workspace is not None and _identity(cleanup) == _identity(workspace),
            "cleanup workspace identity",
        )
        _require(
            workspace["workspace_operation_id"] == cleanup["workspace_operation_id"],
            "cleanup workspace operation",
        )
        saga = document["bind_sagas"].get(cleanup["attempt_id"])
        _require(
            (cleanup["bind_manifest_digest"] is None) == (saga is None),
            "cleanup prepared bind authority",
        )
        if saga is not None:
            _require(
                cleanup["bind_manifest_digest"] == saga["bind_manifest_digest"],
                "cleanup bind manifest mismatch",
            )
        if cleanup["bind_disposition"] == "rolled_back":
            _require(
                saga is not None and saga["state"] == "rolled_back",
                "cleanup bind rollback state",
            )
            _require(
                cleanup["bind_rollback_receipt_digest"]
                == saga["rollback_receipt_digest"],
                "cleanup bind rollback receipt",
            )
            _require(
                cleanup["bind_rollback_operation_digest"]
                == saga["rollback_operation_digest"],
                "cleanup bind rollback operation",
            )
        elif cleanup["bind_disposition"] == "not_created":
            _require(saga is None, "cleanup fabricated no-bind")
        if cleanup["state"] == "done":
            proof = _parent_proof(document, cleanup, outcome="restored_boosted")
            _require(proof is not None, "cleanup restore parent proof")
            _require(
                proof["release_receipt"] == cleanup["release_receipt"],
                "cleanup restore release identity",
            )
            _require(
                proof["receipt_digest"] == cleanup["boost_restore_receipt_digest"]
                and proof["operation_digest"]
                == cleanup["boost_restore_operation_digest"],
                "cleanup restore proof cross-link",
            )
            _require(
                workspace["state"] == "rolled_back", "done cleanup workspace state"
            )

    for history in document["history"].values():
        if history["kind"] == "restore" and history["transition"] == "native_absent":
            cleanup = _cleanup_for_attempt(document, history["attempt_id"])
            _require(
                cleanup is not None
                and cleanup["state"] == "done"
                and cleanup["cleanup_trigger"] == "native_absent",
                "native absent restore cleanup authority",
            )
            _require(
                history["scope"] == cleanup["scope"]
                and history["item_id"] == cleanup["item_id"]
                and history["release_receipt"] == cleanup["release_receipt"]
                and history["receipt_digest"] == cleanup["boost_restore_receipt_digest"]
                and history["operation_digest"]
                == cleanup["boost_restore_operation_digest"],
                "native absent restore cleanup proof",
            )

    # A retained child history row and its terminal child are one exact proof.
    # Neither may float independently or claim a different outer identity.
    for history in document["history"].values():
        if history["kind"] not in ("bind_terminal", "workspace_terminal"):
            continue
        child_collection = (
            document["bind_sagas"]
            if history["kind"] == "bind_terminal"
            else document["workspaces"]
        )
        child = child_collection.get(history["attempt_id"])
        if child is None:
            # The retained manifest is independently reconstructed and validated
            # above; a child may already have been pruned once no live authority
            # references it.
            continue
        projection_fields = (
            BIND_TERMINAL_MANIFEST_FIELDS
            if history["kind"] == "bind_terminal"
            else WORKSPACE_TERMINAL_MANIFEST_FIELDS
        )
        _require(
            history["manifest"] == {name: child[name] for name in projection_fields}
            and history["terminal_at"] == child["terminal_at"],
            "terminal history child mismatch",
        )
        expected_outcome = (
            "native_bound" if child["state"] == "done" else "restored_boosted"
        )
        _require(history["outcome"] == expected_outcome, "terminal history outcome")
        proof = _parent_proof(document, child, outcome=expected_outcome)
        _require(proof is not None, "terminal history parent proof")
        _require(
            proof["release_receipt"] == history["release_receipt"],
            "terminal history release identity",
        )

    for history in document["history"].values():
        if (
            history["kind"] != "completion"
            or _is_legacy_completion(history)
            or history["outcome"] != "native_bound"
        ):
            continue
        saga = document["bind_sagas"].get(history["attempt_id"])
        workspace = document["workspaces"].get(history["attempt_id"])
        _require(
            saga is not None
            and saga["state"] == "done"
            and workspace is not None
            and workspace["state"] == "done",
            "completion history terminal children",
        )
        _require(
            history["scope"] == saga["scope"] == workspace["scope"]
            and history["item_id"] == saga["item_id"] == workspace["item_id"],
            "completion history child identity",
        )

    for parent in document["rows"].values():
        if parent["state"] == "consumed" and parent["outcome"] == "native_bound":
            attempt = parent["completed_attempt_id"]
            saga = document["bind_sagas"].get(attempt)
            workspace = document["workspaces"].get(attempt)
            _require(
                saga is not None
                and saga["state"] == "done"
                and workspace is not None
                and workspace["state"] in ("coord_attached", "done"),
                "native bound terminal children",
            )
            _require(
                _same_identity(parent, saga) and _same_identity(parent, workspace),
                "native bound child identity",
            )
            _require(
                parent["completed_lease_id"]
                == saga["lease_id"]
                == workspace["lease_id"],
                "native bound child lease",
            )
            _require(
                parent["completed_receipt_digest"] == saga["bind_receipt_digest"]
                and parent["operation_digest"] == saga["operation_digest"],
                "native bound completion proof",
            )

    _require(len(_canonical(document)) <= MAX_BYTES, "sidecar byte bound")


def empty_document() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "version": 0,
        "rows": {},
        "bind_sagas": {},
        "workspaces": {},
        "cleanups": {},
        "history": {},
    }


def migrate(document: Any) -> tuple[dict[str, Any], bool]:
    _require(type(document) is dict, "invalid legacy document")
    keys = frozenset(document)
    if keys == TOP_FIELDS:
        return document, False
    _require(
        keys in LEGACY_TOP_FIELDS and document.get("schema") == SCHEMA,
        "unsupported schema shape",
    )
    migrated = copy.deepcopy(document)
    migrated.setdefault("history", {})
    migrated["bind_sagas"] = {}
    migrated["workspaces"] = {}
    migrated["cleanups"] = {}
    return migrated, True


class WaitBoostStore:
    def __init__(
        self,
        path: Path,
        *,
        replace=os.replace,
        fsync=os.fsync,
        open_directory=os.open,
        close_directory=os.close,
        sync_parent_directory: bool | None = None,
    ):
        self.path = Path(path).resolve()
        self._replace = replace
        self._fsync = fsync
        self._open_directory = open_directory
        self._close_directory = close_directory
        self._sync_parent = (
            os.name != "nt" if sync_parent_directory is None else sync_parent_directory
        )

    @staticmethod
    def _object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WaitBoostError("duplicate JSON key")
            result[key] = value
        return result

    def _read_unlocked(self) -> tuple[dict[str, Any], bool]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return empty_document(), False
        except OSError as error:
            raise WaitBoostStateUnavailable("state could not be read") from error
        if len(raw) > MAX_BYTES:
            raise WaitBoostStateUnavailable("state exceeds byte limit")
        try:
            loaded = json.loads(raw.decode("utf-8"), object_pairs_hook=self._object)
            document, migrated = migrate(loaded)
            validate(document)
            return document, migrated
        except (
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            RecursionError,
        ) as error:
            raise WaitBoostStateUnavailable("invalid state; writes blocked") from error

    def read(self) -> dict[str, Any]:
        with exclusive_file_lock(self.path):
            document, _ = self._read_unlocked()
            return copy.deepcopy(document)

    def _persist(self, document: Mapping[str, Any]) -> None:
        payload = _canonical(document)
        _require(len(payload) <= MAX_BYTES, "sidecar byte bound")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            self.path.name + "." + uuid.uuid4().hex + ".tmp"
        )
        replaced = False
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                self._fsync(handle.fileno())
            self._replace(temporary, self.path)
            replaced = True
            if self._sync_parent:
                descriptor = None
                try:
                    descriptor = self._open_directory(self.path.parent, os.O_RDONLY)
                    self._fsync(descriptor)
                    self._close_directory(descriptor)
                    descriptor = None
                except BaseException as error:
                    if descriptor is not None:
                        try:
                            self._close_directory(descriptor)
                        except BaseException:
                            pass
                    raise WaitBoostCommitAmbiguous(
                        "replace committed; parent directory durability failed"
                    ) from error
        finally:
            if not replaced or temporary.exists():
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _protected_attempts(document: Mapping[str, Any]) -> set[str]:
        protected: set[str] = set()
        for row in document["rows"].values():
            if row["state"] in ("boosted", "consuming"):
                protected.update(
                    value
                    for value in (row.get("attempt_id"), row.get("terminal_attempt_id"))
                    if value
                )
        for row in document["bind_sagas"].values():
            if row["state"] in (
                "prepared",
                "ledger_bound",
                "coord_bound",
                "recovery_required",
            ):
                protected.add(row["attempt_id"])
        for row in document["workspaces"].values():
            if row["state"] in (
                "prepared",
                "ready",
                "lease_attached",
                "coord_attached",
                "recovery_required",
            ):
                protected.add(row["attempt_id"])
        for row in document["cleanups"].values():
            if row["state"] != "done":
                protected.add(row["attempt_id"])
        return protected

    @staticmethod
    def _prune_collection(
        collection: dict[str, Any], *, now: float, protected: set[str]
    ) -> None:
        terminal = [
            (key, row)
            for key, row in collection.items()
            if row.get("terminal_at") is not None
            and row.get("attempt_id") not in protected
        ]
        terminal.sort(key=lambda pair: (-float(pair[1]["terminal_at"]), str(pair[0])))
        keep = {key for key, _ in terminal[:KEEP_NEWEST]}
        for key, row in tuple(collection.items()):
            if (
                key in keep
                or row.get("terminal_at") is None
                or row.get("attempt_id") in protected
            ):
                continue
            if now - float(row["terminal_at"]) > RETENTION_SECONDS:
                del collection[key]

    @classmethod
    def _prune_document(cls, document: dict[str, Any], *, now: float) -> None:
        protected = cls._protected_attempts(document)
        # Prune terminal authority as one attempt component.  If one retained
        # member is age/newest protected, no asymmetric child/history deletion
        # is allowed.  Components without crosslinks use normal collection rules.
        referenced: dict[str, list[tuple[str, str, Mapping[str, Any]]]] = {}
        for name in ("history", "bind_sagas", "workspaces", "cleanups"):
            for key, row in document[name].items():
                if row.get("terminal_at") is not None:
                    referenced.setdefault(row["attempt_id"], []).append(
                        (name, key, row)
                    )
        keep_attempts = set(protected)
        for attempt, members in referenced.items():
            if len(members) < 2 or len({name for name, _, _ in members}) < 2:
                continue
            if any(
                now - float(row["terminal_at"]) <= RETENTION_SECONDS
                for _, _, row in members
            ):
                keep_attempts.add(attempt)
            for name in ("history", "bind_sagas", "workspaces", "cleanups"):
                newest = sorted(
                    (
                        (key, row)
                        for key, row in document[name].items()
                        if row.get("terminal_at") is not None
                    ),
                    key=lambda pair: (-float(pair[1]["terminal_at"]), str(pair[0])),
                )[:KEEP_NEWEST]
                if any(row["attempt_id"] == attempt for _, row in newest):
                    keep_attempts.add(attempt)
        for name in ("history", "bind_sagas", "workspaces", "cleanups"):
            cls._prune_collection(document[name], now=now, protected=keep_attempts)

    def _change_locked(
        self,
        expected_version: int,
        mutate: Callable[[dict[str, Any]], Any],
        *,
        replay=None,
        now: float | None = None,
    ) -> tuple[dict[str, Any], Any]:
        _require(_safe_int(expected_version), "expected version required")
        clock = time.time() if now is None else now
        _require(_time(clock), "invalid mutation clock")
        before, _ = self._read_unlocked()
        if before["version"] != expected_version:
            if replay is None:
                raise WaitBoostConflict("state changed; reread")
            return copy.deepcopy(before), copy.deepcopy(
                replay(copy.deepcopy(before))
            )
        after = copy.deepcopy(before)
        legacy_before = _legacy_history(before)
        # Admission always prunes eligible terminal data before the caller can
        # hit a collection/byte capacity check.
        self._prune_document(after, now=clock)
        eligible_pruned = set(before["history"]) - set(after["history"])
        result = mutate(after)
        _require(
            not (eligible_pruned & set(after["history"])),
            "eligible history key reuse forbidden",
        )
        legacy_after = _legacy_history(after)
        _require(
            not (set(legacy_after) - set(legacy_before)),
            "legacy completion insertion forbidden",
        )
        for key, row in legacy_before.items():
            if key in eligible_pruned:
                _require(
                    key not in after["history"],
                    "eligible history key reuse forbidden",
                )
            else:
                _require(
                    legacy_after.get(key) == row,
                    "legacy completion mutation or non-prune deletion forbidden",
                )
        for key, row in before["history"].items():
            if not _is_legacy_completion(row) and key in after["history"]:
                _require(
                    not _is_legacy_completion(after["history"][key]),
                    "enriched history replacement with legacy forbidden",
                )
        if after != before:
            _require(after["version"] < MAX_SAFE_INTEGER, "store version exhausted")
            after["version"] += 1
            validate(after)
            self._persist(after)
        return copy.deepcopy(after), copy.deepcopy(result)

    def change(
        self,
        expected_version: int,
        mutate: Callable[[dict[str, Any]], Any],
        *,
        replay=None,
        now: float | None = None,
    ) -> tuple[dict[str, Any], Any]:
        """Accepted public self-locking CAS wrapper."""
        with exclusive_file_lock(self.path):
            return self._change_locked(
                expected_version, mutate, replay=replay, now=now
            )

    def prune(
        self, *, now: float, expected_version: int
    ) -> tuple[dict[str, Any], dict[str, int]]:
        _require(_time(now), "invalid prune clock")

        def result(document: dict[str, Any]) -> dict[str, int]:
            return {
                name: len(document[name])
                for name in ("history", "bind_sagas", "workspaces", "cleanups")
            }

        return self.change(expected_version, result, now=now)

    def startup(self, *, now: float) -> dict[str, Any]:
        """Run restart pruning under CAS and return the persisted current state."""
        while True:
            current = self.read()
            try:
                state, _ = self.change(
                    current["version"], lambda document: None, now=now
                )
                return state
            except WaitBoostConflict:
                continue

    def resolve_history(
        self,
        history_id: str,
        *,
        scope: str,
        item_id: str,
        release_receipt: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Resolve an exact terminal replay, distinguishing pruned prior identity.

        The caller supplies the complete prior operation identity.  If the active
        row is now a later release/attempt and no retained record exists, this is
        a bounded expired replay, never admission of a new operation.
        """
        _require(
            _hex(history_id, 64) and _text(scope) and _text(item_id),
            "invalid history lookup",
        )
        _require(
            _hex(release_receipt, 64) and _hex(attempt_id, 32),
            "invalid history lookup identity",
        )
        document = self.read()
        found = document["history"].get(history_id)
        if found is not None:
            _require(
                (
                    found["scope"],
                    found["item_id"],
                    found["release_receipt"],
                    found["attempt_id"],
                )
                == (scope, item_id, release_receipt, attempt_id),
                "history identity conflict",
            )
            return copy.deepcopy(found)
        active = document["rows"].get(boost_key(scope, item_id))
        if active is not None:
            active_ids = {
                active.get("attempt_id"),
                active.get("completed_attempt_id"),
                active.get("terminal_attempt_id"),
            }
            if (
                active["release_receipt"] == release_receipt
                and attempt_id in active_ids
            ):
                raise WaitBoostConflict(
                    "operation remains active but history identity differs"
                )
        raise WaitBoostOperationHistoryExpired("operation_history_expired")

    def atomic_cleanup_done(
        self,
        *,
        cleanup_id_value: str,
        restored_boost: Mapping[str, Any],
        done_cleanup: Mapping[str, Any],
        expected_version: int,
        replay_manifest_digest: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically restore the parent and terminalize cleanup exactly once."""
        _require(_hex(replay_manifest_digest, 64), "invalid cleanup replay manifest")
        candidate_cleanup = copy.deepcopy(dict(done_cleanup))
        candidate_boost = copy.deepcopy(dict(restored_boost))
        validate_cleanup(cleanup_id_value, candidate_cleanup)
        key = boost_key(candidate_cleanup["scope"], candidate_cleanup["item_id"])
        validate_boost(key, candidate_boost)
        _require(
            candidate_cleanup["state"] == "done"
            and candidate_boost["state"] == "boosted",
            "cleanup done candidates",
        )
        done_edge_fields = frozenset(
            (
                "state",
                "boost_sidecar_before_version",
                "boost_sidecar_after_version",
                "boost_restore_receipt_digest",
                "boost_restore_operation_digest",
                "cleanup_operation_digest",
                "updated_at",
                "terminal_at",
            )
        )

        def exact(document: Mapping[str, Any]) -> dict[str, Any]:
            cleanup = document["cleanups"].get(cleanup_id_value)
            parent = document["rows"].get(key)
            _require(
                cleanup == candidate_cleanup and parent == candidate_boost,
                "cleanup done replay mismatch",
            )
            digest = domain_digest(
                "ciel.wait-boost.cleanup-done-replay.v1",
                {"cleanup": cleanup, "boost": parent},
            )
            _require(
                digest == replay_manifest_digest,
                "cleanup done replay manifest mismatch",
            )
            return {"cleanup": cleanup, "boost": parent}

        def apply(document: dict[str, Any]) -> dict[str, Any]:
            cleanup = document["cleanups"].get(cleanup_id_value)
            parent = document["rows"].get(key)
            _require(
                cleanup is not None and cleanup["state"] == "lease_released",
                "cleanup is not ready to restore",
            )
            _require(
                parent is not None and parent["state"] == "consuming",
                "cleanup parent is not consuming",
            )
            for name in CLEANUP_FIELDS - done_edge_fields:
                _require(
                    candidate_cleanup[name] == cleanup[name],
                    f"cleanup done rewrites prior field: {name}",
                )
            immutable_parent = BOOST_FIELDS - frozenset(
                (
                    "state",
                    *ATTEMPT_FIELDS,
                    *LAUNCH_FIELDS,
                    "outcome",
                    "consumed_at",
                    *COMPLETION_FIELDS,
                    *TERMINAL_FIELDS,
                    "updated_at",
                )
            )
            for name in immutable_parent:
                _require(
                    candidate_boost[name] == parent[name],
                    f"cleanup restore rewrites parent identity: {name}",
                )
            expected_manifest = {
                name: parent[name]
                for name in (
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
            }
            expected_manifest.update(
                transition=(
                    "launch_denied"
                    if cleanup["cleanup_trigger"] == "spawn_denied"
                    else "native_absent"
                ),
                outcome="restored_boosted",
            )
            _require(
                candidate_boost["terminal_manifest"] == expected_manifest,
                "cleanup restore terminal manifest is not exact parent pin",
            )
            _require(
                candidate_boost["terminal_attempt_id"] == parent["attempt_id"]
                and candidate_boost["terminal_transition"]
                == expected_manifest["transition"]
                and candidate_boost["terminal_outcome"] == "restored_boosted",
                "cleanup restore terminal identity",
            )
            _require(
                candidate_cleanup["boost_sidecar_before_version"] == document["version"]
                and candidate_cleanup["boost_sidecar_after_version"]
                == document["version"] + 1,
                "cleanup restore version",
            )
            document["rows"][key] = candidate_boost
            document["cleanups"][cleanup_id_value] = candidate_cleanup
            return {"cleanup": candidate_cleanup, "boost": candidate_boost}

        return self.change(expected_version, apply, replay=exact)
