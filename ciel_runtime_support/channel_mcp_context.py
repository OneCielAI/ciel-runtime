"""Bounded context for Ciel's stateless, built-in MCP tool server."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from .channel_mcp_http_controller import ChannelMcpHttpController, ChannelMcpHttpServices


@dataclass(frozen=True, slots=True)
class ChannelMcpRuntimePorts:
    version: str
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpRpcPorts:
    tool_schemas: Callable[[], list[dict[str, Any]]]
    tool_call_response: Callable[[Any, dict[str, Any]], dict[str, Any]]
    write_json: Callable[..., None]
    write_accepted: Callable[..., None]


@dataclass(frozen=True, slots=True)
class ChannelMcpContext:
    runtime: ChannelMcpRuntimePorts
    rpc: ChannelMcpRpcPorts

    def controller(self) -> ChannelMcpHttpController:
        return ChannelMcpHttpController(
            ChannelMcpHttpServices(
                version=self.runtime.version,
                tool_schemas=self.rpc.tool_schemas,
                tool_call_response=self.rpc.tool_call_response,
                write_json=self.rpc.write_json,
                write_accepted=self.rpc.write_accepted,
                log=self.runtime.log,
            )
        )

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self.controller().get(handler, path)

    def post(self, handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
        return self.controller().post(handler, path, body)


@dataclass(frozen=True, slots=True)
class ChannelMcpCompatibilityApi:
    context: Callable[[], ChannelMcpContext]

    def controller(self) -> ChannelMcpHttpController:
        return self.context().controller()

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        return self.context().get(handler, path)

    def post(self, handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
        return self.context().post(handler, path, body)


__all__ = [
    "ChannelMcpCompatibilityApi",
    "ChannelMcpContext",
    "ChannelMcpRpcPorts",
    "ChannelMcpRuntimePorts",
]
