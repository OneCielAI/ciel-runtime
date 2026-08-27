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


def _parse_sse_data(
    parts: list[str], *, strict: bool
) -> tuple[str, dict[str, Any] | None] | None:
    body = "\n".join(parts)
    if not body:
        return None
    if body.strip() == "[DONE]":
        return "done", None
    try:
        payload = json.loads(body)
    except ValueError as exc:
        if strict:
            raise UpstreamSseError(
                "invalid_stream",
                "upstream SSE contained malformed JSON",
            ) from exc
        return None
    if isinstance(payload, dict):
        return "payload", payload
    if strict:
        raise UpstreamSseError(
            "invalid_stream",
            "upstream SSE data must contain a JSON object",
        )
    return None


def _iter_sse_records(
    lines: Iterable[Any], *, strict: bool
) -> Iterable[tuple[str, dict[str, Any] | None]]:
    pending: list[str] = []
    for raw in lines:
        if isinstance(raw, (bytes, bytearray)):
            try:
                decoded = raw.decode(
                    "utf-8", errors="strict" if strict else "ignore"
                )
            except UnicodeDecodeError as exc:
                raise UpstreamSseError(
                    "invalid_stream",
                    "upstream SSE contained invalid UTF-8",
                ) from exc
        else:
            decoded = str(raw)
        physical_lines = decoded.splitlines() or [""]
        for line in physical_lines:
            if not line:
                record = _parse_sse_data(pending, strict=strict)
                pending.clear()
                if record is not None:
                    yield record
                continue
            field, separator, value = line.partition(":")
            if field != "data":
                continue
            if separator and value.startswith(" "):
                value = value[1:]
            if pending:
                # A few compatible APIs omit the SSE blank separator. Preserve
                # that established single-line framing while still joining a
                # standards-compliant multi-line data event until its blank line.
                previous = _parse_sse_data(pending, strict=False)
                if previous is not None:
                    yield previous
                    pending.clear()
            pending.append(value)
    record = _parse_sse_data(pending, strict=strict)
    if record is not None:
        yield record


def iter_sse_payloads(lines: Iterable[Any]) -> Iterable[dict[str, Any]]:
    """Yield decoded ``data:`` payloads, ignoring framing and keepalives."""

    for kind, payload in _iter_sse_records(lines, strict=False):
        if kind == "payload" and payload is not None:
            yield payload


@dataclass
class _ToolFragment:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def collect_openai_chat_stream(
    lines: Iterable[Any],
    policy: RunawayOutputPolicy | None = None,
    *,
    strict: bool = False,
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
    terminal_received = False
    choice_received = False
    for kind, payload in _iter_sse_records(lines, strict=strict):
        if kind == "done":
            terminal_received = True
            break
        assert payload is not None
        chunks += 1
        error = payload.get("error")
        if error is None and str(payload.get("type") or "") == "response.failed":
            # The Responses wire reports a refusal as a terminal event whose
            # error sits one level down.  Reading only the top level turned it
            # into an empty successful turn.
            failed = payload.get("response")
            error = failed.get("error") if isinstance(failed, dict) else None
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
        if choice:
            choice_received = True
        if choice.get("finish_reason"):
            finish_reason = str(choice["finish_reason"])
            terminal_received = True
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
    if strict and verdict is None and not terminal_received:
        raise UpstreamSseError(
            "incomplete_stream",
            "upstream OpenAI Chat stream ended before a terminal event",
            output_started=bool(content or reasoning or fragments),
        )
    if strict and verdict is None and not choice_received:
        raise UpstreamSseError(
            "invalid_stream",
            "upstream OpenAI Chat stream contained no choice",
            output_started=bool(content or reasoning or fragments),
        )
    if strict and verdict is None:
        for index, fragment in fragments.items():
            if not fragment.call_id or not fragment.name:
                raise UpstreamSseError(
                    "invalid_stream",
                    f"upstream Chat tool call {index} requires id and function name",
                    output_started=True,
                )
            try:
                arguments = json.loads(fragment.arguments)
            except ValueError as exc:
                raise UpstreamSseError(
                    "invalid_stream",
                    f"upstream Chat tool call {index} contained malformed arguments",
                    output_started=True,
                ) from exc
            if not isinstance(arguments, dict):
                raise UpstreamSseError(
                    "invalid_stream",
                    f"upstream Chat tool call {index} arguments must be a JSON object",
                    output_started=True,
                )
        if bool(fragments) != (finish_reason == "tool_calls"):
            raise UpstreamSseError(
                "invalid_stream",
                "upstream Chat finish_reason is inconsistent with tool calls",
                output_started=bool(content or reasoning or fragments),
            )
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

    def finish(self, *, strict: bool = False) -> dict[str, Any]:
        block = dict(self.block)
        kind = str(block.get("type") or "")
        if kind == "text":
            block["text"] = str(block.get("text") or "") + "".join(self.text)
        elif kind in ("thinking", "redacted_thinking"):
            block["thinking"] = str(block.get("thinking") or "") + "".join(self.thinking)
            if self.signature:
                block["signature"] = self.signature
        elif kind == "tool_use":
            parsed = block.get("input")
            if self.partial_json:
                try:
                    parsed = json.loads(self.partial_json)
                except ValueError as exc:
                    if strict:
                        raise UpstreamSseError(
                            "invalid_stream",
                            "upstream Anthropic tool input contained malformed JSON",
                            output_started=True,
                        ) from exc
            if strict and (
                not str(block.get("id") or "").strip()
                or not str(block.get("name") or "").strip()
                or not isinstance(parsed, dict)
            ):
                raise UpstreamSseError(
                    "invalid_stream",
                    "upstream Anthropic tool call requires id, name, and object input",
                    output_started=True,
                )
            block["input"] = parsed if isinstance(parsed, dict) else block.get("input") or {}
        return block


def collect_anthropic_message_stream(
    lines: Iterable[Any],
    policy: RunawayOutputPolicy | None = None,
    *,
    strict: bool = False,
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
    terminal_received = False
    message_started = False
    for kind, payload in _iter_sse_records(lines, strict=strict):
        if kind == "done":
            continue
        assert payload is not None
        chunks += 1
        event_type = str(payload.get("type") or "")
        if event_type == "error":
            # Anthropic delivers an overload or a request rejection as an SSE
            # error event inside an HTTP 200 stream.  Ignoring it left the
            # collected message empty, so the CLI saw a turn that stopped for
            # no stated reason.
            error = payload.get("error")
            error = error if isinstance(error, dict) else {}
            raise UpstreamSseError(
                str(error.get("type") or error.get("code") or "upstream_error"),
                str(error.get("message") or error.get("detail") or "upstream stream error"),
                output_started=bool(blocks),
            )
        if event_type == "message_stop":
            terminal_received = True
            continue
        if event_type == "message_start":
            message_started = True
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
    if strict and verdict is None and not terminal_received:
        raise UpstreamSseError(
            "incomplete_stream",
            "upstream Anthropic stream ended before message_stop",
            output_started=bool(blocks),
        )
    if strict and verdict is None and (
        not message_started or not str(message.get("stop_reason") or "")
    ):
        raise UpstreamSseError(
            "invalid_stream",
            "upstream Anthropic stream requires message_start and stop_reason",
            output_started=bool(blocks),
        )
    finished_content = [
        state.finish(strict=strict) for _index, state in sorted(blocks.items())
    ]
    if strict and verdict is None:
        has_tool_use = any(
            block.get("type") == "tool_use"
            for block in finished_content
            if isinstance(block, dict)
        )
        if has_tool_use != (message.get("stop_reason") == "tool_use"):
            raise UpstreamSseError(
                "invalid_stream",
                "upstream Anthropic stop_reason is inconsistent with tool_use blocks",
                output_started=bool(blocks),
            )
    message["content"] = finished_content
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
