"""Collect an Ollama chat stream into one response, cutting loops short.

The Codex-facing collection path asks providers for a single complete message.
Asking Ollama for it with ``stream: false`` means a repetition loop is generated
in full before the router ever sees a character of it -- the reported case ran
for four minutes and produced 142,635 characters of the same 37-character block.
Nothing downstream can undo that; the time and the tokens are already spent.

Reading the same request as a stream costs nothing extra and makes the loop
observable while it is still being written, so the guard can close the
connection after a couple of thousand characters. The assembled result is the
same envelope ``decode_ollama_chat_response`` already expects, so every stage
after collection is unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .runaway_output_guard import (
    RunawayOutputDetector,
    RunawayOutputPolicy,
    RunawayVerdict,
)


@dataclass(frozen=True, slots=True)
class OllamaStreamCollection:
    response: dict[str, Any]
    verdict: RunawayVerdict | None = None
    chunks: int = 0


def collect_ollama_chat_stream(
    lines: Iterable[Any],
    policy: RunawayOutputPolicy | None = None,
    *,
    strict: bool = False,
) -> OllamaStreamCollection:
    """Merge Ollama NDJSON chunks into one response envelope.

    Returns as soon as the guard reports a loop, leaving the rest of the stream
    unread so the caller can close the connection and stop generation.
    """

    text_runaway = RunawayOutputDetector(policy)
    thinking_runaway = RunawayOutputDetector(policy)
    verdict: RunawayVerdict | None = None
    content: list[str] = []
    thinking: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    response: dict[str, Any] = {}
    chunks = 0
    terminal_received = False
    message_received = False
    for raw in lines:
        if isinstance(raw, bytes):
            try:
                line = raw.decode(
                    "utf-8", errors="strict" if strict else "ignore"
                ).strip()
            except UnicodeDecodeError as exc:
                raise RuntimeError(
                    "upstream Ollama stream contained invalid UTF-8"
                ) from exc
        else:
            line = str(raw).strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except ValueError as exc:
            if strict:
                raise RuntimeError(
                    "upstream Ollama stream contained malformed JSON"
                ) from exc
            continue
        if not isinstance(chunk, dict):
            if strict:
                raise RuntimeError(
                    "upstream Ollama stream data must contain a JSON object"
                )
            continue
        chunks += 1
        if chunk.get("done") is True:
            terminal_received = True
        for key in ("model", "created_at", "done", "done_reason"):
            if chunk.get(key) is not None:
                response[key] = chunk[key]
        for key in ("prompt_eval_count", "eval_count", "total_duration"):
            try:
                value = int(chunk.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value:
                response[key] = max(int(response.get(key) or 0), value)
        message = chunk.get("message")
        if not isinstance(message, dict):
            continue
        message_received = True
        text_chunk = str(message.get("content") or "")
        thinking_chunk = str(message.get("thinking") or "")
        if text_chunk:
            content.append(text_chunk)
            verdict = verdict or text_runaway.feed(text_chunk)
        if thinking_chunk:
            thinking.append(thinking_chunk)
            verdict = verdict or thinking_runaway.feed(thinking_chunk)
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                if strict:
                    function = (
                        call.get("function")
                        if isinstance(call.get("function"), dict)
                        else {}
                    )
                    name = str(function.get("name") or "").strip()
                    arguments = function.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError as exc:
                            raise RuntimeError(
                                "upstream Ollama tool call contained malformed arguments"
                            ) from exc
                    if not name or not isinstance(arguments, dict):
                        raise RuntimeError(
                            "upstream Ollama tool call requires a function name and "
                            "object arguments"
                        )
                tool_calls.append(call)
        if verdict is not None:
            break
        if terminal_received:
            break
    if strict and verdict is None and not terminal_received:
        raise RuntimeError(
            "upstream Ollama stream ended before done=true"
        )
    if strict and verdict is None and not message_received:
        raise RuntimeError("upstream Ollama stream contained no message")
    collected: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if thinking:
        collected["thinking"] = "".join(thinking)
    if tool_calls:
        collected["tool_calls"] = tool_calls
    response["message"] = collected
    response.setdefault("done", True)
    return OllamaStreamCollection(response=response, verdict=verdict, chunks=chunks)


__all__ = [
    "OllamaStreamCollection",
    "collect_ollama_chat_stream",
]
