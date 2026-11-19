"""Work HTTP controller, mounted after common router access/body guards."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import work_plan_tree as tree
from .work_application import (OUTCOMES, WorkApplication, WorkOperationConflict, WorkOperationOutcomeUnknown,
                               WorkOperationReceiptsFull, WorkOperationResolutionError, openapi_document)
from .work_retention import retention_status
from .work_live_settings import FINGERPRINT_FIELD, SettingsConflict, settings as live_settings
from .work_live_settings import settings_fingerprint
from .work_mode_launch import work_mode_enabled, work_plan_approval_ask, work_auto_continue_enabled
from .work_parallel import WorkParallelConflict, WorkParallelStateUnavailable
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
    # Set only by an in-process caller (ciel-runtimectl): {"source": "ctl",
    # "os_user": <name>, "pid": <int>}. The REST runtime never sets it, so a
    # network request can never claim this origin.
    operator_origin: dict[str, Any] | None = None
    # Safe session restart is runtime control, not a Work mutation. The live
    # router wires these ports; offline/ctl controllers keep it unavailable.
    restart_status: Callable[[], dict[str, Any]] | None = None
    restart_queue: Callable[..., dict[str, Any]] | None = None


from .work_workflow_http import WorkflowPromptConflict  # noqa: E402 - approval workflow 409


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
        nodes = doc["nodes"]
        if self.services.parallel_store is not None:
            try:
                from .work_parallel_p0 import p0_store
                side = p0_store(self.services.parallel_store())
                nodes = [{**node, "effective_priority": side.effective_priority(identity, node, node.get("priority", ""))}
                         if node.get("parent_id") is None else node for node in nodes]
            except (OSError, ValueError):
                pass
        payload.update(ok=True, schema_version=1, session_id=identity, updated_at=doc["updated_at"],
                       nodes=nodes, active_root_id=doc["active_root"],
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
        from .work_workflow_http import PATHS as WORKFLOW_PATHS
        if path not in (*legacy, *v1, *WORKFLOW_PATHS):
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
                runtime["restart"] = self._restart_status()
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
                self.services.write_json(handler, {"ok": True, "schema_version": 2,
                    **self._public_parallel(identity, document)})
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
                side["review_delivery"] = self._review_delivery(identity, document)
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
            elif path in WORKFLOW_PATHS:
                from .work_workflow_http import get_payload
                self.services.write_json(handler, get_payload(self, identity, path, query))
            elif path == "/ca/work/api/v1/audit":
                document = application.document()
                self.services.write_json(handler, self._audit_projection(
                    identity, document, application.audit_receipts()))
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

    def _review_delivery(self, identity: str, board: dict[str, Any]) -> dict[str, Any]:
        """Read-only review_delivery sidecar state of this session's pending reviews.

        The lease ids are the ones /parallel shows for this session (the same
        projection the host reads); the view itself is bounded and secret-free
        (review_delivery.public_view). Unavailable without the parallel store.
        """
        unavailable = {"available": False, "rows": [], "truncated": False}
        if self.services.parallel_store is None:
            return unavailable
        try:
            projected = self.services.parallel_status(identity, board)
            pending = (projected.get("review") or {}).get("pending") or []
            leases = [row.get("lease_id") for row in pending if isinstance(row, dict)]
            from .review_delivery import ReviewDeliveryStore, public_view
            from .work_parallel_mcp import finalization
            return public_view(ReviewDeliveryStore.beside(finalization(self.services.parallel_store())), leases,
                               identity)
        except (OSError, ValueError, tree.WorkPlanError):
            return unavailable

    def _review_delivery_print(self, identity: str, board: dict[str, Any]) -> tuple[str, bool]:
        """What the Side tab's Review delivery section shows, for /events.

        Live while a re-append is submitting and not yet uncertain: it can turn
        uncertain by age alone (60 s, no write), so the stream re-reads it.
        """
        view = self._review_delivery(identity, board)
        rows = view.get("rows") or []
        printed = [(row.get("lease_id"), row.get("state"), row.get("reappends"), row.get("reappend_state"),
                    row.get("reappend_uncertain")) for row in rows]
        live = any(row.get("reappend_state") == "submitting" and row.get("reappend_uncertain") is not True
                   for row in rows)
        return json.dumps([view.get("available"), view.get("truncated"), printed], sort_keys=True), live

    @staticmethod
    def _side_review_public(key: Any, row: dict[str, Any]) -> dict[str, Any]:
        """One side review as GET /side lists it: never the side's reply, source or note."""
        return {"receipt_id": key, **{field: row.get(field) for field in (
            "state", "kind", "action", "item_id", "summary", "recorded_at", "reviewed_at", "verdict")
            if row.get(field) is not None}}

    @staticmethod
    def _side_projection(identity: str, document: dict[str, Any]) -> dict[str, Any]:
        reviews = [WorkHttpController._side_review_public(key, row)
                   for key, row in document.get("side_reviews", {}).items()]
        from .side_wake import redact_wake
        handoffs = [{"event_id": key, **{field: row.get(field) for field in (
            "state", "kind", "action", "item_id", "summary", "question", "waits", "time_limit", "queue",
            "requested_at", "answered_at") if row.get(field) is not None},
            # Shared redactor: attempt_ref names the exact attempt; the raw
            # attempt id, owner fence and receipt are never projected.
            "wake": redact_wake(row.get("wake")),
            "delivery_uncertain": row.get("state") == "requested" and redact_wake(row.get("wake"))["delivery_uncertain"]}
            for key, row in document.get("side_handoffs", {}).items()]
        return {"ok": True, "schema_version": 1, "session_id": identity,
                "version": document["version"], "reviews": reviews[-100:], "handoffs": handoffs[-100:]}

    @staticmethod
    def _audit_projection(identity: str, document: dict[str, Any],
                          receipts: dict[str, Any] | None = None) -> dict[str, Any]:
        source_rows = document.get("rest_operations", {}) if receipts is None else receipts
        operations = sorted(({
            "operation_id": key,
            "command": row.get("command"),
            "created_at": row.get("created_at"),
            "version": (row.get("response") or {}).get("version"),
            "changed": (row.get("response") or {}).get("changed"),
            "source": row.get("source") if row.get("source") in ("web", "rest", "ctl") else None,
            "state": row.get("state") if row.get("state") in OUTCOMES else "committed",
            "outcome": OUTCOMES.get(row.get("state"), "applied"),
            "receipt_version": int(row.get("receipt_version") or 1),
            "last_reconcile": ({"result": row["last_reconcile"].get("result"), "at": row["last_reconcile"].get("at")}
                               if isinstance(row.get("last_reconcile"), dict) else None),
            **({"resolution": {key: row["resolution"].get(key) for key in ("command", "source", "at")}}
               if isinstance(row.get("resolution"), dict) else {}),
            **({"origin": {key: (row.get("origin") or {}).get(key) for key in ("auth", "os_user", "pid")
                           if (row.get("origin") or {}).get(key) is not None}}
               if isinstance(row.get("origin"), dict) else {}),
            # Text-free by construction (work_application.request_summary).
            **({"request": dict(row["request"])} if isinstance(row.get("request"), dict) else {}),
        } for key, row in source_rows.items()),
            key=lambda row: float(row.get("created_at") or 0), reverse=True)
        # Newest 100, plus every outcome-unknown operation (at most 128): newer
        # rows never hide an unresolved operation from the person (R3).
        events = [row for index, row in enumerate(operations) if index < 100 or row["state"] == "prepared"]
        return {"ok": True, "schema_version": 1, "session_id": identity,
                "version": document["version"], "events": events}

    # Unreadable stored state, by type: the board store, the parallel ledger and
    # its journals, the failure log. The 503 body is fixed; the stored detail is
    # never sent. Every other refusal is 422 with its own message.
    UNAVAILABLE_ERRORS = (OSError, tree.WorkStateUnavailable, WorkParallelStateUnavailable)

    def _error(self, handler: Any, exc: Exception) -> None:
        unavailable = isinstance(exc, self.UNAVAILABLE_ERRORS)
        self.services.write_json(handler, {"ok": False, "error": "state_unavailable" if unavailable else "invalid_request",
                                           "message": "Work state unreadable" if unavailable else str(exc)},
                                 503 if unavailable else 422)

    RESTART_PATH = "/ca/work/api/v1/runtime/restart"
    RESTART_FIELDS = frozenset(("session_id", "reason", "runtime", "resume", "client_pid", "confirmed"))

    def _restart_status(self) -> dict[str, Any]:
        """Read-only safe-restart view: live clients, the queued request and input holds."""
        if self.services.restart_status is None:
            return {"available": False}
        try:
            return {"available": True, **self.services.restart_status()}
        except (OSError, ValueError) as exc:
            return {"available": False, "error": type(exc).__name__}

    def restart(self, identity: str, body: dict[str, Any], origin: dict[str, Any]) -> dict[str, Any]:
        """Queue a safe restart of this router instance's live CLI client.

        The same RuntimeSessionRestartService.queue the MCP tool, the slash
        command and ``ciel-runtimectl restart-session`` use. Only this live
        instance's active clients are targets; a pid of another instance is
        refused by the service. The person confirms first (confirmed=true).
        """
        if self.services.restart_queue is None:
            raise tree.WorkPlanError("session restart is unavailable in this process")
        if set(body) - self.RESTART_FIELDS:
            raise tree.WorkPlanError("unsupported restart fields")
        if body.get("confirmed") is not True:
            raise tree.WorkPlanError("a session restart requires explicit confirmation (confirmed=true)")
        reason = body.get("reason", "")
        runtime = body.get("runtime", "")
        if not isinstance(reason, str) or len(reason) > 200 or any(ord(char) < 32 for char in reason):
            raise tree.WorkPlanError("reason must be a single-line string of at most 200 characters")
        if runtime not in ("", "claude", "codex"):
            raise tree.WorkPlanError("runtime must be claude, codex or empty")
        resume = body.get("resume", True)
        if type(resume) is not bool:
            raise tree.WorkPlanError("resume must be boolean")
        client_pid = body.get("client_pid", 0)
        if type(client_pid) is not int or client_pid < 0:
            raise tree.WorkPlanError("client_pid must be a nonnegative integer")
        source = "web" if origin.get("source") == "web" else "rest"
        result = self.services.restart_queue(source=source, reason=reason.strip(), runtime=runtime,
                                             client_pid=client_pid, resume=resume)
        request = result.get("request") or {}
        target = result.get("target") or {}
        refusal = {} if result.get("ok") else {"error": "restart_not_queued",
                                               "message": str(result.get("detail") or "restart was not queued")}
        return {**refusal, "ok": bool(result.get("ok")), "schema_version": 1, "session_id": identity,
                "queued": bool(result.get("queued")), "source": source,
                "request": {key: request.get(key) for key in (
                    "id", "source", "reason", "runtime", "target_pid", "resume", "requested_at", "expires_at")
                    if key in request} or None,
                "target_pid": target.get("pid"), "detail": result.get("detail"),
                "restart": self._restart_status()}

    def _post_restart(self, handler: Any, body: dict[str, Any]) -> bool:
        try:
            if not isinstance(body, dict):
                raise tree.WorkPlanError("body must be an object")
            identity = self._identity(body.get("session_id"))
            result = self.restart(identity, body, self._request_origin(handler))
            self.services.write_json(handler, result, 200 if result["ok"] else 409)
        except (OSError, tree.WorkPlanError) as exc:
            self._error(handler, exc)
        return True

    def post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        if path == self.RESTART_PATH:
            return self._post_restart(handler, body)
        actions = {"/ca/work/nodes/" + n: n for n in ("add", "update", "remove", "reorder")}
        commands = {"/ca/work/api/v1/commands/" + name.replace("_", "-"): name for name in (
            "plan_set", "node_add", "node_update", "node_remove", "active_select", "execute", "replan",
            "sibling_reorder", "queue_position", "focus", "urgent")}
        operator_actions = {"/ca/work/api/v1/commands/" + name.replace("_", "-"): name for name in (
            "settings_update", "parallel_configure", "parallel_orphans_preview", "parallel_orphans_apply",
            "parallel_orphans_reconcile",
            "parallel_item_review", "parallel_launch_resolve", "parallel_legacy_accept",
            "side_review", "side_handoff", "side_reply_retry",
            "side_wake_retry", "side_notice_retry", "side_reappend_retry", "review_reappend_retry", "side_drain",
            "operation_reconcile", "operation_release",
            "workflow_resolve", "workflow_delegate", "workflow_prompt_set", "workflow_prompt_reset")}
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
            # Same server-derived origin as operator commands; never part of the digest.
            priority = payload.get("priority")
            has_priority = ((command == "node_update" and "priority" in payload)
                            or (command == "plan_set" and "priority" in payload
                                and (payload.get("root_id") or priority == "P0")))
            needs_service = has_priority and self.services.parallel_store is not None
            response = self._application(identity).command(
                command, payload, header_key or body_key, origin=self._request_origin(handler),
                scope=identity if needs_service else None,
                parallel_store=self.services.parallel_store() if needs_service and self.services.parallel_store else None)
            response["session_id"] = identity
            self.services.write_json(handler, response)
        except WorkOperationReceiptsFull as exc:
            self.services.write_json(handler, {"ok": False, "error": "operator_receipts_full",
                                               "message": str(exc)}, 409)
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

    # Reads have no effect to audit or replay.
    OPERATOR_READS = frozenset(("parallel_orphans_preview", "side_drain"))
    # A person's resolution of one outcome-unknown receipt (work_operation_reconcile).
    OPERATOR_RESOLUTIONS = frozenset(("operation_reconcile", "operation_release"))

    def _operation_resolution(self, identity: str, action: str, body: dict[str, Any], source: str) -> dict[str, Any]:
        """operation-reconcile observes, read-only, whether the effect applied;
        operation-release gives up on an outcome no stored fact can settle."""
        if body.get("confirmed") is not True:
            raise tree.WorkPlanError(action.replace("_", "-") + " requires explicit confirmation (confirmed=true)")
        application = self._application(identity)
        if action == "operation_release":
            return application.operation_release(body.get("operation_id"), body.get("expected_receipt_version"),
                                                 source=source)
        receipt = application.operation_receipt(body.get("operation_id"))
        if receipt.get("state") != "prepared":
            raise WorkOperationResolutionError("this operation's outcome is already known; reread /audit")
        from .work_operation_reconcile import observe
        result, observed = observe(str(receipt.get("command")), receipt.get("request") or {}, identity=identity,
                                   board=self._store(identity).read_snapshot(),
                                   parallel_store=self.services.parallel_store)
        return application.operation_reconcile_record(body.get("operation_id"),
                                                      int(receipt.get("receipt_version") or 1), result, observed,
                                                      source=source)
    MAX_RECEIPT_RESULT_BYTES = 8192

    def _post_operator(self, handler: Any, action: str, body: dict[str, Any]) -> bool:
        identity = ""
        try:
            if not isinstance(body, dict):
                raise tree.WorkPlanError("body must be an object")
            identity = self._identity(body.get("session_id"))
            self.services.write_json(handler, self.operator(
                identity, action, body, handler.headers.get("Idempotency-Key"),
                origin=self._request_origin(handler)))
        except WorkOperationOutcomeUnknown as exc:
            self.services.write_json(handler, {"ok": False, "error": "operation_outcome_unknown",
                                               "message": str(exc)}, 409)
        except WorkOperationReceiptsFull as exc:
            self.services.write_json(handler, {"ok": False, "error": "operator_receipts_full",
                                               "message": str(exc)}, 409)
        except WorkOperationResolutionError as exc:
            self.services.write_json(handler, {"ok": False, "error": exc.code, "message": str(exc)}, exc.status)
        except WorkflowPromptConflict as exc:
            self.services.write_json(handler, {"ok": False, "error": "prompt_conflict", "message": str(exc)}, 409)
        except SettingsConflict as exc:
            try:
                current = settings_fingerprint(self.services.load_config())
            except (OSError, ValueError):
                current = None
            self.services.write_json(handler, {"ok": False, "error": "settings_conflict", "message": str(exc),
                                               "settings_fingerprint": current}, 409)
        except WorkOperationConflict as exc:
            self.services.write_json(handler, {"ok": False, "error": "idempotency_conflict",
                                               "message": str(exc)}, 409)
        except WorkParallelConflict as exc:
            self.services.write_json(handler, {"ok": False, "error": "version_conflict", "message": str(exc)}, 409)
        except WorkPlanConflict:
            # A stale board version on an operator write (side review, handoff):
            # the same answer as a board command, with the current version.
            try:
                current = self._store(identity).read_snapshot()["version"] if identity else None
            except (OSError, tree.WorkPlanError):
                current = None
            self.services.write_json(handler, {"ok": False, "error": "version_conflict", "version": current}, 409)
        except (OSError, tree.WorkPlanError, ValueError) as exc:
            self._error(handler, exc)
        return True

    # A receipt keeps only these result keys, each a bool, a number, null or a
    # short id-like word: never text, a path, a token or a nested value.
    RECEIPT_SUMMARY_KEYS = ("lease_id", "item_id", "receipt_id", "event_id", "key", "state", "decision",
                            "version", "finalization_version", "stranded_version", "review_journal",
                            "attempt", "launch_ref", "owner_state", "priority", "changed", "replayed",
                            "replacement_generation", "revision", "admitted")
    _RECEIPT_WORD = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")

    @classmethod
    def receipt_summary(cls, result: Any) -> dict[str, Any]:
        """The bounded, text-free summary an operator receipt stores and a replay returns."""
        if isinstance(result, list):
            return {"rows": len(result)}
        if not isinstance(result, dict):
            return {}
        summary: dict[str, Any] = {}
        for key in cls.RECEIPT_SUMMARY_KEYS:
            value = result.get(key)
            if value is None or type(value) in (bool, int, float):
                if key in result:
                    summary[key] = value
            elif isinstance(value, str) and cls._RECEIPT_WORD.fullmatch(value):
                summary[key] = value
        for key in ("rows", "receipts", "reviews", "handoffs"):
            if isinstance(result.get(key), list):
                summary[key + "_count"] = len(result[key])
        return summary

    OPERATOR_PROOFS = {"web": "authenticated web operator", "rest": "authenticated REST operator",
                       "ctl": "ciel-runtimectl operator"}

    def _request_origin(self, handler: Any) -> dict[str, Any]:
        """Who issued an operator command, derived by the server, never claimed.

        An in-process ciel-runtimectl controller carries its trusted origin in
        its services. A network request is ``rest`` when it presents the
        administrator token (Bearer or x-ciel-runtime-token, the router's first
        check) and ``web`` when it presents only the signed-in session cookie; a
        loopback request without credentials is ``rest``. No token, cookie or
        header value is recorded, and X-Ciel-Operator-Source is ignored.
        """
        trusted = self.services.operator_origin
        if trusted is not None:
            return self._ctl_origin(trusted)
        headers = handler.headers
        authorization = str(headers.get("authorization") or headers.get("Authorization") or "")
        if authorization.lower().startswith("bearer ") and authorization[7:].strip():
            return {"source": "rest", "auth": "bearer"}
        if str(headers.get("x-ciel-runtime-token") or headers.get("x-api-key") or "").strip():
            return {"source": "rest", "auth": "token"}
        from .web_access_http import session_token
        if session_token(handler):
            return {"source": "web", "auth": "session"}
        return {"source": "rest", "auth": "loopback"}

    @staticmethod
    def _ctl_origin(value: Any) -> dict[str, Any]:
        if (not isinstance(value, dict) or value.get("source") != "ctl"
                or not isinstance(value.get("os_user"), str) or not 0 < len(value["os_user"]) <= 64
                or any(ord(char) < 32 for char in value["os_user"])
                or type(value.get("pid")) is not int or value["pid"] <= 0):
            raise tree.WorkPlanError("in-process operator origin must be {source: ctl, os_user, pid}")
        return {"source": "ctl", "os_user": value["os_user"], "pid": value["pid"]}

    def operator(self, identity: str, action: str, body: dict[str, Any],
                 idempotency_key: str | None = None, *, origin: dict[str, Any] | None = None) -> dict[str, Any]:
        """One operator command: the single path for the web console, REST and ciel-runtimectl.

        Same key rules as the board commands: the Idempotency-Key header and the
        body operation_id must match when both are given. A committed key replays
        its stored response without repeating the effect; the same key with a
        different request is an idempotency conflict. Every successful effect is
        recorded in the session audit trail (GET /ca/work/api/v1/audit), including
        a call without a key, which gets a server key that a client never reuses.
        """
        if not isinstance(body, dict):
            raise tree.WorkPlanError("body must be an object")
        # The surface that issued the command, server-derived and recorded with
        # its receipt. It is not part of the request digest. A direct in-process
        # call without an origin uses this controller's trusted origin, else rest.
        if origin is None:
            origin = (self._ctl_origin(self.services.operator_origin)
                      if self.services.operator_origin is not None else {"source": "rest", "auth": "in_process"})
        elif origin.get("source") == "ctl":
            origin = self._ctl_origin(origin)
        elif origin.get("source") not in ("web", "rest"):
            raise tree.WorkPlanError("operator origin is server-derived")
        source = origin["source"]
        if action in self.OPERATOR_RESOLUTIONS:
            # Recorded on the receipt they resolve, never as a receipt of their
            # own: with all 128 receipts outcome-unknown they must still run.
            # Their body operation_id names the receipt, not this request's key.
            return {"ok": True, "schema_version": 1, "session_id": identity,
                    "result": self._operation_resolution(identity, action, body, source)}
        body_key = body.get("operation_id")
        if idempotency_key is not None and body_key is not None and idempotency_key != body_key:
            raise tree.WorkPlanError("Idempotency-Key and operation_id must match")
        if action in self.OPERATOR_READS:
            return {"ok": True, "schema_version": 1, "session_id": identity,
                    "result": self._operator_effect(identity, action, body, source)}
        application = self._application(identity)
        key = idempotency_key if idempotency_key is not None else body_key
        if key is None:
            key = "server-" + uuid.uuid4().hex
        # Intent first: a committed key replays its stored summary, a started one
        # whose outcome is unknown is refused, and only a new key runs the effect.
        replay = application.operator_prepare(action, body, key, origin=origin)
        if replay is not None:
            return {"ok": True, "schema_version": 1, "session_id": identity, "operation_id": key,
                    "replayed": True, "result": replay["result"]}
        try:
            result = self._operator_effect(identity, action, body, source)
        except (OSError, tree.WorkPlanError, ValueError):
            # A refused effect leaves no receipt; the same key may be retried.
            application.operator_abandon(action, body, key)
            raise
        application.operator_commit(action, body, key, self.receipt_summary(result))
        return {"ok": True, "schema_version": 1, "session_id": identity, "operation_id": key,
                "replayed": False, "result": result}

    def _operator_effect(self, identity: str, action: str, body: dict[str, Any], source: str = "rest") -> Any:
        if action.startswith("workflow_"):
            from .work_workflow_http import effect as workflow_effect
            return workflow_effect(self, identity, action, body, source)
        if action == "settings_update":
            if self.services.save_config is None:
                raise tree.WorkPlanError("Work settings are read-only")
            # Compare-and-set against the fingerprint the operator read (GET
            # /settings), re-read and saved under the shared settings lock: a
            # change made elsewhere since, or a late replay, is refused.
            expected = body.get(FINGERPRINT_FIELD)
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise tree.WorkPlanError(FINGERPRINT_FIELD + " is required: the settings_fingerprint from "
                                         "GET /ca/work/api/v1/settings (64 lowercase hex)")
            result = live_settings(self.services.load_config(), {
                "key": body.get("key"), "value": body.get("value"), FINGERPRINT_FIELD: expected},
                self.services.save_config, load=self.services.load_config)
        elif action == "parallel_configure":
            if self.services.parallel_store is None:
                raise tree.WorkPlanError("parallel controls are unavailable")
            store = self.services.parallel_store()
            state, _ = store.configure(body.get("changes"), capability_ready=True,
                                       expected_version=body.get("expected_version"))
            result = self._public_parallel(identity, self._store(identity).read_snapshot())
            if self.services.save_config is not None:
                config = self.services.load_config()
                config["work_parallel"] = state["policy"]
                self.services.save_config(config)
        elif action in ("parallel_orphans_preview", "parallel_orphans_apply"):
            if self.services.parallel_store is None:
                raise tree.WorkPlanError("parallel controls are unavailable")
            store = self.services.parallel_store()
            if action.endswith("preview"):
                from .work_parallel_mcp import _orphan_preview
                result = _orphan_preview(store)
            else:
                # One lease per action, pinned to the exact preview the operator
                # saw: the menu and ciel-runtimectl call this same function.
                expected = body.get("expected_version")
                if type(expected) is not int or expected < 0:
                    raise tree.WorkPlanError("expected_version must be the previewed ledger version")
                if store.read()["version"] != expected:
                    raise WorkParallelConflict("Work parallel state changed since the preview; preview again")
                lease_id = body.get("lease_id")
                if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 200:
                    raise tree.WorkPlanError("lease_id must be the exact previewed lease")
                from .work_parallel_menu import release_previewed
                result = release_previewed(store, body.get("preview_digest"), lease_id,
                                           proof=self.OPERATOR_PROOFS[source],
                                           legacy_confirmed=body.get("legacy_confirmed") is True)
        elif action == "parallel_orphans_reconcile":
            # Never-launched coordination runs and P0 intents of the exact
            # preview the operator saw (orphan preview "reconcile"); the router's
            # sweep runs the same function. A backup is written first.
            if self.services.parallel_store is None:
                raise tree.WorkPlanError("parallel controls are unavailable")
            if body.get("confirmed") is not True:
                raise tree.WorkPlanError("parallel-orphans-reconcile requires explicit confirmation (confirmed=true)")
            from .work_parallel_menu import reconcile_previewed
            result = reconcile_previewed(self.services.parallel_store(), body.get("preview_digest"))
        elif action == "parallel_item_review":
            result = self._parallel_item_review(identity, body)
        elif action == "parallel_launch_resolve":
            result = self._parallel_launch_resolve(identity, body)
        elif action == "parallel_legacy_accept":
            result = self._parallel_legacy_accept(identity, body)
        elif action == "side_review":
            from .side_review import review, with_delivery
            result = review(self._store(identity), body.get("receipt_ids"), verdict=body.get("verdict"),
                            note=body.get("note", ""), expected_version=body.get("expected_version"))
            # Operator response: each review exactly as GET /side lists it
            # (public fields, delivery and reply pins; never the reply or source).
            rows = result.get("reviews") if isinstance(result, dict) else result
            public = with_delivery([self._side_review_public(row.get("receipt_id"), row)
                                    for row in rows or [] if isinstance(row, dict)], self.services.state_dir, identity)
            result = {**result, "reviews": public} if isinstance(result, dict) else public
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
            # Browser-visible: the nested wake goes through the shared redactor.
            from .side_wake import redact_handoff
            result = redact_handoff(event_id, result)
        elif action == "side_wake_retry":
            # Operator-confirmed release of an uncertain main wake for a
            # possibly duplicate re-delivery through the normal election.
            from .side_wake import retry as retry_wake
            result = retry_wake(self._store(identity), body.get("event_id"), body.get("attempt_ref"),
                                body.get("revision"), confirmed=body.get("confirmed") is True)
        elif action == "side_notice_retry":
            # Operator-confirmed release of a released-wait notice whose
            # outcome is unknown, for one possibly duplicate re-issue.
            from .side_intake_inbox import SideIntakeInbox
            from .side_notice import retry as retry_notice
            result = retry_notice(SideIntakeInbox(self.services.state_dir, identity), body.get("receipt_id"),
                                  attempt_ref=body.get("attempt_ref"), revision=body.get("revision"),
                                  confirmed=body.get("confirmed") is True)
        elif action == "side_drain":
            result = self.side_drain(identity, body)
        elif action == "side_reappend_retry":
            result = self.side_reappend_retry(identity, body)
        elif action == "review_reappend_retry":
            result = self.review_reappend_retry(identity, body)
        else:
            from .side_reply_outbox import retry
            result = retry(self.services.state_dir, identity, body.get("receipt_id"),
                           body.get("expected_state"), body.get("confirmed", False),
                           attempt_ref=body.get("attempt_ref"), revision=body.get("revision"))
        return result

    def side_drain(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """The console side drain (admit=False) for REST and ctl: nothing is admitted.

        Each row is the redacted receipt detail of GET /side/receipt; the raw
        inbox record (original text, context, fallback) never leaves the host.
        """
        limit = body.get("limit", 10)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise tree.WorkPlanError("side drain limit must be an integer from 1 to 100")
        from .side_intake_inbox import RECEIPT_ID, SideIntakeInbox
        from .side_intake_service import drain
        inbox = SideIntakeInbox(self.services.state_dir, identity)
        rows = []
        for row in drain(self.services.state_dir, identity, admit=False, limit=limit)[:limit]:
            receipt_id = row.get("receipt_id") if isinstance(row, dict) else None
            if isinstance(receipt_id, str) and RECEIPT_ID.fullmatch(receipt_id):
                detail = inbox.receipt_detail(receipt_id)
                if detail is not None:
                    rows.append(detail)
        return {"rows": rows, "admitted": False}

    def side_reappend_retry(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """Operator-confirmed release of a side-wake conversation re-append whose
        outcome is unknown, for one possibly duplicate re-append.

        Shared by REST and the console (ctl). ``body``: event_id, reappend_ref
        (from GET /side handoffs[].wake), revision, confirmed=true. The answer is
        the redacted wake result: no attempt, owner, conversation or receipt.
        """
        from .side_wake import retry_reappend
        return retry_reappend(self._store(identity), body.get("event_id"), body.get("reappend_ref"),
                              body.get("revision"), confirmed=body.get("confirmed") is True)

    def review_reappend_retry(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """Operator-confirmed release of an unknown-outcome review re-append.

        Shared by REST and the console (ctl). ``body``: lease_id, reappend_ref
        (from GET /side review_delivery.rows[]), confirmed=true. Only a review of
        this session is accepted.
        """
        if self.services.parallel_store is None:
            raise tree.WorkPlanError("parallel review controls are unavailable")
        lease_id = body.get("lease_id")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 200:
            raise tree.WorkPlanError("lease_id must be a nonempty string")
        from .review_delivery import ReviewDeliveryStore
        from .work_parallel_mcp import finalization
        journal = finalization(self.services.parallel_store())
        review = (journal.read().get("reviews") or {}).get(lease_id)
        if not isinstance(review, dict) or review.get("scope") != identity:
            raise tree.WorkPlanError("unknown review for this session")
        return ReviewDeliveryStore.beside(journal).reappend_retry(lease_id, body.get("reappend_ref"),
                                                                  confirmed=body.get("confirmed") is True)

    def _public_parallel(self, identity: str, board: dict[str, Any]) -> dict[str, Any]:
        """Browser-safe parallel view: evidence groups and device aggregates only."""
        projected = self.services.parallel_status(identity, board)
        if "forks" not in projected:
            # A projection without native evidence is never read as running forks.
            return {"session_id": identity, "version": projected.get("version", 0), "policy": projected.get("policy", {}),
                    "forks": {"schema": "native-fork-evidence/1", "available": False, "confirmed_count": 0,
                              **{name: [] for name in ("confirmed", "waiting", "reservation", "uncertain",
                                                       "validating", "ended", "legacy_reviewer")}},
                    "device": {"active_slots": projected.get("active_device", 0)},
                    **self._public_fork_gate(identity)}
        from .work_parallel_mcp import public_projection
        result = public_projection(projected, identity)
        result.update(self._public_reviews(projected, self._public_launches(identity)))
        result["parallel_legacy_reviews"] = self._public_legacy_reviews(projected)
        result.update(self._public_failures(identity))
        result.update(self._public_fork_gate(identity))
        return result

    def _public_fork_gate(self, identity: str) -> dict[str, Any]:
        """G5, display only: the Work host's last live probe of the Work fork gate.

        Read from its own sidecar, so the ctl controller (a model projection)
        shows it too. Never the probe nonce.
        """
        from .work_parallel_gate_presence import NOT_REPORTED_TEXT, fork_gate_view
        if self.services.parallel_store is None:
            return {"fork_gate": {"state": "unknown", "reason": "not_reported", "gate_version": None,
                                  "observed_at": None, "reported_at": None, "message": NOT_REPORTED_TEXT},
                    "gate_presence_version": None}
        return fork_gate_view(self.services.parallel_store().path, identity)

    @staticmethod
    def _public_legacy_reviews(projected: dict[str, Any]) -> list[dict[str, Any]]:
        """Workers an older build left ``reviewing``; never a reviewer identity."""
        rows = []
        for source in projected.get("legacy_reviews") or []:
            if not isinstance(source, dict) or source.get("reviewers") not in ("none", "one", "multiple"):
                continue
            row = {key: source[key] for key in ("lease_id", "item_id", "priority", "reviewers")
                   if isinstance(source.get(key), str)}
            if isinstance(source.get("item_title"), str) and source["item_title"]:
                row["item_title"] = source["item_title"][:200]
            rows.append(row)
        return rows[:50]

    def _public_failures(self, identity: str) -> dict[str, Any]:
        """The durable host failure log of this session: fixed codes, short ids and times."""
        unavailable = {"parallel_failures": [], "failures_version": None, "failures_truncated": False}
        if self.services.parallel_store is None:
            return unavailable
        try:
            # Both logs (host refusals, workspace preparation), each row with
            # its one server text.
            from .work_parallel_failures import public_failures
            view = public_failures(self.services.parallel_store().path, identity)
        except (OSError, ValueError):
            return unavailable
        return {"parallel_failures": view["failures"], "failures_version": view["failures_version"],
                "failures_truncated": view["failures_truncated"]}

    STUCK_LAUNCH_SECONDS = 30

    def _public_launches(self, identity: str) -> dict[str, dict[str, Any]]:
        """Browser-safe rework launch state keyed by the reviewed worker lease.

        Exact launch name, agent id, nonce, proof text and audit details stay on
        the host. The browser sees the state, attempt, how long it has been
        unresolved, whether the host last refused a request as agent present, and
        the opaque launch_ref of an unresolved launch, which only names the exact
        attempt a confirmed resolution request is for.
        """
        if self.services.parallel_store is None:
            return {}
        try:
            # Both journals: schema-5 rework launches and stranded-v1 recovery launches.
            from .work_parallel_mcp import review_launch_rows
            launches = review_launch_rows(self.services.parallel_store(), identity)
        except (OSError, ValueError):
            return {}
        now = time.time()
        rows: dict[str, dict[str, Any]] = {}
        for _journal, lease_id, launch in launches:
            audit = launch.get("audit") or []
            last = audit[-1].get("kind") if audit and isinstance(audit[-1], dict) else None
            public: dict[str, Any] = {"state": launch["state"], "attempt": launch.get("attempt")}
            ref = launch.get("launch_ref")
            if (launch["state"] in ("launch_intent", "spawn_observed", "manual_requested") and isinstance(ref, str)
                    and re.fullmatch(r"[0-9a-f]{32}", ref)):
                public["launch_ref"] = ref
            if launch["state"] in ("launch_intent", "spawn_observed") and isinstance(launch.get("updated_at"), (int, float)):
                age = max(0, int(now - launch["updated_at"]))
                public["unresolved_seconds"] = age
                public["stuck"] = age >= self.STUCK_LAUNCH_SECONDS
            if last == "present" and launch["state"] in ("spawn_observed", "bound"):
                public["agent_present"] = True
            rows[lease_id] = public
        return rows

    def _parallel_legacy_accept(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """Consume the unique submitted legacy reviewer for one ``reviewing`` worker."""
        if self.services.parallel_store is None:
            raise tree.WorkPlanError("parallel review controls are unavailable")
        lease_id = body.get("lease_id")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 200:
            raise tree.WorkPlanError("lease_id must be a nonempty string")
        if body.get("confirmed") is not True:
            raise tree.WorkPlanError("accepting a legacy reviewed result requires explicit confirmation")
        expected = body.get("expected_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_version must be a nonnegative integer")
        from .work_parallel_mcp import legacy_accept
        return legacy_accept(self.services.parallel_store(), self._store(identity), identity, lease_id,
                             expected_version=expected,
                             evidence="Accepted by the authenticated operator after legacy independent review.")

    def _parallel_launch_resolve(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """Record the operator's confirmed request to resolve one exact rework launch.

        The browser names the reviewed lease and the opaque launch_ref it was
        shown; the launch name is never read or derived here. This never asserts
        the fork absent: the host mod checks its own native agent list at its
        next event boundary, then resolves the launch or refuses it as agent present.
        A retried request for the same ref is idempotent; a stale ref is refused.
        """
        if self.services.parallel_store is None:
            raise tree.WorkPlanError("parallel review controls are unavailable")
        lease_id, launch_ref = body.get("lease_id"), body.get("launch_ref")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 200:
            raise tree.WorkPlanError("lease_id must be a nonempty string")
        if not isinstance(launch_ref, str) or re.fullmatch(r"[0-9a-f]{32}", launch_ref) is None:
            raise tree.WorkPlanError("launch_ref must be the exact launch reference shown for this result")
        if body.get("confirmed") is not True:
            raise tree.WorkPlanError("resolving a rework launch requires explicit confirmation")
        # The owning journal decides which version pins the request: schema-5
        # finalization for a rework launch, stranded-v1 for a recovery launch.
        from .work_parallel_mcp import request_review_launch_resolve
        result = request_review_launch_resolve(
            self.services.parallel_store(), identity, lease_id, launch_ref,
            expected_finalization_version=body.get("expected_finalization_version"),
            expected_stranded_version=body.get("expected_stranded_version"))
        version = "stranded_version" if result["review_journal"] == "stranded" else "finalization_version"
        return {key: result[key] for key in ("lease_id", "state", "attempt", "launch_ref", "review_journal", version)}

    REVIEW_ROW_FIELDS = ("lease_id", "item_id", "root_id", "priority", "item_title", "item_path", "artifact",
                         "completion_receipt", "summary", "coverage", "verification", "state", "decision",
                         "evidence", "changed_before_bind")
    REVIEW_WORKSPACE_FIELDS = ("branch", "head", "dirty", "changed_count", "changed_files", "diff_stat")

    @classmethod
    def _public_reviews(cls, projected: dict[str, Any], launches: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Browser-safe main-review queue: bounded receipt facts and decision state.

        Same exclusions as the rest of /parallel: no absolute workspace or repo
        path, no fork content, agent/native identity, capability or saga internals.
        """
        review = projected.get("review")
        if not isinstance(review, dict):
            return {"parallel_reviews": [], "review_version": None, "stranded_version": None,
                    "parallel_reviews_ended": []}
        launches = launches or {}
        rows = []
        superseded = []
        # Ended superseded rows come from the host-authenticated review.ended
        # (lead ruling); a superseded row still inside review.pending (an older
        # backend) is shown the same way, once per lease.
        ended_sources = [row for row in review.get("ended") or [] if isinstance(row, dict)]
        for from_ended, source in [*((False, row) for row in review.get("pending") or []),
                                   *((True, row) for row in ended_sources)]:
            if not isinstance(source, dict):
                continue
            stranded = source.get("stranded")
            is_superseded = source.get("state") == "superseded" or (isinstance(stranded, dict)
                                                                     and stranded.get("phase") == "superseded")
            if from_ended and not is_superseded:
                continue  # review.ended carries ended audit rows only; nothing there is decidable.
            if is_superseded:
                # An ended audit row (checkpoint3), never a decidable review: the
                # superseding lease and the time only. No reason, workspace,
                # artifact, receipt, summary or verification is exported.
                stranded = stranded if isinstance(stranded, dict) else {}
                ended = {key: source[key] for key in ("lease_id", "item_id", "priority", "item_title") if source.get(key) is not None}
                if stranded.get("kind") in ("workspace_unavailable", "no_report"):
                    ended["kind"] = stranded["kind"]
                ended["phase"] = "superseded"
                if isinstance(stranded.get("superseding_lease_id"), str):
                    ended["superseding_lease_id"] = stranded["superseding_lease_id"][:64]
                # The superseding lease's current ledger state, or "absent": a fixed word only.
                winner = stranded.get("superseding_state")
                if isinstance(winner, str) and re.fullmatch(r"[a-z_]{1,24}", winner):
                    ended["superseding_state"] = winner
                observed = stranded.get("observed_at")
                if type(observed) in (int, float) and 1e9 < observed < 1e11:
                    ended["observed_at"] = observed
                superseded.append(ended)
                continue
            row = {key: source[key] for key in cls.REVIEW_ROW_FIELDS if source.get(key) is not None}
            for key in ("summary", "verification", "evidence"):
                if isinstance(row.get(key), str):
                    row[key] = row[key][:2000]
            # R1: the read-time content pin of an undecided row, in the router's
            # exact format only. The browser sends back the pin of the row it
            # rendered, so a decision is not refused by unrelated version bumps.
            pin = source.get("review_pin")
            if isinstance(pin, str) and re.fullmatch(r"rp1:[0-9a-f]{64}", pin):
                row["review_pin"] = pin
            blocked = source.get("blocked")
            if isinstance(blocked, dict) and isinstance(blocked.get("code"), str):
                # Code only: a stage-failure message can carry host paths.
                row["blocked"] = {"code": blocked["code"][:40]}
            stranded = source.get("stranded")
            if isinstance(stranded, dict) and stranded.get("kind") in ("workspace_unavailable", "no_report"):
                # Fixed words only. The reason is withheld: an ownership error can
                # carry host paths, the same rule as blocked.message.
                row["stranded"] = {"kind": stranded["kind"],
                                   "approve": "refused" if stranded.get("approve") == "refused" else "unknown",
                                   "rework": "fresh_workspace" if stranded.get("rework") == "fresh_workspace" else "unknown",
                                   "counts_against_rework_limit": stranded.get("counts_against_rework_limit") is True}
                # The sidecar's saga phase (stranded-v1), a fixed word.
                if stranded.get("phase") in ("pending", "deciding", "recovered"):
                    row["stranded"]["phase"] = stranded["phase"]
            # The journal that owns this row, and so which version pins its
            # launch resolution: stranded-v1 for a stranded row, else schema-5.
            row["review_journal"] = "stranded" if "stranded" in row else "finalization"
            if isinstance(row.get("item_path"), list):
                row["item_path"] = [str(value)[:120] for value in row["item_path"][:8]]
            launch = launches.get(source.get("lease_id"))
            if launch is not None:
                row["launch"] = launch
            workspace = source.get("workspace")
            if isinstance(workspace, dict):
                public = {key: workspace[key] for key in cls.REVIEW_WORKSPACE_FIELDS if workspace.get(key) is not None}
                if isinstance(public.get("changed_files"), list):
                    # The backend observes the whole workspace and sends the
                    # true count separately. Keep it while clipping the public
                    # file-name sample to twenty rows.
                    total = workspace.get("changed_count")
                    public["changed_count"] = total if type(total) is int and total >= len(public["changed_files"]) else len(public["changed_files"])
                    public["changed_files"] = [str(value)[:200] for value in public["changed_files"][:20]]
                if isinstance(public.get("diff_stat"), str):
                    public["diff_stat"] = public["diff_stat"][:2000]
                row["workspace"] = public
            rows.append(row)
        version = review.get("version")
        # Ended audit rows: newest first, at most 20 (the backend lists one only
        # while its superseding lease is active and unreviewed).
        seen_ended = set()
        superseded = [row for row in superseded
                      if not (row.get("lease_id") in seen_ended or seen_ended.add(row.get("lease_id")))]
        superseded.sort(key=lambda row: row.get("observed_at") or 0, reverse=True)
        stranded_version = review.get("stranded_version")
        return {"parallel_reviews": rows, "review_version": version if type(version) is int else None,
                "stranded_version": stranded_version if type(stranded_version) is int else None,
                "reviews_truncated": review.get("truncated") is True, "parallel_reviews_ended": superseded[:20]}

    def _parallel_item_review(self, identity: str, body: dict[str, Any]) -> dict[str, Any]:
        """The authenticated web operator decides one pending result as main would.

        The decision id is deterministic per result and decision, so a retried
        click, or the same decision already made by main, is idempotent; a
        different decision for the same result is refused by the journal.
        """
        if self.services.parallel_store is None:
            raise tree.WorkPlanError("parallel review controls are unavailable")
        lease_id, decision = body.get("lease_id"), body.get("decision")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 200:
            raise tree.WorkPlanError("lease_id must be a nonempty string")
        if decision not in ("approve", "rework"):
            raise tree.WorkPlanError("decision must be approve or rework")
        evidence = body.get("evidence", "")
        if not isinstance(evidence, str):
            raise tree.WorkPlanError("evidence must be text")
        evidence = evidence.strip()
        if decision == "rework" and not evidence:
            raise tree.WorkPlanError("rework requires the required change as evidence")
        if not evidence:
            evidence = "Approved by the authenticated operator in the Work web console after reviewing the bounded receipt."
        expected = body.get("expected_finalization_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_finalization_version must be a nonnegative integer")
        pin = body.get("expected_review_pin")
        if pin is not None and (not isinstance(pin, str) or re.fullmatch(r"rp1:[0-9a-f]{64}", pin) is None):
            raise tree.WorkPlanError("expected_review_pin must be the review_pin shown for this row")
        from .work_parallel_mcp import _main_review
        return _main_review(self.services.parallel_store(), self._store(identity), identity, {}, {
            "lease_id": lease_id, "decision": decision, "decision_id": "review:" + lease_id + ":" + decision,
            "evidence": evidence[:2000], "expected_finalization_version": expected,
            **({"expected_review_pin": pin} if pin is not None else {})})

    def _parallel_paths(self) -> tuple[Path, ...]:
        if self.services.parallel_store is None:
            return ()
        path = Path(self.services.parallel_store().path)
        # Every file a /parallel field is read from: the frozen ledger,
        # native evidence, coordination and finalization, plus the stranded-v1,
        # review-delivery and failure-log sidecars.
        return (path, path.with_name("work-parallel-native.json"),
                path.with_name("work-parallel-coordination.json"),
                path.with_name("work-parallel-finalization.json"),
                path.with_name("work-parallel-stranded-v1.json"),
                path.with_name("work-parallel-review-delivery.json"),
                path.with_name("work-parallel-failures-v1.json"),
                path.with_name("work-parallel-gate-presence-v1.json"),
                path.with_name("work-parallel-attachments-v1.json"))

    @staticmethod
    def _stat(paths: tuple[Path, ...]) -> tuple[Any, ...]:
        marks = []
        for path in paths:
            try:
                stat = path.stat()
                marks.append((stat.st_mtime_ns, stat.st_size))
            except OSError:
                marks.append(None)
        return tuple(marks)

    def _parallel_fingerprint(self, identity: str, board: dict[str, Any]) -> tuple[str, bool]:
        """What the Parallel tab would show; changes when its evidence does."""
        try:
            view = self._public_parallel(identity, board)
        except (OSError, ValueError, tree.WorkPlanError):
            return "", False
        forks = view["forks"]
        rows = [(group, row.get("lease_id"), row.get("state"), (row.get("native") or {}).get("status"))
                for group in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended",
                              "legacy_reviewer")
                for row in forks.get(group, [])]
        device = view.get("device", {})
        reviews = [(row.get("lease_id"), row.get("state"), row.get("decision"), row.get("review_journal"),
                    (row.get("stranded") or {}).get("phase"),
                    [(row.get("launch") or {}).get(key) for key in ("state", "attempt", "launch_ref", "stuck",
                                                                    "agent_present")])
                   for row in view.get("parallel_reviews", [])]
        ended = [(row.get("lease_id"), row.get("superseding_lease_id"), row.get("observed_at"))
                 for row in view.get("parallel_reviews_ended", [])]
        legacy = [(row.get("lease_id"), row.get("reviewers")) for row in view.get("parallel_legacy_reviews", [])]
        # An unresolved launch is re-read on the 5 s cadence too, so the tab
        # shows "Rework launch stuck" once it crosses the display threshold.
        launching = any((row.get("launch") or {}).get("stuck") is False for row in view.get("parallel_reviews", []))
        # failures_version is the first log's; a workspace failure row changes
        # only its own id, count or time.
        failures = [(row.get("id"), row.get("count"), row.get("at")) for row in view.get("parallel_failures", [])]
        # The ledger version and every policy value the tab shows: a configure
        # from another surface (menu, ctl, REST) refreshes it too.
        return (json.dumps([rows, device.get("active_by_priority"), device.get("paused"), reviews,
                            view.get("review_version"), view.get("stranded_version"), ended, legacy,
                            view.get("failures_version"), view.get("version"), view.get("policy"),
                            view.get("fork_gate"), view.get("gate_presence_version"), failures],
                           sort_keys=True),
                bool(forks.get("confirmed_count")) or launching)

    @staticmethod
    def _audit_print(doc: dict[str, Any], receipts: dict[str, Any] | None = None) -> str:
        """What the Audit tab shows changing: a receipt added, resolved or committed.

        Audit-only writes keep the board version, so work.changed never fires
        for them. Keys, states and receipt versions only; no request content.
        """
        receipts = (doc.get("rest_operations") or {}) if receipts is None else receipts
        return json.dumps(sorted((key, str((row or {}).get("state")), (row or {}).get("receipt_version"))
                                 for key, row in receipts.items()))

    @staticmethod
    def _stream_audit_receipts(application: WorkApplication) -> dict[str, Any] | None:
        """Audit rows for the event stream, or None while a record is unreadable."""
        try:
            return application.audit_receipts()
        except (OSError, tree.WorkPlanError):
            return None

    @staticmethod
    def _audit_event(identity: str, doc: dict[str, Any],
                     receipts: dict[str, Any] | None = None) -> dict[str, Any]:
        receipts = (doc.get("rest_operations") or {}) if receipts is None else receipts
        newest = max(receipts.items(), key=lambda item: float((item[1] or {}).get("created_at") or 0),
                     default=(None, {}))
        return {"session_id": identity, "count": len(receipts), "newest_operation_id": newest[0],
                "newest_created_at": (newest[1] or {}).get("created_at"),
                "refetch": "/ca/work/api/v1/audit", "coalesced": True}

    def _settings_print(self) -> str | None:
        """The settings fingerprint, or None while the configuration is unreadable."""
        try:
            return settings_fingerprint(self.services.load_config())
        except (OSError, ValueError):
            return None

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
            # Wake-only ledger writes keep the Work version; their revisions
            # still refresh the Side tab (e.g. "main delivery uncertain"). A
            # re-append turns uncertain by age alone (no write), so its derived
            # flag is part of the print too.
            from .side_wake import reappend_uncertain
            from .side_notice import crossed_grace
            def wake_print(board: dict[str, Any]) -> str:
                return json.dumps({key: [*((row.get("wake") or {}).get(field) for field in ("state", "phase", "revision")),
                                         reappend_uncertain(row.get("wake"))]
                                   for key, row in board.get("side_handoffs", {}).items()}, sort_keys=True)
            wake_cursor = wake_print(doc)
            # A release-notice claim also turns uncertain by age alone (60 s).
            notice_cursor = crossed_grace(side_inbox)
            parallel_paths = self._parallel_paths()
            # The Side tab's Review delivery rows (review_delivery sidecar,
            # written by any process) are re-read when a parallel file changes,
            # and every 5 s while a re-append could turn uncertain by age.
            review_stat = self._stat(parallel_paths)
            review_print, review_live = self._review_delivery_print(identity, doc) if parallel_paths else ("", False)
            review_due = time.monotonic() + 5
            parallel_stat = self._stat(parallel_paths)
            parallel_print, parallel_live = self._parallel_fingerprint(identity, doc) if parallel_paths else ("", False)
            parallel_due = time.monotonic() + 5
            audit_application = self._application(identity)
            audit_receipts = self._stream_audit_receipts(audit_application)
            audit_cursor = self._audit_print(doc, audit_receipts) if audit_receipts is not None else None
            settings_cursor = self._settings_print()
            settings_due = time.monotonic() + 2
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
                audit_receipts = self._stream_audit_receipts(audit_application)
                latest_audit = self._audit_print(current, audit_receipts) if audit_receipts is not None else None
                # An unreadable operator-intent record keeps the cursor (GET /audit answers 503).
                if latest_audit is not None and latest_audit != audit_cursor:
                    # Content-free: counts and the newest key only; no event id.
                    frame = chr(10).join(("event: audit.changed", "data: " + json.dumps(
                        self._audit_event(identity, current, audit_receipts)), "", ""))
                    handler.wfile.write(frame.encode("utf-8"))
                    handler.wfile.flush()
                    audit_cursor = latest_audit
                    heartbeat = time.monotonic()
                if time.monotonic() >= settings_due:
                    # Settings live in the configuration file, written by the
                    # menu, ctl, REST or a session command: re-read every 2 s.
                    settings_due = time.monotonic() + 2
                    latest_settings = self._settings_print()
                    if latest_settings is not None and latest_settings != settings_cursor:
                        frame = chr(10).join(("event: settings.changed", "data: " + json.dumps({
                            "session_id": identity, "settings_fingerprint": latest_settings,
                            "refetch": "/ca/work/api/v1/settings", "coalesced": True}), "", ""))
                        handler.wfile.write(frame.encode("utf-8"))
                        handler.wfile.flush()
                        settings_cursor = latest_settings
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
                latest_wake = wake_print(current)
                if latest_wake != wake_cursor and latest_side is not None:
                    wake_cursor = latest_wake
                    side_cursor = None  # force one coalesced side.changed below
                latest_notice = crossed_grace(side_inbox)
                if latest_notice != notice_cursor and latest_side is not None:
                    notice_cursor = latest_notice
                    side_cursor = None
                if parallel_paths and latest_side is not None:
                    latest_review_stat = self._stat(parallel_paths)
                    if latest_review_stat != review_stat or (review_live and time.monotonic() >= review_due):
                        review_stat = latest_review_stat
                        review_due = time.monotonic() + 5
                        latest_review, review_live = self._review_delivery_print(identity, current)
                        if latest_review != review_print:
                            review_print = latest_review
                            side_cursor = None
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
                if parallel_paths:
                    # Stat first: the ledger is read only when a file changed,
                    # or every 5 s while a confirmed fork could go stale.
                    latest_stat = self._stat(parallel_paths)
                    if latest_stat != parallel_stat or (parallel_live and time.monotonic() >= parallel_due):
                        parallel_stat = latest_stat
                        parallel_due = time.monotonic() + 5
                        latest_print, parallel_live = self._parallel_fingerprint(identity, doc)
                        if latest_print != parallel_print:
                            frame = chr(10).join(("event: parallel.changed", "data: " + json.dumps({
                                "session_id": identity, "refetch": "/ca/work/api/v1/parallel",
                                "coalesced": True}), "", ""))
                            handler.wfile.write(frame.encode("utf-8"))
                            handler.wfile.flush()
                            parallel_print = latest_print
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
                       "commands": "POST /ca/work/api/v1/commands/<command> with Idempotency-Key or operation_id",
                       "restart": "POST /ca/work/api/v1/runtime/restart {confirmed: true, reason, runtime: claude|codex|'', resume, client_pid}; GET /ca/work/api/v1/runtime returns runtime.restart (clients, queued request, input holds)"},
                "side_receipts": "Current-session receipts only, newest activity first. Stages: received, processing, processed, failed, quarantined, duplicate. Filters and search use bounded public fields. Credentials, reply routes, claim/fallback generations, outbox attempts and main-context previews are never returned; originals and replies are redacted and limited to 16,000 characters.",
                "runtime": "GET /ca/work/runtime", "events": "GET /ca/work/events",
                "mutations": ["POST /ca/work/nodes/" + n for n in ("add", "update", "remove", "reorder")],
                "authentication": "Existing administrator Bearer token or signed-in web account; external access must already be enabled.",
                "concurrency": "Read version; supply expected_version for every mutation; 409 means refetch, never blind retry.",
                "notifications": "Version invalidations sampled each second; may coalesce commits. Last-Event-ID reconciles latest snapshot, not durable patch replay. side.changed is content-free, has no event id and asks for a /side refetch. Refetch after snapshot, change, reset or reconnect.",
                "retention": {"completed_after_seconds": 86400, "toggle": "POST /ca/config/llm option work_completed_auto_delete on/off"},
                "ordering": "One-based sibling positions; root positions order unfinished goals; active-slot changes need user_requested=true. No reparenting."}
