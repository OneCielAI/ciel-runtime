"""Strict-CAS P0 root and launch-intent state ignored by legacy builds.

Legacy Work files keep P1-P5 only.  This sidecar is authoritative for an exact
root identity and for the launch authorization consumed by P0 admission.  It
contains no worker capability, prompt, transcript, tool input/output or token.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .work_parallel_primitives import (WorkParallelConflict, WorkParallelError,
                            WorkParallelStateUnavailable, integer, number,
                            require, text, unique_object)

SCHEMA = 1
MAX_BYTES = 2_097_152
MAX_ROOTS = 256
MAX_INTENTS = 256
MAX_SENDS = 256
MAX_CLEARS = 64
MARKER_STATES = ("active", "cleared", "ended")
SET_BY = ("user", "side_decision", "operator")
ENDED_REASONS = ("priority_changed", "root_terminal", "root_missing")
CONTINUATIONS = ("initial", "rework", "stranded_recovery")
INTENT_STATES = ("intent", "reserved", "spawning", "bound", "cancelled", "terminal")
SEND_STATES = ("prepared", "slot_acquired", "delivered", "rolled_back", "uncertain")
SEND_OPEN_STATES = frozenset(("prepared", "slot_acquired", "uncertain"))
CLEAR_STATES = ("prepared", "board_committed", "done")
CANONICAL_PRIORITIES = ("", "P1", "P2", "P3", "P4", "P5")
PINNED_INTENT_STATES = frozenset(("reserved", "spawning", "bound", "terminal"))
CAPACITY_INTENT_STATES = frozenset(("reserved", "spawning", "bound"))
OPEN_INTENT_STATES = frozenset(("intent", "reserved", "spawning", "bound"))
NONTERMINAL_CLEAR_STATES = frozenset(("prepared", "board_committed"))
INTENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
P0_DISPATCH_RE = re.compile(r"^work-p0-[0-9a-f]{32}$")


def empty_state():
    return {"schema": SCHEMA, "version": 0, "roots": {}, "intents": {}, "sends": {}, "clears": {}}


def root_key(scope, root_id):
    require(text(scope, 200) and text(root_id, 200), "P0 root identity")
    return scope + "\0" + root_id


def valid_intent_id(value):
    return type(value) is str and INTENT_ID_RE.fullmatch(value) is not None


def dispatch_name(intent_id):
    require(valid_intent_id(intent_id), "P0 intent identity must be lowercase hex32")
    value = "work-p0-" + intent_id
    require(P0_DISPATCH_RE.fullmatch(value) is not None, "P0 dispatch name")
    return value


def _root(key, row):
    require(type(row) is dict and set(row) == {
        "scope", "root_id", "root_created_at", "state", "set_at", "set_by",
        "ended_at", "ended_reason"}, "P0 root marker fields")
    require(key == root_key(row["scope"], row["root_id"]), "P0 root marker key")
    require(number(row["root_created_at"], 1e15) and number(row["set_at"], 1e15),
            "P0 root marker times")
    require(row["state"] in MARKER_STATES and row["set_by"] in SET_BY,
            "P0 root marker state")
    if row["state"] == "active":
        require(row["ended_at"] is None and row["ended_reason"] is None,
                "active P0 root cannot be ended")
    else:
        require(number(row["ended_at"], 1e15) and row["ended_reason"] in ENDED_REASONS,
                "ended P0 root reason")


def _intent(key, row):
    require(type(row) is dict and set(row) == {
        "intent_id", "scope", "root_id", "root_created_at", "item_id",
        "continuation", "predecessor_lease_id", "dispatch_name", "state",
        "lease_id", "dispatch_without_approval", "created_at", "updated_at"},
        "P0 launch intent fields")
    require(key == row["intent_id"] and valid_intent_id(key),
            "P0 launch intent identity must be lowercase hex32")
    require(all(text(row[name], 200) for name in ("scope", "root_id", "item_id")),
            "P0 launch intent scope")
    require(number(row["root_created_at"], 1e15)
            and number(row["created_at"], 1e15)
            and number(row["updated_at"], 1e15)
            and row["updated_at"] >= row["created_at"], "P0 launch intent times")
    require(row["continuation"] in CONTINUATIONS and row["state"] in INTENT_STATES,
            "P0 launch intent state")
    require(P0_DISPATCH_RE.fullmatch(row["dispatch_name"]) is not None
            and row["dispatch_name"] == dispatch_name(key),
            "P0 launch dispatch name")
    require(row["dispatch_without_approval"] is True, "P0 approval bypass marker")
    predecessor = row["predecessor_lease_id"]
    require((row["continuation"] == "initial" and predecessor is None)
            or (row["continuation"] != "initial" and text(predecessor, 200)),
            "P0 predecessor identity")
    lease_id = row["lease_id"]
    require(lease_id is None or text(lease_id, 200), "P0 lease identity")
    require((row["state"] == "intent" and lease_id is None)
            or row["state"] == "cancelled"
            or (row["state"] in ("reserved", "spawning", "bound", "terminal")
                and lease_id is not None), "P0 launch intent lease binding")


def _send(key, row):
    fields = {"send_id", "parent_intent_id", "scope", "root_id", "item_id", "lease_id",
              "host_id", "native_name", "state", "send_receipt", "child_generation",
              "slot_before_version", "slot_after_version", "rollback_receipt_digest",
              "outcome", "operation_digest", "created_at", "updated_at"}
    require(type(row) is dict and set(row) == fields, "P0 reacquire send fields")
    require(key == row["send_id"] and valid_intent_id(key)
            and valid_intent_id(row["parent_intent_id"]), "P0 reacquire send identity")
    require(all(text(row[name], 200) for name in (
        "scope", "root_id", "item_id", "lease_id", "host_id", "native_name")),
        "P0 reacquire send relation")
    require(row["state"] in SEND_STATES and _hex64(row["send_receipt"])
            and integer(row["child_generation"], 1)
            and integer(row["slot_before_version"]), "P0 reacquire send state")
    require(row["slot_after_version"] is None or integer(row["slot_after_version"]),
            "P0 reacquire send slot version")
    require((row["state"] == "prepared" and row["slot_after_version"] is None)
            or (row["state"] != "prepared" and row["slot_after_version"] is not None),
            "P0 reacquire send slot transition")
    require(row["rollback_receipt_digest"] is None or _hex64(row["rollback_receipt_digest"]),
            "P0 reacquire rollback receipt")
    require(row["operation_digest"] is None or _hex64(row["operation_digest"]),
            "P0 reacquire operation digest")
    if row["state"] == "delivered":
        require(row["outcome"] == "delivered", "P0 delivered send outcome")
    elif row["state"] == "rolled_back":
        require(row["outcome"] == "delivery_failed_rolled_back"
                and row["rollback_receipt_digest"] is not None
                and row["operation_digest"] is not None, "P0 rolled back send outcome")
    else:
        require(row["outcome"] is None, "open P0 send cannot have outcome")
    require(number(row["created_at"], 1e15) and number(row["updated_at"], 1e15)
            and row["updated_at"] >= row["created_at"], "P0 reacquire send times")


def _hex64(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _clear(key, row):
    require(type(row) is dict and set(row) == {
        "clear_id", "scope", "root_id", "root_created_at", "target_priority",
        "state", "board_version_before", "set_by", "operation_digest", "created_at"},
        "P0 priority clear fields")
    require(key == row["clear_id"] and text(key, 200), "P0 priority clear identity")
    require(text(row["scope"], 200) and text(row["root_id"], 200)
            and number(row["root_created_at"], 1e15), "P0 priority clear root")
    require(row["target_priority"] in CANONICAL_PRIORITIES
            and row["state"] in CLEAR_STATES and row["set_by"] in SET_BY
            and _hex64(row["operation_digest"]), "P0 priority clear state")
    require(integer(row["board_version_before"]) and number(row["created_at"], 1e15),
            "P0 priority clear version/time")


def validate(state):
    require(type(state) is dict and set(state) == {"schema", "version", "roots", "intents", "sends", "clears"},
            "P0 state fields")
    require(state["schema"] == SCHEMA and integer(state["version"]), "P0 schema/version")
    require(type(state["roots"]) is dict and len(state["roots"]) <= MAX_ROOTS, "P0 root bound")
    require(type(state["intents"]) is dict and len(state["intents"]) <= MAX_INTENTS,
            "P0 intent bound")
    require(type(state["sends"]) is dict and len(state["sends"]) <= MAX_SENDS,
            "P0 reacquire send bound")
    require(type(state["clears"]) is dict and len(state["clears"]) <= MAX_CLEARS,
            "P0 clear bound")
    for key, row in state["roots"].items():
        _root(key, row)
    for key, row in state["intents"].items():
        _intent(key, row)
    by_lease = {}
    for row in state["intents"].values():
        # A cancelled intent never launched a fork; deterministic replacement
        # leases (rework / stranded recovery) reuse their lease_id when
        # re-reserved after an unbound reservation was orphan-swept.
        if row["lease_id"] is not None and row["state"] != "cancelled":
            by_lease.setdefault(row["lease_id"], []).append(row)
    require(all(len(rows) <= 1 for rows in by_lease.values()),
            "multiple original P0 intents for one lease")
    sends_by_parent = {}
    for key, row in state["sends"].items():
        _send(key, row)
        sends_by_parent.setdefault((row["parent_intent_id"], row["lease_id"]), []).append(row)
        parent = state["intents"].get(row["parent_intent_id"])
        require(parent is not None and parent["state"] == "bound"
                and parent["lease_id"] == row["lease_id"]
                and parent["scope"] == row["scope"] and parent["root_id"] == row["root_id"]
                and parent["item_id"] == row["item_id"], "P0 reacquire parent intent invalid")
    for rows in sends_by_parent.values():
        require(sum(row["state"] in SEND_OPEN_STATES for row in rows) <= 1,
                "multiple open P0 reacquire sends")
    for key, row in state["clears"].items():
        _clear(key, row)
    # Cross-store operation invariant: one nonterminal op per exact root, paired
    # with the marker written in the same CAS. Direction is marker state only.
    pending = {}
    by_root = {}
    for row in state["clears"].values():
        identity = (row["scope"], row["root_id"], row["root_created_at"])
        by_root.setdefault(identity, []).append(row)
        if row["state"] not in NONTERMINAL_CLEAR_STATES:
            continue
        require(identity not in pending, "multiple nonterminal P0 priority operations")
        pending[identity] = row
        marker = state["roots"].get(root_key(row["scope"], row["root_id"]))
        require(marker is not None and marker["root_created_at"] == row["root_created_at"],
                "P0 priority operation marker missing")
        if marker["state"] == "active":
            require(row["target_priority"] == "P1", "active P0 operation must target P1")
        elif marker["state"] == "cleared":
            require(row["target_priority"] in CANONICAL_PRIORITIES,
                    "cleared P0 operation target")
        else:
            raise WorkParallelError("P0 priority operation marker state")
    # The newest operation for every active/cleared marker is retained forever;
    # this makes a missing marker/op pair distinguishable from legal pruning.
    for marker in state["roots"].values():
        if marker["state"] not in ("active", "cleared"):
            continue
        identity = (marker["scope"], marker["root_id"], marker["root_created_at"])
        operations = by_root.get(identity, [])
        require(operations, "P0 root marker operation missing")
        newest = max(operations, key=lambda value: (value["created_at"], value["clear_id"]))
        if marker["state"] == "active":
            require(newest["target_priority"] == "P1",
                    "active P0 marker latest operation must target P1")


class WorkParallelP0CommitAmbiguous(WorkParallelStateUnavailable):
    """Target replace succeeded but parent-directory durability was not confirmed."""


class P0ReadView:
    def __init__(self, state):
        validate(state)
        self._state = copy.deepcopy(state)

    def effective_root_priority(self, scope, root):
        marker = WorkParallelP0Store.matching_active_marker(
            self._state, scope, root.get("id"), root.get("created_at"))
        return ("P0" if marker is not None and root.get("status") != "done"
                else root.get("priority", ""))

    def canonical_changed(self, scope, root):
        return (self.effective_root_priority(scope, root) == "P0"
                and root.get("priority", "") != "P1")

    def lease_is_p0(self, lease_id):
        return any(row["lease_id"] == lease_id and row["state"] in PINNED_INTENT_STATES
                   for row in self._state["intents"].values())

    def open_intent_for_dispatch(self, name):
        rows = [copy.deepcopy(row) for row in self._state["intents"].values()
                if row["dispatch_name"] == name and row["state"] in OPEN_INTENT_STATES]
        require(len(rows) <= 1, "multiple P0 dispatch intents")
        return rows[0] if rows else None


class WorkParallelP0Store:
    """Strict-CAS sidecar; corruption blocks P0 writes, not legacy files."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    @classmethod
    def beside(cls, ledger_path):
        return cls(Path(ledger_path).with_name("work-parallel-p0.json"))

    def _read(self):
        try:
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return empty_state()
        if len(raw) > MAX_BYTES:
            raise WorkParallelStateUnavailable("P0 byte limit")
        try:
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            validate(state)
            return state
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError,
                RecursionError) as error:
            raise WorkParallelStateUnavailable(
                "corrupt Work P0 state; P0 writes blocked") from error

    def read_snapshot(self):
        """Validated lock-free snapshot for a ledger lock's admission decision."""
        return self._read()

    def read(self):
        if not self.path.exists():
            return empty_state()
        with self._lock, exclusive_file_lock(self.path):
            return self._read()

    def read_view(self):
        return P0ReadView(self.read())

    @staticmethod
    def _sync_parent(path):
        if os.name == "nt":
            return
        directory = os.open(os.path.dirname(os.fspath(path)), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _change_locked(self, fn, expected_version):
        """Exact CAS body for callers already holding the P0 process/file locks."""
        require(integer(expected_version), "expected_p0_version required")
        before = self._read()
        if before["version"] != expected_version:
            raise WorkParallelConflict("Work P0 state changed; reread")
        state = copy.deepcopy(before)
        result = fn(state)
        if state != before:
            state["version"] += 1
            validate(state)
            raw = json.dumps(state, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
            require(len(raw) <= MAX_BYTES, "P0 byte limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with tmp.open("xb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                for attempt in range(20):
                    try:
                        os.replace(tmp, self.path)
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(.002)
                try:
                    self._sync_parent(self.path)
                except OSError as error:
                    raise WorkParallelP0CommitAmbiguous(
                        "Work P0 state commit durability unknown; recovery required") from error
            finally:
                tmp.unlink(missing_ok=True)
        return copy.deepcopy(state), copy.deepcopy(result)

    def _change(self, fn, expected_version):
        with self._lock, exclusive_file_lock(self.path):
            return self._change_locked(fn, expected_version)

    @staticmethod
    def matching_active_marker(state, scope, root_id, root_created_at):
        marker = state.get("roots", {}).get(root_key(scope, root_id))
        return (marker if marker is not None and marker["state"] == "active"
                and marker["root_created_at"] == root_created_at else None)

    def recover_priority_operations(self, board_store):
        """Roll every nonterminal operation forward from its marker+op pair only."""
        recovered = []
        state = self.read()
        for row in sorted(state["clears"].values(), key=lambda value: (
                value["created_at"], value["clear_id"])):
            if row["state"] in NONTERMINAL_CLEAR_STATES:
                recovered.append(self.roll_forward_priority(row["clear_id"], board_store))
        return recovered

    def sweep_roots(self, scope, board, *, expected_version, now=None):
        stamp = time.time() if now is None else now
        roots = {row["id"]: row for row in board.get("nodes", [])
                 if row.get("parent_id") is None}
        def apply(state):
            changed = 0
            for marker in state["roots"].values():
                if marker["scope"] != scope or marker["state"] != "active":
                    continue
                root = roots.get(marker["root_id"])
                reason = None
                if root is None or root.get("created_at") != marker["root_created_at"]:
                    reason = "root_missing"
                elif root.get("status") == "done":
                    reason = "root_terminal"
                if reason:
                    marker.update(state="ended", ended_at=stamp, ended_reason=reason)
                    changed += 1
            return changed
        return self._change(apply, expected_version)

    def effective_priority(self, scope, root, canonical_priority=None):
        require(type(root) is dict and text(root.get("id"), 200), "P0 effective root")
        canonical = root.get("priority", "") if canonical_priority is None else canonical_priority
        require(canonical in CANONICAL_PRIORITIES, "canonical priority")
        # A pre-created_at board cannot identity-match a marker and remains legacy.
        # Never invent an identity from updated_at here: delete/recreate isolation
        # depends on the exact durable creation time.
        if not number(root.get("created_at"), 1e15):
            return canonical
        try:
            state = self.read()
        except WorkParallelStateUnavailable:
            return canonical
        marker = self.matching_active_marker(state, scope, root["id"], root["created_at"])
        return "P0" if marker is not None and root.get("status") != "done" else canonical

    def activate_root(self, scope, root_id, root_created_at, *, set_by, expected_version,
                      now=None):
        stamp = time.time() if now is None else now
        require(set_by in SET_BY and number(stamp, 1e15) and number(root_created_at, 1e15),
                "P0 root activation")
        key = root_key(scope, root_id)
        def apply(state):
            prior = state["roots"].get(key)
            if (prior is not None and prior["state"] == "active"
                    and prior["root_created_at"] == root_created_at):
                return prior
            require(prior is None or prior["root_created_at"] == root_created_at
                    or prior["state"] != "active", "another P0 root identity is active")
            require(len(state["roots"]) < MAX_ROOTS or key in state["roots"], "P0 root bound")
            marker = {"scope": scope, "root_id": root_id,
                      "root_created_at": root_created_at, "state": "active",
                      "set_at": stamp, "set_by": set_by,
                      "ended_at": None, "ended_reason": None}
            state["roots"][key] = marker
            # Compatibility helper for already-canonical P1 fixtures/callers.
            # Production activation uses prepare_set and its prepared crash row.
            operation_id = uuid.uuid4().hex
            state["clears"][operation_id] = {
                "clear_id": operation_id, "scope": scope, "root_id": root_id,
                "root_created_at": root_created_at, "target_priority": "P1",
                "state": "done", "board_version_before": 0, "set_by": set_by,
                "operation_digest": hashlib.sha256(operation_id.encode()).hexdigest(),
                "created_at": stamp}
            return marker
        return self._change(apply, expected_version)

    def prepare_set(self, scope, root_id, root_created_at, board_version, *, set_by,
                    expected_version, operation_id=None, operation_digest=None, now=None):
        """Marker-first P0 set; the existing operation row durably tracks board P1."""
        stamp = time.time() if now is None else now
        operation_id = operation_id or uuid.uuid4().hex
        operation_digest = operation_digest or hashlib.sha256(operation_id.encode()).hexdigest()
        require(set_by in SET_BY and _hex64(operation_digest) and integer(board_version)
                and number(stamp, 1e15) and number(root_created_at, 1e15),
                "P0 priority set")
        key = root_key(scope, root_id)
        def apply(state):
            prior = state["clears"].get(operation_id)
            row = {"clear_id": operation_id, "scope": scope, "root_id": root_id,
                   "root_created_at": root_created_at, "target_priority": "P1",
                   "state": "prepared", "board_version_before": board_version,
                   "set_by": set_by, "operation_digest": operation_digest,
                   "created_at": stamp}
            if prior is not None:
                require(prior == row, "P0 priority set changed")
                return prior
            open_rows = [value for value in state["clears"].values()
                         if value["scope"] == scope and value["root_id"] == root_id
                         and value["root_created_at"] == root_created_at
                         and value["state"] in NONTERMINAL_CLEAR_STATES]
            if open_rows:
                require(len(open_rows) == 1 and open_rows[0]["target_priority"] == "P1",
                        "conflicting P0 priority operation")
                return open_rows[0]
            require(len(state["clears"]) < MAX_CLEARS, "P0 clear bound")
            marker = state["roots"].get(key)
            if (marker is not None and marker["state"] == "active"
                    and marker["root_created_at"] == root_created_at):
                state["clears"][operation_id] = row
                return row
            require(marker is None or marker["root_created_at"] == root_created_at
                    or marker["state"] != "active", "another P0 root identity is active")
            require(len(state["roots"]) < MAX_ROOTS or key in state["roots"], "P0 root bound")
            state["roots"][key] = {
                "scope": scope, "root_id": root_id,
                "root_created_at": root_created_at, "state": "active",
                "set_at": stamp, "set_by": set_by,
                "ended_at": None, "ended_reason": None}
            state["clears"][operation_id] = row
            return row
        return self._change(apply, expected_version)

    def prepare_clear(self, scope, root_id, root_created_at, target_priority, board_version,
                      *, expected_version, clear_id=None, set_by="user",
                      operation_digest=None, now=None):
        stamp = time.time() if now is None else now
        clear_id = clear_id or uuid.uuid4().hex
        operation_digest = operation_digest or hashlib.sha256(clear_id.encode()).hexdigest()
        require(target_priority in CANONICAL_PRIORITIES and set_by in SET_BY
                and _hex64(operation_digest) and integer(board_version)
                and number(stamp, 1e15), "P0 priority clear")
        key = root_key(scope, root_id)
        def apply(state):
            marker = state["roots"].get(key)
            require(marker is not None and marker["state"] == "active"
                    and marker["root_created_at"] == root_created_at,
                    "P0 root marker is not active")
            prior = state["clears"].get(clear_id)
            row = {"clear_id": clear_id, "scope": scope, "root_id": root_id,
                   "root_created_at": root_created_at, "target_priority": target_priority,
                   "state": "prepared", "board_version_before": board_version,
                   "set_by": set_by, "operation_digest": operation_digest,
                   "created_at": stamp}
            if prior is not None:
                require(prior == row, "P0 priority clear changed")
                return prior
            open_rows = [value for value in state["clears"].values()
                         if value["scope"] == scope and value["root_id"] == root_id
                         and value["root_created_at"] == root_created_at
                         and value["state"] in NONTERMINAL_CLEAR_STATES]
            if open_rows:
                require(len(open_rows) == 1
                        and open_rows[0]["target_priority"] == target_priority,
                        "conflicting P0 priority operation")
                return open_rows[0]
            require(len(state["clears"]) < MAX_CLEARS, "P0 clear bound")
            state["clears"][clear_id] = row
            marker.update(state="cleared", ended_at=stamp,
                          ended_reason="priority_changed")
            return row
        return self._change(apply, expected_version)

    def roll_forward_priority(self, clear_id, board_store):
        """Finish one marker-first priority clear without touching a recreated root."""
        row = self.read()["clears"].get(clear_id)
        require(row is not None, "unknown P0 priority clear")
        if row["state"] == "done":
            return row
        if row["state"] == "prepared":
            state = self.read()
            marker = state["roots"].get(root_key(row["scope"], row["root_id"]))
            require(marker is not None
                    and marker["root_created_at"] == row["root_created_at"]
                    and ((marker["state"] == "active" and row["target_priority"] == "P1")
                         or marker["state"] == "cleared"),
                    "P0 priority operation marker mismatch")
            from . import work_plan_tree as tree
            for attempt in range(3):
                board = board_store.read()
                root = next((node for node in board.get("nodes", [])
                             if node.get("id") == row["root_id"]
                             and node.get("parent_id") is None), None)
                require(root is not None and root.get("created_at") == row["root_created_at"],
                        "P0 priority operation root identity changed")
                try:
                    board_store.transaction(
                        lambda document: tree.update_node(
                            document, row["root_id"], priority=row["target_priority"]),
                        expected_version=board["version"])
                    break
                except Exception as error:
                    # Board-store conflicts carry a stable class but importing it
                    # here would couple this new sidecar to a particular frontend.
                    if attempt == 2 or "plan changed" not in str(error):
                        raise
            state = self.read()
            _, row = self.mark_clear(clear_id, "board_committed",
                                     expected_version=state["version"])
        if row["state"] == "board_committed":
            state = self.read()
            _, row = self.mark_clear(clear_id, "done", expected_version=state["version"])
        # Retain a bounded completed tail; pruning never precedes durable done.
        self.prune_done(expected_version=self.read()["version"])
        return row

    # Compatibility name for callers that describe only the clear case.
    roll_forward_clear = roll_forward_priority

    def prune_done(self, *, expected_version, keep=32):
        require(integer(keep, 0, MAX_CLEARS), "P0 priority retention")
        def apply(state):
            latest = {}
            for row in state["clears"].values():
                identity = (row["scope"], row["root_id"], row["root_created_at"])
                prior = latest.get(identity)
                if prior is None or (row["created_at"], row["clear_id"]) > (
                        prior["created_at"], prior["clear_id"]):
                    latest[identity] = row
            protected = {row["clear_id"] for row in latest.values()}
            done = sorted((row for row in state["clears"].values()
                           if row["state"] in ("done", "cancelled")
                           and row["clear_id"] not in protected),
                          key=lambda row: (row["created_at"], row["clear_id"]))
            remove = done[:-keep] if keep else done
            for row in remove:
                state["clears"].pop(row["clear_id"], None)
            return len(remove)
        return self._change(apply, expected_version)

    def mark_clear(self, clear_id, target_state, *, expected_version):
        require(target_state in ("board_committed", "done"), "P0 priority clear transition")
        def apply(state):
            row = state["clears"].get(clear_id)
            require(row is not None, "unknown P0 priority clear")
            if row["state"] == target_state or row["state"] == "done":
                return row
            require((row["state"], target_state) in (("prepared", "board_committed"),
                                                       ("board_committed", "done")),
                    "P0 priority clear transition")
            row["state"] = target_state
            return row
        return self._change(apply, expected_version)

    def has_key_dependency(self):
        """Read under the sidecar lock for key-creation fencing."""
        with self._lock, exclusive_file_lock(self.path):
            state = self._read()
            return (any(row["state"] in OPEN_INTENT_STATES for row in state["intents"].values())
                    or any(row["state"] in SEND_OPEN_STATES for row in state["sends"].values()))

    def _create_intent_mutation(self, facts, *, now=None):
        require(type(facts) is dict and set(facts) == {
            "scope", "root_id", "root_created_at", "item_id", "continuation",
            "predecessor_lease_id"}, "P0 intent facts")
        stamp = time.time() if now is None else now
        intent_id = uuid.uuid4().hex
        require(valid_intent_id(intent_id) and number(stamp, 1e15), "P0 intent clock")
        def apply(state):
            marker = self.matching_active_marker(state, facts["scope"], facts["root_id"],
                                                 facts["root_created_at"])
            require(marker is not None, "P0 root marker is not active")
            matches = [row for row in state["intents"].values()
                       if row["scope"] == facts["scope"]
                       and row["item_id"] == facts["item_id"]
                       and row["continuation"] == facts["continuation"]
                       and row["state"] in OPEN_INTENT_STATES]
            if matches:
                require(len(matches) == 1, "multiple open P0 intents")
                prior = matches[0]
                require(all(prior[key] == value for key, value in facts.items()),
                        "open P0 intent facts changed")
                return prior
            if len(state["intents"]) >= MAX_INTENTS:
                # A cancelled intent cannot authorize a worker or parent a
                # reacquire send. Keep bound and terminal approval evidence.
                oldest = min((r for r in state["intents"].values() if r["state"] == "cancelled"),
                             key=lambda r: (r["updated_at"], r["intent_id"]), default=None)
                if oldest is not None:
                    del state["intents"][oldest["intent_id"]]
            require(len(state["intents"]) < MAX_INTENTS, "P0 intent bound")
            row = {"intent_id": intent_id, **copy.deepcopy(facts),
                   "dispatch_name": dispatch_name(intent_id), "state": "intent",
                   "lease_id": None, "dispatch_without_approval": True,
                   "created_at": stamp, "updated_at": stamp}
            _intent(intent_id, row)
            state["intents"][intent_id] = row
            return row
        return apply

    def create_intent(self, facts, *, expected_version, now=None, ledger_path=None):
        """Create a key-dependent P0 intent through the shared global fence."""
        from .work_assignment_key_coordinator import (
            AssignmentKeyCoordinator,
            P0StoreDependencyReader,
            WaitStoreDependencyReader,
        )
        from .work_parallel_wait_boost import WaitBoostStore
        ledger = Path(ledger_path or self.path.with_name("work-parallel.json"))
        wait_store = WaitBoostStore(ledger.with_name("work-parallel-wait-boost-v1.json"))
        coordinator = AssignmentKeyCoordinator(
            ledger,
            p0_reader=P0StoreDependencyReader(self),
            wait_reader=WaitStoreDependencyReader(wait_store),
        )
        return coordinator.create_p0_intent(
            self, facts, expected_version=expected_version, now=now
        )

    def prepare_reacquire_send(self, parent_intent_id, lease, *, ledger_before_version,
                               expected_version, send_id=None, send_receipt=None, now=None):
        stamp = time.time() if now is None else now
        send_id = uuid.uuid4().hex if send_id is None else send_id
        send_receipt = secrets.token_hex(32) if send_receipt is None else send_receipt
        require(valid_intent_id(send_id) and _hex64(send_receipt)
                and integer(ledger_before_version) and type(lease) is dict,
                "P0 reacquire send preparation")
        native_name = lease.get("agent_name") or lease.get("dispatch_name")
        rework_native = re.fullmatch(r"work-rework-(.{1,200})-a([1-9][0-9]{0,2})\Z",
                                     str(native_name or ""), re.DOTALL)
        require(text(lease.get("id"), 200) and text(lease.get("scope"), 200)
                and text(lease.get("item_id"), 200) and text(lease.get("host_id"), 200)
                and text(native_name, 200) and lease.get("state") == "waiting_input"
                and lease.get("slot") is False, "P0 reacquire exact waiting lease")
        def apply(state):
            parent = state["intents"].get(parent_intent_id)
            require(parent is not None and parent["state"] == "bound"
                    and parent["lease_id"] == lease["id"]
                    and parent["scope"] == lease["scope"]
                    and parent["item_id"] == lease["item_id"]
                    and (parent["dispatch_name"] == native_name
                         or (parent["continuation"] in ("rework", "stranded_recovery")
                             and rework_native is not None
                             and rework_native.group(1) == lease["id"])),
                    "P0 reacquire parent authority")
            open_rows = [value for value in state["sends"].values()
                         if value["parent_intent_id"] == parent_intent_id
                         and value["lease_id"] == lease["id"]
                         and value["state"] in SEND_OPEN_STATES]
            if open_rows:
                require(len(open_rows) == 1 and open_rows[0]["state"] == "prepared"
                        and open_rows[0]["slot_before_version"] == ledger_before_version
                        and open_rows[0]["host_id"] == lease["host_id"]
                        and open_rows[0]["native_name"] == native_name,
                        "P0 reacquire send already open")
                return open_rows[0]
            prior = state["sends"].get(send_id)
            generation = 1 + max((row["child_generation"] for row in state["sends"].values()
                                  if row["parent_intent_id"] == parent_intent_id), default=0)
            row = {"send_id": send_id, "parent_intent_id": parent_intent_id,
                   "scope": parent["scope"], "root_id": parent["root_id"],
                   "item_id": parent["item_id"], "lease_id": lease["id"],
                   "host_id": lease["host_id"], "native_name": native_name,
                   "state": "prepared", "send_receipt": send_receipt,
                   "child_generation": generation, "slot_before_version": ledger_before_version,
                   "slot_after_version": None, "rollback_receipt_digest": None,
                   "outcome": None, "operation_digest": None,
                   "created_at": stamp, "updated_at": stamp}
            if prior is not None:
                require(prior == row, "P0 reacquire send request changed")
                return prior
            require(len(state["sends"]) < MAX_SENDS, "P0 reacquire send bound")
            state["sends"][send_id] = row
            return row
        return self._change(apply, expected_version)

    def mark_reacquire_slot(self, send_id, ledger_after_version, *, expected_version, now=None):
        stamp = time.time() if now is None else now
        require(integer(ledger_after_version), "P0 reacquire slot version")
        def apply(state):
            row = state["sends"].get(send_id)
            require(row is not None, "unknown P0 reacquire send")
            if row["state"] == "slot_acquired":
                require(row["slot_after_version"] == ledger_after_version,
                        "P0 reacquire slot replay changed")
                return row
            require(row["state"] == "prepared"
                    and ledger_after_version == row["slot_before_version"] + 1,
                    "P0 reacquire slot transition")
            row.update(state="slot_acquired", slot_after_version=ledger_after_version,
                       updated_at=stamp)
            return row
        return self._change(apply, expected_version)

    def complete_reacquire_send(self, send_id, *, outcome, expected_version,
                                rollback_receipt_digest=None, operation_digest=None, now=None):
        stamp = time.time() if now is None else now
        require(outcome in ("sent", "failed", "uncertain"), "P0 reacquire outcome")
        def apply(state):
            row = state["sends"].get(send_id)
            require(row is not None, "unknown P0 reacquire send")
            target = {"sent": "delivered", "failed": "rolled_back",
                      "uncertain": "uncertain"}[outcome]
            final_outcome = {"sent": "delivered", "failed": "delivery_failed_rolled_back",
                             "uncertain": None}[outcome]
            if row["state"] == target:
                require(row["outcome"] == final_outcome
                        and row["rollback_receipt_digest"] == rollback_receipt_digest
                        and row["operation_digest"] == operation_digest,
                        "P0 reacquire completion replay changed")
                return row
            require(row["state"] in ("slot_acquired", "uncertain"),
                    "P0 reacquire send is not completable")
            if outcome == "failed":
                require(_hex64(rollback_receipt_digest) and _hex64(operation_digest),
                        "P0 reacquire rollback proof")
            else:
                require(rollback_receipt_digest is None
                        and operation_digest is None, "P0 reacquire unexpected rollback proof")
            row.update(state=target, outcome=final_outcome,
                       rollback_receipt_digest=rollback_receipt_digest,
                       operation_digest=operation_digest, updated_at=stamp)
            return row
        return self._change(apply, expected_version)

    def reserve_intent(self, intent_id, lease_id, *, expected_version, now=None):
        stamp = time.time() if now is None else now
        require(text(lease_id, 200) and number(stamp, 1e15), "P0 intent reserve")
        def apply(state):
            row = state["intents"].get(intent_id)
            require(row is not None, "unknown P0 launch intent")
            if row["state"] in PINNED_INTENT_STATES and row["lease_id"] == lease_id:
                return row
            require(row["state"] == "intent" and row["lease_id"] is None,
                    "P0 launch intent is not reservable")
            row.update(state="reserved", lease_id=lease_id, updated_at=stamp)
            return row
        return self._change(apply, expected_version)

    def adopt_reserved(self, intent_id, leases, *, expected_version, now=None):
        """Bind one crash-window lease selected only by exact durable dispatch identity."""
        require(type(leases) is dict, "P0 adoption leases")
        state = self.read()
        row = state["intents"].get(intent_id)
        require(row is not None and row["state"] == "intent" and row["lease_id"] is None,
                "P0 launch intent is not adoptable")
        matches = [lease for lease in leases.values()
                   if lease.get("state") == "reserved" and lease.get("host_id") is None
                   and lease.get("scope") == row["scope"] and lease.get("item_id") == row["item_id"]
                   and lease.get("dispatch_name") == row["dispatch_name"]]
        require(len(matches) <= 1, "multiple exact P0 reservations; refusing adoption")
        if not matches:
            return state, None
        return self.reserve_intent(intent_id, matches[0]["id"],
                                   expected_version=expected_version, now=now)

    def release_reservation(self, intent_id, lease_id, *, expected_version, now=None):
        """Return a reserved intent to ``intent`` once its lease was released unbound.

        Only from ``reserved``: the ``spawning`` transition precedes every native
        spawn, so a reserved intent has started no fork and its exact dispatch
        name is still unused. The next attempt reuses this same intent, so a
        repeated failure before launch never adds rows to this bounded file.
        """
        stamp = time.time() if now is None else now
        require(text(lease_id, 200) and number(stamp, 1e15), "P0 intent release")
        def apply(state):
            row = state["intents"].get(intent_id)
            require(row is not None, "unknown P0 launch intent")
            if row["state"] == "intent" and row["lease_id"] is None:
                return row
            require(row["state"] == "reserved" and row["lease_id"] == lease_id,
                    "P0 launch intent is not an unbound reservation")
            row.update(state="intent", lease_id=None, updated_at=stamp)
            return row
        return self._change(apply, expected_version)

    def close_lease_intents(self, lease_id, *, expected_version, now=None):
        stamp = time.time() if now is None else now
        require(text(lease_id, 200) and number(stamp, 1e15), "P0 terminal lease")
        def apply(state):
            closed = []
            for row in state["intents"].values():
                if row["lease_id"] != lease_id or row["state"] in ("terminal", "cancelled"):
                    continue
                row.update(state="terminal", updated_at=stamp)
                closed.append(row["intent_id"])
            # A reacquire send names its bound intent as its parent (validate).
            # The lease ended, so its sends are ended with it; otherwise this
            # write was refused as "P0 reacquire parent intent invalid" and every
            # P0 store write after it failed (Celly 2026-11-20, n1041 orphaned).
            if closed:
                for send in [key for key, row in state["sends"].items()
                             if row["lease_id"] == lease_id and row["parent_intent_id"] in closed]:
                    state["sends"].pop(send)
            return closed
        return self._change(apply, expected_version)

    def transition_intent(self, intent_id, target, *, expected_version, now=None):
        require(target in ("spawning", "bound", "cancelled", "terminal"),
                "P0 intent transition")
        stamp = time.time() if now is None else now
        require(number(stamp, 1e15), "P0 intent transition clock")
        edges = {"intent": {"bound", "cancelled"}, "reserved": {"spawning", "cancelled"},
                 "spawning": {"bound", "cancelled"},
                 "bound": {"terminal"}, "cancelled": set(), "terminal": set()}
        def apply(state):
            row = state["intents"].get(intent_id)
            require(row is not None, "unknown P0 launch intent")
            if row["state"] == target:
                return row
            require(target in edges[row["state"]], "P0 intent transition")
            row.update(state=target, updated_at=stamp)
            return row
        return self._change(apply, expected_version)

    def active_lease_ids(self, state=None):
        state = self.read_snapshot() if state is None else state
        validate(state)
        return {row["lease_id"] for row in state["intents"].values()
                if row["state"] in CAPACITY_INTENT_STATES and row["lease_id"] is not None}

    def exact_intent(self, candidate, *, states=None):
        """Return the current exact row; reject stale or caller-fabricated intent data."""
        require(type(candidate) is dict and text(candidate.get("intent_id"), 200),
                "P0 bypass requires an exact launch intent")
        state = self.read_snapshot()
        current = state["intents"].get(candidate["intent_id"])
        require(current == candidate, "P0 bypass requires an exact launch intent")
        if states is not None:
            require(current["state"] in states, "P0 launch intent state")
        # The marker must authorize reservation. Once a lease is pinned, a later
        # explicit priority change never revokes that already-launched generation.
        if current["state"] == "intent":
            marker = self.matching_active_marker(state, current["scope"], current["root_id"],
                                                 current["root_created_at"])
            require(marker is not None, "P0 root marker is not active")
        return current

    def open_send_for_lease(self, lease_id):
        require(text(lease_id, 200), "P0 reacquire lease")
        state = self.read_snapshot()
        rows = [copy.deepcopy(row) for row in state["sends"].values()
                if row["lease_id"] == lease_id and row["state"] in SEND_OPEN_STATES]
        require(len(rows) <= 1, "multiple open P0 reacquire sends")
        return rows[0] if rows else None

    def exact_send(self, candidate, *, states=None):
        require(type(candidate) is dict and valid_intent_id(candidate.get("send_id")),
                "P0 reacquire requires an exact send pin")
        state = self.read_snapshot()
        current = state["sends"].get(candidate["send_id"])
        require(current == candidate, "P0 reacquire requires an exact send pin")
        if states is not None:
            require(current["state"] in states, "P0 reacquire send state")
        parent = state["intents"].get(current["parent_intent_id"])
        require(parent is not None and parent["state"] == "bound"
                and parent["lease_id"] == current["lease_id"],
                "P0 reacquire parent authority")
        return current

    def approval_intent(self, lease_id, *, scope=None, item_id=None):
        require(text(lease_id, 200), "P0 approval lease")
        try:
            state = self.read_snapshot()
        except WorkParallelStateUnavailable:
            return None
        matches = [row for row in state["intents"].values()
                   if row["lease_id"] == lease_id and row["state"] in PINNED_INTENT_STATES]
        matches = [row for row in matches
                   if (scope is None or row["scope"] == scope)
                   and (item_id is None or row["item_id"] == item_id)]
        if not matches:
            return None
        # One lease may accumulate terminal reacquire intents. The original/new-
        # fork intent remains the planning-approval authority.
        matches.sort(key=lambda row: (row["continuation"] == "reacquire",
                                      row["created_at"], row["intent_id"]))
        return copy.deepcopy(matches[0])


def p0_store(store):
    return WorkParallelP0Store.beside(store.path)
