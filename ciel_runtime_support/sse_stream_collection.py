"""Collect SSE chat responses into one message, cutting loops short.

The Ollama collection path reads NDJSON; the other two protocols the
Codex-facing collector speaks are SSE. DeepSeek publishes both formats -- an
OpenAI-compatible endpoint at ``https://api.deepseek.com`` and an
Anthropic-compatible one at ``https://api.deepseek.com/anthropic`` -- and
ciel-runtime uses the Anthropic one, so a repetition loop on deepseek.com
arrives through :func:`collect_anthropic_message_stream`. Switching endpoints
would not have helped: both collectors used one blocking POST, which is what
let a loop run to completion before anything could look at it.

Each collector assembles exactly the payload the matching decoder already
expects, so nothing downstream changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .runaway_output_guard import (
    RunawayOutputDetector,
    RunawayOutputPolicy,
    RunawayVerdict,
)


@dataclass(frozen=True, slots=True)
class SseStreamCollection:
    response: dict[str, Any]
    verdict: RunawayVerdict | None = None
    chunks: int = 0


class UpstreamSseError(RuntimeError):
    """An error event delivered inside an otherwise successful SSE response."""

    def __init__(self, code: str, message: str, *, output_started: bool = False):
        self.code = str(code or "upstream_error")
        self.message = str(message or self.code)
        self.output_started = bool(output_started)
        super().__init__(self.message)


def iter_sse_payloads(lines: Iterable[Any]) -> Iterable[dict[str, Any]]:
    """Yield decoded ``data:`` payloads, ignoring framing and keepalives."""

    for raw in lines:
        line = (
            raw.decode("utf-8", errors="ignore")
            if isinstance(raw, (bytes, bytearray))
            else str(raw)
        ).strip()
        if not line or not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            payload = json.loads(body)
        except ValueError:
            continue
        if isinstance(payload, dict):
            yield payload


@dataclass
class _ToolFragment:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def collect_openai_chat_stream(
    lines: Iterable[Any], policy: RunawayOutputPolicy | None = None
) -> SseStreamCollection:
    """Merge OpenAI chat-completion chunks into one non-streaming response."""

    text_runaway = RunawayOutputDetector(policy)
    reasoning_runaway = RunawayOutputDetector(policy)
    verdict: RunawayVerdict | None = None
    content: list[str] = []
    reasoning: list[str] = []
    fragments: dict[int, _ToolFragment] = {}
    finish_reason = ""
    usage: dict[str, Any] = {}
    envelope: dict[str, Any] = {}
    chunks = 0
    for payload in iter_sse_payloads(lines):
        chunks += 1
        error = payload.get("error")
        if error is not None or str(payload.get("type") or "").lower() == "error":
            error = error if error is not None else payload
            if isinstance(error, dict):
                code = str(error.get("code") or error.get("type") or "upstream_error")
                message = str(error.get("message") or error.get("detail") or code)
            else:
                code = str(payload.get("code") or payload.get("type") or "upstream_error")
                message = str(error or payload.get("message") or code)
            raise UpstreamSseError(
                code,
                message,
                output_started=bool(content or reasoning or fragments),
            )
        for key in ("id", "model", "created", "system_fingerprint"):
            if payload.get(key) is not None:
                envelope[key] = payload[key]
        if isinstance(payload.get("usage"), dict):
            usage = payload["usage"]
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        if choice.get("finish_reason"):
            finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        reasoning_chunk = str(delta.get("reasoning_content") or "")
        if reasoning_chunk:
            reasoning.append(reasoning_chunk)
            verdict = verdict or reasoning_runaway.feed(reasoning_chunk)
        text_chunk = str(delta.get("content") or "")
        if text_chunk:
            content.append(text_chunk)
            verdict = verdict or text_runaway.feed(text_chunk)
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            try:
                index = int(call.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            fragment = fragments.setdefault(index, _ToolFragment())
            if call.get("id"):
                fragment.call_id = str(call["id"])
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if function.get("name"):
                fragment.name = str(function["name"])
            if function.get("arguments"):
                fragment.arguments += str(function["arguments"])
        if verdict is not None:
            break
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if fragments:
        message["tool_calls"] = [
            {
                "id": fragment.call_id or f"call_{index + 1}",
                "type": "function",
                "function": {"name": fragment.name, "arguments": fragment.arguments},
            }
            for index, fragment in sorted(fragments.items())
        ]
    response = {
        **envelope,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason or "stop"}],
    }
    if usage:
        response["usage"] = usage
    return SseStreamCollection(response=response, verdict=verdict, chunks=chunks)


@dataclass
class _ContentBlock:
    block: dict[str, Any] = field(default_factory=dict)
    text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    signature: str = ""
    partial_json: str = ""

    def finish(self) -> dict[str, Any]:
        block = dict(self.block)
        kind = str(block.get("type") or "")
        if kind == "text":
            block["text"] = str(block.get("text") or "") + "".join(self.text)
        elif kind in ("thinking", "redacted_thinking"):
            block["thinking"] = str(block.get("thinking") or "") + "".join(self.thinking)
            if self.signature:
                block["signature"] = self.signature
        elif kind == "tool_use" and self.partial_json:
            try:
                parsed = json.loads(self.partial_json)
            except ValueError:
                parsed = None
            block["input"] = parsed if isinstance(parsed, dict) else block.get("input") or {}
        return block


def collect_anthropic_message_stream(
    lines: Iterable[Any], policy: RunawayOutputPolicy | None = None
) -> SseStreamCollection:
    """Merge Anthropic Messages SSE events into one non-streaming message."""

    text_runaway = RunawayOutputDetector(policy)
    thinking_runaway = RunawayOutputDetector(policy)
    verdict: RunawayVerdict | None = None
    message: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "content": [],
        "stop_reason": None,
    }
    blocks: dict[int, _ContentBlock] = {}
    chunks = 0
    for payload in iter_sse_payloads(lines):
        chunks += 1
        event_type = str(payload.get("type") or "")
        if event_type == "message_start":
            started = payload.get("message")
            if isinstance(started, dict):
                message.update({key: value for key, value in started.items() if key != "content"})
            continue
        if event_type == "content_block_start":
            index = payload.get("index")
            block = payload.get("content_block")
            if isinstance(index, int) and isinstance(block, dict):
                blocks[index] = _ContentBlock(block=dict(block))
            continue
        if event_type == "content_block_delta":
            index = payload.get("index")
            delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
            if not isinstance(index, int):
                continue
            state = blocks.setdefault(index, _ContentBlock(block={"type": "text", "text": ""}))
            delta_type = str(delta.get("type") or "")
            if delta_type == "text_delta":
                chunk = str(delta.get("text") or "")
                state.text.append(chunk)
                verdict = verdict or text_runaway.feed(chunk)
            elif delta_type == "thinking_delta":
                chunk = str(delta.get("thinking") or "")
                state.thinking.append(chunk)
                verdict = verdict or thinking_runaway.feed(chunk)
            elif delta_type == "signature_delta":
                state.signature += str(delta.get("signature") or "")
            elif delta_type == "input_json_delta":
                state.partial_json += str(delta.get("partial_json") or "")
            if verdict is not None:
                break
            continue
        if event_type == "message_delta":
            delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
            for key in ("stop_reason", "stop_sequence"):
                if delta.get(key) is not None:
                    message[key] = delta[key]
            usage = payload.get("usage")
            if isinstance(usage, dict):
                message["usage"] = {**(message.get("usage") or {}), **usage}
            continue
    message["content"] = [state.finish() for _index, state in sorted(blocks.items())]
    if verdict is not None and not message.get("stop_reason"):
        message["stop_reason"] = "max_tokens"
    return SseStreamCollection(response=message, verdict=verdict, chunks=chunks)


__all__ = [
    "SseStreamCollection",
    "UpstreamSseError",
    "collect_anthropic_message_stream",
    "collect_openai_chat_stream",
    "iter_sse_payloads",
]
