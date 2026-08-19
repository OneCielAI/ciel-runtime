"""Anthropic Messages projections for chat-style provider protocols."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChatProjectionText:
    system_messages: Callable[..., list[dict[str, Any]]]
    execution_reminder: Callable[..., dict[str, str]]
    state_messages: Callable[..., list[dict[str, Any]]]
    content_to_text: Callable[..., str]
    compact_text: Callable[..., str]
    skip_message: Callable[..., bool]
    attachment_only: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChatProjectionTools:
    collect_result_context: Callable[..., tuple[dict[str, str], set[str]]]
    plan_mode_active: Callable[..., bool]
    input_for_prompt: Callable[..., str]
    persisted_output: Callable[..., bool]
    truncate_for_prompt: Callable[..., str]
    canonical_signature: Callable[..., str]
    format_result: Callable[..., tuple[str, str]]


@dataclass(frozen=True, slots=True)
class ChatProjectionPolicy:
    thinking_block_types: frozenset[str] | set[str] | tuple[str, ...]
    tool_result_limit: int


@dataclass(frozen=True, slots=True)
class ChatProjectionServices:
    text: ChatProjectionText
    tools: ChatProjectionTools
    policy: ChatProjectionPolicy


@dataclass(frozen=True, slots=True)
class OpenAiHistoryServices:
    log: Callable[[str, str], None]


def openai_multimodal_content(content: Any, text: ChatProjectionText) -> Any:
    if not isinstance(content, list):
        return text.compact_text(text.content_to_text(content))
    projected: list[dict[str, Any]] = []
    fallback: list[Any] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            value = text.compact_text(str(block.get("text") or ""))
            if value:
                projected.append({"type": "text", "text": value})
            continue
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source") if isinstance(block.get("source"), dict) else {}
            if source.get("type") == "base64" and source.get("data"):
                media_type = str(source.get("media_type") or "image/png")
                projected.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{source['data']}"},
                    }
                )
                continue
            if source.get("type") == "url" and source.get("url"):
                projected.append(
                    {"type": "image_url", "image_url": {"url": str(source["url"])}}
                )
                continue
        fallback.append(block)
    fallback_text = text.compact_text(text.content_to_text(fallback))
    if fallback_text:
        projected.append({"type": "text", "text": fallback_text})
    return projected if any(item.get("type") == "image_url" for item in projected) else text.compact_text(text.content_to_text(content))


def closing_system_message_indexes(
    body: dict[str, Any], text: ChatProjectionText
) -> set[int]:
    """Indexes of the system messages a conversation ends on.

    Anthropic lets a client hand over input that arrived while a turn was still
    running as a system message placed after the last turn; Claude Code delivers
    what the user typed mid-task that way. A chat wire has no such envelope, and
    the difference is not cosmetic: replaying a captured Claude Code request
    against ollama-cloud/deepseek-v4-flash, the same text was acted on 0/10
    times as the closing system message and 9/10 as a user message, while moving
    it earlier or removing the surrounding router text changed nothing.

    Only the closing run counts. A system message with further conversation
    after it is background the model has already had a turn to act on.
    """

    messages = body.get("messages", []) or []
    kept = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict)
        and not text.attachment_only(message)
        and not text.skip_message(message)
    ]
    closing: set[int] = set()
    for index in reversed(kept):
        if messages[index].get("role") != "system":
            break
        closing.add(index)
    return closing


def coalesce_ollama_system_messages(
    messages: list[dict[str, Any]], text: ChatProjectionText
) -> list[dict[str, Any]]:
    """Return an Ollama history with one leading system message.

    Ollama's wire format permits a system role, but model templates are not
    uniform.  In particular, Qwen 3.8 rejects a second or non-leading system
    message with ``system message must be at the beginning``.  Anthropic input
    can contain the original system prompt, Ciel's execution reminder, runtime
    state, and mid-history system context separately.  Preserve their order and
    content while presenting them as the single leading system instruction
    accepted by those templates.
    """

    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "system":
            conversation.append(message)
            continue
        content = text.compact_text(text.content_to_text(message.get("content", "")))
        if content:
            system_parts.append(content)
    if not system_parts:
        return conversation
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *conversation]


def anthropic_messages_to_ollama(body: dict[str, Any], *, services: ChatProjectionServices) -> list[dict[str, Any]]:
    text = services.text
    tools = services.tools
    messages = text.system_messages(body.get("system"))
    messages.append(text.execution_reminder())
    messages.extend(text.state_messages(body))
    prior_tool_results, latest_noop_result_ids = tools.collect_result_context(body)
    in_plan_mode = tools.plan_mode_active(body)
    tool_names_by_id: dict[str, str] = {}
    tool_inputs_by_id: dict[str, Any] = {}
    closing_system = closing_system_message_indexes(body, text)
    for index, message in enumerate(body.get("messages", []) or []):
        if not isinstance(message, dict) or text.attachment_only(message) or text.skip_message(message):
            continue
        role = message.get("role", "user")
        if role == "system" and index in closing_system:
            role = "user"
        content = message.get("content", "")
        if role == "user" and isinstance(content, list):
            text_blocks: list[Any] = []
            tool_blocks: list[dict[str, Any]] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_blocks.append(block)
                else:
                    text_blocks.append(block)
            user_text = text.content_to_text(text_blocks)
            if user_text:
                messages.append({"role": "user", "content": text.compact_text(user_text)})
            for block in tool_blocks:
                tool_use_id = str(block.get("tool_use_id") or "")
                tool_name = tool_names_by_id.get(tool_use_id, "tool")
                tool_input = tool_inputs_by_id.get(tool_use_id)
                tool_input_text = tools.input_for_prompt(tool_input)
                raw_result_text = text.content_to_text(block.get("content", ""))
                if tools.persisted_output(raw_result_text):
                    result_text = raw_result_text
                    tool_text = raw_result_text
                    tool_summary = ""
                else:
                    result_text = tools.truncate_for_prompt(raw_result_text, services.policy.tool_result_limit)
                    signature = tools.canonical_signature(tool_name, tool_input)
                    tool_text, tool_summary = tools.format_result(
                        tool_name=tool_name,
                        tool_input_text=tool_input_text,
                        result_text=result_text,
                        is_error=bool(block.get("is_error")),
                        prior_success_text=prior_tool_results.get(signature, ""),
                        include_prior_success=tool_use_id in latest_noop_result_ids,
                        in_plan_mode=in_plan_mode,
                    )
                    if not block.get("is_error") and tool_name == "TaskList":
                        tool_summary = (
                            f"The task list is current:\n{result_text}\n\n"
                            "If any task is in_progress and the user's request is not finished, your next response "
                            "must call a concrete work tool such as Write, Edit, Read, or Bash. Do not respond with "
                            "another progress announcement like 'I will write the files now'. If everything is "
                            "actually complete, provide the final answer."
                        )
                messages.append({"role": "tool", "tool_name": tool_name, "content": tool_text})
                if tool_summary:
                    messages.append({"role": "user", "content": tool_summary})
            continue
        message_text = text.content_to_text(content)
        out: dict[str, Any] = {"role": role, "content": text.compact_text(message_text)}
        if role == "assistant" and isinstance(content, list):
            calls = []
            thinking_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    thinking = str(block.get("thinking") or "")
                    if thinking:
                        thinking_parts.append(thinking)
                    continue
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name") or "tool")
                    tool_id = str(block.get("id") or "")
                    if tool_id:
                        tool_names_by_id[tool_id] = name
                        tool_inputs_by_id[tool_id] = block.get("input") or {}
                    calls.append({"function": {"name": name, "arguments": block.get("input") or {}}})
            if calls:
                out["tool_calls"] = calls
            if thinking_parts:
                out["thinking"] = "\n".join(thinking_parts)
        messages.append(out)
    return coalesce_ollama_system_messages(messages, text)


def anthropic_messages_to_openai(
    body: dict[str, Any],
    reasoning_passback: bool = False,
    *,
    services: ChatProjectionServices,
) -> list[dict[str, Any]]:
    text = services.text
    tools = services.tools
    messages = text.system_messages(body.get("system"))
    messages.append(text.execution_reminder())
    messages.extend(text.state_messages(body))
    prior_tool_results, latest_noop_result_ids = tools.collect_result_context(body)
    in_plan_mode = tools.plan_mode_active(body)
    tool_names_by_id: dict[str, str] = {}
    tool_inputs_by_id: dict[str, Any] = {}
    for message in body.get("messages", []) or []:
        if not isinstance(message, dict) or text.attachment_only(message) or text.skip_message(message):
            continue
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "assistant" and isinstance(content, list):
            text_blocks: list[Any] = []
            tool_calls: list[dict[str, Any]] = []
            reasoning_seen = False
            reasoning_parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") in services.policy.thinking_block_types:
                    reasoning_seen = True
                    if block.get("type") == "thinking":
                        reasoning_parts.append(str(block.get("thinking") or ""))
                    continue
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_id = str(block.get("id") or f"call_{len(tool_calls) + 1}")
                    name = str(block.get("name") or "tool")
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    tool_names_by_id[tool_id] = name
                    tool_inputs_by_id[tool_id] = tool_input
                    tool_calls.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(tool_input, ensure_ascii=False)},
                    })
                else:
                    text_blocks.append(block)
            out: dict[str, Any] = {"role": "assistant", "content": openai_multimodal_content(text_blocks, text)}
            if reasoning_seen or reasoning_passback:
                out["reasoning_content"] = "\n".join(reasoning_parts)
            if tool_calls:
                out["tool_calls"] = tool_calls
            if message.get("partial") is True:
                out["partial"] = True
            messages.append(out)
            continue
        if role == "user" and isinstance(content, list):
            text_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_id = str(block.get("tool_use_id") or "call_tool")
                    raw_result_text = text.content_to_text(block.get("content", ""))
                    if tool_id not in tool_names_by_id:
                        result_text = raw_result_text if tools.persisted_output(raw_result_text) else tools.truncate_for_prompt(raw_result_text, services.policy.tool_result_limit)
                        if tools.persisted_output(result_text):
                            text_blocks.append({"type": "text", "text": result_text})
                        else:
                            text_blocks.append({"type": "text", "text": f"Historical tool result without a retained assistant tool call ({tool_id}):\n{result_text}"})
                        continue
                    tool_name = tool_names_by_id.get(tool_id, "tool")
                    tool_input = tool_inputs_by_id.get(tool_id)
                    if tools.persisted_output(raw_result_text):
                        tool_text = raw_result_text
                    else:
                        result_text = tools.truncate_for_prompt(raw_result_text, services.policy.tool_result_limit)
                        tool_text, _ = tools.format_result(
                            tool_name=tool_name,
                            tool_input_text=tools.input_for_prompt(tool_input),
                            result_text=result_text,
                            is_error=bool(block.get("is_error")),
                            prior_success_text=prior_tool_results.get(tools.canonical_signature(tool_name, tool_input), ""),
                            include_prior_success=tool_id in latest_noop_result_ids,
                            in_plan_mode=in_plan_mode,
                        )
                    messages.append({"role": "tool", "tool_call_id": tool_id, "id": tool_id, "content": tool_text})
                else:
                    text_blocks.append(block)
            user_content = openai_multimodal_content(text_blocks, text)
            if user_content:
                messages.append({"role": "user", "content": user_content})
            continue
        out = {"role": role, "content": text.compact_text(text.content_to_text(content))}
        if role == "assistant" and reasoning_passback:
            out["reasoning_content"] = ""
        messages.append(out)
    return messages


def missing_openai_tool_result_message(tool_call: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(tool_call.get("id") or "call_tool")
    function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    name = str(function.get("name") or "tool")
    return {
        "role": "tool",
        "tool_call_id": tool_id,
        "id": tool_id,
        "content": (
            f"Tool result for historical tool call `{name}` was not present in the retained "
            "Claude Code transcript. Treat this as missing historical context, not as a "
            "successful tool execution."
        ),
    }


def orphan_openai_tool_message_to_user(message: dict[str, Any]) -> dict[str, str]:
    tool_id = str(message.get("tool_call_id") or message.get("id") or "unknown")
    content = str(message.get("content") or "")
    return {
        "role": "user",
        "content": (
            f"Historical tool message without a retained assistant tool call ({tool_id}):\n"
            f"{content}"
        ),
    }


def repair_openai_tool_call_adjacency(
    messages: list[dict[str, Any]],
    services: OpenAiHistoryServices,
) -> list[dict[str, Any]]:
    """Repair OpenAI's immediate tool-result adjacency invariant."""
    repaired: list[dict[str, Any]] = []
    missing_count = 0
    orphan_count = 0
    index = 0
    while index < len(messages):
        message = messages[index]
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not (message.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls):
            if message.get("role") == "tool":
                repaired.append(orphan_openai_tool_message_to_user(message))
                orphan_count += 1
            else:
                repaired.append(message)
            index += 1
            continue
        repaired.append(message)
        index += 1
        immediate_tools: list[dict[str, Any]] = []
        while (
            index < len(messages)
            and isinstance(messages[index], dict)
            and messages[index].get("role") == "tool"
        ):
            immediate_tools.append(messages[index])
            index += 1
        by_id: dict[str, list[dict[str, Any]]] = {}
        for tool_message in immediate_tools:
            tool_id = str(tool_message.get("tool_call_id") or tool_message.get("id") or "")
            by_id.setdefault(tool_id, []).append(tool_message)
        required_ids: list[str] = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            tool_id = str(call.get("id") or "")
            if not tool_id:
                continue
            required_ids.append(tool_id)
            matches = by_id.get(tool_id) or []
            if matches:
                repaired.append(matches.pop(0))
            else:
                repaired.append(missing_openai_tool_result_message(call))
                missing_count += 1
        required = set(required_ids)
        for tool_message in immediate_tools:
            tool_id = str(tool_message.get("tool_call_id") or tool_message.get("id") or "")
            if tool_id in required:
                remaining = by_id.get(tool_id) or []
                if tool_message in remaining:
                    remaining.remove(tool_message)
                    repaired.append(orphan_openai_tool_message_to_user(tool_message))
                    orphan_count += 1
            else:
                repaired.append(orphan_openai_tool_message_to_user(tool_message))
                orphan_count += 1
    if missing_count or orphan_count:
        services.log(
            "WARN",
            f"openai_tool_call_adjacency_repaired missing_tool_results={missing_count} "
            f"orphan_tool_messages={orphan_count}",
        )
    return repaired
