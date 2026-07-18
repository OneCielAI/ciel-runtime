"""Pure codecs and capability rules used by MCP channel probes."""

from __future__ import annotations

import json
from typing import Any


def probe_strategy(server: dict[str, Any]) -> str:
    """Select JSONL by default, with explicit legacy LSP framing opt-in."""

    if not isinstance(server, dict):
        return "jsonl"
    mode = str(server.get("ciel_runtime_stdio") or server.get("stdio_mode") or "").strip().lower()
    return "framed" if mode in ("framed", "framed-only", "content-length", "lsp") else "jsonl"


def initialize_payload(client_version: str, protocol_version: str = "2024-11-05") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "ciel-runtime-channel-probe", "version": client_version},
        },
    }


def initialize_payload_bytes(client_version: str, protocol_version: str = "2024-11-05") -> bytes:
    return json.dumps(
        initialize_payload(client_version, protocol_version),
        ensure_ascii=False,
    ).encode("utf-8")


def parse_framed_responses(buffer: bytes) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    index = 0
    while index < len(buffer):
        header_end = buffer.find(b"\r\n\r\n", index)
        if header_end < 0:
            return out
        header = buffer[index:header_end].decode("ascii", errors="replace")
        length = _content_length(header)
        if length is None:
            return out
        body_start = header_end + 4
        body_end = body_start + length
        if len(buffer) < body_end:
            return out
        message = _json_object(buffer[body_start:body_end])
        if message is not None:
            out.append(message)
        index = body_end
    return out


def _content_length(header: str) -> int | None:
    for line in header.split("\r\n"):
        if not line.lower().startswith("content-length:"):
            continue
        try:
            value = int(line.split(":", 1)[1].strip())
        except (IndexError, TypeError, ValueError):
            return None
        return value if value >= 0 else None
    return None


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        message = json.loads(raw.decode("utf-8", errors="replace"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return message if isinstance(message, dict) else None


def parse_jsonl_responses(buffer: bytes) -> list[dict[str, Any]]:
    return [
        message
        for raw_line in buffer.split(b"\n")
        if raw_line.strip()
        for message in [_json_object(raw_line.strip())]
        if message is not None
    ]


def find_initialize_response(buffer: bytes, framed: bool) -> dict[str, Any] | None:
    messages = parse_framed_responses(buffer) if framed else parse_jsonl_responses(buffer)
    return next((message for message in messages if message.get("id") == 1 and "result" in message), None)


def channel_capability_present(initialize_response: dict[str, Any]) -> bool:
    result = initialize_response.get("result")
    capabilities = result.get("capabilities") if isinstance(result, dict) else None
    experimental = capabilities.get("experimental") if isinstance(capabilities, dict) else None
    value = experimental.get("claude/channel") if isinstance(experimental, dict) else None
    return value is not None and value is not False


def decode_sse_events(buffer: bytearray) -> tuple[list[tuple[str, str]], bytearray]:
    """Drain complete SSE events and return the incomplete byte remainder."""

    events: list[tuple[str, str]] = []
    text = bytes(buffer).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    while True:
        separator = text.find("\n\n")
        if separator < 0:
            break
        event_text, text = text[:separator], text[separator + 2:]
        event_name = "message"
        data_lines: list[str] = []
        for line in event_text.split("\n"):
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].lstrip()
            elif line.startswith("data:"):
                payload = line[len("data:"):]
                data_lines.append(payload[1:] if payload.startswith(" ") else payload)
        events.append((event_name, "\n".join(data_lines)))
    return events, bytearray(text.encode("utf-8"))


__all__ = [
    "channel_capability_present",
    "decode_sse_events",
    "find_initialize_response",
    "initialize_payload",
    "initialize_payload_bytes",
    "parse_framed_responses",
    "parse_jsonl_responses",
    "probe_strategy",
]
