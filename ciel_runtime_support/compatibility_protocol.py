"""Protocol codec for provider compatibility probes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
import urllib.error


@dataclass(frozen=True, slots=True)
class CompatibilityProtocolPorts:
    max_tokens_for_model: Callable[[str], int]
    first_header: Callable[[Any, list[str]], str | None]
    parse_retry_after: Callable[[str], float | None]
    format_duration: Callable[[float], str]


class CompatibilityProtocolCodec:
    def __init__(self, tool_name: str, ports: CompatibilityProtocolPorts) -> None:
        self.tool_name = tool_name
        self.ports = ports

    def tool_schema(self) -> dict[str, Any]:
        return {
            "name": self.tool_name,
            "description": "A minimal compatibility test tool. It echoes one required text argument.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        }

    def text_request(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "max_tokens": self.ports.max_tokens_for_model(model),
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": "Compatibility text test. Reply with exactly OK and do not call tools.",
                }
            ],
        }

    def tool_request(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "max_tokens": 128,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": "Compatibility tool test. Use the compat_echo tool exactly once with text set to ping.",
                }
            ],
            "tools": [self.tool_schema()],
            "tool_choice": {"type": "tool", "name": self.tool_name},
        }

    def tool_result_request(
        self,
        model: str,
        tool_use: dict[str, Any],
    ) -> dict[str, Any]:
        tool_id = str(tool_use.get("id") or "toolu_compat_echo_1")
        tool_input = (
            tool_use.get("input")
            if isinstance(tool_use.get("input"), dict)
            else {"text": "ping"}
        )
        return {
            "model": model,
            "max_tokens": 64,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": "Compatibility tool test. Use the compat_echo tool exactly once with text set to ping.",
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": self.tool_name,
                            "input": tool_input,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": "pong",
                        },
                        {
                            "type": "text",
                            "text": "Now reply with FINAL_OK and do not call tools.",
                        },
                    ],
                },
            ],
            "tools": [self.tool_schema()],
        }

    @staticmethod
    def content_blocks(data: Any) -> list[dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        content = data.get("content")
        if not isinstance(content, list):
            return []
        return [block for block in content if isinstance(block, dict)]

    def content_types(self, data: Any) -> list[str]:
        return [str(block.get("type", "?")) for block in self.content_blocks(data)]

    def text_preview(self, data: Any) -> str:
        parts = [
            block["text"].strip()
            for block in self.content_blocks(data)
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return " ".join(parts).strip()[:300]

    def find_tool_use(self, data: Any) -> tuple[dict[str, Any] | None, str]:
        for block in self.content_blocks(data):
            if block.get("type") != "tool_use":
                continue
            if block.get("name") != self.tool_name:
                return None, f"unexpected tool name {block.get('name')!r}"
            tool_input = block.get("input")
            if not isinstance(tool_input, dict):
                return None, "tool input was not a JSON object"
            if tool_input.get("text") != "ping":
                return None, (
                    f"tool input text was {tool_input.get('text')!r}, expected 'ping'"
                )
            if not block.get("id"):
                return None, "tool_use block did not include an id"
            return block, ""
        types = ", ".join(self.content_types(data)) or "none"
        preview = self.text_preview(data)
        suffix = f"; text={preview!r}" if preview else ""
        return None, (
            f"no {self.tool_name} tool_use block returned; "
            f"content blocks: {types}{suffix}"
        )

    def summarize_response(self, data: Any, label: str) -> list[str]:
        lines = [f"{label}: OK"]
        if not isinstance(data, dict):
            return lines
        stop = data.get("stop_reason")
        if stop:
            lines.append(f"Stop reason: {stop}")
        types = self.content_types(data)
        if types:
            lines.append("Content blocks: " + ", ".join(types[:6]))
        usage = data.get("usage")
        if isinstance(usage, dict):
            tokens = []
            if "input_tokens" in usage:
                tokens.append(f"in={usage['input_tokens']}")
            if "output_tokens" in usage:
                tokens.append(f"out={usage['output_tokens']}")
            if tokens:
                lines.append("Tokens: " + ", ".join(tokens))
        return lines

    def http_error_message(self, exc: urllib.error.HTTPError) -> str:
        raw = exc.read().decode("utf-8", errors="ignore")
        message = raw.strip()
        error_type = ""
        try:
            error = json.loads(raw)
            if isinstance(error, dict):
                if isinstance(error.get("error"), dict):
                    error_object = error["error"]
                    error_type = str(error_object.get("type") or "").strip()
                    message = str(
                        error_object.get("message")
                        or json.dumps(error_object, ensure_ascii=False)
                    )
                elif error.get("message"):
                    message = str(error["message"])
                    error_type = str(error.get("type") or "").strip()
        except Exception:
            pass
        if error_type and error_type not in message:
            message = f"{error_type}: {message}"
        retry_after = self.ports.first_header(
            exc.headers,
            ["Retry-After", "retry-after"],
        )
        if not retry_after:
            return message
        retry_text = retry_after.strip()
        retry_seconds = self.ports.parse_retry_after(retry_text)
        if retry_seconds is None:
            return f"{message} Retry-After: {retry_text}"
        display = self.ports.format_duration(retry_seconds)
        if not retry_text:
            return f"{message} Retry-After: {display}"
        raw_suffix = f"{retry_text}s" if re.fullmatch(r"\d+(?:\.\d+)?", retry_text) else retry_text
        return f"{message} Retry-After: {display} ({raw_suffix})"
