"""Current-session application service for the Work REST API.

The HTTP controller owns transport and current-conversation identity.  This
module owns Work projections and human board commands so every mutation uses
the same tree/store transaction and compare-and-swap rules as MCP.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any

from . import work_plan_tree as tree
from .work_plan_store import WorkPlanConflict

MAX_OPERATION_ID_CHARS = 200
MAX_OPERATION_RECEIPTS = 128
OPERATION_FIELD = "rest_operations"


class WorkOperationConflict(tree.WorkPlanError):
    """An idempotency key was already committed for another request."""


class WorkApplication:
    """Read and mutate one authoritative current-session Work board."""

    def __init__(self, store: Any, *, approval_ask: bool = False) -> None:
        self.store = store
        self.approval_ask = bool(approval_ask)

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

    def command(self, command: str, payload: dict[str, Any], operation_id: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise tree.WorkPlanError("body must be an object")
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_version must be a nonnegative integer")
        key = self._operation_id(operation_id)
        request = {k: v for k, v in payload.items() if k not in ("operation_id", "session_id")}
        digest = hashlib.sha256(json.dumps(
            {"command": command, "request": request}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()

        def mutate(doc: dict[str, Any]) -> dict[str, Any]:
            receipts = doc.setdefault(OPERATION_FIELD, {})
            prior = receipts.get(key)
            if prior is not None:
                if prior["digest"] != digest:
                    raise WorkOperationConflict("operation_id was already used for a different request")
                return copy.deepcopy(prior["response"])
            if doc["version"] != expected:
                raise WorkPlanConflict(
                    f"the plan changed (stored version {doc['version']}, expected {expected}); refetch and retry")
            before = copy.deepcopy(doc)
            result = self._apply(doc, command, request)
            changed = self._without_receipts(doc) != self._without_receipts(before)
            response = {"ok": True, "schema_version": 1, "operation": command,
                        "operation_id": key, "version": doc["version"] + 1,
                        "changed": changed, "result": result}
            receipts[key] = {"digest": digest, "command": command,
                             "created_at": time.time(), "response": copy.deepcopy(response)}
            if len(receipts) > MAX_OPERATION_RECEIPTS:
                oldest = sorted(receipts, key=lambda item: receipts[item]["created_at"])
                for stale in oldest[:len(receipts) - MAX_OPERATION_RECEIPTS]:
                    receipts.pop(stale, None)
            return response

        document, response = self.store.transaction(mutate)
        # A replay can occur after unrelated commits; the exact committed response
        # is intentionally returned rather than being rebuilt from current state.
        return response

    @staticmethod
    def _operation_id(value: Any) -> str:
        if not isinstance(value, str) or not value or len(value) > MAX_OPERATION_ID_CHARS:
            raise tree.WorkPlanError(
                f"Idempotency-Key or operation_id must be a nonempty string of at most {MAX_OPERATION_ID_CHARS} characters")
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise tree.WorkPlanError("operation_id must contain visible ASCII characters only")
        return value

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
            mutable = {"status", "title", "note", "evidence", "ref", "waiting_on", "priority"}
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
                "parallel-orphans-apply", "side-review", "side-handoff", "side-reply-retry")
    paths: dict[str, Any] = {
        f"/ca/work/api/v1/{name}": {"get": {"summary": f"Read current-session Work {name}"}}
        for name in reads
    }
    redaction = ("Credentials, reply routes and tokens, claim and fallback generations, outbox attempt ids "
                 "and main-context previews are never returned. Text is redacted and limited to 16,000 characters.")
    stages = ["received", "processing", "processed", "failed", "quarantined", "duplicate"]
    paths["/ca/work/api/v1/parallel"]["get"]["description"] = (
        "Current-session Work forks grouped by native agent-list evidence, plus aggregate device slot capacity. "
        "Only a native pending/running row observed within 30 seconds is confirmed. The main session is the "
        "coordinator and is never a fork; side actors are excluded. Other sessions expose counts only, never item "
        "or agent metadata. Raw leases, workspace paths, process identities, worker capabilities and fork content "
        "are never returned.")
    paths["/ca/work/api/v1/side"]["get"].update({
        "description": "Pending reviews, main handoffs and a newest-first side receipt page. " + redaction,
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
        "content-free side.changed (no event id; refetch /ca/work/api/v1/side), and content-free "
        "parallel.changed (no event id; refetch /ca/work/api/v1/parallel).")
    for name in commands:
        paths[f"/ca/work/api/v1/commands/{name}"] = {"post": {
            "summary": f"Apply current-session Work command {name}",
            "parameters": [{"in": "header", "name": "Idempotency-Key", "schema": {
                "type": "string", "maxLength": MAX_OPERATION_ID_CHARS}}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["expected_version"],
                "properties": {"expected_version": {"type": "integer", "minimum": 0},
                               "operation_id": {"type": "string", "maxLength": MAX_OPERATION_ID_CHARS},
                               "session_id": {"type": "string"}}}}}}}}
    return {"openapi": "3.1.0", "info": {"title": "Ciel Work REST", "version": "1"},
            "paths": paths}
