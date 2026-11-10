"""Work HTTP controller, mounted after common router access/body guards."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import work_plan_tree as tree
from .work_application import WorkApplication, WorkOperationConflict, openapi_document
from .work_retention import retention_status
from .work_live_settings import settings as live_settings
from .work_mode_launch import work_mode_enabled, work_plan_approval_ask, work_auto_continue_enabled
from .work_plan_store import WorkPlanConflict, session_work_plan_store, subscribe_work_plan_changes

IDENTITY = re.compile(r"(?:claude|codex):[A-Za-z0-9_-]{1,200}\Z")
NODE_FIELDS = frozenset(("status", "title", "note", "evidence", "ref", "waiting_on"))


@dataclass(frozen=True, slots=True)
class WorkHttpServices:
    state_dir: Path
    load_config: Callable[[], dict[str, Any]]
    current_identity: Callable[[], str | None]
    runtime_status: Callable[[str | None], dict[str, Any]]
    write_json: Callable[..., None]
    instance_id: str
    recent_errors: Callable[[str], list[dict[str, Any]]] = lambda _identity: []
    parallel_status: Callable[[str, dict[str, Any]], dict[str, Any]] = lambda _identity, _board: {
        "version": 0, "policy": {}, "active_device": 0, "active_by_priority": {}, "leases": {}}
    save_config: Callable[[dict[str, Any]], Any] | None = None
    parallel_store: Callable[[], Any] | None = None


class WorkHttpController:
    def __init__(self, services: WorkHttpServices) -> None:
        self.services = services
        self._streams = threading.BoundedSemaphore(16)

    def _identity(self, value: Any) -> str:
        current = self.services.current_identity()
        if current is None:
            raise tree.WorkPlanError("current conversation identity unavailable")
        if not isinstance(current, str) or IDENTITY.fullmatch(current) is None:
            raise tree.WorkPlanError("current conversation identity is invalid")
        if value is not None:
            if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
                raise tree.WorkPlanError("session_id must be exact claude:<id> or codex:<id>")
            if value != current:
                raise tree.WorkPlanError("session_id must equal the current conversation identity")
        return current

    def _application(self, identity: str) -> WorkApplication:
        cfg = self.services.load_config()
        return WorkApplication(self._store(identity), approval_ask=work_plan_approval_ask(cfg))

    def _store(self, identity: str) -> Any:
        return session_work_plan_store(self.services.state_dir, identity)

    def board(self, identity: str, doc: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = self.services.load_config()
        doc = self._store(identity).read_snapshot() if doc is None else doc
        payload = tree.snapshot(doc, enabled=work_mode_enabled(cfg), approval_ask=work_plan_approval_ask(cfg))
        payload.update(ok=True, schema_version=1, session_id=identity, updated_at=doc["updated_at"],
                       nodes=doc["nodes"], active_root_id=doc["active_root"],
                       root_ids=[n["id"] for n in doc["nodes"] if n["parent_id"] is None],
                       queue_ids=[n["id"] for n in tree.queued_roots(doc)],
                       auto_continue=work_auto_continue_enabled(cfg), retention=retention_status(cfg),
                       runtime={**self.services.runtime_status(identity),
                                "errors": self.services.recent_errors(identity)},
                       events="/ca/work/events")
        return payload

    def get(self, handler: Any, path: str) -> bool:
        legacy = ("/ca/work", "/ca/work/runtime", "/ca/work/events", "/ca/work/help")
        v1 = ("/ca/work/api/v1/board", "/ca/work/api/v1/state", "/ca/work/api/v1/queue",
              "/ca/work/api/v1/runtime", "/ca/work/api/v1/openapi", "/ca/work/api/v1/events",
              "/ca/work/api/v1/settings", "/ca/work/api/v1/parallel", "/ca/work/api/v1/side",
              "/ca/work/api/v1/side/receipt", "/ca/work/api/v1/audit")
        if path not in (*legacy, *v1):
            return False
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query, keep_blank_values=True)
        try:
            if path == "/ca/work/help":
                self.services.write_json(handler, self.help_payload())
                return True
            supplied = query.get("session_id", [None])
            if len(supplied) != 1:
                raise tree.WorkPlanError("supply session_id only once")
            identity = self._identity(supplied[0])
            if path == "/ca/work/api/v1/openapi":
                # Even discovery is current-session scoped: fail closed when this
                # router is not bound to an authoritative conversation.
                self.services.write_json(handler, openapi_document())
                return True
            application = self._application(identity)
            if path in ("/ca/work/events", "/ca/work/api/v1/events"):
                self._stream(handler, identity, query, v1=path.startswith("/ca/work/api/v1/"))
            elif path in ("/ca/work/runtime", "/ca/work/api/v1/runtime"):
                runtime = self.services.runtime_status(identity)
                runtime["errors"] = self.services.recent_errors(identity)
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                                                   "runtime": runtime})
            elif path == "/ca/work/api/v1/state":
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                    **application.state(enabled=work_mode_enabled(self.services.load_config()))})
            elif path == "/ca/work/api/v1/queue":
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                    **application.queue()})
            elif path == "/ca/work/api/v1/settings":
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                    **live_settings(self.services.load_config(), {}, None)})
            elif path == "/ca/work/api/v1/parallel":
                document = application.document()
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                    **self.services.parallel_status(identity, document)})
            elif path == "/ca/work/api/v1/side":
                document = application.document()
                side = self._side_projection(identity, document)
                from .side_review import with_delivery
                from .side_intake_inbox import SideIntakeInbox
                side["reviews"] = with_delivery(side["reviews"], self.services.state_dir, identity)
                limit = self._single_query(query, "limit", "50")
                if not isinstance(limit, str) or not limit.isdigit():
                    raise tree.WorkPlanError("side receipt limit must be an integer from 1 to 100")
                page = SideIntakeInbox(self.services.state_dir, identity).receipt_page(
                    limit=int(limit), cursor=self._single_query(query, "cursor", ""),
                    stage=self._single_query(query, "stage", ""),
                    priority=self._single_query(query, "priority", ""),
                    search=self._single_query(query, "q", ""))
                side.update({"receipts": page["items"],
                             "enabled": (self.services.load_config().get("work_side_intake") or {}).get(
                                 "enabled") is True,
                             **{key: page[key] for key in ("next_cursor", "has_more", "total", "counts",
                                                           "counts_complete", "quarantined_count", "watermark")}})
                self.services.write_json(handler, side)
            elif path == "/ca/work/api/v1/side/receipt":
                from .side_intake_inbox import SideIntakeInbox
                receipt_id = self._single_query(query, "receipt_id", "")
                detail = SideIntakeInbox(self.services.state_dir, identity).receipt_detail(receipt_id)
                if detail is None:
                    self.services.write_json(handler, {"ok": False, "error": "unknown_side_receipt"}, 404)
                else:
                    self.services.write_json(handler, {"ok": True, "schema_version": 1,
                                                       "session_id": identity, "receipt": detail})
            elif path == "/ca/work/api/v1/audit":
                document = application.document()
                self.services.write_json(handler, self._audit_projection(identity, document))
            else:
                board = self.board(identity)
                if path == "/ca/work/api/v1/board":
                    board["events"] = "/ca/work/api/v1/events"
                self.services.write_json(handler, board)
        except (OSError, tree.WorkPlanError) as exc:
            self._error(handler, exc)
        return True

    @staticmethod
    def _single_query(query: dict[str, list[str]], name: str, default: str) -> str:
        supplied = query.get(name)
        if supplied is None:
            return default
        if len(supplied) != 1:
            raise tree.WorkPlanError(f'supply {name} only once')
        return supplied[0]

    @staticmethod
    def _side_projection(identity: str, document: dict[str, Any]) -> dict[str, Any]:
        reviews = [{"receipt_id": key, **{field: row.get(field) for field in (
            "state", "kind", "action", "item_id", "summary", "recorded_at", "reviewed_at", "verdict")
            if row.get(field) is not None}}
            for key, row in document.get("side_reviews", {}).items()]
        handoffs = [{"event_id": key, **{field: row.get(field) for field in (
            "state", "kind", "action", "item_id", "summary", "question", "waits", "requested_at", "answered_at")
            if row.get(field) is not None}}
            for key, row in document.get("side_handoffs", {}).items()]
        return {"ok": True, "schema_version": 1, "session_id": identity,
                "version": document["version"], "reviews": reviews[-100:], "handoffs": handoffs[-100:]}

    @staticmethod
    def _audit_projection(identity: str, document: dict[str, Any]) -> dict[str, Any]:
        operations = sorted(({
            "operation_id": key,
            "command": row.get("command"),
            "created_at": row.get("created_at"),
            "version": (row.get("response") or {}).get("version"),
            "changed": (row.get("response") or {}).get("changed"),
        } for key, row in document.get("rest_operations", {}).items()),
            key=lambda row: float(row.get("created_at") or 0), reverse=True)
        return {"ok": True, "schema_version": 1, "session_id": identity,
                "version": document["version"], "events": operations[:100]}

    def _error(self, handler: Any, exc: Exception) -> None:
        unavailable = isinstance(exc, OSError) or "stored" in str(exc) or "invalid Work plan" in str(exc) or "snapshot" in str(exc)
        self.services.write_json(handler, {"ok": False, "error": "state_unavailable" if unavailable else "invalid_request",
                                           "message": "Work state unreadable" if unavailable else str(exc)},
                                 503 if unavailable else 422)

    def post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        actions = {"/ca/work/nodes/" + n: n for n in ("add", "update", "remove", "reorder")}
        commands = {"/ca/work/api/v1/commands/" + name.replace("_", "-"): name for name in (
            "plan_set", "node_add", "node_update", "node_remove", "active_select", "execute", "replan",
            "sibling_reorder", "queue_position", "focus", "urgent")}
        operator_actions = {"/ca/work/api/v1/commands/" + name.replace("_", "-"): name for name in (
            "settings_update", "parallel_configure", "parallel_orphans_preview", "parallel_orphans_apply",
            "side_review", "side_handoff", "side_reply_retry")}
        if path in operator_actions:
            return self._post_operator(handler, operator_actions[path], body)
        if path in commands:
            return self._post_v1(handler, commands[path], body)
        if path not in actions:
            return False
        try:
            if not isinstance(body, dict):
                raise tree.WorkPlanError("body must be an object")
            identity = self._identity(body.get("session_id"))
            version = body.get("expected_version")
            if type(version) is not int or version < 0:
                raise tree.WorkPlanError("expected_version must be a nonnegative integer")
            if not work_mode_enabled(self.services.load_config()):
                self.services.write_json(handler, {"ok": False, "error": "work_disabled"}, 409)
                return True
            action = actions[path]
            allowed = {"session_id", "expected_version"}
            allowed |= ({"parent_id", "title", "note", "children", "ref", "waiting_on"} if action == "add" else
                        {"id", *NODE_FIELDS} if action == "update" else
                        {"id", "position", "user_requested"} if action == "reorder" else {"id"})
            if set(body) - allowed:
                raise tree.WorkPlanError("unsupported mutation fields")
            if action != "add" and (not isinstance(body.get("id"), str) or not body["id"]):
                raise tree.WorkPlanError("id must be a nonempty string")
            for field in NODE_FIELDS - {"status"}:
                if field in body and not isinstance(body[field], str):
                    raise tree.WorkPlanError(field + " must be a string")
            if "user_requested" in body and type(body["user_requested"]) is not bool:
                raise tree.WorkPlanError("user_requested must be boolean")
            if "parent_id" in body and body["parent_id"] is not None and not isinstance(body["parent_id"], str):
                raise tree.WorkPlanError("parent_id must be null or a node id")

            def mutate(doc: dict[str, Any]) -> Any:
                if action == "add":
                    return tree.add_subtree(doc, body.get("parent_id"), {
                        k: body[k] for k in ("title", "note", "children", "ref", "waiting_on") if k in body})
                if not any(n["id"] == body["id"] for n in doc["nodes"]):
                    raise KeyError("unknown_node")
                if action == "update":
                    if not (set(body) & NODE_FIELDS):
                        raise tree.WorkPlanError("at least one editable field is required")
                    return tree.update_node(doc, body["id"], **{k: body[k] for k in NODE_FIELDS if k in body})
                if action == "remove":
                    return tree.remove_node(doc, body["id"])
                return tree.reorder_node(doc, body["id"], body.get("position"),
                                         user_requested=body.get("user_requested") is True)

            doc, _ = self._store(identity).transaction(mutate, expected_version=version)
            self.services.write_json(handler, dict(self.board(identity, doc), changed=doc["version"] != version,
                                                   operation=action))
        except WorkPlanConflict:
            try:
                current = self._store(identity).read_snapshot()["version"]
            except (OSError, tree.WorkPlanError):
                current = None
            self.services.write_json(handler, {"ok": False, "error": "version_conflict", "version": current}, 409)
        except KeyError:
            self.services.write_json(handler, {"ok": False, "error": "unknown_node"}, 404)
        except (OSError, tree.WorkPlanError) as exc:
            self._error(handler, exc)
        return True

    def _post_v1(self, handler: Any, command: str, body: dict[str, Any]) -> bool:
        identity = ""
        try:
            if not isinstance(body, dict):
                raise tree.WorkPlanError("body must be an object")
            identity = self._identity(body.get("session_id"))
            if not work_mode_enabled(self.services.load_config()):
                self.services.write_json(handler, {"ok": False, "error": "work_disabled"}, 409)
                return True
            header_key = handler.headers.get("Idempotency-Key")
            body_key = body.get("operation_id")
            if header_key is not None and body_key is not None and header_key != body_key:
                raise tree.WorkPlanError("Idempotency-Key and operation_id must match")
            payload = {key: value for key, value in body.items() if key not in ("session_id", "operation_id")}
            response = self._application(identity).command(command, payload, header_key or body_key)
            response["session_id"] = identity
            self.services.write_json(handler, response)
        except WorkOperationConflict as exc:
            self.services.write_json(handler, {"ok": False, "error": "idempotency_conflict",
                                               "message": str(exc)}, 409)
        except WorkPlanConflict:
            try:
                current = self._store(identity).read_snapshot()["version"] if identity else None
            except (OSError, tree.WorkPlanError):
                current = None
            self.services.write_json(handler, {"ok": False, "error": "version_conflict", "version": current}, 409)
        except KeyError:
            self.services.write_json(handler, {"ok": False, "error": "unknown_node"}, 404)
        except (OSError, tree.WorkPlanError, ValueError) as exc:
            self._error(handler, exc)
        return True

    def _post_operator(self, handler: Any, action: str, body: dict[str, Any]) -> bool:
        try:
            if not isinstance(body, dict):
                raise tree.WorkPlanError("body must be an object")
            identity = self._identity(body.get("session_id"))
            if action == "settings_update":
                if self.services.save_config is None:
                    raise tree.WorkPlanError("Work settings are read-only")
                result = live_settings(self.services.load_config(), {
                    "key": body.get("key"), "value": body.get("value")}, self.services.save_config)
            elif action == "parallel_configure":
                if self.services.parallel_store is None:
                    raise tree.WorkPlanError("parallel controls are unavailable")
                store = self.services.parallel_store()
                state, _ = store.configure(body.get("changes"), capability_ready=True,
                                           expected_version=body.get("expected_version"))
                result = self.services.parallel_status(identity, self._store(identity).read_snapshot())
                if self.services.save_config is not None:
                    config = self.services.load_config()
                    config["work_parallel"] = state["policy"]
                    self.services.save_config(config)
            elif action in ("parallel_orphans_preview", "parallel_orphans_apply"):
                if self.services.parallel_store is None:
                    raise tree.WorkPlanError("parallel controls are unavailable")
                from .work_parallel_mcp import _orphan_preview
                store = self.services.parallel_store()
                preview = _orphan_preview(store)
                if action.endswith("preview"):
                    result = preview
                else:
                    if body.get("preview_digest") != preview["digest"]:
                        raise tree.WorkPlanError("stale or invalid orphan preview")
                    row = next((item for item in preview["leases"]
                                if item["lease_id"] == body.get("lease_id")), None)
                    if row is None or row["owner_state"] != "dead":
                        raise tree.WorkPlanError("only a positively dead owner can be released")
                    observation = {"owner_state": row["owner_state"],
                                   "observed_identity": row["observed_identity"]}
                    state, _ = store.orphan_release(row["lease_id"], proof="authenticated web operator",
                        observation=observation, expected_version=body.get("expected_version"))
                    result = {"lease_id": row["lease_id"], "version": state["version"],
                              "state": state["leases"][row["lease_id"]]["state"]}
            elif action == "side_review":
                from .side_review import review
                result = review(self._store(identity), body.get("receipt_ids"), verdict=body.get("verdict"),
                                note=body.get("note", ""), expected_version=body.get("expected_version"))
            elif action == "side_handoff":
                from .side_handoff import acknowledge
                from .side_intake_inbox import SideIntakeInbox
                from .side_intake_mcp import (_answer_followup, _followup, _handoff_needs_followup,
                                              _sync_handoff_receipt)
                event_id, answer = body.get("event_id"), body.get("answer", "")
                inbox = SideIntakeInbox(self.services.state_dir, identity)
                original = inbox._load(event_id) if isinstance(event_id, str) else None
                # Same sequence as MCP work_side_handoff: a follow-up that cannot
                # be represented must not commit the board acknowledgement first.
                if not isinstance(answer, str):
                    raise tree.WorkPlanError("coordinator answer must be text")
                if (answer and original and _handoff_needs_followup(original)
                        and _followup(event_id, answer, original) is None):
                    raise tree.WorkPlanError(
                        "coordinator follow-up exceeds inbox record capacity; no acknowledgment committed")
                result = acknowledge(self._store(identity), event_id, answer=answer,
                    activate=body.get("activate", False), clear_wait=body.get("clear_wait"),
                    expected_version=body.get("expected_version"))
                _sync_handoff_receipt(inbox, event_id, result)
                if answer:
                    _answer_followup(inbox, event_id, answer)
            else:
                from .side_reply_outbox import retry
                result = retry(self.services.state_dir, identity, body.get("receipt_id"),
                               body.get("expected_state"), body.get("confirmed", False))
            self.services.write_json(handler, {"ok": True, "schema_version": 1,
                                               "session_id": identity, "result": result})
        except (OSError, tree.WorkPlanError, ValueError) as exc:
            self._error(handler, exc)
        return True

    def _stream(self, handler: Any, identity: str, query: dict[str, list[str]], *, v1: bool = False) -> None:
        if not self._streams.acquire(blocking=False):
            self.services.write_json(handler, {"ok": False, "error": "stream_capacity"}, 429)
            return
        opened = False
        def unsubscribe() -> None:
            return None
        try:
            store = self._store(identity)
            wake = threading.Event()
            target = store.path.resolve()
            unsubscribe = subscribe_work_plan_changes(
                lambda path, _before, _after: wake.set() if path.resolve() == target else None)
            doc = store.read_snapshot()
            cursor = handler.headers.get("Last-Event-ID") or (query.get("after") or [""])[0]
            prefix = self.services.instance_id + ":"
            valid = (isinstance(cursor, str) and len(cursor) <= len(prefix) + 20
                     and cursor.startswith(prefix) and re.fullmatch(r"[0-9]+", cursor[len(prefix):]) is not None)
            previous = int(cursor[len(prefix):]) if valid else None
            handler.send_response(200)
            handler.send_header("content-type", "text/event-stream")
            handler.send_header("cache-control", "no-cache")
            handler.send_header("connection", "close")
            handler.end_headers()
            opened = True
            error_cursor = json.dumps(self.services.recent_errors(identity), sort_keys=True)
            from .side_intake_inbox import SideIntakeInbox
            side_inbox = SideIntakeInbox(self.services.state_dir, identity)
            side_cursor = side_inbox.watermark()
            refetch = "/ca/work/api/v1/board" if v1 else "/ca/work"
            self._event(handler, identity, doc, "work.snapshot" if not cursor else "work.resync_required", previous,
                        refetch=refetch)
            version = doc["version"]
            heartbeat = time.monotonic()
            while True:
                wake.clear()
                current = store.read_snapshot()
                if current["version"] != version:
                    self._event(handler, identity, current, "work.changed", version, doc, refetch=refetch)
                    doc = current
                    version = current["version"]
                    heartbeat = time.monotonic()
                elif time.monotonic() - heartbeat >= 15:
                    handler.wfile.write(b": heartbeat" + bytes([10, 10]))
                    handler.wfile.flush()
                    heartbeat = time.monotonic()
                latest_errors = self.services.recent_errors(identity)
                serialized_errors = json.dumps(latest_errors, sort_keys=True)
                if serialized_errors != error_cursor:
                    runtime_refetch = "/ca/work/api/v1/runtime" if v1 else "/ca/work/runtime"
                    frame = chr(10).join(("event: runtime.error", "data: " + json.dumps({
                        "session_id": identity, "refetch": runtime_refetch}), "", ""))
                    handler.wfile.write(frame.encode("utf-8"))
                    handler.wfile.flush()
                    error_cursor = serialized_errors
                latest_side = side_inbox.watermark()
                # None is a momentarily unreadable marker: keep the cursor. An
                # unreadable initial marker yields at most one extra refetch.
                if latest_side is not None and latest_side != side_cursor:
                    # Content-free invalidation; no id line keeps board
                    # Last-Event-ID reconciliation unchanged.
                    frame = chr(10).join(("event: side.changed", "data: " + json.dumps({
                        "session_id": identity, "watermark": latest_side,
                        "refetch": "/ca/work/api/v1/side", "coalesced": True}), "", ""))
                    handler.wfile.write(frame.encode("utf-8"))
                    handler.wfile.flush()
                    side_cursor = latest_side
                    heartbeat = time.monotonic()
                wake.wait(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (OSError, tree.WorkPlanError):
            if not opened:
                raise
            # Headers are committed; terminate the stream, never append JSON.
            handler.close_connection = True
        finally:
            unsubscribe()
            self._streams.release()

    def _event(self, handler: Any, identity: str, doc: dict[str, Any], kind: str, previous: int | None,
               before: dict[str, Any] | None = None, *, refetch: str = "/ca/work") -> None:
        event = {"session_id": identity, "version": doc["version"], "previous_version": previous,
                 "updated_at": doc["updated_at"], "refetch": refetch, "coalesced": True}
        if before is not None:
            old = {n["id"]: n for n in before["nodes"]}
            new = {n["id"]: n for n in doc["nodes"]}
            changed = {key for key in old.keys() | new.keys() if old.get(key) != new.get(key)}
            old_order = [n["id"] for n in before["nodes"]]
            new_order = [n["id"] for n in doc["nodes"]]
            if old_order != new_order:
                changed.update(key for index, key in enumerate(new_order)
                               if index >= len(old_order) or key != old_order[index])
            event.update(operation="commit", affected_ids=sorted(changed))
        frame = chr(10).join(("id: " + self.services.instance_id + ":" + str(doc["version"]),
                              "event: " + kind, "data: " + json.dumps(event), "", ""))
        handler.wfile.write(frame.encode("utf-8"))
        handler.wfile.flush()

    @staticmethod
    def help_payload() -> dict[str, Any]:
        return {"ok": True, "schema_version": 1, "board": "GET /ca/work?session_id=<current identity>",
                "v1": {"board": "GET /ca/work/api/v1/board", "state": "GET /ca/work/api/v1/state",
                       "queue": "GET /ca/work/api/v1/queue", "runtime": "GET /ca/work/api/v1/runtime",
                       "events": "GET /ca/work/api/v1/events", "openapi": "GET /ca/work/api/v1/openapi",
                       "side": "GET /ca/work/api/v1/side?limit=1-100&cursor=<next_cursor>&stage=<stage>&priority=P1..P5&q=<text>",
                       "side_receipt": "GET /ca/work/api/v1/side/receipt?receipt_id=<64 lowercase hex>",
                       "commands": "POST /ca/work/api/v1/commands/<command> with Idempotency-Key or operation_id"},
                "side_receipts": "Current-session receipts only, newest activity first. Stages: received, processing, processed, failed, quarantined, duplicate. Filters and search use bounded public fields. Credentials, reply routes, claim/fallback generations, outbox attempts and main-context previews are never returned; originals and replies are redacted and limited to 16,000 characters.",
                "runtime": "GET /ca/work/runtime", "events": "GET /ca/work/events",
                "mutations": ["POST /ca/work/nodes/" + n for n in ("add", "update", "remove", "reorder")],
                "authentication": "Existing administrator Bearer token or signed-in web account; external access must already be enabled.",
                "concurrency": "Read version; supply expected_version for every mutation; 409 means refetch, never blind retry.",
                "notifications": "Version invalidations sampled each second; may coalesce commits. Last-Event-ID reconciles latest snapshot, not durable patch replay. side.changed is content-free, has no event id and asks for a /side refetch. Refetch after snapshot, change, reset or reconnect.",
                "retention": {"completed_after_seconds": 86400, "toggle": "POST /ca/config/llm option work_completed_auto_delete on/off"},
                "ordering": "One-based sibling positions; root positions order unfinished goals; active-slot changes need user_requested=true. No reparenting."}
