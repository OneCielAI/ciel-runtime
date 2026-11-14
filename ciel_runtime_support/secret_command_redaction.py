"""Keep the raw key of a `/api-key KEY` turn out of every copy the router makes.

The slash command template places the raw key in the user turn. The router
handles that turn locally, but the turn stays in the conversation history, so
traces, previews, observers and later upstream requests would all see it.
Only text that carries the command marker is replaced; nothing else changes.
"""

from __future__ import annotations

from typing import Any

from .slash_command_assets import LIVE_API_KEYS_REQUEST_MARKERS

REDACTED_API_KEY_COMMAND = "[ciel-runtime API key command redacted]"


def contains_api_key_command(text: Any) -> bool:
    value = text if isinstance(text, str) else ""
    return any(marker in value for marker in LIVE_API_KEYS_REQUEST_MARKERS)


def redact_api_key_command_text(text: Any) -> Any:
    return REDACTED_API_KEY_COMMAND if contains_api_key_command(text) else text


def _redacted_content(content: Any) -> tuple[Any, bool]:
    if isinstance(content, str):
        return (REDACTED_API_KEY_COMMAND, True) if contains_api_key_command(content) else (content, False)
    if not isinstance(content, list):
        return content, False
    changed = False
    blocks: list[Any] = []
    for block in content:
        if isinstance(block, str) and contains_api_key_command(block):
            blocks.append(REDACTED_API_KEY_COMMAND)
            changed = True
        elif (
            isinstance(block, dict)
            and block.get("type") in {"text", "input_text"}
            and contains_api_key_command(block.get("text"))
        ):
            blocks.append({**block, "text": REDACTED_API_KEY_COMMAND})
            changed = True
        else:
            blocks.append(block)
    return (blocks, True) if changed else (content, False)


def redact_api_key_command_turns(body: Any) -> Any:
    """``body`` with each user turn that carries the command replaced.

    Returns the same object when nothing matches, and a shallow copy with new
    message lists otherwise, so a caller's original body is never mutated.
    """

    if not isinstance(body, dict):
        return body
    result = body
    for field in ("messages", "input"):
        items = body.get(field)
        if not isinstance(items, list):
            continue
        replaced: list[Any] = []
        changed = False
        for item in items:
            if isinstance(item, dict) and str(item.get("role") or "user") == "user":
                content, item_changed = _redacted_content(item.get("content"))
                if item_changed:
                    replaced.append({**item, "content": content})
                    changed = True
                    continue
            replaced.append(item)
        if changed:
            if result is body:
                result = dict(body)
            result[field] = replaced
    return result


__all__ = [
    "REDACTED_API_KEY_COMMAND",
    "contains_api_key_command",
    "redact_api_key_command_text",
    "redact_api_key_command_turns",
]
