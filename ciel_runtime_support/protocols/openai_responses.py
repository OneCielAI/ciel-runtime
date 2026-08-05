"""Pure OpenAI Responses <-> Anthropic Messages conversions.

This module intentionally has no router, configuration, or network dependencies.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ..architecture import MessageProtocolAdapter


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content_type = str(content.get("type") or "")
        if content_type in ("input_text", "output_text", "text"):
            return str(content.get("text") or "")
        if content_type == "refusal":
            return str(content.get("refusal") or "")
        return str(content.get("text") or content.get("output") or "")
    if isinstance(content, list):
        parts = [_content_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    text = _content_text(content)
    return [{"type": "text", "text": text}] if text else []


def _reasoning_summary_text(item: dict[str, Any]) -> str:
    summary = item.get("summary")
    if not isinstance(summary, list):
        return ""
    return "\n".join(
        str(part.get("text") or "")
        for part in summary
        if isinstance(part, dict) and part.get("type") == "summary_text"
    ).strip()


def _tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return out
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        description = tool.get("description", "")
        parameters = tool.get("parameters")
        if not name and isinstance(tool.get("function"), dict):
            function = tool["function"]
            name = function.get("name")
            description = function.get("description", description)
            parameters = function.get("parameters", parameters)
        if tool.get("type") not in (None, "function") and not name:
            continue
        if not name:
            continue
        out.append(
            {
                "name": str(name),
                "description": str(description or ""),
                "input_schema": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            }
        )
    return out


def _tool_choice_to_anthropic(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        lowered = tool_choice.strip().lower()
        if lowered == "required":
            return {"type": "any"}
        if lowered in ("auto", "none"):
            return {"type": "auto"} if lowered == "auto" else None
        return tool_choice
    if not isinstance(tool_choice, dict):
        return tool_choice
    if tool_choice.get("type") == "function":
        name = tool_choice.get("name")
        if not name and isinstance(tool_choice.get("function"), dict):
            name = tool_choice["function"].get("name")
        if name:
            return {"type": "tool", "name": str(name)}
    return tool_choice


def openai_responses_to_anthropic_messages(body: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    system_parts: list[str] = []
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        system_parts.append(instructions)
    messages: list[dict[str, Any]] = []
    raw_input = body.get("input", [])
    if isinstance(raw_input, str):
        raw_input = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": raw_input}]}]
    if isinstance(raw_input, dict):
        raw_input = [raw_input]
    if not isinstance(raw_input, list):
        raw_input = []
    saw_conversation_item = False
    pending_reasoning = ""
    for item in raw_input:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "message")
        if item_type == "reasoning":
            pending_reasoning = _reasoning_summary_text(item)
            continue
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or f"call_{len(messages) + 1}")
            content: list[dict[str, Any]] = []
            if pending_reasoning:
                content.append({"type": "thinking", "thinking": pending_reasoning})
                pending_reasoning = ""
            content.append(
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": str(item.get("name") or "tool"),
                    "input": _json_object(item.get("arguments")),
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                }
            )
            saw_conversation_item = True
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or item.get("id") or "call_tool")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": _content_text(item.get("output")),
                        }
                    ],
                }
            )
            saw_conversation_item = True
            continue
        role = str(item.get("role") or "user").strip().lower()
        blocks = _content_blocks(item.get("content", item.get("text", "")))
        if not blocks:
            continue
        if role in ("system", "developer") and not saw_conversation_item:
            system_parts.append(_content_text(blocks))
            continue
        if role in ("system", "developer"):
            role = "user"
            blocks = [{"type": "text", "text": f"[Runtime system context]\n{_content_text(blocks)}"}]
        if role not in ("user", "assistant"):
            role = "user"
        messages.append({"role": role, "content": blocks})
        saw_conversation_item = True
    if not messages:
        messages.append({"role": "user", "content": [{"type": "text", "text": ""}]})
    out: dict[str, Any] = {
        "model": str(body.get("model") or fallback_model or "model"),
        "messages": messages,
        "stream": bool(body.get("stream", True)),
    }
    tools = _tools_to_anthropic(body.get("tools"))
    if tools:
        out["tools"] = tools
    tool_choice = _tool_choice_to_anthropic(body.get("tool_choice"))
    if tool_choice is not None:
        out["tool_choice"] = tool_choice
    max_tokens = _positive_int(body.get("max_output_tokens")) or _positive_int(body.get("max_tokens"))
    if max_tokens:
        out["max_tokens"] = max_tokens
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") is not None:
        effort = str(reasoning["effort"])
        out["thinking"] = {
            "type": "disabled" if effort.strip().lower() in {"none", "minimal"} else "enabled",
            "effort": effort,
        }
    if system_parts:
        out["system"] = [{"type": "text", "text": part} for part in system_parts if part]
    return out


def _usage_from_anthropic(message: dict[str, Any]) -> dict[str, Any]:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    uncached_input = _positive_int(usage.get("input_tokens")) or 0
    cached_input = _positive_int(usage.get("cache_read_input_tokens")) or 0
    cache_write = _positive_int(usage.get("cache_creation_input_tokens")) or 0
    input_tokens = uncached_input + cached_input + cache_write
    output_tokens = _positive_int(usage.get("output_tokens")) or 0
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {
            "cached_tokens": cached_input,
            "cache_write_tokens": cache_write,
        },
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def anthropic_message_to_openai_response(
    message: dict[str, Any], source_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    response_id = f"resp_{uuid.uuid4().hex}"
    created_at = int(time.time())
    model = str(message.get("model") or (source_body or {}).get("model") or "")
    output: list[dict[str, Any]] = []
    for index, block in enumerate(message.get("content") or []):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "")
            output.append(
                {
                    "id": f"msg_{response_id[5:13]}_{index}",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                }
            )
        elif block_type == "tool_use":
            call_id = str(block.get("id") or f"call_{index + 1}")
            output.append(
                {
                    "id": f"fc_{response_id[5:13]}_{index}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": str(block.get("name") or "tool"),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                }
            )
        elif block_type == "thinking":
            thinking = str(block.get("thinking") or "")
            if thinking:
                output.append(
                    {
                        "id": f"rs_{response_id[5:13]}_{index}",
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": thinking}],
                    }
                )
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": "completed",
        "model": model,
        "output": output,
        "parallel_tool_calls": bool((source_body or {}).get("parallel_tool_calls", True)),
        "tool_choice": (source_body or {}).get("tool_choice", "auto"),
        "tools": (source_body or {}).get("tools", []),
        "usage": _usage_from_anthropic(message),
    }


class OpenAIResponsesProtocolAdapter(MessageProtocolAdapter):
    """Concrete adapter between OpenAI Responses and Anthropic Messages."""

    name = "openai_responses"

    def __init__(self, *, fallback_model: str = "model", source_body: dict[str, Any] | None = None) -> None:
        self._fallback_model = fallback_model
        self._source_body = source_body

    def normalize_request(self, request: Any) -> dict[str, Any]:
        return openai_responses_to_anthropic_messages(dict(request), self._fallback_model)

    def normalize_response(self, response: Any) -> dict[str, Any]:
        return anthropic_message_to_openai_response(dict(response), self._source_body)


__all__ = [
    "OpenAIResponsesProtocolAdapter",
    "anthropic_message_to_openai_response",
    "openai_responses_to_anthropic_messages",
]
