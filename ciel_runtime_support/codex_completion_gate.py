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


_ADDITIONAL_TOOLS_ITEM_TYPE = "additional_tools"
_DEFAULT_TOOL_NAMESPACE = "functions"


def _additional_tools_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    items = body.get("input")
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("type") == _ADDITIONAL_TOOLS_ITEM_TYPE
        and item.get("tools")
    ]


def request_offers_tools(body: dict[str, Any]) -> bool:
    """Return whether the request carries a tool catalogue in either shape.

    Codex sends models whose catalogue entry has ``tool_mode:
    "code_mode_only"`` (the gpt-6 and gpt-5.6 families) their tools as an
    ``additional_tools`` input item and omits the top-level ``tools`` array
    (captured from codex-cli 0.150.0, 0.157.1 and 0.159.2, 2026-09-29).
    """

    return bool(body.get("tools")) or bool(_additional_tools_items(body))


def request_allows_completion_check(body: dict[str, Any]) -> bool:
    """Return whether the client let the model act in this request at all."""

    return request_offers_tools(body) and body.get("tool_choice") != "none"


def request_requires_completion_check(
    body: dict[str, Any], observation: ResponsesCompletionObservation
) -> bool:
    """Use response structure only; never classify natural-language wording.

    Any text-only final is ambiguous while tools are available: observed turns
    ended on a progress announcement after a tool result, straight after the
    user's message, and after Codex's mid-turn compaction, with and without a
    reasoning item, across unrelated providers and models. What came before
    the reply therefore decides nothing; the model confirms through the
    private tool or does the work.
    """

    return bool(
        request_allows_completion_check(body)
        and observation.parseable
        and observation.status == "completed"
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
    # A catalogue carried in `additional_tools` gets the private tool inside
    # that item, beside the tools the model already calls; replacing the
    # input below must then keep the item.
    catalogue = None if projected.get("tools") else next(
        iter(_additional_tools_items(projected)), None
    )
    if catalogue is not None:
        _add_to_default_namespace(catalogue, _completion_tool())
    kept = [catalogue] if catalogue is not None else []
    if projected.get("conversation"):
        projected["input"] = [*kept, prompt]
    elif bool(projected.get("store")) and observation.response_id:
        projected["previous_response_id"] = observation.response_id
        projected["input"] = [*kept, prompt]
    else:
        current = projected.get("input")
        items = list(current) if isinstance(current, list) else ([current] if current else [])
        projected["input"] = [*items, *copy.deepcopy(observation.output), prompt]
    projected["stream"] = True
    if catalogue is None:
        projected["tools"] = [*(projected.get("tools") or []), _completion_tool()]
    projected["tool_choice"] = "required"
    return projected


def _completion_tool() -> dict[str, Any]:
    return {
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


def _add_to_default_namespace(catalogue: dict[str, Any], tool: dict[str, Any]) -> None:
    """Place ``tool`` where Codex's own unqualified tools live.

    Calls to tools in the ``functions`` namespace come back without a
    ``namespace`` field (robert-ai rollout: ``wait`` 111 times), so the
    confirmation is recognised by name like on the top-level route.
    """

    entries = list(catalogue.get("tools") or [])
    for index, entry in enumerate(entries):
        if (
            isinstance(entry, dict)
            and entry.get("type") == "namespace"
            and entry.get("name") == _DEFAULT_TOOL_NAMESPACE
        ):
            entries[index] = {**entry, "tools": [*(entry.get("tools") or []), tool]}
            break
    else:
        entries.append(tool)
    catalogue["tools"] = entries


__all__ = [
    "ResponsesCompletionObservation",
    "completion_check_body",
    "request_allows_completion_check",
    "request_offers_tools",
    "request_requires_completion_check",
]
