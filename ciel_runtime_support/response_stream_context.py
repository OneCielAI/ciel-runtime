"""Provider response normalization and streaming bridge bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable

from . import streaming_anthropic
from .openai_responses_stream import OpenAIResponsesStreamServices
from .protocols.ollama_response import (
    OllamaResponseOutput,
    OllamaResponseRecovery,
    OllamaResponseServices,
    OllamaResponseText,
    OllamaResponseTools,
)
from .pseudo_tool_parser import PseudoToolParserServices


@dataclass(frozen=True, slots=True)
class ResponseStreamAlgorithms:
    normalize_tool_arguments: Callable[..., dict[str, Any]]
    infer_tool_name: Callable[[dict[str, Any]], str]
    parse_pseudo_tool_calls: Callable[..., tuple[str, list[dict[str, Any]]]]
    project_ollama_response: Callable[..., dict[str, Any]]
    project_openai_chat_response: Callable[..., dict[str, Any]]
    split_word_buffer: Callable[..., tuple[str, str]]
    write_openai_response: Callable[..., Any]
    write_openai_error: Callable[..., Any]
    protocol_adapter: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ResponseStreamTextPorts:
    decode_ollama: Callable[..., Any]
    ollama_thinking_to_block: Callable[..., Any]
    ollama_reasoning_only_notice: Callable[..., str]
    strip_thinking: Callable[..., str]
    parse_xml_tools: Callable[..., Any]
    find_pseudo_xml_start: Callable[..., Any]
    fuzzy_tool_name: Callable[..., Any]
    reasoning_to_thinking: Callable[..., Any]
    anthropic_content_to_text: Callable[..., str]
    positive_int: Callable[..., int | None]


@dataclass(frozen=True, slots=True)
class ResponseStreamToolPorts:
    resolve_name: Callable[..., str]
    validate_input: Callable[..., Any]
    plan_mode_name: Callable[..., Any]
    cap_notification_wait: Callable[..., dict[str, Any]]
    should_drop: Callable[..., bool]
    should_drop_duplicate: Callable[..., bool]
    append_log: Callable[..., Any]
    remember_tool_use: Callable[..., Any]
    repair_passthrough_input: Callable[..., bool]
    is_mcp_notification_wait: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ResponseStreamRecoveryPorts:
    auto_enter_plan: Callable[..., bool]
    auto_exit_plan: Callable[..., bool]
    recover_empty: Callable[..., bool]
    keep_alive: Callable[..., bool]
    auto_continue_choice: Callable[..., bool]
    empty_notice: Callable[..., str]
    latest_tool_results: Callable[..., list[str]]
    synthetic_tool_response: Callable[..., dict[str, Any]]
    synthesize_tasklist: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ResponseStreamConversationPorts:
    backfill_exit_plan: Callable[..., Any]
    ultracode_enabled: Callable[..., bool]
    has_tool: Callable[..., bool]
    latest_intent_index: Callable[..., int]
    suggestion_mode: Callable[..., bool]
    recent_tasklist_count: Callable[..., int]
    remember_suppressed_thinking: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ResponseStreamIoPorts:
    encode_message: Callable[..., dict[str, Any]]
    estimate_tokens: Callable[..., int]
    log: Callable[..., Any]
    mark_delivery_failed: Callable[..., Any]
    mark_delivery_success: Callable[..., Any]
    client_connection_closed: Callable[..., bool]
    iter_upstream_lines: Callable[..., Any]
    write_activity: Callable[..., Any]
    write_json: Callable[..., Any]
    write_open_stream_stop: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ResponseStreamTracePorts:
    dump_response: Callable[..., Any]
    finish_sse: Callable[..., Any]
    make_sse: Callable[..., Any]
    record_sse: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ResponseStreamRuntimePorts:
    timestamp_ms: Callable[[], int]
    process_id: Callable[[], int]


@dataclass(frozen=True, slots=True)
class ResponseStreamTypes:
    thinking_block_types: Any
    visible_tool_filter: Any
    visible_thinking_filter: Any
    client_disconnected_error: Any
    pseudo_tool_start: str
    pseudo_tool_end: str
    word_chunk_max_buffer: int


@dataclass(frozen=True, slots=True)
class ResponseStreamContext:
    algorithms: ResponseStreamAlgorithms
    text: ResponseStreamTextPorts
    tools: ResponseStreamToolPorts
    recovery: ResponseStreamRecoveryPorts
    conversation: ResponseStreamConversationPorts
    io: ResponseStreamIoPorts
    trace: ResponseStreamTracePorts
    runtime: ResponseStreamRuntimePorts
    types: ResponseStreamTypes

    def normalize_tool_arguments(
        self, tool_name: str, args: Any
    ) -> dict[str, Any]:
        return self.algorithms.normalize_tool_arguments(tool_name, args)

    def infer_tool_name(self, args: dict[str, Any]) -> str:
        return self.algorithms.infer_tool_name(args)

    def parse_pseudo_tool_calls(
        self,
        text: str,
        source_body: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        return self.algorithms.parse_pseudo_tool_calls(
            text,
            source_body,
            PseudoToolParserServices(
                parse_xml=self.text.parse_xml_tools,
                fuzzy_tool_name=self.text.fuzzy_tool_name,
            ),
        )

    def ollama_response_services(self) -> OllamaResponseServices:
        return OllamaResponseServices(
            text=OllamaResponseText(
                decode=self.text.decode_ollama,
                thinking_to_block=self.text.ollama_thinking_to_block,
                reasoning_only_notice=self.text.ollama_reasoning_only_notice,
                strip_thinking=self.text.strip_thinking,
                parse_pseudo_tools=self.parse_pseudo_tool_calls,
                log=self.io.log,
            ),
            tools=OllamaResponseTools(
                resolve_name=self.tools.resolve_name,
                normalize_arguments=self.normalize_tool_arguments,
                validate_input=self.tools.validate_input,
                plan_mode_name=self.tools.plan_mode_name,
                cap_notification_wait=self.tools.cap_notification_wait,
                should_drop=self.tools.should_drop,
                should_drop_duplicate=self.tools.should_drop_duplicate,
                append_log=self.tools.append_log,
            ),
            recovery=OllamaResponseRecovery(
                auto_enter_plan=self.recovery.auto_enter_plan,
                recover_empty_with_tasklist=self.recovery.recover_empty,
                keep_alive_with_tasklist=self.recovery.keep_alive,
                auto_continue_choice=self.recovery.auto_continue_choice,
                empty_notice=self.recovery.empty_notice,
                latest_tool_result_names=self.recovery.latest_tool_results,
                synthetic_tool_response=self.recovery.synthetic_tool_response,
            ),
            output=OllamaResponseOutput(
                encode_message=self.io.encode_message,
                estimate_tokens=self.io.estimate_tokens,
                timestamp_ms=self.runtime.timestamp_ms,
                process_id=self.runtime.process_id,
            ),
        )

    def ollama_chat_to_anthropic(
        self,
        data: dict[str, Any],
        model: str,
        source_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.algorithms.project_ollama_response(
            data,
            model,
            source_body,
            self.ollama_response_services(),
        )

    def split_word_buffer(
        self,
        buf: str,
        force: bool = False,
        max_buffer: int | None = None,
    ) -> tuple[str, str]:
        return self.algorithms.split_word_buffer(
            buf,
            force=force,
            max_buffer=(
                self.types.word_chunk_max_buffer
                if max_buffer is None
                else max_buffer
            ),
        )

    def anthropic_stream_services(self) -> streaming_anthropic.AnthropicStreamServices:
        return streaming_anthropic.AnthropicStreamServices(
            io=streaming_anthropic.AnthropicStreamIO(
                ANTHROPIC_THINKING_BLOCK_TYPES=self.types.thinking_block_types,
                VisibleToolCallArtifactFilter=self.types.visible_tool_filter,
                _find_pseudo_xml_tool_start=self.text.find_pseudo_xml_start,
                _split_word_buffer=self.split_word_buffer,
                mark_pending_channel_delivery_failed=self.io.mark_delivery_failed,
                mark_pending_channel_delivery_success=self.io.mark_delivery_success,
                remember_suppressed_thinking_passback=(
                    self.conversation.remember_suppressed_thinking
                ),
                router_client_connection_closed=self.io.client_connection_closed,
                router_log=self.io.log,
            ),
            tool_projection=streaming_anthropic.AnthropicToolProjection(
                _is_mcp_notification_wait_tool=self.tools.is_mcp_notification_wait,
                _remember_channel_injected_tool_use=self.tools.remember_tool_use,
                _validate_and_fix_tool_input=self.tools.validate_input,
                append_tool_call_log=self.tools.append_log,
                cap_mcp_notification_wait_tool_input=self.tools.cap_notification_wait,
                infer_tool_name_from_args=self.infer_tool_name,
                normalize_tool_arguments=self.normalize_tool_arguments,
                parse_pseudo_tool_calls=self.parse_pseudo_tool_calls,
                plan_mode_tool_name_for_emit=self.tools.plan_mode_name,
                resolve_emitted_tool_name=self.tools.resolve_name,
            ),
            tool_policy=streaming_anthropic.AnthropicToolPolicy(
                should_drop_duplicate_side_effect_tool_call=self.tools.should_drop_duplicate,
                should_drop_emitted_tool_call=self.tools.should_drop,
                should_repair_anthropic_passthrough_tool_input=self.tools.repair_passthrough_input,
            ),
            conversation=streaming_anthropic.AnthropicConversationContext(
                backfill_exit_plan_mode_allowed_prompts=self.conversation.backfill_exit_plan,
                body_ultracode_runtime_enabled=self.conversation.ultracode_enabled,
                empty_end_turn_notice_for_body=self.recovery.empty_notice,
                has_tool=self.conversation.has_tool,
                latest_user_intent_message_index=self.conversation.latest_intent_index,
                latest_user_is_claude_code_suggestion_mode=self.conversation.suggestion_mode,
                latest_user_tool_result_names=self.recovery.latest_tool_results,
                recent_synthetic_tasklist_count=self.conversation.recent_tasklist_count,
            ),
            continuation=streaming_anthropic.AnthropicContinuationPolicy(
                should_auto_continue_choice_question_with_tasklist=self.recovery.auto_continue_choice,
                should_auto_exit_plan_mode=self.recovery.auto_exit_plan,
                should_keep_work_alive_with_tasklist=self.recovery.keep_alive,
                should_recover_empty_end_turn_with_tasklist=self.recovery.recover_empty,
                should_synthesize_tasklist_for_provider=self.recovery.synthesize_tasklist,
            ),
        )

    def rebatch_anthropic_sse_text(
        self,
        handler: BaseHTTPRequestHandler,
        resp: Any,
        model: str = "ciel-runtime-upstream",
        word_chunking: bool = True,
        source_body: dict[str, Any] | None = None,
        preserve_thinking: bool = True,
        normalize_tool_use: bool = False,
        provider: str = "",
    ) -> None:
        return streaming_anthropic.rebatch_anthropic_sse_text(
            handler,
            resp,
            model=model,
            word_chunking=word_chunking,
            source_body=source_body,
            preserve_thinking=preserve_thinking,
            normalize_tool_use=normalize_tool_use,
            provider=provider,
            services=self.anthropic_stream_services(),
        )

    def ollama_stream_services(self) -> streaming_anthropic.OllamaStreamServices:
        return streaming_anthropic.OllamaStreamServices(
            io=streaming_anthropic.OllamaStreamIO(
                UpstreamClientDisconnected=self.types.client_disconnected_error,
                VisibleThinkingMarkupFilter=self.types.visible_thinking_filter,
                _split_word_buffer=self.split_word_buffer,
                estimate_tokens=self.io.estimate_tokens,
                iter_upstream_lines_until_client_disconnect=self.io.iter_upstream_lines,
                mark_pending_channel_delivery_failed=self.io.mark_delivery_failed,
                mark_pending_channel_delivery_success=self.io.mark_delivery_success,
                router_log=self.io.log,
                write_router_activity=self.io.write_activity,
            ),
            trace=streaming_anthropic.OllamaStreamTrace(
                dump_response_for_trace=self.trace.dump_response,
                finish_outgoing_sse_trace=self.trace.finish_sse,
                make_outgoing_sse_trace=self.trace.make_sse,
                record_outgoing_sse_event=self.trace.record_sse,
            ),
            tool_projection=streaming_anthropic.OllamaToolProjection(
                _remember_channel_injected_tool_use=self.tools.remember_tool_use,
                _validate_and_fix_tool_input=self.tools.validate_input,
                append_tool_call_log=self.tools.append_log,
                cap_mcp_notification_wait_tool_input=self.tools.cap_notification_wait,
                normalize_tool_arguments=self.normalize_tool_arguments,
                plan_mode_tool_name_for_emit=self.tools.plan_mode_name,
                resolve_emitted_tool_name=self.tools.resolve_name,
                should_drop_duplicate_side_effect_tool_call=self.tools.should_drop_duplicate,
                should_drop_emitted_tool_call=self.tools.should_drop,
            ),
            continuation=streaming_anthropic.OllamaContinuationPolicy(
                empty_end_turn_notice_for_body=self.recovery.empty_notice,
                reasoning_only_notice=self.text.ollama_reasoning_only_notice,
                should_auto_continue_choice_question_with_tasklist=self.recovery.auto_continue_choice,
                should_auto_enter_plan_mode=self.recovery.auto_enter_plan,
                should_keep_work_alive_with_tasklist=self.recovery.keep_alive,
                should_recover_empty_end_turn_with_tasklist=self.recovery.recover_empty,
            ),
        )

    def ollama_stream_to_anthropic_sse(
        self,
        handler: BaseHTTPRequestHandler,
        resp: Any,
        model: str,
        word_chunking: bool = False,
        provider: str = "ollama",
        source_body: dict[str, Any] | None = None,
        idle_timeout: float = 30.0,
    ) -> None:
        return streaming_anthropic.ollama_stream_to_anthropic_sse(
            handler,
            resp,
            model,
            word_chunking=word_chunking,
            provider=provider,
            source_body=source_body,
            idle_timeout=idle_timeout,
            services=self.ollama_stream_services(),
        )

    def openai_chat_to_anthropic(
        self,
        data: dict[str, Any],
        model: str,
        source_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.algorithms.project_openai_chat_response(
            data,
            model,
            source_body,
            services=self.ollama_response_services(),
            positive_int=self.text.positive_int,
            reasoning_to_block=self.text.reasoning_to_thinking,
            content_to_text=self.text.anthropic_content_to_text,
        )

    def openai_responses_to_anthropic_messages(
        self, body: dict[str, Any], fallback_model: str
    ) -> dict[str, Any]:
        adapter = self.algorithms.protocol_adapter(
            "openai_responses", fallback_model=fallback_model
        )
        return dict(adapter.normalize_request(body))

    def anthropic_message_to_openai_response(
        self,
        message: dict[str, Any],
        source_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adapter = self.algorithms.protocol_adapter(
            "openai_responses", source_body=source_body
        )
        return dict(adapter.normalize_response(message))

    def openai_responses_stream_services(self) -> OpenAIResponsesStreamServices:
        return OpenAIResponsesStreamServices(
            to_response=self.anthropic_message_to_openai_response,
            write_json=self.io.write_json,
        )

    def write_openai_responses_response(
        self,
        handler: BaseHTTPRequestHandler,
        message: dict[str, Any],
        source_body: dict[str, Any] | None = None,
        *,
        stream: bool = True,
    ) -> None:
        self.algorithms.write_openai_response(
            handler,
            message,
            source_body,
            stream=stream,
            services=self.openai_responses_stream_services(),
        )

    def write_openai_responses_error(
        self,
        handler: BaseHTTPRequestHandler,
        message: str,
        *,
        stream: bool = True,
        status: int = 500,
        error_type: str = "api_error",
    ) -> None:
        self.algorithms.write_openai_error(
            handler,
            message,
            stream=stream,
            status=status,
            error_type=error_type,
            services=self.openai_responses_stream_services(),
        )

    def openai_chat_stream_services(
        self,
    ) -> streaming_anthropic.OpenAIChatStreamServices:
        return streaming_anthropic.OpenAIChatStreamServices(
            io=streaming_anthropic.OpenAIChatStreamIO(
                PSEUDO_TOOL_END=self.types.pseudo_tool_end,
                PSEUDO_TOOL_START=self.types.pseudo_tool_start,
                _split_word_buffer=self.split_word_buffer,
                positive_int=self.text.positive_int,
                router_log=self.io.log,
                write_anthropic_open_stream_stop=self.io.write_open_stream_stop,
                write_router_activity=self.io.write_activity,
            ),
            tool_projection=streaming_anthropic.OpenAIChatToolProjection(
                _remember_channel_injected_tool_use=self.tools.remember_tool_use,
                _validate_and_fix_tool_input=self.tools.validate_input,
                append_tool_call_log=self.tools.append_log,
                cap_mcp_notification_wait_tool_input=self.tools.cap_notification_wait,
                normalize_tool_arguments=self.normalize_tool_arguments,
                parse_pseudo_tool_calls=self.parse_pseudo_tool_calls,
                plan_mode_tool_name_for_emit=self.tools.plan_mode_name,
                resolve_emitted_tool_name=self.tools.resolve_name,
                should_drop_duplicate_side_effect_tool_call=self.tools.should_drop_duplicate,
                should_drop_emitted_tool_call=self.tools.should_drop,
            ),
            continuation=streaming_anthropic.OpenAIChatContinuationPolicy(
                empty_end_turn_notice_for_body=self.recovery.empty_notice,
                latest_user_tool_result_names=self.recovery.latest_tool_results,
                should_auto_continue_choice_question_with_tasklist=self.recovery.auto_continue_choice,
                should_auto_enter_plan_mode=self.recovery.auto_enter_plan,
                should_keep_work_alive_with_tasklist=self.recovery.keep_alive,
                should_recover_empty_end_turn_with_tasklist=self.recovery.recover_empty,
            ),
        )

    def stream_openai_chat_to_anthropic_sse(
        self,
        handler: BaseHTTPRequestHandler,
        resp: Any,
        model: str,
        provider: str,
        source_body: dict[str, Any] | None = None,
        start_index: int = 0,
        word_chunking: bool = False,
        input_tokens: int | None = None,
        input_bytes: int | None = None,
    ) -> bool:
        return streaming_anthropic.forward_openai_chat_to_anthropic_sse(
            handler,
            resp,
            model,
            provider,
            source_body=source_body,
            start_index=start_index,
            word_chunking=word_chunking,
            input_tokens=input_tokens,
            input_bytes=input_bytes,
            services=self.openai_chat_stream_services(),
        )


@dataclass(frozen=True, slots=True)
class ResponseStreamCompatibilityApi:
    context: Callable[[], ResponseStreamContext]

    def normalize_tool_arguments(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().normalize_tool_arguments(*args, **kwargs)

    def infer_tool_name(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().infer_tool_name(*args, **kwargs)

    def parse_pseudo_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().parse_pseudo_tool_calls(*args, **kwargs)

    def ollama_response_services(self) -> OllamaResponseServices:
        return self.context().ollama_response_services()

    def ollama_chat_to_anthropic(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().ollama_chat_to_anthropic(*args, **kwargs)

    def split_word_buffer(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().split_word_buffer(*args, **kwargs)

    def rebatch_anthropic_sse_text(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().rebatch_anthropic_sse_text(*args, **kwargs)

    def ollama_stream_to_anthropic_sse(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().ollama_stream_to_anthropic_sse(*args, **kwargs)

    def openai_chat_to_anthropic(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().openai_chat_to_anthropic(*args, **kwargs)

    def openai_responses_to_anthropic_messages(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().openai_responses_to_anthropic_messages(*args, **kwargs)

    def anthropic_message_to_openai_response(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().anthropic_message_to_openai_response(*args, **kwargs)

    def openai_responses_stream_services(self) -> OpenAIResponsesStreamServices:
        return self.context().openai_responses_stream_services()

    def write_openai_responses_response(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().write_openai_responses_response(*args, **kwargs)

    def write_openai_responses_error(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().write_openai_responses_error(*args, **kwargs)

    def stream_openai_chat_to_anthropic_sse(self, *args: Any, **kwargs: Any) -> Any:
        return self.context().stream_openai_chat_to_anthropic_sse(*args, **kwargs)


__all__ = [
    "ResponseStreamAlgorithms",
    "ResponseStreamCompatibilityApi",
    "ResponseStreamContext",
    "ResponseStreamConversationPorts",
    "ResponseStreamIoPorts",
    "ResponseStreamRecoveryPorts",
    "ResponseStreamRuntimePorts",
    "ResponseStreamTextPorts",
    "ResponseStreamToolPorts",
    "ResponseStreamTracePorts",
    "ResponseStreamTypes",
]
