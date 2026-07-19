"""Ollama upstream forwarding application service."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
import json
from typing import Any, Callable
import urllib.error
import urllib.request


@dataclass(frozen=True, slots=True)
class OllamaForwardConstants:
    client_disconnected_error: type[Exception]
    compatibility_test_header: str
    upstream_retry_http_codes: frozenset[int] | set[int] | tuple[int, ...]


@dataclass(frozen=True, slots=True)
class OllamaForwardRequest:
    normalize_thinking: Callable[..., Any]
    ollama_chat_request: Callable[..., Any]
    provider_endpoint: Callable[..., Any]
    provider_headers: Callable[..., Any]
    provider_urlopen: Callable[..., Any]
    request_timeout_seconds: Callable[..., Any]
    resolve_requested_model: Callable[..., Any]
    set_stream_read_timeout: Callable[..., Any]
    stream_idle_timeout_seconds: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaForwardRateLimit:
    apply_router_rate_limit: Callable[..., Any]
    configured_gateway_retries: Callable[..., Any]
    effective_rpm: Callable[..., Any]
    learn_headers: Callable[..., Any]
    notice: Callable[..., Any]
    register_backoff: Callable[..., Any]
    retry_wait_seconds: Callable[..., Any]
    retryable_upstream_exception: Callable[..., Any]
    sleep_until_or_client_disconnect: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaForwardStreaming:
    client_connection_closed: Callable[..., Any]
    iter_upstream_lines: Callable[..., Any]
    log: Callable[..., Any]
    stream_to_anthropic_sse: Callable[..., Any]
    write_router_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaForwardAdvisor:
    body_with_tool: Callable[..., Any]
    estimate_tokens: Callable[..., Any]
    gate_possible: Callable[..., Any]
    gate_reason: Callable[..., Any]
    model_enabled: Callable[..., Any]
    prepend_text: Callable[..., Any]
    provider_supported: Callable[..., Any]
    refine_message: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaForwardResponse:
    context_error_limit: Callable[..., Any]
    context_retry_config: Callable[..., Any]
    mark_pending_delivery_success: Callable[..., Any]
    ollama_chat_to_anthropic: Callable[..., Any]
    remember_injected_tool_uses: Callable[..., Any]
    update_tool_schema_registry: Callable[..., Any]
    upstream_http_error_message: Callable[..., Any]
    write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OllamaForwardServices:
    constants: OllamaForwardConstants
    request: OllamaForwardRequest
    rate_limit: OllamaForwardRateLimit
    streaming: OllamaForwardStreaming
    advisor: OllamaForwardAdvisor
    response: OllamaForwardResponse


def forward_ollama_api_chat(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    services: OllamaForwardServices,
) -> None:
    constants = services.constants
    request = services.request
    rate_limit = services.rate_limit
    streaming = services.streaming
    advisor = services.advisor
    response = services.response
    COMPATIBILITY_TEST_HEADER = constants.compatibility_test_header
    UPSTREAM_RETRY_HTTP_CODES = constants.upstream_retry_http_codes
    UpstreamClientDisconnected = constants.client_disconnected_error
    normalize_thinking_for_non_anthropic_provider = request.normalize_thinking
    ollama_chat_request = request.ollama_chat_request
    ollama_request_timeout_seconds = request.request_timeout_seconds
    provider_endpoint = request.provider_endpoint
    provider_headers = request.provider_headers
    provider_stream_idle_timeout_seconds = request.stream_idle_timeout_seconds
    provider_urlopen = request.provider_urlopen
    resolve_requested_model = request.resolve_requested_model
    set_upstream_stream_read_timeout = request.set_stream_read_timeout
    apply_router_rate_limit = rate_limit.apply_router_rate_limit
    configured_gateway_retries = rate_limit.configured_gateway_retries
    learn_router_rate_limit_headers = rate_limit.learn_headers
    rate_limit_notice = rate_limit.notice
    register_router_rate_limit_backoff = rate_limit.register_backoff
    retryable_upstream_exception = rate_limit.retryable_upstream_exception
    router_rate_limit_effective_rpm = rate_limit.effective_rpm
    sleep_until_or_client_disconnect = rate_limit.sleep_until_or_client_disconnect
    upstream_retry_wait_seconds = rate_limit.retry_wait_seconds
    _ollama_stream_to_anthropic_sse = streaming.stream_to_anthropic_sse
    iter_upstream_lines_until_client_disconnect = streaming.iter_upstream_lines
    router_client_connection_closed = streaming.client_connection_closed
    router_log = streaming.log
    write_router_activity = streaming.write_router_activity
    advisor_gate_possible_for_body = advisor.gate_possible
    advisor_gate_reason_for_body = advisor.gate_reason
    advisor_model_enabled = advisor.model_enabled
    advisor_provider_supported = advisor.provider_supported
    body_with_advisor_tool = advisor.body_with_tool
    estimate_tokens = advisor.estimate_tokens
    prepend_anthropic_text = advisor.prepend_text
    refine_message_with_advisor = advisor.refine_message
    _update_tool_schema_registry = response.update_tool_schema_registry
    mark_pending_channel_delivery_success = response.mark_pending_delivery_success
    ollama_chat_to_anthropic = response.ollama_chat_to_anthropic
    ollama_context_error_limit = response.context_error_limit
    ollama_context_retry_config = response.context_retry_config
    remember_channel_injected_tool_uses = response.remember_injected_tool_uses
    upstream_http_error_message = response.upstream_http_error_message
    write_json = response.write_json
    _update_tool_schema_registry(body.get("tools"))
    body = normalize_thinking_for_non_anthropic_provider(provider, pcfg, body)
    model = resolve_requested_model(provider, pcfg, body.get("model"))
    compatibility_test = str(handler.headers.get(COMPATIBILITY_TEST_HEADER) or "").strip().lower() in ("1", "true", "yes", "on")
    original_body = body
    upstream_body = body_with_advisor_tool(body, pcfg) if advisor_provider_supported(provider) else body
    stream_requested = body.get("stream", True)
    if not bool(pcfg.get("stream_enabled", True)):
        stream_requested = False
    if stream_requested and advisor_model_enabled(pcfg) and advisor_provider_supported(provider):
        stream_requested = False
        router_log("INFO", "advisor tool enabled; collecting this turn so advisor tool calls can be resolved internally")
    if stream_requested and advisor_gate_possible_for_body(provider, pcfg, body):
        gate_reason = advisor_gate_reason_for_body(provider, pcfg, body)
        stream_requested = False
        router_log("INFO", f"advisor gate enabled reason={gate_reason}; collecting this turn before returning it to Claude Code")
    word_chunking = bool(pcfg.get("stream_word_chunking", False))
    req_body = ollama_chat_request(model, upstream_body, pcfg, stream=stream_requested, provider=provider)
    headers = provider_headers(provider, pcfg)
    url = provider_endpoint(provider, pcfg, "ollama_chat")
    if compatibility_test:
        waited, rpm_used, rpm_limit = 0.0, 0, router_rate_limit_effective_rpm(provider, pcfg, model)
    else:
        waited, rpm_used, rpm_limit = apply_router_rate_limit(provider, pcfg, model)
    rpm_status = bool(pcfg.get("rate_limit_status", False))
    if stream_requested:
        # Stream Ollama response through as Anthropic SSE
        data_bytes = json.dumps(req_body).encode("utf-8")
        req_tokens = estimate_tokens(req_body)
        req_bytes = len(data_bytes)
        gateway_retries = 0 if compatibility_test else configured_gateway_retries(pcfg)
        max_attempts = max(1, gateway_retries + 1)
        loop_attempts = max_attempts + 1
        context_retry_used = False
        resp = None
        stream_idle_timeout = provider_stream_idle_timeout_seconds(pcfg)
        for attempt in range(loop_attempts):
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            try:
                write_router_activity(
                    "request",
                    provider,
                    model,
                    attempt=attempt + 1,
                    total=max_attempts,
                    tokens=req_tokens,
                    bytes=req_bytes,
                    timeout=ollama_request_timeout_seconds(pcfg),
                    stream=True,
                )
                router_log("INFO", f"ollama_stream_request provider={provider} model={model} attempt={attempt + 1}/{max_attempts} tokens={req_tokens} bytes={req_bytes}")
                resp = provider_urlopen(req, timeout=ollama_request_timeout_seconds(pcfg), provider=provider, pcfg=pcfg)
                set_upstream_stream_read_timeout(resp, stream_idle_timeout)
                learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
                break
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="ignore")
                learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
                context_limit = ollama_context_error_limit(raw)
                if exc.code == 400 and context_limit and not context_retry_used:
                    context_retry_used = True
                    retry_pcfg = ollama_context_retry_config(pcfg, context_limit)
                    req_body = ollama_chat_request(model, upstream_body, retry_pcfg, stream=stream_requested, provider=provider)
                    data_bytes = json.dumps(req_body).encode("utf-8")
                    req_tokens = estimate_tokens(req_body)
                    req_bytes = len(data_bytes)
                    write_router_activity(
                        "retry",
                        provider,
                        model,
                        attempt=attempt + 1,
                        total=max_attempts,
                        code=exc.code,
                        reason="context_compact_retry",
                        context_limit=context_limit,
                        tokens=req_tokens,
                        bytes=req_bytes,
                        stream=True,
                    )
                    router_log(
                        "WARN",
                        f"ollama_stream_context_retry provider={provider} model={model} n_ctx={context_limit} tokens={req_tokens} bytes={req_bytes}",
                    )
                    continue
                if exc.code == 429 and attempt + 1 < max_attempts:
                    retry_no = attempt + 1
                    wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                    write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=req_tokens, bytes=req_bytes, stream=True)
                    router_log("WARN", f"ollama_stream_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={req_tokens} bytes={req_bytes}")
                    if not sleep_until_or_client_disconnect(handler, wait):
                        write_router_activity("cancel", provider, model, stage="rate_limit_retry_wait", tokens=req_tokens, bytes=req_bytes, stream=True)
                        router_log("WARN", f"ollama_stream_cancelled_before_rate_limit_retry provider={provider} model={model} tokens={req_tokens} bytes={req_bytes}")
                        return
                    continue
                if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                    retry_no = attempt + 1
                    write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=req_tokens, bytes=req_bytes, stream=True)
                    router_log("WARN", f"ollama_stream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} tokens={req_tokens} bytes={req_bytes}")
                    if not sleep_until_or_client_disconnect(handler, upstream_retry_wait_seconds(retry_no)):
                        write_router_activity("cancel", provider, model, stage="http_retry_wait", tokens=req_tokens, bytes=req_bytes, stream=True)
                        router_log("WARN", f"ollama_stream_cancelled_before_http_retry provider={provider} model={model} tokens={req_tokens} bytes={req_bytes}")
                        return
                    continue
                if router_client_connection_closed(handler):
                    write_router_activity("cancel", provider, model, stage="http_error", code=exc.code, tokens=req_tokens, bytes=req_bytes, stream=True)
                    router_log("WARN", f"ollama_stream_client_gone_before_error_response provider={provider} model={model} code={exc.code}")
                    return
                write_router_activity("error", provider, model, code=exc.code, tokens=req_tokens, bytes=req_bytes, stream=True)
                write_json(
                    handler,
                    {"type": "error", "error": {"type": "upstream_error", "message": upstream_http_error_message(exc, raw)}},
                    exc.code,
                )
                return
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                    retry_no = attempt + 1
                    write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes, stream=True)
                    router_log("WARN", f"ollama_stream_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={req_tokens} bytes={req_bytes}")
                    if not sleep_until_or_client_disconnect(handler, upstream_retry_wait_seconds(retry_no)):
                        write_router_activity("cancel", provider, model, stage="exception_retry_wait", error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes, stream=True)
                        router_log("WARN", f"ollama_stream_cancelled_before_exception_retry provider={provider} model={model} error={type(exc).__name__}")
                        return
                    continue
                if router_client_connection_closed(handler):
                    write_router_activity("cancel", provider, model, stage="exception_error", error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes, stream=True)
                    router_log("WARN", f"ollama_stream_client_gone_before_exception_response provider={provider} model={model} error={type(exc).__name__}")
                    return
                write_router_activity("error", provider, model, error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes, stream=True)
                write_json(
                    handler,
                    {"type": "error", "error": {"type": "upstream_error", "message": f"{type(exc).__name__}: {exc}"}},
                    504 if retryable_upstream_exception(exc) else 502,
                )
                return
        if resp is None:
            write_router_activity("error", provider, model, tokens=req_tokens, bytes=req_bytes, stream=True)
            write_json(
                handler,
                {"type": "error", "error": {"type": "upstream_error", "message": "upstream stream request failed"}},
                504,
            )
            return
        # Check if Claude Code requested SSE streaming
        accept = handler.headers.get("accept", "")
        if "text/event-stream" in accept or stream_requested:
            _ollama_stream_to_anthropic_sse(handler, resp, model, word_chunking=word_chunking, provider=provider, source_body=original_body, idle_timeout=stream_idle_timeout)
        else:
            # Non-SSE client but streaming from Ollama: collect full response
            chunks = []
            try:
                for line in iter_upstream_lines_until_client_disconnect(handler, resp, stream_idle_timeout):
                    chunks.append(line)
            except UpstreamClientDisconnected as exc:
                write_router_activity("cancel", provider, model, stage="collect_stream", error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes, stream=True)
                router_log("WARN", f"ollama_stream_collect_client_disconnected provider={provider} model={model} error={exc}")
                try:
                    resp.close()
                except Exception:
                    pass
                return
            resp.close()
            full = b"".join(chunks).decode("utf-8", errors="ignore")
            data = None
            for line in full.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    if isinstance(chunk, dict) and chunk.get("done"):
                        data = chunk
                except Exception:
                    continue
            if data is None:
                data = {"message": {"content": ""}, "done": True, "done_reason": "end_turn"}
            message = ollama_chat_to_anthropic(data, model, source_body=original_body)
            message = refine_message_with_advisor(provider, pcfg, original_body, message, model)
            remember_channel_injected_tool_uses(original_body, message)
            message = prepend_anthropic_text(message, rate_limit_notice(waited, rpm_used, rpm_limit, rpm_status))
            write_json(handler, message)
            mark_pending_channel_delivery_success(handler, "ollama_collected_json")
        return
    # Non-streaming fallback
    data_bytes = json.dumps(req_body).encode("utf-8")
    req_tokens = estimate_tokens(req_body)
    req_bytes = len(data_bytes)
    gateway_retries = 0 if compatibility_test else configured_gateway_retries(pcfg)
    max_attempts = max(1, gateway_retries + 1)
    loop_attempts = max_attempts + 1
    context_retry_used = False
    data = None
    for attempt in range(loop_attempts):
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            write_router_activity(
                "request",
                provider,
                model,
                attempt=attempt + 1,
                total=max_attempts,
                tokens=req_tokens,
                bytes=req_bytes,
                timeout=ollama_request_timeout_seconds(pcfg),
            )
            router_log("INFO", f"ollama_request provider={provider} model={model} attempt={attempt + 1}/{max_attempts} tokens={req_tokens} bytes={req_bytes}")
            with provider_urlopen(req, timeout=ollama_request_timeout_seconds(pcfg), provider=provider, pcfg=pcfg) as resp:
                learn_router_rate_limit_headers(provider, pcfg, model, resp.headers)
                data = json.loads(resp.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="ignore")
            learn_router_rate_limit_headers(provider, pcfg, model, exc.headers)
            context_limit = ollama_context_error_limit(raw)
            if exc.code == 400 and context_limit and not context_retry_used:
                context_retry_used = True
                retry_pcfg = ollama_context_retry_config(pcfg, context_limit)
                req_body = ollama_chat_request(model, upstream_body, retry_pcfg, stream=stream_requested, provider=provider)
                data_bytes = json.dumps(req_body).encode("utf-8")
                req_tokens = estimate_tokens(req_body)
                req_bytes = len(data_bytes)
                write_router_activity(
                    "retry",
                    provider,
                    model,
                    attempt=attempt + 1,
                    total=max_attempts,
                    code=exc.code,
                    reason="context_compact_retry",
                    context_limit=context_limit,
                    tokens=req_tokens,
                    bytes=req_bytes,
                )
                router_log(
                    "WARN",
                    f"ollama_context_retry provider={provider} model={model} n_ctx={context_limit} tokens={req_tokens} bytes={req_bytes}",
                )
                continue
            if exc.code == 429 and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                wait = register_router_rate_limit_backoff(provider, pcfg, model, exc.headers.get("Retry-After"))
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, wait=wait, tokens=req_tokens, bytes=req_bytes)
                router_log("WARN", f"ollama_rate_limit_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} wait={wait:.2f}s tokens={req_tokens} bytes={req_bytes}")
                if not sleep_until_or_client_disconnect(handler, wait):
                    write_router_activity("cancel", provider, model, stage="rate_limit_retry_wait", tokens=req_tokens, bytes=req_bytes)
                    router_log("WARN", f"ollama_cancelled_before_rate_limit_retry provider={provider} model={model} tokens={req_tokens} bytes={req_bytes}")
                    return
                continue
            if exc.code in UPSTREAM_RETRY_HTTP_CODES and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, code=exc.code, tokens=req_tokens, bytes=req_bytes)
                router_log("WARN", f"ollama_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} code={exc.code} tokens={req_tokens} bytes={req_bytes}")
                if not sleep_until_or_client_disconnect(handler, upstream_retry_wait_seconds(retry_no)):
                    write_router_activity("cancel", provider, model, stage="http_retry_wait", tokens=req_tokens, bytes=req_bytes)
                    router_log("WARN", f"ollama_cancelled_before_http_retry provider={provider} model={model} tokens={req_tokens} bytes={req_bytes}")
                    return
                continue
            if router_client_connection_closed(handler):
                write_router_activity("cancel", provider, model, stage="http_error", code=exc.code, tokens=req_tokens, bytes=req_bytes)
                router_log("WARN", f"ollama_client_gone_before_error_response provider={provider} model={model} code={exc.code}")
                return
            write_router_activity("error", provider, model, code=exc.code, tokens=req_tokens, bytes=req_bytes)
            write_json(
                handler,
                {"type": "error", "error": {"type": "upstream_error", "message": upstream_http_error_message(exc, raw)}},
                exc.code,
            )
            return
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            if retryable_upstream_exception(exc) and attempt + 1 < max_attempts:
                retry_no = attempt + 1
                write_router_activity("retry", provider, model, attempt=retry_no, total=gateway_retries, error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes)
                router_log("WARN", f"ollama_retry provider={provider} model={model} attempt={retry_no}/{gateway_retries} error={type(exc).__name__} tokens={req_tokens} bytes={req_bytes}")
                if not sleep_until_or_client_disconnect(handler, upstream_retry_wait_seconds(retry_no)):
                    write_router_activity("cancel", provider, model, stage="exception_retry_wait", error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes)
                    router_log("WARN", f"ollama_cancelled_before_exception_retry provider={provider} model={model} error={type(exc).__name__}")
                    return
                continue
            if router_client_connection_closed(handler):
                write_router_activity("cancel", provider, model, stage="exception_error", error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes)
                router_log("WARN", f"ollama_client_gone_before_exception_response provider={provider} model={model} error={type(exc).__name__}")
                return
            write_router_activity("error", provider, model, error=type(exc).__name__, tokens=req_tokens, bytes=req_bytes)
            write_json(
                handler,
                {"type": "error", "error": {"type": "upstream_error", "message": f"{type(exc).__name__}: {exc}"}},
                504 if retryable_upstream_exception(exc) else 502,
            )
            return
    if data is None:
        write_router_activity("error", provider, model, tokens=req_tokens, bytes=req_bytes)
        write_json(
            handler,
            {"type": "error", "error": {"type": "upstream_error", "message": "upstream request failed"}},
            504,
        )
        return
    message = ollama_chat_to_anthropic(data, model, source_body=original_body)
    message = refine_message_with_advisor(provider, pcfg, original_body, message, model)
    remember_channel_injected_tool_uses(original_body, message)
    message = prepend_anthropic_text(message, rate_limit_notice(waited, rpm_used, rpm_limit, rpm_status))
    write_json(handler, message)
    mark_pending_channel_delivery_success(handler, "ollama_json")

