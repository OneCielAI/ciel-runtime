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
from typing import Any, Callable, Iterable

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
    lines: Iterable[Any], policy: RunawayOutputPolicy | None = None
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
    for raw in lines:
        line = raw.decode("utf-8", errors="ignore").strip() if isinstance(raw, bytes) else str(raw).strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        chunks += 1
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
                tool_calls.append(call)
        if verdict is not None:
            break
    collected: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if thinking:
        collected["thinking"] = "".join(thinking)
    if tool_calls:
        collected["tool_calls"] = tool_calls
    response["message"] = collected
    response.setdefault("done", True)
    return OllamaStreamCollection(response=response, verdict=verdict, chunks=chunks)


@dataclass(frozen=True, slots=True)
class OllamaStreamCollectPorts:
    open_stream: Callable[..., Any]
    log: Callable[..., None] = lambda _level, _message: None
    policy: Callable[[], RunawayOutputPolicy] = RunawayOutputPolicy


@dataclass(frozen=True, slots=True)
class OllamaStreamCollector:
    """Streaming replacement for the collection path's single POST."""

    ports: OllamaStreamCollectPorts

    def __call__(
        self,
        url: str,
        req_body: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
        provider: str,
        pcfg: dict[str, Any],
        model: str,
        *,
        retry_rate_limits: bool = True,
    ) -> dict[str, Any]:
        resp = self.ports.open_stream(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            None,
            retry_rate_limits=retry_rate_limits,
        )
        try:
            collection = collect_ollama_chat_stream(resp, self.ports.policy())
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if collection.verdict is not None:
            self.ports.log(
                "WARN",
                f"ollama_collect_runaway_repetition provider={provider} model={model} "
                f"chunks={collection.chunks} {collection.verdict.log_fields()}",
            )
        return collection.response


__all__ = [
    "OllamaStreamCollectPorts",
    "OllamaStreamCollection",
    "OllamaStreamCollector",
    "collect_ollama_chat_stream",
]
