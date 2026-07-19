"""Claude runtime HTTP router."""

from __future__ import annotations

import json
import os
import time
import urllib.error
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from .agent_router import COMMON_RUNTIME_ROUTER_CAPABILITIES, RouterCapability


@dataclass(frozen=True, slots=True)
class ClaudeRouterCore:
    event_bus: Any
    log: Callable[..., Any]
    try_write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterCountTokens:
    estimate_tokens: Callable[..., Any]
    write_context_usage: Callable[..., Any]
    write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterPipeline:
    update_tool_schema_registry: Callable[..., Any]
    router_event_message_preview: Callable[..., Any]
    dump_request_for_trace: Callable[..., Any]
    filter_blocked_tools: Callable[..., Any]
    normalize_tool_choice: Callable[..., Any]
    write_context_usage: Callable[..., Any]
    strip_advisor_tools: Callable[..., Any]
    inject_channel_context: Callable[..., Any]
    inject_tool_result_context: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterShortcuts:
    plan_mode: Callable[..., Any]
    router_debug: Callable[..., Any]
    version: Callable[..., Any]
    channel_clear: Callable[..., Any]
    import_session: Callable[..., Any]
    llm_options: Callable[..., Any]
    api_keys: Callable[..., Any]
    advisor: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterDelivery:
    begin: Callable[..., Any]
    commit: Callable[..., Any]
    mark_failed: Callable[..., Any]
    mark_success: Callable[..., Any]
    is_client_disconnect: Callable[..., Any]
    write_activity: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterRouting:
    forward_ollama: Callable[..., Any]
    forward_openai: Callable[..., Any]
    openai_router_enabled: Callable[..., Any]
    resolve_model: Callable[..., Any]
    opencode_endpoint_kind: Callable[..., Any]
    opencode_provider_names: frozenset[str]
    provider_labels: Mapping[str, str]
    write_json: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterNativeNormalization:
    normalize_provider_wire: Callable[..., Any]
    normalize_thinking: Callable[..., Any]
    normalize_system_roles: Callable[..., Any]
    cap_body: Callable[..., Any]
    apply_request_options: Callable[..., Any]
    rehydrate_thinking: Callable[..., Any]
    ncp_model_id: Callable[..., Any]
    resolve_tool_models: Callable[..., Any]
    normalize_model_options: Callable[..., Any]
    strip_internal_metadata: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterTransport:
    native_base_url: Callable[..., Any]
    native_compat_enabled: Callable[..., Any]
    upstream_base: Callable[..., Any]
    join_url: Callable[..., Any]
    upstream_query: Callable[..., Any]
    provider_headers: Callable[..., Any]
    apply_rate_limit: Callable[..., Any]
    open_request: Callable[..., Any]
    request_timeout: Callable[..., Any]
    idle_timeout: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterResponse:
    rebatch_sse: Callable[..., Any]
    preserves_thinking: Callable[..., Any]
    normalize_stream_tool_use: Callable[..., Any]
    set_stream_timeout: Callable[..., Any]
    normalize_thinking: Callable[..., Any]
    append_tasklist: Callable[..., Any]
    prepend_text: Callable[..., Any]
    rate_limit_notice: Callable[..., Any]
    register_key_cooldown: Callable[..., Any]
    key_from_headers: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ClaudeRouterServices:
    core: ClaudeRouterCore
    count_tokens: ClaudeRouterCountTokens
    pipeline: ClaudeRouterPipeline
    shortcuts: ClaudeRouterShortcuts
    delivery: ClaudeRouterDelivery
    routing: ClaudeRouterRouting
    normalization: ClaudeRouterNativeNormalization
    transport: ClaudeRouterTransport
    response: ClaudeRouterResponse


def handle_claude_count_tokens_post(
    services: ClaudeRouterServices,
    handler: Any,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> None:
    count = services.count_tokens
    tokens = count.estimate_tokens(body)
    count.write_context_usage(provider, pcfg, body, "count_tokens")
    count.write_json(handler, {"input_tokens": tokens})


def handle_claude_messages_post(
    services: ClaudeRouterServices,
    handler: Any,
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    path: str,
    body: dict[str, Any],
) -> None:
    core = services.core
    pipeline = services.pipeline
    shortcuts = services.shortcuts
    delivery = services.delivery
    routing = services.routing
    normalization = services.normalization
    transport = services.transport
    response = services.response
    _rebatch_anthropic_sse_text = response.rebatch_sse
    _update_tool_schema_registry = pipeline.update_tool_schema_registry
    append_synthetic_tasklist_to_message = response.append_tasklist
    apply_provider_request_options = normalization.apply_request_options
    apply_router_rate_limit = transport.apply_rate_limit
    begin_pending_channel_delivery = delivery.begin
    body_with_channel_tool_result_context = pipeline.inject_tool_result_context
    body_with_pending_channel_messages = pipeline.inject_channel_context
    body_without_ciel_runtime_internal_metadata = normalization.strip_internal_metadata
    cap_anthropic_body_for_provider = normalization.cap_body
    commit_pending_channel_delivery_cursors = delivery.commit
    dump_request_for_trace = pipeline.dump_request_for_trace
    event_bus = core.event_bus
    filter_blocked_tools = pipeline.filter_blocked_tools
    forward_ollama_api_chat = routing.forward_ollama
    forward_openai_compatible_chat = routing.forward_openai
    is_client_disconnect_error = delivery.is_client_disconnect
    join_url = transport.join_url
    key_from_request_headers = response.key_from_headers
    mark_pending_channel_delivery_failed = delivery.mark_failed
    mark_pending_channel_delivery_success = delivery.mark_success
    maybe_handle_advisor_request = shortcuts.advisor
    maybe_handle_channel_clear_request = shortcuts.channel_clear
    maybe_handle_import_session_request = shortcuts.import_session
    maybe_handle_live_api_keys_request = shortcuts.api_keys
    maybe_handle_live_llm_options_request = shortcuts.llm_options
    maybe_handle_plan_mode_tool_choice = shortcuts.plan_mode
    maybe_handle_router_debug_request = shortcuts.router_debug
    maybe_handle_version_request = shortcuts.version
    native_anthropic_base_url = transport.native_base_url
    ncp_model_id_for_nvidia_hosted = normalization.ncp_model_id
    normalize_anthropic_model_request_options = normalization.normalize_model_options
    normalize_anthropic_system_role_messages = normalization.normalize_system_roles
    normalize_request_for_provider_wire = normalization.normalize_provider_wire
    normalize_response_thinking_for_non_anthropic_provider = response.normalize_thinking
    normalize_thinking_for_non_anthropic_provider = normalization.normalize_thinking
    normalize_tool_choice_for_provider = pipeline.normalize_tool_choice
    open_provider_request_with_key_retry = transport.open_request
    opencode_endpoint_kind = routing.opencode_endpoint_kind
    opencode_provider_names = routing.opencode_provider_names
    prepend_anthropic_text = response.prepend_text
    preserves_anthropic_thinking_contract = response.preserves_thinking
    provider_headers = transport.provider_headers
    provider_labels = routing.provider_labels
    provider_native_compat_enabled = transport.native_compat_enabled
    provider_openai_router_enabled = routing.openai_router_enabled
    provider_request_timeout_seconds = transport.request_timeout
    provider_stream_idle_timeout_seconds = transport.idle_timeout
    provider_upstream_request_base = transport.upstream_base
    rate_limit_notice = response.rate_limit_notice
    register_api_key_cooldown = response.register_key_cooldown
    rehydrate_suppressed_thinking_passback = normalization.rehydrate_thinking
    resolve_requested_model = routing.resolve_model
    resolve_tool_model_references = normalization.resolve_tool_models
    router_event_message_preview = pipeline.router_event_message_preview
    router_log = core.log
    set_upstream_stream_read_timeout = response.set_stream_timeout
    should_normalize_anthropic_stream_tool_use = response.normalize_stream_tool_use
    strip_autonomous_advisor_server_tools = pipeline.strip_advisor_tools
    try_write_json = core.try_write_json
    upstream_messages_query = transport.upstream_query
    write_context_usage = pipeline.write_context_usage
    write_json = routing.write_json
    write_router_activity = delivery.write_activity

    self = handler
    _update_tool_schema_registry(body.get("tools"))
    body = normalize_thinking_for_non_anthropic_provider(provider, pcfg, body)
    request_id = f"{os.getpid()}-{time.time_ns()}"
    event_bus.publish(
        level="info",
        category="router.request",
        message="Anthropic messages request received",
        request_id=request_id,
        provider=provider,
        model=str(body.get("model") or ""),
        data={
            "path": path,
            "messages": len(body.get("messages") or []),
            "tools": len(body.get("tools") or []),
            **router_event_message_preview(body, cfg),
        },
    )
    dump_request_for_trace(provider, path, body)
    if maybe_handle_plan_mode_tool_choice(self, provider, pcfg, body):
        event_bus.publish(level="info", category="plan_mode.short_circuit", message="plan mode tool choice handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    body = filter_blocked_tools(provider, pcfg, body)
    body = normalize_tool_choice_for_provider(provider, pcfg, body)
    write_context_usage(provider, pcfg, body, "messages")
    if maybe_handle_router_debug_request(self, body):
        event_bus.publish(level="info", category="router_debug.short_circuit", message="router debug request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_version_request(self, body):
        event_bus.publish(level="info", category="version.short_circuit", message="version request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_channel_clear_request(self, body):
        event_bus.publish(level="info", category="channel_clear.short_circuit", message="channel backlog clear request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_import_session_request(self, body, client_runtime="claude"):
        event_bus.publish(level="info", category="import_session.short_circuit", message="ImportSession request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_live_llm_options_request(self, body):
        event_bus.publish(level="info", category="llm_options.short_circuit", message="live LLM options request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_live_api_keys_request(self, body):
        event_bus.publish(level="info", category="api_keys.short_circuit", message="live API key request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    if maybe_handle_advisor_request(self, provider, pcfg, body):
        event_bus.publish(level="info", category="advisor.short_circuit", message="advisor request handled locally", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
        return
    body = strip_autonomous_advisor_server_tools(provider, body)
    body = body_with_pending_channel_messages(body)
    body = body_with_channel_tool_result_context(body)
    begin_pending_channel_delivery(self, body)
    body = normalize_request_for_provider_wire(provider, pcfg, body)
    router_log("DEBUG", f"POST {path} provider={provider} model={body.get('model')} tools={len(body.get('tools') or [])} msgs={len(body.get('messages') or [])}")
    try:
        if provider in ("ollama", "ollama-cloud"):
            event_bus.publish(level="info", category="upstream.request", message="forwarding to Ollama-compatible provider", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
            forward_ollama_api_chat(self, provider, pcfg, body)
            commit_pending_channel_delivery_cursors(body, self)
            return
        if provider in opencode_provider_names:
            upstream_model = resolve_requested_model(provider, pcfg, body.get("model"))
            endpoint_kind = opencode_endpoint_kind(provider, upstream_model, pcfg)
            provider_label = provider_labels.get(provider, provider)
            if endpoint_kind == "openai-chat":
                event_bus.publish(
                    level="info",
                    category="upstream.request",
                    message=f"forwarding to {provider_label} chat-compatible provider",
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                )
                forward_openai_compatible_chat(self, provider, pcfg, body)
                commit_pending_channel_delivery_cursors(body, self)
                return
            if endpoint_kind not in ("anthropic-messages",):
                write_json(
                    self,
                    {
                        "type": "error",
                        "error": {
                            "type": "unsupported_model_endpoint",
                            "message": (
                                f"{provider_label} model {upstream_model!r} uses the {endpoint_kind} endpoint family. "
                                f"ciel-runtime currently routes {provider_label} /v1/messages and /v1/chat/completions models."
                            ),
                        },
                    },
                    400,
                )
                return
        if provider_openai_router_enabled(provider, pcfg):
            event_bus.publish(level="info", category="upstream.request", message="forwarding to OpenAI-compatible provider", request_id=request_id, provider=provider, model=str(body.get("model") or ""))
            forward_openai_compatible_chat(self, provider, pcfg, body)
            commit_pending_channel_delivery_cursors(body, self)
            return
        body = normalize_thinking_for_non_anthropic_provider(provider, pcfg, body)
        body = normalize_anthropic_system_role_messages(body)
        body = cap_anthropic_body_for_provider(provider, pcfg, body)
        body = apply_provider_request_options(provider, pcfg, body)
        body = rehydrate_suppressed_thinking_passback(provider, pcfg, body)
        upstream_model = resolve_requested_model(provider, pcfg, body.get("model"))
        if provider == "nvidia-hosted":
            upstream_model = ncp_model_id_for_nvidia_hosted(upstream_model)
        body["model"] = upstream_model
        body = resolve_tool_model_references(provider, pcfg, body)
        body = normalize_anthropic_model_request_options(provider, pcfg, body, upstream_model)
        stream_enabled = bool(pcfg.get("stream_enabled", True))
        word_chunking = bool(pcfg.get("stream_word_chunking", False))
        if not stream_enabled:
            body["stream"] = False
        upstream_body = body_without_ciel_runtime_internal_metadata(body)
        base = native_anthropic_base_url(provider, pcfg) if provider_native_compat_enabled(provider, pcfg) else provider_upstream_request_base(provider, pcfg)
        url = join_url(base, "/v1/messages")
        upstream_query = upstream_messages_query(pcfg, self.path, provider)
        if upstream_query:
            url = f"{url}?{upstream_query}"
        headers = provider_headers(provider, pcfg, self.headers)
        for h in ("anthropic-beta", "anthropic-dangerous-direct-browser-access"):
            if self.headers.get(h):
                headers[h] = self.headers[h]
        waited, rpm_used, rpm_limit = apply_router_rate_limit(provider, pcfg, upstream_model)
        try:
            event_bus.publish(level="info", category="upstream.request", message="forwarding to Anthropic-compatible provider", request_id=request_id, provider=provider, model=upstream_model, data={"url": url, "stream": bool(body.get("stream", stream_enabled))})
            resp = open_provider_request_with_key_retry(
                url,
                upstream_body,
                headers,
                provider_request_timeout_seconds(pcfg),
                provider,
                pcfg,
                upstream_model,
                stream=bool(body.get("stream", stream_enabled)),
            )
            if bool(body.get("stream", stream_enabled)):
                set_upstream_stream_read_timeout(resp, provider_stream_idle_timeout_seconds(pcfg))
            status = getattr(resp, "status", 200)
            ctype = resp.headers.get("content-type", "application/json")
            if stream_enabled and "text/event-stream" in ctype:
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.send_header("cache-control", "no-cache")
                self.send_header("connection", "close")
                self.end_headers()
                _rebatch_anthropic_sse_text(
                    self,
                    resp,
                    upstream_model,
                    word_chunking=word_chunking,
                    source_body=body,
                    preserve_thinking=preserves_anthropic_thinking_contract(provider, pcfg),
                    normalize_tool_use=should_normalize_anthropic_stream_tool_use(provider, pcfg),
                    provider=provider,
                )
            else:
                self.send_response(status)
                self.send_header("content-type", ctype)
                self.end_headers()
                raw_resp = resp.read()
                notice = rate_limit_notice(waited, rpm_used, rpm_limit, bool(pcfg.get("rate_limit_status", False)))
                if "application/json" in ctype:
                    try:
                        payload = json.loads(raw_resp.decode("utf-8", errors="replace"))
                        if isinstance(payload, dict):
                            payload = normalize_response_thinking_for_non_anthropic_provider(provider, pcfg, payload, upstream_model)
                            payload = append_synthetic_tasklist_to_message(payload, upstream_model, body, "native_json", provider=provider)
                            if notice:
                                payload = prepend_anthropic_text(payload, notice)
                            raw_resp = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                        router_log(
                            "WARN",
                            "anthropic_response_normalization_skipped "
                            f"provider={provider} model={upstream_model} error={type(exc).__name__}: {exc}",
                        )
                self.wfile.write(raw_resp)
                self.wfile.flush()
                mark_pending_channel_delivery_success(self, "anthropic_json")
            commit_pending_channel_delivery_cursors(body, self)
        except urllib.error.HTTPError as e:
            err = e.read()
            if e.code == 429:
                register_api_key_cooldown(provider, pcfg, key_from_request_headers(headers), e.headers)
            event_bus.publish(level="error", category="upstream.error", message=f"upstream HTTP {e.code}", request_id=request_id, provider=provider, model=upstream_model, data={"status": e.code})
            self.send_response(e.code)
            self.send_header("content-type", e.headers.get("content-type", "application/json"))
            self.end_headers()
            self.wfile.write(err)
    except Exception as exc:
        if is_client_disconnect_error(exc):
            mark_pending_channel_delivery_failed(self, f"client_disconnected:{type(exc).__name__}")
            write_router_activity(
                "cancel",
                provider,
                str(body.get("model") or ""),
                error=type(exc).__name__,
                stream=bool(body.get("stream", True)),
            )
            router_log(
                "WARN",
                f"router_client_disconnected provider={provider} model={body.get('model')} error={type(exc).__name__}: {exc}",
            )
            event_bus.publish(
                level="warning",
                category="router.client_disconnected",
                message=f"client disconnected: {type(exc).__name__}",
                request_id=request_id,
                provider=provider,
                model=str(body.get("model") or ""),
            )
            return
        event_bus.publish(level="error", category="router.error", message=str(exc), request_id=request_id, provider=provider, model=str(body.get("model") or ""), data={"error_type": type(exc).__name__})
        try_write_json(self, {"type": "error", "error": {"type": "api_error", "message": str(exc)}}, 500)


class ClaudeRouter:
    name = "claude"
    runtime = "claude-code"
    protocol = "anthropic_messages"
    request_paths = ("/v1/messages", "/v1/messages/count_tokens")
    capabilities = tuple(
        RouterCapability(name, description)
        for name, description in (
            ("auth_forwarding", "Provider/API-key headers are prepared for Anthropic-compatible upstreams."),
            ("sse_stream_proxy", "Anthropic SSE streams are proxied and normalized for Claude Code."),
            ("channel_context_injection", "Pending external channel messages are injected before upstream calls."),
            ("pending_delivery_ack", "Injected channel cursors are committed after successful delivery."),
            ("request_observability", "Requests are traced and published to the runtime event bus."),
            ("upstream_error_mapping", "Upstream HTTP and client disconnect errors are mapped for the runtime."),
            ("token_count", "Claude Code token-count requests are handled locally."),
            ("tool_choice_short_circuit", "Runtime management tool choices can be handled locally."),
        )
    )

    def __init__(
        self,
        *,
        services: ClaudeRouterServices | None = None,
        handle_count_tokens_post: Callable[[Any, str, dict[str, Any], dict[str, Any]], None] | None = None,
        handle_messages_post: Callable[[Any, dict[str, Any], str, dict[str, Any], str, dict[str, Any]], None] | None = None,
    ) -> None:
        if services is not None:
            self._handle_count_tokens_post = (
                lambda handler, provider, pcfg, body: handle_claude_count_tokens_post(
                    services,
                    handler,
                    provider,
                    pcfg,
                    body,
                )
            )
            self._handle_messages_post = (
                lambda handler, cfg, provider, pcfg, path, body: handle_claude_messages_post(
                    services,
                    handler,
                    cfg,
                    provider,
                    pcfg,
                    path,
                    body,
                )
            )
            return
        if handle_count_tokens_post is None or handle_messages_post is None:
            raise TypeError("ClaudeRouter requires services or both post handlers")
        self._handle_count_tokens_post = handle_count_tokens_post
        self._handle_messages_post = handle_messages_post

    def can_handle_get(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        del path, provider, pcfg
        return False

    def handle_get(self, handler: Any, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        del handler, path, provider, pcfg
        return False

    def can_handle_post(self, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
        del provider, pcfg
        return path in self.request_paths

    def handle_post(
        self,
        handler: Any,
        cfg: dict[str, Any],
        provider: str,
        pcfg: dict[str, Any],
        path: str,
        body: dict[str, Any],
    ) -> bool:
        if path == "/v1/messages/count_tokens":
            self._handle_count_tokens_post(handler, provider, pcfg, body)
            return True
        if path == "/v1/messages":
            self._handle_messages_post(handler, cfg, provider, pcfg, path, body)
            return True
        return False


assert all(any(capability.name == required for capability in ClaudeRouter.capabilities) for required in COMMON_RUNTIME_ROUTER_CAPABILITIES)


__all__ = [
    "ClaudeRouter",
    "ClaudeRouterCore",
    "ClaudeRouterCountTokens",
    "ClaudeRouterDelivery",
    "ClaudeRouterNativeNormalization",
    "ClaudeRouterPipeline",
    "ClaudeRouterResponse",
    "ClaudeRouterRouting",
    "ClaudeRouterServices",
    "ClaudeRouterShortcuts",
    "ClaudeRouterTransport",
    "handle_claude_count_tokens_post",
    "handle_claude_messages_post",
]
