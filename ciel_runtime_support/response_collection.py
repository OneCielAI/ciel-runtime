"""Protocol strategies for collecting one message for a Responses API projection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, is_remote_bridge_request
from .upstream_error_policy import upstream_failure_in_payload


@dataclass(frozen=True, slots=True)
class ChatCollectionStrategy:
    operation: str
    build_request: Callable[..., dict[str, Any]]
    decode_response: Callable[..., dict[str, Any]]
    request_timeout_seconds: Callable[..., float]
    normalize_upstream_model: Callable[..., str]
    skip_rate_limit_during_compatibility_test: bool = False
    # When set, the upstream is read as a stream and assembled here instead of
    # being fetched with one blocking POST. Same result, except a runaway can be
    # cut off while it is still being generated rather than after.
    stream_collect: Callable[..., dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class ResponseCollectionRequest:
    normalize_thinking: Callable[..., dict[str, Any]]
    resolve_model: Callable[..., str]
    body_with_advisor_tool: Callable[..., dict[str, Any]]
    advisor_provider_supported: Callable[..., bool]
    provider_endpoint: Callable[..., str]
    provider_headers: Callable[..., dict[str, str]]


@dataclass(frozen=True, slots=True)
class ResponseCollectionRateLimit:
    apply: Callable[..., tuple[float, int, int]]
    effective_rpm: Callable[..., int]
    notice: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ResponseCollectionProjection:
    refine_with_advisor: Callable[..., dict[str, Any]]
    remember_tool_uses: Callable[..., Any]
    prepend_text: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ResponseCollectionServices:
    compatibility_test_header: str
    request: ResponseCollectionRequest
    rate_limit: ResponseCollectionRateLimit
    projection: ResponseCollectionProjection
    post_json_with_retry: Callable[..., Any]
    hosted_tools: Any


@dataclass(frozen=True, slots=True)
class AnthropicCollectionRequest:
    normalize_thinking: Callable[..., dict[str, Any]]
    normalize_system_roles: Callable[..., dict[str, Any]]
    cap_body: Callable[..., dict[str, Any]]
    apply_options: Callable[..., dict[str, Any]]
    rehydrate_thinking: Callable[..., dict[str, Any]]
    resolve_model: Callable[..., str]
    normalize_upstream_model: Callable[..., str]
    resolve_tool_models: Callable[..., dict[str, Any]]
    normalize_model_options: Callable[..., dict[str, Any]]
    strip_internal_metadata: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class AnthropicCollectionTransport:
    provider_endpoint: Callable[..., str]
    messages_query: Callable[..., str]
    provider_headers: Callable[..., dict[str, str]]
    apply_rate_limit: Callable[..., tuple[float, int, int]]
    open_request_with_retry: Callable[..., Any]
    request_timeout_seconds: Callable[..., float]


@dataclass(frozen=True, slots=True)
class AnthropicCollectionProjection:
    normalize_response_thinking: Callable[..., dict[str, Any]]
    append_synthetic_tasklist: Callable[..., dict[str, Any]]
    prepend_text: Callable[..., dict[str, Any]]
    rate_limit_notice: Callable[..., str]


@dataclass(frozen=True, slots=True)
class AnthropicCollectionServices:
    request: AnthropicCollectionRequest
    transport: AnthropicCollectionTransport
    projection: AnthropicCollectionProjection
    forwarded_headers: tuple[str, ...]


def collect_chat_message_for_responses(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    strategy: ChatCollectionStrategy,
    services: ResponseCollectionServices,
) -> dict[str, Any]:
    """Collect a provider chat response using a protocol-specific strategy."""

    request = services.request
    rate_limit = services.rate_limit
    projection = services.projection
    remote_bridge = is_remote_bridge_request(handler)
    body = request.normalize_thinking(provider, pcfg, body)
    model = request.resolve_model(provider, pcfg, body.get("model"))
    model = strategy.normalize_upstream_model(provider, pcfg, model)
    original_body = body
    projection_body = (
        {**body, REMOTE_BRIDGE_CONFIG_MARKER: True}
        if remote_bridge
        else body
    )
    local_advisor = (
        not remote_bridge and request.advisor_provider_supported(provider)
    )
    upstream_body = request.body_with_advisor_tool(body, pcfg) if local_advisor else body
    streaming = strategy.stream_collect is not None
    req_body = strategy.build_request(provider, model, upstream_body, pcfg, stream=streaming)
    url = request.provider_endpoint(provider, pcfg, strategy.operation)
    timeout = strategy.request_timeout_seconds(pcfg)
    headers = request.provider_headers(provider, pcfg, handler.headers, strategy.operation)
    hosted_state = None
    if not remote_bridge:
        req_body, hosted_state = services.hosted_tools.prepare(
            provider, pcfg, req_body, headers, timeout
        )
    compatibility_test = str(handler.headers.get(services.compatibility_test_header) or "").strip().lower() in ("1", "true", "yes", "on")
    if compatibility_test and strategy.skip_rate_limit_during_compatibility_test:
        waited, rpm_used, rpm_limit = 0.0, 0, rate_limit.effective_rpm(provider, pcfg, model)
    else:
        waited, rpm_used, rpm_limit = rate_limit.apply(provider, pcfg, model)
    if streaming:
        data = strategy.stream_collect(
            url,
            req_body,
            headers,
            timeout,
            provider,
            pcfg,
            model,
            retry_rate_limits=not compatibility_test,
        )
    else:
        data = services.post_json_with_retry(
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
    # Hosted-tool follow-ups are plain request/response, so they keep using the
    # blocking POST regardless of how the first turn was read.
    if hosted_state is not None:
        data = services.hosted_tools.resolve(
            hosted_state,
            {**req_body, "stream": False} if streaming else req_body,
            data,
            lambda next_body: services.post_json_with_retry(
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
    failure = upstream_failure_in_payload(provider, model, data)
    if failure is not None:
        raise failure
    message = strategy.decode_response(
        data, model, source_body=projection_body
    )
    if not remote_bridge:
        message = projection.refine_with_advisor(
            provider, pcfg, original_body, message, model
        )
        projection.remember_tool_uses(original_body, message)
    notice = "" if remote_bridge else rate_limit.notice(
        waited,
        rpm_used,
        rpm_limit,
        bool(pcfg.get("rate_limit_status", False)),
    )
    return projection.prepend_text(message, notice)


def collect_anthropic_message_for_responses(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    services: AnthropicCollectionServices,
    stream_collect: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect one native Anthropic message for a Responses API projection.

    ``stream_collect`` reads the upstream as SSE and assembles the same message
    object, which lets the repetition guard cut a loop off mid-generation
    instead of paying for all of it and trimming afterwards.
    """

    request = services.request
    transport = services.transport
    projection = services.projection
    remote_bridge = is_remote_bridge_request(handler)
    body = request.normalize_thinking(provider, pcfg, body)
    body = request.normalize_system_roles(provider, pcfg, body)
    body = request.cap_body(provider, pcfg, body)
    body = request.apply_options(provider, pcfg, body)
    if not remote_bridge:
        body = request.rehydrate_thinking(provider, pcfg, body)
    upstream_model = request.resolve_model(provider, pcfg, body.get("model"))
    upstream_model = request.normalize_upstream_model(provider, pcfg, upstream_model)
    body["model"] = upstream_model
    body = request.resolve_tool_models(provider, pcfg, body)
    body = request.normalize_model_options(provider, pcfg, body, upstream_model)
    streaming = stream_collect is not None
    upstream_body = request.strip_internal_metadata({**body, "stream": streaming})
    url = transport.provider_endpoint(provider, pcfg, "anthropic_messages")
    upstream_query = transport.messages_query(pcfg, handler.path, provider)
    if upstream_query:
        url = f"{url}?{upstream_query}"
    headers = transport.provider_headers(
        provider, pcfg, handler.headers, "anthropic_messages"
    )
    for header in services.forwarded_headers:
        if handler.headers.get(header):
            headers[header] = handler.headers[header]
    waited, rpm_used, rpm_limit = transport.apply_rate_limit(provider, pcfg, upstream_model)
    upstream_response = transport.open_request_with_retry(
        url,
        upstream_body,
        headers,
        transport.request_timeout_seconds(pcfg),
        provider,
        pcfg,
        upstream_model,
        stream=streaming,
    )
    try:
        if streaming:
            payload = stream_collect(upstream_response, provider, upstream_model)
        else:
            raw_response = upstream_response.read()
            try:
                decoded_response = raw_response.decode(
                    "utf-8", errors="strict" if remote_bridge else "replace"
                )
            except UnicodeDecodeError as exc:
                raise RuntimeError(
                    "upstream Anthropic response contained invalid UTF-8"
                ) from exc
            payload = json.loads(decoded_response)
        if not isinstance(payload, dict):
            raise RuntimeError("upstream returned non-object JSON")
        failure = upstream_failure_in_payload(provider, upstream_model, payload)
        if failure is not None:
            raise failure
        if not remote_bridge:
            payload = projection.normalize_response_thinking(
                provider, pcfg, payload, upstream_model
            )
            payload = projection.append_synthetic_tasklist(
                payload,
                upstream_model,
                body,
                "native_json",
                provider=provider,
            )
        notice = "" if remote_bridge else projection.rate_limit_notice(
            waited,
            rpm_used,
            rpm_limit,
            bool(pcfg.get("rate_limit_status", False)),
        )
        return projection.prepend_text(payload, notice)
    finally:
        try:
            upstream_response.close()
        except Exception:
            pass
