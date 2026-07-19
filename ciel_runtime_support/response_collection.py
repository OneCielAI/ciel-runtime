"""Protocol strategies for collecting one message for a Responses API projection."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChatCollectionStrategy:
    operation: str
    build_request: Callable[..., dict[str, Any]]
    decode_response: Callable[..., dict[str, Any]]
    request_timeout_seconds: Callable[..., float]
    normalize_upstream_model: Callable[..., str]
    skip_rate_limit_during_compatibility_test: bool = False


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
    native_compat_enabled: Callable[..., bool]
    native_base_url: Callable[..., str]
    upstream_request_base: Callable[..., str]
    join_url: Callable[..., str]
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
    body = request.normalize_thinking(provider, pcfg, body)
    model = request.resolve_model(provider, pcfg, body.get("model"))
    model = strategy.normalize_upstream_model(provider, pcfg, model)
    original_body = body
    upstream_body = request.body_with_advisor_tool(body, pcfg) if request.advisor_provider_supported(provider) else body
    req_body = strategy.build_request(provider, model, upstream_body, pcfg, stream=False)
    url = request.provider_endpoint(provider, pcfg, strategy.operation)
    compatibility_test = str(handler.headers.get(services.compatibility_test_header) or "").strip().lower() in ("1", "true", "yes", "on")
    if compatibility_test and strategy.skip_rate_limit_during_compatibility_test:
        waited, rpm_used, rpm_limit = 0.0, 0, rate_limit.effective_rpm(provider, pcfg, model)
    else:
        waited, rpm_used, rpm_limit = rate_limit.apply(provider, pcfg, model)
    data = services.post_json_with_retry(
        url,
        req_body,
        request.provider_headers(provider, pcfg, handler.headers),
        strategy.request_timeout_seconds(pcfg),
        provider,
        pcfg,
        model,
        None,
        retry_rate_limits=not compatibility_test,
    )
    message = strategy.decode_response(data, model, source_body=original_body)
    message = projection.refine_with_advisor(provider, pcfg, original_body, message, model)
    projection.remember_tool_uses(original_body, message)
    notice = rate_limit.notice(waited, rpm_used, rpm_limit, bool(pcfg.get("rate_limit_status", False)))
    return projection.prepend_text(message, notice)


def collect_anthropic_message_for_responses(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    services: AnthropicCollectionServices,
) -> dict[str, Any]:
    """Collect one native Anthropic message for a Responses API projection."""

    request = services.request
    transport = services.transport
    projection = services.projection
    body = request.normalize_thinking(provider, pcfg, body)
    body = request.normalize_system_roles(body)
    body = request.cap_body(provider, pcfg, body)
    body = request.apply_options(provider, pcfg, body)
    body = request.rehydrate_thinking(provider, pcfg, body)
    upstream_model = request.resolve_model(provider, pcfg, body.get("model"))
    upstream_model = request.normalize_upstream_model(provider, pcfg, upstream_model)
    body["model"] = upstream_model
    body = request.resolve_tool_models(provider, pcfg, body)
    body = request.normalize_model_options(provider, pcfg, body, upstream_model)
    upstream_body = request.strip_internal_metadata({**body, "stream": False})
    if transport.native_compat_enabled(provider, pcfg):
        base = transport.native_base_url(provider, pcfg)
    else:
        base = transport.upstream_request_base(provider, pcfg)
    url = transport.join_url(base, "/v1/messages")
    upstream_query = transport.messages_query(pcfg, handler.path, provider)
    if upstream_query:
        url = f"{url}?{upstream_query}"
    headers = transport.provider_headers(provider, pcfg, handler.headers)
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
        stream=False,
    )
    try:
        raw_response = upstream_response.read()
        payload = json.loads(raw_response.decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise RuntimeError("upstream returned non-object JSON")
        payload = projection.normalize_response_thinking(provider, pcfg, payload, upstream_model)
        payload = projection.append_synthetic_tasklist(payload, upstream_model, body, "native_json", provider=provider)
        notice = projection.rate_limit_notice(
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
