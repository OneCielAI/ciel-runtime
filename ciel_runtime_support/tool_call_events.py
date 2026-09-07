"""Normalize tool-call records emitted by supported CLI transcript formats."""

from __future__ import annotations

import json
from typing import Any


_CODEX_CALL_TYPES = {"function_call", "custom_tool_call", "local_shell_call"}
_CODEX_BEGIN_TYPES = {"mcp_tool_call_begin", "tool_call_begin"}
_CLAUDE_CALL_TYPES = {"tool_use", "server_tool_use"}


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _call_id(value: dict[str, Any]) -> str:
    for key in ("call_id", "id", "tool_call_id"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def _arguments(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def _project(value: dict[str, Any], *, call_type: str) -> dict[str, Any]:
    name = str(value.get("name") or value.get("tool_name") or "").strip()
    arguments = value.get("arguments")
    if arguments is None:
        arguments = value.get("input")
    if arguments is None:
        arguments = value.get("action")
    if arguments is None:
        arguments = value.get("args")
    return {
        "call_id": _call_id(value),
        "name": name or call_type,
        "call_type": call_type,
        "arguments": _arguments(arguments),
    }


def project_transcript_tool_calls(
    record: dict[str, Any], runtime: str
) -> list[dict[str, Any]]:
    """Project one Claude/Codex JSONL record into normalized tool-call starts."""

    record_type = str(record.get("type") or "").strip()
    payload = _object(record.get("payload"))
    payload_type = str(payload.get("type") or "").strip()
    calls: list[dict[str, Any]] = []
    if record_type == "response_item" and payload_type in _CODEX_CALL_TYPES:
        calls.append(_project(payload, call_type=payload_type))
    elif record_type == "event_msg" and payload_type in _CODEX_BEGIN_TYPES:
        invocation = _object(payload.get("invocation")) or _object(payload.get("tool_call"))
        calls.append(_project({**payload, **invocation}, call_type=payload_type))

    muse_payload_type = str(record.get("payload_type") or "").strip()
    if str(record.get("record_type") or "").strip() == "event" and muse_payload_type == "runtime.session":
        muse_event = _object(payload.get("event"))
        muse_calls = muse_event.get("tool_calls")
        if isinstance(muse_calls, list):
            for call in muse_calls:
                if isinstance(call, dict):
                    calls.append(_project(call, call_type="muse_tool_call"))

    message = _object(record.get("message"))
    message_role = str(message.get("role") or "").strip()
    if record_type == "assistant" or message_role == "assistant":
        content = message.get("content")
        blocks = [content] if isinstance(content, dict) else content if isinstance(content, list) else []
        for block in blocks:
            block_obj = _object(block)
            block_type = str(block_obj.get("type") or "").strip()
            if block_type in _CLAUDE_CALL_TYPES:
                calls.append(_project(block_obj, call_type=block_type))

    model = str(record.get("model") or message.get("model") or "").strip()
    for call in calls:
        call["runtime"] = str(runtime or "runtime")
        if model:
            call["model"] = model
    return calls


__all__ = ["project_transcript_tool_calls"]
