from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


AUTO_CODEX_MAX_MAP_CHUNKS = 8


class AutomaticContextCompactionCompleted(Exception):
    """Carry a locally completed Codex checkpoint back to the Responses router."""

    def __init__(self, summary: str) -> None:
        super().__init__("automatic context compaction completed locally")
        self.summary = summary


@dataclass(frozen=True)
class ContextCompactionTransport:
    summary_output_tokens: Callable[[dict[str, Any], int], int]
    request_timeout: Callable[[dict[str, Any]], float]
    endpoint: Callable[[str, dict[str, Any], str], str]
    post_json: Callable[..., Any]
    headers: Callable[[str, dict[str, Any]], dict[str, str]]
    extract_text: Callable[[Any, str], str]


@dataclass(frozen=True)
class ContextCompactionWorkflow:
    segmented_mode: Callable[[dict[str, Any], str], str | None]
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
    apply_ollama_optional: Callable[..., dict[str, Any]]


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
        }
        request = services.projection.apply_ollama_optional(
            request,
            provider,
            model,
            provider_config,
            {},
            output_limit=max_tokens,
        )
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
        url = transport.endpoint(provider, provider_config, "anthropic_messages")
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
    if provider_config is None or not messages:
        return None
    workflow = services.workflow
    projection = services.projection
    instruction_index = workflow.instruction_index(messages)
    if instruction_index is None:
        return None
    compact_instruction = workflow.content_to_text(messages[instruction_index].get("content"))
    # Codex custom providers receive its local checkpoint as an ordinary
    # translated /v1/responses turn.  Unlike Claude's compact request, Codex
    # can otherwise keep forwarding a checkpoint that the upstream cannot fit.
    # Use the existing segmented reducer by default only for that captured
    # checkpoint prompt.  Operators can explicitly turn it
    # off, while all other clients retain the opt-in policy that avoids
    # multiplying provider usage.
    segmented_mode = workflow.segmented_mode(provider_config, compact_instruction)
    if segmented_mode is None:
        return None
    history = [
        message
        for index, message in enumerate(messages)
        if index != instruction_index and str(message.get("role") or "") != "system"
    ]
    system_messages = [message for message in messages if str(message.get("role") or "") == "system"]
    if not history:
        return None
    if not workflow.compaction_available(provider, provider_config):
        if segmented_mode == "auto_codex":
            raise AutomaticContextCompactionCompleted(
                _local_checkpoint_summary(
                    [projection.build_fallback_summary(history, budget_tokens)],
                    len(history),
                )
            )
        return None
    target_tokens = workflow.chunk_target_tokens(provider_config, budget_tokens)
    if (
        segmented_mode == "auto_codex"
        and provider_config.get("context_compact_chunk_tokens") is None
    ):
        target_tokens = max(
            target_tokens,
            min(262_144, max(8_192, (max(1, budget_tokens) * 3) // 4)),
        )
    chunks = workflow.split_messages(history, target_tokens)
    if not chunks:
        return None
    if (
        segmented_mode == "auto_codex"
        and len(chunks) > AUTO_CODEX_MAX_MAP_CHUNKS
    ):
        projection.log(
            "WARN",
            f"context_compact_auto_chunk_limit provider={provider} model={model} "
            f"chunks={len(chunks)} limit={AUTO_CODEX_MAX_MAP_CHUNKS}; "
            "completing checkpoint locally",
        )
        raise AutomaticContextCompactionCompleted(
            _local_checkpoint_summary(
                [projection.build_fallback_summary(history, budget_tokens)],
                len(history),
            )
        )
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
    summary_provider_config = provider_config
    if segmented_mode == "auto_codex":
        # The checkpoint request is already a recovery operation for an input
        # that cannot fit upstream.  Retrying the same auxiliary generation on
        # transport/capacity failures only multiplies load and delays the
        # deterministic guard that can compact the request locally.
        summary_provider_config = dict(provider_config)
        summary_provider_config["gateway_retries"] = 0
    for chunk_number, (start, chunk) in enumerate(chunks, start=1):
        prompt = projection.build_chunk_prompt(chunk, start, chunk_number, len(chunks))
        try:
            summary = workflow.request_summary(
                provider,
                model,
                summary_provider_config,
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
            if segmented_mode == "auto_codex":
                summaries.extend(
                    projection.build_fallback_summary(
                        remaining_chunk,
                        target_tokens,
                        start_index=remaining_start,
                    ).strip()
                    for remaining_start, remaining_chunk in chunks[chunk_number - 1 :]
                )
                projection.log(
                    "WARN",
                    f"context_compact_auto_fallback provider={provider} model={model} "
                    "reason=summary_request_failed; completing checkpoint locally",
                )
                raise AutomaticContextCompactionCompleted(
                    _local_checkpoint_summary(summaries, len(history))
                ) from exc
            summary = projection.build_fallback_summary(
                chunk, target_tokens, start_index=start
            )
        if not summary.strip():
            if segmented_mode == "auto_codex":
                summaries.extend(
                    projection.build_fallback_summary(
                        remaining_chunk,
                        target_tokens,
                        start_index=remaining_start,
                    ).strip()
                    for remaining_start, remaining_chunk in chunks[chunk_number - 1 :]
                )
                projection.log(
                    "WARN",
                    f"context_compact_auto_fallback provider={provider} model={model} "
                    "reason=empty_summary; completing checkpoint locally",
                )
                raise AutomaticContextCompactionCompleted(
                    _local_checkpoint_summary(summaries, len(history))
                )
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
    if segmented_mode == "auto_codex":
        local_summary = _local_checkpoint_summary(summaries, len(history))
        final_tokens = workflow.estimate_tokens(local_summary)
        projection.log(
            "WARN",
            f"context_compact_local_complete provider={provider} model={model} "
            f"chunks={len(chunks)} messages={len(history)} tokens={initial_tokens}->{final_tokens}",
        )
        workflow.write_activity(
            provider or "provider",
            model,
            chunks=len(chunks),
            parallel_sessions=parallel_sessions,
            tokens=initial_tokens,
            final_tokens=final_tokens,
            budget=budget_tokens,
            phase="local-complete",
            completed_chunks=len(chunks),
            retained_messages=1,
        )
        raise AutomaticContextCompactionCompleted(local_summary)
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


def _local_checkpoint_summary(summaries: list[str], source_message_count: int) -> str:
    parts = [
        "[ciel-runtime local context checkpoint]",
        f"Compacted {source_message_count} conversation messages into "
        f"{len(summaries)} deterministic segment summaries because provider "
        "summarization was unavailable.",
    ]
    parts.extend(
        f"## Segment {index}\n{summary.strip()}"
        for index, summary in enumerate(summaries, start=1)
        if summary.strip()
    )
    return "\n\n".join(parts)


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
