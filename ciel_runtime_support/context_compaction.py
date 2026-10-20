from __future__ import annotations

import time
import re
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable

from .context_summary_policy import (
    ContextSummaryFragment, compact_instruction_kind, native_compact_text, semantic_payload_cost,
)
from .upstream_error_policy import UpstreamFailure


MAX_MAP_CHUNKS = 256
MAX_SUMMARY_CALLS = 512
MAX_REDUCE_ROUNDS = 12
COMPACTION_TIMEOUT_SECONDS = 900.0


class AutomaticContextCompactionCompleted(Exception):
    """Legacy codec carrier; semantic work now returns a real final generation."""

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
    split_messages: Callable[..., list[tuple[int, list[dict[str, Any] | ContextSummaryFragment]]]]
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


class ContextCompactionFailed(RuntimeError):
    """An incomplete semantic checkpoint must never become a guard success."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"semantic context compaction failed: {reason}")
        self.reason = reason


def _summary_request(
    provider: str, model: str, config: dict[str, Any], prompt: str,
    services: ContextCompactionServices, wire: str, budget: int,
) -> tuple[dict[str, Any], str]:
    maximum = min(
        max(32, services.transport.summary_output_tokens(config, budget)),
        max(32, budget // 8),
    )
    messages = [
        {"role": "system", "content": services.map_system_prompt},
        {"role": "user", "content": prompt},
    ]
    request: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if wire == "ollama":
        request = services.projection.apply_ollama_optional(
            request, provider, model, config, {}, output_limit=maximum,
        )
        return request, "ollama_chat"
    if wire == "responses":
        return {
            "model": model,
            "instructions": services.map_system_prompt,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
            "max_output_tokens": maximum,
            "stream": False,
        }, "openai_responses"
    request["max_tokens"] = maximum
    if wire == "openai":
        return request, "openai_chat"
    request["system"] = services.map_system_prompt
    request["messages"] = messages[1:]
    return request, "anthropic_messages"


def _request_cost(
    provider: str, model: str, config: dict[str, Any], prompt: str,
    services: ContextCompactionServices, wire: str, budget: int,
) -> int:
    request, _operation = _summary_request(provider, model, config, prompt, services, wire, budget)
    output = min(max(32, services.transport.summary_output_tokens(config, budget)), max(32, budget // 8))
    # This UTF-8 envelope is a local safety bound, not tokenizer authority.
    return semantic_payload_cost(request, services.workflow.estimate_tokens) + output + 512


def request_context_summary(
    provider: str, model: str, provider_config: dict[str, Any], prompt: str,
    services: ContextCompactionServices, *, wire: str, budget_tokens: int,
) -> str:
    if _request_cost(provider, model, provider_config, prompt, services, wire, budget_tokens) > budget_tokens:
        raise ContextCompactionFailed("summary_request_exceeds_budget")
    request, operation = _summary_request(
        provider, model, provider_config, prompt, services, wire, budget_tokens
    )
    transport = services.transport
    timeout = min(
        transport.request_timeout(provider_config),
        float(provider_config.get("_context_compact_timeout_seconds", 120.0)),
        120.0,
    )
    config = {**provider_config, "gateway_retries": 0}
    headers = transport.headers(provider, config)
    if wire == "responses":
        headers = {name: value for name, value in headers.items()
                   if str(name).casefold() != "anthropic-version"}
    try:
        data = transport.post_json(
            transport.endpoint(provider, config, operation), request,
            headers, timeout, provider, config, model,
            # Generation is non-idempotent: never replay after an ambiguous failure.
            retry_rate_limits=False,
        )
    except urllib.error.HTTPError as exc:
        raise UpstreamFailure.from_http_error(provider, model, exc) from exc
    if wire == "responses":
        if not isinstance(data, dict):
            raise ContextCompactionFailed("invalid_summary_response")
        if data.get("error"):
            raise UpstreamFailure.from_payload(provider, model, data, status=200, source="json_200")
        if data.get("status") != "completed":
            raise ContextCompactionFailed("incomplete_summary_response")
        output = data.get("output")
        if not isinstance(output, list):
            raise ContextCompactionFailed("invalid_summary_output")
        for item in output:
            if not isinstance(item, dict) or item.get("type") not in {"message", "reasoning"}:
                raise ContextCompactionFailed("invalid_summary_output")
            if item.get("status") not in {None, "completed"}:
                raise ContextCompactionFailed("incomplete_summary_response")
            if item.get("type") == "message":
                content = item.get("content")
                if not isinstance(content, list) or any(
                    not isinstance(block, dict) or block.get("type") != "output_text"
                    or not isinstance(block.get("text"), str) for block in content
                ):
                    raise ContextCompactionFailed("invalid_summary_output")
    if isinstance(data, dict):
        choice = (data.get("choices") or [{}])[0] if wire == "openai" else {}
        reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        if (reason in {"length", "content_filter", "tool_calls"}
            or data.get("stop_reason") in {"max_tokens", "refusal", "tool_use"}
            or data.get("done_reason") == "length"
            or data.get("status") in {"incomplete", "failed", "cancelled"}
            or data.get("done") is False):
            raise ContextCompactionFailed("truncated_summary")
        if data.get("error"):
            raise UpstreamFailure.from_payload(provider, model, data, status=200, source="json_200")
    text = transport.extract_text(data, wire)
    if not isinstance(text, str) or not text.strip():
        raise ContextCompactionFailed("empty_summary")
    return text.strip()


@dataclass
class _SummaryRun:
    provider: str
    model: str
    config: dict[str, Any]
    services: ContextCompactionServices
    wire: str
    budget: int
    deadline: float
    calls: int = 0
    completed_chunks: int = 0
    total_chunks: int = 0

    def fits(self, prompt: str) -> bool:
        return _request_cost(
            self.provider, self.model, self.config, prompt,
            self.services, self.wire, self.budget,
        ) <= self.budget

    def generate(self, prompt: str) -> str:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ContextCompactionFailed("deadline_exceeded")
        if self.calls >= MAX_SUMMARY_CALLS:
            raise ContextCompactionFailed("summary_call_limit")
        if not self.fits(prompt):
            raise ContextCompactionFailed("summary_request_exceeds_budget")
        self.calls += 1
        config = {**self.config, "_context_compact_timeout_seconds": min(remaining, 120.0)}
        summary = self.services.workflow.request_summary(
            self.provider, self.model, config, prompt, wire=self.wire, budget_tokens=self.budget,
        )
        if time.monotonic() > self.deadline:
            raise ContextCompactionFailed("deadline_exceeded")
        if not isinstance(summary, str) or not summary.strip():
            raise ContextCompactionFailed("empty_summary")
        # Finite output guard even for transports without a proven output knob.
        maximum = min(self.services.transport.summary_output_tokens(config, self.budget), max(32, self.budget // 8))
        if len(summary.encode("utf-8")) > max(1024, maximum * 32):
            raise ContextCompactionFailed("summary_output_limit")
        return summary.strip()



def _no_output_context_rejection(error: BaseException) -> bool:
    if not isinstance(error, UpstreamFailure):
        return False
    if error.source != "http_status" or error.upstream_status not in {400, 413}:
        return False
    if error.output_started or error.response_id:
        return False
    return error.error_type in {"context_length_exceeded", "context_window_exceeded"} or (
        error.error_type == "invalid_request_error" and bool(re.match(
            r"^prompt is too long: [0-9]+ tokens > [0-9]+ maximum", error.message.casefold()
        ))
    )


def _split_rejected_chunk(
    chunk: list[dict[str, Any] | ContextSummaryFragment], start: int,
) -> list[tuple[int, list[dict[str, Any] | ContextSummaryFragment]]]:
    if len(chunk) > 1:
        middle = len(chunk) // 2
        right_start = chunk[middle].source_index if isinstance(chunk[middle], ContextSummaryFragment) else start + middle
        return [(start, chunk[:middle]), (right_start, chunk[middle:])]
    if len(chunk) != 1 or not isinstance(chunk[0], ContextSummaryFragment) or len(chunk[0].text) < 2:
        raise ContextCompactionFailed("rejected_chunk_cannot_split")
    fragment = chunk[0]
    middle = len(fragment.text) // 2
    left = ContextSummaryFragment(fragment.source_index, fragment.start,
        fragment.start + middle, fragment.source_chars, fragment.text[:middle], fragment.role)
    right = ContextSummaryFragment(fragment.source_index, fragment.start + middle,
        fragment.end, fragment.source_chars, fragment.text[middle:], fragment.role)
    return [(fragment.source_index, [left]), (fragment.source_index, [right])]


def _map_summaries(chunks: list[Any], run: _SummaryRun, initial_tokens: int) -> list[str]:
    pending = [(start, chunk, 0) for start, chunk in chunks]
    summaries: list[str] = []
    total = len(chunks)
    run.total_chunks = total
    while pending:
        start, chunk, depth = pending.pop(0)
        prompt = run.services.projection.build_chunk_prompt(chunk, start, len(summaries) + 1, total)
        try:
            summary = run.generate(prompt)
        except Exception as exc:
            if not _no_output_context_rejection(exc) or depth >= 4:
                raise
            if total >= MAX_MAP_CHUNKS:
                raise ContextCompactionFailed("map_chunk_limit") from exc
            smaller = _split_rejected_chunk(chunk, start)
            total += len(smaller) - 1
            run.total_chunks = total
            pending[0:0] = [(offset, parts, depth + 1) for offset, parts in smaller]
            continue
        summaries.append(summary)
        run.completed_chunks = len(summaries)
        _write_activity(run.provider, run.model, total, initial_tokens,
            sum(run.services.workflow.estimate_tokens(item) for item in summaries),
            run.budget, "map", len(summaries), run.services.workflow)
    return summaries

def _reduce_summaries(
    summaries: list[str], instruction: str, source_count: int,
    system_messages: list[dict[str, Any]], run: _SummaryRun,
) -> list[dict[str, Any]]:
    projection = run.services.projection
    estimate = run.services.workflow.estimate_tokens

    def prompt(parts: list[str]) -> str:
        return projection.build_reduce_prompt(
            parts, instruction, budget_tokens=run.budget, source_message_count=source_count,
        )

    for round_number in range(MAX_REDUCE_ROUNDS + 1):
        output = [*system_messages, {"role": "user", "content": prompt(summaries)}]
        if semantic_payload_cost({"messages": output}, estimate) + 512 <= run.budget:
            return output
        if round_number >= MAX_REDUCE_ROUNDS:
            raise ContextCompactionFailed("reduce_round_limit")
        parts: list[str] = []
        for summary in summaries:
            if run.fits(prompt([summary])):
                parts.append(summary)
                continue
            # A provider without a proven output knob may emit one large map
            # summary. Split its complete text, never an excerpt, for reduction.
            offset = 0
            while offset < len(summary):
                low, high, end = offset + 1, len(summary), offset
                while low <= high:
                    middle = (low + high) // 2
                    if run.fits(prompt([summary[offset:middle]])):
                        end, low = middle, middle + 1
                    else:
                        high = middle - 1
                if end == offset or len(parts) >= MAX_SUMMARY_CALLS:
                    raise ContextCompactionFailed("reduce_fragment_exceeds_budget")
                parts.append(summary[offset:end])
                offset = end
        groups: list[list[str]] = []
        group: list[str] = []
        for summary in parts:
            if group and not run.fits(prompt([*group, summary])):
                groups.append(group)
                group = []
            if not run.fits(prompt([*group, summary])):
                raise ContextCompactionFailed("reduce_fragment_exceeds_budget")
            group.append(summary)
        if group:
            groups.append(group)
        reduced = [run.generate(prompt(group)) for group in groups]
        if sum(semantic_payload_cost(item, estimate) for item in reduced) >= sum(
            semantic_payload_cost(item, estimate) for item in summaries
        ):
            raise ContextCompactionFailed("reduce_did_not_contract")
        summaries = reduced
        run.services.workflow.write_activity(
            run.provider or "provider", run.model, phase="reduce",
            reduce_round=round_number + 1, summary_calls=run.calls,
            budget=run.budget, final_tokens=estimate({"messages": output}),
        )
    raise ContextCompactionFailed("reduce_round_limit")


def build_llm_compacted_messages(
    provider: str, model: str, provider_config: dict[str, Any] | None,
    messages: list[dict[str, Any]], budget_tokens: int,
    services: ContextCompactionServices, *, wire: str,
) -> list[dict[str, Any]] | None:
    if provider_config is None or not messages:
        return None
    workflow = services.workflow
    projection = services.projection
    instruction_index = workflow.instruction_index(messages)
    if instruction_index is None:
        return None
    content = messages[instruction_index].get("content")
    native_instruction = native_compact_text(content)
    instruction = native_instruction if compact_instruction_kind(native_instruction) else workflow.content_to_text(content)
    if workflow.segmented_mode(provider_config, instruction) is None:
        return None
    history = []
    for index, message in enumerate(messages):
        if str(message.get("role") or "") == "system":
            continue
        if index != instruction_index:
            history.append(message)
        elif isinstance(content, list) and compact_instruction_kind(native_instruction):
            # Claude auto compact appends its instruction to the last real user
            # message. Exclude only that block, never the earlier user content.
            kept = list(content)
            for offset in range(len(kept) - 1, -1, -1):
                block = kept[offset]
                if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                    del kept[offset]
                    break
            if kept:
                history.append({**message, "content": kept})
    systems = [message for message in messages if str(message.get("role") or "") == "system"]
    if not history:
        return None
    initial_tokens = workflow.estimate_tokens({"messages": messages})
    completed = 0
    chunks: list[Any] = []
    config = {**provider_config, "gateway_retries": 0}
    run = _SummaryRun(provider, model, config, services, wire, budget_tokens,
                      time.monotonic() + COMPACTION_TIMEOUT_SECONDS)
    try:
        minimum_final = projection.build_reduce_prompt(
            [""], instruction, budget_tokens=budget_tokens, source_message_count=len(history),
        )
        if semantic_payload_cost({"messages": [*systems, {"role": "user", "content": minimum_final}]}, workflow.estimate_tokens) + 512 > budget_tokens:
            raise ContextCompactionFailed("final_system_instruction_exceeds_budget")
        if not workflow.compaction_available(provider, provider_config):
            raise ContextCompactionFailed("provider_unavailable")
        overhead = _request_cost(provider, model, config, "", services, wire, budget_tokens)
        target = min(workflow.chunk_target_tokens(config, budget_tokens), budget_tokens - overhead - 512)
        if target < 256:
            raise ContextCompactionFailed("insufficient_summary_budget")
        chunks = workflow.split_messages(history, target)
        if not chunks:
            raise ContextCompactionFailed("no_source_chunks")
        if len(chunks) > MAX_MAP_CHUNKS:
            raise ContextCompactionFailed("map_chunk_limit")
        # Preflight the complete map before starting any billable generation.
        prompts = [
            projection.build_chunk_prompt(chunk, start, number, len(chunks))
            for number, (start, chunk) in enumerate(chunks, 1)
        ]
        if not all(run.fits(prompt) for prompt in prompts):
            raise ContextCompactionFailed("map_request_exceeds_budget")
        _write_activity(provider, model, len(chunks), initial_tokens, 0, budget_tokens, "map", 0, workflow)
        summaries = _map_summaries(chunks, run, initial_tokens)
        completed = len(summaries)
        output = _reduce_summaries(summaries, instruction, len(history), systems, run)
    except BaseException as exc:
        workflow.write_activity(
            provider or "provider", model, phase="failed", chunks=run.total_chunks or len(chunks),
            completed_chunks=run.completed_chunks, summary_calls=run.calls,
            reason=exc.reason if isinstance(exc, ContextCompactionFailed) else type(exc).__name__,
            tokens=initial_tokens, budget=budget_tokens,
        )
        projection.log("WARN", f"context_compact_failed provider={provider} model={model} "
                       f"completed_chunks={run.completed_chunks}/{run.total_chunks or len(chunks)} error={type(exc).__name__}")
        raise
    final_tokens = workflow.estimate_tokens({"messages": output})
    projection.log("WARN", f"context_compact_map_reduce provider={provider} model={model} "
                   f"chunks={run.total_chunks} summary_calls={run.calls} "
                   f"tokens={initial_tokens}->{final_tokens} budget={budget_tokens}")
    # Not completed yet: the existing final wire generation owns real usage and
    # the native CLI owns persistence of its checkpoint. Never mint local success.
    workflow.write_activity(
        provider or "provider", model, chunks=run.total_chunks, parallel_sessions=1,
        tokens=initial_tokens, final_tokens=final_tokens, budget=budget_tokens,
        phase="reduce", completed_chunks=completed, retained_messages=len(output),
        summary_calls=run.calls,
    )
    return output


def _write_activity(
    provider: str, model: str, chunks: int, tokens: int, final_tokens: int,
    budget: int, phase: str, completed_chunks: int,
    workflow: ContextCompactionWorkflow,
) -> None:
    workflow.write_activity(
        provider or "provider", model, chunks=chunks, parallel_sessions=1,
        tokens=tokens, final_tokens=final_tokens, budget=budget,
        phase=phase, completed_chunks=completed_chunks,
    )
