"""Built-in channel MCP server bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
from typing import Any, Callable

from .channel_cursor_repository import ChannelCursorRepository
from .channel_cursor_service import (
    ChannelCursorService,
    ChannelCursorServices,
    ChannelResumePolicy,
    ChannelResumeServices,
)
from .channel_mcp_http_controller import (
    ChannelMcpHttpController,
    ChannelMcpRpcServices,
    ChannelMcpSessionStore,
    ChannelMcpStreamServices,
)


@dataclass(frozen=True, slots=True)
class ChannelMcpRuntimePorts:
    version: str
    process_id: Callable[[], int]
    timestamp_ns: Callable[[], int]
    condition: Any
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpStatePorts:
    sessions: dict[str, dict[str, Any]]
    session_lock: Any
    cursor_lock: Any
    cursor_path: Path
    cached_cursor: Callable[[], int | None]
    cache_cursor: Callable[[int], None]
    scan_tail: Callable[[], int]


@dataclass(frozen=True, slots=True)
class ChannelMcpProjectionPorts:
    capabilities: Callable[[], dict[str, Any]]
    notifications: Callable[..., list[tuple[int, dict[str, Any]]]]
    read_messages: Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ChannelMcpRpcPorts:
    tool_schemas: Callable[[], list[dict[str, Any]]]
    tool_call_response: Callable[[Any, dict[str, Any]], dict[str, Any]]
    write_json: Callable[..., None]
    write_accepted: Callable[..., None]


@dataclass(frozen=True, slots=True)
class ChannelMcpResumePorts:
    query_params: Callable[..., dict[str, list[str]]]
    first_param: Callable[..., str]
    ensure_cursor: Callable[[], int]
    update_cursor: Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpContext:
    runtime: ChannelMcpRuntimePorts
    state: ChannelMcpStatePorts
    projection: ChannelMcpProjectionPorts
    rpc: ChannelMcpRpcPorts
    resume: ChannelMcpResumePorts
    cursor_repository: Callable[[Path], ChannelCursorRepository]

    def new_session_id(self) -> str:
        return f"s{self.runtime.process_id()}-{self.runtime.timestamp_ns()}"

    @staticmethod
    def write_sse_event(
        handler: BaseHTTPRequestHandler,
        event: str,
        data: Any,
        event_id: int | None = None,
    ) -> None:
        if event_id is not None:
            handler.wfile.write(f"id: {event_id}\n".encode("utf-8"))
        handler.wfile.write(f"event: {event}\n".encode("utf-8"))
        payload = (
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        )
        for line in payload.splitlines() or [""]:
            handler.wfile.write(f"data: {line}\n".encode("utf-8"))
        handler.wfile.write(b"\n")
        handler.wfile.flush()

    @staticmethod
    def send_sse_headers(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache, no-transform")
        handler.send_header("connection", "keep-alive")
        handler.send_header("x-accel-buffering", "no")
        handler.end_headers()

    def enqueue(self, session: str, payload: dict[str, Any]) -> bool:
        if not session:
            return False
        with self.state.session_lock:
            state = self.state.sessions.get(session)
            if not state:
                return False
            outbox = state.setdefault("outbox", [])
            if isinstance(outbox, list):
                outbox.append(payload)
            else:
                state["outbox"] = [payload]
        with self.runtime.condition:
            self.runtime.condition.notify_all()
        return True

    def take_outbox(self, session: str) -> list[dict[str, Any]]:
        with self.state.session_lock:
            state = self.state.sessions.get(session)
            if not state:
                return []
            outbox = state.get("outbox")
            if not isinstance(outbox, list) or not outbox:
                return []
            state["outbox"] = []
            return [item for item in outbox if isinstance(item, dict)]

    def initialize_response(self, request_id: Any, protocol: str) -> dict[str, Any]:
        del protocol
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": self.projection.capabilities(),
                "serverInfo": {
                    "name": "ciel-runtime-router",
                    "version": self.runtime.version,
                },
            },
        }

    def cursor_service(self) -> ChannelCursorService:
        return ChannelCursorService(
            ChannelCursorServices(
                repository=self.cursor_repository(self.state.cursor_path),
                lock=self.state.cursor_lock,
                cached=self.state.cached_cursor,
                cache=self.state.cache_cursor,
                scan_tail=self.state.scan_tail,
            )
        )

    def write_cursor(self, last_id: int) -> None:
        self.cursor_repository(self.state.cursor_path).write(last_id)

    def read_cursor(self) -> int:
        return self.cursor_service().read_locked()

    def ensure_cursor(self) -> int:
        return self.cursor_service().ensure_initialized()

    def update_cursor(self, last_id: int) -> None:
        self.cursor_service().update(last_id)

    def resume_policy(self) -> ChannelResumePolicy:
        return ChannelResumePolicy(
            ChannelResumeServices(
                query_params=self.resume.query_params,
                first_param=self.resume.first_param,
                ensure_cursor=self.resume.ensure_cursor,
                update_cursor=self.resume.update_cursor,
                log=self.runtime.log,
            )
        )

    def client_last_event_id(
        self, handler: BaseHTTPRequestHandler
    ) -> int | None:
        return self.resume_policy().client_last_event_id(handler)

    def session_start_last_id(self, handler: BaseHTTPRequestHandler) -> int:
        return self.resume_policy().session_start_last_id(handler)

    def controller(self) -> ChannelMcpHttpController:
        return ChannelMcpHttpController(
            store=ChannelMcpSessionStore(
                self.state.sessions, self.state.session_lock
            ),
            stream=ChannelMcpStreamServices(
                new_session_id=self.new_session_id,
                start_last_id=self.session_start_last_id,
                send_headers=self.send_sse_headers,
                write_event=self.write_sse_event,
                take_outbox=self.take_outbox,
                read_messages=self.projection.read_messages,
                project_notifications=self.projection.notifications,
                update_cursor=self.update_cursor,
                condition=self.runtime.condition,
                log=self.runtime.log,
            ),
            rpc=ChannelMcpRpcServices(
                initialize_response=self.initialize_response,
                tool_schemas=self.rpc.tool_schemas,
                tool_call_response=self.rpc.tool_call_response,
                enqueue=self.enqueue,
                write_json=self.rpc.write_json,
                write_accepted=self.rpc.write_accepted,
                log=self.runtime.log,
            ),
        )

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self.controller().get(handler, path)

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        body: dict[str, Any],
    ) -> bool:
        return self.controller().post(handler, path, body)


@dataclass(frozen=True, slots=True)
class ChannelMcpCompatibilityApi:
    context: Callable[[], ChannelMcpContext]

    def new_session_id(self) -> str:
        return self.context().new_session_id()

    def write_sse_event(
        self,
        handler: BaseHTTPRequestHandler,
        event: str,
        data: Any,
        event_id: int | None = None,
    ) -> None:
        self.context().write_sse_event(handler, event, data, event_id)

    def send_sse_headers(self, handler: BaseHTTPRequestHandler) -> None:
        self.context().send_sse_headers(handler)

    def enqueue(self, session: str, payload: dict[str, Any]) -> bool:
        return self.context().enqueue(session, payload)

    def take_outbox(self, session: str) -> list[dict[str, Any]]:
        return self.context().take_outbox(session)

    def initialize_response(
        self, request_id: Any, protocol: str
    ) -> dict[str, Any]:
        return self.context().initialize_response(request_id, protocol)

    def cursor_service(self) -> ChannelCursorService:
        return self.context().cursor_service()

    def write_cursor(self, last_id: int) -> None:
        self.context().write_cursor(last_id)

    def read_cursor(self) -> int:
        return self.context().read_cursor()

    def ensure_cursor(self) -> int:
        return self.context().ensure_cursor()

    def update_cursor(self, last_id: int) -> None:
        self.context().update_cursor(last_id)

    def resume_policy(self) -> ChannelResumePolicy:
        return self.context().resume_policy()

    def client_last_event_id(
        self, handler: BaseHTTPRequestHandler
    ) -> int | None:
        return self.context().client_last_event_id(handler)

    def session_start_last_id(self, handler: BaseHTTPRequestHandler) -> int:
        return self.context().session_start_last_id(handler)

    def controller(self) -> ChannelMcpHttpController:
        return self.context().controller()

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self.context().get(handler, path)

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        body: dict[str, Any],
    ) -> bool:
        return self.context().post(handler, path, body)


__all__ = [
    "ChannelMcpCompatibilityApi",
    "ChannelMcpContext",
    "ChannelMcpProjectionPorts",
    "ChannelMcpResumePorts",
    "ChannelMcpRpcPorts",
    "ChannelMcpRuntimePorts",
    "ChannelMcpStatePorts",
]
