"""MCP HTTP transport primitives with no channel or router state."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


MCP_STREAMABLE_HTTP_PROTOCOL_VERSION = "2025-03-26"
MCP_LEGACY_SSE_PROTOCOL_VERSION = "2024-11-05"
CODEX_MCP_SPLIT_PROXY_PREFIX = "/ca/codex-mcp/"


def read_sse_json_response(response: Any, request_id: Any | None = None) -> Any:
    """Read the first matching JSON object from an SSE response."""

    data_lines: list[str] = []
    while True:
        raw = response.readline()
        if raw == b"":
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            message = _matching_json_message(data_lines, request_id)
            if message is not None:
                return message
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    return _matching_json_message(data_lines, request_id)


def _matching_json_message(data_lines: list[str], request_id: Any | None) -> dict[str, Any] | None:
    if not data_lines:
        return None
    try:
        message = json.loads("\n".join(data_lines).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    if request_id is not None and "id" in message and message.get("id") != request_id:
        return None
    return message


def post_json_with_response_headers(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> tuple[Any, Any]:
    request_headers = {**headers, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in content_type:
            return read_sse_json_response(response, payload.get("id")), response.headers
        data = response.read()
        if not data:
            return None, response.headers
        try:
            return json.loads(data.decode("utf-8")), response.headers
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return data.decode("utf-8", errors="replace"), response.headers


def sse_post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> Any:
    result, _headers = post_json_with_response_headers(endpoint, headers, payload, timeout)
    return result


def streamable_headers(
    headers: dict[str, str],
    protocol_version: str,
    session_id: str | None = None,
    *,
    accept: str = "application/json, text/event-stream",
) -> dict[str, str]:
    out = {**headers, "Accept": accept, "MCP-Protocol-Version": protocol_version}
    if session_id:
        out["Mcp-Session-Id"] = session_id
    return out


def streamable_post_json(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    protocol_version: str,
    session_id: str | None = None,
) -> tuple[Any, str | None]:
    result, response_headers = post_json_with_response_headers(
        endpoint,
        streamable_headers(headers, protocol_version, session_id),
        payload,
        timeout,
    )
    returned_session = None
    if response_headers is not None:
        returned_session = response_headers.get("Mcp-Session-Id") or response_headers.get("MCP-Session-Id")
    return result, str(returned_session).strip() if returned_session else None


def split_proxy_server_name(path: str, prefix: str = CODEX_MCP_SPLIT_PROXY_PREFIX) -> str | None:
    if not path.startswith(prefix):
        return None
    suffix = path[len(prefix):]
    if not suffix or "/" in suffix:
        return None
    name = urllib.parse.unquote(suffix).strip()
    return name or None


def upstream_url(server: dict[str, Any], query: str = "") -> str:
    url = str(server.get("url") or server.get("endpoint") or "").strip()
    if query:
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        url = f"{url}{separator}{query}"
    return url


__all__ = [
    "CODEX_MCP_SPLIT_PROXY_PREFIX",
    "MCP_LEGACY_SSE_PROTOCOL_VERSION",
    "MCP_STREAMABLE_HTTP_PROTOCOL_VERSION",
    "post_json_with_response_headers",
    "read_sse_json_response",
    "split_proxy_server_name",
    "sse_post_json",
    "streamable_headers",
    "streamable_post_json",
    "upstream_url",
]
