"""Built-in channel MCP tool catalog and application service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelMcpToolServices:
    queue_compact: Callable[[str, str], dict[str, Any]]
    append_message: Callable[[dict[str, Any]], dict[str, Any]]
    store_file_path: Callable[[Any, str | None, str | None], dict[str, Any]]
    store_file_upload: Callable[[dict[str, Any]], dict[str, Any]]
    file_message_text: Callable[[str, list[dict[str, Any]]], str]
    handle_llm_options: Callable[[str, str], tuple[list[str], bool]]


def channel_mcp_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "compact_session",
            "description": (
                "Queue Claude Code's /compact slash command for the active Ciel Runtime-launched session. "
                "Use this when the conversation context is too large and the session should compact itself."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Optional short reason shown in Ciel Runtime logs.",
                    },
                },
            },
        },
        {
            "name": "send_message",
            "description": (
                "Send a reply or status message to a Ciel Runtime channel. "
                "Use this to answer messages delivered through the Ciel Runtime channel inbox, "
                "including /ca/web/chat browser sessions."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Destination channel id from the incoming message."},
                    "message": {"type": "string", "description": "Message body to send."},
                    "recipients": {
                        "description": "Recipient id, 'all', or an array of recipients. Use 'web' for /ca/web/chat replies."
                    },
                    "thread_id": {"type": "string", "description": "Thread/conversation id to continue."},
                    "parent_id": {"description": "Optional parent message id."},
                    "delivery": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Delivery targets. Use ['web'] for browser-only replies.",
                    },
                    "kind": {"type": "string", "description": "Optional message kind, for example 'reply' or 'status'."},
                },
                "required": ["channel", "message"],
            },
        },
        {
            "name": "send_file",
            "description": (
                "Send a file attachment to a Ciel Runtime channel. "
                "Use this to return files to /ca/web/chat browser sessions. "
                "Provide either path for an existing local file, or content with encoding='text' or encoding='base64'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "description": "Destination channel id from the incoming message."},
                    "path": {"type": "string", "description": "Optional local file path to attach."},
                    "content": {
                        "type": "string",
                        "description": "Optional inline file content when path is not used.",
                    },
                    "encoding": {"type": "string", "description": "Inline content encoding: text or base64."},
                    "name": {
                        "type": "string",
                        "description": "Display filename. Defaults to the source path basename or file.txt.",
                    },
                    "content_type": {"type": "string", "description": "Optional MIME type."},
                    "message": {"type": "string", "description": "Optional message body to show with the file link."},
                    "recipients": {
                        "description": "Recipient id, 'all', or an array of recipients. Use 'web' for /ca/web/chat replies."
                    },
                    "thread_id": {"type": "string", "description": "Thread/conversation id to continue."},
                    "parent_id": {"description": "Optional parent message id."},
                    "delivery": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Delivery targets. Use ['web'] for browser-only replies.",
                    },
                },
                "required": ["channel"],
            },
        },
        {
            "name": "llm_options",
            "description": (
                "Show, apply, or restore ciel-runtime live LLM option presets for the current routed session. "
                "Use action='list' to show keyboard-selectable slash commands, action='apply' with preset, "
                "or action='restore' to return to the captured original options."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "One of status, list, apply, or restore."},
                    "preset": {
                        "type": "string",
                        "description": (
                            "Preset id or alias when action is apply, for example balanced, long-context-256k, "
                            "long-context-300k, long-context-512k, or million-context-1m."
                        ),
                    },
                },
            },
        },
    ]


def channel_mcp_tool_response(request_id: Any, text: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}], "isError": bool(is_error)},
    }


def dispatch_channel_mcp_tool(
    request_id: Any,
    params: dict[str, Any],
    services: ChannelMcpToolServices,
) -> dict[str, Any]:
    name = str(params.get("name") or "")
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    if name == "compact_session":
        request = services.queue_compact("ciel-runtime-router-tool", str(args.get("reason") or ""))
        return _json_response(
            request_id,
            {
                "ok": True,
                "queued": True,
                "command": request.get("command"),
                "request_id": request.get("id"),
                "expires_at": request.get("expires_at"),
            },
        )
    if name == "send_message":
        return _send_message(request_id, args, services)
    if name == "send_file":
        return _send_file(request_id, args, services)
    if name == "llm_options":
        lines, changed = services.handle_llm_options(
            str(args.get("action") or "status"),
            str(args.get("preset") or ""),
        )
        return _json_response(request_id, {"ok": True, "changed": changed, "lines": lines})
    return channel_mcp_tool_response(request_id, f"Unknown ciel-runtime-router tool: {name}", True)


def _send_message(
    request_id: Any,
    args: dict[str, Any],
    services: ChannelMcpToolServices,
) -> dict[str, Any]:
    channel = str(args.get("channel") or "").strip()
    message = str(args.get("message") or args.get("text") or "").strip()
    if not channel or not message:
        return channel_mcp_tool_response(request_id, "send_message requires channel and message.", True)
    saved = services.append_message(_message_payload(args, channel, message, "reply"))
    return _json_response(request_id, {"ok": True, "message": saved})


def _send_file(
    request_id: Any,
    args: dict[str, Any],
    services: ChannelMcpToolServices,
) -> dict[str, Any]:
    channel = str(args.get("channel") or "").strip()
    if not channel:
        return channel_mcp_tool_response(request_id, "send_file requires channel.", True)
    try:
        upload = _store_file(args, services)
    except (FileNotFoundError, OverflowError, ValueError) as exc:
        return channel_mcp_tool_response(request_id, str(exc), True)
    uploads = [upload]
    message = services.file_message_text(str(args.get("message") or ""), uploads)
    payload = _message_payload(args, channel, message, "file")
    payload["meta"] = {"attachments": uploads, **payload["meta"]}
    saved = services.append_message(payload)
    return _json_response(request_id, {"ok": True, "file": upload, "message": saved})


def _store_file(args: dict[str, Any], services: ChannelMcpToolServices) -> dict[str, Any]:
    if args.get("path"):
        return services.store_file_path(
            args.get("path"),
            str(args.get("name") or "").strip() or None,
            str(args.get("content_type") or args.get("mime_type") or "").strip() or None,
        )
    return services.store_file_upload(
        {
            "name": str(args.get("name") or "file.txt"),
            "encoding": str(args.get("encoding") or "text"),
            "content": args.get("content", ""),
            "content_type": str(args.get("content_type") or args.get("mime_type") or "text/plain"),
        }
    )


def _message_payload(
    args: dict[str, Any],
    channel: str,
    message: str,
    default_kind: str,
) -> dict[str, Any]:
    meta = args.get("meta") if isinstance(args.get("meta"), dict) else {}
    return {
        "channel": channel,
        "sender_id": args.get("sender_id") or "claude-code",
        "recipients": args.get("recipients", args.get("recipient_id", "web")),
        "thread_id": args.get("thread_id"),
        "parent_id": args.get("parent_id"),
        "kind": args.get("kind") or default_kind,
        "message": message,
        "delivery": args.get("delivery", ["web"]),
        "meta": {"source": "ciel-runtime-router-tool", **meta},
    }


def _json_response(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return channel_mcp_tool_response(
        request_id,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
