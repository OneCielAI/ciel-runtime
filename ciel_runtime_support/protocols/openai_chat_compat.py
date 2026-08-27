"""Pure OpenAI Chat Completions <-> Anthropic Messages projections."""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any


_CHAT_REASONING_ENVELOPE_PREFIX = "ciel-chat-reasoning-v1:"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    return "\n".join(
        str(block.get("text") or block.get("refusal") or "")
        for block in value
        if isinstance(block, dict)
        and str(block.get("type") or "text")
        in {"text", "input_text", "output_text", "refusal"}
    )


def _content_to_anthropic(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    blocks: list[dict[str, Any]] = []
    for block in value:
        if isinstance(block, str):
            blocks.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "text")
        if block_type in {"text", "input_text", "output_text"}:
            blocks.append({"type": "text", "text": str(block.get("text") or "")})
            continue
        if block_type == "refusal":
            blocks.append({"type": "text", "text": str(block.get("refusal") or "")})
            continue
        if block_type != "image_url":
            raise ValueError(
                f"adapted Chat Completions routes do not support content type {block_type!r}"
            )
        image = block.get("image_url")
        url = str(image.get("url") or "") if isinstance(image, dict) else str(image or "")
        if not url:
            continue
        if url.startswith("data:") and ";base64," in url:
            media_type, data = url[5:].split(";base64,", 1)
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type or "image/png",
                        "data": data,
                    },
                }
            )
        else:
            blocks.append(
                {"type": "image", "source": {"type": "url", "url": url}}
            )
    return blocks


def _function_input(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must contain a JSON object") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field} must contain a JSON object")


def _encode_reasoning_blocks(blocks: list[dict[str, Any]]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(blocks, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return _CHAT_REASONING_ENVELOPE_PREFIX + encoded


def _decode_reasoning_blocks(value: Any) -> list[dict[str, Any]]:
    raw = str(value or "")
    if not raw.startswith(_CHAT_REASONING_ENVELOPE_PREFIX):
        raise ValueError("assistant reasoning_opaque has an invalid envelope")
    encoded = raw[len(_CHAT_REASONING_ENVELOPE_PREFIX) :]
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("assistant reasoning_opaque is malformed") from exc
    if not isinstance(payload, list):
        raise ValueError("assistant reasoning_opaque must contain an array")
    blocks: list[dict[str, Any]] = []
    for index, block in enumerate(payload):
        if not isinstance(block, dict):
            raise ValueError(f"assistant reasoning_opaque[{index}] must be an object")
        block_type = str(block.get("type") or "")
        if block_type == "thinking":
            projected = {
                "type": "thinking",
                "thinking": str(block.get("thinking") or ""),
            }
            if block.get("signature") is not None:
                projected["signature"] = str(block.get("signature") or "")
            blocks.append(projected)
        elif block_type == "redacted_thinking" and str(block.get("data") or ""):
            blocks.append(
                {"type": "redacted_thinking", "data": str(block["data"])}
            )
        else:
            raise ValueError(
                "assistant reasoning_opaque contains unsupported block type "
                f"{block_type or '(empty)'}"
            )
    return blocks


def _assistant_reasoning_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    if message.get("reasoning_opaque") is not None:
        return _decode_reasoning_blocks(message.get("reasoning_opaque"))
    reasoning = str(message.get("reasoning_content") or "")
    signature = str(message.get("reasoning_signature") or "")
    if signature and not reasoning:
        raise ValueError("assistant reasoning_signature requires reasoning_content")
    if not reasoning:
        return []
    block: dict[str, Any] = {"type": "thinking", "thinking": reasoning}
    if signature:
        block["signature"] = signature
    return [block]


def _tools_to_anthropic(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tools: list[dict[str, Any]] = []
    for index, tool in enumerate(value):
        if not isinstance(tool, dict):
            raise ValueError(f"tools[{index}] must be an object")
        tool_type = str(tool.get("type") or "function")
        if tool_type != "function":
            raise ValueError(
                "adapted Chat Completions routes do not support "
                f"tools[{index}] type {tool_type!r}"
            )
        function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if not name:
            raise ValueError(f"tools[{index}].function.name is required")
        schema = function.get("parameters")
        if schema is not None and not isinstance(schema, dict):
            raise ValueError(
                f"tools[{index}].function.parameters must be an object"
            )
        projected = {
            "name": name,
            "description": str(function.get("description") or ""),
            "input_schema": (
                dict(schema)
                if isinstance(schema, dict)
                else {"type": "object", "properties": {}}
            ),
        }
        strict = function.get("strict", False)
        if not isinstance(strict, bool):
            raise ValueError(f"tools[{index}].function.strict must be a boolean")
        projected["_ciel_openai_strict"] = strict
        tools.append(projected)
    return tools


def _allowed_tools_to_anthropic(
    value: dict[str, Any],
    tools: list[dict[str, Any]],
    parallel: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    allowed = value.get("allowed_tools")
    if not isinstance(allowed, dict):
        raise ValueError("tool_choice.allowed_tools must be an object")
    mode = str(allowed.get("mode") or "").strip()
    if mode not in {"auto", "required"}:
        raise ValueError("tool_choice.allowed_tools.mode must be auto or required")
    definitions = allowed.get("tools")
    if not isinstance(definitions, list) or not definitions:
        raise ValueError("tool_choice.allowed_tools.tools must be a non-empty array")
    names: list[str] = []
    for index, definition in enumerate(definitions):
        if not isinstance(definition, dict):
            raise ValueError(
                f"tool_choice.allowed_tools.tools[{index}] must be an object"
            )
        tool_type = str(definition.get("type") or "")
        function = (
            definition.get("function")
            if isinstance(definition.get("function"), dict)
            else {}
        )
        name = str(function.get("name") or "").strip()
        if tool_type != "function" or not name:
            raise ValueError(
                "adapted Chat Completions routes support only named function "
                f"entries in tool_choice.allowed_tools.tools[{index}]"
            )
        names.append(name)
    available = {str(tool.get("name") or ""): tool for tool in tools}
    missing = sorted(set(names) - set(available))
    if missing:
        raise ValueError(
            "tool_choice.allowed_tools references undefined functions: "
            + ", ".join(missing)
        )
    allowed_names = set(names)
    filtered = [
        tool for tool in tools if str(tool.get("name") or "") in allowed_names
    ]
    choice: dict[str, Any] = {"type": "auto" if mode == "auto" else "any"}
    if parallel is not None:
        choice["disable_parallel_tool_use"] = not bool(parallel)
    return filtered, choice


def _tool_choice_to_anthropic(value: Any, parallel: Any) -> dict[str, Any] | None:
    choice: dict[str, Any] | None
    if value is None or value == "auto":
        choice = {"type": "auto"}
    elif value == "required":
        choice = {"type": "any"}
    elif value == "none":
        return None
    elif isinstance(value, dict):
        choice_type = str(value.get("type") or "").strip()
        if choice_type == "custom":
            raise ValueError(
                "adapted Chat Completions routes do not support custom tool_choice"
            )
        if choice_type == "allowed_tools":
            raise ValueError("allowed_tools must be resolved with the tool definitions")
        function = value.get("function") if isinstance(value.get("function"), dict) else {}
        name = str(function.get("name") or value.get("name") or "").strip()
        if choice_type not in {"", "function"} or not name:
            raise ValueError(f"unsupported tool_choice: {value!r}")
        choice = {"type": "tool", "name": name}
    else:
        raise ValueError(f"unsupported tool_choice: {value!r}")
    if choice is not None and parallel is not None:
        choice["disable_parallel_tool_use"] = not bool(parallel)
    return choice


def openai_chat_to_anthropic_messages(
    body: dict[str, Any],
    fallback_max_tokens: int = 4096,
) -> dict[str, Any]:
    """Project one Chat Completions request onto Anthropic Messages."""

    n_value = body.get("n")
    if n_value is not None and (type(n_value) is not int or n_value != 1):
        raise ValueError("adapted Chat Completions routes support only n=1")
    unsupported: list[str] = []
    for key in (
        "audio",
        "function_call",
        "functions",
        "logit_bias",
        "metadata",
        "modalities",
        "prediction",
        "prompt_cache_key",
        "prompt_cache_retention",
        "response_format",
        "safety_identifier",
        "seed",
        "service_tier",
        "user",
        "verbosity",
        "web_search_options",
    ):
        if body.get(key) not in (None, False, "", [], {}):
            unsupported.append(key)
    for key in ("frequency_penalty", "presence_penalty"):
        if body.get(key) not in (None, 0, 0.0):
            unsupported.append(key)
    if body.get("logprobs") is True or body.get("top_logprobs") is not None:
        unsupported.append("logprobs")
    if body.get("store") is True:
        unsupported.append("store")
    stream_options = body.get("stream_options")
    if isinstance(stream_options, dict):
        unsupported.extend(
            f"stream_options.{key}"
            for key in set(stream_options) - {"include_usage"}
        )
    if unsupported:
        raise ValueError(
            "adapted Chat Completions routes do not support: "
            + ", ".join(sorted(set(unsupported)))
        )
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    source_messages = body.get("messages")
    if not isinstance(source_messages, list):
        raise ValueError("messages must be an array")
    for index, message in enumerate(source_messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object")
        if message.get("name") is not None:
            raise ValueError(
                f"messages[{index}].name is not supported on adapted "
                "Chat Completions routes"
            )
        role = str(message.get("role") or "").strip().lower()
        content = message.get("content", "")
        if role in {"system", "developer"}:
            text = _text(content).strip()
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if not tool_call_id:
                raise ValueError(f"messages[{index}].tool_call_id is required")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": _content_to_anthropic(content),
                        }
                    ],
                }
            )
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"messages[{index}].role is not supported: {role or '(empty)'}")
        projected_content = _content_to_anthropic(content)
        if role == "assistant":
            reasoning_blocks = _assistant_reasoning_blocks(message)
            if reasoning_blocks:
                visible_blocks = (
                    list(projected_content)
                    if isinstance(projected_content, list)
                    else (
                        [{"type": "text", "text": projected_content}]
                        if projected_content
                        else []
                    )
                )
                projected_content = reasoning_blocks + visible_blocks
        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            blocks = (
                list(projected_content)
                if isinstance(projected_content, list)
                else ([{"type": "text", "text": projected_content}] if projected_content else [])
            )
            for call_index, call in enumerate(message["tool_calls"]):
                if not isinstance(call, dict):
                    raise ValueError(
                        f"messages[{index}].tool_calls[{call_index}] must be an object"
                    )
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                call_id = str(call.get("id") or "").strip()
                name = str(function.get("name") or "").strip()
                if not call_id or not name:
                    raise ValueError(
                        f"messages[{index}].tool_calls[{call_index}] requires id and function.name"
                    )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": _function_input(
                            function.get("arguments", {}),
                            f"messages[{index}].tool_calls[{call_index}].function.arguments",
                        ),
                    }
                )
            projected_content = blocks
        messages.append({"role": role, "content": projected_content})

    request: dict[str, Any] = {
        "model": str(body.get("model") or ""),
        "messages": messages,
        "max_tokens": (
            _positive_int(body.get("max_completion_tokens"))
            or _positive_int(body.get("max_tokens"))
            or max(1, int(fallback_max_tokens))
        ),
        "stream": bool(body.get("stream", False)),
    }
    if system_parts:
        request["system"] = "\n\n".join(system_parts)
    tools = _tools_to_anthropic(body.get("tools"))
    choice_value = body.get("tool_choice")
    if tools and choice_value != "none":
        if (
            isinstance(choice_value, dict)
            and str(choice_value.get("type") or "") == "allowed_tools"
        ):
            tools, tool_choice = _allowed_tools_to_anthropic(
                choice_value,
                tools,
                body.get("parallel_tool_calls"),
            )
        else:
            tool_choice = _tool_choice_to_anthropic(
                choice_value, body.get("parallel_tool_calls")
            )
            if tool_choice is not None and tool_choice.get("type") == "tool":
                tool_name = str(tool_choice.get("name") or "")
                if tool_name not in {str(tool.get("name") or "") for tool in tools}:
                    raise ValueError(
                        f"tool_choice references undefined function: {tool_name}"
                    )
        request["tools"] = tools
        if tool_choice is not None:
            request["tool_choice"] = tool_choice
    elif not tools and choice_value not in (None, "none", "auto"):
        raise ValueError("tool_choice requires at least one supported function tool")
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            request[key] = body[key]
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        request["stop_sequences"] = [stop]
    elif isinstance(stop, list):
        request["stop_sequences"] = [str(item) for item in stop if str(item)]
    if body.get("reasoning_effort") is not None:
        request["output_config"] = {"effort": str(body["reasoning_effort"])}
    return request


def anthropic_message_to_openai_chat_completion(
    message: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """Project one completed Anthropic message onto Chat Completions JSON."""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_blocks: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    content = message.get("content") if isinstance(message.get("content"), list) else []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block_type == "thinking":
            reasoning_parts.append(str(block.get("thinking") or ""))
            preserved = {
                "type": "thinking",
                "thinking": str(block.get("thinking") or ""),
            }
            if block.get("signature") is not None:
                preserved["signature"] = str(block.get("signature") or "")
            reasoning_blocks.append(preserved)
        elif block_type == "redacted_thinking" and str(block.get("data") or ""):
            reasoning_blocks.append(
                {"type": "redacted_thinking", "data": str(block["data"])}
            )
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": str(block.get("id") or f"call_{len(tool_calls) + 1}"),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or "tool"),
                        "arguments": json.dumps(
                            block.get("input") if isinstance(block.get("input"), dict) else {},
                            ensure_ascii=False,
                        ),
                    },
                }
            )
    stop_reason = str(message.get("stop_reason") or "end_turn")
    finish_reason = {
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "refusal": "content_filter",
    }.get(stop_reason, "stop")
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts) if text_parts else None,
    }
    if reasoning_parts:
        assistant["reasoning_content"] = "".join(reasoning_parts)
    if reasoning_blocks:
        assistant["reasoning_opaque"] = _encode_reasoning_blocks(reasoning_blocks)
        signed = [
            str(block.get("signature") or "")
            for block in reasoning_blocks
            if block.get("type") == "thinking" and block.get("signature")
        ]
        if len(signed) == 1:
            assistant["reasoning_signature"] = signed[0]
    if tool_calls:
        assistant["tool_calls"] = tool_calls
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    cache_read = max(0, int(usage.get("cache_read_input_tokens") or 0))
    cache_creation = max(0, int(usage.get("cache_creation_input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    prompt_tokens = input_tokens + cache_read + cache_creation
    prompt_details = {"cached_tokens": cache_read} if cache_read else None
    projected_usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
    }
    if prompt_details:
        projected_usage["prompt_tokens_details"] = prompt_details
    return {
        "id": str(message.get("id") or f"chatcmpl_{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(message.get("model") or model),
        "choices": [
            {
                "index": 0,
                "message": assistant,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": projected_usage,
    }


__all__ = [
    "anthropic_message_to_openai_chat_completion",
    "openai_chat_to_anthropic_messages",
]
