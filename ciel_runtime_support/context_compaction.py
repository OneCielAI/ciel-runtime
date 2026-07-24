from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ContextCompactionTransport:
    summary_output_tokens: Callable[[dict[str, Any], int], int]
    request_timeout: Callable[[dict[str, Any]], float]
    endpoint: Callable[[str, dict[str, Any], str], str]
    post_json: Callable[..., Any]
    headers: Callable[[str, dict[str, Any]], dict[str, str]]
    extract_text: Callable[[Any, str], str]
    native_compat_enabled: Callable[[str, dict[str, Any]], bool]
    native_anthropic_base: Callable[[str, dict[str, Any]], str]
    upstream_base: Callable[[str, dict[str, Any]], str]
    join_url: Callable[[str, str], str]


@dataclass(frozen=True)
class ContextCompactionWorkflow:
    parse_bool: Callable[..., bool]
    compaction_available: Callable[[str, dict[str, Any]], bool]
    instruction_index: Callable[[list[dict[str, Any]]], int | None]
    content_to_text: Callable[[Any], str]
    chunk_target_tokens: Callable[[dict[str, Any], int], int]
    split_messages: Callable[..., list[tuple[int, list[dict[str, Any]]]]]
    parallel_sessions: Callable[[dict[str, Any], int], int]
    write_activity: Callable[..., Any]
    estimate_tokens: Callable[[Any], int]
    request_summary: Callable[..., str]


@dataclass(frozen=True)
class ContextCompactionProjection:
    build_chunk_prompt: Callable[..., str]
    build_fallback_summary: Callable[..., str]
    build_reduce_prompt: Callable[..., str]
    log: Callable[[str, str], None]


@dataclass(frozen=True)
class ContextCompactionServices:
    transport: ContextCompactionTransport
    workflow: ContextCompactionWorkflow
    projection: ContextCompactionProjection
    map_system_prompt: str


def request_context_summary(
    provider: str,
    model: str,
    provider_config: dict[str, Any],
    prompt: str,
    services: ContextCompactionServices,
    *,
    wire: str,
    budget_tokens: int,
) -> str:
    transport = services.transport
    max_tokens = transport.summary_output_tokens(provider_config, budget_tokens)
    timeout = transport.request_timeout(provider_config)
    if wire == "ollama":
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": services.map_system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "options": {"num_predict": max_tokens},
        }
        if provider_config.get("keep_alive"):
            request["keep_alive"] = str(provider_config["keep_alive"])
        operation = "ollama_chat"
    elif wire == "openai":
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": services.map_system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": max_tokens,
        }
        operation = "openai_chat"
    else:
        request = {
            "model": model,
            "system": services.map_system_prompt,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,
        }
        base = (
            transport.native_anthropic_base(provider, provider_config)
            if transport.native_compat_enabled(provider, provider_config)
            else transport.upstream_base(provider, provider_config)
        )
        url = transport.join_url(base, "/v1/messages")
        return _post_summary(
            provider, model, provider_config, request, url, "anthropic", timeout, services
        )
    url = transport.endpoint(provider, provider_config, operation)
    return _post_summary(
        provider, model, provider_config, request, url, wire, timeout, services
    )


def _post_summary(
    provider: str,
    model: str,
    provider_config: dict[str, Any],
    request: dict[str, Any],
    url: str,
    wire: str,
    timeout: float,
    services: ContextCompactionServices,
) -> str:
    transport = services.transport
    data = transport.post_json(
        url,
        request,
        transport.headers(provider, provider_config),
        timeout,
        provider,
        provider_config,
        model,
        # Summarization is a non-idempotent generation request.  A retry can
        # consume quota even when the provider completed the first attempt but
        # the response was lost, so never multiply it implicitly.
        retry_rate_limits=False,
    )
    return transport.extract_text(data, wire)


def build_llm_compacted_messages(
    provider: str,
    model: str,
    provider_config: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    budget_tokens: int,
    services: ContextCompactionServices,
    *,
    wire: str,
) -> list[dict[str, Any]] | None:
    if not provider_config or not messages:
        return None
    workflow = services.workflow
    projection = services.projection
    # Claude Code already performs the final compact generation.  The former
    # default ran one extra generation per segment before forwarding that
    # request (N map calls + one reduce call), which multiplied provider quota.
    # Keep segmented LLM compaction as an explicit compatibility opt-in only.
    if workflow.parse_bool(provider_config.get("context_compact_llm"), default=False) is False:
        return None
    if not workflow.compaction_available(provider, provider_config):
        return None
    instruction_index = workflow.instruction_index(messages)
    if instruction_index is None:
        return None
    compact_instruction = workflow.content_to_text(messages[instruction_index].get("content"))
    history = [
        message
        for index, message in enumerate(messages)
        if index != instruction_index and str(message.get("role") or "") != "system"
    ]
    system_messages = [message for message in messages if str(message.get("role") or "") == "system"]
    if not history:
        return None
    target_tokens = workflow.chunk_target_tokens(provider_config, budget_tokens)
    chunks = workflow.split_messages(history, target_tokens)
    if not chunks:
        return None
    parallel_sessions = workflow.parallel_sessions(provider_config, len(chunks))
    initial_tokens = workflow.estimate_tokens({"messages": messages})
    _write_activity(
        provider,
        model,
        len(chunks),
        parallel_sessions,
        initial_tokens,
        0,
        budget_tokens,
        "map",
        0,
        workflow,
    )
    summaries: list[str] = []
    for chunk_number, (start, chunk) in enumerate(chunks, start=1):
        prompt = projection.build_chunk_prompt(chunk, start, chunk_number, len(chunks))
        try:
            summary = workflow.request_summary(
                provider,
                model,
                provider_config,
                prompt,
                wire=wire,
                budget_tokens=budget_tokens,
            )
        except Exception as exc:
            projection.log(
                "WARN",
                f"context_compact_chunk_failed provider={provider} model={model} "
                f"chunk={chunk_number}/{len(chunks)} error={type(exc).__name__}: {exc}",
            )
            summary = projection.build_fallback_summary(
                chunk, target_tokens, start_index=start
            )
        if not summary.strip():
            summary = projection.build_fallback_summary(
                chunk, target_tokens, start_index=start
            )
        summaries.append(summary.strip())
        _write_activity(
            provider,
            model,
            len(chunks),
            parallel_sessions,
            initial_tokens,
            sum(workflow.estimate_tokens(item) for item in summaries),
            budget_tokens,
            "map",
            chunk_number,
            workflow,
        )
    reduce_prompt = projection.build_reduce_prompt(
        summaries,
        compact_instruction,
        budget_tokens=budget_tokens,
        source_message_count=len(history),
    )
    output = [*system_messages, {"role": "user", "content": reduce_prompt}]
    final_tokens = workflow.estimate_tokens({"messages": output})
    projection.log(
        "WARN",
        f"context_compact_map_reduce provider={provider} model={model} chunks={len(chunks)} "
        f"messages {len(messages)}->{len(output)} tokens {initial_tokens}->{final_tokens} "
        f"budget={budget_tokens}",
    )
    workflow.write_activity(
        provider or "provider",
        model,
        chunks=len(chunks),
        parallel_sessions=parallel_sessions,
        tokens=initial_tokens,
        final_tokens=final_tokens,
        budget=budget_tokens,
        phase="reduce",
        completed_chunks=len(chunks),
        retained_messages=len(output),
    )
    return output


def _write_activity(
    provider: str,
    model: str,
    chunks: int,
    parallel_sessions: int,
    tokens: int,
    final_tokens: int,
    budget: int,
    phase: str,
    completed_chunks: int,
    workflow: ContextCompactionWorkflow,
) -> None:
    workflow.write_activity(
        provider or "provider",
        model,
        chunks=chunks,
        parallel_sessions=parallel_sessions,
        tokens=tokens,
        final_tokens=final_tokens,
        budget=budget,
        phase=phase,
        completed_chunks=completed_chunks,
    )
