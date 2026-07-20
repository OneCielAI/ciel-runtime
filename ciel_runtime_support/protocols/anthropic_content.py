"""Pure Anthropic content-block text projection."""

from __future__ import annotations

from typing import Any


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "tool_result":
            tool_text = content_to_text(block.get("content", ""))
            parts.append(
                "Tool result for %s:\n%s"
                % (block.get("tool_use_id", "tool"), tool_text)
            )
    return "\n".join(part for part in parts if part)
