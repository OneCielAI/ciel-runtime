"""Stateless HTTP controller for Ciel's built-in MCP tool server.

The router does not implement an MCP notification channel.  It exposes only
the tools needed by Web Chat and explicit Ciel runtime actions.  External MCP
servers are owned and transported by the CLI client that configured them.
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable


MCP_2026_PROTOCOL_VERSION = "2026-07-28"
LEGACY_STREAMABLE_HTTP_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
)
SUPPORTED_PROTOCOL_VERSIONS = (MCP_2026_PROTOCOL_VERSION, *LEGACY_STREAMABLE_HTTP_VERSIONS)
SERVER_NAME = "ciel-runtime-router"


@dataclass(frozen=True, slots=True)
class ChannelMcpHttpServices:
    version: str
    tool_schemas: Callable[[], list[dict[str, Any]]]
    tool_call_response: Callable[[Any, dict[str, Any]], dict[str, Any]]
    write_json: Callable[..., None]
    write_accepted: Callable[[BaseHTTPRequestHandler], None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelMcpHttpController:
    services: ChannelMcpHttpServices

    def get(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        if path == "/ca/mcp/health":
            self.services.write_json(
                handler,
                {
                    "ok": True,
                    "name": SERVER_NAME,
                    "endpoint": "/ca/mcp",
                    "transport": "streamable-http",
                    "stateless": True,
                    "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                },
            )
            return True
        if path == "/ca/mcp":
            self.services.write_json(
                handler,
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "MCP endpoint accepts POST only"},
                },
                405,
            )
            return True
        # The deprecated 2024 HTTP+SSE endpoint intentionally does not exist.
        return False

    def post(
        self,
        handler: BaseHTTPRequestHandler,
        path: str,
        body: dict[str, Any],
    ) -> bool:
        if path != "/ca/mcp":
            return False
        request_id = body.get("id")
        method = str(body.get("method") or "")
        modern = self._is_modern_request(handler, body)
        if modern:
            validation_error = self._validate_modern_request(handler, body)
            if validation_error:
                payload, status = validation_error
                self.services.write_json(handler, payload, status)
                return True
        response, status = self._rpc_response(body, modern=modern)
        if response is None:
            self.services.write_accepted(handler)
        else:
            self.services.write_json(handler, response, status)
        self.services.log(
            "INFO",
            "channel_mcp_http method=%s request_id=%s protocol=%s"
            % (method, request_id, MCP_2026_PROTOCOL_VERSION if modern else "legacy-streamable-http"),
        )
        return True

    @staticmethod
    def _is_modern_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
        header_version = str(handler.headers.get("MCP-Protocol-Version") or "")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        return (
            header_version == MCP_2026_PROTOCOL_VERSION
            or bool(header_version and header_version not in LEGACY_STREAMABLE_HTTP_VERSIONS)
            or "io.modelcontextprotocol/protocolVersion" in meta
            or str(body.get("method") or "") == "server/discover"
        )

    def _validate_modern_request(
        self,
        handler: BaseHTTPRequestHandler,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], int] | None:
        request_id = body.get("id")
        method = str(body.get("method") or "")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        header_version = str(handler.headers.get("MCP-Protocol-Version") or "")
        body_version = str(meta.get("io.modelcontextprotocol/protocolVersion") or "")
        requested = header_version or body_version
        if requested not in SUPPORTED_PROTOCOL_VERSIONS:
            return self._error(
                request_id,
                -32022,
                "Unsupported protocol version",
                data={"supported": list(SUPPORTED_PROTOCOL_VERSIONS), "requested": requested},
            ), 400
        if header_version != body_version:
            return self._header_error(request_id, "MCP-Protocol-Version does not match request _meta"), 400
        if not isinstance(meta.get("io.modelcontextprotocol/clientCapabilities"), dict):
            return self._error(
                request_id,
                -32602,
                "Missing required client capabilities",
            ), 400
        if str(handler.headers.get("Mcp-Method") or "") != method:
            return self._header_error(request_id, "Mcp-Method does not match request method"), 400
        if method == "tools/call":
            expected_name = str(params.get("name") or "")
            actual_name = self._decoded_header(handler.headers.get("Mcp-Name"))
            if actual_name != expected_name:
                return self._header_error(request_id, "Mcp-Name does not match params.name"), 400
        origin = str(handler.headers.get("Origin") or "").strip()
        if origin and not self._same_origin(origin, str(handler.headers.get("Host") or "")):
            return self._error(request_id, -32600, "Invalid Origin header"), 403
        return None

    def _rpc_response(
        self,
        body: dict[str, Any],
        *,
        modern: bool,
    ) -> tuple[dict[str, Any] | None, int]:
        request_id = body.get("id")
        method = str(body.get("method") or "")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        if not method:
            return self._error(request_id, -32600, "Invalid Request"), 400
        if request_id is None:
            # 2026 core defines no client-to-server HTTP notifications.  Older
            # official clients may still send initialized/cancel notifications.
            return None, 202
        if modern and method == "server/discover":
            return self._response(
                request_id,
                self._modern_result(
                    {
                        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "capabilities": {"tools": {"listChanged": False}},
                        "instructions": "Tools for Web Chat replies and explicit Ciel runtime actions.",
                        "ttlMs": 300_000,
                        "cacheScope": "private",
                    }
                ),
            ), 200
        if not modern and method == "initialize":
            requested = str(params.get("protocolVersion") or LEGACY_STREAMABLE_HTTP_VERSIONS[-1])
            protocol = requested if requested in LEGACY_STREAMABLE_HTTP_VERSIONS else LEGACY_STREAMABLE_HTTP_VERSIONS[0]
            return self._response(
                request_id,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": self.services.version},
                    "instructions": "Tools for Web Chat replies and explicit Ciel runtime actions.",
                },
            ), 200
        if method == "tools/list":
            result: dict[str, Any] = {"tools": self.services.tool_schemas()}
            if modern:
                result.update({"ttlMs": 300_000, "cacheScope": "private"})
                result = self._modern_result(result)
            return self._response(request_id, result), 200
        if method == "tools/call":
            response = self.services.tool_call_response(request_id, params)
            if modern and isinstance(response.get("result"), dict):
                response["result"] = self._modern_result(dict(response["result"]))
            return response, 200
        if method == "ping":
            if modern:
                return self._error(request_id, -32601, "Method not found"), 404
            return self._response(request_id, {}), 200
        return self._error(request_id, -32601, "Method not found"), 404 if modern else 200

    def _modern_result(self, result: dict[str, Any]) -> dict[str, Any]:
        result["resultType"] = "complete"
        result.setdefault(
            "_meta",
            {"io.modelcontextprotocol/serverInfo": {"name": SERVER_NAME, "version": self.services.version}},
        )
        return result

    @staticmethod
    def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(
        request_id: Any,
        code: int,
        message: str,
        *,
        data: Any | None = None,
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def _header_error(self, request_id: Any, message: str) -> dict[str, Any]:
        return self._error(request_id, -32020, f"Header mismatch: {message}")

    @staticmethod
    def _decoded_header(value: str | None) -> str:
        text = str(value or "")
        if text.startswith("=?base64?") and text.endswith("?="):
            try:
                return base64.b64decode(text[9:-2], validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                return ""
        return text

    @staticmethod
    def _same_origin(origin: str, host: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(origin)
        except ValueError:
            return False
        return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == host.lower()


__all__ = [
    "ChannelMcpHttpController",
    "ChannelMcpHttpServices",
    "LEGACY_STREAMABLE_HTTP_VERSIONS",
    "MCP_2026_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
]
