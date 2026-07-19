"""Protocol-aware prompt compaction policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class PromptCompactionText:
    content_to_text: Callable[..., str]
    compact_text: Callable[..., str]
    build_summary: Callable[..., str]
    append_system_texts: Callable[..., Any]
    truncate: Callable[..., str]
    chunk_count: Callable[..., int]


@dataclass(frozen=True, slots=True)
class PromptCompactionRuntime:
    estimate_tokens: Callable[..., int]
    llm_compact_messages: Callable[..., list[dict[str, Any]] | None]
    write_activity: Callable[..., Any]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class PromptCompactionServices:
    text: PromptCompactionText
    runtime: PromptCompactionRuntime


def anthropic_message_has_tool_result(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


def anthropic_safe_tail_start(message: dict[str, Any]) -> bool:
    return str(message.get("role") or "") == "user" and not anthropic_message_has_tool_result(message)


def compact_anthropic_body_for_budget(
    body: dict[str, Any],
    budget_tokens: int,
    *,
    provider: str = "",
    model: str = "",
    pcfg: dict[str, Any] | None = None,
    full_compact_request: bool = False,
    services: PromptCompactionServices,
) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return body
    typed_messages = [message for message in messages if isinstance(message, dict)]
    if len(typed_messages) != len(messages):
        return body
    text = services.text
    runtime = services.runtime
    budget_tokens = max(8192, budget_tokens)
    initial_tokens = runtime.estimate_tokens(body)
    if initial_tokens <= budget_tokens:
        return body
    if full_compact_request and pcfg is not None and provider and model:
        compact_messages = runtime.llm_compact_messages(
            provider,
            model,
            pcfg,
            typed_messages,
            budget_tokens,
            wire="anthropic",
        )
        if compact_messages:
            compacted = dict(body)
            compacted["messages"] = compact_messages
            compacted["tools"] = []
            compacted.pop("tool_choice", None)
            compacted.pop("parallel_tool_calls", None)
            final_tokens = runtime.estimate_tokens(compacted)
            if final_tokens <= budget_tokens:
                return compacted
            runtime.log(
                "WARN",
                f"context_compact_map_reduce_oversize provider={provider} model={model} tokens={final_tokens} budget={budget_tokens}; falling back to deterministic compact",
            )

    summary_budget = max(1024, min(24576, budget_tokens // 10))
    tail_budget = max(8192, budget_tokens - summary_budget)
    tail_start = len(typed_messages)
    base = dict(body)
    for index in range(len(typed_messages) - 1, -1, -1):
        candidate_body = dict(base)
        candidate_body["messages"] = typed_messages[index:]
        if runtime.estimate_tokens(candidate_body) <= tail_budget:
            tail_start = index
            continue
        break
    while tail_start < len(typed_messages) and not anthropic_safe_tail_start(typed_messages[tail_start]):
        tail_start += 1
    if tail_start >= len(typed_messages):
        tail_start = max(0, len(typed_messages) - 1)
        latest = typed_messages[tail_start]
        if anthropic_message_has_tool_result(latest):
            safe_text = text.content_to_text(latest.get("content"))
            tail = [{"role": "user", "content": text.compact_text(safe_text)}]
        else:
            tail = [latest]
    else:
        tail = typed_messages[tail_start:]
    omitted = typed_messages[:tail_start]
    summary_text = text.build_summary(omitted, budget_tokens)
    out = dict(body)
    out["messages"] = tail
    out["system"] = text.append_system_texts(body.get("system"), [summary_text])
    while runtime.estimate_tokens(out) > budget_tokens and len(tail) > 1:
        tail = tail[1:]
        while tail and not anthropic_safe_tail_start(tail[0]):
            tail = tail[1:]
        if not tail:
            tail = [typed_messages[-1]]
            break
        out["messages"] = tail
        omitted = typed_messages[: len(typed_messages) - len(tail)]
        out["system"] = text.append_system_texts(
            body.get("system"),
            [text.build_summary(omitted, budget_tokens)],
        )
    final_tokens = runtime.estimate_tokens(out)
    if final_tokens > budget_tokens:
        compact_summary = text.build_summary(omitted, max(8192, budget_tokens // 2))
        out["system"] = text.append_system_texts(
            body.get("system"),
            [text.truncate(compact_summary, max(4096, budget_tokens * 2))],
        )
        final_tokens = runtime.estimate_tokens(out)
    if final_tokens > budget_tokens and tail:
        base_without_messages = dict(out)
        base_without_messages["messages"] = []
        remaining_chars = max(1024, (budget_tokens - runtime.estimate_tokens(base_without_messages)) * 4 - 1024)
        latest = tail[-1]
        latest_role = str(latest.get("role") or "unknown")
        latest_text = text.content_to_text(latest.get("content"))
        out["messages"] = [{
            "role": "user",
            "content": text.truncate(
                f"[Latest retained message compacted from role={latest_role} because it alone exceeded the provider context budget.]\n{latest_text}",
                remaining_chars,
            ),
        }]
        final_tokens = runtime.estimate_tokens(out)
    runtime.log(
        "WARN",
        f"compacted anthropic payload messages {len(typed_messages)}->{len(out.get('messages') or [])} tokens {initial_tokens}->{final_tokens} budget={budget_tokens}",
    )
    chunk_count = text.chunk_count(omitted, budget_tokens)
    if chunk_count and (provider or model):
        runtime.write_activity(
            provider or "provider",
            model or str(body.get("model") or ""),
            chunks=chunk_count,
            parallel_sessions=1,
            tokens=initial_tokens,
            final_tokens=final_tokens,
            budget=budget_tokens,
            omitted_messages=len(omitted),
            retained_messages=len(out.get("messages") or []),
        )
    return out
