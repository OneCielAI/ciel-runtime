"""OpenAI-compatible chat forwarding application service."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class OpenAIForwardPolicy:
    compatibility_test_header: str
    provider_requires_streaming: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class OpenAIForwardRequest:
    update_tool_schema_registry: Callable[..., Any]
    normalize_thinking: Callable[..., Any]
    resolve_model: Callable[..., str]
    provider_upstream_model: Callable[..., str]
    body_with_advisor_tool: Callable[..., Any]
    advisor_provider_supported: Callable[..., bool]
    join_url: Callable[..., str]
    upstream_request_base: Callable[..., str]
    build_chat_request: Callable[..., Any]
    provider_headers: Callable[..., dict[str, str]]


@dataclass(frozen=True, slots=True)
class OpenAIForwardRateLimit:
    apply: Callable[..., tuple[float, int, int]]
    notice: Callable[..., str]
    estimate_tokens: Callable[..., int]
    request_timeout_seconds: Callable[..., float]


@dataclass(frozen=True, slots=True)
class OpenAIForwardAdvisor:
    model_enabled: Callable[..., bool]
    gate_possible_for_body: Callable[..., bool]
    gate_reason_for_body: Callable[..., str]
    refine_message: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIForwardStreaming:
    write_open_start: Callable[..., Any]
    write_blocks: Callable[..., int]
    open_with_retry: Callable[..., Any]
    post_json_with_retry: Callable[..., Any]
    stream_to_anthropic_sse: Callable[..., bool]
    write_open_stop: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIForwardResponse:
    mark_delivery_success: Callable[..., Any]
    mark_delivery_failed: Callable[..., Any]
    write_activity: Callable[..., Any]
    chat_to_anthropic: Callable[..., Any]
    remember_tool_uses: Callable[..., Any]
    prepend_text: Callable[..., Any]
    write_message: Callable[..., Any]
    write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIForwardServices:
    policy: OpenAIForwardPolicy
    request: OpenAIForwardRequest
    rate_limit: OpenAIForwardRateLimit
    advisor: OpenAIForwardAdvisor
    streaming: OpenAIForwardStreaming
    response: OpenAIForwardResponse
    log: Callable[[str, str], Any]


def forward_openai_compatible_chat(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    services: OpenAIForwardServices,
) -> None:
    """Forward one Anthropic-facing request through an OpenAI-compatible provider."""

    policy = services.policy
    request = services.request
    rate_limit = services.rate_limit
    advisor = services.advisor
    streaming = services.streaming
    response = services.response

    request.update_tool_schema_registry(body.get("tools"))
    body = request.normalize_thinking(provider, pcfg, body)
    model = request.resolve_model(provider, pcfg, body.get("model"))
    model = request.provider_upstream_model(provider, pcfg, model)
    original_body = body
    upstream_body = request.body_with_advisor_tool(body, pcfg) if request.advisor_provider_supported(provider) else body
    url = request.join_url(request.upstream_request_base(provider, pcfg), "/v1/chat/completions")
    waited, rpm_used, rpm_limit = rate_limit.apply(provider, pcfg, model)
    compatibility_test = str(handler.headers.get(policy.compatibility_test_header) or "").strip().lower() in ("1", "true", "yes", "on")
    stream_enabled = bool(pcfg.get("stream_enabled", True))
    stream = policy.provider_requires_streaming(provider, pcfg) or (bool(body.get("stream", stream_enabled)) and stream_enabled)
    if stream and advisor.model_enabled(pcfg) and request.advisor_provider_supported(provider):
        stream = False
        services.log("INFO", f"advisor tool enabled for {provider}; collecting this turn so advisor tool calls can be resolved internally")
    if stream and advisor.gate_possible_for_body(provider, pcfg, body):
        gate_reason = advisor.gate_reason_for_body(provider, pcfg, body)
        stream = False
        services.log("INFO", f"advisor gate enabled for {provider} reason={gate_reason}; collecting this turn before returning it to Claude Code")
    notice = rate_limit.notice(waited, rpm_used, rpm_limit, bool(pcfg.get("rate_limit_status", False)))
    if stream:
        req_body = request.build_chat_request(provider, model, upstream_body, pcfg, stream=True)
        req_tokens = rate_limit.estimate_tokens(req_body)
        req_bytes = len(json.dumps(req_body, ensure_ascii=False).encode("utf-8"))
        streaming.write_open_start(handler, model, input_tokens=req_tokens)
        index = 0
        if notice:
            index = streaming.write_blocks(handler, [{"type": "text", "text": notice}], index)
        try:
            def emit_retry_notice(text: str) -> None:
                nonlocal index
                index = streaming.write_blocks(handler, [{"type": "text", "text": text + "\n"}], index)

            upstream_response = streaming.open_with_retry(
                url,
                req_body,
                request.provider_headers(provider, pcfg),
                rate_limit.request_timeout_seconds(pcfg),
                provider,
                pcfg,
                model,
                emit_retry_notice,
                retry_rate_limits=not compatibility_test,
            )
            stream_ok = streaming.stream_to_anthropic_sse(
                handler,
                upstream_response,
                model,
                provider,
                source_body=original_body,
                start_index=index,
                word_chunking=bool(pcfg.get("stream_word_chunking", False)),
                input_tokens=req_tokens,
                input_bytes=req_bytes,
            )
            if stream_ok:
                response.mark_delivery_success(handler, "openai_stream_message_stop")
                response.write_activity("success", provider, model, tokens=req_tokens, bytes=req_bytes, stream=True)
            else:
                response.mark_delivery_failed(handler, "openai_stream_error")
        except RuntimeError as exc:
            response.mark_delivery_failed(handler, f"openai_stream_runtime_error:{type(exc).__name__}")
            streaming.write_blocks(handler, [{"type": "text", "text": f"Upstream error: {exc}"}], index)
            streaming.write_open_stop(handler)
            return
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            response.mark_delivery_failed(handler, f"openai_stream_error:{type(exc).__name__}")
            response.write_activity("error", provider, model, error=type(exc).__name__, stream=True)
            streaming.write_blocks(handler, [{"type": "text", "text": f"Upstream error: {message}"}], index)
            streaming.write_open_stop(handler)
            return
        return

    req_body = request.build_chat_request(provider, model, upstream_body, pcfg, stream=False)
    try:
        data = streaming.post_json_with_retry(
            url,
            req_body,
            request.provider_headers(provider, pcfg),
            rate_limit.request_timeout_seconds(pcfg),
            provider,
            pcfg,
            model,
            None,
            retry_rate_limits=not compatibility_test,
        )
    except RuntimeError as exc:
        response.write_json(handler, {"type": "error", "error": {"type": "upstream_error", "message": str(exc)}}, 500)
        return
    message = response.chat_to_anthropic(data, model, source_body=original_body)
    message = advisor.refine_message(provider, pcfg, original_body, message, model)
    response.remember_tool_uses(original_body, message)
    message = response.prepend_text(message, notice)
    response.write_message(handler, message, stream)
    response.mark_delivery_success(handler, "openai_json")
