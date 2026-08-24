"""OpenAI-compatible chat forwarding application service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
from typing import Any, Callable

from .initial_stream_retry import InitialStreamRetry
from .upstream_error_policy import (
    UpstreamFailure,
    upstream_failure_in_payload,
    initial_stream_retries,
    retry_wait_seconds,
    retryable_exception,
)


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
    provider_endpoint: Callable[..., str]
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
    hosted_tools: Any
    log: Callable[[str, str], Any]


def _http_error_failure(
    provider: str,
    model: str,
    error: urllib.error.HTTPError,
    *,
    output_started: bool,
) -> UpstreamFailure:
    """Read one upstream HTTP error into the shared failure model."""

    return UpstreamFailure.from_http_error(
        provider, model, error, output_started=output_started
    )


def _write_stream_error(handler: Any, payload: dict[str, Any]) -> None:
    """Terminate an already-started Anthropic SSE response without a second status."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    handler.wfile.write(f"event: error\ndata: {data}\n\n".encode("utf-8"))
    handler.wfile.flush()


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
    # Provider adapters own endpoint layout.  Building ``/v1/chat/completions``
    # here duplicates version segments for plan-scoped bases such as
    # ``.../api/v1/zcode-plan``.
    url = request.provider_endpoint(provider, pcfg, "openai_chat")
    timeout = rate_limit.request_timeout_seconds(pcfg)
    headers = request.provider_headers(provider, pcfg, handler.headers, "openai_chat")
    waited, rpm_used, rpm_limit = rate_limit.apply(provider, pcfg, model)
    compatibility_test = str(handler.headers.get(policy.compatibility_test_header) or "").strip().lower() in ("1", "true", "yes", "on")
    stream_enabled = bool(pcfg.get("stream_enabled", True))
    stream = policy.provider_requires_streaming(provider, pcfg) or (bool(body.get("stream", stream_enabled)) and stream_enabled)
    collected_request = request.build_chat_request(provider, model, upstream_body, pcfg, stream=False)
    collected_request, hosted_state = services.hosted_tools.prepare(
        provider, pcfg, collected_request, headers, timeout
    )
    if hosted_state.enabled and stream:
        stream = False
        services.log("INFO", f"provider-hosted tools enabled for {provider}; collecting tool rounds internally")
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
        index = 0
        stream_started = False
        pending_notices = [notice] if notice else []

        def start_stream() -> None:
            nonlocal index, stream_started
            if stream_started:
                return
            streaming.write_open_start(handler, model, input_tokens=req_tokens)
            stream_started = True
            for pending_notice in pending_notices:
                index = streaming.write_blocks(
                    handler,
                    [{"type": "text", "text": pending_notice}],
                    index,
                )

        try:
            def emit_retry_notice(text: str) -> None:
                nonlocal index
                # Retrying may take long enough that Claude needs the same
                # early SSE heartbeat it received before this 413 fix.  Once
                # headers are committed, a later 413 is emitted as SSE.
                start_stream()
                index = streaming.write_blocks(
                    handler,
                    [{"type": "text", "text": text + "\n"}],
                    index,
                )

            upstream_response = streaming.open_with_retry(
                url,
                req_body,
                headers,
                timeout,
                provider,
                pcfg,
                model,
                emit_retry_notice,
                retry_rate_limits=not compatibility_test,
            )
            reconnects = initial_stream_retries(pcfg)
            if reconnects:
                def reopen_initial_stream(_attempt: int) -> Any:
                    return streaming.open_with_retry(
                        url,
                        req_body,
                        headers,
                        timeout,
                        provider,
                        pcfg,
                        model,
                        None,
                        retry_rate_limits=not compatibility_test,
                    )

                def record_initial_retry(attempt: int, error: BaseException) -> None:
                    services.log(
                        "WARN",
                        "upstream_stream_initial_retry "
                        f"provider={provider} model={model} "
                        f"attempt={attempt}/{reconnects} "
                        f"error={type(error).__name__}: {error}",
                    )
                    response.write_activity(
                        "retry",
                        provider,
                        model,
                        attempt=attempt,
                        total=reconnects,
                        error=type(error).__name__,
                        stream=True,
                        stage="initial_stream",
                    )

                upstream_response = InitialStreamRetry(
                    upstream_response,
                    reopen_initial_stream,
                    reconnects,
                    retryable_exception,
                    retry_wait_seconds,
                    time.sleep,
                    record_initial_retry,
                )
            # urllib raises an HTTP status such as 413 while opening the
            # upstream response.  Delay our 200/SSE headers until that status
            # is known so it can still be returned faithfully to Claude.
            start_stream()
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
            else:
                response.mark_delivery_failed(handler, "openai_stream_error")
        except urllib.error.HTTPError as exc:
            # Every upstream status is an error, including 429.  Writing one
            # as ordinary assistant text used to hand Claude a 200 whose only
            # content was "HTTPError: HTTP Error 429", so the session limit
            # that actually stopped the turn never reached the CLI.
            failure = _http_error_failure(
                provider, model, exc, output_started=stream_started
            )
            response.mark_delivery_failed(
                handler, f"openai_stream_http_error:{exc.code}"
            )
            response.write_activity(
                "error", provider, model, code=exc.code, stream=True
            )
            if stream_started:
                _write_stream_error(handler, failure.anthropic_payload())
            else:
                response.write_json(
                    handler, failure.anthropic_payload(), failure.status_code
                )
            return
        except UpstreamFailure as exc:
            # The provider status and message are known here, so this answers
            # as an error even though the failure arrived as an exception.
            response.mark_delivery_failed(
                handler, f"openai_stream_upstream_failure:{exc.category}"
            )
            response.write_activity(
                "error", provider, model, code=exc.status_code, stream=True
            )
            if stream_started:
                _write_stream_error(handler, exc.anthropic_payload())
            else:
                response.write_json(
                    handler, exc.anthropic_payload(), exc.status_code
                )
            return
        except RuntimeError as exc:
            response.mark_delivery_failed(handler, f"openai_stream_runtime_error:{type(exc).__name__}")
            start_stream()
            streaming.write_blocks(handler, [{"type": "text", "text": f"Upstream error: {exc}"}], index)
            streaming.write_open_stop(handler)
            return
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            response.mark_delivery_failed(handler, f"openai_stream_error:{type(exc).__name__}")
            response.write_activity("error", provider, model, error=type(exc).__name__, stream=True)
            start_stream()
            streaming.write_blocks(handler, [{"type": "text", "text": f"Upstream error: {message}"}], index)
            streaming.write_open_stop(handler)
            return
        return

    req_body = collected_request
    try:
        data = streaming.post_json_with_retry(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            None,
            retry_rate_limits=not compatibility_test,
        )
        data = services.hosted_tools.resolve(
            hosted_state,
            req_body,
            data,
            lambda next_body: streaming.post_json_with_retry(
                url,
                next_body,
                headers,
                timeout,
                provider,
                pcfg,
                model,
                None,
                retry_rate_limits=not compatibility_test,
            ),
            timeout,
        )
    except urllib.error.HTTPError as exc:
        failure = _http_error_failure(provider, model, exc, output_started=False)
        response.mark_delivery_failed(handler, f"openai_http_error:{exc.code}")
        response.write_activity(
            "error", provider, model, code=exc.code, stream=False
        )
        response.write_json(
            handler, failure.anthropic_payload(), failure.status_code
        )
        return
    except UpstreamFailure as exc:
        # The transport already read the provider status, type and message.
        # Reporting all of it as a local 500 is what made a rejected request
        # look like a provider outage.
        response.mark_delivery_failed(handler, f"openai_upstream_failure:{exc.category}")
        response.write_json(handler, exc.anthropic_payload(), exc.status_code)
        return
    except RuntimeError as exc:
        response.write_json(handler, {"type": "error", "error": {"type": "upstream_error", "message": str(exc)}}, 500)
        return
    failure = upstream_failure_in_payload(provider, model, data)
    if failure is not None:
        # An OpenAI-compatible server can answer HTTP 200 with only an error
        # object.  Decoding it for choices produced an empty `end_turn`, so
        # the CLI reported a turn that did nothing instead of the quota or
        # request error that actually stopped it.
        response.mark_delivery_failed(handler, f"openai_payload_failure:{failure.category}")
        response.write_activity(
            "error", provider, model, code=failure.status_code, stream=False
        )
        response.write_json(
            handler, failure.anthropic_payload(), failure.status_code
        )
        return
    message = response.chat_to_anthropic(data, model, source_body=original_body)
    message = advisor.refine_message(provider, pcfg, original_body, message, model)
    response.remember_tool_uses(original_body, message)
    message = response.prepend_text(message, notice)
    response.write_message(handler, message, stream)
    response.mark_delivery_success(handler, "openai_json")
