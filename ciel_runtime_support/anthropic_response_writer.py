"""HTTP/SSE adapter for locally generated Anthropic message responses."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any


def anthropic_text_response(
    model: str, text: str, stop_reason: str = "end_turn"
) -> dict[str, Any]:
    return {
        "id": f"msg_ollama_advisor_{int(time.time() * 1000)}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": max(1, len(text) // 4)},
    }


def prepend_anthropic_text(
    message: dict[str, Any], text: str
) -> dict[str, Any]:
    if not text:
        return message
    projected = dict(message)
    content = projected.get("content")
    blocks = list(content) if isinstance(content, list) else []
    blocks.insert(0, {"type": "text", "text": text})
    projected["content"] = blocks
    return projected


class AnthropicResponseWriter:
    def __init__(self, write_json: Callable[[Any, Any], None]) -> None:
        self.write_json = write_json

    @staticmethod
    def _event(handler: Any, name: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        handler.wfile.write(f"event: {name}\ndata: {data}\n\n".encode())

    @staticmethod
    def _start_headers(handler: Any) -> None:
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()

    def text(self, handler: Any, model: str, text: str, stream: bool) -> None:
        message = anthropic_text_response(model, text)
        if not stream:
            self.write_json(handler, message)
            return
        self._start_headers(handler)
        started = {
            **message,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        self._event(
            handler,
            "message_start",
            {"type": "message_start", "message": started},
        )
        self.blocks(handler, message["content"], flush=False)
        self.stop(handler, message, flush=False)
        handler.wfile.flush()

    def message(
        self, handler: Any, message: dict[str, Any], stream: bool
    ) -> None:
        if not stream:
            self.write_json(handler, message)
            return
        self._start_headers(handler)
        self._event(
            handler,
            "message_start",
            {
                "type": "message_start",
                "message": {**message, "content": [], "stop_reason": None},
            },
        )
        self.blocks(handler, message.get("content") or [], flush=False)
        self.stop(handler, message, flush=False)
        handler.wfile.flush()

    def block(self, handler: Any, index: int, block: dict[str, Any]) -> None:
        block_type = block.get("type")
        if block_type == "text":
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            self._event(
                handler,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "text_delta",
                        "text": block.get("text", ""),
                    },
                },
            )
        elif block_type == "thinking":
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
            )
            thinking = str(block.get("thinking") or "")
            if thinking:
                self._event(
                    handler,
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "thinking_delta", "thinking": thinking},
                    },
                )
            signature = str(block.get("signature") or "")
            if signature:
                self._event(
                    handler,
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "signature_delta", "signature": signature},
                    },
                )
        elif block_type == "redacted_thinking":
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": block,
                },
            )
        elif block_type == "tool_use":
            tool_input = block.get("input") or {}
            self._event(
                handler,
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {**block, "input": {}},
                },
            )
            self._event(
                handler,
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(tool_input, ensure_ascii=False),
                    },
                },
            )
        else:
            return
        self._event(
            handler,
            "content_block_stop",
            {"type": "content_block_stop", "index": index},
        )

    def start(self, handler: Any, model: str, input_tokens: int = 0) -> None:
        self._start_headers(handler)
        self._event(
            handler,
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_ciel_runtime_{int(time.time() * 1000)}",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": max(0, int(input_tokens or 0)),
                        "output_tokens": 0,
                    },
                },
            },
        )
        handler.wfile.flush()

    def blocks(
        self,
        handler: Any,
        blocks: list[dict[str, Any]],
        start_index: int = 0,
        *,
        flush: bool = True,
    ) -> int:
        index = start_index
        for block in blocks:
            self.block(handler, index, block)
            index += 1
        if flush:
            handler.wfile.flush()
        return index

    def stop(
        self,
        handler: Any,
        message: dict[str, Any] | None = None,
        *,
        flush: bool = True,
    ) -> None:
        message = message or {}
        self._event(
            handler,
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": message.get("stop_reason") or "end_turn",
                    "stop_sequence": None,
                },
                "usage": message.get("usage") or {"output_tokens": 1},
            },
        )
        self._event(handler, "message_stop", {"type": "message_stop"})
        if flush:
            handler.wfile.flush()
