"""Pure Anthropic-to-Ollama chat wire projections."""

from __future__ import annotations

from typing import Any


def _anthropic_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict) and block.get("type") == "tool_result":
            tool_text = _anthropic_content_to_text(block.get("content", ""))
            parts.append(f"Tool result for {block.get('tool_use_id', 'tool')}:\n{tool_text}")
    return "\n".join(part for part in parts if part)


def anthropic_system_to_ollama_messages(system: Any) -> list[dict[str, Any]]:
    if not system:
        return []
    text = system if isinstance(system, str) else _anthropic_content_to_text(system)
    return [{"role": "system", "content": text}] if text else []


def ollama_claude_code_reminder() -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Claude Code execution reminder: when the user asks to create, edit, or run code, "
            "use the available tools such as Write, Edit, Read, and Bash. Do not stop after saying "
            "you will run something. Do not present a code block as a substitute for creating the file "
            "unless the user explicitly asks for code only. Exception: while Claude Code is in Plan Mode, "
            "do not implement normal project files; explore as needed, write or update the plan file, "
            "then call ExitPlanMode when the plan is ready for approval."
        ),
    }


def anthropic_tools_to_ollama(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    ]


__all__ = [
    "anthropic_system_to_ollama_messages",
    "anthropic_tools_to_ollama",
    "ollama_claude_code_reminder",
]
