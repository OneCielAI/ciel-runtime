"""Normalize retained Anthropic tool turns for non-native provider wires."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnthropicToolTurnServices:
    log: Callable[[str, str], Any]


def normalize_historical_anthropic_tool_turns(
    provider: str,
    body: dict[str, Any],
    services: AnthropicToolTurnServices,
) -> dict[str, Any]:
    """Discard unmatched historical tool blocks instead of replaying them as text.

    An unmatched tool block cannot be sent on provider wires that require strict
    tool-use/result pairing.  Turning it into conversation text is also unsafe:
    the model can treat the diagnostic, command, or stale result as a new user
    instruction.  Keep valid pairs verbatim and report discarded history only
    through the runtime log.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body

    changed = False
    converted_tool_uses = 0
    converted_tool_results = 0
    normalized_messages: list[Any] = []
    retained_tool_ids_for_next_user: set[str] = set()

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            normalized_messages.append(message)
            retained_tool_ids_for_next_user = set()
            continue

        role = str(message.get("role") or "")
        content = message.get("content")
        if role == "assistant" and isinstance(content, list):
            tool_ids = _tool_use_ids(message)
            if not tool_ids:
                normalized_messages.append(message)
                retained_tool_ids_for_next_user = set()
                continue
            next_message = messages[index + 1] if index + 1 < len(messages) else None
            next_result_ids = (
                set(_tool_result_ids(next_message))
                if isinstance(next_message, dict) and str(next_message.get("role") or "") == "user"
                else set()
            )
            retained = {tool_id for tool_id in tool_ids if tool_id in next_result_ids}
            next_content: list[Any] = []
            content_changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = str(block.get("id") or "")
                    if not tool_id or tool_id not in retained:
                        converted_tool_uses += 1
                        content_changed = True
                        continue
                next_content.append(block)
            if next_content:
                normalized_messages.append(_with_content(message, next_content) if content_changed else message)
            changed = changed or content_changed
            retained_tool_ids_for_next_user = retained
            continue

        if role == "user" and isinstance(content, list):
            next_content = []
            content_changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_id = str(block.get("tool_use_id") or "")
                    if tool_id and tool_id in retained_tool_ids_for_next_user:
                        next_content.append(block)
                    else:
                        converted_tool_results += 1
                        content_changed = True
                    continue
                next_content.append(block)
            if next_content:
                normalized_messages.append(_with_content(message, next_content) if content_changed else message)
            changed = changed or content_changed
            retained_tool_ids_for_next_user = set()
            continue

        normalized_messages.append(message)
        retained_tool_ids_for_next_user = set()

    if not changed:
        return body
    out = dict(body)
    out["messages"] = normalized_messages
    services.log(
        "WARN",
        "discarded unmatched historical Anthropic tool blocks for provider=%s tool_uses=%d tool_results=%d"
        % (provider, converted_tool_uses, converted_tool_results),
    )
    return out


def _content_blocks(message: dict[str, Any]) -> list[Any]:
    content = message.get("content")
    return content if isinstance(content, list) else []


def _tool_use_ids(message: dict[str, Any]) -> list[str]:
    return [
        tool_id
        for block in _content_blocks(message)
        if isinstance(block, dict) and block.get("type") == "tool_use"
        if (tool_id := str(block.get("id") or ""))
    ]


def _tool_result_ids(message: dict[str, Any]) -> list[str]:
    return [
        tool_id
        for block in _content_blocks(message)
        if isinstance(block, dict) and block.get("type") == "tool_result"
        if (tool_id := str(block.get("tool_use_id") or ""))
    ]


def _with_content(message: dict[str, Any], content: list[Any]) -> dict[str, Any]:
    updated = dict(message)
    updated["content"] = content
    return updated
