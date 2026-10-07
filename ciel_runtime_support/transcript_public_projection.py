"""The public projection of transcript JSONL sent by ``transcript_events``.

Walkie's relay (``server/agent-transcript-relay.py`` ``public_records``) strips
user text, tool arguments and results, and reasoning before anything leaves
the PC.  Runtime posting transcripts directly sent the raw JSONL instead, so
``transcript_events.content_filter`` defaults to ``public_only``, which applies
the same projection here, on the PC (Walkie 34059 / 35053).

What is kept, in the relay's record shapes:

* user messages: only a Walkie routing ticket (``data.activity_context`` that
  starts with ``wkp_``) from one ``[ciel-runtime external channel message]``
  line; any other user text becomes an empty message;
* assistant text: Codex ``commentary`` messages and Claude text blocks;
* tool calls: the tool name only;
* turn ends: one ``task_complete`` per turn.
"""

from __future__ import annotations

import json
import re
from typing import Any

CONTENT_FILTERS = ("public_only", "raw")
_CHANNEL_PREFIX = "[ciel-runtime external channel message] "
_TOOL_NAME = re.compile(r"[\w.:-]{1,100}", re.ASCII)
_TICKET_LIMIT = 4096


def content_filter(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in CONTENT_FILTERS else "public_only"


def _text(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
        and part.get("type") in ("text", "input_text", "output_text")
        and isinstance(part.get("text"), str)
    )


def _routing_ticket_text(value: str) -> str:
    tickets: set[str] = set()
    for line in value.splitlines():
        if not line.startswith(_CHANNEL_PREFIX) or " text=" not in line:
            continue
        try:
            event = json.loads(json.loads(line.split(" text=", 1)[1]))
            ticket = event.get("data", {}).get("activity_context")
        except (ValueError, TypeError, AttributeError):
            continue
        if (
            isinstance(event, dict)
            and event.get("specversion") == "1.0"
            and isinstance(ticket, str)
            and ticket.startswith("wkp_")
            and len(ticket) <= _TICKET_LIMIT
        ):
            tickets.add(ticket)
    if len(tickets) != 1:
        return ""
    event = {"specversion": "1.0", "data": {"activity_context": next(iter(tickets))}}
    return _CHANNEL_PREFIX.rstrip() + " text=" + json.dumps(json.dumps(event))


def public_records(content: str) -> str:
    """``content`` (complete JSONL records) reduced to the public projection."""

    result: list[dict[str, Any]] = []
    ended = False

    def complete() -> None:
        nonlocal ended
        if not ended:
            result.append({"type": "event_msg", "payload": {"type": "task_complete"}})
        ended = True

    def message(role: str, value: str) -> None:
        result.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "phase": "commentary",
                    "content": [{"type": "text", "text": value}],
                },
            }
        )

    def tool(name: Any) -> None:
        name = name if isinstance(name, str) and _TOOL_NAME.fullmatch(name) else "running"
        result.append({"type": "response_item", "payload": {"type": "function_call", "name": name}})

    for line in content.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if row.get("type") == "response_item" else None
        if isinstance(payload, dict):
            kind = payload.get("type")
            if kind == "message" and payload.get("role") == "user":
                message("user", _routing_ticket_text(_text(payload.get("content"))))
            elif kind == "message" and payload.get("role") == "assistant" and (
                payload.get("phase") == "commentary" or payload.get("channel") == "commentary"
            ):
                message("assistant", _text(payload.get("content")))
            elif kind in ("function_call", "custom_tool_call"):
                tool(payload.get("name"))
        elif row.get("type") == "user" and isinstance(row.get("message"), dict):
            parts = row["message"].get("content", [])
            if not isinstance(parts, list) or not any(
                isinstance(part, dict) and part.get("type") == "tool_result" for part in parts
            ):
                ended = False
                message("user", _routing_ticket_text(_text(parts)))
        elif row.get("type") == "assistant" and isinstance(row.get("message"), dict):
            parts = row["message"].get("content", [])
            for part in parts if isinstance(parts, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text = part.get("text", "")
                    message("assistant", text if isinstance(text, str) else "")
                elif part.get("type") == "tool_use":
                    tool(part.get("name"))
            if row["message"].get("stop_reason") == "end_turn":
                complete()
        elif row.get("type") == "system" and row.get("subtype") == "turn_duration":
            complete()
        elif row.get("type") == "result" or (
            row.get("type") == "event_msg"
            and isinstance(row.get("payload"), dict)
            and row["payload"].get("type") == "task_complete"
        ):
            result.append({"type": "event_msg", "payload": {"type": "task_complete"}})
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in result)


__all__ = ["CONTENT_FILTERS", "content_filter", "public_records"]
