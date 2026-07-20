"""HTTP adapter for Codex's split MCP transport."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True, slots=True)
class McpSplitProxyHttpPorts:
    resolve_server: Callable[[str], tuple[str, dict[str, Any]] | None]
    upstream_url: Callable[[dict[str, Any], str], str]
    runtime_headers: Callable[[dict[str, Any]], dict[str, str]]
    copy_response_headers: Callable[[Any, Any], None]
    is_client_disconnect: Callable[[BaseException], bool]
    write_json: Callable[..., Any]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class McpSplitProxyHttpAdapter:
    ports: McpSplitProxyHttpPorts
    channel_notification_method: str

    @staticmethod
    def local_sse_hold_seconds() -> float:
        raw = os.environ.get("CIEL_RUNTIME_CODEX_MCP_LOCAL_SSE_SECONDS", "3600")
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            value = 3600.0
        return max(0.0, min(24 * 3600.0, value))

    def handle_get(self, handler: Any, path: str) -> bool:
        resolved = self.ports.resolve_server(path)
        if resolved is None:
            return False
        name, _server = resolved
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        self.ports.log("INFO", f"codex_mcp_split_proxy_local_sse name={name} upstream_get=false")
        deadline = time.time() + self.local_sse_hold_seconds()
        try:
            while time.time() < deadline:
                handler.wfile.write(b": ciel-runtime owns upstream SSE for this MCP server\n\n")
                handler.wfile.flush()
                time.sleep(min(15.0, max(0.05, deadline - time.time())))
        except (BrokenPipeError, ConnectionError, ConnectionResetError):
            pass
        return True

    def handle_request(self, handler: Any, path: str, raw_body: bytes, method: str) -> bool:
        resolved = self.ports.resolve_server(path)
        if resolved is None:
            return False
        name, server = resolved
        verb = method.upper()
        query = urllib.parse.urlparse(handler.path).query
        upstream_url = self.ports.upstream_url(server, query)
        data = raw_body if verb in {"POST", "PUT", "PATCH"} else None
        try:
            request = urllib.request.Request(
                upstream_url,
                data=data,
                headers=self._request_headers(handler, server),
                method=verb,
            )
            with urllib.request.urlopen(request, timeout=120.0) as response:
                handler.send_response(getattr(response, "status", 200))
                self.ports.copy_response_headers(handler, response.headers)
                handler.end_headers()
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if content_type == "text/event-stream":
                    self._forward_sse(handler, response, name)
                else:
                    self._forward_body(handler, response)
            self.ports.log(
                "INFO",
                f"codex_mcp_split_proxy_forwarded name={name} method={verb} upstream={upstream_url}",
            )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            handler.send_response(exc.code)
            self.ports.copy_response_headers(handler, exc.headers)
            handler.end_headers()
            if raw:
                handler.wfile.write(raw)
            self.ports.log("WARN", f"codex_mcp_split_proxy_http_error name={name} method={verb} status={exc.code}")
        except Exception as exc:
            if self.ports.is_client_disconnect(exc):
                return True
            self.ports.write_json(handler, {"error": {"message": f"{type(exc).__name__}: {exc}"}}, status=502)
            self.ports.log(
                "WARN",
                f"codex_mcp_split_proxy_failed name={name} method={verb} error={type(exc).__name__}: {exc}",
            )
        return True

    def _request_headers(self, handler: Any, server: dict[str, Any]) -> dict[str, str]:
        headers = self.ports.runtime_headers(server)
        skipped = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}
        for key, value in handler.headers.items():
            if str(key).lower() not in skipped:
                headers[str(key)] = str(value)
        return headers

    @staticmethod
    def _forward_body(handler: Any, response: Any) -> None:
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()

    def _forward_sse(self, handler: Any, response: Any, server_name: str) -> None:
        event = bytearray()
        while True:
            line = response.readline()
            if line:
                event.extend(line)
            if not line or line in {b"\n", b"\r\n"}:
                if event:
                    raw_event = bytes(event)
                    if self._is_channel_event(raw_event):
                        self.ports.log(
                            "INFO",
                            f"codex_mcp_split_proxy_channel_notification_suppressed "
                            f"name={server_name} source=post_sse",
                        )
                    else:
                        handler.wfile.write(raw_event)
                        handler.wfile.flush()
                    event.clear()
                if not line:
                    break

    def _is_channel_event(self, event: bytes) -> bool:
        data_lines: list[str] = []
        for raw_line in event.splitlines():
            field, separator, value = raw_line.decode("utf-8", errors="replace").partition(":")
            if separator and field == "data":
                data_lines.append(value[1:] if value.startswith(" ") else value)
        if not data_lines:
            return False
        try:
            payload = json.loads("\n".join(data_lines))
        except (json.JSONDecodeError, TypeError):
            return False
        return bool(
            isinstance(payload, dict)
            and str(payload.get("method") or "").strip() == self.channel_notification_method
        )


__all__ = ["McpSplitProxyHttpAdapter", "McpSplitProxyHttpPorts"]
