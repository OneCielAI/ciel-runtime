"""Compose OpenAI Responses and Claude router request pipelines from typed ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import claude_router, openai_responses_router
from .router_request_context import (
    RouterRequestContext,
    RouterRequestPorts,
    RuntimeRouterPorts,
)


Callback = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class OpenAIResponseCorePorts:
    event_bus: Any
    request_id: Callback
    input_as_list: Callback
    is_client_disconnect: Callback
    log: Callback


@dataclass(frozen=True, slots=True)
class OpenAIResponseConversionPorts:
    to_anthropic: Callback
    current_alias: Callback
    update_tool_schema: Callback
    normalize_thinking: Callback
    filter_blocked_tools: Callback
    normalize_tool_choice: Callback
    write_context_usage: Callback
    strip_advisor_tools: Callback
    inject_channel_context: Callback
    inject_tool_result_context: Callback


@dataclass(frozen=True, slots=True)
class OpenAIResponseRoutingPorts:
    maybe_import_session: Callback
    codex_routed_enabled: Callback
    forward_codex: Callback
    select_protocol: Callback
    forward_provider_responses: Callback
    dump_request: Callback
    normalize_provider_wire: Callback
    collect_message: Callback


@dataclass(frozen=True, slots=True)
class OpenAIResponseDeliveryPorts:
    begin: Callback
    mark_success: Callback
    mark_failed: Callback
    commit: Callback


@dataclass(frozen=True, slots=True)
class OpenAIResponseOutputPorts:
    write_response: Callback
    write_error: Callback
    upstream_error_message: Callback
    codex_auth_error_message: Callback
    event_preview: Callback


@dataclass(frozen=True, slots=True)
class OpenAIResponseAssembly:
    core: OpenAIResponseCorePorts
    conversion: OpenAIResponseConversionPorts
    routing: OpenAIResponseRoutingPorts
    delivery: OpenAIResponseDeliveryPorts
    output: OpenAIResponseOutputPorts

    def services(self) -> openai_responses_router.OpenAIResponsesServices:
        return openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=self.core.event_bus,
                request_id=self.core.request_id,
                input_as_list=self.core.input_as_list,
                is_client_disconnect=self.core.is_client_disconnect,
                log=self.core.log,
            ),
            conversion=openai_responses_router.OpenAIResponsesConversion(
                to_anthropic=self.conversion.to_anthropic,
                current_alias=self.conversion.current_alias,
                update_tool_schema=self.conversion.update_tool_schema,
                normalize_thinking=self.conversion.normalize_thinking,
                filter_blocked_tools=self.conversion.filter_blocked_tools,
                normalize_tool_choice=self.conversion.normalize_tool_choice,
                write_context_usage=self.conversion.write_context_usage,
                strip_advisor_tools=self.conversion.strip_advisor_tools,
                inject_channel_context=self.conversion.inject_channel_context,
                inject_tool_result_context=self.conversion.inject_tool_result_context,
            ),
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=self.routing.maybe_import_session,
                codex_routed_enabled=self.routing.codex_routed_enabled,
                forward_codex=self.routing.forward_codex,
                select_protocol=self.routing.select_protocol,
                forward_provider_responses=self.routing.forward_provider_responses,
                dump_request=self.routing.dump_request,
                normalize_provider_wire=self.routing.normalize_provider_wire,
                collect_message=self.routing.collect_message,
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=self.delivery.begin,
                mark_success=self.delivery.mark_success,
                mark_failed=self.delivery.mark_failed,
                commit=self.delivery.commit,
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
                write_response=self.output.write_response,
                write_error=self.output.write_error,
                upstream_error_message=self.output.upstream_error_message,
                codex_auth_error_message=self.output.codex_auth_error_message,
                event_preview=self.output.event_preview,
            ),
        )


@dataclass(frozen=True, slots=True)
class ClaudeRouterCorePorts:
    event_bus: Any
    log: Callback
    try_write_json: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterCountPorts:
    estimate_tokens: Callback
    write_context_usage: Callback
    write_json: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterPipelinePorts:
    update_tool_schema: Callback
    event_preview: Callback
    dump_request: Callback
    filter_blocked_tools: Callback
    normalize_tool_choice: Callback
    write_context_usage: Callback
    strip_advisor_tools: Callback
    inject_channel_context: Callback
    inject_tool_result_context: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterShortcutPorts:
    plan_mode: Callback
    router_debug: Callback
    version: Callback
    channel_clear: Callback
    import_session: Callback
    llm_options: Callback
    api_keys: Callback
    advisor: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterDeliveryPorts:
    begin: Callback
    commit: Callback
    mark_failed: Callback
    mark_success: Callback
    is_client_disconnect: Callback
    write_activity: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterRoutingPorts:
    forward_ollama: Callback
    forward_openai: Callback
    select_protocol: Callback
    request_policy: Callback
    resolve_model: Callback
    provider_labels: Mapping[str, str]
    write_json: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterNormalizationPorts:
    normalize_provider_wire: Callback
    normalize_thinking: Callback
    normalize_system_roles: Callback
    cap_body: Callback
    apply_request_options: Callback
    rehydrate_thinking: Callback
    ncp_model_id: Callback
    resolve_tool_models: Callback
    normalize_model_options: Callback
    strip_internal_metadata: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterTransportPorts:
    native_base_url: Callback
    native_compat_enabled: Callback
    upstream_base: Callback
    join_url: Callback
    upstream_query: Callback
    provider_headers: Callback
    apply_rate_limit: Callback
    open_request: Callback
    request_timeout: Callback
    idle_timeout: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterResponsePorts:
    rebatch_sse: Callback
    preserves_thinking: Callback
    normalize_stream_tool_use: Callback
    set_stream_timeout: Callback
    normalize_thinking: Callback
    append_tasklist: Callback
    prepend_text: Callback
    rate_limit_notice: Callback
    register_key_cooldown: Callback
    key_from_headers: Callback


@dataclass(frozen=True, slots=True)
class ClaudeRouterAssembly:
    core: ClaudeRouterCorePorts
    count_tokens: ClaudeRouterCountPorts
    pipeline: ClaudeRouterPipelinePorts
    shortcuts: ClaudeRouterShortcutPorts
    delivery: ClaudeRouterDeliveryPorts
    routing: ClaudeRouterRoutingPorts
    normalization: ClaudeRouterNormalizationPorts
    transport: ClaudeRouterTransportPorts
    response: ClaudeRouterResponsePorts

    def services(self) -> claude_router.ClaudeRouterServices:
        return claude_router.ClaudeRouterServices(
            core=claude_router.ClaudeRouterCore(
                event_bus=self.core.event_bus,
                log=self.core.log,
                try_write_json=self.core.try_write_json,
            ),
            count_tokens=claude_router.ClaudeRouterCountTokens(
                estimate_tokens=self.count_tokens.estimate_tokens,
                write_context_usage=self.count_tokens.write_context_usage,
                write_json=self.count_tokens.write_json,
            ),
            pipeline=claude_router.ClaudeRouterPipeline(
                update_tool_schema_registry=self.pipeline.update_tool_schema,
                router_event_message_preview=self.pipeline.event_preview,
                dump_request_for_trace=self.pipeline.dump_request,
                filter_blocked_tools=self.pipeline.filter_blocked_tools,
                normalize_tool_choice=self.pipeline.normalize_tool_choice,
                write_context_usage=self.pipeline.write_context_usage,
                strip_advisor_tools=self.pipeline.strip_advisor_tools,
                inject_channel_context=self.pipeline.inject_channel_context,
                inject_tool_result_context=self.pipeline.inject_tool_result_context,
            ),
            shortcuts=claude_router.ClaudeRouterShortcuts(
                plan_mode=self.shortcuts.plan_mode,
                router_debug=self.shortcuts.router_debug,
                version=self.shortcuts.version,
                channel_clear=self.shortcuts.channel_clear,
                import_session=self.shortcuts.import_session,
                llm_options=self.shortcuts.llm_options,
                api_keys=self.shortcuts.api_keys,
                advisor=self.shortcuts.advisor,
            ),
            delivery=claude_router.ClaudeRouterDelivery(
                begin=self.delivery.begin,
                commit=self.delivery.commit,
                mark_failed=self.delivery.mark_failed,
                mark_success=self.delivery.mark_success,
                is_client_disconnect=self.delivery.is_client_disconnect,
                write_activity=self.delivery.write_activity,
            ),
            routing=claude_router.ClaudeRouterRouting(
                forward_ollama=self.routing.forward_ollama,
                forward_openai=self.routing.forward_openai,
                select_protocol=self.routing.select_protocol,
                request_policy=self.routing.request_policy,
                resolve_model=self.routing.resolve_model,
                provider_labels=self.routing.provider_labels,
                write_json=self.routing.write_json,
            ),
            normalization=claude_router.ClaudeRouterNativeNormalization(
                normalize_provider_wire=self.normalization.normalize_provider_wire,
                normalize_thinking=self.normalization.normalize_thinking,
                normalize_system_roles=self.normalization.normalize_system_roles,
                cap_body=self.normalization.cap_body,
                apply_request_options=self.normalization.apply_request_options,
                rehydrate_thinking=self.normalization.rehydrate_thinking,
                ncp_model_id=self.normalization.ncp_model_id,
                resolve_tool_models=self.normalization.resolve_tool_models,
                normalize_model_options=self.normalization.normalize_model_options,
                strip_internal_metadata=self.normalization.strip_internal_metadata,
            ),
            transport=claude_router.ClaudeRouterTransport(
                native_base_url=self.transport.native_base_url,
                native_compat_enabled=self.transport.native_compat_enabled,
                upstream_base=self.transport.upstream_base,
                join_url=self.transport.join_url,
                upstream_query=self.transport.upstream_query,
                provider_headers=self.transport.provider_headers,
                apply_rate_limit=self.transport.apply_rate_limit,
                open_request=self.transport.open_request,
                request_timeout=self.transport.request_timeout,
                idle_timeout=self.transport.idle_timeout,
            ),
            response=claude_router.ClaudeRouterResponse(
                rebatch_sse=self.response.rebatch_sse,
                preserves_thinking=self.response.preserves_thinking,
                normalize_stream_tool_use=self.response.normalize_stream_tool_use,
                set_stream_timeout=self.response.set_stream_timeout,
                normalize_thinking=self.response.normalize_thinking,
                append_tasklist=self.response.append_tasklist,
                prepend_text=self.response.prepend_text,
                rate_limit_notice=self.response.rate_limit_notice,
                register_key_cooldown=self.response.register_key_cooldown,
                key_from_headers=self.response.key_from_headers,
            ),
        )


@dataclass(frozen=True, slots=True)
class RouterRequestOuterPorts:
    forward_backend_json: Callback
    forward_backend_get: Callback
    write_responses_error: Callback
    write_json: Callback
    upstream_error_message: Callback
    is_client_disconnect: Callback


@dataclass(frozen=True, slots=True)
class RouterRequestAssembly:
    openai: OpenAIResponseAssembly
    outer: RouterRequestOuterPorts
    codex_routed_enabled: Callback
    forward_provider_chat: Callback
    claude: ClaudeRouterAssembly

    def context(self) -> RouterRequestContext:
        return RouterRequestContext(
            request=RouterRequestPorts(
                openai_responses=self.openai.services(),
                forward_backend_json=self.outer.forward_backend_json,
                forward_backend_get=self.outer.forward_backend_get,
                write_responses_error=self.outer.write_responses_error,
                write_json=self.outer.write_json,
                upstream_error_message=self.outer.upstream_error_message,
                is_client_disconnect=self.outer.is_client_disconnect,
            ),
            runtime=RuntimeRouterPorts(
                codex_routed_enabled=self.codex_routed_enabled,
                forward_provider_chat=self.forward_provider_chat,
                claude_services=self.claude.services(),
            ),
        )


__all__ = [
    "ClaudeRouterAssembly",
    "ClaudeRouterCorePorts",
    "ClaudeRouterCountPorts",
    "ClaudeRouterDeliveryPorts",
    "ClaudeRouterNormalizationPorts",
    "ClaudeRouterPipelinePorts",
    "ClaudeRouterResponsePorts",
    "ClaudeRouterRoutingPorts",
    "ClaudeRouterShortcutPorts",
    "ClaudeRouterTransportPorts",
    "OpenAIResponseAssembly",
    "OpenAIResponseConversionPorts",
    "OpenAIResponseCorePorts",
    "OpenAIResponseDeliveryPorts",
    "OpenAIResponseOutputPorts",
    "OpenAIResponseRoutingPorts",
    "RouterRequestAssembly",
    "RouterRequestOuterPorts",
]
