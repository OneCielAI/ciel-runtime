"""OpenAI Responses HTTP routing application service."""

from __future__ import annotations

import copy
import re
import urllib.error
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from .cielairouter_gateway_overlay import DISCOVERY_BASE_URL_KEY
from .context_compaction import AutomaticContextCompactionCompleted
from .remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER, is_remote_bridge_request
from .provider_failover import FailoverRequested, maybe_fail_over
from .upstream_error_policy import (
    UpstreamFailure,
    UpstreamStreamReadError,
    anthropic_error_type_for_status,
    is_upstream_transport_error,
)


@dataclass(frozen=True, slots=True)
class OpenAIResponsesCore:
    event_bus: Any
    request_id: Callable[[], str]
    input_as_list: Callable[[Any], list[Any]]
    is_client_disconnect: Callable[[BaseException], bool]
    log: Callable[[str, str], Any]
    observe_context_request: Callable[..., Any] = lambda *_args: None
    observe_context_failure: Callable[..., Any] = lambda *_args: None


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


def _observe_context_request(core: Any, *args: Any) -> None:
    # Older injected cores predate the context observers; they observe nothing.
    getattr(core, "observe_context_request", lambda *_args: None)(*args)


def _observe_context_failure(core: Any, *args: Any) -> None:
    getattr(core, "observe_context_failure", lambda *_args: None)(*args)


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


def _core_log(core: Any, level: str, message: str) -> None:
    """Log through ``core`` when it exposes one (tests use a bare namespace)."""

    log = getattr(core, "log", None)
    if callable(log):
        log(level, message)


def _request_path(handler: Any) -> str:
    """The request path for a log line, or ``-`` when the handler has none."""

    raw = getattr(handler, "path", "")
    if not isinstance(raw, str) or not raw:
        return "-"
    try:
        return urllib.parse.urlparse(raw).path or "-"
    except ValueError:
        return "-"


def _log_upstream_error(
    core: Any,
    *,
    path: str,
    provider: str,
    model: str,
    status: int,
    error_type: str,
    message: Any,
) -> None:
    """Record the upstream failure that becomes this client's HTTP error.

    A relayed 5xx used to leave only the access line, so the request that
    produced it could not be identified after the fact.
    """

    text = " ".join(str(message or "").split())[:300]
    _core_log(
        core,
        "ERROR",
        f"responses_upstream_error path={path or '-'} provider={provider} "
        f"model={model or '-'} status={status} type={error_type or '-'} "
        f"message={text or '-'}",
    )


def _responses_error_type(status: int) -> str:
    """Name the failure the way the shared classifier does.

    Reporting 400, 404, 409, 422 and 429 all as ``api_error`` left Codex with
    no machine-readable difference between a rejected request and a provider
    outage, even when the message said which one it was.
    """

    return anthropic_error_type_for_status(status)


def _complete_local_delivery(
    handler: Any,
    delivery: OpenAIResponsesDelivery,
    body: dict[str, Any],
    reason: str,
) -> None:
    if is_remote_bridge_request(handler):
        return
    delivery.mark_success(handler, reason)
    delivery.commit(body, handler)


def _write_transport_failure(
    *,
    handler: Any,
    provider: str,
    model: str,
    stream: bool,
    core: OpenAIResponsesCore,
    delivery: OpenAIResponsesDelivery,
    output: OpenAIResponsesOutput,
    error: BaseException,
    reason: str,
    request_id: str | None = None,
) -> bool:
    """Preserve a provider connection failure instead of returning local 500."""

    failure = UpstreamFailure.from_transport_error(provider, model, error)
    if failure is None:
        return False
    if not failure.output_started:
        maybe_fail_over(
            handler,
            provider,
            status=failure.status_code,
            reason=failure.message,
            rate_limited=False,
        )
    core.event_bus.publish(
        level="error",
        category="router.transport_error",
        message=failure.detail(),
        request_id=request_id or core.request_id(),
        provider=provider,
        model=model,
        data={
            "error_type": type(error).__name__,
            "transport_error": True,
            "status": failure.status_code,
            "source": reason,
        },
    )
    _log_upstream_error(
        core,
        path=_request_path(handler),
        provider=provider,
        model=model,
        status=failure.status_code,
        error_type=failure.anthropic_error_type,
        message=failure.message,
    )
    delivery.mark_failed(handler, f"{reason}:transport:{type(error).__name__}")
    output.write_error(
        handler,
        failure.message,
        stream=stream,
        status=failure.status_code,
        error_type=failure.anthropic_error_type,
    )
    return True


# A hostname as ``urlsplit`` returns it (IPv6 brackets removed, zone id kept).
_LOGGABLE_HOST = re.compile(r"[A-Za-z0-9._~%:-]+")


def _endpoint_origin(value: Any) -> str:
    """``scheme://host[:port]`` of a configured base URL, and nothing else.

    Userinfo, path, query and fragment are dropped, so a credential carried in
    any of them cannot reach the log.  A value that does not parse to a scheme
    and a plain host is reported as ``invalid`` instead of being echoed.
    """

    text = str(value or "").strip()
    if not text:
        return "unset"
    try:
        parts = urllib.parse.urlsplit(text)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "invalid"
    if not parts.scheme or not _LOGGABLE_HOST.fullmatch(host):
        return "invalid"
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme.lower()}://{host}" + ("" if port is None else f":{port}")


def _handler_downstream_started(handler: Any) -> str:
    """Whether this handler had already sent a response status to the client."""

    status = getattr(handler, "_ciel_runtime_response_status", "unknown")
    if status is None:
        return "false"
    if isinstance(status, int) and not isinstance(status, bool):
        return "true"
    return "unknown"


def _log_native_responses_failure(
    core: OpenAIResponsesCore,
    handler: Any,
    *,
    request_id: str,
    provider: str,
    pcfg: Mapping[str, Any],
    error: BaseException,
) -> None:
    """Persist which endpoint a failed native Responses request was sent to.

    Metadata only: exception class names, never their text, and the request's
    provider config ``base_url`` reduced to scheme, host and port.  ``stage``
    is ``stream`` when the reader reported a truncated response body
    (``UpstreamStreamReadError``) and ``open`` for a ``URLError``, which urllib
    raises only before it reads a response (connect, TLS handshake, sending the
    request); it is ``unknown`` for any other transport exception.  ``open``
    does not mean the provider never received the request.  The node behind a
    selected CielAiRouter gateway is not recorded here.
    """

    if isinstance(error, UpstreamStreamReadError):
        stage = "stream"
        cause: Any = error.error
        downstream_started = "true" if error.downstream_started else "false"
    else:
        stage = "open" if isinstance(error, urllib.error.URLError) else "unknown"
        cause = getattr(error, "reason", None) if stage == "open" else None
        downstream_started = _handler_downstream_started(handler)
    if not isinstance(pcfg, Mapping):
        pcfg = {}
    received = getattr(error, "received_bytes", None)
    received_text = (
        str(received)
        if isinstance(received, int) and not isinstance(received, bool) and received >= 0
        else "unknown"
    )
    gateway = DISCOVERY_BASE_URL_KEY in pcfg
    fields = [
        f"request_id={request_id}",
        f"provider={provider}",
        f"stage={stage}",
        f"error={type(error).__name__}",
        f"cause={type(cause).__name__ if isinstance(cause, BaseException) else '-'}",
        f"downstream_started={downstream_started}",
        f"received_bytes={received_text}",
        f"config_base_url={_endpoint_origin(pcfg.get('base_url'))}",
        f"gateway_selected={'true' if gateway else 'false'}",
    ]
    if gateway:
        fields.append(
            f"gateway_discovery_base_url={_endpoint_origin(pcfg.get(DISCOVERY_BASE_URL_KEY))}"
        )
    core.log("ERROR", "provider_responses_failure " + " ".join(fields))


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
    remote_bridge = is_remote_bridge_request(handler)
    raw_body = copy.deepcopy(body) if not remote_bridge else body
    _core_log(
        core,
        "DEBUG",
        f"responses_request_received path={_request_path(handler)} "
        f"provider={provider} model={body.get('model') or '-'} "
        f"input_items={len(body.get('input')) if isinstance(body.get('input'), list) else 0} "
        f"tools={len(body.get('tools') or [])}",
    )
    if not remote_bridge:
        body = routing.apply_codex_compat_instructions(cfg, provider, pcfg, body)
    if (
        remote_bridge
        and routing.select_protocol(
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
            raw_body=raw_body,
        )
        return
    conversion_body = (
        {**body, REMOTE_BRIDGE_CONFIG_MARKER: True}
        if remote_bridge
        else body
    )
    anthropic_body = conversion.to_anthropic(
        conversion_body,
        conversion.current_alias(cfg),
    )
    if not remote_bridge and routing.maybe_import_session(
        handler,
        anthropic_body,
        client_runtime="codex",
        response_format="openai",
        source_body=body,
    ):
        return
    if not remote_bridge:
        _observe_context_request(core, provider, pcfg, raw_body, "codex", getattr(handler, "headers", None))
    if routing.codex_routed_enabled(provider, pcfg):
        _handle_codex_route(handler, provider, pcfg, body, services, raw_body=raw_body)
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
            raw_body=raw_body,
        )
        return

    stream = bool(body.get("stream", True))
    if not remote_bridge:
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
    if not remote_bridge:
        anthropic_body = conversion.filter_blocked_tools(
            provider, pcfg, anthropic_body
        )
        anthropic_body = conversion.normalize_tool_choice(
            provider, pcfg, anthropic_body
        )
        conversion.write_context_usage(provider, pcfg, anthropic_body, "responses")
        anthropic_body = conversion.strip_advisor_tools(provider, anthropic_body)
    if not remote_bridge:
        anthropic_body = conversion.inject_channel_context(anthropic_body)
        anthropic_body = conversion.inject_tool_result_context(anthropic_body)
        delivery.begin(handler, anthropic_body)
    try:
        if not remote_bridge:
            anthropic_body = routing.normalize_provider_wire(
                provider, pcfg, anthropic_body
            )
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
        if not remote_bridge:
            message = routing.recover_preamble_only_turn(
                handler, provider, pcfg, anthropic_body, message
            )
        projection_body = (
            {**body, REMOTE_BRIDGE_CONFIG_MARKER: True}
            if remote_bridge
            else body
        )
        output.write_response(
            handler,
            message,
            source_body=projection_body,
            stream=stream,
        )
        _complete_local_delivery(
            handler, delivery, anthropic_body, "responses_json"
        )
    except AutomaticContextCompactionCompleted as completed:
        _complete_local_compaction(
            handler, provider, body, anthropic_body, completed, services
        )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        if not remote_bridge:
            _observe_context_failure(core, provider, pcfg, raw_body, raw, "codex", getattr(handler, "headers", None))
        maybe_fail_over(handler, provider, status=exc.code, headers=exc.headers, reason=raw)
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.code,
            error_type=_responses_error_type(exc.code),
            message=raw,
        )
        delivery.mark_failed(handler, f"responses_http_error:{exc.code}")
        output.write_error(
            handler,
            output.upstream_error_message(exc, raw),
            stream=stream,
            status=exc.code,
            error_type=_responses_error_type(exc.code),
            **({"upstream_provider": provider} if exc.code == 401 else {}),
        )
    except UpstreamFailure as exc:
        # The upstream status, type and message were all read before this was
        # raised.  Answering with a generic local 500 is what made a rejected
        # request look like a provider fault to Codex.
        if not remote_bridge:
            _observe_context_failure(core, provider, pcfg, raw_body, exc.body or exc.message, "codex", getattr(handler, "headers", None))
        if not exc.output_started:
            maybe_fail_over(handler, provider, status=exc.status_code, reason=exc.message,
                            rate_limited=exc.category == "rate_limit")
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            message=exc.message,
        )
        delivery.mark_failed(handler, f"responses_upstream_failure:{exc.category}")
        output.write_error(
            handler,
            exc.message,
            stream=stream,
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            **({"upstream_provider": provider} if exc.status_code == 401 else {}),
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
    except FailoverRequested:
        raise
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(handler, f"responses_client_disconnected:{type(exc).__name__}")
            return
        if _write_transport_failure(
            handler=handler,
            provider=provider,
            model=str(anthropic_body.get("model") or ""),
            stream=stream,
            core=core,
            delivery=delivery,
            output=output,
            error=exc,
            reason="responses",
            request_id=request_id,
        ):
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
    _complete_local_delivery(
        handler,
        services.delivery,
        anthropic_body,
        "responses_local_compaction",
    )


def _handle_codex_route(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    services: OpenAIResponsesServices,
    *,
    raw_body: dict[str, Any] | None = None,
) -> None:
    raw_body = copy.deepcopy(body) if raw_body is None else raw_body
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
        if not is_remote_bridge_request(handler):
            _observe_context_failure(core, provider, pcfg, raw_body, raw, "codex", getattr(handler, "headers", None))
        maybe_fail_over(handler, provider, status=exc.code, headers=exc.headers, reason=raw)
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.code,
            error_type=_responses_error_type(exc.code),
            message=raw,
        )
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
        if not is_remote_bridge_request(handler):
            _observe_context_failure(core, provider, pcfg, raw_body, exc.body or exc.message, "codex", getattr(handler, "headers", None))
        if not exc.output_started:
            maybe_fail_over(handler, provider, status=exc.status_code, reason=exc.message,
                            rate_limited=exc.category == "rate_limit")
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            message=exc.message,
        )
        delivery.mark_failed(handler, f"codex_responses_upstream_failure:{exc.category}")
        output.write_error(
            handler,
            exc.message,
            stream=bool(body.get("stream", True)),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
        )
    except FailoverRequested:
        raise
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(handler, f"codex_responses_client_disconnected:{type(exc).__name__}")
            return
        if _write_transport_failure(
            handler=handler,
            provider=provider,
            model=str(body.get("model") or ""),
            stream=bool(body.get("stream", True)),
            core=core,
            delivery=delivery,
            output=output,
            error=exc,
            reason="codex_responses",
            request_id=request_id,
        ):
            return
        delivery.mark_failed(handler, f"codex_responses_error:{type(exc).__name__}")
        output.write_error(handler, f"{type(exc).__name__}: {exc}", stream=bool(body.get("stream", True)))


def _handle_provider_responses_route(
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    services: OpenAIResponsesServices,
    *,
    raw_body: dict[str, Any] | None = None,
) -> None:
    raw_body = copy.deepcopy(body) if raw_body is None else raw_body
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
        _complete_local_delivery(
            handler,
            delivery,
            delivery_body,
            "provider_responses_proxy",
        )
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        if not is_remote_bridge_request(handler):
            _observe_context_failure(core, provider, pcfg, raw_body, raw, "codex", getattr(handler, "headers", None))
        maybe_fail_over(handler, provider, status=exc.code, headers=exc.headers, reason=raw)
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.code,
            error_type=_responses_error_type(exc.code),
            message=raw,
        )
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
            **({"upstream_provider": provider} if exc.code == 401 else {}),
        )
    except UpstreamFailure as exc:
        if not is_remote_bridge_request(handler):
            _observe_context_failure(core, provider, pcfg, raw_body, exc.body or exc.message, "codex", getattr(handler, "headers", None))
        if not exc.output_started:
            maybe_fail_over(handler, provider, status=exc.status_code, reason=exc.message,
                            rate_limited=exc.category == "rate_limit")
        _log_upstream_error(
            core,
            path=_request_path(handler),
            provider=provider,
            model=str(body.get("model") or ""),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            message=exc.message,
        )
        delivery.mark_failed(
            handler, f"provider_responses_upstream_failure:{exc.category}"
        )
        output.write_error(
            handler,
            exc.message,
            stream=bool(body.get("stream", True)),
            status=exc.status_code,
            error_type=exc.anthropic_error_type,
            **({"upstream_provider": provider} if exc.status_code == 401 else {}),
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
        _log_native_responses_failure(
            core,
            handler,
            request_id=request_id,
            provider=provider,
            pcfg=pcfg,
            error=exc,
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
    except FailoverRequested:
        raise
    except Exception as exc:
        if core.is_client_disconnect(exc):
            delivery.mark_failed(
                handler,
                f"provider_responses_client_disconnected:{type(exc).__name__}",
            )
            return
        if is_upstream_transport_error(exc):
            _log_native_responses_failure(
                core,
                handler,
                request_id=request_id,
                provider=provider,
                pcfg=pcfg,
                error=exc,
            )
        if _write_transport_failure(
            handler=handler,
            provider=provider,
            model=str(body.get("model") or ""),
            stream=bool(body.get("stream", True)),
            core=core,
            delivery=delivery,
            output=output,
            error=exc,
            reason="provider_responses",
            request_id=request_id,
        ):
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
