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
from .work_parallel import WorkParallelConflict
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
            "parallel_item_review", "parallel_launch_resolve", "side_review", "side_handoff", "side_reply_retry",
            "side_wake_retry")}
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
                result = self._public_parallel(identity, self._store(identity).read_snapshot())
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
            elif action == "parallel_item_review":
                result = self._parallel_item_review(identity, body)
            elif action == "parallel_launch_resolve":
                result = self._parallel_launch_resolve(identity, body)
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
                # Browser-visible: the nested wake goes through the shared redactor.
                from .side_wake import redact_handoff
                result = redact_handoff(event_id, result)
            elif action == "side_wake_retry":
                # Operator-confirmed release of an uncertain main wake for a
                # possibly duplicate re-delivery through the normal election.
                from .side_wake import retry as retry_wake
                result = retry_wake(self._store(identity), body.get("event_id"), body.get("attempt_ref"),
                                    body.get("revision"), confirmed=body.get("confirmed") is True)
            else:
                from .side_reply_outbox import retry
                result = retry(self.services.state_dir, identity, body.get("receipt_id"),
                               body.get("expected_state"), body.get("confirmed", False))
            self.services.write_json(handler, {"ok": True, "schema_version": 1,
                                               "session_id": identity, "result": result})
        except WorkParallelConflict as exc:
            self.services.write_json(handler, {"ok": False, "error": "version_conflict", "message": str(exc)}, 409)
        except (OSError, tree.WorkPlanError, ValueError) as exc:
            self._error(handler, exc)
        return True

    def _public_parallel(self, identity: str, board: dict[str, Any]) -> dict[str, Any]:
        """Browser-safe parallel view: evidence groups and device aggregates only."""
        projected = self.services.parallel_status(identity, board)
        if "forks" not in projected:
            # A projection without native evidence is never read as running forks.
            return {"session_id": identity, "version": projected.get("version", 0), "policy": projected.get("policy", {}),
                    "forks": {"schema": "native-fork-evidence/1", "available": False, "confirmed_count": 0,
                              **{name: [] for name in ("confirmed", "waiting", "reservation", "uncertain",
                                                       "validating", "ended")}},
                    "device": {"active_slots": projected.get("active_device", 0)}}
        from .work_parallel_mcp import public_projection
        result = public_projection(projected, identity)
        result.update(self._public_reviews(projected, self._public_launches(identity)))
        return result

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
            from .work_parallel_mcp import finalization
            state = finalization(self.services.parallel_store()).read()
        except (OSError, ValueError):
            return {}
        now = time.time()
        rows: dict[str, dict[str, Any]] = {}
        for row in state.get("reviews", {}).values():
            launch = row.get("launch") or {}
            if row.get("scope") != identity or row.get("decision") != "rework" or launch.get("state") in (None, "none"):
                continue
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
            rows[row["lease_id"]] = public
        return rows

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
        expected = body.get("expected_finalization_version")
        if type(expected) is not int or expected < 0:
            raise tree.WorkPlanError("expected_finalization_version must be a nonnegative integer")
        from .work_parallel_mcp import request_rework_launch_manual
        result = request_rework_launch_manual(self.services.parallel_store(), identity, lease_id, launch_ref,
                                              confirmed=True, expected_version=expected)
        return {key: result[key] for key in ("lease_id", "state", "attempt", "launch_ref", "finalization_version")}

    REVIEW_ROW_FIELDS = ("lease_id", "item_id", "root_id", "priority", "item_title", "item_path", "artifact",
                         "completion_receipt", "summary", "coverage", "verification", "state", "decision",
                         "evidence")
    REVIEW_WORKSPACE_FIELDS = ("branch", "head", "dirty", "changed_count", "changed_files", "diff_stat")

    @classmethod
    def _public_reviews(cls, projected: dict[str, Any], launches: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Browser-safe main-review queue: bounded receipt facts and decision state.

        Same exclusions as the rest of /parallel: no absolute workspace or repo
        path, no fork content, agent/native identity, capability or saga internals.
        """
        review = projected.get("review")
        if not isinstance(review, dict):
            return {"parallel_reviews": [], "review_version": None}
        launches = launches or {}
        rows = []
        for source in review.get("pending") or []:
            if not isinstance(source, dict):
                continue
            row = {key: source[key] for key in cls.REVIEW_ROW_FIELDS if source.get(key) is not None}
            for key in ("summary", "verification", "evidence"):
                if isinstance(row.get(key), str):
                    row[key] = row[key][:2000]
            blocked = source.get("blocked")
            if isinstance(blocked, dict) and isinstance(blocked.get("code"), str):
                # Code only: a stage-failure message can carry host paths.
                row["blocked"] = {"code": blocked["code"][:40]}
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
        return {"parallel_reviews": rows, "review_version": version if type(version) is int else None,
                "reviews_truncated": review.get("truncated") is True}

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
        from .work_parallel_mcp import _main_review
        return _main_review(self.services.parallel_store(), self._store(identity), identity, {}, {
            "lease_id": lease_id, "decision": decision, "decision_id": "review:" + lease_id + ":" + decision,
            "evidence": evidence[:2000], "expected_finalization_version": expected})

    def _parallel_paths(self) -> tuple[Path, ...]:
        if self.services.parallel_store is None:
            return ()
        path = Path(self.services.parallel_store().path)
        return (path, path.with_name("work-parallel-native.json"),
                path.with_name("work-parallel-coordination.json"),
                path.with_name("work-parallel-finalization.json"))

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
                for group in ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")
                for row in forks.get(group, [])]
        device = view.get("device", {})
        reviews = [(row.get("lease_id"), row.get("state"), row.get("decision"),
                    [(row.get("launch") or {}).get(key) for key in ("state", "attempt", "launch_ref", "stuck",
                                                                    "agent_present")])
                   for row in view.get("parallel_reviews", [])]
        # An unresolved launch is re-read on the 5 s cadence too, so the tab
        # shows "Rework launch stuck" once it crosses the display threshold.
        launching = any((row.get("launch") or {}).get("stuck") is False for row in view.get("parallel_reviews", []))
        return (json.dumps([rows, device.get("active_by_priority"), device.get("paused"), reviews,
                            view.get("review_version")], sort_keys=True),
                bool(forks.get("confirmed_count")) or launching)

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
            # still refresh the Side tab (e.g. "main delivery uncertain").
            def wake_print(board: dict[str, Any]) -> str:
                return json.dumps({key: [(row.get("wake") or {}).get(field) for field in ("state", "phase", "revision")]
                                   for key, row in board.get("side_handoffs", {}).items()}, sort_keys=True)
            wake_cursor = wake_print(doc)
            parallel_paths = self._parallel_paths()
            parallel_stat = self._stat(parallel_paths)
            parallel_print, parallel_live = self._parallel_fingerprint(identity, doc) if parallel_paths else ("", False)
            parallel_due = time.monotonic() + 5
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
                latest_wake = wake_print(current)
                if latest_wake != wake_cursor and latest_side is not None:
                    wake_cursor = latest_wake
                    side_cursor = None  # force one coalesced side.changed below
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
                       "commands": "POST /ca/work/api/v1/commands/<command> with Idempotency-Key or operation_id"},
                "side_receipts": "Current-session receipts only, newest activity first. Stages: received, processing, processed, failed, quarantined, duplicate. Filters and search use bounded public fields. Credentials, reply routes, claim/fallback generations, outbox attempts and main-context previews are never returned; originals and replies are redacted and limited to 16,000 characters.",
                "runtime": "GET /ca/work/runtime", "events": "GET /ca/work/events",
                "mutations": ["POST /ca/work/nodes/" + n for n in ("add", "update", "remove", "reorder")],
                "authentication": "Existing administrator Bearer token or signed-in web account; external access must already be enabled.",
                "concurrency": "Read version; supply expected_version for every mutation; 409 means refetch, never blind retry.",
                "notifications": "Version invalidations sampled each second; may coalesce commits. Last-Event-ID reconciles latest snapshot, not durable patch replay. side.changed is content-free, has no event id and asks for a /side refetch. Refetch after snapshot, change, reset or reconnect.",
                "retention": {"completed_after_seconds": 86400, "toggle": "POST /ca/config/llm option work_completed_auto_delete on/off"},
                "ordering": "One-based sibling positions; root positions order unfinished goals; active-slot changes need user_requested=true. No reparenting."}
