"""Pure projections for Anthropic tool request metadata."""

from __future__ import annotations

import time
import re
from dataclasses import dataclass
from typing import Any, Callable


ULTRACODE_STATE_RE = re.compile(
    r"\bUltracode\s+is\s+(?:still\s+)?(on|off)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class UltracodeSessionPolicy:
    content_to_text: Callable[[Any], str]

    def runtime_enabled(self, body: dict[str, Any]) -> bool:
        enabled = False
        sources = [body.get("system")]
        sources.extend(
            message.get("content")
            for message in body.get("messages") or []
            if isinstance(message, dict)
        )
        for source in sources:
            for match in ULTRACODE_STATE_RE.finditer(
                self.content_to_text(source)
            ):
                enabled = match.group(1).lower() == "on"
        return enabled

    def workflow_preferred(self, body: dict[str, Any]) -> bool:
        return self.runtime_enabled(body) and has_tool(body, "Workflow")


def forced_tool_choice_name(body: dict[str, Any]) -> str | None:
    value = body.get("tool_choice")
    choice = value if isinstance(value, dict) else None
    if not choice or choice.get("type") != "tool":
        return None
    name = choice.get("name")
    return name if isinstance(name, str) and name else None


def tool_names_in_body(body: dict[str, Any]) -> set[str]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return set()
    return {
        tool["name"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("name"), str)
    }


def has_tool(body: dict[str, Any], name: str) -> bool:
    return name in tool_names_in_body(body)


def synthetic_tool_use_response(
    model: str,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": f"msg_ciel_runtime_tool_{now}",
        "type": "message",
        "role": "assistant",
        "model": model or "ciel-runtime-router",
        "content": [
            {
                "type": "tool_use",
                "id": f"toolu_ciel_runtime_{tool_name}_{now}",
                "name": tool_name,
                "input": tool_input or {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


__all__ = [
    "UltracodeSessionPolicy",
    "forced_tool_choice_name",
    "has_tool",
    "synthetic_tool_use_response",
    "tool_names_in_body",
]
