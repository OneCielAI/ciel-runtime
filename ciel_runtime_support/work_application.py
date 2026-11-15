"""Current-session application service for the Work REST API.

The HTTP controller owns transport and current-conversation identity.  This
module owns Work projections and human board commands so every mutation uses
the same tree/store transaction and compare-and-swap rules as MCP.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from typing import Any

from . import work_plan_tree as tree
from .work_operator_intents import MAX_PREPARED, WorkOperatorIntents
from .work_plan_store import WorkPlanConflict

MAX_OPERATION_ID_CHARS = 200
MAX_OPERATION_RECEIPTS = 128
OPERATION_FIELD = "rest_operations"
_SUMMARY_WORD = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
# Pins are kept only in their exact shape: never a word that merely fits.
_SUMMARY_PINS = {"expected_review_pin": re.compile(r"rp1:[0-9a-f]{64}\Z"),
                 "expected_settings_fingerprint": re.compile(r"[0-9a-f]{64}\Z")}


class WorkOperationConflict(tree.WorkPlanError):
    """An idempotency key was already committed for another request."""


class WorkOperationOutcomeUnknown(WorkOperationConflict):
    """An operator command under this key started and its outcome was not recorded."""


class WorkOperationReceiptsFull(WorkOperationConflict):
    """Every one of the 128 receipts is an operation whose outcome is unknown."""


class WorkOperationResolutionError(WorkOperationConflict):
    """A reconcile or release of an operator receipt was refused; ``code`` and ``status`` name why."""

    code, status = "operation_not_unknown", 409

    def __init__(self, message: str, *, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.status = status or self.status


# Stored receipt state -> the outcome a person reads (GET /audit).
OUTCOMES = {"prepared": "unknown", "committed": "applied", "refused": "not_applied",
            "released": "not_applied", "released_unverified": "released_unverified"}


def board_command_receipt(row: Any) -> bool:
    """A board command's receipt: a board row that is not an operator receipt (no operator state)."""
    return isinstance(row, dict) and row.get("state") not in OUTCOMES


RECEIPTS_FULL = ("all 128 operation receipts are operations whose outcome is unknown; "
                 "resolve outcome-unknown operations first")
# The request fields an audit receipt may keep: ids, versions, pins and flags.
# Free text (answer, note, evidence, value, findings) is never kept; a list
# keeps only its length.
REQUEST_SUMMARY_KEYS = ("lease_id", "item_id", "receipt_id", "event_id", "launch_ref", "attempt_ref",
                        "reappend_ref", "preview_digest", "decision", "decision_id", "verdict", "expected_state",
                        "key", "expected_version", "expected_finalization_version", "expected_stranded_version",
                        "revision", "confirmed", "legacy_confirmed", "activate", "user_requested", "limit",
                        "expected_receipt_version", *_SUMMARY_PINS)
REQUEST_SUMMARY_COUNTS = ("receipt_ids", "clear_wait", "node_ids", "changes")


def request_summary(payload: Any) -> dict[str, Any]:
    """The bounded, text-free record of what an operator asked for."""
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in REQUEST_SUMMARY_KEYS:
        value = payload.get(key)
        if value is None or type(value) in (bool, int, float):
            if key in payload:
                summary[key] = value
        elif isinstance(value, str) and (_SUMMARY_PINS.get(key) or _SUMMARY_WORD).fullmatch(value):
            summary[key] = value
    for key in REQUEST_SUMMARY_COUNTS:
        if isinstance(payload.get(key), (list, dict)):
            summary[key + "_count"] = len(payload[key])
    return summary


def evict_finished(receipts: dict[str, Any], keep: str) -> list[tuple[str, Any]]:
    """Bring the map back to MAX_OPERATION_RECEIPTS by removing the oldest finished receipts.

    A prepared receipt (outcome unknown) is never removed: forgetting it would
    let a retry of its key run the effect a second time. When only prepared
    receipts remain, the new receipt is refused and nothing is written.
    """
    evicted = []
    while len(receipts) > MAX_OPERATION_RECEIPTS:
        finished = sorted((key for key, row in receipts.items()
                           if key != keep and (row or {}).get("state") != "prepared"),
                          key=lambda item: receipts[item]["created_at"])
        if not finished:
            raise WorkOperationReceiptsFull(RECEIPTS_FULL)
        evicted.append((finished[0], receipts.pop(finished[0])))
    return evicted


class WorkApplication:
    """Read and mutate one authoritative current-session Work board."""

    def __init__(self, store: Any, *, approval_ask: bool = False,
                 operator_intents: WorkOperatorIntents | None = None) -> None:
        self.store = store
        self.approval_ask = bool(approval_ask)
        self._operator_intents = operator_intents
        # key -> (bytes before, record written) of this instance's own prepare,
        # so a refused effect can give back exactly what it found.
        self._prepare_undo: dict[str, tuple[bytes | None, dict[str, Any]]] = {}

    @property
    def operator_intents(self) -> WorkOperatorIntents:
        """The authoritative operator-intent sidecar beside the resolved board file.

        Resolved at use, never at construction: a continuation mapping may move
        the board, and constructing the application stays free of file access.
        """
        return self._operator_intents or WorkOperatorIntents.beside(self.store.path)

    def _intent_rows(self, *, adopt: bool = True) -> dict[str, dict[str, Any]]:
        """Authoritative operator rows, with pre-sidecar board rows merged."""
        board = self.document().get(OPERATION_FIELD) or {}
        if adopt:
            return self.operator_intents.adopt_board(board)
        return self.operator_intents.view(board)

    @staticmethod
    def _mirror(receipts: dict[str, Any], key: str, row: dict[str, Any]) -> None:
        """Best-effort board mirror, bounded for every frozen-schema reader.

        A prepared mirror keeps the finished board row it displaced, so a
        refused effect can give it back (the pre-sidecar board contract).
        """
        prior = receipts.get(key)
        if board_command_receipt(prior):
            # One key space (review F2): a board command's receipt is never
            # replaced by an operator mirror under the same key.
            return
        mirrored = copy.deepcopy(row)
        if (row.get("state") == "prepared" and isinstance(prior, dict) and prior.get("state") == "prepared"
                and isinstance(prior.get("evicted"), dict)):
            mirrored["evicted"] = prior["evicted"]
        receipts[key] = mirrored
        evicted = evict_finished(receipts, key)
        if evicted and row.get("state") == "prepared" and "evicted" not in mirrored:
            mirrored["evicted"] = {"key": evicted[0][0], "row": evicted[0][1]}

    def _mirror_row(self, key: str, row: dict[str, Any]) -> None:
        try:
            self.store.transaction(lambda doc: self._mirror(doc.setdefault(OPERATION_FIELD, {}), key, row),
                                   audit_only=True)
        except (OSError, tree.WorkPlanError):
            # The sidecar fact is authoritative. A later lookup/write retries it.
            pass

    @staticmethod
    def _mirror_abandon(doc: dict[str, Any], key: str, digest: str) -> bool:
        """The pre-sidecar board contract for a refused effect's mirror.

        True means the evicted key was already reused, so the refused receipt
        must stay in the sidecar as well as the board mirror.
        """
        receipts = doc.get(OPERATION_FIELD) or {}
        prior = receipts.get(key)
        if prior is None or prior.get("digest") != digest or prior.get("state") != "prepared":
            return False
        evicted = prior.get("evicted")
        if isinstance(evicted, dict) and evicted.get("key") in receipts:
            # The evicted key was used again meanwhile: it is not put back,
            # and this attempt stays on record as refused (no effect ran).
            prior.pop("evicted", None)
            prior.update(state="refused", response={"ok": False, "state": "refused"},
                         receipt_version=int(prior.get("receipt_version") or 1) + 1)
            return True
        receipts.pop(key)
        if isinstance(evicted, dict) and isinstance(evicted.get("key"), str):
            # Only this key left the map, so the receipt it displaced fits again.
            receipts[evicted["key"]] = evicted["row"]
        if not receipts:
            # Back to the exact document the refused command found.
            doc.pop(OPERATION_FIELD, None)
        return False

    def document(self) -> dict[str, Any]:
        return self.store.read_snapshot()

    def state(self, document: dict[str, Any] | None = None, *, enabled: bool = True) -> dict[str, Any]:
        return tree.snapshot(document or self.document(), enabled=enabled, approval_ask=self.approval_ask)

    def queue(self, document: dict[str, Any] | None = None) -> dict[str, Any]:
        doc = document or self.document()
        by_id = {node["id"]: node for node in doc["nodes"]}
        active_id = doc.get("active_root")
        active = copy.deepcopy(by_id.get(active_id)) if active_id else None
        queued = []
        for position, node in enumerate(tree.queued_roots(doc), 1):
            queued.append({**copy.deepcopy(node), "queue_position": position})
        return {"version": doc["version"], "active": active, "items": queued,
                "focus_ids": list(doc.get("focus_ids", []))}

    def command(self, command: str, payload: dict[str, Any], operation_id: Any, *,
                origin: dict[str, Any] | None = None, scope: str | None = None,
                parallel_store: Any = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise tree.WorkPlanError("body must be an object")
        # The surface that issued the board command (server-derived), recorded
        # with its receipt and never part of the digest: a replay from another
        # surface still resolves to the same operation.
        origin = dict(origin or {"source": "rest", "auth": "in_process"})
        source = origin.pop("source")
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_version must be a nonnegative integer")
        key = self._operation_id(operation_id)
        request = {k: v for k, v in payload.items() if k not in ("operation_id", "session_id")}
        priority_command = (parallel_store is not None and (
            (command == "node_update" and "priority" in request)
            or (command == "plan_set" and "priority" in request
                and (request.get("root_id") or request.get("priority") == "P0"))))
        if priority_command:
            if scope is None or parallel_store is None:
                raise tree.WorkPlanError("priority command requires the authenticated Work application boundary")
            return self._priority_command(command, request, key, scope, parallel_store, origin)
        digest = hashlib.sha256(json.dumps(
            {"command": command, "request": request}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        # Resolved before the board lock is taken (a continuation mapping may
        # lock while resolving the board path).
        intents = self.operator_intents

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            receipts = doc.setdefault(OPERATION_FIELD, {})
            prior = receipts.get(key)
            if prior is not None:
                if prior["digest"] != digest:
                    raise WorkOperationConflict("operation_id was already used for a different request")
                return copy.deepcopy(prior["response"])
            # One key space (review F2): a key an operator intent holds, even
            # after an older build evicted its board mirror, is never reused.
            held = intents.read()["intents"].get(key)
            if held is not None:
                if held["digest"] != digest:
                    raise WorkOperationConflict("operation_id was already used for a different request")
                return copy.deepcopy(held["response"])
            if doc["version"] != expected:
                raise WorkPlanConflict(
                    f"the plan changed (stored version {doc['version']}, expected {expected}); refetch and retry")
            before = copy.deepcopy(doc)
            result = self._apply(doc, command, request)
            changed = self._without_receipts(doc) != self._without_receipts(before)
            response = {"ok": True, "schema_version": 1, "operation": command,
                        "operation_id": key, "version": doc["version"] + 1,
                        "changed": changed, "result": result}
            receipts[key] = {"digest": digest, "command": command, "source": source, "origin": origin,
                             "created_at": time.time(), "response": copy.deepcopy(response)}
            evict_finished(receipts, key)
            return response

        document, response = self.store.transaction(mutate)
        # A replay can occur after unrelated commits; the exact committed response
        # is intentionally returned rather than being rebuilt from current state.
        return response

    def _operator_digest(self, command: str, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise tree.WorkPlanError("body must be an object")
        request = {k: v for k, v in payload.items() if k not in ("operation_id", "session_id")}
        return hashlib.sha256(json.dumps(
            {"command": command, "request": request}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

    def operator_prepare(self, command: str, payload: dict[str, Any], operation_id: Any, *,
                         origin: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Write an authoritative intent first, then its schema-compatible board mirror."""
        key = self._operation_id(operation_id)
        digest = self._operator_digest(command, payload)
        origin = dict(origin or {"source": "rest", "auth": "in_process"})
        source = origin.pop("source")
        # Adopt a pre-sidecar operator mirror before deciding whether this key is new.
        self._intent_rows()

        def prepare(intents: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
            prior = intents.get(key)
            if prior is not None:
                if prior["digest"] != digest:
                    raise WorkOperationConflict("operation_id was already used for a different request")
                if prior.get("state") == "prepared":
                    raise WorkOperationOutcomeUnknown(
                        "an operator command with this key was started and its outcome is unknown; "
                        "reread the current view, then use a new key if the change is still needed")
                if prior.get("state") in ("released", "released_unverified"):
                    raise WorkOperationConflict(
                        "this key's operation was resolved by a person (" + OUTCOMES[prior["state"]]
                        + "); reread the current view, then use a new key if the change is still needed")
                if prior.get("state") != "refused":
                    return copy.deepcopy(prior["response"])
            prepared = sum(row.get("state") == "prepared" for row in intents.values())
            if prepared >= MAX_PREPARED and (prior or {}).get("state") != "prepared":
                raise WorkOperationReceiptsFull(RECEIPTS_FULL)
            intents[key] = {"digest": digest, "command": command, "source": source, "origin": origin,
                            "state": "prepared", "created_at": time.time(),
                            "request": request_summary(payload),
                            "receipt_version": int((prior or {}).get("receipt_version") or 0) + 1,
                            "last_reconcile": None, "resolution": None,
                            "response": {"ok": False, "state": "prepared"}}
            return None

        undo: list = []
        intents = self.operator_intents

        def guarded(doc: dict[str, Any]) -> dict[str, Any] | None:
            # Under the board lock, so a board command cannot commit this key
            # between this check and the intent write (one key space, F2).
            board = (doc.get(OPERATION_FIELD) or {}).get(key)
            if board_command_receipt(board):
                if board["digest"] != digest:
                    raise WorkOperationConflict("operation_id was already used for a different request")
                return copy.deepcopy(board["response"])
            return intents.transaction(prepare, undo=undo)

        try:
            _, replay = self.store.transaction(guarded, audit_only=True)
        except WorkOperationOutcomeUnknown:
            row = self.operator_intents.read()["intents"].get(key)
            if row is not None:
                self._mirror_row(key, row)
            raise
        if undo and replay is None:
            self._prepare_undo[key] = undo[-1]
        row = intents.read()["intents"].get(key)
        if row is not None:
            self._mirror_row(key, row)
        return replay

    def operator_abandon(self, command: str, payload: dict[str, Any], operation_id: Any) -> None:
        """Record a refused effect in the authority, then converge the board mirror."""
        key = self._operation_id(operation_id)
        digest = self._operator_digest(command, payload)
        self._intent_rows()

        def abandon(intents: dict[str, dict[str, Any]]) -> None:
            prior = intents.get(key)
            if prior is not None and prior["digest"] == digest and prior.get("state") == "prepared":
                # No effect ran: the key may be retried. A crash before the
                # mirror below leaves a prepared mirror, which a later lookup
                # adopts as outcome-unknown (fail closed, never a second effect).
                del intents[key]

        undo = self._prepare_undo.pop(key, None)
        if undo is None or not self.operator_intents.restore(*undo):
            self.operator_intents.transaction(abandon)
        try:
            _, retained = self.store.transaction(lambda doc: self._mirror_abandon(doc, key, digest), audit_only=True)
            if retained:
                def retain(intents: dict[str, dict[str, Any]]) -> None:
                    board = (self.document().get(OPERATION_FIELD) or {}).get(key)
                    if isinstance(board, dict):
                        from .work_operator_intents import _from_board
                        intents[key] = _from_board(board)
                self.operator_intents.transaction(retain)
        except (OSError, tree.WorkPlanError):
            pass

    def operator_commit(self, command: str, payload: dict[str, Any], operation_id: Any,
                        summary: dict[str, Any]) -> dict[str, Any]:
        """Commit the sidecar intent first, then converge its board mirror."""
        key = self._operation_id(operation_id)
        digest = self._operator_digest(command, payload)
        self._intent_rows()
        board_version = self.document()["version"]

        def commit(intents: dict[str, dict[str, Any]]) -> dict[str, Any]:
            prior = intents.get(key)
            if prior is None or prior["digest"] != digest:
                raise WorkOperationConflict("operator receipt changed before its commit")
            if prior.get("state") not in ("prepared", "released", "released_unverified"):
                return copy.deepcopy(prior["response"])
            response = {"ok": True, "schema_version": 1, "operation": command,
                        "operation_id": key, "version": board_version,
                        "changed": True, "result": copy.deepcopy(summary)}
            prior.update(state="committed", response=copy.deepcopy(response),
                         receipt_version=int(prior.get("receipt_version") or 1) + 1)
            return response

        self._prepare_undo.pop(key, None)
        response = self.operator_intents.transaction(commit)
        self._mirror_row(key, self.operator_intents.read()["intents"][key])
        return response

    def audit_receipts(self) -> dict[str, dict[str, Any]]:
        """Board-command rows plus authoritative operator rows, keyed once."""
        board = self.document().get(OPERATION_FIELD) or {}
        return {**copy.deepcopy(board), **self.operator_intents.view(board)}

    def operation_receipt(self, operation_id: Any) -> dict[str, Any]:
        """One authoritative operator receipt for a person's reconcile."""
        key = self._operation_id(operation_id)
        row = self._intent_rows(adopt=False).get(key)
        if not isinstance(row, dict):
            raise WorkOperationResolutionError("no operation receipt with this operation_id",
                                               code="unknown_operation", status=404)
        return copy.deepcopy(row)

    def operation_reconcile_record(self, operation_id: Any, receipt_version: int, result: str, observed: str, *,
                                   source: str) -> dict[str, Any]:
        """Settle or annotate an authoritative outcome-unknown intent."""
        key = self._operation_id(operation_id)
        if result not in ("applied", "not_applied", "not_observable"):
            raise tree.WorkPlanError("reconcile result must be applied, not_applied or not_observable")
        self._intent_rows()
        board_version = self.document()["version"]

        def apply(intents: dict[str, dict[str, Any]]) -> dict[str, Any]:
            prior = intents.get(key)
            if not isinstance(prior, dict):
                raise WorkOperationResolutionError("no operation receipt with this operation_id",
                                                   code="unknown_operation", status=404)
            if prior.get("state") != "prepared":
                raise WorkOperationResolutionError("this operation's outcome is already known; reread /audit")
            if int(prior.get("receipt_version") or 1) != receipt_version:
                raise WorkOperationResolutionError("the operation receipt changed during the reconcile; reread",
                                                   code="receipt_changed")
            stamp = time.time()
            version = receipt_version + 1
            prior.update(last_reconcile={"result": result, "at": stamp}, receipt_version=version,
                         resolution={"command": "operation_reconcile", "source": source, "at": stamp})
            if result == "applied":
                prior.update(state="committed", response={
                    "ok": True, "schema_version": 1, "operation": prior.get("command"), "operation_id": key,
                    "version": board_version, "changed": True,
                    "result": {"reconciled": "applied", "observed": observed}})
            elif result == "not_applied":
                prior.update(state="released", response={"ok": False, "state": "released"})
            return {"operation_id": key, "command": prior.get("command"), "outcome": OUTCOMES[prior["state"]],
                    "observed": observed, "receipt_version": version}

        value = self.operator_intents.transaction(apply)
        self._mirror_row(key, self.operator_intents.read()["intents"][key])
        return value

    def operation_release(self, operation_id: Any, expected_receipt_version: Any, *, source: str) -> dict[str, Any]:
        """Release one authoritative intent after a pinned not-observable reconcile."""
        key = self._operation_id(operation_id)
        if type(expected_receipt_version) is not int or expected_receipt_version < 1:
            raise tree.WorkPlanError("expected_receipt_version must be the receipt_version shown in /audit")
        self._intent_rows()

        def apply(intents: dict[str, dict[str, Any]]) -> dict[str, Any]:
            prior = intents.get(key)
            if not isinstance(prior, dict):
                raise WorkOperationResolutionError("no operation receipt with this operation_id",
                                                   code="unknown_operation", status=404)
            if prior.get("state") != "prepared":
                raise WorkOperationResolutionError("this operation's outcome is already known; reread /audit")
            if int(prior.get("receipt_version") or 1) != expected_receipt_version:
                raise WorkOperationResolutionError("the operation receipt changed; reread /audit",
                                                   code="receipt_changed")
            if (prior.get("last_reconcile") or {}).get("result") != "not_observable":
                raise WorkOperationResolutionError(
                    "reconcile this operation first; release is only for an outcome no stored fact can settle",
                    code="reconcile_required")
            stamp = time.time()
            version = expected_receipt_version + 1
            prior.update(state="released_unverified", response={"ok": False, "state": "released_unverified"},
                         last_reconcile=None, receipt_version=version,
                         resolution={"command": "operation_release", "source": source, "at": stamp})
            return {"operation_id": key, "command": prior.get("command"), "outcome": "released_unverified",
                    "receipt_version": version}

        value = self.operator_intents.transaction(apply)
        self._mirror_row(key, self.operator_intents.read()["intents"][key])
        return value

    @staticmethod
    def _operation_id(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_OPERATION_ID_CHARS:
            raise tree.WorkPlanError(
                f"Idempotency-Key or operation_id must be a nonempty string of at most {MAX_OPERATION_ID_CHARS} characters")
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise tree.WorkPlanError("operation_id must contain visible ASCII characters only")
        return value

    def _priority_command(self, command: str, request: dict[str, Any], operation_id: str,
                          scope: str, parallel_store: Any, origin: dict[str, Any]) -> dict[str, Any]:
        """Existing-root priority mutation shared by REST, web and ctl.

        Its P0 marker operation is the exact external command id. The board CAS
        applies every other field with the canonical priority in one commit.
        """
        from .work_parallel import WorkParallelError
        from .work_priority_service import create_p0_root, mutate_existing_priority
        target = request.get("priority")
        if target not in ("P0", "", *tree.PRIORITIES):
            raise tree.WorkPlanError("priority must be one of P0, P1, P2, P3, P4, P5")
        root_id = str((request.get("root_id") if command == "plan_set" else request.get("id")) or "")
        if command == "plan_set" and target == "P0" and not root_id:
            try:
                create_p0_root()
            except WorkParallelError as error:
                raise tree.WorkPlanError(str(error)) from None
        expected = request.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_version must be a nonnegative integer")
        clean = {key: value for key, value in request.items()
                 if key not in ("priority", "expected_version")}
        manifest = hashlib.sha256(json.dumps(
            {"command": command, "payload": clean}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        if command == "plan_set":
            spec = {"title": clean.get("goal"), "note": clean.get("note", ""),
                    "children": clean.get("children") or []}
            spec.update({key: clean[key] for key in ("ref", "waiting_on") if key in clean})
            def mutate(document):
                return tree.set_plan(document, spec, root_id)
            def satisfied(document):
                probe = copy.deepcopy(document)
                before = copy.deepcopy(probe)
                tree.set_plan(probe, spec, root_id)
                tree.update_node(probe, root_id, priority="P1" if target == "P0" else target)
                return probe == before
        else:
            mutable = {"status", "title", "note", "progress", "evidence", "ref", "waiting_on"}
            supplied = {key: clean[key] for key in mutable if key in clean and clean[key] is not None}
            def mutate(document):
                return tree.update_node(document, root_id, **supplied)
            def satisfied(document):
                node = next((row for row in document.get("nodes", []) if row.get("id") == root_id), None)
                return (node is not None and all(node.get(key, "") == value for key, value in supplied.items())
                        and node.get("priority", "") == ("P1" if target == "P0" else target))
        try:
            result = mutate_existing_priority(
                self.store, parallel_store, scope, root_id, target,
                expected_board_version=expected, external_operation_id=operation_id,
                manifest_digest=manifest, mutate=mutate, satisfied=satisfied, set_by="operator")
        except WorkParallelError as error:
            raise tree.WorkPlanError(str(error)) from None
        ref = hashlib.sha256(("ciel.priority-ref.v1\0" + operation_id).encode()).hexdigest()[:16]
        return {"ok": True, "schema_version": 1, "operation": command,
                "operation_id": operation_id, "priority_operation_id": operation_id,
                "priority_operation_ref": ref, "version": result["version"],
                "changed": not result["replayed"], "replayed": result["replayed"],
                "result": {"root_id": root_id, "canonical_priority": result["canonical_priority"],
                           "effective_priority": result["effective_priority"],
                           "priority_operation_ref": ref}}

    @staticmethod
    def _without_receipts(doc: dict[str, Any]) -> dict[str, Any]:
        value = dict(doc)
        value.pop(OPERATION_FIELD, None)
        return value

    def _apply(self, doc: dict[str, Any], command: str, args: dict[str, Any]) -> dict[str, Any]:
        if command == "plan_set":
            self._only(args, {"expected_version", "goal", "note", "children", "ref", "waiting_on", "priority", "root_id"})
            spec = {"title": args.get("goal"), "note": args.get("note", ""),
                    "children": args.get("children") or []}
            spec.update({key: args[key] for key in ("ref", "waiting_on", "priority") if key in args})
            root = tree.set_plan(doc, spec, str(args["root_id"]) if args.get("root_id") else None)
            return {"root_id": root, "active_root_id": doc.get("active_root"),
                    "phase": tree.root_phase(doc, root)}
        if command == "node_add":
            self._only(args, {"expected_version", "parent_id", "title", "note", "children", "ref", "waiting_on", "priority", "within_scope"})
            parent = args.get("parent_id")
            spec = {"title": args.get("title"), "note": args.get("note", ""),
                    "children": args.get("children") or [], "ref": args.get("ref", ""),
                    "waiting_on": args.get("waiting_on", "")}
            if parent is None and "priority" in args:
                spec["priority"] = args["priority"]
            node = tree.add_subtree(doc, str(parent) if parent is not None else None, spec,
                                    within_scope=args.get("within_scope") is True)
            return {"id": node, "active_root_id": doc.get("active_root")}
        if command == "node_update":
            mutable = {"status", "title", "note", "progress", "evidence", "ref", "waiting_on", "priority"}
            self._only(args, {"expected_version", "id", *mutable})
            node_id = self._existing(doc, args)
            supplied = {key: args[key] for key in mutable if key in args}
            if not supplied:
                raise tree.WorkPlanError("at least one editable field is required")
            node = tree.update_node(doc, node_id, **supplied)
            return {"id": node["id"], "status": node["status"],
                    "active_root_id": doc.get("active_root")}
        if command == "node_remove":
            self._only(args, {"expected_version", "id"})
            node_id = self._existing(doc, args)
            return {"id": node_id, "removed": tree.remove_node(doc, node_id),
                    "active_root_id": doc.get("active_root")}
        if command == "active_select":
            self._only(args, {"expected_version", "root_id", "user_requested"})
            root = self._id(args, "root_id")
            tree.select_root(doc, root, user_requested=args.get("user_requested") is True)
            return {"root_id": root, "phase": doc["phase"]}
        if command == "execute":
            self._only(args, {"expected_version", "user_confirmed"})
            ready, reason = tree.readiness(doc)
            if not ready:
                raise tree.WorkPlanError(f"The plan is not ready to execute: {reason}.")
            if doc["phase"] == tree.PHASE_EXECUTING:
                outcome = "already"
            elif self.approval_ask and args.get("user_confirmed") is not True:
                outcome = "confirmation_required"
            else:
                tree.set_phase(doc, tree.PHASE_EXECUTING)
                outcome = "started"
            return {"outcome": outcome, "phase": doc["phase"], "root_id": doc.get("active_root")}
        if command == "replan":
            self._only(args, {"expected_version", "reason"})
            if "reason" in args and not isinstance(args["reason"], str):
                raise tree.WorkPlanError("reason must be a string")
            tree.set_phase(doc, tree.PHASE_PLANNING)
            return {"phase": doc["phase"], "root_id": doc.get("active_root")}
        if command == "sibling_reorder":
            self._only(args, {"expected_version", "id", "position"})
            node_id = self._existing(doc, args)
            node = next(node for node in doc["nodes"] if node["id"] == node_id)
            if node["parent_id"] is None:
                raise tree.WorkPlanError("sibling reorder requires a non-root node; use queue_position for waiting goals")
            tree.reorder_node(doc, node_id, args.get("position"))
            return {"id": node_id, "position": args.get("position")}
        if command == "queue_position":
            self._only(args, {"expected_version", "root_id", "queue_position"})
            root = self._id(args, "root_id")
            tree.reorder_queued_root(doc, root, args.get("queue_position"))
            return tree.queue_position(doc, root)
        if command == "focus":
            self._only(args, {"expected_version", "node_ids", "user_requested"})
            tree.set_focus(doc, args.get("node_ids"), user_requested=args.get("user_requested") is True)
            return {"node_ids": list(doc.get("focus_ids", []))}
        if command == "urgent":
            self._only(args, {"expected_version", "goal", "note", "children", "ref", "user_requested"})
            spec = {"title": args.get("goal"), "note": args.get("note", ""),
                    "children": args.get("children") or [], "ref": args.get("ref", "")}
            root = tree.add_urgent(doc, spec, user_requested=args.get("user_requested") is True)
            return {"root_id": root, "active_root_id": doc.get("active_root"), "urgent": True}
        raise tree.WorkPlanError("unknown Work command")

    @staticmethod
    def _only(args: dict[str, Any], allowed: set[str]) -> None:
        if set(args) - allowed:
            raise tree.WorkPlanError("unsupported mutation fields")

    @staticmethod
    def _id(args: dict[str, Any], field: str = "id") -> str:
        value = args.get(field)
        if not isinstance(value, str) or not value:
            raise tree.WorkPlanError(field + " must be a nonempty string")
        return value

    @classmethod
    def _existing(cls, doc: dict[str, Any], args: dict[str, Any]) -> str:
        # Same 404 contract as the legacy node routes.
        node_id = cls._id(args)
        if not any(node["id"] == node_id for node in doc["nodes"]):
            raise KeyError("unknown_node")
        return node_id


def openapi_document() -> dict[str, Any]:
    """Small transport-independent OpenAPI description for the v1 surface."""
    reads = ("board", "state", "queue", "runtime", "settings", "parallel", "side", "audit",
             "openapi", "events")
    commands = ("plan-set", "node-add", "node-update", "node-remove", "active-select",
                "execute", "replan", "sibling-reorder", "queue-position", "focus", "urgent",
                "settings-update", "parallel-configure", "parallel-orphans-preview",
                "parallel-orphans-apply", "parallel-item-review", "parallel-launch-resolve",
                "parallel-legacy-accept", "side-review", "side-handoff", "side-reply-retry", "side-wake-retry",
                "side-notice-retry", "side-reappend-retry", "review-reappend-retry", "side-drain",
                "operation-reconcile", "operation-release")
    paths: dict[str, Any] = {
        f"/ca/work/api/v1/{name}": {"get": {"summary": f"Read current-session Work {name}"}}
        for name in reads
    }
    redaction = ("Credentials, reply routes and tokens, claim and fallback generations, outbox attempt ids "
                 "and main-context previews are never returned. Text is redacted and limited to 16,000 characters.")
    stages = ["received", "processing", "processed", "failed", "quarantined", "duplicate"]
    paths["/ca/work/api/v1/parallel"]["get"]["description"] = (
        "Current-session Work forks grouped by native agent-list evidence, bounded main-review receipts, plus aggregate device slot capacity. "
        "Only a native pending/running row observed within 30 seconds is confirmed. The main session is the "
        "coordinator and is never a fork; side actors are excluded. Other sessions expose counts only, never item "
        "or agent metadata. Raw leases, workspace paths, process identities, worker capabilities and fork content "
        "are never returned.")
    paths["/ca/work/api/v1/side"]["get"].update({
        "description": "Pending reviews, main handoffs (each with its redacted main-wake state), review_delivery "
                       "(where each pending main review's payload was appended: unknown, appended, reappending, "
                       "reappended, read or decided, in_this_conversation, reappends and times; never a conversation, "
                       "attempt, owner or receipt) and a newest-first side receipt page. " + redaction,
        "parameters": [
            {"in": "query", "name": "limit", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
            {"in": "query", "name": "cursor", "description": "next_cursor from the previous page",
             "schema": {"type": "string"}},
            {"in": "query", "name": "stage", "schema": {"type": "string", "enum": stages}},
            {"in": "query", "name": "priority", "schema": {"type": "string", "enum": ["P1", "P2", "P3", "P4", "P5"]}},
            {"in": "query", "name": "q", "description": "Case-insensitive match on bounded public fields",
             "schema": {"type": "string", "maxLength": 200}}]})
    paths["/ca/work/api/v1/side/receipt"] = {"get": {
        "summary": "Read one current-session side receipt detail",
        "description": redaction,
        "parameters": [{"in": "query", "name": "receipt_id", "required": True,
                        "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}],
        "responses": {"200": {"description": "Redacted receipt detail"},
                      "404": {"description": "unknown_side_receipt"},
                      "422": {"description": "Malformed receipt id"}}}}
    paths["/ca/work/api/v1/events"]["get"]["description"] = (
        "Server-sent events: work.snapshot, work.changed, work.resync_required, runtime.error, "
        "content-free side.changed (no event id; refetch /ca/work/api/v1/side), content-free "
        "parallel.changed (no event id; refetch /ca/work/api/v1/parallel; also on a ledger version or policy "
        "change), settings.changed (no event id; settings_fingerprint; refetch /ca/work/api/v1/settings) and "
        "audit.changed (no event id; count, newest_operation_id and newest_created_at; refetch "
        "/ca/work/api/v1/audit).")
    paths["/ca/work/api/v1/parallel"]["get"]["responses"] = {"200": {
        "description": "Current-session parallel view",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ParallelView"}}}}}
    paths["/ca/work/api/v1/audit"]["get"]["description"] = (
        "Newest-first REST operation receipts of this session: board commands and every successful operator "
        "command (decisions, launch resolutions, legacy accepts, orphan releases, retries, settings and policy "
        "changes). Operator receipts never change the board version and keep only a bounded summary of ids, "
        "states and versions, never text, paths or secrets; a replay returns that summary. A receipt is prepared "
        "before the effect and committed after it. Each operator receipt has a server-derived "
        "source: web (signed-in session cookie), rest (administrator token, or loopback without credentials) or "
        "ctl (in-process ciel-runtimectl, with its OS user and pid). No client header sets it. Each operator "
        "receipt also keeps a text-free request summary (ids, versions, pins, flags; lists as counts). "
        "Operator receipts are authoritative in the session's operator-intent record "
        "(work-operator-intents-v1.json beside the board), written before the board's replay mirror and read "
        "first; board commands are read from the board. Idempotency bound: the board mirror holds at most 128 "
        "receipts and this build removes the oldest finished one first, never a prepared one; an older build "
        "may remove any mirror row, which only shortens the board replay window. The operator-intent record "
        "never removes a prepared intent and keeps the newest 128 resolved ones. A key in neither record is no "
        "longer replayed: the command's own version or attempt pin refuses a repeat. When 128 operator intents "
        "are prepared, new commands answer 409 operator_receipts_full. The list is the newest 100 rows plus "
        "every prepared (outcome-unknown) row. state is committed, prepared or "
        "refused (the effect was refused and its key may run the same request again), released or "
        "released_unverified (resolved by a person). Each row has outcome (applied, unknown, not_applied, "
        "released_unverified), receipt_version and last_reconcile (null or {result, at}); resolve an unknown "
        "outcome with operation-reconcile, then operation-release if it stays not_observable.")
    for name in commands:
        body = OPERATOR_BODIES.get(name)
        if body is None:
            body = {"type": "object", "required": ["expected_version"],
                    "properties": {"expected_version": {"type": "integer", "minimum": 0}}}
        body = {**body, "properties": {**body["properties"], **_COMMON_BODY}}
        schema = "OperatorResponse" if name in OPERATOR_BODIES else "CommandResponse"
        error = {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}
        responses = {"200": {"description": "Applied, or the stored response of a replayed key",
                             "content": {"application/json": {"schema": {
                                 "$ref": "#/components/schemas/" + schema}}}},
                     "409": {"description": "version_conflict (refetch, never blind retry), idempotency_conflict "
                                            "(key reused for another request) or operation_outcome_unknown (an "
                                            "operator command under this key started and its outcome was not "
                                            "recorded; reread, then use a new key) or operator_receipts_full "
                                            "(all 128 receipts are outcome-unknown operations; resolve those "
                                            "first); settings-update also answers settings_conflict",
                             "content": error},
                     "422": {"description": "invalid_request; the message names the refused field",
                             "content": error},
                     "503": {"description": "state_unavailable", "content": error}}
        paths[f"/ca/work/api/v1/commands/{name}"] = {"post": {
            "summary": f"Apply current-session Work command {name}",
            "parameters": [{"in": "header", "name": "Idempotency-Key", "schema": {
                "type": "string", "minLength": 1, "maxLength": MAX_OPERATION_ID_CHARS, "pattern": "^[!-~]+$"}},
                           ],
            "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
            "responses": responses}}
    return {"openapi": "3.1.0", "info": {"title": "Ciel Work REST", "version": "1"},
            "security": [{"adminBearer": []}, {"adminToken": []}, {"webSession": []}],
            "components": {"securitySchemes": SECURITY_SCHEMES, "schemas": _component_schemas()},
            "paths": paths}


_HEX64 = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
_HEX32 = {"type": "string", "pattern": "^[0-9a-f]{32}$"}
_HEX16 = {"type": "string", "pattern": "^[0-9a-f]{16}$"}
_LEASE = {"type": "string", "minLength": 1, "maxLength": 200}
_VERSION = {"type": "integer", "minimum": 0}
_CONFIRMED = {"type": "boolean", "const": True}
_COMMON_BODY = {"operation_id": {"type": "string", "minLength": 1, "maxLength": MAX_OPERATION_ID_CHARS,
                                 "pattern": "^[!-~]+$",
                                 "description": "Same as the Idempotency-Key header; both must match when given"},
                "session_id": {"type": "string", "pattern": "^(claude|codex):[A-Za-z0-9_-]{1,200}$",
                               "description": "Optional; must equal the current conversation"}}


def _obj(required, **properties):
    return {"type": "object", "required": list(required), "properties": properties}


# Request bodies of the operator commands. Each expected version names the
# store the command writes; only side-review and side-handoff pin the board.
OPERATOR_BODIES: dict[str, Any] = {
    "settings-update": {
        **_obj(("key", "value", "expected_settings_fingerprint"), key={"type": "string"}, value={},
               expected_settings_fingerprint={"type": "string", "pattern": "^[0-9a-f]{64}$", "description":
                                              "settings_fingerprint from GET /ca/work/api/v1/settings"}),
        "description": "Sets one Work/SSE setting as a compare-and-set against the fingerprint the operator "
                       "read, checked and saved under the settings lock. A change made elsewhere since, or a late "
                       "replay of a request whose receipt aged out, answers 409 settings_conflict with the "
                       "current settings_fingerprint; nothing is saved."},
    "parallel-configure": _obj(
        ("changes", "expected_version"), changes={"type": "object"},
        expected_version={**_VERSION, "description": "Parallel ledger version: GET /parallel version"}),
    "parallel-orphans-preview": _obj(()),
    "parallel-orphans-apply": {
        **_obj(("lease_id", "preview_digest", "expected_version"), lease_id=_LEASE, preview_digest=_HEX64,
               expected_version={**_VERSION, "description": "The previewed ledger version (preview result version)"},
               legacy_confirmed={"type": "boolean", "description":
                                 "Required true to release a legacy_no_owner lease: the operator confirms its "
                                 "session is shut down"}),
        "description": "Releases exactly one lease of the preview named by preview_digest: a dead owner, or a "
                       "legacy_no_owner lease with legacy_confirmed. Live or unknown owners are never released."},
    "parallel-item-review": _obj(
        ("lease_id", "decision", "expected_finalization_version"), lease_id=_LEASE,
        decision={"type": "string", "enum": ["approve", "rework"]},
        evidence={"type": "string", "maxLength": 2000, "description": "Required for rework"},
        expected_finalization_version={**_VERSION, "description": "GET /parallel review_version"},
        expected_review_pin={"type": "string", "pattern": "^rp1:[0-9a-f]{64}$", "description":
                             "The row's review_pin from GET /parallel. When it matches the row, a change to "
                             "another review or to wake bookkeeping does not refuse the decision; without it "
                             "the version rule applies."}),
    "parallel-launch-resolve": {
        **_obj(("lease_id", "launch_ref", "confirmed"), lease_id=_LEASE, launch_ref=_HEX32,
               confirmed=_CONFIRMED,
               expected_finalization_version={**_VERSION, "description":
                                              "GET /parallel review_version, when review_journal is finalization"},
               expected_stranded_version={**_VERSION, "description":
                                          "GET /parallel stranded_version, when review_journal is stranded"}),
        "description": "Pin the version of the journal named by the row's review_journal."},
    "parallel-legacy-accept": _obj(
        ("lease_id", "confirmed", "expected_version"), lease_id=_LEASE, confirmed=_CONFIRMED,
        expected_version={**_VERSION, "description": "Parallel ledger version: GET /parallel version"}),
    "side-review": _obj(
        ("receipt_ids", "verdict", "expected_version"),
        receipt_ids={"type": "array", "minItems": 1, "maxItems": 50, "uniqueItems": True, "items": _HEX64},
        verdict={"type": "string", "enum": ["ok", "corrected"]},
        note={"type": "string", "maxLength": 2000, "description": "Required non-blank when verdict is corrected"},
        expected_version={**_VERSION, "description": "Work board version"}),
    "side-handoff": _obj(
        ("event_id", "expected_version"), event_id=_HEX64, answer={"type": "string", "maxLength": 16000},
        activate={"type": "boolean"},
        clear_wait={"type": "array", "uniqueItems": True, "items": {"type": "string"}},
        expected_version={**_VERSION, "description": "Work board version"}),
    "side-reply-retry": {
        **_obj(("receipt_id", "expected_state", "revision"), receipt_id=_HEX64,
               expected_state={"type": "string", "enum": ["failed", "route_unavailable", "uncertain", "submitting"]},
               revision={**_VERSION, "description": "The receipt's reply_revision (count of earlier delivery "
                                                    "retries), from GET /side receipts or GET /side/receipt"},
               attempt_ref={"type": ["string", "null"], "pattern": "^[0-9a-f]{16}$", "description":
                            "The receipt's reply_attempt_ref. Required when the receipt shows one; omitted or "
                            "null when it has no outbox attempt (route_unavailable)"},
               confirmed={"type": "boolean", "description": "Required true for uncertain or submitting"}),
        "description": "Retries one side reply pinned to the exact attempt the operator saw. A missing or "
                       "malformed pin is refused (422 \"side reply retry needs the receipt's reply_revision and "
                       "reply_attempt_ref; refresh before retry\"); a changed state, revision or attempt is "
                       "refused (422 \"side delivery changed; refresh before retry\")."},
    "side-wake-retry": _obj(("event_id", "attempt_ref", "revision", "confirmed"), event_id=_HEX64,
                            attempt_ref=_HEX16, revision={"type": "integer", "minimum": 0}, confirmed=_CONFIRMED),
    "side-notice-retry": {
        **_obj(("receipt_id", "attempt_ref", "revision", "confirmed"), receipt_id=_HEX64,
               attempt_ref={**_HEX16, "description": "The receipt's notice_attempt_ref, from GET /side "
                                                     "receipts or GET /side/receipt"},
               revision={**_VERSION, "description": "The receipt's notice_revision (count of earlier notice "
                                                    "retries)"},
               confirmed=_CONFIRMED),
        "description": "Re-issues one released-wait notice whose delivery is uncertain, pinned to the exact "
                       "attempt the operator saw. A missing or malformed pin is refused (422 \"side notice retry "
                       "needs the receipt's notice_attempt_ref and notice_revision; refresh before retry\"); a "
                       "changed attempt or revision is refused (422 \"side notice changed; refresh before "
                       "retry\"); a notice that is not uncertain is refused (422 \"side notice delivery is not "
                       "uncertain; refresh before retry\")."},
    "side-reappend-retry": _obj(("event_id", "reappend_ref", "revision", "confirmed"), event_id=_HEX64,
                                reappend_ref=_HEX16, revision={"type": "integer", "minimum": 0},
                                confirmed=_CONFIRMED),
    "review-reappend-retry": _obj(("lease_id", "reappend_ref", "confirmed"), lease_id=_LEASE,
                                  reappend_ref=_HEX16, confirmed=_CONFIRMED),
    "operation-reconcile": {
        **_obj(("operation_id", "confirmed"), confirmed=_CONFIRMED),
        "description": "Checks, read-only, whether an outcome-unknown operator command (GET /audit outcome "
                       "unknown) took effect, from the store that command writes; nothing is re-run. The body "
                       "operation_id names that receipt. applied -> outcome applied (a replay of its key returns "
                       "the reconciled result); not_applied -> outcome not_applied (use a new key to try again); "
                       "not_observable -> outcome stays unknown with last_reconcile recorded. Not itself audited "
                       "as a receipt, so it works when all 128 receipts are outcome-unknown. Errors: 404 "
                       "unknown_operation, 409 operation_not_unknown, 409 receipt_changed, 422."},
    "operation-release": {
        **_obj(("operation_id", "confirmed", "expected_receipt_version"), confirmed=_CONFIRMED,
               expected_receipt_version={"type": "integer", "minimum": 1,
                                         "description": "The receipt_version shown in GET /audit"}),
        "description": "Releases an outcome-unknown receipt whose reconcile observed not_observable, as "
                       "released_unverified: the effect may already have happened; retrying (with a new key) "
                       "may duplicate it. Nothing is run. Errors: 404 unknown_operation, 409 "
                       "operation_not_unknown, 409 reconcile_required, 409 receipt_changed, 422."},
    "side-drain": {**_obj((), limit={"type": "integer", "minimum": 1, "maximum": 100, "default": 10}),
                   "description": "The console side drain without admission: the newest side receipts as "
                                  "redacted receipt details (GET /side/receipt shape). A read: not audited."},
}

SECURITY_SCHEMES = {
    "adminBearer": {"type": "http", "scheme": "bearer",
                    "description": "Administrator token; external access must be enabled"},
    "adminToken": {"type": "apiKey", "in": "header", "name": "x-ciel-runtime-token",
                   "description": "Administrator token in a header"},
    "webSession": {"type": "apiKey", "in": "cookie", "name": "ciel_session",
                   "description": "Signed-in web account (same-origin, HttpOnly, SameSite=Strict)"},
}


def _component_schemas() -> dict[str, Any]:
    ts = {"type": "number", "description": "Unix seconds"}
    launch = {"type": "object", "properties": {
        "state": {"type": "string"}, "attempt": {"type": "integer"}, "launch_ref": _HEX32,
        "unresolved_seconds": {"type": "integer"}, "stuck": {"type": "boolean"},
        "agent_present": {"type": "boolean"}}}
    review = {"type": "object", "required": ["lease_id", "state", "review_journal"], "properties": {
        "lease_id": _LEASE, "item_id": {"type": "string"}, "priority": {"type": "string"},
        "state": {"type": "string"}, "decision": {"type": ["string", "null"]},
        "review_journal": {"type": "string", "enum": ["finalization", "stranded"]},
        "stranded": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["workspace_unavailable", "no_report"]},
            "phase": {"type": "string", "enum": ["pending", "deciding", "recovered"]},
            "approve": {"type": "string"}, "rework": {"type": "string"},
            "counts_against_rework_limit": {"type": "boolean"}}},
        "launch": launch}}
    ended = {"type": "object", "required": ["lease_id", "phase"], "properties": {
        "lease_id": _LEASE, "item_id": {"type": "string"}, "priority": {"type": "string"},
        "item_title": {"type": "string"},
        "kind": {"type": "string", "enum": ["workspace_unavailable", "no_report"]},
        "phase": {"type": "string", "const": "superseded"},
        "superseding_lease_id": {"type": "string", "maxLength": 64}, "observed_at": ts}}
    legacy = {"type": "object", "required": ["lease_id", "reviewers"], "properties": {
        "lease_id": _LEASE, "item_id": {"type": "string"}, "priority": {"type": "string"},
        "item_title": {"type": "string"},
        "reviewers": {"type": "string", "enum": ["none", "one", "multiple"]}}}
    failure = {"type": "object", "required": ["id", "code", "at", "count"], "properties": {
        "id": _HEX16, "code": {"type": "string", "enum": ["revival_refused", "decision_refused_stop",
                                                          "fork_attribution_stop", "owner_unknown", "owner_live"]},
        "lease_id": {"type": ["string", "null"], "maxLength": 64}, "first_at": ts, "at": ts,
        "count": {"type": "integer", "minimum": 1},
        "message": {"type": "string", "description": "Server text for owner_unknown/owner_live, shown verbatim"}}}
    fork_gate = {"type": "object", "required": ["state", "reason", "gate_version", "observed_at", "reported_at",
                                                "message"],
                 "description": ("Display only: the Work host's last live probe of the Work fork gate, reported on "
                                 "process start and on a change. It never authorizes or refuses a reservation; the "
                                 "Work host refuses a Work fork spawn itself when its live probe gets no answer."),
                 "properties": {
        "state": {"type": "string", "enum": ["loaded", "not_loaded", "unknown"]},
        "reason": {"type": ["string", "null"], "enum": [None, "missing", "mismatch", "not_reported", "unreadable"]},
        "gate_version": {"type": ["string", "null"], "maxLength": 32},
        "observed_at": {**ts, "type": ["number", "null"],
                        "description": "When this reported state began (not a freshness time)"},
        "reported_at": {**ts, "type": ["number", "null"]},
        "message": {"type": ["string", "null"],
                    "description": "Server text to show verbatim; null when loaded"}}}
    return {
        "Error": {"type": "object", "required": ["ok", "error"], "properties": {
            "ok": {"type": "boolean", "const": False}, "error": {"type": "string"},
            "message": {"type": "string"}, "version": {"type": ["integer", "null"]}}},
        "CommandResponse": {"type": "object", "properties": {
            "ok": {"type": "boolean"}, "operation": {"type": "string"}, "operation_id": {"type": "string"},
            "version": {"type": "integer"}, "changed": {"type": "boolean"}, "result": {}}},
        "OperatorResponse": {"type": "object", "required": ["ok", "result"], "properties": {
            "ok": {"type": "boolean"}, "session_id": {"type": "string"},
            "operation_id": {"type": "string", "description": "Client key, or a server key for an unkeyed call"},
            "replayed": {"type": "boolean"}, "result": {}}},
        "ParallelView": {"type": "object", "properties": {
            "version": {**_VERSION, "description": "Parallel ledger version"},
            "review_version": {"type": ["integer", "null"], "description": "Finalization journal version"},
            "stranded_version": {"type": ["integer", "null"], "description": "Stranded-v1 journal version"},
            "parallel_reviews": {"type": "array", "items": review},
            "parallel_reviews_ended": {"type": "array", "maxItems": 20, "items": ended},
            "parallel_legacy_reviews": {"type": "array", "maxItems": 50, "items": legacy},
            "parallel_failures": {"type": "array", "maxItems": 50, "items": failure},
            "failures_version": {"type": ["integer", "null"]}, "failures_truncated": {"type": "boolean"},
            "fork_gate": fork_gate,
            "gate_presence_version": {"type": ["integer", "null"],
                                      "description": "Version of the gate presence record; null when unreadable"}}},
    }
