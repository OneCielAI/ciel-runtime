"""OpenAI Responses HTTP routing application service."""

from __future__ import annotations

import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from .context_compaction import AutomaticContextCompactionCompleted
from .upstream_error_policy import (
    UpstreamFailure,
    UpstreamStreamReadError,
    anthropic_error_type_for_status,
)


@dataclass(frozen=True, slots=True)
class OpenAIResponsesCore:
    event_bus: Any
    request_id: Callable[[], str]
    input_as_list: Callable[[Any], list[Any]]
    is_client_disconnect: Callable[[BaseException], bool]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class OpenAIResponsesConversion:
    to_anthropic: Callable[..., dict[str, Any]]
    current_alias: Callable[[dict[str, Any]], str]
    update_tool_schema: Callable[[Any], Any]
    normalize_thinking: Callable[..., dict[str, Any]]
    filter_blocked_tools: Callable[..., dict[str, Any]]
    normalize_tool_choice: Callable[..., dict[str, Any]]
    write_context_usage: Callable[..., Any]
    strip_advisor_tools: Callable[..., dict[str, Any]]
    inject_channel_context: Callable[[dict[str, Any]], dict[str, Any]]
    inject_tool_result_context: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIResponsesRouting:
    maybe_import_session: Callable[..., bool]
    codex_routed_enabled: Callable[[str, dict[str, Any]], bool]
    forward_codex: Callable[..., Any]
    select_protocol: Callable[[str, dict[str, Any], str, str | None], str]
    forward_provider_responses: Callable[..., dict[str, Any]]
    dump_request: Callable[..., Any]
    normalize_provider_wire: Callable[..., dict[str, Any]]
    collect_message: Callable[..., dict[str, Any]]
    # Both belong here rather than with the conversions: each one's behaviour is
    # decided by the routed-vs-native split this group already owns.
    apply_codex_compat_instructions: Callable[..., dict[str, Any]]
    recover_preamble_only_turn: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIResponsesDelivery:
    begin: Callable[..., Any]
    mark_success: Callable[..., Any]
    mark_failed: Callable[..., Any]
    commit: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIResponsesOutput:
    write_response: Callable[..., Any]
    write_error: Callable[..., Any]
    upstream_error_message: Callable[..., str]
    codex_auth_error_message: Callable[[str], str]
    event_preview: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAIResponsesServices:
    core: OpenAIResponsesCore
    conversion: OpenAIResponsesConversion
    routing: OpenAIResponsesRouting
    delivery: OpenAIResponsesDelivery
    output: OpenAIResponsesOutput


def _responses_error_type(status: int) -> str:
    """Name the failure the way the shared classifier does.

    Reporting 400, 404, 409, 422 and 429 all as ``api_error`` left Codex with
    no machine-readable difference between a rejected request and a provider
    outage, even when the message said which one it was.
    """

    return anthropic_error_type_for_status(status)


def handle_openai_responses_request(
    handler: Any,
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    services: OpenAIResponsesServices,
) -> None:
    core = services.core
    conversion = services.conversion
    routing = services.routing
    delivery = services.delivery
    output = services.output
    # Codex cannot receive --append-system-prompt, so the routed compatibility
    # instruction has to ride along in the request itself. Do this before the
    # conversion so it reaches both the translated path and a native Responses
    # provider; the native Codex backend is excluded inside the port.
    body = routing.apply_codex_compat_instructions(cfg, provider, pcfg, body)
    anthropic_body = conversion.to_anthropic(body, conversion.current_alias(cfg))
    if routing.maybe_import_session(
        handler,
        anthropic_body,
        client_runtime="codex",
        response_format="openai",
        source_body=body,
    ):
        return
    if routing.codex_routed_enabled(provider, pcfg):
        _handle_codex_route(handler, provider, pcfg, body, services)
        return
    if (
        routing.select_protocol(
            provider,
            pcfg,
            "openai_responses",
            str(body.get("model") or ""),
        )
        == "openai_responses"
    ):
        _handle_provider_responses_route(
            handler,
            provider,
            pcfg,
            body,
            services,
        )
        return

    stream = bool(body.get("stream", True))
    conversion.update_tool_schema(anthropic_body.get("tools"))
    anthropic_body = conversion.normalize_thinking(provider, pcfg, anthropic_body)
    request_id = core.request_id()
    core.event_bus.publish(
        level="info",
        category="router.request",
        message="OpenAI Responses request received",
        request_id=request_id,
        provider=provider,
        model=str(anthropic_body.get("model") or ""),
        data={
            "path": "/v1/responses",
            "messages": len(anthropic_body.get("messages") or []),
            "tools": len(anthropic_body.get("tools") or []),
            **output.event_preview(anthropic_body, cfg),
        },
    )
    routing.dump_request(provider, "/v1/responses", body)
    anthropic_body = conversion.filter_blocked_tools(provider, pcfg, anthropic_body)
    anthropic_body = conversion.normalize_tool_choice(provider, pcfg, anthropic_body)
    conversion.write_context_usage(provider, pcfg, anthropic_body, "responses")
    anthropic_body = conversion.strip_advisor_tools(provider, anthropic_body)
    anthropic_body = conversion.inject_channel_context(anthropic_body)
    anthropic_body = conversion.inject_tool_result_context(anthropic_body)
    delivery.begin(handler, anthropic_body)
    try:
        anthropic_body = routing.normalize_provider_wire(provider, pcfg, anthropic_body)
    except AutomaticContextCompactionCompleted as completed:
        _complete_local_compaction(
            handler, provider, body, anthropic_body, completed, services
        )
        return
    core.log(
        "DEBUG",
        f"POST /v1/responses provider={provider} model={anthropic_body.get('model')} "
        f"tools={len(anthropic_body.get('tools') or [])} msgs={len(anthropic_body.get('messages') or [])}",
    )
    try:
        message = routing.collect_message(handler, provider, pcfg, anthropic_body)
        message = routing.recover_preamble_only_turn(
            handler, provider, pcfg, anthropic_body, message
        )
        output.write_response(handler, message, source_body=body, stream=stream)
        delivery.mark_success(handler, "responses_json")
        delivery.commit(anthropic_body, handler)
    except AutomaticContextCompactionCompleted as completed:
        _complete_local_compaction(
            handler, provider, body, anthropic_body, completed, services
        )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        delivery.mark_failed(handler, f"responses_http_error:{exc.code}")
        output.write_error(
            handler,
            output.upstream_error_message(exc, raw),
            stream=stream,
            status=exc.code,
            error_type=_responses_error_type(exc.code),
        )
    except UpstreamFailure as exc:
        # The upstream status, type and message were all read before this was
        # raised.  Answering with a generic local 500 is what made a rejected
        # request look like a provider fault to Codex.
        delivery.mark_failed(handler, f"responses_upstream_failure:{exc.category}")
        output.write_error(
            handler,
            exc.message,
            stream=stream,
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
        )
    except UpstreamStreamReadError as exc:
        core.event_bus.publish(
            level="error",
            category="router.error",
            message=str(exc),
            request_id=request_id,
            provider=provider,
            model=str(anthropic_body.get("model") or ""),
            data={
                "error_type": type(exc.error).__name__,
                "upstream_stream_truncated": True,
                "attempts": exc.attempts,
            },
        )
        delivery.mark_failed(handler, "responses_upstream_stream_truncated")
        # No downstream response bytes have been emitted: collection happens
        # first.  Return an ordinary HTTP error body so Codex reports the real
        # 502 instead of consuming an SSE `error` event and later complaining
        # that `response.completed` was missing.
        output.write_error(
            handler,
            str(exc),
            stream=False,
            status=exc.status_code,
            error_type="api_error",
        )
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(handler, f"responses_client_disconnected:{type(exc).__name__}")
            return
        core.event_bus.publish(
            level="error",
            category="router.error",
            message=str(exc),
            request_id=request_id,
            provider=provider,
            model=str(anthropic_body.get("model") or ""),
            data={"error_type": type(exc).__name__},
        )
        delivery.mark_failed(handler, f"responses_error:{type(exc).__name__}")
        output.write_error(handler, f"{type(exc).__name__}: {exc}", stream=stream)


def _complete_local_compaction(
    handler: Any,
    provider: str,
    body: dict[str, Any],
    anthropic_body: dict[str, Any],
    completed: AutomaticContextCompactionCompleted,
    services: OpenAIResponsesServices,
) -> None:
    services.core.log(
        "WARN",
        f"context_compact_local_complete provider={provider} "
        f"model={anthropic_body.get('model')}",
    )
    services.output.write_response(
        handler,
        {
            "type": "message",
            "role": "assistant",
            "model": str(anthropic_body.get("model") or body.get("model") or ""),
            "content": [{"type": "text", "text": completed.summary}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 0,
                "output_tokens": max(1, len(completed.summary) // 4),
            },
        },
        source_body=body,
        stream=bool(body.get("stream", True)),
    )
    services.delivery.mark_success(handler, "responses_local_compaction")
    services.delivery.commit(anthropic_body, handler)


def _handle_codex_route(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    services: OpenAIResponsesServices,
) -> None:
    core = services.core
    routing = services.routing
    delivery = services.delivery
    output = services.output
    request_id = core.request_id()
    core.event_bus.publish(
        level="info",
        category="router.request",
        message="Codex Responses request received",
        request_id=request_id,
        provider=provider,
        model=str(body.get("model") or ""),
        data={
            "path": urllib.parse.urlparse(handler.path).path,
            "input_items": len(core.input_as_list(body.get("input", []))),
            "tools": len(body.get("tools") or []),
        },
    )
    routing.dump_request(provider, urllib.parse.urlparse(handler.path).path, body)
    try:
        routing.forward_codex(handler, provider, pcfg, body)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        delivery.mark_failed(handler, f"codex_responses_http_error:{exc.code}")
        message = output.upstream_error_message(exc, raw)
        if exc.code in (401, 403):
            message = output.codex_auth_error_message(message)
        output.write_error(
            handler,
            message,
            stream=bool(body.get("stream", True)),
            status=exc.code,
            error_type=_responses_error_type(exc.code),
        )
    except UpstreamFailure as exc:
        delivery.mark_failed(handler, f"codex_responses_upstream_failure:{exc.category}")
        output.write_error(
            handler,
            exc.message,
            stream=bool(body.get("stream", True)),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
        )
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(handler, f"codex_responses_client_disconnected:{type(exc).__name__}")
            return
        delivery.mark_failed(handler, f"codex_responses_error:{type(exc).__name__}")
        output.write_error(handler, f"{type(exc).__name__}: {exc}", stream=bool(body.get("stream", True)))


def _handle_provider_responses_route(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    services: OpenAIResponsesServices,
) -> None:
    core = services.core
    routing = services.routing
    delivery = services.delivery
    output = services.output
    request_id = core.request_id()
    core.event_bus.publish(
        level="info",
        category="router.request",
        message="Native provider Responses request received",
        request_id=request_id,
        provider=provider,
        model=str(body.get("model") or ""),
        data={
            "path": urllib.parse.urlparse(handler.path).path,
            "input_items": len(core.input_as_list(body.get("input", []))),
            "tools": len(body.get("tools") or []),
        },
    )
    routing.dump_request(
        provider,
        urllib.parse.urlparse(handler.path).path,
        body,
    )
    try:
        delivery_body = routing.forward_provider_responses(
            handler,
            provider,
            pcfg,
            body,
        )
        delivery.mark_success(handler, "provider_responses_proxy")
        delivery.commit(delivery_body, handler)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        delivery.mark_failed(
            handler,
            f"provider_responses_http_error:{exc.code}",
        )
        output.write_error(
            handler,
            output.upstream_error_message(exc, raw),
            stream=bool(body.get("stream", True)),
            status=exc.code,
            error_type=_responses_error_type(exc.code),
        )
    except UpstreamFailure as exc:
        delivery.mark_failed(
            handler, f"provider_responses_upstream_failure:{exc.category}"
        )
        output.write_error(
            handler,
            exc.message,
            stream=bool(body.get("stream", True)),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            response_started=exc.output_started,
            response_id=exc.response_id,
        )
    except UpstreamStreamReadError as exc:
        core.event_bus.publish(
            level="error",
            category="router.error",
            message=str(exc),
            request_id=request_id,
            provider=provider,
            model=str(body.get("model") or ""),
            data={
                "error_type": type(exc.error).__name__,
                "upstream_stream_truncated": True,
                "attempts": exc.attempts,
                "downstream_started": exc.downstream_started,
            },
        )
        delivery.mark_failed(handler, "provider_responses_upstream_stream_truncated")
        output.write_error(
            handler,
            str(exc),
            stream=bool(body.get("stream", True)),
            status=exc.status_code,
            error_type="upstream_stream_truncated",
            response_started=exc.downstream_started,
            response_id=exc.response_id,
        )
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(
                handler,
                f"provider_responses_client_disconnected:{type(exc).__name__}",
            )
            return
        core.event_bus.publish(
            level="error",
            category="router.error",
            message=str(exc),
            request_id=request_id,
            provider=provider,
            model=str(body.get("model") or ""),
            data={"error_type": type(exc).__name__},
        )
        delivery.mark_failed(
            handler,
            f"provider_responses_error:{type(exc).__name__}",
        )
        output.write_error(
            handler,
            f"{type(exc).__name__}: {exc}",
            stream=bool(body.get("stream", True)),
        )
