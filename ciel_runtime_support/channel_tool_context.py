"""Channel-injected tool context storage and follow-up projection."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelToolContextPolicy:
    max_inject: int = 8
    prompt_limit: int = 4000


@dataclass(frozen=True, slots=True)
class ChannelToolContextPorts:
    content_to_text: Callable[[Any], str]
    truncate: Callable[[str, int], str]
    now: Callable[[], float]
    log: Callable[[str, str], None]


class ChannelToolContextRepository:
    """Thread-safe bounded repository keyed by upstream tool-use id."""

    def __init__(
        self,
        contexts: dict[str, dict[str, Any]] | None = None,
        lock: threading.Lock | None = None,
        limit: int = 200,
    ) -> None:
        self.contexts = contexts if contexts is not None else {}
        self._lock = lock or threading.Lock()
        self._limit = max(1, limit)

    def store(self, tool_use_id: str, context: dict[str, Any]) -> None:
        with self._lock:
            self.contexts[tool_use_id] = context
            overflow = len(self.contexts) - self._limit
            if overflow <= 0:
                return
            oldest = sorted(
                self.contexts.items(),
                key=lambda item: item[1].get("created_at", 0),
            )[:overflow]
            for old_id, _context in oldest:
                self.contexts.pop(old_id, None)

    def take_for_body(self, body: dict[str, Any], limit: int) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            for message in body.get("messages") or []:
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tool_use_id = str(block.get("tool_use_id") or "")
                    context = self.contexts.pop(tool_use_id, None)
                    if context:
                        found.append((tool_use_id, dict(context)))
                        if len(found) >= limit:
                            return found
        return found


@dataclass(frozen=True, slots=True)
class ChannelToolContextService:
    repository: ChannelToolContextRepository
    policy: ChannelToolContextPolicy
    ports: ChannelToolContextPorts

    def prompt_text(self, body: dict[str, Any]) -> str:
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        for message in reversed(body.get("messages") or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = self.ports.content_to_text(message.get("content", ""))
            is_channel_prompt = metadata.get("ciel_runtime_channel_injected") or (
                "[external channel input]" in text or "[ciel-runtime channel inbox]" in text
            )
            if is_channel_prompt and text:
                return self.ports.truncate(text, self.policy.prompt_limit)
        return ""

    def remember(self, source_body: dict[str, Any] | None, tool_use_id: str, tool_name: str, tool_input: Any) -> None:
        if not isinstance(source_body, dict) or not tool_use_id:
            return
        metadata = source_body.get("metadata") if isinstance(source_body.get("metadata"), dict) else {}
        if not metadata.get("ciel_runtime_channel_injected"):
            return
        context = {
            "created_at": self.ports.now(),
            "channel_message_ids": str(metadata.get("ciel_runtime_channel_message_ids") or ""),
            "prompt": self.prompt_text(source_body),
            "tool_name": tool_name,
            "tool_input": self._json_value(tool_input),
        }
        self.repository.store(tool_use_id, context)
        self.ports.log(
            "INFO",
            f"channel_llm_tool_context_stored tool_use_id={tool_use_id} tool={tool_name} "
            f"message_ids={context['channel_message_ids']}",
        )

    def remember_message(self, source_body: dict[str, Any] | None, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                self.remember(
                    source_body,
                    str(block.get("id") or ""),
                    str(block.get("name") or "tool"),
                    block.get("input"),
                )

    def inject_followup(self, body: dict[str, Any]) -> dict[str, Any]:
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        if metadata.get("ciel_runtime_channel_tool_result_followup"):
            return body
        contexts = self.repository.take_for_body(body, self.policy.max_inject)
        if not contexts:
            return body
        parts = [
            "[ciel-runtime channel tool_result follow-up]",
            "tool_result data from a previous channel-injected tool call.",
        ]
        for tool_use_id, context in contexts:
            parts.append(self._context_text(tool_use_id, context))
        projected = dict(body)
        messages = [message for message in body.get("messages", []) if isinstance(message, dict)]
        messages.append({"role": "user", "content": [{"type": "text", "text": "\n\n".join(parts)}]})
        projected["messages"] = messages
        projected["metadata"] = {**metadata, "ciel_runtime_channel_tool_result_followup": True}
        self.ports.log(
            "INFO",
            "channel_llm_tool_result_context_injected tool_use_ids="
            + ",".join(tool_use_id for tool_use_id, _context in contexts),
        )
        return projected

    @staticmethod
    def _json_value(value: Any) -> Any:
        return value if isinstance(value, (dict, list, str, int, float, bool)) or value is None else str(value)

    @staticmethod
    def _context_text(tool_use_id: str, context: dict[str, Any]) -> str:
        return "\n".join(
            [
                f"tool_use_id={tool_use_id}",
                f"tool={context.get('tool_name') or 'tool'}",
                f"channel_message_ids={context.get('channel_message_ids') or ''}",
                f"tool_input={json.dumps(context.get('tool_input'), ensure_ascii=False)}",
                f"original_channel_prompt:\n{context.get('prompt') or '(not captured)'}",
            ]
        )


__all__ = [
    "ChannelToolContextPolicy",
    "ChannelToolContextPorts",
    "ChannelToolContextRepository",
    "ChannelToolContextService",
]
