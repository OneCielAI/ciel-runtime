"""Anthropic thinking and tool-choice compatibility policies.

The policy owns protocol-level message transformations. Provider discovery and
logging remain injected ports so this module does not depend on the runtime
facade or on concrete provider adapters.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


THINKING_BLOCK_TYPES: tuple[str, ...] = ("thinking", "redacted_thinking")


def message_content_blocks(message: dict[str, Any]) -> list[Any]:
    content = message.get("content")
    if isinstance(content, list):
        return content
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def thinking_requested(body: dict[str, Any]) -> bool:
    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "").strip().lower()
        return thinking_type not in ("", "disabled", "none", "off", "false")
    return bool(thinking)


def thinking_block_count(body: dict[str, Any]) -> int:
    return sum(
        1
        for message in body.get("messages") or []
        if isinstance(message, dict)
        for block in message_content_blocks(message)
        if isinstance(block, dict) and block.get("type") in THINKING_BLOCK_TYPES
    )


def tool_continuation_block_count(body: dict[str, Any]) -> int:
    count = 0
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        for block in message_content_blocks(message):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if (role == "assistant" and block_type == "tool_use") or (
                role == "user" and block_type == "tool_result"
            ):
                count += 1
    return count


def assistant_history_count(body: dict[str, Any]) -> int:
    return sum(
        1
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "assistant"
    )


def strip_thinking_blocks(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    changed = False
    projected: list[Any] = []
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            projected.append(message)
            continue
        filtered = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") in THINKING_BLOCK_TYPES
            )
        ]
        if len(filtered) == len(content):
            projected.append(message)
            continue
        projected.append({**message, "content": filtered})
        changed = True
    return {**body, "messages": projected} if changed else body


def has_synthetic_tool_use(body: dict[str, Any]) -> bool:
    return any(
        str(block.get("id") or "").startswith("toolu_ciel_runtime_")
        for message in body.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "assistant"
        for block in message_content_blocks(message)
        if isinstance(block, dict) and block.get("type") == "tool_use"
    )


def copy_thinking_blocks(blocks: Any) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        return []
    return [
        dict(block)
        for block in blocks
        if isinstance(block, dict) and block.get("type") in THINKING_BLOCK_TYPES
    ]


@dataclass(frozen=True, slots=True)
class ThinkingPolicyPorts:
    preserves_contract: Callable[[str, dict[str, Any]], bool]
    reasoning_passback_enabled: Callable[[str, dict[str, Any], dict[str, Any]], bool]
    suggestion_mode: Callable[[dict[str, Any]], bool]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ToolChoicePorts:
    normalize: Callable[[str, dict[str, Any], dict[str, Any], Any], Any]
    supports: Callable[[str, dict[str, Any], dict[str, Any]], bool]
    log: Callable[[str, str], None]


class SuppressedThinkingRepository:
    """Bounded in-memory repository for passback blocks hidden from clients."""

    def __init__(
        self,
        records: list[dict[str, Any]],
        capacity: Callable[[], int],
    ) -> None:
        self._records = records
        self._capacity = capacity

    def clear(self) -> None:
        self._records.clear()

    def remember(self, provider: str, model: str, blocks: list[Any]) -> int:
        copied = copy_thinking_blocks(blocks)
        if not copied:
            return 0
        self._records.append(
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "provider": provider,
                "model": model,
                "blocks": copied,
            }
        )
        capacity = max(1, self._capacity())
        del self._records[:-capacity]
        return len(copied)

    def recent_for(self, provider: str, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        matches = [record for record in self._records if record.get("provider") == provider]
        return matches[-limit:]

    def __len__(self) -> int:
        return len(self._records)


class AnthropicThinkingPolicy:
    def __init__(
        self,
        ports: ThinkingPolicyPorts,
        repository: SuppressedThinkingRepository,
    ) -> None:
        self._ports = ports
        self._repository = repository

    def normalize_request(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        requested = thinking_requested(body)
        block_count = thinking_block_count(body)
        if (not requested and block_count <= 0) or self._ports.preserves_contract(
            provider, config
        ):
            return body
        if self._ports.reasoning_passback_enabled(provider, config, body):
            if not requested:
                return body
            projected = dict(body)
            projected.pop("thinking", None)
            self._ports.log(
                "INFO",
                "removed top-level Anthropic thinking request but preserved thinking blocks "
                f"for OpenAI-chat reasoning passback provider={provider} "
                f"thinking_blocks={block_count}",
            )
            return projected
        projected = dict(strip_thinking_blocks(body))
        projected.pop("thinking", None)
        self._ports.log(
            "WARN",
            "removed Anthropic thinking request and thinking content blocks for "
            f"non-Anthropic provider provider={provider} "
            f"synthetic_tool={has_synthetic_tool_use(body)} "
            f"continuation_blocks={tool_continuation_block_count(body)} "
            f"assistant_history={assistant_history_count(body)} "
            f"thinking_blocks={block_count}",
        )
        return projected

    def normalize_response(
        self,
        provider: str,
        config: dict[str, Any],
        message: dict[str, Any],
        model: str | None = None,
    ) -> dict[str, Any]:
        if self._ports.preserves_contract(provider, config):
            return message
        content = message.get("content")
        if not isinstance(content, list):
            return message
        filtered = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") in THINKING_BLOCK_TYPES
            )
        ]
        if len(filtered) == len(content):
            return message
        self.remember(provider, str(model or message.get("model") or ""), content)
        self._ports.log(
            "WARN",
            "removed Anthropic thinking response blocks for non-Anthropic provider "
            f"provider={provider} thinking_blocks={len(content) - len(filtered)}",
        )
        return {**message, "content": filtered or [{"type": "text", "text": ""}]}

    def remember(self, provider: str, model: str, blocks: list[Any]) -> None:
        copied = self._repository.remember(provider, model, blocks)
        if copied:
            self._ports.log(
                "WARN",
                "stored suppressed Anthropic thinking passback blocks "
                f"provider={provider} model={model} blocks={copied} "
                f"cache={len(self._repository)}",
            )

    def rehydrate(
        self, provider: str, config: dict[str, Any], body: dict[str, Any]
    ) -> dict[str, Any]:
        if (
            self._ports.preserves_contract(provider, config)
            or self._ports.suggestion_mode(body)
            or not len(self._repository)
        ):
            return body
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body
        indices = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and not thinking_block_count({"messages": [message]})
            and (
                (isinstance(message.get("content"), list) and bool(message["content"]))
                or (isinstance(message.get("content"), str) and bool(message["content"]))
            )
        ]
        records = self._repository.recent_for(provider, len(indices))
        if not records:
            return body
        selected = indices[-len(records) :]
        projected = list(messages)
        inserted = 0
        for index, record in zip(selected, records):
            message = projected[index]
            blocks = copy_thinking_blocks(record.get("blocks"))
            if not isinstance(message, dict) or not blocks:
                continue
            content = message.get("content")
            if isinstance(content, list):
                new_content = blocks + list(content)
            elif isinstance(content, str):
                new_content = blocks + [{"type": "text", "text": content}]
            else:
                continue
            projected[index] = {**message, "content": new_content}
            inserted += len(blocks)
        if not inserted:
            return body
        self._ports.log(
            "WARN",
            "rehydrated suppressed Anthropic thinking passback blocks for upstream "
            f"provider={provider} blocks={inserted} "
            f"assistant_messages={len(selected)} cache={len(self._repository)}",
        )
        return {**body, "messages": projected}


def normalize_tool_choice(
    provider: str,
    config: dict[str, Any],
    body: dict[str, Any],
    ports: ToolChoicePorts,
) -> dict[str, Any]:
    tool_choice = body.get("tool_choice")
    if tool_choice is None:
        return body
    normalized = ports.normalize(provider, config, body, tool_choice)
    if normalized != tool_choice:
        ports.log(
            "WARN",
            f"normalized unsupported forced tool_choice for provider={provider}: "
            f"model={body.get('model')} tool_choice={tool_choice}",
        )
        return {**body, "tool_choice": normalized}
    if ports.supports(provider, config, body):
        return body
    projected = dict(body)
    removed = projected.pop("tool_choice", None)
    ports.log(
        "WARN",
        f"removed unsupported tool_choice for {provider}: "
        f"model={body.get('model')} tool_choice={removed}",
    )
    return projected
