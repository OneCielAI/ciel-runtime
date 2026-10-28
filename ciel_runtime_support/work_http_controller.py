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


class WorkHttpController:
    def __init__(self, services: WorkHttpServices) -> None:
        self.services = services
        self._streams = threading.BoundedSemaphore(16)

    def _identity(self, value: Any) -> str:
        if value is None:
            value = self.services.current_identity()
            if value is None:
                raise tree.WorkPlanError("current conversation identity unavailable; supply session_id")
        if not isinstance(value, str) or IDENTITY.fullmatch(value) is None:
            raise tree.WorkPlanError("session_id must be exact claude:<id> or codex:<id>")
        return value

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
                       auto_continue=work_auto_continue_enabled(cfg),
                       runtime={**self.services.runtime_status(identity),
                                "errors": self.services.recent_errors(identity)},
                       events="/ca/work/events")
        return payload

    def get(self, handler: Any, path: str) -> bool:
        if path not in ("/ca/work", "/ca/work/runtime", "/ca/work/events", "/ca/work/help"):
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
            if path == "/ca/work/events":
                self._stream(handler, identity, query)
            elif path == "/ca/work/runtime":
                runtime = self.services.runtime_status(identity)
                runtime["errors"] = self.services.recent_errors(identity)
                self.services.write_json(handler, {"ok": True, "schema_version": 1, "session_id": identity,
                                                   "runtime": runtime})
            else:
                self.services.write_json(handler, self.board(identity))
        except (OSError, tree.WorkPlanError) as exc:
            self._error(handler, exc)
        return True

    def _error(self, handler: Any, exc: Exception) -> None:
        unavailable = isinstance(exc, OSError) or "stored" in str(exc) or "invalid Work plan" in str(exc) or "snapshot" in str(exc)
        self.services.write_json(handler, {"ok": False, "error": "state_unavailable" if unavailable else "invalid_request",
                                           "message": "Work state unreadable" if unavailable else str(exc)},
                                 503 if unavailable else 422)

    def post(self, handler: Any, path: str, body: dict[str, Any]) -> bool:
        actions = {"/ca/work/nodes/" + n: n for n in ("add", "update", "remove", "reorder")}
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

    def _stream(self, handler: Any, identity: str, query: dict[str, list[str]]) -> None:
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
            self._event(handler, identity, doc, "work.snapshot" if not cursor else "work.resync_required", previous)
            version = doc["version"]
            heartbeat = time.monotonic()
            while True:
                wake.clear()
                current = store.read_snapshot()
                if current["version"] != version:
                    self._event(handler, identity, current, "work.changed", version, doc)
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
                    frame = chr(10).join(("event: runtime.error", "data: " + json.dumps({
                        "session_id": identity, "refetch": "/ca/work/runtime"}), "", ""))
                    handler.wfile.write(frame.encode("utf-8"))
                    handler.wfile.flush()
                    error_cursor = serialized_errors
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
               before: dict[str, Any] | None = None) -> None:
        event = {"session_id": identity, "version": doc["version"], "previous_version": previous,
                 "updated_at": doc["updated_at"], "refetch": "/ca/work", "coalesced": True}
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
        return {"ok": True, "schema_version": 1, "board": "GET /ca/work?session_id=claude:<id>",
                "runtime": "GET /ca/work/runtime", "events": "GET /ca/work/events",
                "mutations": ["POST /ca/work/nodes/" + n for n in ("add", "update", "remove", "reorder")],
                "authentication": "Existing administrator Bearer token or signed-in web account; external access must already be enabled.",
                "concurrency": "Read version; supply expected_version for every mutation; 409 means refetch, never blind retry.",
                "notifications": "Version invalidations sampled each second; may coalesce commits. Last-Event-ID reconciles latest snapshot, not durable patch replay. Refetch after snapshot, change, reset or reconnect.",
                "ordering": "One-based sibling positions; root positions order unfinished goals; active-slot changes need user_requested=true. No reparenting."}
