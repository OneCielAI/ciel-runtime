"""Language-independent completion validation for Responses agent turns."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

from .codex_turn_recovery import (
    CODEX_COMPLETION_TOOL_NAME,
    CODEX_STRICT_CONTINUATION_NUDGE,
)


_MAX_SSE_EVENT_BYTES = 8 * 1024 * 1024


def _event_payload(block: bytes) -> dict[str, Any] | None:
    data = b"\n".join(
        line[5:].lstrip()
        for line in block.splitlines()
        if line.startswith(b"data:")
    )
    if not data or data == b"[DONE]":
        return None
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _message_text(item: dict[str, Any]) -> str:
    if item.get("type") != "message":
        return ""
    parts: list[str] = []
    for part in item.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") not in {"output_text", "text", "refusal"}:
            continue
        value = part.get("text") or part.get("refusal")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


@dataclass(slots=True)
class ResponsesCompletionObservation:
    """Incrementally retain only terminal Responses protocol state."""

    response_id: str = ""
    status: str = ""
    output: list[dict[str, Any]] = field(default_factory=list)
    parseable: bool = True
    _pending: bytearray = field(default_factory=bytearray, repr=False)
    _items: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False)

    def feed(self, chunk: bytes) -> None:
        if not self.parseable or not chunk:
            return
        self._pending.extend(chunk)
        if (
            len(self._pending) > _MAX_SSE_EVENT_BYTES
            and b"\n\n" not in self._pending
            and b"\r\n\r\n" not in self._pending
        ):
            self.parseable = False
            self._pending.clear()
            return
        while True:
            candidates = [
                (index, len(separator))
                for separator in (b"\n\n", b"\r\n\r\n")
                if (index := self._pending.find(separator)) >= 0
            ]
            marker, marker_size = min(candidates, default=(-1, 0))
            if marker < 0:
                return
            block = bytes(self._pending[:marker])
            del self._pending[: marker + marker_size]
            self._accept(_event_payload(block))

    def finish(self) -> None:
        if self.parseable and self._pending:
            self._accept(_event_payload(bytes(self._pending)))
        self._pending.clear()
        if not self.output and self._items:
            self.output = [self._items[index] for index in sorted(self._items)]

    def _accept(self, event: dict[str, Any] | None) -> None:
        if not event:
            return
        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            index = event.get("output_index")
            if isinstance(item, dict) and isinstance(index, int):
                self._items[index] = item
            return
        if event_type not in {"response.completed", "response.incomplete", "response.failed"}:
            return
        response = event.get("response")
        if not isinstance(response, dict):
            return
        self.response_id = str(response.get("id") or "")
        self.status = str(response.get("status") or "")
        output = response.get("output")
        if isinstance(output, list):
            self.output = [item for item in output if isinstance(item, dict)]

    @property
    def has_reasoning(self) -> bool:
        return any(item.get("type") == "reasoning" for item in self.output)

    @property
    def has_action(self) -> bool:
        # Responses output items other than messages/reasoning are protocol-level
        # actions or state. Treat unknown future item types conservatively as work.
        return any(item.get("type") not in {"message", "reasoning"} for item in self.output)

    @property
    def visible_text(self) -> str:
        return "\n".join(filter(None, (_message_text(item) for item in self.output)))

    @property
    def completion_confirmed(self) -> bool:
        actions = [
            item for item in self.output if item.get("type") not in {"message", "reasoning"}
        ]
        return bool(actions) and all(
            item.get("name") == CODEX_COMPLETION_TOOL_NAME for item in actions
        )


def request_requires_completion_check(
    body: dict[str, Any], observation: ResponsesCompletionObservation
) -> bool:
    """Use response structure only; never classify natural-language wording."""

    return bool(
        body.get("tools")
        and observation.parseable
        and observation.status == "completed"
        and observation.has_reasoning
        and not observation.has_action
        and observation.visible_text.strip()
        and observation.output
    )


def completion_check_body(
    body: dict[str, Any], observation: ResponsesCompletionObservation
) -> dict[str, Any]:
    """Continue from a candidate final response using the official state shapes."""

    projected = copy.deepcopy(body)
    prompt = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": CODEX_STRICT_CONTINUATION_NUDGE}],
    }
    if projected.get("conversation"):
        projected["input"] = [prompt]
    elif bool(projected.get("store")) and observation.response_id:
        projected["previous_response_id"] = observation.response_id
        projected["input"] = [prompt]
    else:
        current = projected.get("input")
        items = list(current) if isinstance(current, list) else ([current] if current else [])
        projected["input"] = [*items, *copy.deepcopy(observation.output), prompt]
    projected["stream"] = True
    tools = list(projected.get("tools") or [])
    tools.append(
        {
            "type": "function",
            "name": CODEX_COMPLETION_TOOL_NAME,
            "description": "Confirm that every action requested by the user is complete.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "strict": True,
        }
    )
    projected["tools"] = tools
    projected["tool_choice"] = "required"
    return projected


__all__ = [
    "ResponsesCompletionObservation",
    "completion_check_body",
    "request_requires_completion_check",
]
