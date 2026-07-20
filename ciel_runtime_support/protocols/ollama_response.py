"""Ollama response projection into Anthropic Messages content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OllamaResponseText:
    decode: Callable[[dict[str, Any]], Any]
    strip_thinking: Callable[[str], str]
    parse_pseudo_tools: Callable[..., tuple[str, list[dict[str, Any]]]]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class OllamaResponseTools:
    resolve_name: Callable[..., str]
    normalize_arguments: Callable[..., dict[str, Any]]
    validate_input: Callable[..., dict[str, Any]]
    plan_mode_name: Callable[..., tuple[str | None, dict[str, Any]]]
    cap_notification_wait: Callable[..., dict[str, Any]]
    should_drop: Callable[..., bool]
    append_log: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaResponseRecovery:
    auto_enter_plan: Callable[..., bool]
    recover_empty_with_tasklist: Callable[..., bool]
    keep_alive_with_tasklist: Callable[..., bool]
    auto_continue_choice: Callable[..., bool]
    empty_notice: Callable[[dict[str, Any]], str]
    latest_tool_result_names: Callable[[dict[str, Any]], list[str]]
    synthetic_tool_response: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OllamaResponseOutput:
    encode_message: Callable[..., dict[str, Any]]
    estimate_tokens: Callable[[dict[str, Any]], int]
    timestamp_ms: Callable[[], int]
    process_id: Callable[[], int]


@dataclass(frozen=True, slots=True)
class OllamaResponseServices:
    text: OllamaResponseText
    tools: OllamaResponseTools
    recovery: OllamaResponseRecovery
    output: OllamaResponseOutput


def project_ollama_response(
    data: dict[str, Any],
    model: str,
    source_body: dict[str, Any] | None,
    services: OllamaResponseServices,
) -> dict[str, Any]:
    decoded = services.text.decode(data)
    content: list[dict[str, Any]] = []
    raw_text = decoded.text
    text = services.text.strip_thinking(raw_text)
    if text != raw_text:
        services.text.log(
            "WARN",
            f"suppressed visible Ollama thinking markup model={model} removed_chars={len(raw_text) - len(text)}",
        )
    text, pseudo_tool_calls = services.text.parse_pseudo_tools(text, source_body)
    if text:
        content.append({"type": "text", "text": text})
    tool_id_prefix = f"toolu_ollama_{services.output.timestamp_ms()}_{services.output.process_id()}"
    for index, call in enumerate(list(decoded.tool_calls) + pseudo_tool_calls):
        tool_block = _project_tool_call(call, index, tool_id_prefix, model, source_body, services)
        if tool_block is not None:
            content.append(tool_block)

    emitted = [block for block in content if block.get("type") == "tool_use"]
    recovered = _recover_response(model, source_body, text, emitted, content, tool_id_prefix, services)
    if recovered is not None:
        return recovered
    if source_body is not None and not text.strip() and not emitted:
        text = services.recovery.empty_notice(source_body)
        names = ",".join(services.recovery.latest_tool_result_names(source_body)) or "-"
        services.text.log("WARN", f"ollama_empty_end_turn_notice model={model} latest_tool_results={names}")
        content.append({"type": "text", "text": text})

    input_tokens = decoded.input_tokens
    if input_tokens <= 0 and isinstance(source_body, dict):
        input_tokens = services.output.estimate_tokens(source_body)
    output_tokens = decoded.output_tokens or max(1, len(text) // 4)
    return services.output.encode_message(
        message_id=f"msg_ollama_{services.output.timestamp_ms()}",
        model=model,
        content=content,
        done_reason=decoded.done_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def project_openai_chat_response(
    data: dict[str, Any],
    model: str,
    source_body: dict[str, Any] | None,
    *,
    services: OllamaResponseServices,
    positive_int: Callable[[Any], int | None],
    reasoning_to_block: Callable[[Any], dict[str, Any] | None],
    content_to_text: Callable[[Any], str],
) -> dict[str, Any]:
    """Project an OpenAI-compatible chat response through the shared codec."""
    choices = data.get("choices")
    choice = (
        choices[0]
        if isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
        else {}
    )
    raw_message = choice.get("message")
    message = raw_message if isinstance(raw_message, dict) else {}
    usage_value = data.get("usage")
    usage = usage_value if isinstance(usage_value, dict) else {}
    wrapped = {
        "message": {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        },
        "done_reason": (
            "length"
            if choice.get("finish_reason") == "length"
            else "stop"
        ),
        "prompt_eval_count": positive_int(usage.get("prompt_tokens"))
        or (
            services.output.estimate_tokens(source_body)
            if isinstance(source_body, dict)
            else 0
        ),
        "eval_count": positive_int(usage.get("completion_tokens")) or 0,
    }
    output = project_ollama_response(
        wrapped,
        model,
        source_body,
        services,
    )
    thinking_block = reasoning_to_block(message.get("reasoning_content"))
    if thinking_block is None:
        return output
    content = output.get("content")
    if not isinstance(content, list):
        content = [{"type": "text", "text": content_to_text(content)}]
    output = dict(output)
    output["content"] = [thinking_block, *content]
    return output


def _project_tool_call(
    call: dict[str, Any],
    index: int,
    id_prefix: str,
    model: str,
    source_body: dict[str, Any] | None,
    services: OllamaResponseServices,
) -> dict[str, Any] | None:
    function = call.get("function") if isinstance(call, dict) else {}
    if not isinstance(function, dict) or not function.get("name"):
        return None
    raw_name = str(function["name"])
    name = services.tools.resolve_name(raw_name, source_body)
    raw_arguments = function.get("arguments")
    normalized = services.tools.normalize_arguments(name, raw_arguments)
    tool_input = services.tools.validate_input(name, normalized)
    if source_body is not None:
        name, tool_input = services.tools.plan_mode_name(source_body, name, tool_input)
        if name is None:
            return None
    tool_input = services.tools.cap_notification_wait(name, tool_input)
    if services.tools.should_drop(name, tool_input, raw_name, source_body):
        return None
    services.tools.append_log(
        "ollama_nonstream_tool_call",
        {
            "model": model,
            "raw_name": raw_name,
            "matched_name": name,
            "raw_arguments": raw_arguments,
            "normalized_arguments": normalized,
            "emitted_input": tool_input,
        },
    )
    return {"type": "tool_use", "id": f"{id_prefix}_{index}", "name": name, "input": tool_input}


def _recover_response(
    model: str,
    source_body: dict[str, Any] | None,
    text: str,
    emitted: list[dict[str, Any]],
    content: list[dict[str, Any]],
    id_prefix: str,
    services: OllamaResponseServices,
) -> dict[str, Any] | None:
    if source_body is None:
        return None
    recovery = services.recovery
    if recovery.auto_enter_plan(source_body, text, emitted):
        services.text.log("WARN", "auto-synthesized EnterPlanMode from short/empty upstream response")
        return recovery.synthetic_tool_response(model, "EnterPlanMode")
    if recovery.recover_empty_with_tasklist(source_body, text, emitted):
        services.text.log("WARN", "auto-synthesized TaskList from empty upstream end_turn")
        return recovery.synthetic_tool_response(model, "TaskList")
    if recovery.keep_alive_with_tasklist(source_body, text, emitted):
        services.text.log("WARN", "auto-synthesized TaskList to keep work moving after tool result")
        block = {"type": "tool_use", "id": f"{id_prefix}_keepalive", "name": "TaskList", "input": {}}
        content.append(block)
        emitted.append(block)
    if recovery.auto_continue_choice(source_body, text, emitted):
        services.text.log("WARN", "auto-synthesized TaskList after clarification question")
        block = {
            "type": "tool_use",
            "id": f"toolu_ollama_choice_{services.output.timestamp_ms()}",
            "name": "TaskList",
            "input": {},
        }
        content.append(block)
        emitted.append(block)
    return None
