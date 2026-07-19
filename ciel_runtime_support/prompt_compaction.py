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


def compact_chat_messages_for_budget(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    budget_tokens: int,
    *,
    provider: str = "",
    model: str = "",
    pcfg: dict[str, Any] | None = None,
    full_compact_request: bool = False,
    wire: str | None = None,
    services: PromptCompactionServices,
) -> list[dict[str, Any]]:
    if not messages:
        return messages
    text = services.text
    runtime = services.runtime
    budget_tokens = max(8192, budget_tokens)
    initial_tokens = runtime.estimate_tokens({"messages": messages, "tools": tools})
    if initial_tokens <= budget_tokens:
        return messages
    if full_compact_request and pcfg is not None and provider and model:
        compacted_by_llm = runtime.llm_compact_messages(
            provider,
            model,
            pcfg,
            messages,
            budget_tokens,
            wire=wire or "ollama",
        )
        if compacted_by_llm:
            final_tokens = runtime.estimate_tokens({"messages": compacted_by_llm, "tools": []})
            if final_tokens <= budget_tokens:
                return compacted_by_llm
            runtime.log(
                "WARN",
                f"context_compact_map_reduce_oversize provider={provider} model={model} tokens={final_tokens} budget={budget_tokens}; falling back to deterministic compact",
            )

    def compact_message(message: dict[str, Any], max_chars: int, prefix: str) -> dict[str, Any]:
        role = str(message.get("role") or "user")
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        content = message.get("content")
        content_text = content if isinstance(content, str) else text.content_to_text(content)
        out: dict[str, Any] = {"role": role}
        if message.get("name"):
            out["name"] = str(message.get("name"))
        out["content"] = text.truncate(f"{prefix}\n{content_text}", max(256, max_chars))
        return out

    def hard_cap(
        system_messages: list[dict[str, Any]],
        latest_message: dict[str, Any] | None,
        omitted_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        summary_text = text.build_summary(omitted_messages, max(8192, budget_tokens // 2))
        summary_prefix = (
            "[ciel-runtime context guard: older conversation history and oversized retained messages were "
            "compacted because they exceeded the provider context budget.]"
        )
        latest_prefix = "[Latest retained message compacted because the previous payload exceeded the provider context budget.]"
        system_chars = max(512, min(8192, budget_tokens))
        summary_chars = max(512, min(4096, budget_tokens))
        latest_chars = max(1024, budget_tokens * 4)
        last_candidate: list[dict[str, Any]] = []
        for _ in range(10):
            per_system_chars = max(256, system_chars // max(1, len(system_messages)))
            capped_systems = [
                compact_message(message, per_system_chars, "[System message compacted for context budget.]")
                for message in system_messages
            ]
            summary_message = {
                "role": "user",
                "content": text.truncate(f"{summary_prefix}\n{summary_text}", summary_chars),
            }
            candidate = [*capped_systems, summary_message]
            base_tokens = runtime.estimate_tokens({"messages": candidate, "tools": tools})
            remaining_chars = max(256, (budget_tokens - base_tokens) * 4 - 1024)
            if latest_message is not None:
                candidate.append(compact_message(latest_message, min(latest_chars, remaining_chars), latest_prefix))
            last_candidate = candidate
            if runtime.estimate_tokens({"messages": candidate, "tools": tools}) <= budget_tokens:
                return candidate
            system_chars = max(256, system_chars // 2)
            summary_chars = max(256, summary_chars // 2)
            latest_chars = max(256, latest_chars // 2)
        return last_candidate

    system_messages = [message for message in messages if message.get("role") == "system"]
    non_system = [message for message in messages if message.get("role") != "system"]
    first_user = next((message for message in non_system if message.get("role") == "user"), None)
    preserved_tail: list[dict[str, Any]] = []
    omitted_reversed: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "role": "user",
        "content": (
            "[ciel-runtime context guard: older conversation messages were omitted because the provider context "
            "budget would be exceeded. Large file contents and prior Write/Edit inputs were truncated. "
            "Use Read on specific files if exact old content is needed.]"
        ),
    }
    fixed_prefix = list(system_messages)
    if first_user is not None:
        fixed_prefix.append(first_user)
    fixed_prefix.append(summary)
    for message in reversed(non_system):
        if first_user is not None and message is first_user:
            continue
        candidate = fixed_prefix + list(reversed(preserved_tail + [message]))
        if runtime.estimate_tokens({"messages": candidate, "tools": tools}) <= budget_tokens:
            preserved_tail.append(message)
        else:
            omitted_reversed.append(message)
    if first_user is None:
        fixed_prefix = [*system_messages, summary]
    omitted_messages = list(reversed(omitted_reversed))
    summary["content"] = text.build_summary(omitted_messages, budget_tokens)
    compacted = fixed_prefix + list(reversed(preserved_tail))
    while runtime.estimate_tokens({"messages": compacted, "tools": tools}) > budget_tokens and preserved_tail:
        omitted_messages.append(preserved_tail.pop())
        summary["content"] = text.build_summary(omitted_messages, budget_tokens)
        compacted = fixed_prefix + list(reversed(preserved_tail))
    if runtime.estimate_tokens({"messages": compacted, "tools": tools}) > budget_tokens:
        latest_message = next((message for message in reversed(non_system) if isinstance(message, dict)), None)
        omitted_for_hard_cap = [
            message
            for message in messages
            if message not in system_messages and (latest_message is None or message is not latest_message)
        ]
        compacted = hard_cap(system_messages, latest_message, omitted_for_hard_cap)
    final_tokens = runtime.estimate_tokens({"messages": compacted, "tools": tools})
    runtime.log(
        "WARN",
        f"compacted ollama payload messages {len(messages)}->{len(compacted)} tokens {initial_tokens}->{final_tokens} budget={budget_tokens}",
    )
    chunk_count = text.chunk_count(omitted_messages, budget_tokens)
    if chunk_count and (provider or model):
        runtime.write_activity(
            provider or "provider",
            model,
            chunks=chunk_count,
            parallel_sessions=1,
            tokens=initial_tokens,
            final_tokens=final_tokens,
            budget=budget_tokens,
            omitted_messages=len(omitted_messages),
            retained_messages=len(compacted),
        )
    return compacted


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
