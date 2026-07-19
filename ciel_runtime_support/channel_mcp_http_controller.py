"""HTTP/SSE controller for the built-in Channel MCP server."""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from threading import Condition, Lock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelMcpSessionStore:
    states: dict[str, dict[str, Any]]
    lock: Lock

    def create(self, session: str, last_id: int) -> None:
        with self.lock:
            self.states[session] = {
                "created_at": time.time(),
                "last_id": last_id,
                "initialized": False,
                "outbox": [],
            }

    def stream_state(self, session: str) -> tuple[int, bool] | None:
        with self.lock:
            state = self.states.get(session)
            if not state:
                return None
            return int(state.get("last_id") or 0), bool(state.get("initialized"))

    def set_last_id(self, session: str, last_id: int) -> None:
        with self.lock:
            if state := self.states.get(session):
                state["last_id"] = last_id

    def touch(self, session: str) -> None:
        with self.lock:
            if session and session in self.states:
                self.states[session]["last_seen_at"] = time.time()

    def initialize(self, session: str) -> None:
        with self.lock:
            if session and session in self.states:
                self.states[session]["initialized"] = True

    def remove(self, session: str) -> None:
        with self.lock:
            self.states.pop(session, None)


@dataclass(frozen=True, slots=True)
class ChannelMcpStreamServices:
    new_session_id: Callable[[], str]
    start_last_id: Callable[[BaseHTTPRequestHandler], int]
    send_headers: Callable[[BaseHTTPRequestHandler], None]
    write_event: Callable[..., None]
    take_outbox: Callable[[str], list[dict[str, Any]]]
    read_messages: Callable[[int, str | None, str | None, int], list[dict[str, Any]]]
    project_notifications: Callable[[list[dict[str, Any]], str], tuple[int, list[tuple[int, dict[str, Any]]]]]
    update_cursor: Callable[[int], None]
    condition: Condition
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpRpcServices:
    initialize_response: Callable[[Any, str], dict[str, Any]]
    tool_schemas: Callable[[], list[dict[str, Any]]]
    tool_call_response: Callable[[Any, dict[str, Any]], dict[str, Any]]
    enqueue: Callable[[str, dict[str, Any]], bool]
    write_json: Callable[..., None]
    write_accepted: Callable[[BaseHTTPRequestHandler], None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpHttpController:
    store: ChannelMcpSessionStore
    stream: ChannelMcpStreamServices
    rpc: ChannelMcpRpcServices

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        if path == "/ca/mcp/health":
            self.rpc.write_json(
                handler,
                {"ok": True, "name": "ciel-runtime-router", "sse": "/ca/mcp/sse"},
            )
            return True
        if path != "/ca/mcp/sse":
            return False
        session = self.stream.new_session_id()
        last_id = self.stream.start_last_id(handler)
        self.store.create(session, last_id)
        self.stream.log("INFO", f"channel_mcp_session_started session={session} last_id={last_id}")
        started_at = time.time()
        close_reason = "finished"
        self.stream.send_headers(handler)
        self.stream.write_event(
            handler,
            "endpoint",
            f"/ca/mcp/messages?sessionId={urllib.parse.quote(session)}",
        )
        try:
            while True:
                current = self.store.stream_state(session)
                if current is None:
                    close_reason = "session_missing"
                    return True
                last_id, initialized = current
                if outbox := self.stream.take_outbox(session):
                    for payload in outbox:
                        self.stream.write_event(handler, "message", payload)
                    self.stream.log("INFO", f"channel_mcp_rpc_flushed session={session} count={len(outbox)}")
                    continue
                if not initialized:
                    handler.wfile.write(b": waiting-for-initialize\n\n")
                    handler.wfile.flush()
                    with self.stream.condition:
                        self.stream.condition.wait(timeout=1.0)
                    continue
                messages = self.stream.read_messages(last_id, None, None, 100)
                if messages:
                    delivered_id, events = self.stream.project_notifications(messages, session)
                    last_id = max(last_id, delivered_id)
                    for event_id, notification in events:
                        self.stream.write_event(handler, "message", notification, event_id)
                        self.stream.log(
                            "INFO",
                            f"channel_mcp_notification_written session={session} message_id={event_id}",
                        )
                    self.stream.update_cursor(last_id)
                    self.store.set_last_id(session, last_id)
                    continue
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                with self.stream.condition:
                    self.stream.condition.wait(timeout=5.0)
        except (BrokenPipeError, ConnectionError, ConnectionResetError) as error:
            close_reason = type(error).__name__
            return True
        except Exception as error:
            close_reason = type(error).__name__
            self.stream.log(
                "ERROR",
                f"channel_mcp_session_failed session={session} error={type(error).__name__}: {error}",
            )
            return True
        finally:
            age = time.time() - started_at
            self.stream.log(
                "INFO",
                f"channel_mcp_session_closed session={session} reason={close_reason} age={age:.1f}s",
            )
            self.store.remove(session)

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        body: dict[str, Any],
    ) -> bool:
        if path != "/ca/mcp/messages":
            return False
        query = urllib.parse.parse_qs(
            urllib.parse.urlparse(handler.path).query,
            keep_blank_values=True,
        )
        session = (query.get("sessionId") or query.get("session") or [""])[0]
        self.store.touch(session)
        method = str(body.get("method") or "")
        request_id = body.get("id")
        response: dict[str, Any] | None = None
        if method == "initialize":
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            protocol = str(params.get("protocolVersion") or "2024-11-05")
            self.store.initialize(session)
            response = self.rpc.initialize_response(request_id, protocol)
            self.rpc.log("INFO", f"channel_mcp_initialized session={session or '-'} protocol={protocol}")
        elif method == "tools/list":
            response = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.rpc.tool_schemas()}}
        elif method == "tools/call":
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            response = self.rpc.tool_call_response(request_id, params)
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": request_id, "result": {}}
        elif request_id is not None:
            response = {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if response is not None:
            if not self.rpc.enqueue(session, response):
                self.rpc.log("WARN", f"channel_mcp_rpc_enqueue_failed session={session or '-'} method={method}")
                self.rpc.write_json(
                    handler,
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "MCP SSE session is not connected"},
                    },
                    404,
                )
                return True
            self.rpc.log(
                "INFO",
                f"channel_mcp_rpc_queued session={session or '-'} method={method} request_id={request_id}",
            )
        self.rpc.write_accepted(handler)
        return True
