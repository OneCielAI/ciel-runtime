#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import hashlib
import hmac
import html as html_lib
import importlib.util
import json
import math
import mimetypes
import os
import platform
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Iterable

from ciel_runtime_support.agent_router import missing_common_capabilities, router_capability_matrix
from ciel_runtime_support.advisor_policy import (
    AdvisorDecisionServices,
    AdvisorServices,
    AdvisorTextServices,
    advisor_focus_for_message as project_advisor_focus,
    advisor_gate_reason_for_body as project_advisor_gate_reason,
    advisor_messages_and_system as project_advisor_messages_and_system,
    advisor_tool_focus_from_message as project_advisor_tool_focus,
    advisor_tool_schema as project_advisor_tool_schema,
    advisor_trigger_for_message as project_advisor_trigger,
    anthropic_message_tool_names as project_anthropic_message_tool_names,
    assistant_tool_call_summary_for_prompt as project_assistant_tool_summary,
    body_has_advisor_feedback as project_body_has_advisor_feedback,
    body_with_advisor_tool as project_body_with_advisor_tool,
    is_claude_code_advisor_server_tool as project_is_advisor_server_tool,
    strip_autonomous_advisor_server_tools as project_strip_advisor_server_tools,
    tool_review_context_from_message as project_tool_review_context,
)
from ciel_runtime_support.advisor_request_builder import (
    AdvisorBudgetPorts,
    AdvisorEndpointPorts,
    AdvisorProjectionPorts,
    AdvisorRequestBuilder,
)
from ciel_runtime_support.advisor_refinement import (
    AdvisorRefinementIO,
    AdvisorRefinementPolicy,
    AdvisorRefinementService,
    AdvisorRefinementText,
)
from ciel_runtime_support.advisor_client import (
    AdvisorClient,
    AdvisorClientIO,
    AdvisorClientPolicy,
    ProviderChatExecutor,
    ProviderChatIO,
    ProviderChatPolicy,
)
from ciel_runtime_support.architecture import LaunchSpec, MessageProtocol, ProviderConfig, RuntimeConfig
from ciel_runtime_support.anthropic_tool_turns import (
    AnthropicToolTurnServices,
    normalize_historical_anthropic_tool_turns,
)
from ciel_runtime_support.anthropic_response_writer import (
    AnthropicResponseWriter,
    anthropic_text_response as project_anthropic_text_response,
    prepend_anthropic_text as project_prepend_anthropic_text,
)
from ciel_runtime_support import anthropic_model_policy
from ciel_runtime_support.agy_cli import agy_dangerous_launch_args, agy_passthrough_args_for_launch, agy_passthrough_has_command
from ciel_runtime_support.claude_router import (
    ClaudeRouter,
    ClaudeRouterCore,
    ClaudeRouterCountTokens,
    ClaudeRouterDelivery,
    ClaudeRouterNativeNormalization,
    ClaudeRouterPipeline,
    ClaudeRouterResponse,
    ClaudeRouterRouting,
    ClaudeRouterServices,
    ClaudeRouterShortcuts,
    ClaudeRouterTransport,
)
from ciel_runtime_support.channel_injection import (
    CallableInputTransport,
    ChannelPromptInjector,
    PromptInjection,
    RuntimeInjectionPolicy,
)
from ciel_runtime_support.chat_http_controller import (
    ChatHttpController,
    ChatHttpReadServices,
    ChatHttpWriteServices,
)
from ciel_runtime_support.channel_inflight import ChannelInflightEffects
from ciel_runtime_support.channel_connection_registry import ChannelConnectionRegistry
from ciel_runtime_support.channel_connection_lifecycle import (
    ChannelConnectionLifecycle,
    ChannelConnectionLifecycleEffects,
    ChannelConnectionLifecyclePolicy,
    ChannelConnectionLifecycleStore,
)
from ciel_runtime_support.channel_connection_worker import (
    ChannelConnectionWorker,
    ChannelWorkerEffects,
    ChannelWorkerPolicy,
    ChannelWorkerStateStore,
)
from ciel_runtime_support.channel_config_service import (
    ChannelConfigPorts,
    ChannelConfigService,
)
from ciel_runtime_support.channel_compact_request_repository import (
    ChannelCompactRequestRepository,
    compact_request_ttl,
)
from ciel_runtime_support.command_asset_installer import (
    CommandAsset,
    CommandAssetInstaller,
    is_owned_command_file,
)
from ciel_runtime_support.executable_discovery import ExecutableDiscovery
from ciel_runtime_support.channel_event_projection import (
    CHANNEL_CONTROL_KINDS as _CHANNEL_CONTROL_KINDS,
    compact_json_for_prompt as _compact_json_for_prompt,
    event_meta_from_sources as _event_meta_from_sources,
    event_payload_text as _event_payload_text,
    json_safe_metadata as _json_safe_metadata,
    notification_semantic_text_from_envelope as _notification_semantic_text_from_envelope,
    pretty_json_value as _pretty_json_value,
    sse_payload_to_chat_payload as _sse_payload_to_chat_payload,
)
from ciel_runtime_support.channel_session_repository import ChannelSessionRepository
from ciel_runtime_support.channel_session_lifecycle import (
    ChannelSessionLifecycleServices,
    cleanup_stale_channel_sessions,
    delete_channel_session,
)
from ciel_runtime_support.channel_llm_context import (
    ChannelLlmContextPolicy,
    ChannelLlmContextProjection,
    ChannelLlmContextRepository,
    ChannelLlmContextServices,
    inject_pending_channel_context,
)
from ciel_runtime_support.channel_mcp_tools import (
    ChannelMcpToolServices,
    channel_mcp_tool_response,
    channel_mcp_tool_schemas,
    dispatch_channel_mcp_tool,
)
from ciel_runtime_support.channel_mcp_discovery import (
    ChannelMcpDiscoveryPorts,
    ChannelMcpDiscoveryService,
)
from ciel_runtime_support.channel_mcp_ownership import (
    ChannelProxyOwnershipRepository,
    ChannelRouterLifecycle,
    ChannelRouterLifecyclePorts,
)
from ciel_runtime_support.channel_mcp_http_controller import (
    ChannelMcpHttpController,
    ChannelMcpRpcServices,
    ChannelMcpSessionStore,
    ChannelMcpStreamServices,
)
from ciel_runtime_support.channel_mcp_transport import (
    ChannelMcpEffects,
    ChannelMcpHttpPorts,
    ChannelMcpTransport,
    ChannelMcpTransportConfig,
    ChannelMcpTransportState,
)
from ciel_runtime_support.channel_pending_injection import (
    ChannelInjectionIO,
    ChannelInjectionPolicy,
    ChannelInjectionPrompts,
    ChannelInjectionServices,
    ChannelInjectionState,
    ChannelInjectionWakeStore,
    inject_pending_channel_messages as run_pending_channel_injection,
)
from ciel_runtime_support.channel_terminal_proxy import (
    ChannelTerminalIO,
    ChannelTerminalPolicy,
    ChannelTerminalPolling,
    ChannelTerminalProcess,
    ChannelTerminalServices,
    ChannelWindowsConsole,
    ChannelWindowsServices,
    run_posix_channel_terminal_proxy,
    run_windows_channel_terminal_proxy,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices,
    active_tool_call_from_text as _channel_stdin_active_tool_call_from_text,
    active_turn_from_text as _channel_stdin_active_turn_from_text,
    queued_age_seconds_from_text as analyze_channel_queued_age,
    queued_command_ids_from_text as analyze_channel_queued_ids,
    wake_state_from_text as analyze_channel_wake_state,
)
from ciel_runtime_support.channel_message_policy import (
    message_has_external_provenance as _channel_message_has_external_provenance,
    message_is_web_chat_request as _channel_message_is_web_chat_request,
    superseded_message_ids as _channel_superseded_message_ids,
)
from ciel_runtime_support.channel_message_prompt import (
    NATIVE_ROUTER_CHANNEL_NAMES as _NATIVE_ROUTER_CHANNEL_NAMES,
    format_llm_batch_prompt as format_channel_llm_batch_prompt,
    format_llm_batch_prompt as format_channel_llm_delivery_wake_prompt,
    format_wake_batch_prompt as format_channel_wake_batch_prompt,
    format_wake_prompt as format_channel_wake_prompt,  # noqa: F401 - compatibility export
    format_web_chat_wake_batch_prompt as format_channel_web_chat_wake_batch_prompt,
    llm_message_skip_reason as _channel_llm_message_skip_reason,
    wake_message_noise_reason as _channel_wake_message_noise_reason,
)
from ciel_runtime_support.channel_notification_projection import (
    ChannelNotificationConfig,
    ChannelNotificationPorts,
    ChannelNotificationProjection,
)
from ciel_runtime_support.channel_event_identity import (
    fallback_dedupe_key as _chat_message_fallback_dedupe_key,
    message_event_identity_key as _channel_message_event_identity_key,
    message_time_seconds as _chat_message_time_seconds,
    stable_dedupe_key as _chat_message_stable_dedupe_key,
)
from ciel_runtime_support.channel_message_repository import ChannelMessageRepository
from ciel_runtime_support.channel_launch_guard_repository import ChannelLaunchGuardRepository
from ciel_runtime_support.channel_cursor_repository import ChannelCursorRepository
from ciel_runtime_support.channel_cursor_service import (
    ChannelCursorService,
    ChannelCursorServices,
    ChannelResumePolicy,
    ChannelResumeServices,
    parse_channel_event_id,
)
from ciel_runtime_support.channel_wake_claim_repository import (
    ChannelWakeClaimRepository,
    prompt_message_ids as _channel_prompt_message_ids,
    prompt_references_message_id as analyze_prompt_message_reference,
)
from ciel_runtime_support.channel_terminal_input import (
    bounded_delay_seconds as _bounded_delay_seconds,
    enter_bytes_from_user_input as _channel_enter_bytes_from_user_input,  # noqa: F401 - compatibility export
    enter_label as _channel_enter_label,
    platform_default_enter_bytes as _channel_platform_default_enter_bytes,
    resolve_enter_bytes as resolve_channel_enter_bytes,
    synthetic_enter_bytes_from_user_input as _channel_synthetic_enter_bytes_from_user_input,
    wake_enter_env_is_fixed as _channel_wake_enter_env_is_fixed,
    wake_input_bytes as build_channel_wake_input_bytes,
    wake_submit_delay_seconds as _channel_wake_submit_delay_seconds,
    wake_submit_retry_delay_seconds as _channel_wake_submit_retry_delay_seconds,
)
from ciel_runtime_support.channel_probe_report import (
    ChannelProbeReportServices,
    channel_probe_report_lines,
)
from ciel_runtime_support.channel_probe_cache import (
    ChannelProbeCacheRepository,
    ChannelProbePorts,
    ChannelProbeService,
)
from ciel_runtime_support.channel_panel import (
    ChannelPanelPolicy,
    _channel_panel_first_selectable as first_selectable_channel_row,
    _channel_panel_step as step_channel_row,
    channel_delivery_panel_rows as project_channel_delivery_panel_rows,
    channel_panel_rows as project_channel_panel_rows,
)
from ciel_runtime_support.model_panel import (
    ModelPanelCatalog,
    ModelPanelPresentation,
    ModelPanelServices,
    advisor_model_panel_rows as project_advisor_model_panel_rows,
    model_panel_rows as project_model_panel_rows,
)
from ciel_runtime_support.model_catalog_projection import (
    ModelCatalogProjectionServices,
    project_model_info,
)
from ciel_runtime_support.model_registry_repository import (
    ModelRegistryPaths,
    ModelRegistryPolicy,
    ModelRegistryRepository,
)
from ciel_runtime_support.lm_studio_runtime import (
    LmStudioLifecyclePolicy,
    LmStudioModelLifecycle,
    LmStudioRuntimeServices,
    discover_lm_studio_runtime,
)
from ciel_runtime_support.cli_dispatch import (
    CliChannelCommands,
    CliConfiguration,
    CliCore,
    CliOperations,
    CliProviderCommands,
    CliRuntime,
    CliServices,
    CliSpecialCommands,
    dispatch_cli,
)
from ciel_runtime_support.cli_usage import cli_usage_text
from ciel_runtime_support.cli_parser import (
    CliParserLaunch,
    CliParserModels,
    CliParserProvider,
    CliParserRuntime,
    CliParserServices,
    CliParserSettings,
    build_cli_parser,
)
from ciel_runtime_support.compatibility_test import (
    CompatibilityTestConfig,
    CompatibilityTestConstants,
    CompatibilityTestMode,
    CompatibilityTestOutput,
    CompatibilityTestProtocol,
    CompatibilityTestRequest,
    CompatibilityTestServices,
    run_compatibility_test as run_provider_compatibility_test,
)
from ciel_runtime_support.headless_config import (
    HeadlessChannelCommands,
    HeadlessConfigCommands,
    HeadlessConfigServices,
    apply_headless_config,
)
from ciel_runtime_support.http_response import ChannelDeliveryGuard, HttpResponseAdapter
from ciel_runtime_support.config_repository import JsonConfigRepository
from ciel_runtime_support.settings_repository import JsonSettingsRepository, SettingsFileEffects
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository
from ciel_runtime_support.slash_command_assets import (
    ADVISOR_NATIVE_DISABLED_SLASH_COMMAND,  # noqa: F401 - compatibility export
    ADVISOR_SLASH_COMMAND,
    API_KEYS_SLASH_COMMAND,
    CHANNEL_CLEAR_SLASH_COMMAND,
    IMPORT_SESSION_SLASH_COMMAND,
    LLM_OPTIONS_SLASH_COMMAND,
    LLM_RESTORE_SLASH_COMMAND,
    LLM_SLIDER_SLASH_COMMAND,
    ROUTER_DEBUG_NATIVE_DISABLED_SLASH_COMMAND,  # noqa: F401 - compatibility export
    ROUTER_DEBUG_SLASH_COMMAND,
)
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT
from ciel_runtime_support.statusline_settings import StatusLineServices, install_statusline_settings
from ciel_runtime_support.config_migrations import (
    ConfigMigrationPolicy,
    apply_config_migrations as run_config_migrations,
)
from ciel_runtime_support.context_compaction import (
    ContextCompactionProjection,
    ContextCompactionServices,
    ContextCompactionTransport,
    ContextCompactionWorkflow,
    build_llm_compacted_messages,
    request_context_summary,
)
from ciel_runtime_support.context_summary_policy import ContextSummaryPolicy
from ciel_runtime_support.codex_process_lifecycle import (
    CodexProcessLifecycle,
    CodexProcessPorts,
    CodexProcessRepository,
    managed_process as project_managed_codex_process,
    terminate_recorded_child as terminate_project_recorded_child,
)
from ciel_runtime_support.credentials import (
    api_key_clear_requested as project_api_key_clear_requested,
    meaningful_key_value as project_meaningful_key_value,
    parse_api_key_list as project_parse_api_key_list,
    provider_config_api_keys as project_provider_config_api_keys,
    provider_contract_config as project_provider_contract_config,
    resolve_anthropic_credentials,
)
from ciel_runtime_support.tool_guard_hooks import (
    ToolGuardHookPolicy,
    ToolGuardHookServices,
    install_tool_guard_hook_settings,
)
from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessInspectionServices,
    ProcessQueryServices,
    ProcessSignalServices,
    posix_process_rows,
    linux_procfs_pids_on_port,
    posix_pids_on_port as project_posix_pids_on_port,
    process_command_line as inspect_process_command_line,
    process_cwd as inspect_process_cwd,
    process_environ_contains as inspect_process_environ_contains,
    terminate_matching_processes as run_terminate_matching_processes,
    windows_pids_on_port,
)
from ciel_runtime_support.router_process_lifecycle import (
    ClockPorts as RouterProcessClock,
    RouterProcessConfig,
    RouterStatePorts,
    RouterTerminationPorts,
    ensure_port_available as ensure_project_router_port_available,
    stop_router_processes as stop_project_router_processes,
    stop_with_guarantee as stop_project_router_with_guarantee,
    terminate_health_pid as terminate_project_router_health_pid,
    terminate_pid_file as terminate_project_pid_file,
)
from ciel_runtime_support.provider_config_mutations import (
    ProviderOptionPolicy,
    apply_ollama_option as mutate_ollama_option,
    apply_provider_option as mutate_provider_option,
)
from ciel_runtime_support.llm_presets import (
    PresetContextPolicy,
    PresetDefinition,
    PresetProviderMutation,
    PresetServices,
    apply_preset_to_provider,
)
from ciel_runtime_support.llm_presentation_data import (
    AUTO_TIMEOUT_MAX_MS,
    AUTO_TIMEOUT_MIN_MS,
    AUTO_TIMEOUT_ROUND_MS,
    CONTEXT_HEAVY_PRESETS,
    LLM_OPTION_DESCRIPTIONS,
    LLM_OPTION_TOGGLE_KEYS,
    LLM_PRESET_I18N,
    LLM_PRESET_TIMEOUT_MS,
    LLM_PRESETS,
    LLM_SLIDER_LABELS,
    MODEL_FAMILY_I18N,
    RUNTIME_LLM_OPTION_KEYS,
    RUNTIME_LLM_ORIGINAL_KEY,
    TIMEOUT_PRESET_I18N,
    TIMEOUT_PRESETS,
)
from ciel_runtime_support.llm_option_config import (
    LlmOptionConfigServices,
    LlmOptionMutation,
    LlmOptionPolicy,
    LlmOptionRepository,
    set_llm_option_config as apply_llm_option_config,
)
from ciel_runtime_support.launch_state import (
    LaunchStateRepository,
    current_launch_cwd_key as project_current_launch_cwd_key,
    last_launch_runtime as project_last_launch_runtime,
    launch_mode_name as project_launch_mode_name,
    session_control_requested as project_session_control_requested,
    should_fork_native_session as project_should_fork_native_session,
)
from ciel_runtime_support.launch_diagnostics import LaunchCommandDiagnostics, StderrCaptureAdapter
from ciel_runtime_support.mcp_transport import (
    CODEX_MCP_SPLIT_PROXY_PREFIX,
    MCP_LEGACY_SSE_PROTOCOL_VERSION,
    MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
    split_proxy_server_name as codex_mcp_split_proxy_server_name,
    sse_post_json as _mcp_sse_post_json,
    streamable_headers as _mcp_streamable_headers,
    streamable_post_json as _mcp_streamable_post_json,
    upstream_url as _codex_mcp_split_proxy_upstream_url,
)
from ciel_runtime_support.mcp_config_reader import (
    dedupe_strings as _dedupe_strings,
    path_for_compare as _path_for_compare,
    read_mcp_config_items,
    server_names_from_mapping as _mcp_server_names_from_mapping,
    servers_from_mapping as _mcp_servers_from_mapping,
)
from ciel_runtime_support.mcp_proxy_codec import (
    McpProxyCodecPolicy,
    _mcp_proxy_error_response,
    _mcp_proxy_notification_wait_response,
    _mcp_proxy_tool_call_arguments,
    _mcp_proxy_tool_call_name,
    _mcp_proxy_tool_is_notification_wait,
    _mcp_proxy_wait_timeout_seconds,
    compact_tool_result_response as compact_mcp_tool_result_response,
)
from ciel_runtime_support.mcp_proxy_process import (
    _McpStdoutObserver as McpStdoutObserver,
    _mcp_proxy_drain_input_messages,
    _mcp_proxy_stdio_mode,
    _mcp_proxy_write_json_response,
    _mcp_proxy_forward_stdin as proxy_forward_stdin,
    _mcp_proxy_forward_stdin_jsonl as proxy_forward_stdin_jsonl,
    _mcp_proxy_forward_stdout_jsonl as proxy_forward_stdout_jsonl,
    _mcp_proxy_forward_stderr as proxy_forward_stderr,
    _mcp_proxy_streamable_http_request as proxy_streamable_http_request,
)
from ciel_runtime_support.mcp_http_proxy import (
    McpHttpProxyCodec,
    McpHttpProxyRuntime,
    McpHttpProxyServices,
    McpHttpProxyTransport,
    run_mcp_streamable_http_proxy as run_streamable_http_mcp_proxy,
)
from ciel_runtime_support.mcp_probe_codec import (
    channel_capability_present as _channel_probe_capability_present,
    decode_sse_events as _decode_sse_events,
    find_initialize_response as _channel_probe_find_initialize_response,
    initialize_payload as _mcp_probe_initialize_payload,
    initialize_payload_bytes as _mcp_probe_initialize_payload_bytes,
    probe_strategy as _channel_probe_strategy_for,
)
from ciel_runtime_support.mcp_probe_transport import (
    McpProbeCodec,
    McpProbeHttp,
    McpProbePolicy,
    McpProbeServices,
    probe_sse_mcp_for_channel_capability_detailed as run_sse_mcp_probe,
    probe_streamable_http_mcp_for_channel_capability_detailed as run_streamable_http_mcp_probe,
)
from ciel_runtime_support.mcp_stdio_probe import (
    StdioProbeCodec,
    StdioProbePolicy,
    StdioProbeProcess,
    StdioProbeServices,
    probe_stdio_mcp_for_channel_capability_detailed as run_stdio_mcp_probe,
)
from ciel_runtime_support.codex_app_server import codex_app_server_launch_args
from ciel_runtime_support.codex_config import (
    codex_alternate_screen_value_from_config_text as project_codex_alternate_screen_value,
    codex_config_override_keys as project_codex_config_override_keys,
    codex_config_paths_for_launch as project_codex_config_paths,
    codex_mcp_servers_from_config_text as project_codex_mcp_servers,
    codex_mcp_servers_from_toml_data as project_codex_mcp_servers_from_toml,
    discover_codex_mcp_servers as project_discover_codex_mcp_servers,
    fallback_codex_mcp_servers_from_config_text as project_fallback_codex_mcp_servers,
    normalize_codex_mcp_server as project_normalize_codex_mcp_server,
    parse_simple_toml_value as project_parse_simple_toml_value,
    toml_scalar_without_comment as project_toml_scalar_without_comment,
    toml_string as project_toml_string,
    toml_table_parts as project_toml_table_parts,
    unquote_toml_string as project_unquote_toml_string,
)
from ciel_runtime_support.codex_launch_policy import (
    current_model_args as project_codex_current_model_args,
    help_requested as project_codex_help_requested,
    native_routed_config_args as project_codex_native_routed_config_args,
    passthrough_has_model_override as project_codex_model_overridden,
    yolo_launch_args as project_codex_yolo_launch_args,
)
from ciel_runtime_support.codex_model_catalog import (
    CodexModelCatalogService,
    CodexModelCatalogSpec,
)
from ciel_runtime_support.codex_cli import (
    codex_passthrough_args_for_launch,
    codex_passthrough_has_command,
    codex_resume_picker_requested,
    codex_resume_with_session_id,
)
from ciel_runtime_support.codex_router import CodexRouter, read_codex_response_preamble
from ciel_runtime_support.codex_session_repository import (
    CodexSessionRepository,
    codex_sqlite_home,
)
from ciel_runtime_support.observability import EventBus, render_events_html
from ciel_runtime_support.request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    dump_request_for_trace as write_request_trace,
    dump_response_for_trace as write_response_trace,
    summarize_messages_for_trace as project_messages_for_trace,
    truncate_for_dump as _truncate_for_dump,
)
from ciel_runtime_support.request_shortcuts import (
    ShortcutTextServices,
    format_channel_messages,  # noqa: F401 - compatibility export
    has_marker as project_has_request_marker,
    import_session_args as project_import_session_args,
    live_api_keys_value as project_live_api_keys_value,
    live_option_value as project_live_option_value,
    marker_tail as project_request_marker_tail,
    parse_channel_bridge_args,  # noqa: F401 - compatibility export
    single_value as project_single_shortcut_value,
    split_import_session_arguments as project_split_import_session_arguments,
)
from ciel_runtime_support import ollama_catalog as ollama_catalog_policy
from ciel_runtime_support.ollama_catalog_repository import OllamaCatalogRepository
from ciel_runtime_support.ollama_context_sync import (
    OllamaContextPolicy,
    OllamaContextSources,
    sync_ollama_context_limit,
)
from ciel_runtime_support.ollama_forwarding import (
    OllamaForwardAdvisor,
    OllamaForwardConstants,
    OllamaForwardRateLimit,
    OllamaForwardRequest,
    OllamaForwardResponse,
    OllamaForwardServices,
    OllamaForwardStreaming,
    forward_ollama_api_chat as run_ollama_forward,
)
from ciel_runtime_support.openai_forwarding import (
    OpenAIForwardAdvisor,
    OpenAIForwardPolicy,
    OpenAIForwardRateLimit,
    OpenAIForwardRequest,
    OpenAIForwardResponse,
    OpenAIForwardServices,
    OpenAIForwardStreaming,
    forward_openai_compatible_chat as run_openai_forward,
)
from ciel_runtime_support.openai_responses_router import (
    OpenAIResponsesConversion,
    OpenAIResponsesCore,
    OpenAIResponsesDelivery,
    OpenAIResponsesOutput,
    OpenAIResponsesRouting,
    OpenAIResponsesServices,
    handle_openai_responses_request,
)
from ciel_runtime_support.openai_responses_stream import (
    OpenAIResponsesStreamServices,
    write_openai_responses as project_openai_responses_stream,
    write_openai_responses_error as project_openai_responses_error,
)
from ciel_runtime_support.protocols import PROTOCOL_ADAPTERS
from ciel_runtime_support.protocols.anthropic_thinking_policy import (
    AnthropicThinkingPolicy,
    SuppressedThinkingRepository,
    ThinkingPolicyPorts,
    ToolChoicePorts,
    assistant_history_count as project_anthropic_assistant_history_count,
    copy_thinking_blocks as project_copy_thinking_blocks,
    has_synthetic_tool_use as project_has_synthetic_tool_use,
    message_content_blocks as project_message_content_blocks,
    normalize_tool_choice as project_normalize_tool_choice,
    strip_thinking_blocks as project_strip_thinking_blocks,
    thinking_block_count as project_anthropic_thinking_block_count,
    thinking_requested as project_anthropic_thinking_requested,
    tool_continuation_block_count as project_anthropic_tool_continuation_block_count,
)
from ciel_runtime_support.protocols.ollama_chat import (
    anthropic_system_to_ollama_messages,
    anthropic_tools_to_ollama,
    decode_ollama_chat_response,
    encode_anthropic_message,
    ollama_claude_code_reminder,
)
from ciel_runtime_support.protocols.ollama_response import (
    OllamaResponseOutput,
    OllamaResponseRecovery,
    OllamaResponseServices,
    OllamaResponseText,
    OllamaResponseTools,
    project_ollama_response,
)
from ciel_runtime_support.protocols.chat_projection import (
    ChatProjectionPolicy,
    ChatProjectionServices,
    ChatProjectionText,
    ChatProjectionTools,
    OpenAiHistoryServices,
    anthropic_messages_to_ollama as project_anthropic_messages_to_ollama,
    anthropic_messages_to_openai as project_anthropic_messages_to_openai,
    missing_openai_tool_result_message as project_missing_openai_tool_result_message,
    orphan_openai_tool_message_to_user as project_orphan_openai_tool_message_to_user,
    repair_openai_tool_call_adjacency as project_repair_openai_tool_call_adjacency,
)
from ciel_runtime_support.protocols.conversation_policy import (
    ConversationPolicyServices,
    canonical_tool_signature,
    claude_code_state_messages as project_claude_code_state_messages,
    collect_tool_result_context as project_collect_tool_result_context,
    is_attachment_only_message as project_is_attachment_only_message,
    is_read_unchanged_result,
    latest_plan_attachment,  # noqa: F401 - compatibility export
    message_attachment,  # noqa: F401 - compatibility export
    plan_file_written_in_body as project_plan_file_written_in_body,
    should_skip_upstream_message as project_should_skip_upstream_message,
    upstream_relevant_message as project_upstream_relevant_message,
)
from ciel_runtime_support.protocols.conversation_turn_policy import (
    ConversationTurnPolicy,
    ConversationTurnPorts,
)
from ciel_runtime_support.protocols.tool_result_projection import (
    ToolResultProjectionServices,
    project_tool_result,
)
from ciel_runtime_support.protocols.pseudo_tool_history import (
    PseudoToolHistoryServices,
    find_pseudo_xml_tool_start,
    parse_xml_pseudo_tool_calls,
    sanitize_assistant_pseudo_tool_history,
)
from ciel_runtime_support.provider_adapters import (
    PROVIDER_ADAPTERS,
    PROVIDER_LABELS,
    provider_default_configurations,
)
from ciel_runtime_support.provider_compatibility import PROVIDER_COMPATIBILITY
from ciel_runtime_support.provider_context import (
    ContextPresetServices,
    ProviderContextServices,
    cap_context_settings as apply_context_capacity_cap,
    classify_model_family,
    infer_context_preset,
    recommended_preset,
    required_context_for_preset as context_required_for_preset,
    resolve_context_capacity,
)
from ciel_runtime_support.provider_option_panel import (
    OptionPanelPolicy,
    OptionPanelProvider,
    OptionPanelRuntime,
    OptionPanelServices,
    OptionPanelText,
    OptionValuePolicy,
    build_option_panel_rows,
    current_option_bool,
    option_prompt_default,
)
from ciel_runtime_support.provider_limits import (
    ProviderKeyServices,
    RateLimitApplyPolicy,
    RateLimitApplyServices,
    RateLimitBackoffPolicy,
    RateLimitBackoffServices,
    RateLimitLearningPolicy,
    RateLimitLearningServices,
    RateLimitStateStore,
    apply_rate_limit,
    choose_provider_api_key,
    learn_rate_limit_headers,
    register_rate_limit_backoff,
)
from ciel_runtime_support import rate_limit_policy
from ciel_runtime_support.rate_limit_repository import RateLimitRepository
from ciel_runtime_support.plan_artifact_controller import (
    PlanArtifactController,
    PlanArtifactServices,
)
from ciel_runtime_support import provider_network
from ciel_runtime_support.provider_models import (
    ModelCatalogHttp,
    ModelCatalogPolicy,
    ModelCatalogResponseCodec,
    ModelCatalogStorage,
    ProviderCatalogSources,
    ProviderModelServices,
    fetch_upstream_model_ids,
)
from ciel_runtime_support.provider_model_selection import (
    ModelCatalogPorts,
    ModelIdentityPorts,
    ModelSelectionPorts,
    ProviderModelSelection,
)
from ciel_runtime_support.response_collection import (
    AnthropicCollectionProjection,
    AnthropicCollectionRequest,
    AnthropicCollectionServices,
    AnthropicCollectionTransport,
    ChatCollectionStrategy,
    ResponseCollectionProjection,
    ResponseCollectionRateLimit,
    ResponseCollectionRequest,
    ResponseCollectionServices,
    collect_anthropic_message_for_responses as collect_anthropic_response,
    collect_chat_message_for_responses as collect_chat_response,
)
from ciel_runtime_support.router_http import (
    RouterHttpCore,
    RouterHttpErrors,
    RouterHttpGetEndpoints,
    RouterHttpHandler,
    RouterHttpPostEndpoints,
    RouterHttpPresentation,
    RouterHttpServices,
)
from ciel_runtime_support.provider_policy import (
    ProviderRequestServices,
    ProviderWireServices,
    normalize_provider_request,
    resolve_provider_wire_profile,
)
from ciel_runtime_support.provider_readiness import (
    ProviderReadinessCapabilities,
    ProviderReadinessLmStudio,
    ProviderReadinessMode,
    ProviderReadinessServices,
    launch_readiness_errors as evaluate_provider_readiness,
)
from ciel_runtime_support.provider_request_builder import (
    OllamaRequestPorts,
    OpenAIRequestPorts,
    ProviderOptionPorts,
    ProviderRequestBudget,
    ProviderRequestBuilder,
)
from ciel_runtime_support.providers.ollama_runtime import (
    OllamaRuntimeService,
    OllamaRuntimeServices,
)
from ciel_runtime_support.provider_status import (
    ProviderStatusCatalog,
    ProviderStatusGeneric,
    ProviderStatusRouting,
    ProviderStatusServices,
    base_url_status_line as project_provider_base_url_status,
)
from ciel_runtime_support.prelaunch import (
    PrelaunchChannelCommands,
    PrelaunchChannelQuery,
    PrelaunchConfig,
    PrelaunchConstants,
    PrelaunchLaunchPolicy,
    PrelaunchMutations,
    PrelaunchOptions,
    PrelaunchPanelRows,
    PrelaunchSecrets,
    PrelaunchServices,
    PrelaunchTerminal,
    run_prelaunch_menu as execute_prelaunch_menu,
)
from ciel_runtime_support.prelaunch_terminal import (
    PrelaunchInputStyle,
    PrelaunchRenderBrand,
    PrelaunchRenderData,
    PrelaunchRenderServices,
    PrelaunchRenderText,
    TerminalSelectionServices,
    _prompt_menu_multiline_value_raw as read_menu_multiline_value_raw,
    _prompt_menu_value_raw as read_menu_value_raw,
    append_menu_key_debug_log as write_menu_key_debug_log,
    animated_ansi_text as render_animated_ansi_text,
    ansi as render_ansi,
    cell_width as terminal_cell_width,
    enable_ansi as enable_terminal_ansi,
    fit_cells as fit_terminal_cells,
    intro_panel_lines as render_intro_panel_lines,
    pad_cells as pad_terminal_cells,
    portable_select as run_portable_select,
    prompt_menu_multiline_value as read_menu_multiline_value,
    prompt_menu_value as read_menu_value,
    read_menu_key as read_terminal_menu_key,
    render_prelaunch_screen as render_prelaunch_terminal_screen,
)
from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime,
    PromptCompactionServices,
    PromptCompactionText,
    anthropic_message_has_tool_result as compacted_anthropic_message_has_tool_result,
    anthropic_safe_tail_start as compacted_anthropic_safe_tail_start,
    compact_chat_messages_for_budget as run_chat_prompt_compaction,
    compact_anthropic_body_for_budget as run_anthropic_prompt_compaction,
)
from ciel_runtime_support.prompt_injection import (
    append_anthropic_system_texts as project_append_anthropic_system_texts,
    normalize_anthropic_system_role_messages as project_normalize_anthropic_system_role_messages,
)
from ciel_runtime_support.runtime_adapters import RUNTIME_ADAPTERS
from ciel_runtime_support.runtime_compatibility import DEFAULT_RUNTIME_COMPATIBILITY
from ciel_runtime_support.runtime_logging import (
    LOG_LEVEL_NAMES,
    LOG_LEVELS,
    LogLevelRepository,
    RouterFileLogger,
    normalize_log_level as normalize_runtime_log_level,
)
from ciel_runtime_support.sse_trace import (
    SseTraceConfig,
    SseTracePorts,
    SseTraceRepository,
    summarize_payload as summarize_sse_payload,
)
from ciel_runtime_support.runtime_launch import (
    AgyLaunchChannel,
    AgyLaunchCliPolicy,
    AgyLaunchConfig,
    AgyLaunchConstants,
    AgyLaunchDispatch,
    AgyLaunchInstallation,
    AgyLaunchProcess,
    AgyLaunchRouting,
    AgyLaunchServices,
    ClaudeLaunchChannelDelivery,
    ClaudeLaunchChannelDiscovery,
    ClaudeLaunchConfig,
    ClaudeLaunchConstants,
    ClaudeLaunchDispatch,
    ClaudeLaunchInstallation,
    ClaudeLaunchMcpConfig,
    ClaudeLaunchPolicy,
    ClaudeLaunchProcess,
    ClaudeLaunchRouting,
    ClaudeLaunchServices,
    CodexLaunchChannel,
    CodexLaunchCliPolicy,
    CodexLaunchConfig,
    CodexLaunchConstants,
    CodexLaunchDispatch,
    CodexLaunchInstallation,
    CodexLaunchProcess,
    CodexLaunchRouting,
    CodexAppServerChannel,
    CodexAppServerCliPolicy,
    CodexAppServerConfig,
    CodexAppServerDispatch,
    CodexAppServerInstallation,
    CodexAppServerLaunchServices,
    CodexAppServerProcess,
    CodexAppServerRouting,
    CodexLaunchServices,
    run_agy,
    run_claude,
    run_codex,
    run_codex_app_server,
)
from ciel_runtime_support.streaming_anthropic import (
    AnthropicContinuationPolicy,
    AnthropicConversationContext,
    AnthropicStreamIO,
    AnthropicStreamServices,
    AnthropicToolPolicy,
    AnthropicToolProjection,
    OllamaContinuationPolicy,
    OllamaStreamIO,
    OllamaStreamServices,
    OllamaStreamTrace,
    OllamaToolProjection,
    OpenAIChatContinuationPolicy,
    OpenAIChatStreamIO,
    OpenAIChatStreamServices,
    OpenAIChatToolProjection,
    forward_openai_chat_to_anthropic_sse,
    ollama_stream_to_anthropic_sse,
    rebatch_anthropic_sse_text,
)
from ciel_runtime_support.pseudo_tool_parser import (
    PseudoToolParserServices,
    infer_tool_name_from_args as project_infer_tool_name,
    normalize_tool_arguments as project_normalize_tool_arguments,
    parse_pseudo_tool_calls as project_parse_pseudo_tool_calls,
)
from ciel_runtime_support.stream_chunk_policy import split_word_buffer
from ciel_runtime_support.session_import import (
    ImportSessionLimits,
    ImportSessionRepository,
    ImportSessionService,
    import_record_line,
    import_tool_text,
    normalize_import_source,
)
from ciel_runtime_support.upstream_retry import (
    UpstreamRetryHttp,
    UpstreamRetryKeys,
    UpstreamRetryPolicy,
    UpstreamRetryRateLimit,
    UpstreamRetryServices,
    open_openai_stream_with_rate_retry as retry_openai_stream,
    open_provider_request_with_key_retry as retry_provider_request,
    post_json_with_rate_retry as retry_post_json,
)
from ciel_runtime_support.upstream_error_policy import (
    configured_gateway_retries as project_configured_gateway_retries,
    http_error_message as project_upstream_http_error_message,
    retry_message as project_upstream_retry_message,
    retry_wait_seconds as project_upstream_retry_wait_seconds,
    retryable_exception as project_retryable_upstream_exception,
)
from ciel_runtime_support.upstream_stream_io import (
    UpstreamClientDisconnected,
    client_connection_closed as project_client_connection_closed,
    iter_lines_until_disconnect as project_iter_upstream_lines,
    set_stream_read_timeout as project_set_stream_read_timeout,
    sleep_until_disconnect as project_sleep_until_disconnect,
    stream_idle_timeout as project_stream_idle_timeout,
)
from ciel_runtime_support.tool_dialects import TOOL_DIALECTS, mcp_server_normalized_key
from ciel_runtime_support.tool_schema import (
    _fuzzy_match_tool_name,
    _lookup_tool_schema,
    _missing_required_tool_fields,
    _update_tool_schema_registry,
    _validate_and_fix_tool_input as _tool_schema_validate_and_fix,
)
from ciel_runtime_support.usage_events import JsonlUsageEventSink, UsageEvent
from ciel_runtime_support.ui_text import PROVIDER_NOTES, UI_TEXT
from ciel_runtime_support.transcript_filter import (
    is_claude_code_transcript_event,
)
from ciel_runtime_support.web_ui import render_router_home_page, render_web_chat_page
from ciel_runtime_support.windows_console_input import (
    WindowsConsoleInputWriter,
    _windows_console_utf16_units as project_windows_console_utf16_units,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HOME = Path.home()


def platform_path(value: str | os.PathLike[str]) -> Any:
    if os.name == "nt" and sys.platform != "win32":
        return PureWindowsPath(value)
    return Path(value)


def windows_appdata_root() -> Path:
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        raw = os.environ.get(env_name)
        if raw:
            return platform_path(raw)
    return HOME / "AppData" / "Roaming"


def windows_local_appdata_root() -> Path:
    raw = os.environ.get("LOCALAPPDATA")
    if raw:
        return platform_path(raw)
    return HOME / "AppData" / "Local"


def platform_config_dir(app_name: str) -> Path:
    if os.name == "nt":
        return windows_appdata_root() / app_name
    return HOME / ".config" / app_name


def ciel_runtime_user_bin_dir() -> Path:
    if os.name == "nt":
        return windows_local_appdata_root() / "ciel-runtime" / "bin"
    return HOME / ".local" / "bin"


def agy_user_bin_dir() -> Path:
    if os.name == "nt":
        return windows_local_appdata_root() / "agy" / "bin"
    return HOME / ".local" / "bin"


def path_with_ciel_runtime_user_dirs(env: dict[str, str]) -> str:
    dirs = [ciel_runtime_user_bin_dir(), agy_user_bin_dir()]
    if os.name == "nt":
        appdata = env.get("APPDATA") or os.environ.get("APPDATA")
        if appdata:
            dirs.append(platform_path(appdata) / "npm")
        local_appdata = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA")
        if local_appdata:
            dirs.append(platform_path(local_appdata) / "Programs" / "nodejs")
    existing = env.get("PATH", "")
    prefix = os.pathsep.join(str(path) for path in dirs if str(path))
    return prefix + (os.pathsep + existing if existing else "")


def default_router_port() -> int:
    configured = str(os.environ.get("CIEL_RUNTIME_ROUTER_PORT") or "").strip()
    if configured:
        try:
            port = int(configured)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
    base = 8799
    try:
        getuid = getattr(os, "getuid")
    except AttributeError:
        getuid = None
    if callable(getuid):
        try:
            return base + (int(getuid()) % 1000)
        except Exception:
            pass
    seed = f"{getpass.getuser()}|{HOME}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()
    return base + (int(digest[:8], 16) % 1000)


CONFIG_DIR = Path(os.environ.get("CIEL_RUNTIME_CONFIG_DIR") or platform_config_dir("ciel-runtime"))
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_PATH = CONFIG_DIR / "router.log"
LOG_LEVEL_PATH = CONFIG_DIR / "log-level"
REQUEST_DUMP_PATH = CONFIG_DIR / "requests.jsonl"
RESPONSE_DUMP_PATH = CONFIG_DIR / "responses.jsonl"
USAGE_EVENTS_PATH = CONFIG_DIR / "usage-events.jsonl"
SSE_TRACE_PATH = CONFIG_DIR / "router-sse-trace.jsonl"
SSE_LAST_PATH = CONFIG_DIR / "router-last-sse.json"
TOOL_CALL_LOG_PATH = CONFIG_DIR / "tool-calls.jsonl"
RATE_LIMIT_STATE_PATH = CONFIG_DIR / "rate-limit-state.json"
ROUTER_ACTIVITY_PATH = CONFIG_DIR / "router-activity.json"
CONTEXT_COMPACT_ACTIVITY_PATH = CONFIG_DIR / "context-compact-activity.json"
CONTEXT_USAGE_PATH = CONFIG_DIR / "context-usage.json"
OLLAMA_MODEL_CATALOG_PATH = CONFIG_DIR / "ollama-model-catalog.json"
CHAT_MESSAGES_PATH = CONFIG_DIR / "chat-messages.jsonl"
CHAT_FILES_DIR = CONFIG_DIR / "chat-files"
MENU_KEY_DEBUG_PATH = CONFIG_DIR / "ca-key-debug.log"
PLAN_ARTIFACTS_DIR = CONFIG_DIR / "plan-artifacts"
PID_PATH = CONFIG_DIR / "router.pid"
ROUTER_EXTERNAL_TOKEN_PATH = CONFIG_DIR / "router-external-token"
ROUTER_CLIENTS_DIR = CONFIG_DIR / "router-clients"
MODEL_LIST_CACHE_PATH = CONFIG_DIR / "model-list-cache.json"
MODEL_REGISTRY_PATH = CONFIG_DIR / "model-registry.json"
LAUNCH_STATE_PATH = CONFIG_DIR / "launch-state.json"
WEB_TOOLS_MCP_CONFIG = CONFIG_DIR / "web-tools-mcp.json"
DUCKDUCKGO_MCP_CONFIG = CONFIG_DIR / "duckduckgo-mcp.json"
ZAI_MCP_CONFIG = CONFIG_DIR / "zai-mcp.json"
CHANNEL_MCP_CONFIG = CONFIG_DIR / "channel-mcp.json"
NATIVE_MCP_CONFIG = CONFIG_DIR / "native-mcp.json"
CODEX_MCP_CONFIG = CONFIG_DIR / "codex-mcp.json"
CODEX_PROCESS_DIR = CONFIG_DIR / "codex-processes"
CODEX_PROMPTS_DIR_NAME = "prompts"
CHANNEL_MCP_CURSOR_PATH = CONFIG_DIR / "channel-mcp-cursor.json"
CHANNEL_LLM_CURSOR_PATH = CONFIG_DIR / "channel-llm-cursor.json"
CHANNEL_LLM_CLEAR_FLOOR_PATH = CONFIG_DIR / "channel-llm-clear-floor.json"
CHANNEL_LLM_LAUNCH_GUARD_PATH = CONFIG_DIR / "channel-llm-launch-guard.json"
CHANNEL_COMPACT_REQUEST_PATH = CONFIG_DIR / "channel-compact-request.json"
CHANNEL_STDIN_WAKE_CLAIMS_PATH = CONFIG_DIR / "channel-stdin-wake-claims.json"
CHANNEL_PROBE_CACHE_PATH = CONFIG_DIR / "channel-probe-cache.json"
MCP_PROXY_CONFIG = CONFIG_DIR / "mcp-proxy.json"
ROUTER_HOST = os.environ.get("CIEL_RUNTIME_ROUTER_CLIENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
ROUTER_PORT = default_router_port()
ROUTER_BASE = f"http://{ROUTER_HOST}:{ROUTER_PORT}"
CLAUDE_GATEWAY_CACHE = HOME / ".claude" / "cache" / "gateway-models.json"
CLAUDE_SETTINGS_PATH = HOME / ".claude" / "settings.json"
CLAUDE_COMMANDS_DIR = HOME / ".claude" / "commands"
CIEL_RUNTIME_STATUSLINE_PATH = ciel_runtime_user_bin_dir() / "ciel-runtime-statusline.py"
NCP_ENV = platform_config_dir("nvd-claude-proxy") / ".env"
NCP_LOG = platform_config_dir("nvd-claude-proxy") / "proxy.log"
MODEL_CACHE_TTL_SECONDS = 300
OLLAMA_MODEL_CATALOG_URL = "https://ollama.com/api/tags"
OLLAMA_MODEL_CATALOG_TTL_SECONDS = 24 * 60 * 60
ANTHROPIC_MODEL_DOCS_URL = "https://docs.anthropic.com/en/docs/about-claude/models/overview"
ANTHROPIC_MODEL_DOCS_URLS = (
    ANTHROPIC_MODEL_DOCS_URL,
    "https://platform.claude.com/docs/en/about-claude/models/overview",
)
ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS: tuple[str, ...] = (
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",
)
ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS: tuple[str, ...] = ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS
ANTHROPIC_LIMITED_ACCESS_MODEL_IDS: tuple[str, ...] = (
    "claude-mythos-5",
    "claude-mythos-preview",
)
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go"
KIMI_CODING_BASE_URL = "https://api.kimi.com/coding"
KIMI_DEFAULT_MODEL = "kimi-for-coding"
KIMI_K3_MODEL = "k3"
KIMI_MODEL_FALLBACK_IDS: tuple[str, ...] = (KIMI_K3_MODEL, KIMI_DEFAULT_MODEL)
ZAI_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"
ZAI_DEFAULT_MODEL = "glm-5.2[1m]"
ZAI_MODEL_CONTEXT_HINTS: tuple[tuple[str, int], ...] = (
    ("glm-5.2", 1_000_000),
    ("glm-5-turbo", 200_000),
    ("glm-5.1", 200_000),
    ("glm-5", 200_000),
    ("glm-4.7", 200_000),
    ("glm-4.6", 200_000),
    ("glm-4.5", 128_000),
    ("glm-4-32b-0414-128k", 128_000),
)
ZAI_MANAGED_MCP_SERVERS: tuple[tuple[str, str], ...] = (
    ("web-search-prime", "https://api.z.ai/api/mcp/web_search_prime/mcp"),
    ("web-reader", "https://api.z.ai/api/mcp/web_reader/mcp"),
    ("zread", "https://api.z.ai/api/mcp/zread/mcp"),
)
FIREWORKS_INFERENCE_BASE_URL = "https://api.fireworks.ai/inference"
FIREWORKS_API_BASE_URL = "https://api.fireworks.ai"
FIREWORKS_DEFAULT_ACCOUNT_ID = "fireworks"
NCP_PYPI_PACKAGE = "nvd-claude-proxy"

PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "claude-native": "anthropic",
    "native": "anthropic",
    "claude-code": "anthropic",
    "agy": "agy",
    "antigravity": "agy",
    "google-antigravity": "agy",
    "agy-native": "agy",
    "native-agy": "agy",
    "codex": "codex",
    "codex-native": "codex",
    "native-codex": "codex",
    "openai-codex": "codex",
    "ollama": "ollama",
    "ollama-cloud": "ollama-cloud",
    "cloud-ollama": "ollama-cloud",
    "deepseek": "deepseek",
    "deepseek.com": "deepseek",
    "deepseek-com": "deepseek",
    "deepseek-api": "deepseek",
    "ds": "deepseek",
    "opencode": "opencode",
    "opencode.ai": "opencode",
    "opencode-ai": "opencode",
    "opencode-zen": "opencode",
    "zen": "opencode",
    "opencode-go": "opencode-go",
    "opencode.go": "opencode-go",
    "opencode_go": "opencode-go",
    "opencodego": "opencode-go",
    "kimi": "kimi",
    "kimi.com": "kimi",
    "kimi-code": "kimi",
    "kimi-coding": "kimi",
    "moonshot": "kimi",
    "moonshot-kimi": "kimi",
    "zai": "zai",
    "z.ai": "zai",
    "z-ai": "zai",
    "zhipu": "zai",
    "bigmodel": "zai",
    "glm": "zai",
    "vllm": "vllm",
    "vllm-local": "vllm",
    "lm-studio": "lm-studio",
    "lmstudio": "lm-studio",
    "lm": "lm-studio",
    "nvidia": "nvidia-hosted",
    "nvidia-hosted": "nvidia-hosted",
    "hosted-nvidia": "nvidia-hosted",
    "nim": "self-hosted-nim",
    "self-hosted-nim": "self-hosted-nim",
    "self-nim": "self-hosted-nim",
    "openrouter": "openrouter",
    "open-router": "openrouter",
    "openrouter.ai": "openrouter",
    "or": "openrouter",
    "fireworks": "fireworks",
    "fireworks.ai": "fireworks",
    "fireworks-ai": "fireworks",
    "fw": "fireworks",
}

ANTHROPIC_NATIVE_PROVIDER_CHOICE = "anthropic:native"
ANTHROPIC_ROUTED_PROVIDER_CHOICE = "anthropic:routed"
AGY_NATIVE_PROVIDER_CHOICE = "agy:native"
AGY_ROUTED_PROVIDER_CHOICE = "agy:routed"
CODEX_NATIVE_PROVIDER_CHOICE = "codex:native"
CODEX_ROUTED_PROVIDER_CHOICE = "codex:routed"

OPENCODE_PROVIDER_NAMES = provider_network.OPENCODE_PROVIDER_NAMES
OPENCODE_ENDPOINT_ALIASES = {
    "messages": "anthropic-messages",
    "anthropic": "anthropic-messages",
    "anthropic-messages": "anthropic-messages",
    "chat": "openai-chat",
    "openai-chat": "openai-chat",
    "chat-completions": "openai-chat",
    "responses": "openai-responses",
    "openai-responses": "openai-responses",
    "gemini": "google-generative",
    "google": "google-generative",
    "google-generative": "google-generative",
}
OFFICIAL_CHANNEL_PLUGINS = {
    "telegram": "plugin:telegram@claude-plugins-official",
    "discord": "plugin:discord@claude-plugins-official",
    "imessage": "plugin:imessage@claude-plugins-official",
    "fakechat": "plugin:fakechat@claude-plugins-official",
}
APP_NAME = "Ciel Runtime"
VERSION = "0.1.1"
DEFAULT_UPSTREAM_USER_AGENT = provider_network.DEFAULT_UPSTREAM_USER_AGENT


def upstream_user_agent() -> str:
    """Return the User-Agent used for upstream provider HTTP calls.

    Some provider gateways/WAFs treat Python's default urllib identity as a
    non-CLI browser signature. ciel-runtime is acting as the Claude CLI transport
    here, so keep that identity explicit and generic across providers.
    """
    return provider_network.upstream_user_agent()


def with_upstream_user_agent(headers: dict[str, str] | None = None) -> dict[str, str]:
    return provider_network.with_upstream_user_agent(headers)


IP_FAMILY_ALIASES = provider_network.IP_FAMILY_ALIASES
IP_FAMILY_CHOICES = provider_network.IP_FAMILY_CHOICES


def normalize_ip_family(value: Any, default: str = "auto") -> str:
    return provider_network.normalize_ip_family(value, default)


def default_provider_ip_family(provider: str) -> str:
    return provider_network.default_provider_ip_family(provider)


def provider_ip_family(provider: str | None, pcfg: dict[str, Any] | None) -> str:
    return provider_network.provider_ip_family(provider, pcfg)


@contextlib.contextmanager
def socket_getaddrinfo_ip_family_policy(ip_family: str) -> Iterable[None]:
    with provider_network.socket_ip_family_policy(ip_family):
        yield


def provider_urlopen(
    req: urllib.request.Request,
    timeout: float,
    provider: str | None = None,
    pcfg: dict[str, Any] | None = None,
) -> Any:
    return provider_network.provider_urlopen(req, timeout, provider, pcfg, router_log)


def ip_family_connectivity(host: str, port: int, family: int, timeout: float = 1.5) -> tuple[bool, str]:
    return provider_network.ip_family_connectivity(host, port, family, timeout)


def provider_ip_family_probe_lines(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return provider_network.provider_ip_family_probe_lines(provider, pcfg, default_base_url)


def ciel_runtime_source_fingerprint() -> str:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    except Exception:
        try:
            stat = Path(__file__).stat()
            return f"{int(stat.st_mtime_ns)}-{int(stat.st_size)}"
        except Exception:
            return "unknown"


SOURCE_FINGERPRINT = ciel_runtime_source_fingerprint()
CREDITS = "Credits: One Ciel LLC"
PRELAUNCH_CANCEL = 10
PRELAUNCH_LAUNCH_CODEX = 11
PRELAUNCH_LAUNCH_CLAUDE = 12
PRELAUNCH_LAUNCH_AGY = 13
PRELAUNCH_LAUNCH_CODEX_APP_SERVER = 14

LOG_LEVEL_DEFAULT = LOG_LEVELS["ERROR"]
ROUTER_LOG_MAX_BYTES = 1_000_000
REQUEST_DUMP_MAX_BYTES = 5_000_000
RESPONSE_DUMP_MAX_BYTES = 5_000_000
RESPONSE_DUMP_TEXT_LIMIT = 16_000
SSE_TRACE_MAX_BYTES = 2 * 1024 * 1024
SSE_TRACE_EVENT_LIMIT = 240
SSE_TRACE_PAYLOAD_LIMIT = 4_000
CHAT_MESSAGES_MAX_BYTES = 20_000_000
CHAT_MESSAGE_DEDUPE_SCAN_LIMIT = 500
CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS = 30.0
CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT = 600.0
DEFAULT_REQUEST_TIMEOUT_MS = 300000
_LOG_LEVEL_CACHE: dict[str, Any] = {"value": None, "checked_at": 0.0, "file_mtime": 0.0}
_RATE_LIMIT_LOCK = threading.Lock()
_API_KEY_ROTATION_LOCK = threading.Lock()
_API_KEY_ROTATION_CURSOR: dict[str, int] = {}
_CHAT_CONDITION = threading.Condition()
_CHAT_NEXT_ID: int | None = None
_CHANNEL_SSE_LOCK = threading.Lock()
_CHANNEL_SSE_CONNECTIONS: dict[str, dict[str, Any]] = {}
_CHANNEL_SSE_RPC_CONDITION = threading.Condition()
_CHANNEL_MCP_LOCK = threading.Lock()
_CHANNEL_MCP_SESSIONS: dict[str, dict[str, Any]] = {}
_CHANNEL_MCP_CURSOR_LOCK = threading.Lock()
_CHANNEL_MCP_CURSOR_LAST_ID: int | None = None
_CHANNEL_LLM_CURSOR_LOCK = threading.Lock()
_CHANNEL_LLM_CURSOR_LAST_ID: int | None = None
_CHANNEL_STDIN_WAKE_LOCK = threading.Lock()
_CHANNEL_STDIN_INJECT_LOCK = threading.Lock()
_CHANNEL_STDIN_WAKE_DELIVERED: set[int] = set()
_CHANNEL_STDIN_WAKE_PROMPTS: dict[int, str] = {}
_CHANNEL_COMPACT_REQUEST_LOCK = threading.Lock()
_NATIVE_CHANNEL_NOTIFICATION_METHOD = "notifications/claude/channel"
BUILTIN_CHANNEL_SPEC = "server:ciel-runtime-router"
CHANNEL_LLM_WAKE_PREFIX = "[external input pending]"
CHANNEL_LLM_WAKE_LEGACY_PREFIXES = ("[ciel-runtime channel wake]", "[channel pending]")
_MCP_NOTIFICATION_DEDUP_TTL_SECONDS = 3.0
_MCP_NOTIFICATION_DEDUP_LOCK = threading.Lock()
_MCP_NOTIFICATION_DEDUP_RECENT: dict[str, tuple[str, float]] = {}
_MCP_NOTIFICATION_WAIT_RECENT: dict[str, float] = {}
_MCP_NOTIFICATION_WAIT_RECENT_LOCK = threading.Lock()
MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT = 24000
MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS = 6000
_TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS = 10 * 60.0
_TOOL_SIDE_EFFECT_DEDUP_LOCK = threading.Lock()
_TOOL_SIDE_EFFECT_DEDUP_RECENT: dict[str, float] = {}
EVENT_BUS = EventBus()
USAGE_EVENT_SINK = JsonlUsageEventSink(
    USAGE_EVENTS_PATH,
    enabled=lambda: str(os.environ.get("CIEL_RUNTIME_USAGE_LOG", "1")).strip().lower()
    not in {"0", "false", "off", "no", ""},
)
ADVISOR_FEEDBACK_MARKER = "CIEL_RUNTIME_ADVISOR_FEEDBACK"
PLAN_GUARD_MARKER = "[ciel-runtime-plan-guard]"
# Tools Claude Code injects into every model's tool list that misfire when called
# by non-Anthropic models. See docs/notes from anthropics/claude-code issues
# #25720, #29950 and Piebald-AI/claude-code-system-prompts for tool semantics.
PLAN_MODE_SELF_TOOLS: tuple[str, ...] = ("EnterPlanMode", "ExitPlanMode")
ANTHROPIC_THINKING_BLOCK_TYPES: tuple[str, ...] = ("thinking", "redacted_thinking")


def positive_env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return default


SUPPRESSED_THINKING_PASSBACK_MAX = positive_env_int("CIEL_RUNTIME_THINKING_PASSBACK_MAX", 4096)
SUPPRESSED_THINKING_PASSBACK_CACHE: list[dict[str, Any]] = []
SUPPRESSED_THINKING_REPOSITORY = SuppressedThinkingRepository(
    SUPPRESSED_THINKING_PASSBACK_CACHE,
    capacity=lambda: SUPPRESSED_THINKING_PASSBACK_MAX,
)
DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC: tuple[str, ...] = (
    "EnterWorktree",
    "ExitWorktree",
    "TeamCreate",
    "TeamDelete",
    "TeammateTool",
    "SendMessage",
    "SendMessageTool",
    "ScheduleWakeup",
    "WaitForMcpServers",
    "WebSearch",
    "web_search",
    "WebFetch",
    "web_fetch",
    "RemoteTrigger",
    "PushNotification",
)
CLAUDE_SERVER_SIDE_WEB_TOOLS: tuple[str, ...] = ("WebSearch", "WebFetch")
ROUTED_COMPAT_PROMPT = (
    "You are running inside Claude Code through the ciel-runtime router. "
    "Do not stop after announcing what you plan to do. When the user asks you to create, edit, or run code, "
    "immediately use the available Claude Code tools such as Write, Edit, Read, and Bash as appropriate, "
    "except while Claude Code is in Plan Mode. In Plan Mode, first explore/read as needed, write or update the plan file named "
    "by the plan_mode attachment, and only then call ExitPlanMode to leave Plan Mode; when bypass permissions is active, "
    "ciel-runtime auto-approves that plan exit, so do not ask the user separately and do not call EnterPlanMode again. "
    "then report the concrete result. If the task has several reasonable implementation parts, do all in-scope parts; "
    "do not ask the user which part to start or whether to do all unless the user explicitly requested a choice. "
    "If you decide not to use tools, provide the complete requested code or answer in the same turn. "
    "Use skills only when the user's request clearly matches that skill; never invoke keybindings-help unless the user asks about keybindings. "
    "Keep final answers concise and do not expose hidden chain-of-thought. "
    "When calling Claude Code tools, use exactly the tool schema and do not invent extra fields. "
    "Bash: command (string), description (string), timeout (integer), run_in_background (boolean). "
    "Read: file_path (string), offset (integer), limit (integer). "
    "Write: file_path (string), content (string). "
    "Edit: file_path (string), old_string (string), new_string (string), replace_all (boolean). "
    "TaskList: no input. TaskUpdate: taskId (string), optional status enum exactly one of pending, in_progress, completed, deleted. "
    "CronCreate: cron (standard 5-field local-time cron string), prompt (string), optional recurring (boolean), optional durable (boolean). "
    "CronDelete: id (string returned by CronCreate). CronList: no input. "
    "Do not call WaitForMcpServers; it is a Claude Code lifecycle tool that may exist but is often not enabled in the current routed context. "
    "If an MCP server appears disconnected, use only tools present in the current tool list, retry ordinary MCP tools when available, or report the concrete connection state. "
    "Never write pseudo tool calls, partial JSON, or markdown code fences when a real Claude Code tool call is required."
)
NON_ANTHROPIC_COMPAT_PROMPT = ROUTED_COMPAT_PROMPT
LANGUAGES = {
    "en": "English",
    "ko": "한국어",
    "ja": "日本語",
    "zh": "中文",
}

MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "glm-5.2": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 999424},
    "glm-5.2:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 999424},
    "glm-4.7": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-5.1": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-4.7:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "glm-5.1:cloud": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "qwen3-coder": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "qwen3-coder:30b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "qwen3.6:27b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 65536},
    "deepseek-r1": {"compat_max_tokens": 64, "thinking": True, "num_ctx_min": 32768, "num_ctx_max": 131072},
    "llama3.3:70b": {"compat_max_tokens": 16, "thinking": False, "num_ctx_min": 32768, "num_ctx_max": 131072},
}
LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT = 32768
LM_STUDIO_DEFAULT_CLAUDE_CODE_CONTEXT = 65536


def nvidia_hosted_context_default(model_id: str) -> int:
    model = model_id.lower()
    if "kimi-k2.6" in model or "kimi_k2.6" in model:
        return 262144
    if "deepseek" in model:
        return 131072
    if "glm" in model or "qwen" in model:
        return 65536
    return 65536


def model_lookup_ids(model_id: str) -> list[str]:
    raw = (model_id or "").strip()
    if not raw:
        return []
    out = [raw]
    low = raw.lower().replace("_", "-")
    compact = re.sub(r"[^a-z0-9]+", "", low)

    def add(value: str) -> None:
        if value and value not in out:
            out.append(value)

    if ("qwen3.6" in low or "qwen36" in compact) and "27b" in low:
        add("qwen3.6:27b")
    if ("qwen3.6" in low or "qwen36" in compact) and "35b" in low:
        add("qwen3.6:35b-a3b")
        add("qwen3.6:35b")
    return out


def model_preset(model_id: str) -> dict[str, Any]:
    """Return preset dict for a model ID, checking exact match then prefix match."""
    for candidate in model_lookup_ids(model_id):
        if candidate in MODEL_PRESETS:
            return MODEL_PRESETS[candidate]
        for key, value in MODEL_PRESETS.items():
            candidate_base = candidate.split(":", 1)[0]
            if candidate.startswith(key) or (":" not in candidate and key.startswith(candidate_base)):
                return value
    return {}


def compat_max_tokens_for_model(model_id: str) -> int:
    return model_preset(model_id).get("compat_max_tokens", 16)


def ollama_library_model_parts(model_id: str) -> tuple[str, str] | None:
    return ollama_catalog_policy.library_model_parts(model_id)


def context_label_to_tokens(number: str, unit: str | None) -> int | None:
    return ollama_catalog_policy.context_label_to_tokens(number, unit)


def recommended_timeout_ms_for_context(context_tokens: int | None) -> int:
    return ollama_catalog_policy.recommended_timeout_ms(context_tokens, DEFAULT_REQUEST_TIMEOUT_MS)


def ollama_model_catalog_key(model_id: str) -> tuple[str, str, str] | None:
    return ollama_catalog_policy.model_catalog_key(model_id)


def ollama_catalog_repository() -> OllamaCatalogRepository:
    return OllamaCatalogRepository(OLLAMA_MODEL_CATALOG_PATH, router_log, with_upstream_user_agent)


def load_ollama_model_catalog() -> dict[str, Any]:
    return ollama_catalog_repository().load()


def save_ollama_model_catalog(catalog: dict[str, Any]) -> None:
    ollama_catalog_repository().save(catalog)


def ollama_catalog_model_ids(provider: str = "ollama-cloud", catalog: dict[str, Any] | None = None) -> list[str]:
    source = catalog if isinstance(catalog, dict) else load_ollama_model_catalog()
    return ollama_catalog_policy.catalog_model_ids(
        source, provider, normalize_model_id=normalize_model_id,
        unique_model_ids=unique_model_ids, sorted_model_ids=sorted_model_ids,
    )


def ollama_catalog_is_stale(catalog: dict[str, Any], ttl_seconds: int = OLLAMA_MODEL_CATALOG_TTL_SECONDS) -> bool:
    return ollama_catalog_policy.catalog_is_stale(catalog, ttl_seconds)


def fetch_json_url(url: str, timeout: float = 12.0) -> Any:
    return ollama_catalog_repository().fetch_json(url, timeout)


def context_tokens_from_ollama_snippet(snippet: str, table_fallback: bool = True) -> int | None:
    return ollama_catalog_policy.context_tokens_from_snippet(snippet, table_fallback)


def parse_ollama_library_context_map(page_html: str, base_model: str) -> dict[str, int]:
    return ollama_catalog_policy.parse_library_context_map(page_html, base_model)


def fetch_ollama_library_context_map(base_model: str, timeout: float = 10.0) -> tuple[dict[str, int], str | None]:
    return ollama_catalog_repository().fetch_library_context_map(base_model, timeout)


def refresh_ollama_model_catalog(include_contexts: bool = True, timeout: float = 10.0) -> dict[str, Any]:
    return ollama_catalog_policy.refresh_model_catalog(
        ollama_catalog_policy.OllamaCatalogRefreshServices(
            load_catalog=load_ollama_model_catalog,
            fetch_catalog=fetch_json_url,
            fetch_context_map=fetch_ollama_library_context_map,
            save_catalog=save_ollama_model_catalog,
            positive_int=positive_int,
        ),
        include_contexts=include_contexts,
        timeout=timeout,
        catalog_url=OLLAMA_MODEL_CATALOG_URL,
    )


def ollama_catalog_context_for_model(model_id: str) -> tuple[int | None, str | None, str | None]:
    return ollama_catalog_policy.catalog_context_for_model(
        load_ollama_model_catalog(), model_id, model_lookup_ids,
    )


def ollama_catalog_timeout_for_model(model_id: str) -> int | None:
    return ollama_catalog_policy.catalog_timeout_for_model(
        load_ollama_model_catalog(), model_id, model_lookup_ids,
    )


def update_ollama_catalog_context(model_id: str, limit: int, matched_model: str | None, source_url: str | None) -> None:
    catalog = ollama_catalog_policy.with_updated_context(
        load_ollama_model_catalog(), model_id, limit, matched_model, source_url,
    )
    save_ollama_model_catalog(catalog)


def parse_ollama_library_context_limit(tags_html: str, full_model_id: str) -> int | None:
    return ollama_catalog_policy.parse_library_context_limit(tags_html, full_model_id)


def fetch_ollama_library_context_limit(model_id: str, timeout: float = 6.0) -> tuple[int | None, str | None, str | None]:
    parts = ollama_library_model_parts(model_id)
    if not parts:
        return None, None, None
    base, tag = parts
    full_model = f"{base}:{tag}"
    context_map, url = fetch_ollama_library_context_map(base, timeout=timeout)
    limit = positive_int(context_map.get(tag.lower()))
    if not limit and tag == "latest":
        cloud_limit = positive_int(context_map.get("cloud"))
        if cloud_limit:
            return cloud_limit, f"{base}:cloud", url
    return limit, full_model, url


def ollama_context_model_matches(current_model: str, cached_model: str | None) -> bool:
    return ollama_catalog_policy.context_model_matches(current_model, cached_model)


def sync_ollama_library_context_limit(provider: str, pcfg: dict[str, Any], model_id: str) -> list[str]:
    return sync_ollama_context_limit(
        provider,
        pcfg,
        model_id,
        OllamaContextSources(
            fetch_api_specs=fetch_ollama_api_model_specs,
            load_catalog=load_ollama_model_catalog,
            catalog_is_stale=ollama_catalog_is_stale,
            refresh_catalog=refresh_ollama_model_catalog,
            catalog_context=ollama_catalog_context_for_model,
            fetch_library_context=fetch_ollama_library_context_limit,
            update_catalog_context=update_ollama_catalog_context,
        ),
        OllamaContextPolicy(
            positive_int=positive_int,
            normalize_model_id=normalize_model_id,
            model_context_hint=model_context_hint_from_model_id,
            context_model_matches=ollama_context_model_matches,
            preserve_configured_cap=ollama_preserve_configured_context_cap,
            log=router_log,
        ),
    )


# ---------------------------------------------------------------------------
# Tool schema registry and parameter validation
# ---------------------------------------------------------------------------

def _validate_and_fix_tool_input(
    tool_name: str,
    input_dict: dict[str, Any],
    source_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dialect = TOOL_DIALECTS.create(
        "claude",
        repair=lambda name, value: _tool_schema_validate_and_fix(name, value, source_body, log=router_log),
    )
    return dict(dialect.repair_tool_input(tool_name, input_dict))
def should_drop_emitted_tool_call(
    tool_name: str,
    tool_input: dict[str, Any],
    raw_name: str = "",
    source_body: dict[str, Any] | None = None,
) -> bool:
    missing = _missing_required_tool_fields(tool_name, tool_input, source_body)
    if not missing:
        return False
    router_log(
        "WARN",
        f"dropped emitted tool call with missing required fields raw_name={raw_name or tool_name!r} "
        f"matched_name={tool_name!r} fields={','.join(missing)}",
    )
    append_tool_call_log(
        "dropped_tool_call_missing_required",
        {
            "raw_name": raw_name or tool_name,
            "matched_name": tool_name,
            "missing_required": missing,
            "emitted_input": tool_input,
        },
    )
    return True


_SIDE_EFFECT_TOOL_SUFFIXES = {
    "send_message",
    "send_dm",
    "send_file",
    "create_message",
    "create_dm",
    "post_message",
    "reply",
}


def side_effect_tool_call_dedupe_key(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Stable key for exact duplicate side-effect tool calls.

    This intentionally avoids read-only tools such as get_messages. Some
    non-native streaming backends can repeat the same side-effect MCP tool call
    after receiving its tool result, which posts duplicate external messages.
    """
    if not isinstance(tool_name, str) or not tool_name:
        return None
    normalized_name = tool_name.strip()
    tool_leaf = normalized_name.rsplit("__", 1)[-1].strip().lower()
    if tool_leaf not in _SIDE_EFFECT_TOOL_SUFFIXES:
        return None
    try:
        payload = json.dumps(tool_input or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        payload = repr(tool_input)
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"{normalized_name}:{digest}"


def should_drop_duplicate_side_effect_tool_call(
    tool_name: str,
    tool_input: dict[str, Any],
    raw_name: str = "",
) -> bool:
    key = side_effect_tool_call_dedupe_key(tool_name, tool_input)
    if not key:
        return False
    now = time.monotonic()
    with _TOOL_SIDE_EFFECT_DEDUP_LOCK:
        expired = [k for k, ts in _TOOL_SIDE_EFFECT_DEDUP_RECENT.items() if now - ts > _TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS]
        for expired_key in expired:
            _TOOL_SIDE_EFFECT_DEDUP_RECENT.pop(expired_key, None)
        previous = _TOOL_SIDE_EFFECT_DEDUP_RECENT.get(key)
        if previous is not None and now - previous <= _TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS:
            append_tool_call_log(
                "dropped_duplicate_side_effect_tool_call",
                {
                    "raw_name": raw_name or tool_name,
                    "matched_name": tool_name,
                    "emitted_input": tool_input,
                    "age_seconds": round(now - previous, 3),
                    "ttl_seconds": _TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS,
                },
            )
            router_log(
                "WARN",
                f"dropped duplicate side-effect tool call raw_name={raw_name or tool_name!r} "
                f"matched_name={tool_name!r} age={now - previous:.1f}s",
            )
            return True
        _TOOL_SIDE_EFFECT_DEDUP_RECENT[key] = now
    return False


MCP_NOTIFICATION_WAIT_TOOL_NAMES = {
    "wait_for_notification",
    "wait_for_notifications",
    "wait_for_message",
    "wait_for_messages",
    "wait_for_event",
    "wait_for_events",
    "wait_for_response",
    "wait_for_responses",
}


def _mcp_tool_leaf_name(tool_name: str) -> str:
    text = str(tool_name or "").strip()
    if "__" in text:
        return text.rsplit("__", 1)[-1].strip().lower()
    return text.lower()


def _is_mcp_notification_wait_tool(tool_name: str) -> bool:
    text = str(tool_name or "").strip().lower()
    if not text.startswith("mcp__"):
        return False
    return _mcp_tool_leaf_name(text) in MCP_NOTIFICATION_WAIT_TOOL_NAMES


def _mcp_notification_wait_timeout_cap_ms() -> int:
    raw = os.environ.get("CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS")
    if raw is None:
        return 1000
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 1000
    if value <= 0:
        return 0
    return max(100, min(10_000, value))


def _mcp_notification_wait_duplicate_cap_ms() -> int:
    raw = os.environ.get("CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_TIMEOUT_MS")
    if raw is None:
        return 100
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 100
    if value <= 0:
        return 0
    return max(50, min(5000, value))


def _mcp_notification_wait_duplicate_window_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_WINDOW_SECONDS")
    if raw is None:
        return 90.0
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return 90.0
    return max(0.0, min(600.0, value))


def _mcp_notification_wait_effective_cap_ms(tool_name: str) -> tuple[int, bool]:
    cap_ms = _mcp_notification_wait_timeout_cap_ms()
    if cap_ms <= 0:
        return 0, False
    duplicate_cap_ms = _mcp_notification_wait_duplicate_cap_ms()
    window = _mcp_notification_wait_duplicate_window_seconds()
    if duplicate_cap_ms <= 0 or window <= 0:
        return cap_ms, False
    key = str(tool_name or "").strip().lower()
    now = time.time()
    duplicate = False
    with _MCP_NOTIFICATION_WAIT_RECENT_LOCK:
        stale = [item_key for item_key, seen_at in _MCP_NOTIFICATION_WAIT_RECENT.items() if now - seen_at > window]
        for item_key in stale:
            _MCP_NOTIFICATION_WAIT_RECENT.pop(item_key, None)
        previous = _MCP_NOTIFICATION_WAIT_RECENT.get(key)
        duplicate = previous is not None and now - previous <= window
        _MCP_NOTIFICATION_WAIT_RECENT[key] = now
    if duplicate:
        return min(cap_ms, duplicate_cap_ms), True
    return cap_ms, False


def cap_mcp_notification_wait_tool_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    if not _is_mcp_notification_wait_tool(tool_name):
        return tool_input
    cap_ms, duplicate = _mcp_notification_wait_effective_cap_ms(tool_name)
    if cap_ms <= 0:
        return tool_input
    fixed = dict(tool_input) if isinstance(tool_input, dict) else {}
    schema = _lookup_tool_schema(tool_name) or {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    changed: list[str] = []

    def set_if_lower(key: str, value: int | float) -> None:
        old = fixed.get(key)
        try:
            numeric = float(old)
        except Exception:
            fixed[key] = int(value) if float(value).is_integer() else value
            changed.append(f"{key}=missing->{value:g}")
            return
        if numeric > float(value):
            fixed[key] = int(value) if float(value).is_integer() else value
            changed.append(f"{key}={numeric:g}->{value:g}")

    for key in list(fixed):
        key_l = str(key).strip().lower()
        if key_l in {"timeout_ms", "timeoutms", "wait_ms", "waitms", "max_wait_ms", "maxwaitms"}:
            set_if_lower(key, cap_ms)
        elif key_l in {"timeout", "wait_seconds", "wait_s", "max_wait_seconds"}:
            set_if_lower(key, max(0.1, cap_ms / 1000.0))

    if not changed:
        if "timeout_ms" in properties or "timeout_ms" in fixed or not properties:
            set_if_lower("timeout_ms", cap_ms)
        elif "timeout" in properties:
            set_if_lower("timeout", max(0.1, cap_ms / 1000.0))

    if changed:
        duplicate_label = " duplicate=true" if duplicate else ""
        router_log("INFO", f"mcp_notification_wait_timeout_capped tool={tool_name}{duplicate_label} {' '.join(changed)}")
    return fixed


def ui_text(key: str, lang: str | None = None) -> str:
    lang = lang or load_config().get("language", "en")
    return UI_TEXT.get(lang, UI_TEXT["en"]).get(key, UI_TEXT["en"].get(key, key))


DEFAULT_CONFIG: dict[str, Any] = {
    "current_provider": "nvidia-hosted",
    "language": "en",
    "migrations": {},
    "router_debug_external_access": False,
    "router_debug_external_access_confirmed": False,
    "router_debug_message_preview_chars": 0,
    "claude_code": {
        "compat_prompt_for_non_anthropic": True,
        "channels": [],
        "development_channels": False,
        "channel_delivery": "llm",
    },
    "cleanup": {
        "managed_services_on_launch": True,
    },
    "web_search": {
        "auto_for_non_native": True,
        "provider": "duckduckgo",
        "package": "ddg-mcp-search",
        "fetch_enabled": True,
        "fetch_package": "mcp-server-fetch",
        "fetch_ignore_robots_txt": False,
        "fetch_user_agent": "",
    },
    "providers": provider_default_configurations(),
}

def deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(a))
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def apply_config_migrations(cfg: dict[str, Any]) -> None:
    run_config_migrations(
        cfg,
        policy=ConfigMigrationPolicy(
            default_request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            kimi_k3_model=KIMI_K3_MODEL,
            opencode_provider_names=OPENCODE_PROVIDER_NAMES,
            is_qwen36_plus_model_id=is_qwen36_plus_model_id,
            normalize_channel_delivery=normalize_channel_delivery,
            normalize_model_id=normalize_model_id,
            nvidia_hosted_context_default=nvidia_hosted_context_default,
            positive_int=positive_int,
            strip_claude_context_suffix=strip_claude_context_suffix,
        ),
    )

_CONFIG_REPOSITORY: JsonConfigRepository | None = None


def _normalize_loaded_config(cfg: dict[str, Any]) -> None:
    cloud = cfg["providers"].get("ollama-cloud", {})
    local_key = cfg["providers"].get("ollama", {}).get("api_key", "")
    if not cloud.get("api_key") and local_key and local_key not in ("ollama", "dummy", "not-used"):
        cloud["api_key"] = local_key
    for provider_name, pcfg in cfg.get("providers", {}).items():
        if isinstance(pcfg, dict):
            if pcfg.get("current_model"):
                pcfg["current_model"] = normalize_model_id(provider_name, str(pcfg["current_model"]))
            if isinstance(pcfg.get("custom_models"), list):
                pcfg["custom_models"] = [
                    normalize_model_id(provider_name, str(model_id))
                    for model_id in pcfg["custom_models"]
                    if str(model_id).strip()
                ]


def config_repository() -> JsonConfigRepository:
    global _CONFIG_REPOSITORY
    if _CONFIG_REPOSITORY is None or _CONFIG_REPOSITORY.path != CONFIG_PATH:
        _CONFIG_REPOSITORY = JsonConfigRepository(
            path=CONFIG_PATH,
            defaults=DEFAULT_CONFIG,
            merge=deep_merge,
            migrate=apply_config_migrations,
            normalize=_normalize_loaded_config,
        )
    return _CONFIG_REPOSITORY


def json_artifact_repository(path: Path) -> SecureJsonRepository:
    return SecureJsonRepository(
        path=path,
        effects=SecureJsonEffects(log=router_log),
    )


def load_config() -> dict[str, Any]:
    return config_repository().load()


def invalidate_config_cache() -> None:
    config_repository().invalidate()


def save_config(cfg: dict[str, Any]) -> None:
    config_repository().save(cfg)


def clear_model_cache() -> None:
    invalidate_config_cache()
    try:
        CLAUDE_GATEWAY_CACHE.unlink()
    except FileNotFoundError:
        pass
    try:
        MODEL_LIST_CACHE_PATH.unlink()
    except FileNotFoundError:
        pass
    try:
        MODEL_REGISTRY_PATH.unlink()
    except FileNotFoundError:
        pass


def normalize_provider(name: str) -> str:
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key not in PROVIDER_ALIASES:
        raise SystemExit(f"Unknown provider: {name}\nKnown: {', '.join(PROVIDER_LABELS)}")
    return PROVIDER_ALIASES[key]


def normalize_provider_choice(name: str) -> str | None:
    raw = str(name or "").strip().lower().replace("_", "-").replace(" ", "-")
    key = raw.replace(":", "-")
    choices = {
        "anthropic-native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
        "claude-native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
        "native": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
        "claude-code": ANTHROPIC_NATIVE_PROVIDER_CHOICE,
        "anthropic-routed": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
        "anthropic-router": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
        "claude-routed": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
        "claude-router": ANTHROPIC_ROUTED_PROVIDER_CHOICE,
        "agy": AGY_NATIVE_PROVIDER_CHOICE,
        "agy-native": AGY_NATIVE_PROVIDER_CHOICE,
        "native-agy": AGY_NATIVE_PROVIDER_CHOICE,
        "antigravity": AGY_NATIVE_PROVIDER_CHOICE,
        "google-antigravity": AGY_NATIVE_PROVIDER_CHOICE,
        "agy-routed": AGY_ROUTED_PROVIDER_CHOICE,
        "agy-router": AGY_ROUTED_PROVIDER_CHOICE,
        "routed-agy": AGY_ROUTED_PROVIDER_CHOICE,
        "antigravity-routed": AGY_ROUTED_PROVIDER_CHOICE,
        "codex": CODEX_NATIVE_PROVIDER_CHOICE,
        "codex-native": CODEX_NATIVE_PROVIDER_CHOICE,
        "native-codex": CODEX_NATIVE_PROVIDER_CHOICE,
        "codex-routed": CODEX_ROUTED_PROVIDER_CHOICE,
        "codex-router": CODEX_ROUTED_PROVIDER_CHOICE,
        "routed-codex": CODEX_ROUTED_PROVIDER_CHOICE,
    }
    if raw in (
        ANTHROPIC_NATIVE_PROVIDER_CHOICE,
        ANTHROPIC_ROUTED_PROVIDER_CHOICE,
        AGY_NATIVE_PROVIDER_CHOICE,
        AGY_ROUTED_PROVIDER_CHOICE,
        CODEX_NATIVE_PROVIDER_CHOICE,
        CODEX_ROUTED_PROVIDER_CHOICE,
    ):
        return raw
    return choices.get(key)


def slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9_.-]+", "-", s.lower())).strip("-") or "model"


def model_sort_key(model_id: str) -> tuple[str, str]:
    return (model_id.casefold(), model_id)


def sorted_model_ids(ids: list[str]) -> list[str]:
    return sorted(ids, key=model_sort_key)


def unique_model_ids(provider: str, ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        mid = normalize_model_id(provider, str(raw))
        if not mid:
            continue
        key = mid.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(mid)
    return out


def normalize_model_id(provider: str, model_id: str) -> str:
    return PROVIDER_ADAPTERS.create(provider).normalize_model_id(model_id)


def strip_claude_context_suffix(model_id: str | None) -> str:
    text = str(model_id or "").strip()
    return re.sub(r"\[(?:1m)\]\s*$", "", text, flags=re.IGNORECASE)


def upstream_api_model_id(provider: str, model_id: str | None) -> str:
    """Return the provider's real API model code for a Claude Code-facing id."""
    return PROVIDER_ADAPTERS.create(provider).upstream_api_model_id(str(model_id or ""))


def alias_for(provider: str, model_id: str) -> str:
    if PROVIDER_ADAPTERS.create(provider).preserves_claude_model_alias(model_id):
        return model_id
    return f"ciel-runtime-{provider}-{slug(model_id)}"


def unslug_provider_alias(provider: str, alias: str, model_map: dict[str, str]) -> str | None:
    alias = strip_claude_context_suffix(alias)
    if alias in model_map:
        return model_map[alias]
    prefix = f"ciel-runtime-{provider}-"
    if alias.startswith(prefix):
        target_slug = alias[len(prefix):]
        for _, model_id in model_map.items():
            if slug(model_id) == target_slug:
                return model_id
    return None


def display_name(provider: str, model_id: str) -> str:
    label = PROVIDER_LABELS.get(provider, provider).replace("-", " ")
    cleaned = model_id
    if provider == "nvidia-hosted" and cleaned.startswith("claude-nvidia-"):
        cleaned = cleaned[len("claude-"):]
        return cleaned.replace("/", " ").replace("-", " ").replace("_", " ").title().replace("Nvidia", "Nvidia")
    cleaned = cleaned.replace("/", " ").replace("-", " ").replace("_", " ")
    return f"{label} {cleaned}".title().replace("Vllm", "vLLM").replace("Nvidia", "Nvidia")


def model_object(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> dict[str, Any]:
    model_id = normalize_model_id(provider, model_id)
    alias = alias_for(provider, model_id)
    obj = {
        "id": alias,
        "type": "model",
        "display_name": display_name(provider, model_id),
        "created_at": 1700000000,
        "object": "model",
        "created": 1700000000,
        "owned_by": f"ciel-runtime/{provider}",
        "ciel_runtime": {"provider": provider, "upstream_model": model_id},
    }
    if provider in OPENCODE_PROVIDER_NAMES:
        endpoint = opencode_endpoint_kind(provider, model_id, pcfg)
        obj["ciel_runtime"]["opencode_endpoint"] = endpoint
        obj["ciel_runtime"]["router_supported"] = opencode_model_supported_by_router(provider, model_id, pcfg)
    return obj


def join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        return base + path[3:]
    return base + path


def inbound_query_has_beta_flag(request_path: str) -> bool:
    """True when Claude Code's inbound request carried ?beta=true.

    Claude Code signals beta-feature opt-in (e.g. the context-1m long-context
    beta) via the ``beta=true`` query parameter on /v1/messages. The router
    parses only the path and would otherwise drop it, so callers re-attach the
    flag when forwarding upstream. Only the known ``beta`` flag is inspected;
    other query parameters are intentionally not forwarded.
    """
    query = urllib.parse.urlparse(request_path).query
    for value in urllib.parse.parse_qs(query).get("beta", []):
        if value.strip().lower() in ("true", "1"):
            return True
    return False


def upstream_messages_query(pcfg: dict[str, Any], request_path: str, provider: str | None = None) -> str:
    """Query string to append to the upstream /v1/messages URL.

    A configured ``force_query_string`` (operator override) wins, letting an
    operator inject an arbitrary raw query (e.g. "beta=true") from the options
    screen. Otherwise the inbound Claude Code ``beta=true`` flag is propagated
    only for the Anthropic provider. Non-Anthropic providers default to an empty
    query string unless the operator explicitly configures one.
    """
    forced = str(pcfg.get("force_query_string") or "").strip().lstrip("?").strip()
    if forced:
        return forced
    provider_name = str(provider or pcfg.get("provider") or "").strip()
    provider_key = normalize_provider(provider_name) if provider_name else ""
    if provider_key:
        adapter = configured_provider_adapter(provider_key, pcfg)
        config = provider_contract_config(provider_key, pcfg)
    else:
        adapter = None
        config = None
    if (
        adapter is not None
        and config is not None
        and adapter.propagates_inbound_beta_query(config)
        and inbound_query_has_beta_flag(request_path)
    ):
        return "beta=true"
    return ""


def upstream_query_string_status(provider: str, pcfg: dict[str, Any]) -> str:
    forced = str(pcfg.get("force_query_string") or "").strip()
    if forced:
        return forced
    provider_key = normalize_provider(str(provider))
    adapter = configured_provider_adapter(provider_key, pcfg)
    if adapter.propagates_inbound_beta_query(provider_contract_config(provider_key, pcfg)):
        return "auto (beta=true when routed)"
    return "empty"


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("'\"")
    return env


def meaningful_key_value(value: Any) -> bool:
    return project_meaningful_key_value(value)


def api_key_clear_requested(value: Any) -> bool:
    return project_api_key_clear_requested(value)


def parse_api_key_list(value: Any) -> list[str]:
    """Compatibility export for credential parsing."""

    return project_parse_api_key_list(value)


def provider_config_api_keys(provider: str, pcfg: dict[str, Any]) -> list[str]:
    supplemental = nvidia_api_key() if provider == "nvidia-hosted" else ""
    return project_provider_config_api_keys(pcfg, supplemental)


def provider_contract_config(provider: str, pcfg: dict[str, Any]) -> ProviderConfig:
    """Translate legacy configuration into the provider-owned contract."""

    return project_provider_contract_config(provider, pcfg, provider_config_api_keys(provider, pcfg))


def configured_provider_adapter(provider: str, pcfg: dict[str, Any]):
    return PROVIDER_ADAPTERS.create(provider, base_url=str(pcfg.get("base_url") or ""))


def provider_endpoint(provider: str, pcfg: dict[str, Any], operation: str) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    return join_url(provider_upstream_request_base(provider, pcfg), adapter.resolve_endpoint(operation, provider_contract_config(provider, pcfg)))


def provider_model_paths(provider: str, pcfg: dict[str, Any]) -> tuple[str, ...]:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.model_paths(provider_contract_config(provider, pcfg))


def provider_request_policy(provider: str, pcfg: dict[str, Any]):
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.request_policy(provider_contract_config(provider, pcfg))


def provider_model_catalog_policy(provider: str, pcfg: dict[str, Any]):
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.model_catalog_policy(provider_contract_config(provider, pcfg))


def select_provider_protocol(
    provider: str,
    pcfg: dict[str, Any],
    operation: MessageProtocol,
    model: str | None = None,
) -> MessageProtocol:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.select_protocol(operation, provider_contract_config(provider, pcfg), model)


def apply_provider_adapter_request_policy(
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    adapter = configured_provider_adapter(provider, pcfg)
    normalized = adapter.normalize_request_options(provider_contract_config(provider, pcfg), body)
    return dict(normalized)


def provider_has_api_key(provider: str, pcfg: dict[str, Any]) -> bool:
    return bool(provider_config_api_keys(provider, pcfg))


def provider_api_key_count(provider: str, pcfg: dict[str, Any]) -> int:
    return len(provider_config_api_keys(provider, pcfg))


def provider_primary_api_key(provider: str, pcfg: dict[str, Any]) -> str:
    keys = provider_config_api_keys(provider, pcfg)
    return keys[0] if keys else ""


def provider_api_key_rotation_name(provider: str, pcfg: dict[str, Any]) -> str:
    return f"{provider}:{str(pcfg.get('base_url') or default_base_url(provider)).rstrip('/')}"


def select_provider_api_key(provider: str, pcfg: dict[str, Any], *, rotate: bool = True) -> str:
    return choose_provider_api_key(
        provider, pcfg, rotate=rotate,
        services=ProviderKeyServices(
            _API_KEY_ROTATION_CURSOR=_API_KEY_ROTATION_CURSOR,
            _API_KEY_ROTATION_LOCK=_API_KEY_ROTATION_LOCK,
            api_key_cooldown_until=api_key_cooldown_until,
            provider_api_key_rotation_name=provider_api_key_rotation_name,
            provider_config_api_keys=provider_config_api_keys,
            router_log=router_log
        ),
    )


def env_bool(value: str | None, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    text = value.strip().lower()
    if text in ("1", "true", "yes", "on", "y"):
        return True
    if text in ("0", "false", "no", "off", "n"):
        return False
    return default


def load_dotenv_into_environ(path: Path, *, override: bool = True) -> None:
    for key, value in read_env_file(path).items():
        if override or key not in os.environ:
            os.environ[key] = value


def executable_candidates(name: str) -> list[str]:
    return ExecutableDiscovery.candidates(name)


def executable_discovery() -> ExecutableDiscovery:
    return ExecutableDiscovery(
        HOME,
        Path(__file__),
        platform_path,
        ciel_runtime_user_bin_dir,
        agy_user_bin_dir,
    )


def executable_extra_dirs() -> list[Path]:
    return executable_discovery().extra_dirs()


def find_executable(name: str) -> str | None:
    return executable_discovery().find(name)


def resolve_executable_for_subprocess(command: str) -> str:
    return executable_discovery().resolve(command)


def resolve_mcp_server_process(command: str, args: list[str]) -> tuple[str, list[str]]:
    return executable_discovery().resolve_mcp_process(command, args, find_executable)


def shell_command_string(args: list[str]) -> str:
    return ExecutableDiscovery.shell_command(args)


def find_tool_guard_script() -> Path | None:
    return executable_discovery().find_tool_guard(find_executable)


def ciel_runtime_tool_guard_command() -> str | None:
    script = find_tool_guard_script()
    if script is None:
        return None
    if script.suffix == ".py":
        return shell_command_string([sys.executable, str(script)])
    return shell_command_string([str(script)])


def install_legacy_tool_guard_compat_shim() -> None:
    """Keep already-running pre-rename Claude sessions from failing old hooks."""
    try:
        package_root = Path(__file__).resolve().parent
        if package_root.name != "ciel-runtime" or package_root.parent.name != "@oneciel-ai":
            return
        target = find_tool_guard_script()
        if target is None or not target.exists():
            return
        legacy_guard = package_root.parent / "claude-any" / "claude-any-tool-guard.py"
        if legacy_guard.exists() and not legacy_guard.is_symlink():
            return
        legacy_guard.parent.mkdir(parents=True, exist_ok=True)
        if legacy_guard.is_symlink() or legacy_guard.exists():
            legacy_guard.unlink()
        try:
            legacy_guard.symlink_to(target.resolve())
        except Exception:
            wrapper = (
                "#!/usr/bin/env python3\n"
                "import runpy\n"
                f"runpy.run_path({json.dumps(str(target.resolve()))}, run_name='__main__')\n"
            )
            legacy_guard.write_text(wrapper, encoding="utf-8")
            try:
                os.chmod(legacy_guard, 0o755)
            except Exception:
                pass
    except Exception as exc:
        router_log("WARN", f"legacy_tool_guard_compat_shim_failed error={type(exc).__name__}: {exc}")


TOOL_GUARD_EVENTS_WITH_TOOL_MATCHER: tuple[str, ...] = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
)

TOOL_GUARD_EVENTS_WITHOUT_MATCHER: tuple[str, ...] = (
    "PostToolBatch",
    "SessionStart",
    "SessionEnd",
    "Setup",
    "UserPromptSubmit",
    "UserPromptExpansion",
    "Stop",
    "StopFailure",
    "InstructionsLoaded",
    "ConfigChange",
    "CwdChanged",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TeammateIdle",
    "TaskCreated",
    "TaskCompleted",
    "PreCompact",
    "PostCompact",
    "WorktreeCreate",
    "WorktreeRemove",
    "Elicitation",
    "ElicitationResult",
)


def install_tool_guard_hooks() -> None:
    install_tool_guard_hook_settings(
        ciel_runtime_tool_guard_command(),
        ToolGuardHookPolicy(
            events_with_matcher=TOOL_GUARD_EVENTS_WITH_TOOL_MATCHER,
            events_without_matcher=TOOL_GUARD_EVENTS_WITHOUT_MATCHER,
        ),
        ToolGuardHookServices(
            repository=JsonSettingsRepository(
                path=CLAUDE_SETTINGS_PATH,
                effects=SettingsFileEffects(log=router_log),
            ),
            install_legacy_shim=install_legacy_tool_guard_compat_shim,
            warn=lambda message: print(f"Ciel Runtime warning: {message}", flush=True),
        ),
    )



def install_ciel_runtime_statusline() -> None:
    install_statusline_settings(
        CIEL_RUNTIME_STATUSLINE_PATH,
        STATUSLINE_SCRIPT,
        sys.executable,
        StatusLineServices(
            repository=JsonSettingsRepository(
                path=CLAUDE_SETTINGS_PATH,
                effects=SettingsFileEffects(log=router_log),
            ),
            warn=lambda message: print(f"Ciel Runtime warning: {message}", flush=True),
        ),
    )






VERSION_SLASH_COMMAND = """---
description: Show ciel-runtime version
argument-hint: [ignored]
---

CIEL_RUNTIME_VERSION_STATUS

Show the running ciel-runtime version for this session. This command is handled locally by the ciel-runtime router and must not be forwarded upstream.
"""







CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS = (
    "CIEL_RUNTIME_ADVISOR_CALL",
    "Run the selected ciel-runtime Advisor Model",
    "Ciel Runtime Advisor is unavailable in direct Claude Native mode",
)
CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS = (
    "CIEL_RUNTIME_ROUTER_DEBUG_ACCESS",
    "Toggle ciel-runtime router external debug access",
    "ciel-runtime router debug controls are unavailable in direct Claude Native mode",
)
CIEL_RUNTIME_VERSION_COMMAND_MARKERS = (
    "CIEL_RUNTIME_VERSION_STATUS",
    "Show ciel-runtime version",
)
CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS = (
    "CIEL_RUNTIME_LIVE_LLM_OPTIONS",
    "Show or change ciel-runtime live LLM options",
    "Restore ciel-runtime live LLM options",
)
CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS = (
    "CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG",
    "Discard pending ciel-runtime external channel backlog",
)
CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS = (
    "CIEL_RUNTIME_LIVE_API_KEYS",
    "Set/show ciel-runtime live API key(s)",
)
CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS = (
    "CIEL_RUNTIME_IMPORT_SESSION",
    "Import a Claude/Codex session transcript",
)
LEGACY_MARKER_PREFIX = "CLAUDE" + "_ANY"
LEGACY_ADVISOR_CALL_MARKER = LEGACY_MARKER_PREFIX + "_ADVISOR_CALL"
LEGACY_ROUTER_DEBUG_ACCESS_MARKER = LEGACY_MARKER_PREFIX + "_ROUTER_DEBUG_ACCESS"
LEGACY_LIVE_LLM_OPTIONS_MARKER = LEGACY_MARKER_PREFIX + "_LIVE_LLM_OPTIONS"
LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER = LEGACY_MARKER_PREFIX + "_CHANNEL_CLEAR_BACKLOG"
LEGACY_LIVE_API_KEYS_MARKER = LEGACY_MARKER_PREFIX + "_LIVE_API_KEYS"
ADVISOR_REQUEST_MARKERS = ("CIEL_RUNTIME_ADVISOR_CALL", LEGACY_ADVISOR_CALL_MARKER)
ROUTER_DEBUG_REQUEST_MARKERS = ("CIEL_RUNTIME_ROUTER_DEBUG_ACCESS", LEGACY_ROUTER_DEBUG_ACCESS_MARKER)
VERSION_REQUEST_MARKERS = ("CIEL_RUNTIME_VERSION_STATUS",)
LIVE_LLM_OPTIONS_REQUEST_MARKERS = ("CIEL_RUNTIME_LIVE_LLM_OPTIONS", LEGACY_LIVE_LLM_OPTIONS_MARKER)
CHANNEL_CLEAR_REQUEST_MARKERS = ("CIEL_RUNTIME_CHANNEL_CLEAR_BACKLOG", LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER)
LIVE_API_KEYS_REQUEST_MARKERS = ("CIEL_RUNTIME_LIVE_API_KEYS", LEGACY_LIVE_API_KEYS_MARKER)
IMPORT_SESSION_REQUEST_MARKERS = ("CIEL_RUNTIME_IMPORT_SESSION",)

CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS = (*CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS, LEGACY_ADVISOR_CALL_MARKER)
CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS = (*CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS, LEGACY_ROUTER_DEBUG_ACCESS_MARKER)
CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS = (*CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS, LEGACY_LIVE_LLM_OPTIONS_MARKER)
CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS = (*CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS, LEGACY_CHANNEL_CLEAR_BACKLOG_MARKER)
CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS = (*CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS, LEGACY_LIVE_API_KEYS_MARKER)


def command_file_is_ciel_runtime_owned(path: Path, markers: tuple[str, ...]) -> bool:
    return is_owned_command_file(path, markers)


def _command_asset_installer(directory: Path) -> CommandAssetInstaller:
    return CommandAssetInstaller(
        directory,
        lambda message: print(f"Ciel Runtime warning: {message}", flush=True),
    )


def remove_ciel_runtime_advisor_command() -> None:
    """Remove the ciel-runtime-owned /advisor command so Claude Code's built-in
    /advisor (the standard flow for the anthropic provider) surfaces."""
    _command_asset_installer(CLAUDE_COMMANDS_DIR).remove_one(
        "advisor.md", CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS
    )


def codex_prompts_dir(env: dict[str, str] | None = None) -> Path:
    raw_home = (env or os.environ).get("CODEX_HOME")
    home = Path(raw_home).expanduser() if raw_home else HOME / ".codex"
    return home / CODEX_PROMPTS_DIR_NAME


def install_ciel_runtime_codex_prompts(env: dict[str, str] | None = None) -> None:
    _command_asset_installer(codex_prompts_dir(env)).install_one(
        "ImportSession.md",
        CommandAsset(IMPORT_SESSION_SLASH_COMMAND, CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS),
    )


def disable_ciel_runtime_codex_prompts_for_native(env: dict[str, str] | None = None) -> None:
    _command_asset_installer(codex_prompts_dir(env)).remove_one(
        "ImportSession.md", CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS
    )


def _ciel_runtime_command_assets(include_advisor: bool = True) -> dict[str, CommandAsset]:
    assets = {
        "router-debug.md": CommandAsset(ROUTER_DEBUG_SLASH_COMMAND, CIEL_RUNTIME_ROUTER_DEBUG_COMMAND_MARKERS),
        "ciel-version.md": CommandAsset(VERSION_SLASH_COMMAND, CIEL_RUNTIME_VERSION_COMMAND_MARKERS),
        "llm.md": CommandAsset(LLM_SLIDER_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS),
        "llm-options.md": CommandAsset(LLM_OPTIONS_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS),
        "llm-restore.md": CommandAsset(LLM_RESTORE_SLASH_COMMAND, CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS),
        "channel-clear.md": CommandAsset(CHANNEL_CLEAR_SLASH_COMMAND, CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS),
        "api-key.md": CommandAsset(API_KEYS_SLASH_COMMAND, CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS),
        "api-keys.md": CommandAsset(API_KEYS_SLASH_COMMAND, CIEL_RUNTIME_API_KEYS_COMMAND_MARKERS),
        "ImportSession.md": CommandAsset(IMPORT_SESSION_SLASH_COMMAND, CIEL_RUNTIME_IMPORT_SESSION_COMMAND_MARKERS),
    }
    if include_advisor:
        assets["advisor.md"] = CommandAsset(ADVISOR_SLASH_COMMAND, CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS)
    return assets


def install_ciel_runtime_slash_commands(include_advisor: bool = True) -> None:
    if not include_advisor:
        remove_ciel_runtime_advisor_command()
    _command_asset_installer(CLAUDE_COMMANDS_DIR).install_all(
        _ciel_runtime_command_assets(include_advisor),
        stale_glob="llm-*.md",
        stale_markers=CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
    )


def disable_ciel_runtime_slash_commands_for_native() -> None:
    _command_asset_installer(CLAUDE_COMMANDS_DIR).remove_all(
        _ciel_runtime_command_assets(),
        stale_glob="llm-*.md",
        stale_markers=CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
    )


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
    provider: str | None = None,
    pcfg: dict[str, Any] | None = None,
) -> Any:
    req = urllib.request.Request(url, headers=with_upstream_user_agent(headers))
    with provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg) as r:
        return json.loads(r.read().decode("utf-8"))


def log_level_repository() -> LogLevelRepository:
    return LogLevelRepository(CONFIG_DIR, LOG_LEVEL_PATH, _LOG_LEVEL_CACHE, LOG_LEVEL_DEFAULT, os.environ)


def current_log_level() -> int:
    return log_level_repository().current()


def reset_log_level_cache() -> None:
    log_level_repository().reset_cache()


def log_level_name(value: int | None = None) -> str:
    return log_level_repository().name(value)


def log_level_source() -> str:
    return log_level_repository().source()


def log_level_status() -> str:
    return log_level_repository().status()


def normalize_log_level(value: str) -> str | None:
    return normalize_runtime_log_level(value)


def set_log_level_config(value: str) -> list[str]:
    return log_level_repository().set(value)


def router_log(level: str, message: str) -> None:
    """Append a line to router.log if the active level allows it.
    Rotates router.log when it exceeds ROUTER_LOG_MAX_BYTES."""
    RouterFileLogger(CONFIG_DIR, LOG_PATH, ROUTER_LOG_MAX_BYTES, log_level_repository()).write(
        level, message
    )


def resolve_blocked_tools(provider: str, pcfg: dict[str, Any]) -> set[str]:
    """Return the set of tool names to strip from upstream requests.
    `pcfg['blocked_tools']` overrides: None/missing => default list, False/[] => disable, list => explicit set."""
    if provider == "anthropic":
        return set()
    override = pcfg.get("blocked_tools", None)
    if override is False:
        return set()
    if isinstance(override, list):
        return {str(name).strip() for name in override if str(name).strip()}
    return set(DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC)


def forced_tool_choice_name(body: dict[str, Any]) -> str | None:
    tool_choice = body.get("tool_choice") if isinstance(body.get("tool_choice"), dict) else None
    if not tool_choice:
        return None
    if tool_choice.get("type") != "tool":
        return None
    name = tool_choice.get("name")
    return name if isinstance(name, str) and name else None


def tool_names_in_body(body: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    tools = body.get("tools")
    if not isinstance(tools, list):
        return names
    for tool in tools:
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            names.add(tool["name"])
    return names


def tool_schema_in_body(body: dict[str, Any], tool_name: str) -> dict[str, Any] | None:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("name") or "") != tool_name:
            continue
        schema = tool.get("input_schema")
        return schema if isinstance(schema, dict) else None
    return None


def _match_available_tool_name(name: str, available: set[str]) -> str | None:
    dialect = TOOL_DIALECTS.create("claude", available_tools=available)
    normalized = dialect.normalize_tool_name(name)
    return normalized if normalized in available else None


def _mcp_tool_name_server_normalized_key(name: str) -> tuple[str, str] | None:
    """Return a safe comparison key for MCP tool names.

    Some non-native models rewrite the MCP server segment, e.g.
    ``mcp__ai-net-http__get_messages`` becomes
    ``mcp__ai-net_http__get_messages``. Only the server segment is normalized;
    the tool name segment must still match exactly case-insensitively.
    """
    return mcp_server_normalized_key(name)


def resolve_emitted_tool_name(raw_name: str, source_body: dict[str, Any] | None) -> str:
    available = tool_names_in_body(source_body or {}) if isinstance(source_body, dict) else set()
    return _match_available_tool_name(raw_name, available) or _fuzzy_match_tool_name(raw_name) or raw_name


ANTHROPIC_PASSTHROUGH_TOOL_INPUT_REPAIR_TOOLS = {"AskUserQuestion"}


def should_repair_anthropic_passthrough_tool_input(
    provider: str,
    raw_name: str,
    source_body: dict[str, Any] | None,
) -> bool:
    if provider != "anthropic":
        return False
    matched_name = resolve_emitted_tool_name(raw_name, source_body)
    return matched_name in ANTHROPIC_PASSTHROUGH_TOOL_INPUT_REPAIR_TOOLS


def synthetic_tool_use_response(model: str, tool_name: str, tool_input: dict[str, Any] | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": f"msg_ciel_runtime_tool_{now}",
        "type": "message",
        "role": "assistant",
        "model": model or "ciel-runtime-router",
        "content": [
            {
                "type": "tool_use",
                "id": f"toolu_ciel_runtime_{tool_name}_{now}",
                "name": tool_name,
                "input": tool_input or {},
            }
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def has_tool(body: dict[str, Any], name: str) -> bool:
    return name in tool_names_in_body(body)


ULTRACODE_ON_RE = re.compile(r"\bUltracode\s+is\s+(?:still\s+)?on\b", re.IGNORECASE)
ULTRACODE_OFF_RE = re.compile(r"\bUltracode\s+is\s+off\b", re.IGNORECASE)
ULTRACODE_STATE_RE = re.compile(r"\bUltracode\s+is\s+(?:still\s+)?(on|off)\b", re.IGNORECASE)


def body_ultracode_runtime_enabled(body: dict[str, Any]) -> bool:
    """Infer Claude Code's per-session ultracode runtime state from the prompt.

    `/effort ultracode` is session-scoped in Claude Code and is not persisted in
    ciel-runtime provider config. Claude Code advertises that runtime state via
    system reminder text, so the router must infer it from the current request
    body rather than from config alone.
    """
    enabled = False
    system_text = anthropic_content_to_text(body.get("system"))
    for match in ULTRACODE_STATE_RE.finditer(system_text):
        enabled = match.group(1).lower() == "on"
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        text = anthropic_content_to_text(message.get("content"))
        for match in ULTRACODE_STATE_RE.finditer(text):
            enabled = match.group(1).lower() == "on"
    return enabled


def ultracode_workflow_preferred(body: dict[str, Any]) -> bool:
    return body_ultracode_runtime_enabled(body) and has_tool(body, "Workflow")


def _message_content_blocks(message: dict[str, Any]) -> list[Any]:
    return project_message_content_blocks(message)


def anthropic_thinking_requested(body: dict[str, Any]) -> bool:
    return project_anthropic_thinking_requested(body)


def anthropic_thinking_block_count(body: dict[str, Any]) -> int:
    return project_anthropic_thinking_block_count(body)


def anthropic_tool_continuation_block_count(body: dict[str, Any]) -> int:
    return project_anthropic_tool_continuation_block_count(body)


def anthropic_assistant_history_count(body: dict[str, Any]) -> int:
    return project_anthropic_assistant_history_count(body)


def strip_anthropic_thinking_blocks_from_messages(body: dict[str, Any]) -> dict[str, Any]:
    return project_strip_thinking_blocks(body)


def has_ciel_runtime_synthetic_tool_use(body: dict[str, Any]) -> bool:
    return project_has_synthetic_tool_use(body)


def should_defer_forced_tool_choice_for_thinking(provider: str, pcfg: dict[str, Any], body: dict[str, Any], name: str | None) -> bool:
    if name not in PLAN_MODE_SELF_TOOLS:
        return False
    return False


def preserves_anthropic_thinking_contract(provider: str, pcfg: dict[str, Any]) -> bool:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.preserves_anthropic_thinking(provider_contract_config(provider, pcfg))


def anthropic_thinking_policy() -> AnthropicThinkingPolicy:
    """Compose protocol policy ports late to preserve facade monkeypatch support."""
    return AnthropicThinkingPolicy(
        ThinkingPolicyPorts(
            preserves_contract=preserves_anthropic_thinking_contract,
            reasoning_passback_enabled=openai_chat_reasoning_passback_enabled_for_body,
            suggestion_mode=latest_user_is_claude_code_suggestion_mode,
            log=router_log,
        ),
        SUPPRESSED_THINKING_REPOSITORY,
    )


def should_normalize_anthropic_stream_tool_use(provider: str, pcfg: dict[str, Any]) -> bool:
    configured = pcfg.get("normalize_anthropic_tool_use")
    if configured is not None:
        return bool(configured)
    return provider != "anthropic" and not preserves_anthropic_thinking_contract(provider, pcfg)


def normalize_thinking_for_non_anthropic_provider(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return anthropic_thinking_policy().normalize_request(provider, pcfg, body)


def normalize_thinking_for_non_anthropic_native_provider(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return normalize_thinking_for_non_anthropic_provider(provider, pcfg, body)


def provider_supports_tool_choice(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    model_hint = strip_claude_context_suffix(str(body.get("model") or pcfg.get("current_model") or "")).lower()
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.supports_tool_choice(provider_contract_config(provider, pcfg), model_hint)


def provider_tool_choice_status(provider: str, pcfg: dict[str, Any]) -> str:
    configured = pcfg.get("supports_tool_choice")
    if configured is not None:
        return "on" if bool(configured) else "off"
    model = current_upstream_model_id(provider, pcfg) if provider in PROVIDER_LABELS else str(pcfg.get("current_model") or "")
    default = provider_supports_tool_choice(provider, pcfg, {"model": model})
    return f"auto ({'on' if default else 'off'})"


def normalize_tool_choice_for_provider(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return project_normalize_tool_choice(
        provider,
        pcfg,
        body,
        ToolChoicePorts(
            normalize=lambda current_provider, config, request, choice: configured_provider_adapter(
                current_provider, config
            ).normalize_tool_choice(
                provider_contract_config(current_provider, config),
                str(request.get("model") or config.get("current_model") or ""),
                choice,
            ),
            supports=provider_supports_tool_choice,
            log=router_log,
        ),
    )


def normalize_response_thinking_for_non_anthropic_provider(provider: str, pcfg: dict[str, Any], message: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    return anthropic_thinking_policy().normalize_response(provider, pcfg, message, model)


def clear_suppressed_thinking_passback_cache() -> None:
    SUPPRESSED_THINKING_REPOSITORY.clear()


def _copy_thinking_blocks(blocks: Any) -> list[dict[str, Any]]:
    return project_copy_thinking_blocks(blocks)


def remember_suppressed_thinking_passback(provider: str, model: str, blocks: list[Any]) -> None:
    anthropic_thinking_policy().remember(provider, model, blocks)


def rehydrate_suppressed_thinking_passback(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return anthropic_thinking_policy().rehydrate(provider, pcfg, body)


def conversation_turn_policy() -> ConversationTurnPolicy:
    return ConversationTurnPolicy(
        ConversationTurnPorts(
            content_blocks=_message_content_blocks,
            lookup_tool_schema=_lookup_tool_schema,
            tool_schema=tool_schema_in_body,
            log=router_log,
            has_tool=has_tool,
            ultracode_preferred=ultracode_workflow_preferred,
            content_to_text=anthropic_content_to_text,
        )
    )


def plan_mode_active(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().plan_mode_active(body)


def channel_llm_wake_text(text: str) -> bool:
    return conversation_turn_policy().channel_llm_wake_text(text)


def channel_llm_wake_request(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().channel_llm_wake_request(body)


def body_without_channel_llm_wake_prompt(body: dict[str, Any]) -> dict[str, Any]:
    return conversation_turn_policy().body_without_channel_llm_wake_prompt(body)


def has_plan_mode_exit(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().has_plan_mode_exit(body)


def allowed_prompt_tools_for_exit_plan_mode(body: dict[str, Any]) -> list[str]:
    return conversation_turn_policy().allowed_prompt_tools_for_exit_plan_mode(body)


def exit_plan_mode_default_prompt_for_tool(tool_name: str) -> str:
    return conversation_turn_policy().exit_plan_mode_default_prompt_for_tool(tool_name)


def backfill_exit_plan_mode_allowed_prompts(body: dict[str, Any], tool_input: dict[str, Any]) -> dict[str, Any]:
    return conversation_turn_policy().backfill_exit_plan_mode_allowed_prompts(body, tool_input)


def plan_mode_tool_name_for_emit(body: dict[str, Any], name: str, tool_input: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    return conversation_turn_policy().plan_mode_tool_name_for_emit(body, name, tool_input)


def is_guard_feedback_text(text: str) -> bool:
    return conversation_turn_policy().is_guard_feedback_text(text)


def strip_claude_code_system_reminders(text: str) -> str:
    return conversation_turn_policy().strip_claude_code_system_reminders(text)


def is_claude_code_suggestion_mode_text(text: str) -> bool:
    return conversation_turn_policy().is_claude_code_suggestion_mode_text(text)


def user_intent_text_from_message(message: dict[str, Any]) -> str:
    return conversation_turn_policy().user_intent_text_from_message(message)


def latest_user_text(body: dict[str, Any]) -> str:
    return conversation_turn_policy().latest_user_text(body)


def latest_user_intent_message_index(body: dict[str, Any]) -> int | None:
    return conversation_turn_policy().latest_user_intent_message_index(body)


def latest_user_is_claude_code_suggestion_mode(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().latest_user_is_claude_code_suggestion_mode(body)


def router_debug_message_preview_chars(cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or load_config()
    env = os.environ.get("CIEL_RUNTIME_ROUTER_MESSAGE_PREVIEW_CHARS", "").strip()
    if env:
        value = positive_int(env)
        return min(value or 0, 4000)
    value = positive_int(cfg.get("router_debug_message_preview_chars"))
    return min(value or 0, 4000)


def router_event_message_preview(body: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    limit = router_debug_message_preview_chars(cfg)
    if limit <= 0:
        return {}
    text = latest_user_text(body).strip()
    if not text:
        return {"message_preview_chars": limit, "message_preview": "", "message_preview_truncated": False}
    normalized = re.sub(r"\s+", " ", redact_sensitive_text(text))
    truncated = len(normalized) > limit
    return {
        "message_preview_chars": limit,
        "message_preview": normalized[:limit].rstrip(),
        "message_preview_truncated": truncated,
    }


def likely_implementation_planning_request(text: str) -> bool:
    return conversation_turn_policy().likely_implementation_planning_request(text)


def non_actionable_short_response(text: str) -> bool:
    return conversation_turn_policy().non_actionable_short_response(text)


def body_is_channel_prompt(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().body_is_channel_prompt(body)


def should_auto_enter_plan_mode(body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]) -> bool:
    return conversation_turn_policy().should_auto_enter_plan_mode(body, response_text, tool_calls)


def response_text_signals_plan_exit(text: str) -> bool:
    return conversation_turn_policy().response_text_signals_plan_exit(text)


def should_auto_exit_plan_mode(body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]) -> bool:
    return conversation_turn_policy().should_auto_exit_plan_mode(body, response_text, tool_calls)


def bash_command_looks_mutating(command: str) -> bool:
    return conversation_turn_policy().bash_command_looks_mutating(command)


def latest_user_tool_result_details(body: dict[str, Any]) -> list[dict[str, Any]]:
    return conversation_turn_policy().latest_user_tool_result_details(body)


def latest_tool_result_indicates_completed_work(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().latest_tool_result_indicates_completed_work(body)


def latest_user_tool_result_names(body: dict[str, Any]) -> list[str]:
    return conversation_turn_policy().latest_user_tool_result_names(body)


def latest_user_tool_result_text(body: dict[str, Any]) -> str:
    return conversation_turn_policy().latest_user_tool_result_text(body)


def synthetic_tasklist_tool_use_id(tool_id: str, name: str) -> bool:
    return conversation_turn_policy().synthetic_tasklist_tool_use_id(tool_id, name)


def recent_synthetic_tasklist_count(body: dict[str, Any], after_message_index: int | None = None) -> int:
    return conversation_turn_policy().recent_synthetic_tasklist_count(body, after_message_index)


def tasklist_result_has_active_work(text: str) -> bool:
    return conversation_turn_policy().tasklist_result_has_active_work(text)


def latest_tasklist_result_has_no_active_work(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().latest_tasklist_result_has_no_active_work(body)


def latest_assistant_text(body: dict[str, Any]) -> str:
    return conversation_turn_policy().latest_assistant_text(body)


def short_resume_prompt(text: str) -> bool:
    return conversation_turn_policy().short_resume_prompt(text)


def latest_user_looks_like_work_request(body: dict[str, Any]) -> bool:
    return conversation_turn_policy().latest_user_looks_like_work_request(body)


def response_asks_for_user_choice_or_permission(text: str) -> bool:
    return conversation_turn_policy().response_asks_for_user_choice_or_permission(text)


def should_auto_continue_choice_question_with_tasklist(body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]) -> bool:
    return conversation_turn_policy().should_auto_continue_choice_question_with_tasklist(body, response_text, tool_calls)


def should_synthesize_tasklist_for_provider(provider: str) -> bool:
    return conversation_turn_policy().should_synthesize_tasklist_for_provider(provider)


def should_keep_work_alive_with_tasklist(body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]) -> bool:
    return conversation_turn_policy().should_keep_work_alive_with_tasklist(body, response_text, tool_calls)


def should_recover_empty_end_turn_with_tasklist(body: dict[str, Any], response_text: str, tool_calls: list[dict[str, Any]]) -> bool:
    return conversation_turn_policy().should_recover_empty_end_turn_with_tasklist(body, response_text, tool_calls)


def empty_end_turn_notice() -> str:
    return conversation_turn_policy().empty_end_turn_notice()


def empty_end_turn_notice_for_body(body: dict[str, Any] | None) -> str:
    return conversation_turn_policy().empty_end_turn_notice_for_body(body)


def append_synthetic_tasklist_to_message(
    message: dict[str, Any],
    model: str,
    source_body: dict[str, Any],
    reason: str,
    provider: str = "",
) -> dict[str, Any]:
    if not should_synthesize_tasklist_for_provider(provider):
        return message
    content = message.get("content")
    if not isinstance(content, list):
        content = [{"type": "text", "text": anthropic_content_to_text(content)}] if content else []
    tool_calls = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]
    text = anthropic_content_to_text(content)
    if not should_auto_continue_choice_question_with_tasklist(source_body, text, tool_calls):
        return message
    now = int(time.time() * 1000)
    out = dict(message)
    out_content = list(content)
    out_content.append(
        {
            "type": "tool_use",
            "id": f"toolu_ciel_runtime_TaskList_{reason}_{now}",
            "name": "TaskList",
            "input": {},
        }
    )
    out["content"] = out_content
    out["stop_reason"] = "tool_use"
    out.setdefault("model", model or message.get("model") or "ciel-runtime-router")
    router_log("WARN", f"auto-synthesized TaskList after clarification question ({reason})")
    return out


def maybe_handle_plan_mode_tool_choice(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    """Support Claude Code's forced Plan-mode entry without relying on upstream model behavior."""
    if provider == "anthropic":
        return False
    name = forced_tool_choice_name(body)
    if name != "EnterPlanMode":
        return False
    if should_defer_forced_tool_choice_for_thinking(provider, pcfg, body, name):
        router_log(
            "INFO",
            f"deferred forced {name} tool_choice to native Anthropic-compatible upstream because thinking is enabled",
        )
        return False
    # Claude Code may force this tool when the user uses /plan or toggles Plan mode.
    # Returning a valid tool_use locally is more reliable than asking arbitrary
    # OpenAI/Ollama-compatible backends to select an internal Claude Code tool.
    available = tool_names_in_body(body)
    if available and name not in available:
        return False
    emit_name = name
    tool_input: dict[str, Any] = {}
    if plan_mode_active(body):
        router_log("WARN", f"ignored forced {name} tool_choice because plan mode is already active")
        return False
    else:
        router_log("INFO", f"synthesized {name} tool_use for {provider} forced tool_choice")
    write_json(handler, synthetic_tool_use_response(str(body.get("model") or ""), emit_name, tool_input))
    return True


def filter_blocked_tools(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Strip Claude-Code self-tools the upstream model shouldn't see (e.g. EnterPlanMode).
    Returns a (possibly new) body dict."""
    blocked = resolve_blocked_tools(provider, pcfg)
    dynamic_blocked: set[str] = set()
    if provider != "anthropic" and ultracode_workflow_preferred(body) and not plan_mode_active(body):
        dynamic_blocked.add("EnterPlanMode")
    blocked = set(blocked) | dynamic_blocked
    if not blocked:
        return body
    tools = body.get("tools")
    tool_choice = body.get("tool_choice") if isinstance(body.get("tool_choice"), dict) else None
    tool_choice_name = tool_choice.get("name") if tool_choice else None
    must_drop_tool_choice = isinstance(tool_choice_name, str) and tool_choice_name in blocked
    if not isinstance(tools, list) or not tools:
        if not must_drop_tool_choice:
            return body
        new_body = dict(body)
        new_body.pop("tool_choice", None)
        router_log("WARN", f"removed blocked tool_choice for {provider}: {tool_choice_name}")
        return new_body
    kept: list[Any] = []
    dropped: list[str] = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if isinstance(name, str) and name in blocked:
            dropped.append(name)
            continue
        kept.append(tool)
    if not dropped:
        if not must_drop_tool_choice:
            return body
        new_body = dict(body)
        new_body.pop("tool_choice", None)
        router_log("WARN", f"removed blocked tool_choice for {provider}: {tool_choice_name}")
        return new_body
    reason = " ultracode_workflow_preferred=true" if dynamic_blocked & set(dropped) else ""
    router_log("INFO", f"filtered upstream tools for {provider}: {', '.join(sorted(set(dropped)))}{reason}")
    new_body = dict(body)
    new_body["tools"] = kept
    if must_drop_tool_choice:
        new_body.pop("tool_choice", None)
        router_log("WARN", f"removed blocked tool_choice for {provider}: {tool_choice_name}")
    return new_body


def summarize_messages_for_trace(messages: Any, max_messages: int = 30) -> list[dict[str, Any]]:
    return project_messages_for_trace(
        messages,
        request_trace_projection(),
        max_messages=max_messages,
    )


def request_trace_projection() -> RequestTraceProjection:
    return RequestTraceProjection(
        content_to_text=anthropic_content_to_text,
        thinking_block_count=anthropic_thinking_block_count,
        tool_continuation_block_count=anthropic_tool_continuation_block_count,
    )


def request_trace_services() -> RequestTraceServices:
    return RequestTraceServices(
        policy=RequestTracePolicy(
            enabled=lambda: current_log_level() >= LOG_LEVELS["TRACE"],
            request_path=REQUEST_DUMP_PATH,
            response_path=RESPONSE_DUMP_PATH,
            request_max_bytes=REQUEST_DUMP_MAX_BYTES,
            response_max_bytes=RESPONSE_DUMP_MAX_BYTES,
            response_text_limit=RESPONSE_DUMP_TEXT_LIMIT,
        ),
        projection=request_trace_projection(),
        log=router_log,
    )


def dump_request_for_trace(provider: str, path: str, body: dict[str, Any]) -> None:
    """At TRACE level, append a redacted snapshot of an inbound /v1/messages body
    (tools list, system prompt summary, message/tool block summary) to requests.jsonl.
    Used to capture tool definitions Claude Code injects (e.g. EnterPlanMode)."""
    write_request_trace(provider, path, body, request_trace_services())


def dump_response_for_trace(provider: str, model: str, text_so_far: str, tool_calls: list[dict[str, Any]], stop_reason: str | None, input_tokens: int, output_tokens: int, last_chunk: dict[str, Any] | None = None) -> None:
    """At TRACE level, append a per-response summary to responses.jsonl.
    Used to confirm what GLM-5.1 (and other upstream models) actually sent
    when the Claude Code session appears to stall."""
    try:
        usage_event = UsageEvent(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        USAGE_EVENT_SINK.record(usage_event)
        EVENT_BUS.publish(
            level="info",
            category="usage.response",
            message="upstream token usage recorded",
            provider=provider,
            model=model,
            data={"input_tokens": max(0, input_tokens), "output_tokens": max(0, output_tokens)},
        )
    except Exception as exc:
        router_log("WARN", f"usage_event_record_failed error={type(exc).__name__}: {exc}")
    write_response_trace(
        provider,
        model,
        text_so_far,
        tool_calls,
        stop_reason,
        input_tokens,
        output_tokens,
        request_trace_services(),
        last_chunk=last_chunk,
    )


def sse_trace_enabled() -> bool:
    value = os.environ.get("CIEL_RUNTIME_SSE_TRACE", "").strip().lower()
    if value in {"1", "true", "yes", "on", "trace"}:
        return True
    return current_log_level() >= LOG_LEVELS["TRACE"]


def sse_trace_repository() -> SseTraceRepository:
    return SseTraceRepository(
        SseTraceConfig(
            CONFIG_DIR,
            SSE_LAST_PATH,
            SSE_TRACE_PATH,
            TOOL_CALL_LOG_PATH,
            SSE_TRACE_EVENT_LIMIT,
            SSE_TRACE_PAYLOAD_LIMIT,
            SSE_TRACE_MAX_BYTES,
        ),
        SseTracePorts(sse_trace_enabled, _truncate_for_dump, router_log),
    )


def _summarize_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return summarize_sse_payload(payload, _truncate_for_dump)


def make_outgoing_sse_trace(provider: str, model: str, source: str, source_body: dict[str, Any] | None = None) -> dict[str, Any]:
    return sse_trace_repository().begin(provider, model, source, source_body)


def record_outgoing_sse_event(trace: dict[str, Any] | None, event_name: str, payload: dict[str, Any]) -> None:
    sse_trace_repository().record(trace, event_name, payload)


def finish_outgoing_sse_trace(
    trace: dict[str, Any] | None,
    *,
    outcome: str,
    text_len: int = 0,
    tool_call_count: int = 0,
    chunks: int = 0,
    stop_reason: str | None = None,
    error: str | None = None,
) -> None:
    result: dict[str, Any] = {
        "outcome": outcome,
        "text_len": text_len,
        "tool_call_count": tool_call_count,
        "chunks": chunks,
        "stop_reason": stop_reason,
    }
    if error:
        result["error"] = error
    sse_trace_repository().finish(trace, **result)


def append_tool_call_log(event: str, payload: dict[str, Any]) -> None:
    sse_trace_repository().append_tool_call(event, payload)


def model_cache_key(provider: str, pcfg: dict[str, Any]) -> str:
    api_count = provider_api_key_count(provider, pcfg)
    api_state = "key" if api_count else "nokey"
    return json.dumps(
        {
            "provider": provider,
            "base_url": pcfg.get("base_url", ""),
            "model_api_base_url": pcfg.get("model_api_base_url", ""),
            "account_id": pcfg.get("account_id", ""),
            "api": api_state,
            "custom": pcfg.get("custom_models", []),
            "schema": 7,
        },
        sort_keys=True,
    )


def anthropic_model_family_from_id(model_id: str) -> str:
    return anthropic_model_policy.model_family(model_id)


def anthropic_model_limit_hints(model_id: str) -> dict[str, Any]:
    return anthropic_model_policy.limit_hints(model_id)


def anthropic_model_runtime_hints(model_id: str) -> dict[str, Any]:
    return anthropic_model_policy.runtime_hints(model_id)


CLAUDE_CODE_SUPPORTED_CAPABILITY_VALUES = anthropic_model_policy.SUPPORTED_CAPABILITIES


def normalize_claude_code_supported_capabilities(value: Any) -> list[str]:
    return anthropic_model_policy.normalize_capabilities(value)


def infer_claude_code_supported_capabilities_from_model(model_id: str) -> list[str]:
    return anthropic_model_policy.infer_capabilities(model_id, strip_claude_context_suffix)




def claude_code_supported_capabilities(provider: str, pcfg: dict[str, Any], model_id: str | None = None) -> list[str]:
    configured = pcfg.get("claude_code_supported_capabilities")
    caps = normalize_claude_code_supported_capabilities(configured)
    model = model_id or current_upstream_model_id(provider, pcfg)
    if not caps:
        caps = infer_claude_code_supported_capabilities_from_model(model)
    if provider == "kimi" and is_kimi_k3_model_id(model) and "max_effort" not in caps:
        caps.append("max_effort")
    return caps


def claude_code_capability_string(provider: str, pcfg: dict[str, Any], model_id: str | None = None) -> str:
    return ",".join(claude_code_supported_capabilities(provider, pcfg, model_id))


def claude_code_workflows_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    if parse_bool(pcfg.get("ultracode_enabled") if "ultracode_enabled" in pcfg else pcfg.get("ultracode"), False):
        return True
    if "workflows_enabled" in pcfg:
        return parse_bool(pcfg.get("workflows_enabled"), False)
    return parse_bool(pcfg.get("workflows"), False)


def claude_code_ultracode_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    if "ultracode_enabled" in pcfg:
        return parse_bool(pcfg.get("ultracode_enabled"), False)
    return parse_bool(pcfg.get("ultracode"), False)


def anthropic_recommended_preset_for_model(model_id: str) -> str:
    return anthropic_model_policy.recommended_preset(model_id)


def model_registry_recommendations(provider: str, models: list[str]) -> dict[str, Any]:
    return anthropic_model_policy.AnthropicModelRecommendations(
        unique_model_ids,
        llm_preset_timeout_ms,
        timeout_profile_idle_ms,
    ).build(provider, models)




def model_registry_repository() -> ModelRegistryRepository:
    return ModelRegistryRepository(
        paths=ModelRegistryPaths(CONFIG_DIR, MODEL_REGISTRY_PATH, MODEL_LIST_CACHE_PATH),
        policy=ModelRegistryPolicy(
            cache_key=model_cache_key,
            unique_ids=unique_model_ids,
            normalize_id=normalize_model_id,
            positive_int=positive_int,
            recommendations=model_registry_recommendations,
            log=router_log,
        ),
        ttl_seconds=MODEL_CACHE_TTL_SECONDS,
    )


def read_model_registry(
    provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS
) -> dict[str, Any] | None:
    return model_registry_repository().read_registry(provider, pcfg, max_age_seconds)


def read_model_registry_models(
    provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS
) -> list[str] | None:
    return model_registry_repository().read_registry_models(provider, pcfg, max_age_seconds)


def read_model_registry_info(
    provider: str, pcfg: dict[str, Any], max_age_seconds: float = MODEL_CACHE_TTL_SECONDS
) -> dict[str, dict[str, Any]]:
    return model_registry_repository().read_registry_info(provider, pcfg, max_age_seconds)


def write_model_registry(
    provider: str,
    pcfg: dict[str, Any],
    models: list[str],
    source: str = "provider",
    metadata: dict[str, Any] | None = None,
) -> None:
    model_registry_repository().write_registry(provider, pcfg, models, source, metadata)


def read_model_list_cache(provider: str, pcfg: dict[str, Any]) -> list[str] | None:
    return model_registry_repository().read_list_cache(provider, pcfg)


def read_model_info_cache(provider: str, pcfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return model_registry_repository().read_info_cache(provider, pcfg)


def write_model_list_cache(
    provider: str,
    pcfg: dict[str, Any],
    models: list[str],
    metadata: dict[str, Any] | None = None,
) -> None:
    model_registry_repository().write_list_cache(provider, pcfg, models, metadata)




def cached_or_configured_model_ids(provider: str, pcfg: dict[str, Any]) -> list[str]:
    ids = read_model_list_cache(provider, pcfg) or []
    if provider == "ollama-cloud":
        ids.extend(ollama_catalog_model_ids(provider))
    for mid in pcfg.get("custom_models", []) or []:
        mid = normalize_model_id(provider, mid)
        if mid and mid not in ids:
            ids.append(mid)
    cur = normalize_model_id(provider, pcfg.get("current_model") or "")
    if cur and cur not in ids and not cur.startswith(f"ciel-runtime-{provider}-"):
        ids.insert(0, cur)
    ids = unique_model_ids(provider, ids)
    if provider == "anthropic":
        return ids
    return sorted_model_ids(ids)


def ensure_model_cache_for_launch(provider: str, pcfg: dict[str, Any]) -> None:
    """Populate the model list before building Claude Code launch env.

    Claude Code consumes ANTHROPIC_DEFAULT_*_MODEL only at process start. If
    those values are computed before the provider model list is available,
    family defaults collapse to the current model and /model cannot switch
    families reliably inside that session.
    """
    if read_model_list_cache(provider, pcfg):
        return
    if read_model_registry_models(provider, pcfg, max_age_seconds=0):
        return
    try:
        ids = upstream_model_ids(provider, pcfg)
    except Exception as exc:
        router_log("WARN", f"launch_model_cache_refresh_failed provider={provider} error={type(exc).__name__}: {exc}")
        return
    if ids:
        router_log("INFO", f"launch_model_cache_ready provider={provider} count={len(ids)}")


def model_ids_from_response(data: Any) -> list[str]:
    ids: list[str] = []
    candidates: Any
    if isinstance(data, dict):
        candidates = data.get("data")
        if candidates is None:
            candidates = data.get("models")
        if candidates is None:
            candidates = data.get("model")
    else:
        candidates = data
    if isinstance(candidates, str):
        candidates = [candidates]
    if not isinstance(candidates, list):
        return ids
    for item in candidates:
        if isinstance(item, str):
            mid = item
        elif isinstance(item, dict):
            mid = item.get("id") or item.get("key") or item.get("name") or item.get("model")
        else:
            mid = None
        if mid and str(mid).strip():
            ids.append(str(mid).strip())
    return ids


def model_info_from_response(provider: str, data: Any) -> dict[str, dict[str, Any]]:
    adapter = PROVIDER_ADAPTERS.create(provider)
    return project_model_info(
        provider,
        data,
        ModelCatalogProjectionServices(
            normalize_model_id=normalize_model_id,
            model_context=model_context_field,
            positive_int=positive_int,
            project_metadata=adapter.project_model_metadata,
        ),
    )


def fireworks_account_id(pcfg: dict[str, Any]) -> str:
    configured = str(pcfg.get("account_id") or "").strip()
    if configured:
        return configured
    for value in (pcfg.get("current_model"), *(pcfg.get("custom_models", []) or [])):
        text = str(value or "")
        match = re.match(r"^accounts/([^/]+)/models/[^/]+$", text)
        if match:
            return match.group(1)
    return FIREWORKS_DEFAULT_ACCOUNT_ID


def fireworks_management_base_url(pcfg: dict[str, Any]) -> str:
    configured = str(pcfg.get("model_api_base_url") or "").strip().rstrip("/")
    base = str(pcfg.get("base_url") or FIREWORKS_INFERENCE_BASE_URL).strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if configured and (
        configured != FIREWORKS_API_BASE_URL
        or not (parsed.scheme and parsed.netloc)
        or parsed.netloc.endswith("fireworks.ai")
    ):
        return configured
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return configured or FIREWORKS_API_BASE_URL


def fetch_fireworks_model_ids(
    pcfg: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 8.0,
) -> tuple[list[str], dict[str, dict[str, Any]], str]:
    account_id = fireworks_account_id(pcfg)
    base = fireworks_management_base_url(pcfg)
    models: list[str] = []
    model_info: dict[str, dict[str, Any]] = {}
    page_token = ""
    source = f"fireworks:{account_id}"
    for _ in range(20):
        query = {"pageSize": "200"}
        if page_token:
            query["pageToken"] = page_token
        path = f"/v1/accounts/{urllib.parse.quote(account_id, safe='')}/models?{urllib.parse.urlencode(query)}"
        data = http_json(join_url(base, path), headers=headers, timeout=timeout, provider="fireworks", pcfg=pcfg)
        ids = [normalize_model_id("fireworks", mid) for mid in model_ids_from_response(data)]
        for mid in ids:
            if mid and mid not in models:
                models.append(mid)
        model_info.update(model_info_from_response("fireworks", data))
        if not isinstance(data, dict):
            break
        page_token = str(data.get("nextPageToken") or "").strip()
        if not page_token:
            break
    return models, model_info, source


ANTHROPIC_PUBLIC_MODEL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_.@:-])"
    r"(?:"
    r"claude-(?:fable|mythos)-\d+(?:-\d+)?(?:-\d{8})?|"
    r"claude-mythos-preview|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d+-\d{8}|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d{8}|"
    r"claude-(?:opus|sonnet|haiku)-\d+-\d+|"
    r"claude-(?:opus|sonnet|haiku)-\d+(?:-\d+)?-latest|"
    r"claude-\d+(?:-\d+){0,2}-(?:opus|sonnet|haiku)-(?:\d{8}|latest)"
    r")"
    r"(?![A-Za-z0-9_.@:-])"
)


def fetch_text_url(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers=with_upstream_user_agent())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(5_000_000).decode("utf-8", errors="replace")


def anthropic_model_ids_from_docs_text(text: str) -> list[str]:
    """Extract public Claude API model IDs from Anthropic's models overview page.

    Claude Native usually runs on Claude Code OAuth rather than an API key, so
    `/v1/models` is not available to ciel-runtime. The public docs are the only
    unauthenticated source for the current model picker seed.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for match in ANTHROPIC_PUBLIC_MODEL_ID_RE.finditer(html_lib.unescape(text or "")):
        mid = match.group(0)
        key = mid.casefold()
        if key in seen:
            continue
        seen.add(key)
        ids.append(mid)
    return ids


def filter_anthropic_default_model_ids(ids: list[str]) -> list[str]:
    """Keep only generally available current Claude models for the default picker.

    Anthropic's model overview page also mentions limited-access research models,
    cloud-provider IDs, and legacy/upgrade-path IDs. Those are useful reference
    text but bad defaults for Claude Code Native and routed launches because many
    users cannot select them. Custom model IDs still remain supported separately.
    """
    allowed = set(ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS)
    limited = set(ANTHROPIC_LIMITED_ACCESS_MODEL_IDS)
    out: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        mid = normalize_model_id("anthropic", raw)
        key = mid.casefold()
        if not mid or key in seen or mid in limited or mid not in allowed:
            continue
        out.append(mid)
        seen.add(key)
    return out


def fetch_anthropic_public_model_ids(timeout: float = 8.0) -> list[str]:
    ids: list[str] = []
    errors: list[str] = []
    for url in ANTHROPIC_MODEL_DOCS_URLS:
        try:
            ids.extend(anthropic_model_ids_from_docs_text(fetch_text_url(url, timeout=timeout)))
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    out = filter_anthropic_default_model_ids(unique_model_ids("anthropic", ids))
    if out:
        return out
    if errors:
        router_log("WARN", "anthropic model docs fetch failed: " + " ; ".join(errors))
    return list(ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS)


def opencode_zen_endpoint_kind(model_id: str) -> str:
    """Return the documented OpenCode Zen endpoint family for a model id."""
    model = strip_claude_context_suffix(model_id).strip().lower()
    if model.startswith("ciel-runtime-opencode-"):
        model = model[len("ciel-runtime-opencode-"):]
    if model.startswith("claude-") or model.startswith("qwen3."):
        return "anthropic-messages"
    if model.startswith("gpt-"):
        return "openai-responses"
    if model.startswith("gemini-"):
        return "google-generative"
    if model.startswith(("minimax-", "glm-", "kimi-", "grok-", "big-pickle", "deepseek-", "mimo-", "nemotron-", "north-")):
        return "openai-chat"
    return "anthropic-messages"


def opencode_zen_model_supported_by_router(model_id: str) -> bool:
    return opencode_zen_endpoint_kind(model_id) in ("anthropic-messages", "openai-chat")


def normalize_opencode_endpoint_kind(value: Any) -> str | None:
    key = str(value or "").strip().lower().replace("_", "-")
    return OPENCODE_ENDPOINT_ALIASES.get(key)


def opencode_endpoint_override(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> str | None:
    if not pcfg:
        return None
    overrides = pcfg.get("model_endpoints")
    if not isinstance(overrides, dict):
        return None
    normalized = normalize_model_id(provider, model_id)
    candidates = [
        model_id,
        normalized,
        strip_claude_context_suffix(model_id),
        alias_for(provider, normalized),
    ]
    for candidate in candidates:
        raw = overrides.get(candidate)
        endpoint = normalize_opencode_endpoint_kind(raw)
        if endpoint:
            return endpoint
    return None


def opencode_go_endpoint_kind(model_id: str) -> str:
    """Return the documented OpenCode Go endpoint family for a model id."""
    model = strip_claude_context_suffix(model_id).strip().lower()
    if model.startswith("ciel-runtime-opencode-go-"):
        model = model[len("ciel-runtime-opencode-go-"):]
    if model.startswith("qwen3.") or model.startswith("minimax-"):
        return "anthropic-messages"
    if model.startswith(("glm-", "kimi-", "deepseek-", "mimo-", "hy3-")):
        return "openai-chat"
    return "anthropic-messages"


def opencode_endpoint_kind(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> str:
    override = opencode_endpoint_override(provider, model_id, pcfg)
    if override:
        return override
    if provider == "opencode-go":
        return opencode_go_endpoint_kind(model_id)
    return opencode_zen_endpoint_kind(model_id)


def opencode_model_supported_by_router(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> bool:
    return opencode_endpoint_kind(provider, model_id, pcfg) in ("anthropic-messages", "openai-chat")


def opencode_endpoint_display(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> str:
    endpoint = opencode_endpoint_kind(provider, model_id, pcfg)
    labels = {
        "anthropic-messages": "messages",
        "openai-chat": "chat",
        "openai-responses": "responses",
        "google-generative": "gemini",
    }
    text = labels.get(endpoint, endpoint)
    if not opencode_model_supported_by_router(provider, model_id, pcfg):
        text += " unsupported"
    if opencode_endpoint_override(provider, model_id, pcfg):
        text += " override"
    return text


def nvidia_hosted_list_headers() -> dict[str, str]:
    headers = {"content-type": "application/json"}
    key = read_env_file(NCP_ENV).get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_API_KEY")
    if key:
        headers["authorization"] = f"Bearer {key}"
        headers["x-api-key"] = key
    return headers


def provider_model_list_headers(provider: str, pcfg: dict[str, Any]) -> dict[str, str]:
    headers = with_upstream_user_agent({"content-type": "application/json"})
    key = provider_primary_api_key(provider, pcfg)
    meaningful = str(key) if meaningful_key(str(key) if key is not None else None) else None
    adapter = configured_provider_adapter(provider, pcfg)
    headers.update(adapter.build_model_headers(provider_contract_config(provider, pcfg), meaningful))
    return headers


def fetch_anthropic_api_model_ids(
    pcfg: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 6.0,
) -> tuple[list[str], str]:
    base = provider_upstream_request_base("anthropic", pcfg)
    errors: list[str] = []
    for path in ("/v1/models", "/models"):
        try:
            data = http_json(join_url(base, path), headers=headers, timeout=timeout)
            ids = unique_model_ids("anthropic", model_ids_from_response(data))
            if ids:
                return ids, f"api:{path}"
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    if errors:
        router_log("DEBUG", "anthropic model API fetch failed: " + " ; ".join(errors))
    return [], ""


def post_json(
    url: str,
    body: Any,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    provider: str | None = None,
    pcfg: dict[str, Any] | None = None,
) -> Any:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=with_upstream_user_agent(headers), method="POST")
    with provider_urlopen(req, timeout=timeout, provider=provider, pcfg=pcfg) as r:
        return json.loads(r.read().decode("utf-8"))


def rate_limit_repository() -> RateLimitRepository:
    return RateLimitRepository(CONFIG_DIR, RATE_LIMIT_STATE_PATH, _RATE_LIMIT_LOCK, router_log)


def router_rate_limit_legacy_key(
    provider: str, pcfg: dict[str, Any], model: str | None
) -> str:
    return f"{provider}:{model or current_upstream_model_id(provider, pcfg)}"


def router_rate_limit_configured_rpm(provider: str, pcfg: dict[str, Any]) -> int | None:
    return rate_limit_policy.configured_rpm(pcfg, positive_int)


def router_rate_limit_rpm(provider: str, pcfg: dict[str, Any]) -> int | None:
    rpm = router_rate_limit_configured_rpm(provider, pcfg)
    return rpm if rpm and rpm > 0 else None


def router_rate_limit_key(provider: str, pcfg: dict[str, Any], model: str | None = None) -> str:
    # Provider/account limits such as NVIDIA NIM RPM apply across models.
    return f"{provider}:__global__"


def router_rate_limit_state_entry(provider: str, pcfg: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    return rate_limit_repository().entry(
        router_rate_limit_key(provider, pcfg, model),
        router_rate_limit_legacy_key(provider, pcfg, model),
    )


def router_rate_limit_effective_rpm(provider: str, pcfg: dict[str, Any], model: str | None = None) -> int | None:
    return rate_limit_repository().effective_rpm(
        router_rate_limit_key(provider, pcfg, model),
        router_rate_limit_legacy_key(provider, pcfg, model),
        router_rate_limit_configured_rpm(provider, pcfg),
    )


def router_rate_limit_capacity(rpm: int) -> int:
    return rate_limit_policy.capacity(rpm)


def router_rate_limit_recent(timestamps: Any, now: float, window: float, *, include_future: bool) -> list[float]:
    return rate_limit_policy.recent_timestamps(
        timestamps, now, window, include_future=include_future
    )


def router_rate_limit_usage(provider: str, pcfg: dict[str, Any], model: str | None = None) -> tuple[int, int | None]:
    return rate_limit_repository().usage(
        router_rate_limit_key(provider, pcfg, model),
        router_rate_limit_legacy_key(provider, pcfg, model),
        router_rate_limit_effective_rpm(provider, pcfg, model),
        router_rate_limit_recent,
    )


def record_router_rate_usage(provider: str, pcfg: dict[str, Any], model: str | None, rpm: int | None) -> tuple[int, int | None]:
    return rate_limit_repository().record_usage(
        router_rate_limit_key(provider, pcfg, model),
        router_rate_limit_legacy_key(provider, pcfg, model),
        rpm,
        router_rate_limit_recent,
    )


def parse_retry_after_seconds(value: str | None) -> float | None:
    return rate_limit_policy.retry_after_seconds(value)


def format_duration_seconds(seconds: float) -> str:
    return rate_limit_policy.format_duration(seconds)


def first_header(headers: Any, names: list[str]) -> str | None:
    return rate_limit_policy.first_header(headers, names)


def first_int_in_header(value: str | None) -> int | None:
    return rate_limit_policy.first_integer(value)


def rate_limit_reset_seconds(value: str | None) -> float | None:
    return rate_limit_policy.reset_seconds(value)


def learn_router_rate_limit_headers(provider: str, pcfg: dict[str, Any], model: str | None, headers: Any) -> None:
    return learn_rate_limit_headers(
        provider, pcfg, model, headers,
        services=RateLimitLearningServices(
            state_store=RateLimitStateStore(
                CONFIG_DIR=CONFIG_DIR,
                RATE_LIMIT_STATE_PATH=RATE_LIMIT_STATE_PATH,
                _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
                router_log=router_log,
            ),
            policy=RateLimitLearningPolicy(
                current_upstream_model_id=current_upstream_model_id,
                first_header=first_header,
                first_int_in_header=first_int_in_header,
                provider_api_key_count=provider_api_key_count,
                rate_limit_reset_seconds=rate_limit_reset_seconds,
                router_rate_limit_configured_rpm=router_rate_limit_configured_rpm,
                router_rate_limit_key=router_rate_limit_key,
                router_rate_limit_recent=router_rate_limit_recent,
            ),
        ),
    )


def register_router_rate_limit_backoff(provider: str, pcfg: dict[str, Any], model: str | None, retry_after: str | None = None) -> float:
    return register_rate_limit_backoff(
        provider, pcfg, model, retry_after,
        services=RateLimitBackoffServices(
            state_store=RateLimitStateStore(
                CONFIG_DIR=CONFIG_DIR,
                RATE_LIMIT_STATE_PATH=RATE_LIMIT_STATE_PATH,
                _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
                router_log=router_log,
            ),
            policy=RateLimitBackoffPolicy(
                current_upstream_model_id=current_upstream_model_id,
                parse_retry_after_seconds=parse_retry_after_seconds,
                provider_api_key_count=provider_api_key_count,
                router_rate_limit_capacity=router_rate_limit_capacity,
                router_rate_limit_configured_rpm=router_rate_limit_configured_rpm,
                router_rate_limit_effective_rpm=router_rate_limit_effective_rpm,
                router_rate_limit_key=router_rate_limit_key,
                router_rate_limit_recent=router_rate_limit_recent,
            ),
        ),
    )


_RATE_LIMIT_RESET_HEADER_NAMES = (
    "x-ratelimit-reset-requests",
    "x-rate-limit-reset-requests",
    "ratelimit-reset",
    "rate-limit-reset",
    "x-ratelimit-reset",
    "x-rate-limit-reset",
)

# Ceiling covers a full daily-quota reset (e.g. OpenRouter :free RPD resets at
# 00:00 UTC, up to ~24h away) plus slack, so a key that hit its per-day limit
# rests until the quota actually refreshes instead of retrying hourly and burning
# more of the (failure-counted) daily allowance.
API_KEY_COOLDOWN_MAX_SECONDS = 90000.0
API_KEY_COOLDOWN_DEFAULT_SECONDS = 60.0


def _api_key_cooldown_state_key(provider: str, pcfg: dict[str, Any], key: str) -> str:
    # Namespaced by provider+base_url (so the same key rotates independently per
    # endpoint) and hashed -- the state file is plaintext, never store raw secrets.
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:12]
    return f"{provider_api_key_rotation_name(provider, pcfg)}:__key__:{digest}"


def api_key_cooldown_reset_seconds(headers: Any) -> float:
    """Seconds to rest a key after a 429, from the response headers.

    Priority: X-RateLimit-Reset (exact reset, possibly ms epoch) -> Retry-After
    (seconds) -> a conservative default. Clamped to a sane ceiling.
    """
    reset = rate_limit_reset_seconds(first_header(headers, list(_RATE_LIMIT_RESET_HEADER_NAMES)))
    if reset is None or reset <= 0:
        reset = parse_retry_after_seconds(first_header(headers, ["Retry-After", "retry-after"]))
    if reset is None or reset <= 0:
        reset = API_KEY_COOLDOWN_DEFAULT_SECONDS
    return max(1.0, min(float(reset), API_KEY_COOLDOWN_MAX_SECONDS))


def register_api_key_cooldown(provider: str, pcfg: dict[str, Any], key: str, headers: Any) -> float:
    """Rest a specific API key until its rate-limit reset. Returns the cooldown seconds."""
    if not meaningful_key(key):
        return 0.0
    reset = api_key_cooldown_reset_seconds(headers)
    state_key = _api_key_cooldown_state_key(provider, pcfg, key)
    rate_limit_repository().register_cooldown(state_key, reset)
    router_log("WARN", f"api_key_cooldown provider={provider} key_hash={state_key.rsplit(':', 1)[-1]} rest={reset:.0f}s")
    return reset


def api_key_cooldown_until(provider: str, pcfg: dict[str, Any], key: str) -> float:
    """Epoch until which this key is resting (0.0 if not cooling / expired)."""
    if not meaningful_key(key):
        return 0.0
    return rate_limit_repository().cooldown_until(
        _api_key_cooldown_state_key(provider, pcfg, key)
    )


def provider_live_api_key_count(provider: str, pcfg: dict[str, Any]) -> int:
    keys = provider_config_api_keys(provider, pcfg)
    if len(keys) <= 1:
        return len(keys)
    now = time.time()
    return sum(1 for key in keys if api_key_cooldown_until(provider, pcfg, key) <= now)


def provider_has_live_api_key(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider_live_api_key_count(provider, pcfg) > 0


def reset_api_key_cooldowns_for_router_start() -> int:
    """Clear per-API-key cooldowns when a fresh router process starts.

    Key cooldowns are runtime retry state. Keeping them across a new Ciel Runtime
    router process can make a restarted session use only the one key that did
    not hit a prior 429. Provider/global RPM state is intentionally preserved.
    """
    removed = rate_limit_repository().reset_key_cooldowns()
    if removed:
        router_log("INFO", f"api_key_cooldown_reset_on_router_start removed={removed}")
    return removed


def retry_after_exceeds_request_timeout(headers: Any, timeout: float) -> tuple[bool, float | None]:
    retry_after = first_header(headers, ["Retry-After", "retry-after"])
    seconds = parse_retry_after_seconds(retry_after)
    if seconds is None:
        return False, None
    return seconds >= max(1.0, float(timeout) - 1.0), seconds


def apply_router_rate_limit(provider: str, pcfg: dict[str, Any], model: str | None = None) -> tuple[float, int, int | None]:
    return apply_rate_limit(
        provider, pcfg, model,
        services=RateLimitApplyServices(
            state_store=RateLimitStateStore(
                CONFIG_DIR=CONFIG_DIR,
                RATE_LIMIT_STATE_PATH=RATE_LIMIT_STATE_PATH,
                _RATE_LIMIT_LOCK=_RATE_LIMIT_LOCK,
                router_log=router_log,
            ),
            policy=RateLimitApplyPolicy(
                current_upstream_model_id=current_upstream_model_id,
                provider_api_key_count=provider_api_key_count,
                record_router_rate_usage=record_router_rate_usage,
                router_rate_limit_capacity=router_rate_limit_capacity,
                router_rate_limit_effective_rpm=router_rate_limit_effective_rpm,
                router_rate_limit_key=router_rate_limit_key,
                router_rate_limit_recent=router_rate_limit_recent,
                wait_for_router_rate_limit_penalty=wait_for_router_rate_limit_penalty,
            ),
        ),
    )


def wait_for_router_rate_limit_penalty(provider: str, pcfg: dict[str, Any], model: str | None, rpm: int | None) -> float:
    key = router_rate_limit_key(provider, pcfg, model)
    multi_key = provider_api_key_count(provider, pcfg) > 1
    waited = 0.0
    while True:
        with _RATE_LIMIT_LOCK:
            try:
                state = json.loads(RATE_LIMIT_STATE_PATH.read_text(encoding="utf-8")) if RATE_LIMIT_STATE_PATH.exists() else {}
                if not isinstance(state, dict):
                    state = {}
            except Exception:
                state = {}
            now = time.time()
            entry = state.get(key)
            if not isinstance(entry, dict):
                legacy_key = f"{provider}:{model or current_upstream_model_id(provider, pcfg)}"
                entry = state.get(legacy_key)
            try:
                penalty_until = 0.0 if multi_key else float(entry.get("penalty_until") or 0.0) if isinstance(entry, dict) else 0.0
            except Exception:
                penalty_until = 0.0
            wait = max(0.0, penalty_until - now)
            if wait <= 0.001:
                return waited
        sleep_for = min(wait, 10.0)
        router_log("INFO", f"rate_limit_penalty_wait provider={provider} model={model or ''} rpm={rpm if rpm is not None else 'auto'} wait={wait:.2f}s waited={waited:.2f}s")
        time.sleep(sleep_for)
        waited += sleep_for


RATE_LIMIT_NOTICE_PALETTE = (203, 209, 215, 221, 229, 187, 151, 116, 111, 147, 183, 219)


def colorize_status_text(text: str) -> str:
    if os.environ.get("CIEL_RUNTIME_RATE_LIMIT_ANSI", "1").lower() in ("0", "false", "no"):
        return text
    parts: list[str] = []
    phase = int(time.monotonic() * 8)
    for i, ch in enumerate(text):
        if ch.isspace():
            parts.append(ch)
            continue
        color = RATE_LIMIT_NOTICE_PALETTE[(phase + i) % len(RATE_LIMIT_NOTICE_PALETTE)]
        parts.append(f"\033[1;38;5;{color}m{ch}\033[0m")
    return "".join(parts)


def rate_limit_notice(waited: float, used: int = 0, rpm: int | None = None, show_status: bool = False) -> str:
    return ""


def is_url_up(url: str) -> bool:
    try:
        http_json(url, timeout=1.5)
        return True
    except Exception:
        return False


def nvidia_upstream_base_url() -> str:
    return "https://integrate.api.nvidia.com/v1"


def nvidia_proxy_base_url() -> str:
    env = read_env_file(NCP_ENV)
    host = env.get("PROXY_HOST") or "127.0.0.1"
    port = env.get("PROXY_PORT") or "8788"
    return f"http://{host}:{port}"


def nvidia_api_key() -> str:
    return (
        read_env_file(NCP_ENV).get("NVIDIA_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NV_API_KEY")
        or ""
    ).strip()


def install_ncp_proxy() -> str | None:
    if os.environ.get("CIEL_RUNTIME_AUTO_INSTALL_NCP", "1").lower() in ("0", "false", "no"):
        return None
    NCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(NCP_LOG, "ab", buffering=0) as log:
        log.write(b"\n[ciel-runtime] installing nvd-claude-proxy with pip\n")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "--upgrade", NCP_PYPI_PACKAGE],
            stdout=log,
            stderr=log,
            timeout=240,
        )
    if proc.returncode != 0:
        return None
    importlib.invalidate_caches()
    return find_executable("ncp")


def ncp_module_available() -> bool:
    return importlib.util.find_spec("nvd_claude_proxy") is not None


def ncp_proxy_executable() -> str | None:
    return find_executable("nvd-claude-proxy") or find_executable("ncp")


def ensure_ncp() -> None:
    cfg = load_config()
    provider = cfg["providers"]["nvidia-hosted"]
    upstream = provider.get("base_url") or nvidia_upstream_base_url()
    env = os.environ.copy()
    env.update(read_env_file(NCP_ENV))
    env["NVIDIA_BASE_URL"] = upstream.rstrip("/")
    env.setdefault("PROXY_HOST", "127.0.0.1")
    env.setdefault("PROXY_PORT", "8788")
    env.setdefault("STORAGE_ENGINE", "sqlite")
    timeout_ms = positive_int(provider.get("request_timeout_ms"))
    if timeout_ms:
        env["REQUEST_TIMEOUT_SECONDS"] = str(max(1, timeout_ms / 1000))
    base = f"http://{env['PROXY_HOST']}:{env['PROXY_PORT']}"
    if is_url_up(f"{base}/v1/models"):
        return
    NCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    ncp_exe = ncp_proxy_executable()
    if not (ncp_exe or ncp_module_available()):
        install_ncp_proxy()
        ncp_exe = ncp_proxy_executable()
    if not (ncp_exe or ncp_module_available()):
        raise RuntimeError("nvd-claude-proxy was not found. Install it with: python -m pip install --user nvd-claude-proxy")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with open(NCP_LOG, "ab", buffering=0) as log:
        if ncp_exe:
            cmd = [ncp_exe]
            log.write(f"\n[ciel-runtime] starting nvd-claude-proxy executable: {ncp_exe}\n".encode())
        else:
            cmd = [sys.executable, "-m", "nvd_claude_proxy.main"]
            log.write(b"\n[ciel-runtime] starting nvd-claude-proxy module\n")
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            env=env,
            cwd=str(NCP_ENV.parent),
            creationflags=creationflags,
        )
    deadline = time.time() + 45
    while time.time() < deadline:
        if is_url_up(f"{base}/v1/models"):
            return
        time.sleep(0.5)
    raise RuntimeError("nvd-claude-proxy did not become ready")


def ncp_model_id_for_nvidia_hosted(model_id: str) -> str:
    if model_id.startswith("claude-") and not model_id.startswith("ciel-runtime-"):
        return model_id
    try:
        data = http_json(join_url(nvidia_proxy_base_url(), "/v1/models"), timeout=3.0)
    except Exception:
        return model_id
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return model_id
    for item in items:
        if not isinstance(item, dict):
            continue
        ncp_id = str(item.get("id") or "").strip()
        nvidia_id = str(item.get("nvidia_id") or "").strip()
        if ncp_id == model_id:
            return ncp_id
        if nvidia_id and nvidia_id == model_id and ncp_id:
            return ncp_id
    return model_id


def provider_upstream_model(provider: str, pcfg: dict[str, Any], model: str) -> str:
    """Apply the model alias strategy owned by the configured provider adapter."""

    strategy = provider_request_policy(provider, pcfg).model_alias_strategy
    normalizers = {
        "identity": lambda value: value,
        "ncp": ncp_model_id_for_nvidia_hosted,
    }
    return normalizers[strategy](model)


def provider_requires_streaming(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider_request_policy(provider, pcfg).stream_required


def key_from_request_headers(headers: Any) -> str:
    """Recover the API key baked into an outgoing request's headers (for cooldown)."""
    try:
        key = headers.get("x-api-key")
        if key:
            return str(key)
        auth = str(headers.get("authorization") or headers.get("Authorization") or "")
    except Exception:
        return ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def provider_headers(provider: str, pcfg: dict[str, Any], inbound_headers: Any | None = None) -> dict[str, str]:
    headers = with_upstream_user_agent({"content-type": "application/json", "anthropic-version": "2023-06-01"})
    key = select_provider_api_key(provider, pcfg) or str(pcfg.get("api_key") or "") or "not-used"
    if provider == "anthropic":
        credential = resolve_anthropic_credentials(str(key) if meaningful_key(key) else "", inbound_headers)
        if credential is None:
            raise RuntimeError("Anthropic routed mode needs a configured API key or inbound Claude Code auth headers.")
        headers.update(credential.headers)
    else:
        meaningful = str(key) if meaningful_key(key) else None
        adapter = configured_provider_adapter(provider, pcfg)
        config = provider_contract_config(provider, pcfg)
        headers.update(adapter.build_headers(config, meaningful))
    return headers


def get_current_provider(cfg: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    provider = normalize_provider(cfg.get("current_provider", "nvidia-hosted"))
    return provider, cfg["providers"][provider]


def materialize_runtime_command(
    runtime_name: str,
    executable: str,
    env: dict[str, str],
    provider: str,
    pcfg: dict[str, Any],
    *,
    mode: str,
    protocol: str,
    cwd: Path | None = None,
    enable_channels: bool = False,
    passthrough: Iterable[str] = (),
    options: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Cross the runtime/provider ownership boundary using normalized contracts."""
    if not executable:
        raise RuntimeError(f"{runtime_name} runtime command is empty")
    provider_config = ProviderConfig(
        name=provider,
        base_url=str(pcfg.get("base_url") or ""),
        model=str(pcfg.get("current_model") or pcfg.get("model") or ""),
        api_keys=tuple(parse_api_key_list(pcfg.get("api_keys") or pcfg.get("api_key") or "")),
        options=pcfg,
    )
    runtime_config = RuntimeConfig(
        name=runtime_name,
        executable=executable,
        enable_channels=enable_channels,
        options=options or {},
    )
    spec = LaunchSpec(
        runtime=runtime_config,
        provider=provider_config,
        mode=mode,  # type: ignore[arg-type]
        protocol=protocol,  # type: ignore[arg-type]
        passthrough=tuple(str(value) for value in passthrough),
        cwd=cwd,
    )
    adapter = RUNTIME_ADAPTERS.create(
        runtime_name,
        executable=executable,
        environment=env,
        channel_injection=enable_channels,
    )
    command = adapter.build_command(spec)
    return list(command.argv), dict(command.env)


def native_anthropic_enabled(provider: str) -> bool:
    return provider == "anthropic"


def anthropic_routed_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "anthropic" and parse_bool(pcfg.get("route_through_router"), default=False)


def direct_native_anthropic_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return native_anthropic_enabled(provider) and not anthropic_routed_enabled(provider, pcfg)


def native_agy_enabled(provider: str) -> bool:
    return provider == "agy"


def agy_routed_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "agy" and parse_bool(pcfg.get("route_through_router"), default=False)


def direct_native_agy_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return native_agy_enabled(provider) and not agy_routed_enabled(provider, pcfg)


def native_codex_enabled(provider: str) -> bool:
    return provider == "codex"


def codex_routed_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "codex" and parse_bool(pcfg.get("route_through_router"), default=False)


def direct_native_codex_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return native_codex_enabled(provider) and not codex_routed_enabled(provider, pcfg)


def upstream_model_ids(provider: str, pcfg: dict[str, Any], force_refresh: bool = False) -> list[str]:
    return fetch_upstream_model_ids(
        provider, pcfg, force_refresh,
        services=ProviderModelServices(
            storage=ModelCatalogStorage(
                read_model_list_cache=read_model_list_cache,
                write_model_list_cache=write_model_list_cache,
                write_model_registry=write_model_registry,
                router_log=router_log,
            ),
            http=ModelCatalogHttp(
                http_json=http_json,
                join_url=join_url,
                with_upstream_user_agent=with_upstream_user_agent,
                lm_studio_api_base=lm_studio_api_base,
                nvidia_hosted_list_headers=nvidia_hosted_list_headers,
                nvidia_upstream_base_url=nvidia_upstream_base_url,
            ),
            sources=ProviderCatalogSources(
                ANTHROPIC_MODEL_DOCS_URLS=ANTHROPIC_MODEL_DOCS_URLS,
                fetch_anthropic_api_model_ids=fetch_anthropic_api_model_ids,
                fetch_anthropic_public_model_ids=fetch_anthropic_public_model_ids,
                fetch_fireworks_model_ids=fetch_fireworks_model_ids,
                fireworks_account_id=fireworks_account_id,
                fireworks_management_base_url=fireworks_management_base_url,
            ),
            response_codec=ModelCatalogResponseCodec(
                model_ids_from_response=model_ids_from_response,
                model_info_from_response=model_info_from_response,
            ),
            policy=ModelCatalogPolicy(
                normalize_model_id=normalize_model_id,
                ollama_catalog_model_ids=ollama_catalog_model_ids,
                provider_has_api_key=provider_has_api_key,
                provider_model_catalog_policy=provider_model_catalog_policy,
                provider_model_paths=provider_model_paths,
                provider_model_list_headers=provider_model_list_headers,
                provider_upstream_request_base=provider_upstream_request_base,
                sorted_model_ids=sorted_model_ids,
                unique_model_ids=unique_model_ids,
            ),
        ),
    )


def model_context_field(item: dict[str, Any]) -> int | None:
    for key in (
        "max_model_len",
        "max_context_length",
        "context_length",
        "contextLength",
        "max_context_tokens",
        "max_position_embeddings",
        "trainingContextLength",
    ):
        value = positive_int(item.get(key))
        if value:
            return value
    for key, value in item.items():
        if not isinstance(key, str):
            continue
        leaf = key.rsplit(".", 1)[-1]
        if leaf in ("max_model_len", "max_context_length", "context_length", "max_context_tokens", "max_position_embeddings"):
            fixed = positive_int(value)
            if fixed:
                return fixed
    details = item.get("details")
    if isinstance(details, dict):
        for key in ("max_model_len", "max_context_length", "context_length", "contextLength", "max_context_tokens", "max_position_embeddings"):
            value = positive_int(details.get(key))
            if value:
                return value
    return None


def ollama_runtime_service() -> OllamaRuntimeService:
    return OllamaRuntimeService(
        OllamaRuntimeServices(
            request_base=provider_upstream_request_base,
            post_json=post_json,
            http_json=http_json,
            join_url=join_url,
            model_headers=provider_model_list_headers,
            current_model=current_upstream_model_id,
            positive_int=positive_int,
            model_context=model_context_field,
            format_context=format_context_tokens,
        )
    )


def ollama_api_base(pcfg: dict[str, Any]) -> str:
    return ollama_runtime_service().api_base("ollama", pcfg)


def ollama_provider_api_base(provider: str, pcfg: dict[str, Any]) -> str:
    return ollama_runtime_service().api_base(provider, pcfg)


def ollama_show_parameters(data: dict[str, Any]) -> dict[str, Any]:
    return ollama_runtime_service().show_parameters(data)


def fetch_ollama_api_model_specs(provider: str, pcfg: dict[str, Any], model_id: str, timeout: float = 3.0) -> dict[str, Any]:
    return ollama_runtime_service().fetch_model_specs(provider, pcfg, model_id, timeout)


def ollama_model_id_matches(left: str, right: str) -> bool:
    return ollama_runtime_service().model_id_matches(left, right)


def ollama_runtime_info(pcfg: dict[str, Any], timeout: float = 1.5) -> dict[str, Any] | None:
    return ollama_runtime_service().runtime_info(pcfg, timeout)


def ollama_output_cap_for_context(context_length: int | None) -> int | None:
    return ollama_runtime_service().output_cap(context_length)


def apply_ollama_runtime_output_guard(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return ollama_runtime_service().apply_output_guard(
        provider, pcfg, runtime_info=ollama_runtime_info
    )


def lm_studio_api_base(pcfg: dict[str, Any]) -> str:
    base = provider_upstream_request_base("lm-studio", pcfg)
    if base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


def lm_studio_model_id_matches(left: str, right: str) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()


def lm_studio_runtime_services() -> LmStudioRuntimeServices:
    return LmStudioRuntimeServices(
        api_base=lm_studio_api_base,
        current_model=current_upstream_model_id,
        http_json=http_json,
        join_url=join_url,
        model_list_headers=provider_model_list_headers,
        model_id_matches=lm_studio_model_id_matches,
        positive_int=positive_int,
        model_context=model_context_field,
        log=router_log,
    )


def lm_studio_model_lifecycle() -> LmStudioModelLifecycle:
    return LmStudioModelLifecycle(
        lm_studio_runtime_services(),
        LmStudioLifecyclePolicy(
            recommended_preset=recommended_preset_id,
            required_context=required_context_for_preset,
            model_context_hint=model_context_hint_from_model_id,
            default_context=LM_STUDIO_DEFAULT_CLAUDE_CODE_CONTEXT,
            minimum_context=LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT,
        ),
        post_json,
    )


def lm_studio_runtime_info(pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    return discover_lm_studio_runtime(
        pcfg,
        lm_studio_runtime_services(),
        timeout=timeout,
    )


def lm_studio_v1_model_info(pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    return lm_studio_model_lifecycle().v1_model_info(pcfg, timeout)


def lm_studio_loaded_instance_ids(pcfg: dict[str, Any], timeout: float = 3.0) -> list[str]:
    return lm_studio_model_lifecycle().loaded_instance_ids(pcfg, timeout)


def lm_studio_target_context(pcfg: dict[str, Any], info: dict[str, Any] | None = None) -> int | None:
    return lm_studio_model_lifecycle().target_context(pcfg, info)


def lm_studio_load_timeout_seconds(pcfg: dict[str, Any]) -> float:
    return lm_studio_model_lifecycle().load_timeout_seconds(pcfg)


def lm_studio_load_model(pcfg: dict[str, Any], context_length: int, timeout: float | None = None) -> dict[str, Any]:
    return lm_studio_model_lifecycle().load_model(pcfg, context_length, timeout)


def lm_studio_unload_loaded_instances(pcfg: dict[str, Any], timeout: float = 20.0) -> list[str]:
    return lm_studio_model_lifecycle().unload_loaded_instances(pcfg, timeout)


def lm_studio_load_response_context(response: dict[str, Any], fallback: int) -> int:
    return lm_studio_model_lifecycle().load_response_context(response, fallback)


def ensure_lm_studio_model_loaded_for_context(pcfg: dict[str, Any], timeout: float = 3.0) -> list[str]:
    return lm_studio_model_lifecycle().ensure_loaded_context(pcfg, timeout)


def upstream_model_runtime_info(provider: str, pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    strategy = PROVIDER_COMPATIBILITY.resolve(provider).runtime_model_info_strategy
    if not strategy:
        return None
    if strategy == "lm_studio":
        info = lm_studio_runtime_info(pcfg, timeout=timeout)
        if info:
            return info
    base = provider_upstream_request_base(provider, pcfg)
    if not base:
        return None
    current = current_upstream_model_id(provider, pcfg)
    try:
        data = http_json(join_url(base, "/v1/models"), headers=provider_model_list_headers(provider, pcfg), timeout=timeout)
    except Exception:
        return None
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    fallback_item: dict[str, Any] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if fallback_item is None:
            fallback_item = item
        if str(item.get("id") or "") == current:
            selected = item
            break
    else:
        selected = fallback_item
    if not selected:
        return None
    return {
        "models_url": join_url(base, "/v1/models"),
        "requested_model": current,
        "runtime_model": str(selected.get("id") or ""),
        "max_model_len": model_context_field(selected),
        "owned_by": selected.get("owned_by"),
        "root": selected.get("root"),
    }


def upstream_model_context_limit(provider: str, pcfg: dict[str, Any], timeout: float = 3.0) -> int | None:
    info = upstream_model_runtime_info(provider, pcfg, timeout=timeout)
    if not info:
        return None
    return positive_int(info.get("max_model_len"))


def model_map_for(provider: str, pcfg: dict[str, Any], fetch: bool = True) -> dict[str, str]:
    ids = upstream_model_ids(provider, pcfg) if fetch else cached_or_configured_model_ids(provider, pcfg)
    return {alias_for(provider, mid): mid for mid in ids}


def current_alias(cfg: dict[str, Any]) -> str:
    provider, pcfg = get_current_provider(cfg)
    cur = normalize_model_id(provider, pcfg.get("current_model") or "model")
    if cur.startswith(f"ciel-runtime-{provider}-"):
        return cur
    return alias_for(provider, cur)


def ollama_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "ollama" and bool(pcfg.get("native_compat", True))


def vllm_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "vllm" and bool(pcfg.get("native_compat", True))


def nim_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "self-hosted-nim" and bool(pcfg.get("native_compat", True))


def lm_studio_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "lm-studio" and bool(pcfg.get("native_compat", True))


def nvidia_hosted_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    # NVIDIA's self-hosted NIM server exposes Anthropic-compatible /v1/messages.
    # The hosted API Catalog endpoint at integrate.api.nvidia.com currently
    # exposes OpenAI-compatible /v1/chat/completions instead, so keep it on the
    # ciel-runtime router conversion path even if an old config has native=true.
    return False


def deepseek_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "deepseek" and bool(pcfg.get("native_compat", True))


def opencode_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return (
        provider in OPENCODE_PROVIDER_NAMES
        and bool(pcfg.get("native_compat", True))
        and opencode_endpoint_kind(provider, str(pcfg.get("current_model") or ""), pcfg) == "anthropic-messages"
    )


def kimi_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "kimi" and bool(pcfg.get("native_compat", True))


def zai_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "zai" and bool(pcfg.get("native_compat", True))


def fireworks_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider == "fireworks" and bool(pcfg.get("native_compat", True))


def provider_native_compat_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.router_native_anthropic_enabled(
        provider_contract_config(provider, pcfg),
        str(pcfg.get("current_model") or ""),
    )


def provider_model_selection() -> ProviderModelSelection:
    return ProviderModelSelection(
        ModelIdentityPorts(
            normalize=normalize_model_id,
            model_map=model_map_for,
            unslug=unslug_provider_alias,
            api_model_id=upstream_api_model_id,
            strip_context_suffix=strip_claude_context_suffix,
            alias=alias_for,
        ),
        ModelSelectionPorts(
            adapter=configured_provider_adapter,
            contract=provider_contract_config,
            placeholders=lambda provider: set(
                PROVIDER_ADAPTERS.create(provider).placeholder_model_ids()
            ),
            upstream_ids=upstream_model_ids,
            unique_ids=unique_model_ids,
            apply_specs=apply_current_model_specs_to_provider,
            apply_timeout=apply_recommended_timeout_for_model_context,
        ),
        ModelCatalogPorts(
            model_object=model_object,
            headers=provider_headers,
            fetch_anthropic=fetch_anthropic_api_model_ids,
            sorted_ids=sorted_model_ids,
            routed_anthropic=anthropic_routed_enabled,
            log=router_log,
        ),
    )


def current_upstream_model_id(provider: str, pcfg: dict[str, Any]) -> str:
    return provider_model_selection().current_upstream_id(provider, pcfg)


def provider_placeholder_model_ids(provider: str) -> set[str]:
    return provider_model_selection().selection.placeholders(provider)


def current_model_needs_provider_selection(provider: str, pcfg: dict[str, Any]) -> bool:
    return provider_model_selection().needs_selection(provider, pcfg)


def ensure_current_model_from_provider_list(
    provider: str,
    pcfg: dict[str, Any],
    *,
    force_refresh: bool = False,
) -> tuple[bool, list[str]]:
    return provider_model_selection().ensure_selected(
        provider, pcfg, force_refresh=force_refresh
    )


def launch_model_id(provider: str, pcfg: dict[str, Any]) -> str:
    return provider_model_selection().launch_id(provider, pcfg)


def resolve_requested_model(provider: str, pcfg: dict[str, Any], requested: str | None) -> str:
    return provider_model_selection().resolve_requested(provider, pcfg, requested)


def resolve_tool_model_references(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return provider_model_selection().resolve_tool_models(provider, pcfg, body)


def list_model_objects(provider: str, pcfg: dict[str, Any]) -> list[dict[str, Any]]:
    return provider_model_selection().list_objects(provider, pcfg)


def list_model_objects_for_request(provider: str, pcfg: dict[str, Any], inbound_headers: Any | None = None) -> list[dict[str, Any]]:
    return provider_model_selection().list_objects_for_request(
        provider, pcfg, inbound_headers
    )


def provider_upstream_request_base(provider: str, pcfg: dict[str, Any]) -> str:
    return configured_provider_adapter(provider, pcfg).default_base_url().rstrip("/")


def native_anthropic_base_url(provider: str, pcfg: dict[str, Any]) -> str:
    base = pcfg.get("base_url", "http://127.0.0.1:8000").rstrip("/")
    if provider in ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter", "kimi", "fireworks") and base.endswith("/v1"):
        return base[:-3].rstrip("/")
    return base


OPENAI_COMPATIBLE_ROUTER_PROVIDERS = ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter")
CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS = (
    "vllm",
    "lm-studio",
    "nvidia-hosted",
    "self-hosted-nim",
    "openrouter",
    "kimi",
    "fireworks",
)
AUTO_DETECT_NATIVE_COMPAT_PROVIDERS = ("vllm", "lm-studio", "self-hosted-nim")
CLAUDE_ANTHROPIC_ENDPOINT_PROVIDERS = ("deepseek", "kimi", "zai", "fireworks")


def provider_openai_router_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    model = str(pcfg.get("current_model") or pcfg.get("model") or "")
    return select_provider_protocol(provider, pcfg, "anthropic_messages", model) == "openai_chat"


def codex_openai_router_enabled(provider: str, pcfg: dict[str, Any]) -> bool:
    del pcfg
    return provider in CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS


def preferred_native_compat_for_launch_runtime(runtime: str, provider: str, pcfg: dict[str, Any]) -> tuple[bool | None, str]:
    runtime = str(runtime or "").strip().lower()
    if provider in ("anthropic", "codex", "agy", "ollama", "ollama-cloud"):
        return None, ""
    if runtime == "claude":
        if provider in AUTO_DETECT_NATIVE_COMPAT_PROVIDERS:
            return auto_detect_native_compat_for_base_url(provider, pcfg)
        if provider in CLAUDE_ANTHROPIC_ENDPOINT_PROVIDERS:
            return True, "Claude Code prefers the provider's Anthropic Messages compatible endpoint"
        if provider in OPENCODE_PROVIDER_NAMES:
            endpoint_kind = opencode_endpoint_kind(provider, str(pcfg.get("current_model") or ""), pcfg)
            if endpoint_kind == "anthropic-messages":
                return True, "Claude Code prefers the model's Anthropic Messages endpoint"
            if endpoint_kind == "openai-chat":
                return False, "selected model uses an OpenAI Chat endpoint"
        return None, ""
    if runtime in ("codex", "codex-app-server"):
        if provider in OPENCODE_PROVIDER_NAMES:
            endpoint_kind = opencode_endpoint_kind(provider, str(pcfg.get("current_model") or ""), pcfg)
            if endpoint_kind == "openai-chat":
                return False, "Codex prefers the model's OpenAI Chat compatible endpoint"
            return None, ""
        if provider in CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS:
            return False, "Codex prefers OpenAI Chat compatible upstream routing"
        return None, ""
    return None, ""


def apply_launch_endpoint_policy(cfg: dict[str, Any], runtime: str) -> list[str]:
    provider, pcfg = get_current_provider(cfg)
    desired, reason = preferred_native_compat_for_launch_runtime(runtime, provider, pcfg)
    if desired is None:
        return []
    current = bool(pcfg.get("native_compat", True))
    if current == desired:
        router_log("INFO", f"launch_endpoint_policy runtime={runtime} provider={provider} native_compat={current} unchanged reason={reason}")
        return []
    pcfg["native_compat"] = desired
    clear_model_cache()
    save_config(cfg)
    mode = "Anthropic Messages compatible" if desired else "OpenAI Chat compatible"
    line = f"Endpoint policy updated for {runtime}: {PROVIDER_LABELS.get(provider, provider)} -> {mode} ({reason})."
    router_log("INFO", f"launch_endpoint_policy runtime={runtime} provider={provider} native_compat={desired} changed reason={reason}")
    return [line]


def provider_wire_profile(provider: str, pcfg: dict[str, Any], body: dict[str, Any] | None = None) -> dict[str, str]:
    return resolve_provider_wire_profile(
        provider, pcfg, body,
        services=ProviderWireServices(
            normalize_model_id=normalize_model_id,
            openai_chat_reasoning_passback_enabled_for_body=openai_chat_reasoning_passback_enabled_for_body,
            preserves_anthropic_thinking_contract=preserves_anthropic_thinking_contract,
            provider_supports_tool_choice=provider_supports_tool_choice,
            resolve_requested_model=resolve_requested_model,
            select_provider_protocol=select_provider_protocol,
        ),
    )


def endpoint_route_exists(url: str, headers: dict[str, str], timeout: float = 1.5) -> bool | None:
    req = urllib.request.Request(url, data=b"{}", headers=with_upstream_user_agent(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except Exception:
            pass
        if exc.code == 404:
            return False
        if exc.code in (400, 401, 403, 405, 422):
            return True
        return None
    except Exception:
        return None


def auto_detect_native_compat_for_base_url(provider: str, pcfg: dict[str, Any]) -> tuple[bool | None, str]:
    if provider not in AUTO_DETECT_NATIVE_COMPAT_PROVIDERS:
        return None, ""
    base = provider_upstream_request_base(provider, pcfg)
    if not base:
        return None, "missing base URL"
    headers = provider_model_list_headers(provider, pcfg)
    anthropic_route = endpoint_route_exists(join_url(native_anthropic_base_url(provider, pcfg), "/v1/messages"), headers)
    openai_route = endpoint_route_exists(join_url(base, "/v1/chat/completions"), headers)
    if anthropic_route is True:
        return True, "Anthropic Messages route detected"
    if openai_route is True and anthropic_route is False:
        return False, "OpenAI chat completions route detected"
    if openai_route is True:
        return None, "OpenAI route detected, Anthropic route inconclusive; keeping Anthropic default"
    return None, "endpoint family inconclusive; keeping Anthropic default"


def endpoint_probe_status_label(value: bool | None) -> str:
    if value is True:
        return "available"
    if value is False:
        return "missing"
    return "inconclusive"


def compatibility_endpoint_probe_headers(provider: str, pcfg: dict[str, Any]) -> dict[str, str]:
    try:
        return provider_headers(provider, pcfg)
    except Exception:
        return provider_model_list_headers(provider, pcfg)


def compatibility_endpoint_probe_lines(provider: str, pcfg: dict[str, Any], timeout: float = 1.5) -> list[str]:
    if provider in ("agy", "codex", "ollama", "ollama-cloud"):
        return []
    probe_timeout = max(0.25, min(float(timeout or 1.5), 3.0))
    headers = compatibility_endpoint_probe_headers(provider, pcfg)
    anthropic_url = join_url(native_anthropic_base_url(provider, pcfg), "/v1/messages")
    openai_url = join_url(provider_upstream_request_base(provider, pcfg), "/v1/chat/completions")
    anthropic_status = endpoint_route_exists(anthropic_url, headers, timeout=probe_timeout)
    openai_status = endpoint_route_exists(openai_url, headers, timeout=probe_timeout)
    return [
        "Endpoint probes:",
        f"- Anthropic Messages (/v1/messages): {endpoint_probe_status_label(anthropic_status)} ({anthropic_url})",
        f"- OpenAI Chat (/v1/chat/completions): {endpoint_probe_status_label(openai_status)} ({openai_url})",
    ]


def http_response_adapter() -> HttpResponseAdapter:
    return HttpResponseAdapter(router_log)


def channel_delivery_guard() -> ChannelDeliveryGuard:
    return ChannelDeliveryGuard(router_log)


def write_json(handler: BaseHTTPRequestHandler, obj: Any, status: int = 200) -> None:
    http_response_adapter().write_json(handler, obj, status)


def is_client_disconnect_error(exc: BaseException) -> bool:
    return http_response_adapter().is_client_disconnect(exc)


def try_write_json(handler: BaseHTTPRequestHandler, obj: Any, status: int = 200) -> bool:
    return http_response_adapter().try_write_json(handler, obj, status)


def _handler_response_status(handler: BaseHTTPRequestHandler) -> int | None:
    return http_response_adapter().response_status(handler)


def _channel_delivery_metadata(metadata: dict[str, Any] | None) -> bool:
    return channel_delivery_guard().metadata_enabled(metadata)


def begin_pending_channel_delivery(handler: BaseHTTPRequestHandler | None, body: dict[str, Any]) -> None:
    channel_delivery_guard().begin(handler, body)


def mark_pending_channel_delivery_success(handler: BaseHTTPRequestHandler | None, reason: str = "response_complete") -> None:
    channel_delivery_guard().success(handler, reason)


def mark_pending_channel_delivery_failed(handler: BaseHTTPRequestHandler | None, reason: str = "response_failed") -> None:
    channel_delivery_guard().failed(handler, reason)


def pending_channel_delivery_confirmed(handler: BaseHTTPRequestHandler | None) -> bool:
    return channel_delivery_guard().confirmed(handler)


def write_empty_response(handler: BaseHTTPRequestHandler, status: int = 202) -> None:
    http_response_adapter().write_empty(handler, status)


def write_accepted_response(handler: BaseHTTPRequestHandler) -> None:
    http_response_adapter().write_accepted(handler)


def reject_external_router_request(handler: BaseHTTPRequestHandler, cfg: dict[str, Any] | None = None) -> bool:
    if router_request_allowed(handler, cfg):
        return False
    external_enabled = router_debug_external_access_enabled(cfg)
    status = 401 if external_enabled else 403
    message = (
        "ciel-runtime router external authentication is required."
        if external_enabled
        else "ciel-runtime router external debug access is off."
    )
    payload = json.dumps(
        {"type": "error", "error": {"type": "unauthorized" if status == 401 else "forbidden", "message": message}},
        ensure_ascii=False,
    ).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("content-length", str(len(payload)))
    if status == 401:
        handler.send_header("www-authenticate", 'Bearer realm="ciel-runtime"')
    handler.end_headers()
    handler.wfile.write(payload)
    return True


def write_router_activity(event: str, provider: str, model: str | None = None, **fields: Any) -> None:
    try:
        level = "error" if event == "error" else ("warn" if event in {"retry", "wait"} else "info")
        EVENT_BUS.publish(
            level=level,
            category=f"router.{event}",
            message=f"{event} {provider} {model or ''}".strip(),
            provider=provider,
            model=model,
            data=fields,
        )
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": time.time(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": event,
            "provider": provider,
            "model": model or "",
        }
        data.update(fields)
        tmp = ROUTER_ACTIVITY_PATH.with_name(f"{ROUTER_ACTIVITY_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(ROUTER_ACTIVITY_PATH)
    except Exception:
        pass


def write_context_compact_activity(provider: str, model: str | None = None, **fields: Any) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "updated_at": time.time(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": "compact",
            "provider": provider,
            "model": model or "",
        }
        data.update(fields)
        tmp = CONTEXT_COMPACT_ACTIVITY_PATH.with_name(
            f"{CONTEXT_COMPACT_ACTIVITY_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CONTEXT_COMPACT_ACTIVITY_PATH)
        EVENT_BUS.publish(
            level="info",
            category="context.compact",
            message=f"compact {provider} {model or ''}".strip(),
            provider=provider,
            model=model,
            data=fields,
        )
    except Exception:
        pass


def context_limit_for_status(provider: str, pcfg: dict[str, Any]) -> int | None:
    strategy = provider_context_policy(provider, pcfg).status_capacity_strategy
    if strategy == "ollama_budget":
        return ollama_context_limit_for_budget(pcfg)
    if strategy == "openai_budget":
        return openai_context_limit_for_budget(provider, pcfg)
    if strategy == "provider":
        return provider_model_context_capacity(provider, pcfg)
    return positive_int(pcfg.get("context_window")) or positive_int(pcfg.get("max_model_len"))


def write_context_usage(provider: str, pcfg: dict[str, Any], body: dict[str, Any], source: str) -> None:
    try:
        tokens = estimate_tokens(body)
        limit = context_limit_for_status(provider, pcfg)
        percent = round((tokens / limit) * 100.0, 1) if limit else None
        EVENT_BUS.publish(
            level="debug",
            category="context.usage",
            message=f"context usage {tokens}/{limit or '?'} tokens",
            provider=provider,
            model=str(body.get("model") or pcfg.get("current_model") or ""),
            data={
                "source": source,
                "tokens": tokens,
                "context_limit": limit,
                "percent": percent,
                "messages": len(body.get("messages") or []),
                "tools": len(body.get("tools") or []),
            },
        )
        data = {
            "updated_at": time.time(),
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": provider,
            "model": str(body.get("model") or pcfg.get("current_model") or ""),
            "source": source,
            "tokens": tokens,
            "context_limit": limit,
            "percent": percent,
            "messages": len(body.get("messages") or []),
            "tools": len(body.get("tools") or []),
        }
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONTEXT_USAGE_PATH.with_name(f"{CONTEXT_USAGE_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CONTEXT_USAGE_PATH)
    except Exception:
        pass


def write_text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", content_type)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def llm_config_payload(messages: list[str] | None = None) -> dict[str, Any]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    lang = cfg.get("language", "en")
    rows, values = llm_option_panel_rows(provider, pcfg, lang)
    option_rows = [
        {"label": row, "key": key, "value": llm_option_prompt_default(provider, pcfg, key)}
        for row, key in zip(rows, values)
        if key not in ("back", "__info__", "preset", "context_setup", "timeout_profile")
    ]
    preset_rows, preset_values = llm_preset_panel_rows(provider, pcfg, lang)
    context_rows, context_values = context_setup_panel_rows(provider, pcfg, lang)
    timeout_rows, timeout_values = timeout_profile_panel_rows(pcfg, lang)
    return {
        "ok": True,
        "messages": messages or [],
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider),
        "model": str(pcfg.get("current_model") or ""),
        "alias": current_alias(cfg),
        "advisor_model": str(pcfg.get("advisor_model") or ""),
        "preset": applied_preset_id(provider, pcfg),
        "context": context_setting_status(provider, pcfg),
        "timeout": timeout_profile_status(pcfg, lang),
        "options": option_rows,
        "presets": [
            {"label": row, "value": value}
            for row, value in zip(preset_rows, preset_values)
            if value not in ("back", "__info__")
        ],
        "contexts": [
            {"label": row, "value": value}
            for row, value in zip(context_rows, context_values)
            if value not in ("back", "__info__")
        ],
        "timeouts": [
            {"label": row, "value": value}
            for row, value in zip(timeout_rows, timeout_values)
            if value not in ("back", "__info__")
        ],
    }


def apply_timeout_profile_config(provider: str, profile_id: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    lines = apply_timeout_profile_to_provider(pcfg, profile_id, cfg.get("language", "en"))
    save_config(cfg)
    clear_model_cache()
    return lines


def handle_llm_config_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    if path != "/ca/config/llm":
        return False
    write_json(handler, llm_config_payload())
    return True


def handle_llm_config_post(handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
    if path != "/ca/config/llm":
        return False
    cfg = load_config()
    provider, _pcfg = get_current_provider(cfg)
    action = str(body.get("action") or "option").strip()
    value = str(body.get("value") or "").strip()
    key = str(body.get("key") or "").strip()
    messages: list[str]
    try:
        if action == "model":
            messages = set_model_config(value)
        elif action == "advisor_model":
            messages = set_advisor_model_config(value)
        elif action == "preset":
            messages = apply_llm_preset_config(provider, value)
        elif action == "context_setup":
            messages = apply_context_setup_config(provider, value)
        elif action == "timeout_profile":
            messages = apply_timeout_profile_config(provider, value)
        elif action == "option":
            if not key:
                raise SystemExit("Missing option key")
            messages = set_llm_option_config(provider, key, value)
        else:
            raise SystemExit(f"Unknown LLM config action: {action}")
        EVENT_BUS.publish(level="info", category="config.llm", message=f"updated {action} {key or value}", provider=provider, data={"action": action, "key": key, "value": value})
        write_json(handler, llm_config_payload(messages))
    except SystemExit as exc:
        write_json(handler, {"ok": False, "error": str(exc), "messages": [str(exc)]}, 400)
    except Exception as exc:
        router_log("ERROR", f"llm config update failed: {type(exc).__name__}: {exc}")
        write_json(handler, {"ok": False, "error": f"{type(exc).__name__}: {exc}", "messages": [f"{type(exc).__name__}: {exc}"]}, 500)
    return True


def render_router_home_html(cfg: dict[str, Any], provider: str, pcfg: dict[str, Any]) -> str:
    model = current_alias(cfg)
    upstream = read_json_file(ROUTER_ACTIVITY_PATH)
    context = read_json_file(CONTEXT_USAGE_PATH)
    used, rpm_limit = router_rate_limit_usage(provider, pcfg)
    rpm_text = "off"
    if bool(pcfg.get("rate_limit_status", False)):
        rpm_text = f"{used}/min unmanaged" if rpm_limit == 0 else (f"{used}/{rpm_limit}" if rpm_limit else "unknown")
    timeout_ms = positive_int(pcfg.get("request_timeout_ms")) or DEFAULT_REQUEST_TIMEOUT_MS
    idle_ms = positive_int(pcfg.get("stream_idle_timeout_ms")) or timeout_profile_idle_ms(timeout_ms)
    context_limit = context_limit_for_status(provider, pcfg)
    context_tokens = positive_int(context.get("tokens"))
    context_pct = context.get("percent")
    if isinstance(context_pct, (int, float)):
        context_text = f"{context_tokens or 0:,}/{context_limit or 0:,} tok ({context_pct}%)"
    else:
        context_text = f"{context_tokens or 0:,}/{context_limit or 0:,} tok" if context_limit else "unknown"
    upstream_text = " · ".join(
        bit
        for bit in (
            str(upstream.get("event") or "idle"),
            str(upstream.get("provider") or provider),
            str(upstream.get("model") or pcfg.get("current_model") or ""),
        )
        if bit
    )
    return render_router_home_page(
        version=VERSION,
        provider=provider,
        model=model,
        context_text=context_text,
        timeout_ms=timeout_ms,
        idle_ms=idle_ms,
        rpm_text=rpm_text,
        upstream_text=upstream_text,
    )

def render_web_chat_html(cfg: dict[str, Any], provider: str, pcfg: dict[str, Any]) -> str:
    return render_web_chat_page(
        model=current_alias(cfg),
        provider=provider,
        mode=provider_mode_label(provider, pcfg),
        api_status=api_key_status_line(provider, pcfg),
        timeout_ms=positive_int(pcfg.get("request_timeout_ms")) or DEFAULT_REQUEST_TIMEOUT_MS,
    )

def handle_web_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    if path not in ("/ca/web/chat", "/ca/web/chat/"):
        return False
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    write_text_response(handler, render_web_chat_html(cfg, provider, pcfg), content_type="text/html; charset=utf-8")
    return True


def parse_json_body(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8") if raw else "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def query_int(params: dict[str, list[str]], name: str, default: int) -> int:
    values = params.get(name) or []
    try:
        return int(values[0])
    except Exception:
        return default


def handle_events_get(handler: BaseHTTPRequestHandler, path: str, query: dict[str, list[str]]) -> bool:
    if path == "/ca/events":
        write_text_response(handler, render_events_html(), content_type="text/html; charset=utf-8")
        return True
    if path == "/ca/events/recent":
        write_json(
            handler,
            {
                "ok": True,
                "events": EVENT_BUS.recent(
                    limit=query_int(query, "limit", 200),
                    min_id=query_int(query, "after", 0),
                    level=(query.get("level") or [None])[0],
                    category=(query.get("category") or [None])[0],
                ),
            },
        )
        return True
    if path == "/ca/events/stream":
        last_id = query_int(query, "after", 0)
        handler.send_response(200)
        handler.send_header("content-type", "text/event-stream")
        handler.send_header("cache-control", "no-cache")
        handler.send_header("connection", "close")
        handler.end_headers()
        try:
            for event in EVENT_BUS.recent(limit=200, min_id=last_id):
                last_id = max(last_id, int(event.get("id") or 0))
                handler.wfile.write(f"event: event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
            handler.wfile.flush()
            while True:
                events = EVENT_BUS.wait_after(last_id, timeout=15.0)
                if not events:
                    handler.wfile.write(b": keepalive\n\n")
                    handler.wfile.flush()
                    continue
                for event in events:
                    last_id = max(last_id, int(event.get("id") or 0))
                    handler.wfile.write(f"event: event\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return True
        except Exception as exc:
            try:
                router_log("DEBUG", f"events stream closed: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        return True
    return False


def _safe_segment(value: str, fallback: str = "item") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip(".-")
    return text[:120] or fallback


def chat_file_max_bytes() -> int:
    raw = str(os.environ.get("CIEL_RUNTIME_CHAT_FILE_MAX_BYTES") or "").strip()
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return 25 * 1024 * 1024


def store_chat_file_upload(body: dict[str, Any]) -> dict[str, Any]:
    CHAT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    raw_name = str(body.get("name") or f"file-{int(time.time())}.txt").strip() or "file"
    content = body.get("content", "")
    encoding = str(body.get("encoding") or "utf-8").strip().lower()
    if encoding == "base64":
        try:
            data = base64.b64decode(str(content).encode("ascii"), validate=True)
        except Exception as exc:
            raise ValueError("invalid base64 file content") from exc
    elif encoding in {"", "text", "utf-8", "utf8"}:
        data = str(content).encode("utf-8")
    else:
        raise ValueError(f"unsupported file encoding: {encoding}")
    max_bytes = chat_file_max_bytes()
    if len(data) > max_bytes:
        raise OverflowError(f"file too large: {len(data)} bytes exceeds {max_bytes} bytes")
    name = f"{time.time_ns()}-{_safe_segment(raw_name, 'file')}"
    target = CHAT_FILES_DIR / name
    target.write_bytes(data)
    path = f"/ca/chat/files/{urllib.parse.quote(name)}"
    content_type = str(body.get("content_type") or body.get("mime_type") or "application/octet-stream").strip()
    return {
        "name": name,
        "original_name": raw_name,
        "url": f"{ROUTER_BASE}{path}",
        "path": path,
        "bytes": len(data),
        "content_type": content_type[:200] or "application/octet-stream",
    }


def store_chat_file_from_path(path_value: Any, name: str | None = None, content_type: str | None = None) -> dict[str, Any]:
    raw_path = str(path_value or "").strip()
    if not raw_path:
        raise ValueError("file path is required")
    source = Path(raw_path).expanduser()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"file not found: {raw_path}")
    data = source.read_bytes()
    max_bytes = chat_file_max_bytes()
    if len(data) > max_bytes:
        raise OverflowError(f"file too large: {len(data)} bytes exceeds {max_bytes} bytes")
    guessed_type = content_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return store_chat_file_upload(
        {
            "name": name or source.name,
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
            "content_type": guessed_type,
        }
    )


def chat_file_markdown_lines(uploads: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for upload in uploads:
        label = str(upload.get("original_name") or upload.get("name") or "file")
        url = str(upload.get("url") or upload.get("path") or "")
        byte_count = upload.get("bytes")
        ctype = str(upload.get("content_type") or "application/octet-stream")
        detail_parts = []
        if isinstance(byte_count, int):
            detail_parts.append(f"{byte_count} bytes")
        if ctype:
            detail_parts.append(ctype)
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"- [{label}]({url}){detail}")
    return lines


def chat_file_message_text(message: str, uploads: list[dict[str, Any]]) -> str:
    body = str(message or "").strip()
    lines = chat_file_markdown_lines(uploads)
    if not lines:
        return body
    attachment_text = "Attached files:\n" + "\n".join(lines)
    return f"{body}\n\n{attachment_text}" if body else attachment_text


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return _as_string_list(parsed)
            except Exception:
                pass
        if text.lower() in ("all", "*"):
            return ["all"]
        return [text]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_as_string_list(item))
        return out
    return [str(value).strip()] if str(value).strip() else []


def _chat_init_next_id() -> int:
    global _CHAT_NEXT_ID
    if _CHAT_NEXT_ID is not None:
        return _CHAT_NEXT_ID
    _CHAT_NEXT_ID = _chat_scan_max_id() + 1
    return _CHAT_NEXT_ID


def channel_message_repository() -> ChannelMessageRepository:
    return ChannelMessageRepository(path=CHAT_MESSAGES_PATH, log=router_log)


def _chat_scan_max_id() -> int:
    return channel_message_repository().max_id()


def _channel_launch_recent_seconds() -> float:
    raw = str(os.environ.get("CIEL_RUNTIME_CHANNEL_LAUNCH_RECENT_SECONDS") or "").strip()
    if not raw:
        return CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT
    try:
        return float(raw)
    except (TypeError, ValueError):
        return CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT


def _chat_scan_max_id_before_epoch(cutoff_epoch: float) -> int:
    return channel_message_repository().max_id_before_epoch(cutoff_epoch)


@contextlib.contextmanager
def _chat_messages_file_lock() -> Iterable[None]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CHAT_MESSAGES_PATH.with_name(CHAT_MESSAGES_PATH.name + ".lock")
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_chat_messages(after_id: int = 0, channel: str | None = None, recipient: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return channel_message_repository().read(after_id, channel, recipient, limit)


def read_chat_messages_before(before_id: int = 0, channel: str | None = None, recipient: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return channel_message_repository().read_before(before_id, channel, recipient, limit)


def _chat_message_recent_rows_locked(limit: int = CHAT_MESSAGE_DEDUPE_SCAN_LIMIT) -> list[dict[str, Any]]:
    return channel_message_repository().recent_rows(limit)


def channel_launch_guard_repository() -> ChannelLaunchGuardRepository:
    return ChannelLaunchGuardRepository(
        path=CHANNEL_LLM_LAUNCH_GUARD_PATH,
        now=time.time,
        log=router_log,
    )


def _channel_llm_launch_guard() -> dict[str, Any] | None:
    return channel_launch_guard_repository().read()


def _write_channel_llm_launch_guard(max_existing_id: int, ttl_seconds: float = 180.0) -> None:
    channel_launch_guard_repository().write(max_existing_id, ttl_seconds)


def _chat_message_duplicate_locked(message: dict[str, Any]) -> dict[str, Any] | None:
    stable_key = _chat_message_stable_dedupe_key(message)
    fallback_key = _chat_message_fallback_dedupe_key(message)
    if not stable_key and not fallback_key:
        return None
    now = time.time()
    launch_guard = _channel_llm_launch_guard() if fallback_key else None
    guard_max_existing_id = int(launch_guard.get("max_existing_id") or 0) if launch_guard else 0
    for row in reversed(_chat_message_recent_rows_locked()):
        if stable_key and _chat_message_stable_dedupe_key(row) == stable_key:
            return row
        if not fallback_key or _chat_message_fallback_dedupe_key(row) != fallback_key:
            continue
        row_time = _chat_message_time_seconds(row.get("time"))
        if row_time > 0 and now - row_time <= CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS:
            return row
        try:
            row_id = int(row.get("id") or 0)
        except Exception:
            row_id = 0
        if guard_max_existing_id > 0 and 0 < row_id <= guard_max_existing_id:
            return row
    return None


def append_chat_message(payload: dict[str, Any]) -> dict[str, Any]:
    global _CHAT_NEXT_ID
    with _CHAT_CONDITION:
        with _chat_messages_file_lock():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if CHAT_MESSAGES_PATH.exists() and CHAT_MESSAGES_PATH.stat().st_size > CHAT_MESSAGES_MAX_BYTES:
                CHAT_MESSAGES_PATH.replace(CHAT_MESSAGES_PATH.with_suffix(".jsonl.1"))
                _CHAT_NEXT_ID = 1
            next_id = _chat_scan_max_id() + 1
            _CHAT_NEXT_ID = next_id + 1
            message = {
                "id": next_id,
                "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "channel": str(payload.get("channel") or "default"),
                "sender_id": str(payload.get("sender_id") or payload.get("sender") or "anonymous"),
                "recipients": _as_string_list(payload.get("recipients", payload.get("recipient_id"))),
                "thread_id": str(payload.get("thread_id") or payload.get("parent_id") or next_id),
                "parent_id": payload.get("parent_id"),
                "message": str(payload.get("message") or payload.get("text") or ""),
                "kind": str(payload.get("kind") or "message"),
                "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
            }
            if payload.get("visibility") is not None:
                message["visibility"] = str(payload.get("visibility") or "user")
            if payload.get("delivery") is not None:
                message["delivery"] = _as_string_list(payload.get("delivery"))
            duplicate = _chat_message_duplicate_locked(message)
            if duplicate:
                existing_id = duplicate.get("id")
                returned = dict(duplicate)
                returned["_ciel_runtime_duplicate"] = True
                router_log(
                    "INFO",
                    f"chat_message_skipped_duplicate existing_id={existing_id} channel={message.get('channel')} kind={message.get('kind')}",
                )
                return returned
            with CHAT_MESSAGES_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        _CHAT_CONDITION.notify_all()
        return message








def channel_connection_registry() -> ChannelConnectionRegistry:
    return ChannelConnectionRegistry(
        states=_CHANNEL_SSE_CONNECTIONS,
        lock=_CHANNEL_SSE_LOCK,
        rpc_condition=_CHANNEL_SSE_RPC_CONDITION,
        log=router_log,
    )


def _channel_sse_status_public(name: str, state: dict[str, Any]) -> dict[str, Any]:
    return ChannelConnectionRegistry.public_status(name, state)


def channel_sse_status() -> dict[str, Any]:
    return channel_connection_registry().statuses()


def _channel_sse_set_state(name: str, **updates: Any) -> None:
    channel_connection_registry().update(name, **updates)


def _channel_streamable_http_mark_session_lost(name: str, reason: str) -> None:
    channel_connection_registry().mark_session_lost(name, reason)


def _channel_sse_store_rpc_response(name: str, data_text: str) -> bool:
    return channel_connection_registry().store_rpc_response(name, data_text)


def _channel_sse_take_rpc_response(name: str, rpc_id: Any, timeout: float) -> dict[str, Any] | None:
    return channel_connection_registry().take_rpc_response(name, rpc_id, timeout)


def _channel_sse_public_mcp_name(name: str) -> str:
    return ChannelConnectionRegistry.public_mcp_name(name)


def _channel_sse_state_name_for_mcp_server(server_name: str) -> str | None:
    return channel_connection_registry().state_name_for_mcp_server(server_name)


def _channel_sse_absolute_endpoint(stream_url: str, endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return urllib.parse.urljoin(stream_url, endpoint)


def codex_mcp_split_proxy_url(server_name: str) -> str:
    return f"{ROUTER_BASE}{CODEX_MCP_SPLIT_PROXY_PREFIX}{urllib.parse.quote(str(server_name), safe='')}"


def codex_mcp_split_proxy_server(path: str) -> tuple[str, dict[str, Any]] | None:
    name = codex_mcp_split_proxy_server_name(path)
    if not name:
        return None
    server = codex_streamable_http_mcp_servers(CODEX_MCP_CONFIG).get(name)
    if not isinstance(server, dict):
        return None
    return name, server


def _codex_mcp_split_proxy_headers(handler: BaseHTTPRequestHandler, server: dict[str, Any]) -> dict[str, str]:
    out = mcp_server_runtime_headers(server)
    skipped = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}
    for key, value in handler.headers.items():
        if str(key).lower() in skipped:
            continue
        out[str(key)] = str(value)
    return out


def codex_mcp_local_sse_hold_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CODEX_MCP_LOCAL_SSE_SECONDS", "3600")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        value = 3600.0
    return max(0.0, min(24 * 3600.0, value))


def codex_mcp_split_proxy_enabled() -> bool:
    return env_bool(os.environ.get("CIEL_RUNTIME_CODEX_MCP_SPLIT_PROXY"), False)


def handle_codex_mcp_split_proxy_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    resolved = codex_mcp_split_proxy_server(path)
    if resolved is None:
        return False
    name, _server = resolved
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache")
    handler.send_header("connection", "close")
    handler.end_headers()
    router_log("INFO", f"codex_mcp_split_proxy_local_sse name={name} upstream_get=false")
    deadline = time.time() + codex_mcp_local_sse_hold_seconds()
    try:
        while time.time() < deadline:
            handler.wfile.write(b": ciel-runtime owns upstream SSE for this MCP server\n\n")
            handler.wfile.flush()
            time.sleep(min(15.0, max(0.05, deadline - time.time())))
    except (BrokenPipeError, ConnectionError, ConnectionResetError):
        pass
    return True


def _codex_mcp_split_proxy_is_channel_sse_event(event: bytes) -> bool:
    data_lines: list[str] = []
    for raw_line in event.splitlines():
        line = raw_line.decode("utf-8", errors="replace")
        field, separator, value = line.partition(":")
        if separator and field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if not data_lines:
        return False
    try:
        payload = json.loads("\n".join(data_lines))
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(
        isinstance(payload, dict)
        and str(payload.get("method") or "").strip() == _NATIVE_CHANNEL_NOTIFICATION_METHOD
    )


def _forward_codex_mcp_split_proxy_sse(
    handler: BaseHTTPRequestHandler,
    response: Any,
    server_name: str,
) -> None:
    event = bytearray()
    while True:
        line = response.readline()
        if line:
            event.extend(line)
        if not line or line in {b"\n", b"\r\n"}:
            if event:
                raw_event = bytes(event)
                if _codex_mcp_split_proxy_is_channel_sse_event(raw_event):
                    router_log(
                        "INFO",
                        f"codex_mcp_split_proxy_channel_notification_suppressed name={server_name} source=post_sse",
                    )
                else:
                    handler.wfile.write(raw_event)
                    handler.wfile.flush()
                event.clear()
            if not line:
                break


def handle_codex_mcp_split_proxy_request(
    handler: BaseHTTPRequestHandler,
    path: str,
    raw_body: bytes,
    method: str,
) -> bool:
    resolved = codex_mcp_split_proxy_server(path)
    if resolved is None:
        return False
    name, server = resolved
    parsed = urllib.parse.urlparse(handler.path)
    upstream_url = _codex_mcp_split_proxy_upstream_url(server, parsed.query)
    headers = _codex_mcp_split_proxy_headers(handler, server)
    data = raw_body if method.upper() in {"POST", "PUT", "PATCH"} else None
    try:
        req = urllib.request.Request(upstream_url, data=data, headers=headers, method=method.upper())
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            handler.send_response(getattr(resp, "status", 200))
            _copy_upstream_response_headers(handler, resp.headers)
            handler.end_headers()
            content_type = str(resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type == "text/event-stream":
                _forward_codex_mcp_split_proxy_sse(handler, resp, name)
            else:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
        router_log("INFO", f"codex_mcp_split_proxy_forwarded name={name} method={method.upper()} upstream={upstream_url}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        handler.send_response(exc.code)
        _copy_upstream_response_headers(handler, exc.headers)
        handler.end_headers()
        if raw:
            handler.wfile.write(raw)
        router_log("WARN", f"codex_mcp_split_proxy_http_error name={name} method={method.upper()} status={exc.code}")
    except Exception as exc:
        if is_client_disconnect_error(exc):
            return True
        write_json(handler, {"error": {"message": f"{type(exc).__name__}: {exc}"}}, status=502)
        router_log("WARN", f"codex_mcp_split_proxy_failed name={name} method={method.upper()} error={type(exc).__name__}: {exc}")
    return True


def _http_error_body_text(exc: urllib.error.HTTPError) -> str:
    try:
        data = exc.read()
    except Exception:
        data = b""
    return data.decode("utf-8", errors="replace") if data else ""


def _streamable_http_session_not_found(exc: urllib.error.HTTPError, body_text: str = "") -> bool:
    text = f"{getattr(exc, 'reason', '')} {body_text}".strip().lower()
    return bool(
        exc.code == 404
        or "session-not-found" in text
        or "session not found" in text
        or ("session" in text and "not found" in text)
    )


def build_channel_session_lifecycle_services() -> ChannelSessionLifecycleServices:
    return ChannelSessionLifecycleServices(
        streamable_headers=_mcp_streamable_headers,
        http_error_body=_http_error_body_text,
        session_not_found=_streamable_http_session_not_found,
        records=_channel_streamable_session_records,
        forget=_forget_channel_streamable_session,
        log=router_log,
    )


def channel_streamable_sessions_path() -> Path:
    return CONFIG_DIR / "channel-streamable-sessions.json"


def build_channel_session_repository() -> ChannelSessionRepository:
    return ChannelSessionRepository(
        path=channel_streamable_sessions_path(),
        default_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
        log=router_log,
    )


def _channel_streamable_session_records() -> list[dict[str, Any]]:
    return build_channel_session_repository().records()


def _write_channel_streamable_session_records(records: list[dict[str, Any]]) -> None:
    build_channel_session_repository().write(records)


def _record_channel_streamable_session(name: str, url: str, session_id: str | None, protocol_version: str) -> None:
    build_channel_session_repository().record(name, url, session_id, protocol_version)


def _forget_channel_streamable_session(name: str, url: str, session_id: str | None) -> None:
    del name, url
    build_channel_session_repository().forget(session_id)


def _channel_streamable_http_delete_session(
    name: str,
    endpoint: str,
    headers: dict[str, str],
    protocol_version: str,
    session_id: str | None,
    reason: str,
    *,
    timeout: float = 5.0,
) -> bool:
    return delete_channel_session(
        name,
        endpoint,
        headers,
        protocol_version,
        session_id,
        reason,
        build_channel_session_lifecycle_services(),
        default_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
        timeout=timeout,
    )


def _channel_streamable_http_close_state_session(state: dict[str, Any], reason: str) -> bool:
    if str(state.get("transport") or "").strip().lower() not in {"http", "streamable-http"}:
        return True
    name = str(state.get("name") or "")
    endpoint = str(state.get("mcp_endpoint") or state.get("url") or "")
    session_id = str(state.get("mcp_session_id") or "").strip() or None
    headers = dict(state.get("headers") or {})
    protocol_version = str(state.get("mcp_protocol_version") or MCP_STREAMABLE_HTTP_PROTOCOL_VERSION)
    ok = _channel_streamable_http_delete_session(name, endpoint, headers, protocol_version, session_id, reason)
    if ok:
        with _CHANNEL_SSE_LOCK:
            current = _CHANNEL_SSE_CONNECTIONS.get(name)
            if current is state or (current and str(current.get("mcp_session_id") or "") == str(session_id or "")):
                current["mcp_session_id"] = None
                current["mcp_initialized"] = False
    return ok


def _channel_streamable_http_cleanup_stale_sessions(
    name: str,
    url: str,
    headers: dict[str, str],
    protocol_version: str,
    *,
    keep_session_id: str | None = None,
) -> None:
    cleanup_stale_channel_sessions(
        name,
        url,
        headers,
        protocol_version,
        build_channel_session_lifecycle_services(),
        default_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
        keep_session_id=keep_session_id,
    )


def _mcp_stream_read_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException) and reason is not exc:
        return _mcp_stream_read_timeout_error(reason)
    return "timed out" in str(exc).lower()












def channel_mcp_transport() -> ChannelMcpTransport:
    return ChannelMcpTransport(
        ChannelMcpTransportConfig(
            VERSION,
            MCP_LEGACY_SSE_PROTOCOL_VERSION,
            MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
            _NATIVE_ROUTER_CHANNEL_NAMES,
        ),
        ChannelMcpTransportState(_CHANNEL_SSE_CONNECTIONS, _CHANNEL_SSE_LOCK),
        ChannelMcpHttpPorts(
            legacy_post=_mcp_sse_post_json,
            streamable_post=_mcp_streamable_post_json,
            error_body=_http_error_body_text,
            session_not_found=_streamable_http_session_not_found,
            parse_bool=parse_bool,
        ),
        ChannelMcpEffects(
            set_state=_channel_sse_set_state,
            take_response=_channel_sse_take_rpc_response,
            mark_session_lost=_channel_streamable_http_mark_session_lost,
            absolute_endpoint=_channel_sse_absolute_endpoint,
            record_session=_record_channel_streamable_session,
            store_response=_channel_sse_store_rpc_response,
            project_payload=_sse_payload_to_chat_payload,
            append_message=append_chat_message,
            log=router_log,
        ),
    )


def _channel_sse_rpc_request(name: str, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    return channel_mcp_transport().rpc_request(name, method, params, timeout)


def _channel_sse_maybe_initialize_mcp(name: str, endpoint_text: str) -> None:
    channel_mcp_transport().maybe_initialize(name, endpoint_text)


def _channel_streamable_http_initialize_mcp(name: str) -> None:
    channel_mcp_transport().initialize_streamable(name)


def _channel_sse_dispatch(name: str, event_name: str, data_lines: list[str], event_id: str | None = None) -> None:
    channel_mcp_transport().dispatch(name, event_name, data_lines, event_id)


def _channel_connection_matches(state: dict[str, Any], connection_id: str | None) -> bool:
    if not connection_id:
        return True
    return str(state.get("connection_id") or "") == str(connection_id)


def _channel_worker_running(name: str, connection_id: str | None) -> bool:
    with _CHANNEL_SSE_LOCK:
        state = _CHANNEL_SSE_CONNECTIONS.get(name)
        return bool(state and state.get("running") and _channel_connection_matches(state, connection_id))


def channel_connection_worker() -> ChannelConnectionWorker:
    return ChannelConnectionWorker(
        state_store=ChannelWorkerStateStore(_CHANNEL_SSE_CONNECTIONS, _CHANNEL_SSE_LOCK),
        effects=ChannelWorkerEffects(
            log=router_log,
            dispatch=lambda name, event, data, event_id: _channel_sse_dispatch(
                name, event, data, event_id=event_id
            ),
            set_state=_channel_sse_set_state,
            initialize_streamable=_channel_streamable_http_initialize_mcp,
            close_state_session=_channel_streamable_http_close_state_session,
            streamable_headers=lambda headers, protocol, session, accept: _mcp_streamable_headers(
                headers, protocol, session, accept=accept
            ),
            session_not_found=_streamable_http_session_not_found,
            http_error_body=_http_error_body_text,
        ),
        policy=ChannelWorkerPolicy(
            streamable_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
            legacy_sse_protocol_version=MCP_LEGACY_SSE_PROTOCOL_VERSION,
            parse_bool=parse_bool,
        ),
    )


def _channel_sse_worker(name: str, connection_id: str | None = None) -> None:
    channel_connection_worker().run_sse(name, connection_id)


def _channel_streamable_http_worker(name: str, connection_id: str | None = None) -> None:
    channel_connection_worker().run_streamable_http(name, connection_id)


def channel_connection_lifecycle() -> ChannelConnectionLifecycle:
    return ChannelConnectionLifecycle(
        store=ChannelConnectionLifecycleStore(_CHANNEL_SSE_CONNECTIONS, _CHANNEL_SSE_LOCK),
        effects=ChannelConnectionLifecycleEffects(
            safe_segment=_safe_segment,
            close_session=_channel_streamable_http_close_state_session,
            cleanup_stale_sessions=_channel_streamable_http_cleanup_stale_sessions,
            public_status=_channel_sse_status_public,
            all_statuses=channel_sse_status,
            sse_worker=_channel_sse_worker,
            streamable_http_worker=_channel_streamable_http_worker,
        ),
        policy=ChannelConnectionLifecyclePolicy(
            streamable_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
            legacy_sse_protocol_version=MCP_LEGACY_SSE_PROTOCOL_VERSION,
            parse_bool=parse_bool,
        ),
    )


def start_channel_sse_connection(config: dict[str, Any]) -> dict[str, Any]:
    return channel_connection_lifecycle().start(config)


def stop_channel_sse_connection(name: str | None = None) -> dict[str, Any]:
    return channel_connection_lifecycle().stop(name)


def _channel_mcp_session_id() -> str:
    return f"s{os.getpid()}-{time.time_ns()}"


def channel_notification_projection() -> ChannelNotificationProjection:
    return ChannelNotificationProjection(
        ChannelNotificationConfig(
            _NATIVE_CHANNEL_NOTIFICATION_METHOD,
            _CHANNEL_CONTROL_KINDS,
        ),
        ChannelNotificationPorts(
            json_safe=_json_safe_metadata,
            string_list=_as_string_list,
            external_provenance=_channel_message_has_external_provenance,
            wake_noise_reason=_channel_wake_message_noise_reason,
            superseded_ids=_channel_superseded_message_ids,
            log=router_log,
        ),
    )


def _native_channel_meta_value(value: Any) -> str:
    return channel_notification_projection().meta_value(value)


def _native_channel_meta(message: dict[str, Any]) -> dict[str, str]:
    return channel_notification_projection().meta(message)


def _native_channel_param_value(value: Any) -> Any:
    return channel_notification_projection().param_value(value)


def _channel_mcp_notification(message: dict[str, Any]) -> dict[str, Any]:
    return channel_notification_projection().notification(message)


def _channel_mcp_capabilities() -> dict[str, Any]:
    return channel_notification_projection().capabilities()


def _write_sse_event(handler: BaseHTTPRequestHandler, event: str, data: Any, event_id: int | None = None) -> None:
    if event_id is not None:
        handler.wfile.write(f"id: {event_id}\n".encode("utf-8"))
    handler.wfile.write(f"event: {event}\n".encode("utf-8"))
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for line in payload.splitlines() or [""]:
        handler.wfile.write(f"data: {line}\n".encode("utf-8"))
    handler.wfile.write(b"\n")
    handler.wfile.flush()


def _send_channel_mcp_sse_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("content-type", "text/event-stream")
    handler.send_header("cache-control", "no-cache, no-transform")
    handler.send_header("connection", "keep-alive")
    handler.send_header("x-accel-buffering", "no")
    handler.end_headers()


def _channel_mcp_enqueue(session: str, payload: dict[str, Any]) -> bool:
    if not session:
        return False
    with _CHANNEL_MCP_LOCK:
        state = _CHANNEL_MCP_SESSIONS.get(session)
        if not state:
            return False
        outbox = state.setdefault("outbox", [])
        if isinstance(outbox, list):
            outbox.append(payload)
        else:
            state["outbox"] = [payload]
    with _CHAT_CONDITION:
        _CHAT_CONDITION.notify_all()
    return True


def _channel_mcp_take_outbox(session: str) -> list[dict[str, Any]]:
    with _CHANNEL_MCP_LOCK:
        state = _CHANNEL_MCP_SESSIONS.get(session)
        if not state:
            return []
        outbox = state.get("outbox")
        if not isinstance(outbox, list) or not outbox:
            return []
        state["outbox"] = []
        return [item for item in outbox if isinstance(item, dict)]


def _channel_mcp_initialize_response(request_id: Any, protocol: str) -> dict[str, Any]:
    # This endpoint implements the legacy HTTP+SSE transport, whose stable
    # protocol version is 2024-11-05 even when newer clients initiate the
    # handshake with a newer preferred protocol.
    protocol = "2024-11-05"
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": protocol,
            "capabilities": _channel_mcp_capabilities(),
            "serverInfo": {"name": "ciel-runtime-router", "version": VERSION},
        },
    }


def _channel_compact_request_ttl_seconds() -> float:
    return compact_request_ttl(
        os.environ.get("CIEL_RUNTIME_CHANNEL_COMPACT_REQUEST_TTL_SECONDS")
    )


def channel_compact_request_repository() -> ChannelCompactRequestRepository:
    artifact = json_artifact_repository(CHANNEL_COMPACT_REQUEST_PATH)
    return ChannelCompactRequestRepository(
        CHANNEL_COMPACT_REQUEST_PATH,
        _CHANNEL_COMPACT_REQUEST_LOCK,
        save=artifact.save,
        truncate=truncate_for_prompt,
        log=router_log,
        ttl=_channel_compact_request_ttl_seconds,
    )


def _channel_compact_request_payload(source: str, reason: str) -> dict[str, Any]:
    return channel_compact_request_repository().payload(source, reason)


def _write_channel_compact_request(source: str = "mcp", reason: str = "") -> dict[str, Any]:
    return channel_compact_request_repository().queue(source, reason)


def _read_channel_compact_request() -> dict[str, Any] | None:
    return channel_compact_request_repository().read()


def _clear_channel_compact_request(request_id: str | None = None) -> bool:
    return channel_compact_request_repository().clear(request_id)


def _channel_mcp_tool_schemas() -> list[dict[str, Any]]:
    return channel_mcp_tool_schemas()


def _channel_mcp_tool_response(request_id: Any, text: str, is_error: bool = False) -> dict[str, Any]:
    return channel_mcp_tool_response(request_id, text, is_error)


def _channel_mcp_tool_call_response(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    return dispatch_channel_mcp_tool(
        request_id,
        params,
        ChannelMcpToolServices(
            queue_compact=_write_channel_compact_request,
            append_message=append_chat_message,
            store_file_path=store_chat_file_from_path,
            store_file_upload=store_chat_file_upload,
            file_message_text=chat_file_message_text,
            handle_llm_options=handle_live_llm_options_action,
        ),
    )


def channel_cursor_repository(path: Path) -> ChannelCursorRepository:
    return ChannelCursorRepository(path=path, log=router_log)


def _channel_mcp_cached_cursor() -> int | None:
    return _CHANNEL_MCP_CURSOR_LAST_ID


def _channel_mcp_cache_cursor(last_id: int) -> None:
    global _CHANNEL_MCP_CURSOR_LAST_ID
    _CHANNEL_MCP_CURSOR_LAST_ID = last_id


def channel_mcp_cursor_service() -> ChannelCursorService:
    return ChannelCursorService(
        ChannelCursorServices(
            repository=channel_cursor_repository(CHANNEL_MCP_CURSOR_PATH),
            lock=_CHANNEL_MCP_CURSOR_LOCK,
            cached=_channel_mcp_cached_cursor,
            cache=_channel_mcp_cache_cursor,
            scan_tail=_chat_scan_max_id,
        )
    )


def _channel_mcp_write_cursor_locked(last_id: int) -> None:
    channel_cursor_repository(CHANNEL_MCP_CURSOR_PATH).write(last_id)


def _channel_mcp_read_cursor_locked() -> int:
    return channel_mcp_cursor_service().read_locked()


def _channel_mcp_ensure_cursor_initialized() -> int:
    return channel_mcp_cursor_service().ensure_initialized()


def _channel_mcp_update_cursor(last_id: int) -> None:
    channel_mcp_cursor_service().update(last_id)


def _channel_mcp_parse_event_id(value: Any) -> int | None:
    return parse_channel_event_id(value)


def channel_mcp_resume_policy() -> ChannelResumePolicy:
    return ChannelResumePolicy(
        ChannelResumeServices(
            query_params=_query_params,
            first_param=_first_param,
            ensure_cursor=_channel_mcp_ensure_cursor_initialized,
            update_cursor=_channel_mcp_update_cursor,
            log=router_log,
        )
    )


def _channel_mcp_client_last_event_id(handler: BaseHTTPRequestHandler) -> int | None:
    return channel_mcp_resume_policy().client_last_event_id(handler)


def _channel_mcp_session_start_last_id(handler: BaseHTTPRequestHandler) -> int:
    return channel_mcp_resume_policy().session_start_last_id(handler)


def _channel_mcp_message_skip_reason(message: dict[str, Any]) -> str | None:
    return channel_notification_projection().skip_reason(message)


def _channel_mcp_notifications_for_messages(
    messages: list[dict[str, Any]], after_id: int
) -> list[tuple[int, dict[str, Any]]]:
    return channel_notification_projection().notifications_for_messages(messages, after_id)


def channel_mcp_http_controller() -> ChannelMcpHttpController:
    return ChannelMcpHttpController(
        store=ChannelMcpSessionStore(_CHANNEL_MCP_SESSIONS, _CHANNEL_MCP_LOCK),
        stream=ChannelMcpStreamServices(
            new_session_id=_channel_mcp_session_id,
            start_last_id=_channel_mcp_session_start_last_id,
            send_headers=_send_channel_mcp_sse_headers,
            write_event=_write_sse_event,
            take_outbox=_channel_mcp_take_outbox,
            read_messages=read_chat_messages,
            project_notifications=_channel_mcp_notifications_for_messages,
            update_cursor=_channel_mcp_update_cursor,
            condition=_CHAT_CONDITION,
            log=router_log,
        ),
        rpc=ChannelMcpRpcServices(
            initialize_response=_channel_mcp_initialize_response,
            tool_schemas=_channel_mcp_tool_schemas,
            tool_call_response=_channel_mcp_tool_call_response,
            enqueue=_channel_mcp_enqueue,
            write_json=write_json,
            write_accepted=write_accepted_response,
            log=router_log,
        ),
    )


def handle_channel_mcp_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    return channel_mcp_http_controller().get(handler, path)


def handle_channel_mcp_post(handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
    return channel_mcp_http_controller().post(handler, path, body)




def _query_params(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query, keep_blank_values=True)


def _first_param(params: dict[str, list[str]], name: str, default: str = "") -> str:
    values = params.get(name)
    return values[0] if values else default


def chat_http_controller() -> ChatHttpController:
    return ChatHttpController(
        router_base=ROUTER_BASE,
        reads=ChatHttpReadServices(
            read_after=read_chat_messages,
            read_before=read_chat_messages_before,
            condition=_CHAT_CONDITION,
            connection_statuses=channel_sse_status,
            safe_segment=_safe_segment,
            files_dir=CHAT_FILES_DIR,
        ),
        writes=ChatHttpWriteServices(
            write_json=write_json,
            append_message=append_chat_message,
            store_upload=store_chat_file_upload,
            start_connection=start_channel_sse_connection,
            stop_connection=stop_channel_sse_connection,
        ),
    )


def handle_chat_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    return chat_http_controller().get(handler, path)


def handle_chat_post(handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
    return chat_http_controller().post(handler, path, body)




def plan_artifact_controller() -> PlanArtifactController:
    return PlanArtifactController(
        PlanArtifactServices(
            directory=PLAN_ARTIFACTS_DIR,
            router_base=ROUTER_BASE,
            safe_segment=_safe_segment,
            write_json=write_json,
            write_text=write_text_response,
            announce=append_chat_message,
        )
    )


def handle_plan_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    return plan_artifact_controller().get(handler, path)


def handle_plan_post(handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
    return plan_artifact_controller().post(handler, path, body)




def estimate_tokens(body: Any, _cache: dict[int, int] | None = None) -> int:
    if _cache is not None:
        body_id = id(body)
        if body_id in _cache:
            return _cache[body_id]
    text = json.dumps(body, ensure_ascii=False)
    result = max(1, len(text) // 4)
    if _cache is not None:
        _cache[id(body)] = result
    return result


def anthropic_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(str(block.get("text", "")))
        elif btype == "tool_result":
            tool_text = anthropic_content_to_text(block.get("content", ""))
            parts.append(f"Tool result for {block.get('tool_use_id', 'tool')}:\n{tool_text}")
    return "\n".join(part for part in parts if part)


COMPACT_TEXT_ONLY_SYSTEM_PROMPT = (
    "Claude Code is compacting the conversation. Return only the requested summary text. "
    "Do not call tools, browse, inspect files, or request external data during compaction."
)


def is_claude_code_compact_request(body: dict[str, Any]) -> bool:
    """Detect Claude Code's internal /compact summarization request.

    Compact requests must produce text. If an upstream model sees the normal
    tool list and chooses a tool instead, Claude Code reports an empty compact
    summary even though the router returned HTTP 200.
    """
    return context_summary_policy().is_compact_request(body)


def compact_request_text_only_body(body: dict[str, Any]) -> dict[str, Any]:
    return context_summary_policy().text_only_body(
        body,
        COMPACT_TEXT_ONLY_SYSTEM_PROMPT,
        append_anthropic_system_texts,
        router_log,
    )


PROMPT_TOOL_INPUT_FIELD_LIMIT = 1200
PROMPT_TOOL_RESULT_LIMIT = 12000
PROMPT_MESSAGE_TEXT_LIMIT = 20000
CLAUDE_CODE_PERSISTED_OUTPUT_MARKER = "<persisted-output>"


def context_summary_policy() -> ContextSummaryPolicy:
    return ContextSummaryPolicy(
        estimate_tokens,
        positive_int,
        anthropic_content_to_text,
        _compact_json_for_prompt,
        latest_user_text,
    )


def truncate_for_prompt(text: str, limit: int) -> str:
    return ContextSummaryPolicy.truncate(text, limit)


def is_claude_code_persisted_output_text(text: str) -> bool:
    return ContextSummaryPolicy.is_persisted_output(text)


def compact_tool_value_for_prompt(value: Any, limit: int = PROMPT_TOOL_INPUT_FIELD_LIMIT) -> Any:
    return context_summary_policy().compact_tool_value(value, limit)


def tool_input_for_prompt(tool_input: Any) -> str:
    return context_summary_policy().tool_input(tool_input)


def compact_message_text_for_prompt(text: str) -> str:
    return context_summary_policy().message_text(text)


def _message_tool_markers_for_summary(message: dict[str, Any]) -> list[str]:
    return ContextSummaryPolicy.tool_markers(message)


def compact_message_summary_line(index: int, message: dict[str, Any], *, text_limit: int = 700) -> str:
    return context_summary_policy().summary_line(index, message, text_limit)


def _compact_chunk_ranges(count: int, chunks: int) -> list[tuple[int, int]]:
    return ContextSummaryPolicy.chunk_ranges(count, chunks)


def context_guard_chunk_count(omitted_messages: list[dict[str, Any]], budget_tokens: int | None = None) -> int:
    return context_summary_policy().guard_chunk_count(omitted_messages, budget_tokens)


def build_chunked_context_guard_summary(
    omitted_messages: list[dict[str, Any]], budget_tokens: int, *, start_index: int = 0
) -> str:
    return context_summary_policy().guard_summary(omitted_messages, budget_tokens, start_index)


def context_compact_message_text(message: dict[str, Any], index: int) -> str:
    return context_summary_policy().compact_message(message, index)


def context_compact_instruction_index(messages: list[dict[str, Any]]) -> int | None:
    return context_summary_policy().instruction_index(messages)


def context_compact_chunk_target_tokens(pcfg: dict[str, Any] | None, budget_tokens: int) -> int:
    return context_summary_policy().chunk_target_tokens(pcfg, budget_tokens)


def context_compact_summary_output_tokens(pcfg: dict[str, Any] | None, budget_tokens: int) -> int:
    return context_summary_policy().summary_output_tokens(pcfg, budget_tokens)


def context_compact_parallel_sessions(pcfg: dict[str, Any] | None, chunks: int) -> int:
    return 1


def split_messages_for_context_compact(
    messages: list[dict[str, Any]], target_tokens: int
) -> list[tuple[int, list[dict[str, Any]]]]:
    return context_summary_policy().split_messages(messages, target_tokens)




CONTEXT_COMPACT_MAP_SYSTEM_PROMPT = (
    "You are compacting one segment of a larger Claude Code conversation. "
    "Return only a concise but durable summary of this segment. Preserve user goals, "
    "decisions, file paths, tool results, unresolved tasks, errors, and any facts needed "
    "to continue later. Do not call tools."
)


def build_context_compact_chunk_prompt(chunk: list[dict[str, Any]], start_index: int, chunk_no: int, chunk_total: int) -> str:
    return context_summary_policy().chunk_prompt(chunk, start_index, chunk_no, chunk_total)


def context_compact_extract_text(data: Any, wire: str) -> str:
    return context_summary_policy().extract_response_text(data, wire)


def context_compaction_available(provider: str, pcfg: dict[str, Any]) -> bool:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.context_compaction_available(provider_contract_config(provider, pcfg))


def context_compaction_services() -> ContextCompactionServices:
    return ContextCompactionServices(
        transport=ContextCompactionTransport(
            summary_output_tokens=context_compact_summary_output_tokens,
            request_timeout=provider_request_timeout_seconds,
            endpoint=provider_endpoint,
            post_json=post_json_with_rate_retry,
            headers=provider_headers,
            extract_text=context_compact_extract_text,
            native_compat_enabled=provider_native_compat_enabled,
            native_anthropic_base=native_anthropic_base_url,
            upstream_base=provider_upstream_request_base,
            join_url=join_url,
        ),
        workflow=ContextCompactionWorkflow(
            parse_bool=parse_bool,
            compaction_available=context_compaction_available,
            instruction_index=context_compact_instruction_index,
            content_to_text=anthropic_content_to_text,
            chunk_target_tokens=context_compact_chunk_target_tokens,
            split_messages=split_messages_for_context_compact,
            parallel_sessions=context_compact_parallel_sessions,
            write_activity=write_context_compact_activity,
            estimate_tokens=estimate_tokens,
            request_summary=context_compact_request_summary,
        ),
        projection=ContextCompactionProjection(
            build_chunk_prompt=build_context_compact_chunk_prompt,
            build_fallback_summary=build_chunked_context_guard_summary,
            build_reduce_prompt=build_context_compact_reduce_prompt,
            log=router_log,
        ),
        map_system_prompt=CONTEXT_COMPACT_MAP_SYSTEM_PROMPT,
    )


def context_compact_request_summary(
    provider: str,
    model: str,
    pcfg: dict[str, Any],
    prompt: str,
    *,
    wire: str,
    budget_tokens: int,
) -> str:
    return request_context_summary(
        provider,
        model,
        pcfg,
        prompt,
        context_compaction_services(),
        wire=wire,
        budget_tokens=budget_tokens,
    )


def build_context_compact_reduce_prompt(
    summaries: list[str],
    compact_instruction: str,
    *,
    budget_tokens: int,
    source_message_count: int,
) -> str:
    return context_summary_policy().reduce_prompt(
        summaries,
        compact_instruction,
        budget_tokens,
        source_message_count,
    )


def maybe_build_llm_compacted_messages(
    provider: str,
    model: str,
    pcfg: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    budget_tokens: int,
    *,
    wire: str,
) -> list[dict[str, Any]] | None:
    return build_llm_compacted_messages(
        provider,
        model,
        pcfg,
        messages,
        budget_tokens,
        context_compaction_services(),
        wire=wire,
    )






def shortcut_text_services() -> ShortcutTextServices:
    return ShortcutTextServices(latest_user_text=latest_user_text)


def latest_user_text_has_marker(body: dict[str, Any], markers: tuple[str, ...]) -> bool:
    return project_has_request_marker(body, markers, shortcut_text_services())


def latest_user_text_marker_tail(body: dict[str, Any], markers: tuple[str, ...]) -> str:
    return project_request_marker_tail(body, markers, shortcut_text_services())


def router_debug_value_from_body(body: dict[str, Any]) -> str:
    return project_single_shortcut_value(
        body, ROUTER_DEBUG_REQUEST_MARKERS, shortcut_text_services(),
        empty_default="status", blank_value_default="toggle",
    )


def channel_clear_value_from_body(body: dict[str, Any]) -> str:
    return project_single_shortcut_value(
        body, CHANNEL_CLEAR_REQUEST_MARKERS, shortcut_text_services(),
        empty_default="all", blank_value_default="all",
    )


def live_llm_options_value_from_body(body: dict[str, Any]) -> str:
    return project_live_option_value(body, LIVE_LLM_OPTIONS_REQUEST_MARKERS, shortcut_text_services())


def live_api_keys_value_from_body(body: dict[str, Any]) -> str:
    return project_live_api_keys_value(body, LIVE_API_KEYS_REQUEST_MARKERS, shortcut_text_services())


def _split_import_session_arguments(value: str) -> tuple[str, str]:
    return project_split_import_session_arguments(value, posix=os.name != "nt")


def import_session_args_from_body(body: dict[str, Any]) -> tuple[str, str]:
    return project_import_session_args(
        body, IMPORT_SESSION_REQUEST_MARKERS, shortcut_text_services(), posix=os.name != "nt"
    )


def is_advisor_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, ADVISOR_REQUEST_MARKERS)


def is_router_debug_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, ROUTER_DEBUG_REQUEST_MARKERS)


def is_version_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, VERSION_REQUEST_MARKERS)


def is_channel_clear_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, CHANNEL_CLEAR_REQUEST_MARKERS)


def is_live_llm_options_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, LIVE_LLM_OPTIONS_REQUEST_MARKERS)


def is_live_api_keys_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, LIVE_API_KEYS_REQUEST_MARKERS)


def is_import_session_request(body: dict[str, Any]) -> bool:
    return latest_user_text_has_marker(body, IMPORT_SESSION_REQUEST_MARKERS)






















def advisor_focus_from_body(body: dict[str, Any]) -> str:
    text = latest_user_text(body)
    marker = "CIEL_RUNTIME_ADVISOR_CALL"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].strip()


def advisor_model_enabled(pcfg: dict[str, Any]) -> str:
    return str(pcfg.get("advisor_model") or "").strip()


def advisor_provider_kind(provider: str, pcfg: dict[str, Any] | None = None) -> str:
    del pcfg
    return PROVIDER_COMPATIBILITY.resolve(provider).advisor_transport


def advisor_provider_supported(provider: str) -> bool:
    return bool(advisor_provider_kind(provider))


def anthropic_system_with_advisor(system: Any, extra_system_texts: list[str] | None = None) -> list[dict[str, Any]]:
    """Build the advisor request system blocks, keeping the session identity first.

    Anthropic rejects OAuth-authenticated requests whose first system block is
    not the original Claude Code identity block (HTTP 429 ``rate_limit_error``
    with message "Error"), so the inbound session's first system block stays
    first and verbatim; the advisor instruction rides behind it.
    """
    blocks: list[dict[str, Any]] = []
    original_text = ""
    if isinstance(system, str):
        clean = system.strip()
        if clean:
            blocks.append({"type": "text", "text": clean})
    elif isinstance(system, list) and system:
        first = system[0]
        if isinstance(first, dict) and str(first.get("type") or "") == "text" and str(first.get("text") or "").strip():
            blocks.append(dict(first))
            original_text = anthropic_content_to_text(system[1:]).strip()
        else:
            original_text = anthropic_content_to_text(system).strip()
    elif system:
        original_text = anthropic_content_to_text(system).strip()
    blocks.append({"type": "text", "text": ADVISOR_REVIEW_PROMPT})
    if original_text:
        blocks.append({"type": "text", "text": "Original session system context:\n" + original_text})
    for text in extra_system_texts or []:
        clean = str(text or "").strip()
        if clean:
            blocks.append({"type": "text", "text": "Additional system context from message history:\n" + clean})
    return blocks


def append_anthropic_system_texts(system: Any, extra_system_texts: list[str] | None = None) -> Any:
    """Compatibility export; protocol policy lives in ``prompt_injection``."""

    return project_append_anthropic_system_texts(system, extra_system_texts)


def normalize_anthropic_system_role_messages(body: dict[str, Any]) -> dict[str, Any]:
    """Move non-standard ``messages[].role == "system"`` entries to top-level system.

    Claude Code can include runtime state as a system-role item in message
    history. Anthropic-compatible /v1/messages servers such as vLLM accept
    only user/assistant roles in ``messages`` and expect system context at the
    top level.
    """
    return project_normalize_anthropic_system_role_messages(body, anthropic_content_to_text)


def pseudo_tool_history_services() -> PseudoToolHistoryServices:
    return PseudoToolHistoryServices(
        tool_names=tool_names_in_body,
        match_tool_name=_match_available_tool_name,
        resolve_emitted_name=resolve_emitted_tool_name,
        normalize_arguments=normalize_tool_arguments,
        log=router_log,
    )


def _find_pseudo_xml_tool_start(text: str, source_body: dict[str, Any] | None) -> int:
    return find_pseudo_xml_tool_start(text, source_body, pseudo_tool_history_services())


def _parse_xml_pseudo_tool_calls(
    text: str,
    source_body: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    return parse_xml_pseudo_tool_calls(text, source_body, pseudo_tool_history_services())


def sanitize_assistant_pseudo_tool_text_history(body: dict[str, Any]) -> dict[str, Any]:
    return sanitize_assistant_pseudo_tool_history(body, pseudo_tool_history_services())


def _historical_tool_use_as_text(block: dict[str, Any]) -> dict[str, str]:
    tool_id = str(block.get("id") or "missing-id")
    name = str(block.get("name") or "tool")
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    input_text = truncate_for_prompt(json.dumps(tool_input, ensure_ascii=False, sort_keys=True), 2000)
    return {
        "type": "text",
        "text": (
            "[ciel-runtime preserved a historical tool request as text because its matching "
            f"tool_result is not present in the retained transcript. tool={name} id={tool_id} input={input_text}]"
        ),
    }


def _historical_tool_result_as_text(block: dict[str, Any]) -> dict[str, str]:
    tool_id = str(block.get("tool_use_id") or "unknown")
    text = truncate_for_prompt(anthropic_content_to_text(block.get("content")), PROMPT_TOOL_RESULT_LIMIT)
    return {
        "type": "text",
        "text": (
            "[ciel-runtime preserved a historical tool result as text because its matching "
            f"assistant tool_use is not present in the retained transcript. tool_use_id={tool_id}]\n{text}"
        ),
    }


def normalize_anthropic_tool_turns_for_provider(
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    policy = provider_request_policy(provider, pcfg)
    if not policy.normalize_historical_tool_turns:
        return body
    return normalize_historical_anthropic_tool_turns(
        provider,
        body,
        AnthropicToolTurnServices(
            tool_use_as_text=_historical_tool_use_as_text,
            tool_result_as_text=_historical_tool_result_as_text,
            log=router_log,
        ),
    )


def normalize_request_for_provider_wire(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return normalize_provider_request(
        provider, pcfg, body,
        services=ProviderRequestServices(
            apply_provider_adapter_request_policy=apply_provider_adapter_request_policy,
            normalize_anthropic_system_role_messages=normalize_anthropic_system_role_messages,
            normalize_anthropic_tool_turns_for_provider=normalize_anthropic_tool_turns_for_provider,
            normalize_thinking_for_non_anthropic_provider=normalize_thinking_for_non_anthropic_provider,
            normalize_tool_choice_for_provider=normalize_tool_choice_for_provider,
            provider_wire_profile=provider_wire_profile,
            sanitize_assistant_pseudo_tool_text_history=sanitize_assistant_pseudo_tool_text_history
        ),
    )


def advisor_services() -> AdvisorServices:
    return AdvisorServices(
        text=AdvisorTextServices(
            content_to_text=anthropic_content_to_text,
            tool_input_for_prompt=tool_input_for_prompt,
        ),
        decisions=AdvisorDecisionServices(
            advisor_enabled=advisor_model_enabled,
            is_advisor_request=is_advisor_request,
            completed_work=latest_tool_result_indicates_completed_work,
            non_actionable_response=non_actionable_short_response,
            plan_mode_active=plan_mode_active,
            provider_supported=advisor_provider_supported,
        ),
        log=router_log,
        feedback_marker=ADVISOR_FEEDBACK_MARKER,
    )


def anthropic_advisor_messages_and_system(body: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    return project_advisor_messages_and_system(body, advisor_services())


def advisor_tool_schema() -> dict[str, Any]:
    return project_advisor_tool_schema()


def body_with_advisor_tool(body: dict[str, Any], pcfg: dict[str, Any]) -> dict[str, Any]:
    return project_body_with_advisor_tool(body, pcfg, advisor_services())


def is_claude_code_advisor_server_tool(tool: Any) -> bool:
    return project_is_advisor_server_tool(tool)


def strip_autonomous_advisor_server_tools(provider: str, body: dict[str, Any]) -> dict[str, Any]:
    adapter = PROVIDER_ADAPTERS.create(provider)
    provider_config = ProviderConfig(name=provider, base_url='', model='')
    return project_strip_advisor_server_tools(
        body,
        advisor_services(),
        server_tool_supported=adapter.supports_server_advisor_tool(provider_config),
    )


def advisor_tool_focus_from_message(message: dict[str, Any]) -> str | None:
    return project_advisor_tool_focus(message)


def tool_review_context_from_message(message: dict[str, Any], trigger: str) -> str:
    return project_tool_review_context(message, trigger, advisor_services())


def advisor_focus_for_message(message: dict[str, Any], trigger: str | None) -> tuple[str | None, str | None]:
    return project_advisor_focus(message, trigger, advisor_services())


def assistant_tool_call_summary_for_prompt(message: dict[str, Any]) -> str:
    return project_assistant_tool_summary(message, advisor_services())


def body_has_advisor_feedback(body: dict[str, Any]) -> bool:
    return project_body_has_advisor_feedback(body, advisor_services())


def anthropic_message_tool_names(message: dict[str, Any]) -> list[str]:
    return project_anthropic_message_tool_names(message)


def advisor_trigger_for_message(body: dict[str, Any], message: dict[str, Any]) -> str | None:
    return project_advisor_trigger(body, message, advisor_services())


def advisor_gate_possible_for_body(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    return bool(advisor_gate_reason_for_body(provider, pcfg, body))


def advisor_gate_reason_for_body(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> str:
    return project_advisor_gate_reason(provider, pcfg, body, advisor_services())





















def conversation_policy_services() -> ConversationPolicyServices:
    return ConversationPolicyServices(
        content_blocks=_message_content_blocks,
        content_to_text=anthropic_content_to_text,
        plan_mode_active=plan_mode_active,
        persisted_output=is_claude_code_persisted_output_text,
        transcript_event=is_claude_code_transcript_event,
        guard_feedback=is_guard_feedback_text,
    )


def is_attachment_only_message(message: dict[str, Any]) -> bool:
    return project_is_attachment_only_message(message, conversation_policy_services())


def plan_file_written_in_body(body: dict[str, Any], plan_file_path: str) -> bool:
    return project_plan_file_written_in_body(body, plan_file_path, conversation_policy_services())


def claude_code_state_messages(body: dict[str, Any]) -> list[dict[str, str]]:
    return project_claude_code_state_messages(body, conversation_policy_services())


def upstream_relevant_message(message: dict[str, Any]) -> bool:
    return project_upstream_relevant_message(message, conversation_policy_services())


def collect_tool_result_context(body: dict[str, Any]) -> tuple[dict[str, str], set[str]]:
    return project_collect_tool_result_context(body, conversation_policy_services())


def should_skip_upstream_message(message: dict[str, Any]) -> bool:
    return project_should_skip_upstream_message(message, conversation_policy_services())


def format_tool_result_for_upstream(
    tool_name: str,
    tool_input_text: str,
    result_text: str,
    is_error: bool,
    prior_success_text: str = "",
    include_prior_success: bool = False,
    in_plan_mode: bool = False,
) -> tuple[str, str]:
    return project_tool_result(
        tool_name,
        tool_input_text,
        result_text,
        is_error,
        ToolResultProjectionServices(
            is_read_unchanged=is_read_unchanged_result,
            truncate=truncate_for_prompt,
            result_limit=PROMPT_TOOL_RESULT_LIMIT,
        ),
        prior_success_text=prior_success_text,
        include_prior_success=include_prior_success,
        in_plan_mode=in_plan_mode,
    )




def chat_projection_services() -> ChatProjectionServices:
    return ChatProjectionServices(
        text=ChatProjectionText(
            system_messages=anthropic_system_to_ollama_messages,
            execution_reminder=ollama_claude_code_reminder,
            state_messages=claude_code_state_messages,
            content_to_text=anthropic_content_to_text,
            compact_text=compact_message_text_for_prompt,
            skip_message=should_skip_upstream_message,
            attachment_only=is_attachment_only_message,
        ),
        tools=ChatProjectionTools(
            collect_result_context=collect_tool_result_context,
            plan_mode_active=plan_mode_active,
            input_for_prompt=tool_input_for_prompt,
            persisted_output=is_claude_code_persisted_output_text,
            truncate_for_prompt=truncate_for_prompt,
            canonical_signature=canonical_tool_signature,
            format_result=format_tool_result_for_upstream,
        ),
        policy=ChatProjectionPolicy(
            thinking_block_types=ANTHROPIC_THINKING_BLOCK_TYPES,
            tool_result_limit=PROMPT_TOOL_RESULT_LIMIT,
        ),
    )


def anthropic_messages_to_ollama(body: dict[str, Any]) -> list[dict[str, Any]]:
    return project_anthropic_messages_to_ollama(body, services=chat_projection_services())




def anthropic_messages_to_openai(body: dict[str, Any], reasoning_passback: bool = False) -> list[dict[str, Any]]:
    return project_anthropic_messages_to_openai(
        body,
        reasoning_passback,
        services=chat_projection_services(),
    )


def missing_openai_tool_result_message(tool_call: dict[str, Any]) -> dict[str, Any]:
    return project_missing_openai_tool_result_message(tool_call)


def orphan_openai_tool_message_to_user(message: dict[str, Any]) -> dict[str, str]:
    return project_orphan_openai_tool_message_to_user(message)


def repair_openai_tool_call_adjacency(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return project_repair_openai_tool_call_adjacency(
        messages,
        OpenAiHistoryServices(log=router_log),
    )


def anthropic_tool_choice_to_openai(tool_choice: Any) -> Any:
    if not isinstance(tool_choice, dict):
        return tool_choice
    choice_type = tool_choice.get("type")
    if choice_type == "tool" and tool_choice.get("name"):
        return {"type": "function", "function": {"name": str(tool_choice["name"])}}
    if choice_type == "any":
        return "required"
    if choice_type == "auto":
        return "auto"
    return tool_choice


def opencode_model_id_hint(provider: str, pcfg: dict[str, Any], model: str | None) -> str:
    requested = strip_claude_context_suffix(model).strip()
    fallback = normalize_model_id(provider, pcfg.get("current_model") or "")
    prefix = f"ciel-runtime-{provider}-"
    if requested.startswith(prefix):
        return requested[len(prefix):]
    if requested.startswith("ciel-runtime-"):
        return fallback
    return normalize_model_id(provider, requested or fallback)


def openai_chat_reasoning_passback_enabled(provider: str, model: str | None, pcfg: dict[str, Any]) -> bool:
    if provider not in OPENCODE_PROVIDER_NAMES:
        return False
    model_id = opencode_model_id_hint(provider, pcfg, model).strip().lower()
    if not model_id.startswith("deepseek-"):
        return False
    return opencode_endpoint_kind(provider, model_id, pcfg) == "openai-chat"


def openai_chat_reasoning_passback_enabled_for_body(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    return openai_chat_reasoning_passback_enabled(provider, str(body.get("model") or ""), pcfg)


def openai_reasoning_to_anthropic_thinking_block(reasoning_content: Any) -> dict[str, Any] | None:
    reasoning = str(reasoning_content or "")
    if not reasoning:
        return None
    digest = hashlib.sha256(reasoning.encode("utf-8", errors="replace")).hexdigest()[:24]
    return {
        "type": "thinking",
        "thinking": reasoning,
        "signature": f"ciel-runtime-openai-reasoning-{digest}",
    }


VISIBLE_THINKING_MARKUP_TAG_RE = re.compile(r"</?think(?:ing)?\b[^>]*>", re.I)
VISIBLE_THINKING_MARKUP_PREFIXES = ("<think", "</think", "<thinking", "</thinking")


def _visible_thinking_markup_partial_start(text: str) -> int:
    lt = text.rfind("<")
    if lt < 0 or lt <= text.rfind(">"):
        return -1
    suffix = text[lt:].lower()
    if any(prefix.startswith(suffix) or suffix.startswith(prefix) for prefix in VISIBLE_THINKING_MARKUP_PREFIXES):
        return lt
    return -1


class VisibleThinkingMarkupFilter:
    def __init__(self) -> None:
        self.in_thinking = False
        self.pending = ""

    def feed(self, text: Any) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        value = self.pending + raw
        self.pending = ""
        partial_start = _visible_thinking_markup_partial_start(value)
        if partial_start >= 0:
            self.pending = value[partial_start:]
            value = value[:partial_start]
        return self._strip_complete_tags(value)

    def finish(self) -> str:
        pending = self.pending
        self.pending = ""
        if not pending or self.in_thinking or _visible_thinking_markup_partial_start(pending) == 0:
            self.in_thinking = False
            return ""
        return self._strip_complete_tags(pending)

    def _strip_complete_tags(self, text: str) -> str:
        out: list[str] = []
        pos = 0
        for match in VISIBLE_THINKING_MARKUP_TAG_RE.finditer(text):
            tag = match.group(0).lstrip().lower()
            closing = tag.startswith("</")
            if self.in_thinking:
                if closing:
                    self.in_thinking = False
                pos = match.end()
                continue
            out.append(text[pos:match.start()])
            pos = match.end()
            if not closing:
                self.in_thinking = True
        if not self.in_thinking:
            out.append(text[pos:])
        return "".join(out)


def strip_visible_thinking_markup(text: Any) -> str:
    raw = str(text or "")
    if "<think" not in raw.lower() and "</think" not in raw.lower():
        return raw
    filter_state = VisibleThinkingMarkupFilter()
    return (filter_state.feed(raw) + filter_state.finish()).lstrip()


VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE = re.compile(
    r"(?s)(?:^|(?:\r?\n){1,3})[ \t]*call[ \t]*(?:\r?\n)[ \t]*ignore[ \t]*(?:\r?\n)?[ \t]*$"
)
VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS = 96


def strip_visible_tool_call_artifact_suffix(text: Any) -> str:
    raw = str(text or "")
    if "call" not in raw or "ignore" not in raw:
        return raw
    return VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE.sub("", raw)


class VisibleToolCallArtifactFilter:
    def __init__(self, hold_chars: int = VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS) -> None:
        self.hold_chars = max(16, int(hold_chars))
        self.pending = ""
        self.stripped = False

    def feed(self, text: Any) -> str:
        raw = str(text or "")
        if not raw:
            return ""
        value = self.pending + raw
        if len(value) <= self.hold_chars:
            self.pending = value
            return ""
        emit_len = len(value) - self.hold_chars
        self.pending = value[emit_len:]
        return value[:emit_len]

    def finish(self) -> str:
        pending = self.pending
        self.pending = ""
        stripped = strip_visible_tool_call_artifact_suffix(pending)
        self.stripped = self.stripped or stripped != pending
        return stripped


def should_omit_openai_chat_tool_choice(provider: str, model: str, body: dict[str, Any], pcfg: dict[str, Any]) -> bool:
    """Return true when an OpenAI-chat backend should receive tools without a forced tool_choice."""
    if body.get("tool_choice") is None:
        return False
    return openai_chat_reasoning_passback_enabled(provider, model, pcfg)


def positive_int(value: Any) -> int | None:
    try:
        out = int(value)
    except Exception:
        return None
    return out if out > 0 else None


def finite_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def parse_config_value(value: str) -> Any:
    text = value.strip()
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("none", "null"):
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return int(text)
    except Exception:
        pass
    try:
        return float(text)
    except Exception:
        return text


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1", "enable", "enabled"):
        return True
    if text in ("false", "no", "off", "0", "disable", "disabled"):
        return False
    return default


def router_debug_external_access_enabled(cfg: dict[str, Any] | None = None) -> bool:
    env = env_bool(os.environ.get("CIEL_RUNTIME_ROUTER_DEBUG_EXTERNAL"), None)
    if env is not None:
        return bool(env)
    if cfg is None:
        cfg = load_config()
    return (
        parse_bool(cfg.get("router_debug_external_access"), False)
        and parse_bool(cfg.get("router_debug_external_access_confirmed"), False)
    )


def router_bind_host(cfg: dict[str, Any] | None = None) -> str:
    env_host = (os.environ.get("CIEL_RUNTIME_ROUTER_BIND_HOST") or os.environ.get("CIEL_RUNTIME_ROUTER_HOST") or "").strip()
    if env_host:
        return env_host
    return "0.0.0.0" if router_debug_external_access_enabled(cfg) else "127.0.0.1"


def is_loopback_address(host: str | None) -> bool:
    host = (host or "").strip().lower()
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


def router_external_access_token() -> str:
    configured = str(os.environ.get("CIEL_RUNTIME_ROUTER_EXTERNAL_TOKEN") or "").strip()
    if configured:
        return configured
    try:
        return ROUTER_EXTERNAL_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_router_external_access_token() -> str:
    existing = router_external_access_token()
    if existing:
        return existing
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    tmp = ROUTER_EXTERNAL_TOKEN_PATH.with_name(
        f"{ROUTER_EXTERNAL_TOKEN_PATH.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    tmp.write_text(token + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ROUTER_EXTERNAL_TOKEN_PATH)
    return token


def router_request_bearer_token(handler: BaseHTTPRequestHandler) -> str:
    try:
        authorization = str(handler.headers.get("authorization") or handler.headers.get("Authorization") or "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return str(handler.headers.get("x-ciel-runtime-token") or "").strip()
    except Exception:
        return ""


def router_request_allowed(handler: BaseHTTPRequestHandler, cfg: dict[str, Any] | None = None) -> bool:
    try:
        if is_loopback_address(str(handler.client_address[0])):
            return True
    except Exception:
        return False
    if not router_debug_external_access_enabled(cfg):
        return False
    expected = router_external_access_token()
    supplied = router_request_bearer_token(handler)
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def set_router_debug_external_access_config(value: Any) -> list[str]:
    cfg = load_config()
    enabled = parse_bool(value, False)
    cfg["router_debug_external_access"] = enabled
    cfg["router_debug_external_access_confirmed"] = enabled
    save_config(cfg)
    clear_model_cache()
    bind = router_bind_host(cfg)
    if enabled:
        token = ensure_router_external_access_token()
        return [
            "Router debug external access: on.",
            f"Router bind host for next launch: {bind}.",
            "External clients must authenticate with Authorization: Bearer <token>.",
            f"External access token: {token}",
        ]
    return [
        "Router debug external access: off.",
        "External clients are denied immediately; next launch binds to 127.0.0.1 unless overridden by environment.",
    ]


def schedule_router_process_restart(delay: float = 0.8) -> None:
    def restart() -> None:
        try:
            router_log("INFO", "router_debug_restart execing router process")
            os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "serve"])
        except Exception as exc:
            try:
                router_log("ERROR", f"router_debug_restart_failed {type(exc).__name__}: {exc}")
            except Exception as log_exc:
                try:
                    sys.stderr.write(
                        "router_debug_restart_diagnostic_failed "
                        f"restart_error={type(exc).__name__}: {exc} "
                        f"log_error={type(log_exc).__name__}: {log_exc}\n"
                    )
                    sys.stderr.flush()
                except (OSError, ValueError):
                    return

    timer = threading.Timer(delay, restart)
    timer.daemon = True
    timer.start()


def ctx_bucket(target: int, minimum: int, maximum: int) -> int:
    target = max(minimum, min(maximum, target))
    buckets = [4096, 8192, 16384, 32768, 65536, 131072, 262144]
    for bucket in buckets:
        if bucket >= target:
            return min(bucket, maximum)
    return maximum


def ollama_provider_context_limit(pcfg: dict[str, Any]) -> int | None:
    current_model = str(pcfg.get("current_model") or "")
    cached_model = str(pcfg.get("model_context_model") or "")
    cached_limit = positive_int(pcfg.get("model_context_max"))
    if not cached_limit:
        return None
    if cached_model and (not current_model or not ollama_context_model_matches(current_model, cached_model)):
        return None
    return cached_limit


def ollama_preserve_configured_context_cap(pcfg: dict[str, Any]) -> bool:
    preset = str(pcfg.get("llm_preset") or "").strip()
    return preset in LLM_PRESETS


def ollama_effective_context_limit(pcfg: dict[str, Any]) -> int | None:
    provider_limit = ollama_provider_context_limit(pcfg)
    configured_max = positive_int(pcfg.get("num_ctx_max"))
    if provider_limit and configured_max and ollama_preserve_configured_context_cap(pcfg):
        return min(provider_limit, configured_max)
    return provider_limit or configured_max


def ollama_num_ctx_for_payload(pcfg: dict[str, Any], payload: Any, _token_cache: dict[int, int] | None = None) -> int | None:
    override = os.environ.get("CIEL_RUNTIME_OLLAMA_NUM_CTX")
    if override:
        return positive_int(override)
    raw = pcfg.get("num_ctx", "auto")
    if isinstance(raw, str) and raw.strip().lower() in ("", "auto", "dynamic"):
        provider_limit = ollama_provider_context_limit(pcfg)
        if provider_limit:
            effective_limit = ollama_effective_context_limit(pcfg) or provider_limit
            return effective_limit
        minimum = positive_int(pcfg.get("num_ctx_min")) or 8192
        maximum = positive_int(pcfg.get("num_ctx_max")) or 65536
        if maximum < minimum:
            maximum = minimum
        estimated = estimate_tokens(payload, _token_cache)
        # Leave headroom for tool results, follow-up commands, and model-side formatting.
        target = int(estimated * 1.45) + 2048
        return ctx_bucket(target, minimum, maximum)
    return positive_int(raw)


def ollama_num_ctx_status(pcfg: dict[str, Any]) -> str:
    raw = pcfg.get("num_ctx", "auto")
    if isinstance(raw, str) and raw.strip().lower() in ("", "auto", "dynamic"):
        provider_limit = ollama_provider_context_limit(pcfg)
        if provider_limit:
            effective_limit = ollama_effective_context_limit(pcfg) or provider_limit
            if provider_limit and effective_limit < provider_limit:
                return f"auto ({effective_limit:,}; model max {provider_limit:,})"
            return f"auto (provider {effective_limit:,})"
        minimum = positive_int(pcfg.get("num_ctx_min")) or 8192
        maximum = positive_int(pcfg.get("num_ctx_max")) or 65536
        return f"auto ({minimum}-{maximum})"
    return str(positive_int(raw) or raw)


def ollama_extra_options(pcfg: dict[str, Any]) -> dict[str, Any]:
    raw = pcfg.get("ollama_options") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if v is not None}


def ollama_options_status(pcfg: dict[str, Any]) -> str:
    opts = ollama_extra_options(pcfg)
    if not opts:
        return "{}"
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in sorted(opts.items()))


def ollama_request_timeout_seconds(pcfg: dict[str, Any]) -> float:
    raw = pcfg.get("request_timeout_ms", pcfg.get("request_timeout", pcfg.get("timeout_ms", DEFAULT_REQUEST_TIMEOUT_MS)))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 120.0
    if value <= 0:
        return 120.0
    # Values above 10k are treated as milliseconds, matching common UI/API timeout notation.
    if value > 10000:
        return max(1.0, value / 1000.0)
    return value


def ollama_context_error_limit(raw: str | None) -> int | None:
    text = str(raw or "")
    low = text.lower()
    if "context" not in low and "n_ctx" not in low:
        return None
    patterns = (
        r"available context size\s*\(\s*(\d+)\s+tokens?\s*\)",
        r'"n_ctx"\s*:\s*(\d+)',
        r"\bn_ctx\s*[=:]\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return positive_int(match.group(1))
    return None


def ollama_context_retry_config(pcfg: dict[str, Any], context_limit: int) -> dict[str, Any]:
    retry_pcfg = dict(pcfg)
    context_limit = max(8192, int(context_limit))
    retry_pcfg["num_ctx"] = context_limit
    retry_pcfg["num_ctx_max"] = context_limit
    minimum = positive_int(retry_pcfg.get("num_ctx_min"))
    if minimum and minimum > context_limit:
        retry_pcfg["num_ctx_min"] = context_limit
    output_cap = max(256, min(2048, context_limit // 8))
    configured_output = positive_int(retry_pcfg.get("max_output_tokens"))
    retry_pcfg["max_output_tokens"] = min(configured_output, output_cap) if configured_output else output_cap
    opts = dict(ollama_extra_options(retry_pcfg))
    configured_num_predict = positive_int(opts.get("num_predict"))
    if configured_num_predict:
        opts["num_predict"] = min(configured_num_predict, output_cap)
    retry_pcfg["ollama_options"] = opts
    return retry_pcfg


def configured_output_tokens(pcfg: dict[str, Any], body: dict[str, Any], option_key: str | None = None) -> int | None:
    configured = positive_int(pcfg.get("max_output_tokens"))
    if option_key:
        opts = ollama_extra_options(pcfg)
        configured = positive_int(opts.get(option_key)) or configured
    requested = positive_int(body.get("max_tokens"))
    if configured and requested:
        return min(configured, requested)
    return configured or requested


def cap_output_tokens_for_context(
    pcfg: dict[str, Any],
    body: dict[str, Any],
    payload: Any,
    context_limit: int | None,
    configured: int | None,
    _token_cache: dict[int, int] | None = None,
) -> int | None:
    if not configured:
        return None
    if not context_limit:
        return configured
    reserve = context_guard_reserve_tokens(pcfg, context_limit)
    estimated_input = estimate_tokens(payload, _token_cache)
    available = context_limit - estimated_input - reserve
    if available <= 0:
        return min(configured, 256)
    return max(1, min(configured, available))

def context_guard_reserve_tokens(pcfg: dict[str, Any], context_limit: int | None) -> int:
    configured = positive_int(pcfg.get("context_reserve_tokens"))
    if configured:
        return configured
    if not context_limit:
        return 1024
    return max(1024, min(32768, int(context_limit) // 32))

def ollama_context_limit_for_budget(pcfg: dict[str, Any]) -> int:
    raw = pcfg.get("num_ctx", "auto")
    if isinstance(raw, str) and raw.strip().lower() in ("", "auto", "dynamic"):
        return ollama_effective_context_limit(pcfg) or 65536
    return positive_int(raw) or positive_int(pcfg.get("num_ctx_max")) or 65536


def openai_context_limit_for_budget(provider: str, pcfg: dict[str, Any]) -> int:
    configured = positive_int(pcfg.get("context_window")) or positive_int(pcfg.get("max_model_len"))
    if provider in ("vllm", "self-hosted-nim"):
        runtime = provider_model_context_capacity(provider, pcfg)
        if configured and str(pcfg.get("llm_preset") or "").strip():
            return min(configured, runtime) if runtime else configured
        if runtime:
            return runtime
    if configured:
        return configured
    if provider == "nvidia-hosted":
        return nvidia_hosted_context_default(str(pcfg.get("current_model") or ""))
    return 65536

def compact_ollama_messages_for_budget(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    budget_tokens: int,
    *,
    provider: str = "",
    model: str = "",
    pcfg: dict[str, Any] | None = None,
    full_compact_request: bool = False,
    wire: str | None = None,
) -> list[dict[str, Any]]:
    return run_chat_prompt_compaction(
        messages,
        tools,
        budget_tokens,
        provider=provider,
        model=model,
        pcfg=pcfg,
        full_compact_request=full_compact_request,
        wire=wire,
        services=prompt_compaction_services(),
    )

def prompt_compaction_services() -> PromptCompactionServices:
    return PromptCompactionServices(
        text=PromptCompactionText(
            content_to_text=anthropic_content_to_text,
            compact_text=compact_message_text_for_prompt,
            build_summary=build_chunked_context_guard_summary,
            append_system_texts=append_anthropic_system_texts,
            truncate=truncate_for_prompt,
            chunk_count=context_guard_chunk_count,
        ),
        runtime=PromptCompactionRuntime(
            estimate_tokens=estimate_tokens,
            llm_compact_messages=maybe_build_llm_compacted_messages,
            write_activity=write_context_compact_activity,
            log=router_log,
        ),
    )


def anthropic_message_has_tool_result(message: dict[str, Any]) -> bool:
    return compacted_anthropic_message_has_tool_result(message)


def anthropic_safe_tail_start(message: dict[str, Any]) -> bool:
    return compacted_anthropic_safe_tail_start(message)


def compact_anthropic_body_for_budget(
    body: dict[str, Any],
    budget_tokens: int,
    *,
    provider: str = "",
    model: str = "",
    pcfg: dict[str, Any] | None = None,
    full_compact_request: bool = False,
) -> dict[str, Any]:
    return run_anthropic_prompt_compaction(
        body,
        budget_tokens,
        provider=provider,
        model=model,
        pcfg=pcfg,
        full_compact_request=full_compact_request,
        services=prompt_compaction_services(),
    )

def provider_request_timeout_seconds(pcfg: dict[str, Any]) -> float:
    return ollama_request_timeout_seconds(pcfg)


def provider_stream_idle_timeout_seconds(pcfg: dict[str, Any]) -> float:
    return project_stream_idle_timeout(
        pcfg,
        positive_int=positive_int,
        request_timeout=provider_request_timeout_seconds,
    )


def set_upstream_stream_read_timeout(resp: Any, timeout: float) -> None:
    project_set_stream_read_timeout(resp, timeout)


def router_client_connection_closed(handler: BaseHTTPRequestHandler) -> bool:
    return project_client_connection_closed(handler)


def iter_upstream_lines_until_client_disconnect(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    idle_timeout: float,
) -> Iterable[bytes]:
    return project_iter_upstream_lines(
        handler,
        resp,
        idle_timeout,
        set_timeout=set_upstream_stream_read_timeout,
        disconnected=router_client_connection_closed,
    )


def sleep_until_or_client_disconnect(handler: BaseHTTPRequestHandler, seconds: float) -> bool:
    return project_sleep_until_disconnect(
        handler,
        seconds,
        disconnected=router_client_connection_closed,
    )


def provider_request_builder() -> ProviderRequestBuilder:
    return ProviderRequestBuilder(
        ProviderRequestBudget(
            context_limit=context_limit_for_status,
            positive_int=positive_int,
            configured_output=configured_output_tokens,
            cap_output_ratio=cap_output_tokens_to_context_ratio,
            reserve=context_guard_reserve_tokens,
            compact_anthropic=compact_anthropic_body_for_budget,
            compact_messages=compact_ollama_messages_for_budget,
            compact_requested=is_claude_code_compact_request,
            cap_output=cap_output_tokens_for_context,
            write_usage=write_context_usage,
        ),
        OllamaRequestPorts(
            messages=anthropic_messages_to_ollama,
            tools=anthropic_tools_to_ollama,
            extra_options=ollama_extra_options,
            context_limit=ollama_context_limit_for_budget,
            num_ctx=ollama_num_ctx_for_payload,
            think_enabled=ollama_request_think_enabled,
        ),
        OpenAIRequestPorts(
            messages=anthropic_messages_to_openai,
            tools=anthropic_tools_to_ollama,
            context_limit=openai_context_limit_for_budget,
            reasoning_passback=openai_chat_reasoning_passback_enabled,
            repair_tools=repair_openai_tool_call_adjacency,
            is_kimi_k3=is_kimi_k3_model_id,
            omit_tool_choice=should_omit_openai_chat_tool_choice,
            tool_choice=anthropic_tool_choice_to_openai,
        ),
        ProviderOptionPorts(
            sampling_providers=frozenset(PROVIDER_SAMPLING_OPTION_PROVIDERS),
            sampling_options=tuple(PROVIDER_SAMPLING_OPTIONS),
            anthropic_runtime_hints=anthropic_model_runtime_hints,
            log=router_log,
        ),
    )


def cap_anthropic_body_for_provider(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return provider_request_builder().cap_anthropic_body(provider, pcfg, body)

def apply_provider_request_options(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return provider_request_builder().apply_options(provider, pcfg, body)


def normalize_anthropic_model_request_options(provider: str, pcfg: dict[str, Any], body: dict[str, Any], model_id: str) -> dict[str, Any]:
    return provider_request_builder().normalize_anthropic_options(
        provider,
        body,
        model_id,
    )


def ollama_request_think_enabled(model: str | None, pcfg: dict[str, Any]) -> bool:
    return bool(pcfg.get("think", False))


def ollama_think_status(model: str | None, pcfg: dict[str, Any]) -> str:
    return str(ollama_request_think_enabled(model, pcfg))


def ollama_chat_request(model: str, body: dict[str, Any], pcfg: dict[str, Any], stream: bool = True, provider: str = "ollama") -> dict[str, Any]:
    return provider_request_builder().ollama_chat(
        model,
        body,
        pcfg,
        stream=stream,
        provider=provider,
    )

def openai_compatible_chat_request(provider: str, model: str, body: dict[str, Any], pcfg: dict[str, Any], stream: bool = False) -> dict[str, Any]:
    return provider_request_builder().openai_chat(
        provider,
        model,
        body,
        pcfg,
        stream=stream,
    )

ADVISOR_REVIEW_PROMPT = (
    "You are ciel-runtime Advisor, a stronger reviewer model. Review the current task state and provide "
    "concise, actionable guidance for the executor model. Review now; do not say that you will review later. "
    "Do not write code unless a small exact patch is the clearest advice. Use this exact structure: "
    "Verdict: approve, revise, or continue. Key findings: concrete gaps or risks. Required next action: "
    "the next action or Claude Code tool call. Validation: the check that proves the work. "
    "If the executor is stuck after progress announcements, tell it the exact next Claude Code tool to call."
)


def advisor_request_builder() -> AdvisorRequestBuilder:
    return AdvisorRequestBuilder(
        ADVISOR_REVIEW_PROMPT,
        AdvisorProjectionPorts(
            provider_kind=advisor_provider_kind,
            anthropic_messages=anthropic_advisor_messages_and_system,
            openai_messages=anthropic_messages_to_openai,
            ollama_messages=anthropic_messages_to_ollama,
            focus_from_body=advisor_focus_from_body,
            compact_text=compact_message_text_for_prompt,
            anthropic_system=anthropic_system_with_advisor,
            anthropic_text=anthropic_content_to_text,
        ),
        AdvisorBudgetPorts(
            ollama_context=ollama_context_limit_for_budget,
            provider_context=context_limit_for_status,
            openai_context=openai_context_limit_for_budget,
            reserve=context_guard_reserve_tokens,
            compact_messages=compact_ollama_messages_for_budget,
            configured_output=configured_output_tokens,
            ollama_options=ollama_extra_options,
            positive_int=positive_int,
            ollama_num_ctx=ollama_num_ctx_for_payload,
            think_enabled=ollama_request_think_enabled,
        ),
        AdvisorEndpointPorts(
            join_url=join_url,
            upstream_query=upstream_messages_query,
            provider_request_base=provider_upstream_request_base,
            upstream_model=ncp_model_id_for_nvidia_hosted,
        ),
    )


def advisor_messages_for_provider(provider: str, body: dict[str, Any], focus_override: str = "") -> list[dict[str, Any]]:
    return advisor_request_builder().messages(provider, body, focus_override)


def advisor_input_budget(provider: str, pcfg: dict[str, Any]) -> int:
    return advisor_request_builder().input_budget(provider, pcfg)

def advisor_upstream_model(provider: str, model: str) -> str:
    return advisor_request_builder().model(provider, model)


def advisor_request(provider: str, model: str, body: dict[str, Any], pcfg: dict[str, Any], focus_override: str = "") -> dict[str, Any]:
    return advisor_request_builder().request(
        provider,
        model,
        body,
        pcfg,
        focus_override,
    )


def advisor_response_text(provider: str, data: Any) -> str:
    return advisor_request_builder().response_text(provider, data)


def advisor_endpoint(provider: str, pcfg: dict[str, Any]) -> str:
    return advisor_request_builder().endpoint_url(provider, pcfg)


def advisor_client() -> AdvisorClient:
    return AdvisorClient(
        AdvisorClientPolicy(
            model_enabled=advisor_model_enabled,
            provider_supported=advisor_provider_supported,
            upstream_model=advisor_upstream_model,
            provider_kind=advisor_provider_kind,
            request=advisor_request,
            endpoint=advisor_endpoint,
            response_text=advisor_response_text,
        ),
        AdvisorClientIO(
            apply_rate_limit=apply_router_rate_limit,
            write_activity=write_router_activity,
            estimate_tokens=estimate_tokens,
            post_json=post_json_with_rate_retry,
            headers=provider_headers,
            provider_timeout=provider_request_timeout_seconds,
            ollama_timeout=ollama_request_timeout_seconds,
            log=router_log,
        ),
    )


def call_advisor_text(
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    focus: str = "",
    inbound_headers: Any | None = None,
    *,
    allow_rate_limit_wait: bool = True,
    retry_rate_limits: bool = True,
    raise_errors: bool = False,
) -> str:
    return advisor_client().review(
        provider,
        pcfg,
        body,
        focus,
        inbound_headers,
        allow_rate_limit_wait=allow_rate_limit_wait,
        retry_rate_limits=retry_rate_limits,
        raise_errors=raise_errors,
    )


def advisor_refinement_service() -> AdvisorRefinementService:
    return AdvisorRefinementService(
        ADVISOR_FEEDBACK_MARKER,
        AdvisorRefinementText(
            content_text=anthropic_content_to_text,
            tool_names=anthropic_message_tool_names,
            tool_summary=assistant_tool_call_summary_for_prompt,
            compact_text=compact_message_text_for_prompt,
            prepend_text=prepend_anthropic_text,
        ),
        AdvisorRefinementPolicy(
            model_enabled=advisor_model_enabled,
            provider_supported=advisor_provider_supported,
            is_advisor_request=is_advisor_request,
            has_feedback=body_has_advisor_feedback,
            trigger=advisor_trigger_for_message,
            focus=advisor_focus_for_message,
        ),
        AdvisorRefinementIO(
            call_advisor=call_advisor_text,
            call_provider=call_provider_chat_once,
            log=router_log,
            write_activity=write_router_activity,
        ),
    )


def body_with_internal_advisor_feedback(body: dict[str, Any], assistant_message: dict[str, Any], advisor_text: str, trigger: str) -> dict[str, Any]:
    return advisor_refinement_service().body_with_feedback(
        body,
        assistant_message,
        advisor_text,
        trigger,
    )


def advisor_visible_summary(advisor_text: str, trigger: str, limit: int = 700) -> str:
    return advisor_refinement_service().visible_summary(advisor_text, trigger, limit)


def provider_chat_executor() -> ProviderChatExecutor:
    return ProviderChatExecutor(
        ProviderChatPolicy(
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            normalize_tool_choice=normalize_tool_choice_for_provider,
            provider_kind=advisor_provider_kind,
            upstream_model=provider_upstream_model,
            ollama_request=ollama_chat_request,
            openai_request=openai_compatible_chat_request,
            ollama_response=ollama_chat_to_anthropic,
            openai_response=openai_chat_to_anthropic,
        ),
        ProviderChatIO(
            apply_rate_limit=apply_router_rate_limit,
            post_json=post_json_with_rate_retry,
            join_url=join_url,
            request_base=provider_upstream_request_base,
            headers=provider_headers,
            provider_timeout=provider_request_timeout_seconds,
            ollama_timeout=ollama_request_timeout_seconds,
        ),
    )


def call_provider_chat_once(provider: str, pcfg: dict[str, Any], body: dict[str, Any], model: str) -> dict[str, Any]:
    return provider_chat_executor().execute(provider, pcfg, body, model)

def refine_message_with_advisor(
    provider: str,
    pcfg: dict[str, Any],
    original_body: dict[str, Any],
    message: dict[str, Any],
    main_model: str,
) -> dict[str, Any]:
    return advisor_refinement_service().refine(
        provider,
        pcfg,
        original_body,
        message,
        main_model,
    )


def anthropic_text_response(model: str, text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return project_anthropic_text_response(model, text, stop_reason)


def anthropic_response_writer() -> AnthropicResponseWriter:
    return AnthropicResponseWriter(write_json)


def write_anthropic_text_response(handler: BaseHTTPRequestHandler, model: str, text: str, stream: bool) -> None:
    anthropic_response_writer().text(handler, model, text, stream)


def write_anthropic_message_response(handler: BaseHTTPRequestHandler, message: dict[str, Any], stream: bool) -> None:
    anthropic_response_writer().message(handler, message, stream)


def _write_anthropic_stream_block(handler: BaseHTTPRequestHandler, index: int, block: dict[str, Any]) -> None:
    anthropic_response_writer().block(handler, index, block)


def write_anthropic_open_stream_start(handler: BaseHTTPRequestHandler, model: str, input_tokens: int = 0) -> None:
    anthropic_response_writer().start(handler, model, input_tokens)


def write_anthropic_stream_blocks(handler: BaseHTTPRequestHandler, blocks: list[dict[str, Any]], start_index: int = 0) -> int:
    return anthropic_response_writer().blocks(handler, blocks, start_index)


def write_anthropic_open_stream_stop(handler: BaseHTTPRequestHandler, message: dict[str, Any] | None = None) -> None:
    anthropic_response_writer().stop(handler, message)


def prepend_anthropic_text(message: dict[str, Any], text: str) -> dict[str, Any]:
    return project_prepend_anthropic_text(message, text)


def import_session_max_bytes() -> int:
    return max(4096, positive_env_int("CIEL_RUNTIME_IMPORT_SESSION_MAX_BYTES", 512 * 1024))


def import_session_max_chars() -> int:
    return max(4096, positive_env_int("CIEL_RUNTIME_IMPORT_SESSION_MAX_CHARS", 240000))


def normalize_import_session_source(value: str) -> str:
    return normalize_import_source(value)


def import_session_repository() -> ImportSessionRepository:
    return ImportSessionRepository(
        HOME,
        os.environ,
        ImportSessionLimits(import_session_max_bytes(), import_session_max_chars()),
    )


def latest_import_session_transcript_path(source: str) -> Path | None:
    return import_session_repository().latest(source)


def resolve_import_session_transcript_path(source: str, path_text: str) -> tuple[Path | None, str]:
    return import_session_repository().resolve(source, path_text)


def _import_session_tool_text(record: dict[str, Any]) -> str:
    return import_tool_text(record)


def _import_session_record_to_line(record: dict[str, Any]) -> str:
    return import_record_line(record)


def read_import_session_transcript(source: str, path: Path) -> tuple[str, dict[str, Any]]:
    return import_session_repository().read(source, path)


def import_session_response_text(client_runtime: str, body: dict[str, Any]) -> str:
    return ImportSessionService(
        import_session_repository(),
        import_session_args_from_body,
    ).response_text(client_runtime, body)


def maybe_handle_import_session_request(
    handler: BaseHTTPRequestHandler,
    body: dict[str, Any],
    *,
    client_runtime: str,
    response_format: str = "anthropic",
    source_body: dict[str, Any] | None = None,
) -> bool:
    if not is_import_session_request(body):
        return False
    text = import_session_response_text(client_runtime, body)
    model = str(body.get("model") or current_alias(load_config()))
    stream = bool((source_body or body).get("stream", True))
    if response_format == "openai":
        message = {
            "id": f"msg_ciel_runtime_import_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": max(1, estimate_tokens({"text": text}))},
        }
        write_openai_responses_response(handler, message, source_body=source_body or body, stream=stream)
    else:
        write_anthropic_text_response(handler, model, text, stream)
    EVENT_BUS.publish(
        level="info",
        category="import_session.short_circuit",
        message="ImportSession request handled locally",
        provider=get_current_provider(load_config())[0],
        model=model,
        data={"client_runtime": client_runtime},
    )
    return True


def maybe_handle_advisor_request(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    if provider == "anthropic":
        # Claude native and Anthropic routed sessions follow Claude Code's
        # built-in /advisor flow (model picker + advisor server tool);
        # ciel-runtime neither installs its own /advisor command nor intercepts
        # advisor traffic for this provider.
        return False
    if not is_advisor_request(body):
        return False
    advisor_model = str(pcfg.get("advisor_model") or "").strip()
    stream = bool(body.get("stream", True))
    if not advisor_model:
        write_anthropic_text_response(
            handler,
            str(body.get("model") or current_alias(load_config())),
            "Advisor is off. Choose an Advisor Model in the ciel-runtime launch menu (item 5), or run `ciel-runtime advisor-model <model-id>`, then use `/advisor` again.",
            stream,
        )
        return True
    if not advisor_provider_supported(provider):
        write_anthropic_text_response(
            handler,
            advisor_model,
            f"Advisor Model is configured as `{advisor_model}`, but ciel-runtime advisor calling is not implemented for provider `{provider}`.",
            stream,
        )
        return True
    try:
        text = call_advisor_text(
            provider,
            pcfg,
            body,
            inbound_headers=handler.headers,
            allow_rate_limit_wait=False,
            retry_rate_limits=False,
            raise_errors=True,
        )
        if not text:
            text = "Advisor returned no text."
    except Exception as exc:
        text = f"Advisor request failed: {type(exc).__name__}: {exc}"
    write_anthropic_text_response(handler, advisor_model, "Advisor guidance:\n\n" + text, stream)
    return True


def maybe_handle_router_debug_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    if not is_router_debug_request(body):
        return False
    stream = bool(body.get("stream", True))
    value = router_debug_value_from_body(body).strip()
    cfg = load_config()
    current = router_debug_external_access_enabled(cfg)
    low = value.lower()
    should_restart = False
    if low in ("", "status", "state", "show", "?"):
        lines = [
            f"Router debug external access: {'on' if current else 'off'}.",
            f"Current router bind host: {router_bind_host(cfg)}.",
        ]
    elif low in ("toggle", "tog", "switch"):
        lines = set_router_debug_external_access_config(not current)
        should_restart = True
    elif low in ("on", "true", "1", "enable", "enabled"):
        lines = set_router_debug_external_access_config(True)
        should_restart = True
    elif low in ("off", "false", "0", "disable", "disabled"):
        lines = set_router_debug_external_access_config(False)
        should_restart = True
    else:
        lines = ["Usage: `/router-debug`, `/router-debug on`, `/router-debug off`, or `/router-debug status`."]
    if should_restart:
        lines.append("Router restart scheduled so the bind address changes immediately.")
    write_anthropic_text_response(handler, str(body.get("model") or current_alias(load_config())), "\n".join(lines), stream)
    if should_restart:
        schedule_router_process_restart()
    return True


def maybe_handle_version_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    if not is_version_request(body):
        return False
    stream = bool(body.get("stream", True))
    lines = [
        f"ciel-runtime {VERSION}",
        f"source: {SOURCE_FINGERPRINT[:12]}",
        f"config dir: {CONFIG_DIR}",
    ]
    write_anthropic_text_response(handler, str(body.get("model") or current_alias(load_config())), "\n".join(lines), stream)
    return True


def _format_channel_backlog_status_lines(stats: dict[str, Any], cleared: bool) -> list[str]:
    if cleared:
        return [
            "Ciel Runtime channel backlog discarded.",
            f"- chat tail: {stats.get('chat_tail')}",
            f"- LLM cursor advanced by: {stats.get('discarded_llm')}",
            f"- MCP cursor advanced by: {stats.get('discarded_mcp')}",
            f"- active MCP channel sessions updated: {stats.get('mcp_sessions_updated')}",
            "New channel events arriving after this point will still be delivered.",
        ]
    return [
        "Ciel Runtime channel backlog status.",
        f"- chat tail: {stats.get('chat_tail')}",
        f"- pending LLM items by id range: {stats.get('pending_llm')}",
        f"- pending MCP items by id range: {stats.get('pending_mcp')}",
        f"- active MCP channel sessions: {stats.get('mcp_sessions')}",
    ]


def maybe_handle_channel_clear_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    if not is_channel_clear_request(body):
        return False
    stream = bool(body.get("stream", True))
    value = channel_clear_value_from_body(body).strip().lower()
    if value in {"", "all", "clear", "discard", "drop", "purge", "reset", "now"}:
        stats = clear_channel_backlog()
        lines = _format_channel_backlog_status_lines(stats, cleared=True)
    elif value in {"status", "state", "show", "?", "dry-run", "dryrun"}:
        stats = channel_backlog_status()
        lines = _format_channel_backlog_status_lines(stats, cleared=False)
    else:
        lines = ["Usage: `/channel-clear`, `/channel-clear all`, or `/channel-clear status`."]
    write_anthropic_text_response(handler, str(body.get("model") or current_alias(load_config())), "\n".join(lines), stream)
    return True


def handle_live_llm_options_action(action: str = "status", preset: str = "") -> tuple[list[str], bool]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    raw_action = str(action or "").strip()
    raw_preset = str(preset or "").strip()
    if not raw_action and raw_preset:
        raw_action = "apply"
    value = raw_preset or raw_action
    normalized = normalize_llm_preset_token(value)
    if normalized in {"", "status", "state", "show", "current", "now"}:
        return runtime_llm_status_lines(provider, pcfg), False
    if normalized in {"list", "presets", "preset", "help", "options", "menu", "select"}:
        return runtime_llm_preset_list_lines(provider, pcfg), False
    if normalized in {"left", "prev", "previous", "backward", "back", "decrease", "minus"}:
        lines = apply_runtime_llm_slider_delta_config(provider, -1)
        cfg_after = load_config()
        _provider_after, pcfg_after = get_current_provider(cfg_after)
        return lines + ["", "Updated live LLM options. The next model request uses these settings."] + runtime_llm_status_lines(provider, pcfg_after), True
    if normalized in {"right", "next", "forward", "increase", "plus"}:
        lines = apply_runtime_llm_slider_delta_config(provider, 1)
        cfg_after = load_config()
        _provider_after, pcfg_after = get_current_provider(cfg_after)
        return lines + ["", "Updated live LLM options. The next model request uses these settings."] + runtime_llm_status_lines(provider, pcfg_after), True
    if normalized in {"restore", "original", "reset", "revert", "undo"}:
        had_snapshot = isinstance(pcfg.get(RUNTIME_LLM_ORIGINAL_KEY), dict)
        lines = restore_runtime_llm_original_options(provider)
        cfg_after = load_config()
        _provider_after, pcfg_after = get_current_provider(cfg_after)
        return lines + [""] + runtime_llm_status_lines(provider, pcfg_after), had_snapshot
    preset_id = resolve_llm_preset_id(value)
    if not preset_id:
        return [
            f"Unknown live LLM preset/action: {value or raw_action or raw_preset}",
            "Use `/llm-options list` to see available presets, or `/llm-restore` to revert.",
        ], False
    lines = apply_runtime_llm_preset_config(provider, preset_id)
    cfg_after = load_config()
    _provider_after, pcfg_after = get_current_provider(cfg_after)
    return lines + ["", "Updated live LLM options. The next model request uses these settings."] + runtime_llm_status_lines(provider, pcfg_after), True


def maybe_handle_live_llm_options_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    if not is_live_llm_options_request(body):
        return False
    stream = bool(body.get("stream", True))
    value = live_llm_options_value_from_body(body)
    lines, changed = handle_live_llm_options_action(value)
    if changed:
        EVENT_BUS.publish(
            level="info",
            category="config.llm",
            message="live LLM options updated from slash command",
            provider=get_current_provider(load_config())[0],
            data={"value": value},
        )
    write_anthropic_text_response(handler, str(body.get("model") or current_alias(load_config())), "\n".join(lines), stream)
    return True


def live_api_key_status_lines(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return [
        f"Live API key status for provider: {provider}",
        api_key_status_line(provider, pcfg),
        f"Stored: {stored_api_key_mask(provider, pcfg)}",
    ]


def handle_live_api_keys_action(value: str) -> tuple[list[str], bool]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    raw = str(value or "").strip()
    normalized = raw.lower()
    if normalized in {"", "status", "state", "show", "current", "now"}:
        return live_api_key_status_lines(provider, pcfg), False
    if normalized in {"help", "usage", "list"}:
        return [
            "Use `/api-key status` to show masked key status.",
            "Use `/api-key clear` or `/api-key unset` to remove API keys for only the current provider.",
            "Use `/api-key KEY` to set one key.",
            "Use `/api-key KEY1,KEY2` or `/api-keys KEY1;KEY2` to set multiple round-robin keys.",
            "Raw keys are never echoed; responses show only masked keys and fingerprints.",
        ], False
    try:
        lines = store_api_key_input_config(provider, raw)
    except SystemExit as exc:
        message = str(exc).strip() or "No API keys provided; unchanged."
        return [message, "", *live_api_key_status_lines(provider, pcfg)], False
    cfg_after = load_config()
    provider_after, pcfg_after = get_current_provider(cfg_after)
    return lines + ["", "Updated live API key settings. The next model request uses these settings.", *live_api_key_status_lines(provider_after, pcfg_after)], True


def maybe_handle_live_api_keys_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    if not is_live_api_keys_request(body):
        return False
    stream = bool(body.get("stream", True))
    value = live_api_keys_value_from_body(body)
    lines, changed = handle_live_api_keys_action(value)
    if changed:
        provider_after, pcfg_after = get_current_provider(load_config())
        EVENT_BUS.publish(
            level="info",
            category="config.api_key",
            message="live API key settings updated from slash command",
            provider=provider_after,
            data={"key_count": provider_api_key_count(provider_after, pcfg_after)},
        )
    write_anthropic_text_response(handler, str(body.get("model") or current_alias(load_config())), "\n".join(lines), stream)
    return True


def normalize_tool_arguments(tool_name: str, args: Any) -> dict[str, Any]:
    return project_normalize_tool_arguments(tool_name, args)


PSEUDO_TOOL_START = "<|tool_calls_section_begin|>"
PSEUDO_TOOL_END = "<|tool_calls_section_end|>"
PSEUDO_CALL_BEGIN = "<|tool_call_begin|>"
PSEUDO_ARG_BEGIN = "<|tool_call_argument_begin|>"
PSEUDO_CALL_END = "<|tool_call_end|>"


def infer_tool_name_from_args(args: dict[str, Any]) -> str:
    return project_infer_tool_name(args)


def parse_pseudo_tool_calls(text: str, source_body: dict[str, Any] | None = None) -> tuple[str, list[dict[str, Any]]]:
    return project_parse_pseudo_tool_calls(
        text,
        source_body,
        PseudoToolParserServices(
            parse_xml=_parse_xml_pseudo_tool_calls,
            fuzzy_tool_name=_fuzzy_match_tool_name,
        ),
    )


def ollama_response_services() -> OllamaResponseServices:
    return OllamaResponseServices(
        text=OllamaResponseText(
            decode=decode_ollama_chat_response,
            strip_thinking=strip_visible_thinking_markup,
            parse_pseudo_tools=parse_pseudo_tool_calls,
            log=router_log,
        ),
        tools=OllamaResponseTools(
            resolve_name=resolve_emitted_tool_name,
            normalize_arguments=normalize_tool_arguments,
            validate_input=_validate_and_fix_tool_input,
            plan_mode_name=plan_mode_tool_name_for_emit,
            cap_notification_wait=cap_mcp_notification_wait_tool_input,
            should_drop=should_drop_emitted_tool_call,
            append_log=append_tool_call_log,
        ),
        recovery=OllamaResponseRecovery(
            auto_enter_plan=should_auto_enter_plan_mode,
            recover_empty_with_tasklist=should_recover_empty_end_turn_with_tasklist,
            keep_alive_with_tasklist=should_keep_work_alive_with_tasklist,
            auto_continue_choice=should_auto_continue_choice_question_with_tasklist,
            empty_notice=empty_end_turn_notice_for_body,
            latest_tool_result_names=latest_user_tool_result_names,
            synthetic_tool_response=synthetic_tool_use_response,
        ),
        output=OllamaResponseOutput(
            encode_message=encode_anthropic_message,
            estimate_tokens=estimate_tokens,
            timestamp_ms=lambda: int(time.time() * 1000),
            process_id=os.getpid,
        ),
    )


def ollama_chat_to_anthropic(
    data: dict[str, Any],
    model: str,
    source_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return project_ollama_response(data, model, source_body, ollama_response_services())


STREAM_WORD_CHUNK_MAX_BUFFER = 64


def _split_word_buffer(buf: str, force: bool = False, max_buffer: int = STREAM_WORD_CHUNK_MAX_BUFFER) -> tuple[str, str]:
    return split_word_buffer(buf, force=force, max_buffer=max_buffer)


def _rebatch_anthropic_sse_text(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    model: str = "ciel-runtime-upstream",
    word_chunking: bool = True,
    source_body: dict[str, Any] | None = None,
    preserve_thinking: bool = True,
    normalize_tool_use: bool = False,
    provider: str = "",
) -> None:
    return rebatch_anthropic_sse_text(
        handler,
        resp,
        model=model,
        word_chunking=word_chunking,
        source_body=source_body,
        preserve_thinking=preserve_thinking,
        normalize_tool_use=normalize_tool_use,
        provider=provider,
        services=AnthropicStreamServices(
            io=AnthropicStreamIO(
                ANTHROPIC_THINKING_BLOCK_TYPES=ANTHROPIC_THINKING_BLOCK_TYPES,
                VisibleToolCallArtifactFilter=VisibleToolCallArtifactFilter,
                _find_pseudo_xml_tool_start=_find_pseudo_xml_tool_start,
                _split_word_buffer=_split_word_buffer,
                mark_pending_channel_delivery_failed=mark_pending_channel_delivery_failed,
                mark_pending_channel_delivery_success=mark_pending_channel_delivery_success,
                remember_suppressed_thinking_passback=remember_suppressed_thinking_passback,
                router_client_connection_closed=router_client_connection_closed,
                router_log=router_log,
            ),
            tool_projection=AnthropicToolProjection(
                _is_mcp_notification_wait_tool=_is_mcp_notification_wait_tool,
                _remember_channel_injected_tool_use=_remember_channel_injected_tool_use,
                _validate_and_fix_tool_input=_validate_and_fix_tool_input,
                append_tool_call_log=append_tool_call_log,
                cap_mcp_notification_wait_tool_input=cap_mcp_notification_wait_tool_input,
                infer_tool_name_from_args=infer_tool_name_from_args,
                normalize_tool_arguments=normalize_tool_arguments,
                parse_pseudo_tool_calls=parse_pseudo_tool_calls,
                plan_mode_tool_name_for_emit=plan_mode_tool_name_for_emit,
                resolve_emitted_tool_name=resolve_emitted_tool_name,
            ),
            tool_policy=AnthropicToolPolicy(
                should_drop_duplicate_side_effect_tool_call=should_drop_duplicate_side_effect_tool_call,
                should_drop_emitted_tool_call=should_drop_emitted_tool_call,
                should_repair_anthropic_passthrough_tool_input=should_repair_anthropic_passthrough_tool_input,
            ),
            conversation=AnthropicConversationContext(
                backfill_exit_plan_mode_allowed_prompts=backfill_exit_plan_mode_allowed_prompts,
                body_ultracode_runtime_enabled=body_ultracode_runtime_enabled,
                empty_end_turn_notice_for_body=empty_end_turn_notice_for_body,
                has_tool=has_tool,
                latest_user_intent_message_index=latest_user_intent_message_index,
                latest_user_is_claude_code_suggestion_mode=latest_user_is_claude_code_suggestion_mode,
                latest_user_tool_result_names=latest_user_tool_result_names,
                recent_synthetic_tasklist_count=recent_synthetic_tasklist_count,
            ),
            continuation=AnthropicContinuationPolicy(
                should_auto_continue_choice_question_with_tasklist=should_auto_continue_choice_question_with_tasklist,
                should_auto_exit_plan_mode=should_auto_exit_plan_mode,
                should_keep_work_alive_with_tasklist=should_keep_work_alive_with_tasklist,
                should_recover_empty_end_turn_with_tasklist=should_recover_empty_end_turn_with_tasklist,
                should_synthesize_tasklist_for_provider=should_synthesize_tasklist_for_provider,
            ),
        ),
    )


def _ollama_stream_to_anthropic_sse(
    handler: BaseHTTPRequestHandler,
    resp: Any,
    model: str,
    word_chunking: bool = False,
    provider: str = "ollama",
    source_body: dict[str, Any] | None = None,
    idle_timeout: float = 30.0,
) -> None:
    return ollama_stream_to_anthropic_sse(
        handler, resp, model, word_chunking=word_chunking, provider=provider,
        source_body=source_body, idle_timeout=idle_timeout,
        services=OllamaStreamServices(
            io=OllamaStreamIO(
                UpstreamClientDisconnected=UpstreamClientDisconnected,
                VisibleThinkingMarkupFilter=VisibleThinkingMarkupFilter,
                _split_word_buffer=_split_word_buffer,
                estimate_tokens=estimate_tokens,
                iter_upstream_lines_until_client_disconnect=iter_upstream_lines_until_client_disconnect,
                mark_pending_channel_delivery_failed=mark_pending_channel_delivery_failed,
                mark_pending_channel_delivery_success=mark_pending_channel_delivery_success,
                router_log=router_log,
                write_router_activity=write_router_activity,
            ),
            trace=OllamaStreamTrace(
                dump_response_for_trace=dump_response_for_trace,
                finish_outgoing_sse_trace=finish_outgoing_sse_trace,
                make_outgoing_sse_trace=make_outgoing_sse_trace,
                record_outgoing_sse_event=record_outgoing_sse_event,
            ),
            tool_projection=OllamaToolProjection(
                _remember_channel_injected_tool_use=_remember_channel_injected_tool_use,
                _validate_and_fix_tool_input=_validate_and_fix_tool_input,
                append_tool_call_log=append_tool_call_log,
                cap_mcp_notification_wait_tool_input=cap_mcp_notification_wait_tool_input,
                normalize_tool_arguments=normalize_tool_arguments,
                plan_mode_tool_name_for_emit=plan_mode_tool_name_for_emit,
                resolve_emitted_tool_name=resolve_emitted_tool_name,
                should_drop_duplicate_side_effect_tool_call=should_drop_duplicate_side_effect_tool_call,
                should_drop_emitted_tool_call=should_drop_emitted_tool_call,
            ),
            continuation=OllamaContinuationPolicy(
                empty_end_turn_notice_for_body=empty_end_turn_notice_for_body,
                should_auto_continue_choice_question_with_tasklist=should_auto_continue_choice_question_with_tasklist,
                should_auto_enter_plan_mode=should_auto_enter_plan_mode,
                should_keep_work_alive_with_tasklist=should_keep_work_alive_with_tasklist,
                should_recover_empty_end_turn_with_tasklist=should_recover_empty_end_turn_with_tasklist,
            ),
        ),
    )


def ollama_forward_services() -> OllamaForwardServices:
    return OllamaForwardServices(
        constants=OllamaForwardConstants(
            client_disconnected_error=UpstreamClientDisconnected,
            compatibility_test_header=COMPATIBILITY_TEST_HEADER,
            upstream_retry_http_codes=UPSTREAM_RETRY_HTTP_CODES,
        ),
        request=OllamaForwardRequest(
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            ollama_chat_request=ollama_chat_request,
            provider_endpoint=provider_endpoint,
            provider_headers=provider_headers,
            provider_urlopen=provider_urlopen,
            request_timeout_seconds=ollama_request_timeout_seconds,
            resolve_requested_model=resolve_requested_model,
            set_stream_read_timeout=set_upstream_stream_read_timeout,
            stream_idle_timeout_seconds=provider_stream_idle_timeout_seconds,
        ),
        rate_limit=OllamaForwardRateLimit(
            apply_router_rate_limit=apply_router_rate_limit,
            configured_gateway_retries=configured_gateway_retries,
            effective_rpm=router_rate_limit_effective_rpm,
            learn_headers=learn_router_rate_limit_headers,
            notice=rate_limit_notice,
            register_backoff=register_router_rate_limit_backoff,
            retry_wait_seconds=upstream_retry_wait_seconds,
            retryable_upstream_exception=retryable_upstream_exception,
            sleep_until_or_client_disconnect=sleep_until_or_client_disconnect,
        ),
        streaming=OllamaForwardStreaming(
            client_connection_closed=router_client_connection_closed,
            iter_upstream_lines=iter_upstream_lines_until_client_disconnect,
            log=router_log,
            stream_to_anthropic_sse=_ollama_stream_to_anthropic_sse,
            write_router_activity=write_router_activity,
        ),
        advisor=OllamaForwardAdvisor(
            body_with_tool=body_with_advisor_tool,
            estimate_tokens=estimate_tokens,
            gate_possible=advisor_gate_possible_for_body,
            gate_reason=advisor_gate_reason_for_body,
            model_enabled=advisor_model_enabled,
            prepend_text=prepend_anthropic_text,
            provider_supported=advisor_provider_supported,
            refine_message=refine_message_with_advisor,
        ),
        response=OllamaForwardResponse(
            context_error_limit=ollama_context_error_limit,
            context_retry_config=ollama_context_retry_config,
            mark_pending_delivery_success=mark_pending_channel_delivery_success,
            ollama_chat_to_anthropic=ollama_chat_to_anthropic,
            remember_injected_tool_uses=remember_channel_injected_tool_uses,
            update_tool_schema_registry=_update_tool_schema_registry,
            upstream_http_error_message=upstream_http_error_message,
            write_json=write_json,
        ),
    )


def forward_ollama_api_chat(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> None:
    run_ollama_forward(
        handler,
        provider,
        pcfg,
        body,
        services=ollama_forward_services(),
    )


def openai_chat_to_anthropic(data: dict[str, Any], model: str, source_body: dict[str, Any] | None = None) -> dict[str, Any]:
    choice = {}
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    wrapped = {
        "message": {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        },
        "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
    }
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    wrapped["prompt_eval_count"] = positive_int(usage.get("prompt_tokens")) or (estimate_tokens(source_body) if isinstance(source_body, dict) else 0)
    wrapped["eval_count"] = positive_int(usage.get("completion_tokens")) or 0
    out = ollama_chat_to_anthropic(wrapped, model, source_body=source_body)
    thinking_block = openai_reasoning_to_anthropic_thinking_block(message.get("reasoning_content"))
    if thinking_block is None:
        return out
    content = out.get("content")
    if not isinstance(content, list):
        content = [{"type": "text", "text": anthropic_content_to_text(content)}]
    out = dict(out)
    out["content"] = [thinking_block] + content
    return out


def openai_responses_to_anthropic_messages(body: dict[str, Any], fallback_model: str) -> dict[str, Any]:
    adapter = PROTOCOL_ADAPTERS.create("openai_responses", fallback_model=fallback_model)
    return dict(adapter.normalize_request(body))


def anthropic_message_to_openai_response(
    message: dict[str, Any], source_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    adapter = PROTOCOL_ADAPTERS.create("openai_responses", source_body=source_body)
    return dict(adapter.normalize_response(message))


def openai_responses_stream_services() -> OpenAIResponsesStreamServices:
    return OpenAIResponsesStreamServices(
        to_response=anthropic_message_to_openai_response,
        write_json=write_json,
    )


def write_openai_responses_response(
    handler: BaseHTTPRequestHandler,
    message: dict[str, Any],
    source_body: dict[str, Any] | None = None,
    *,
    stream: bool = True,
) -> None:
    project_openai_responses_stream(
        handler,
        message,
        source_body,
        stream=stream,
        services=openai_responses_stream_services(),
    )


def write_openai_responses_error(
    handler: BaseHTTPRequestHandler,
    message: str,
    *,
    stream: bool = True,
    status: int = 500,
) -> None:
    project_openai_responses_error(
        handler,
        message,
        stream=stream,
        status=status,
        services=openai_responses_stream_services(),
    )


def stream_openai_chat_to_anthropic_sse(
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
    return forward_openai_chat_to_anthropic_sse(
        handler, resp, model, provider, source_body=source_body,
        start_index=start_index, word_chunking=word_chunking,
        input_tokens=input_tokens, input_bytes=input_bytes,
        services=OpenAIChatStreamServices(
            io=OpenAIChatStreamIO(
                PSEUDO_TOOL_END=PSEUDO_TOOL_END,
                PSEUDO_TOOL_START=PSEUDO_TOOL_START,
                _split_word_buffer=_split_word_buffer,
                positive_int=positive_int,
                router_log=router_log,
                write_anthropic_open_stream_stop=write_anthropic_open_stream_stop,
                write_router_activity=write_router_activity,
            ),
            tool_projection=OpenAIChatToolProjection(
                _remember_channel_injected_tool_use=_remember_channel_injected_tool_use,
                _validate_and_fix_tool_input=_validate_and_fix_tool_input,
                append_tool_call_log=append_tool_call_log,
                cap_mcp_notification_wait_tool_input=cap_mcp_notification_wait_tool_input,
                normalize_tool_arguments=normalize_tool_arguments,
                parse_pseudo_tool_calls=parse_pseudo_tool_calls,
                plan_mode_tool_name_for_emit=plan_mode_tool_name_for_emit,
                resolve_emitted_tool_name=resolve_emitted_tool_name,
                should_drop_duplicate_side_effect_tool_call=should_drop_duplicate_side_effect_tool_call,
                should_drop_emitted_tool_call=should_drop_emitted_tool_call,
            ),
            continuation=OpenAIChatContinuationPolicy(
                empty_end_turn_notice_for_body=empty_end_turn_notice_for_body,
                latest_user_tool_result_names=latest_user_tool_result_names,
                should_auto_continue_choice_question_with_tasklist=should_auto_continue_choice_question_with_tasklist,
                should_auto_enter_plan_mode=should_auto_enter_plan_mode,
                should_keep_work_alive_with_tasklist=should_keep_work_alive_with_tasklist,
                should_recover_empty_end_turn_with_tasklist=should_recover_empty_end_turn_with_tasklist,
            ),
        ),
    )


def upstream_http_error_message(exc: urllib.error.HTTPError, raw: str | None = None) -> str:
    return project_upstream_http_error_message(
        exc,
        raw,
        first_header=first_header,
        parse_retry_after=parse_retry_after_seconds,
        format_duration=format_duration_seconds,
    )


UPSTREAM_RETRY_HTTP_CODES: frozenset[int] = frozenset({502, 503, 504})


def upstream_retry_message(attempt: int, total: int) -> str:
    return project_upstream_retry_message(
        str(load_config().get("language") or "en"),
        attempt,
        total,
    )


def upstream_rate_limit_retry_message(attempt: int, total: int) -> str:
    return project_upstream_retry_message(
        str(load_config().get("language") or "en"),
        attempt,
        total,
        rate_limit=True,
    )


def upstream_retry_wait_seconds(attempt: int) -> float:
    return project_upstream_retry_wait_seconds(attempt)


def retryable_upstream_exception(exc: BaseException) -> bool:
    return project_retryable_upstream_exception(exc)


def configured_gateway_retries(pcfg: dict[str, Any]) -> int:
    return project_configured_gateway_retries(pcfg)


def upstream_retry_services() -> UpstreamRetryServices:
    return UpstreamRetryServices(
        policy=UpstreamRetryPolicy(
            configured_gateway_retries=configured_gateway_retries,
            retry_after_exceeds_request_timeout=retry_after_exceeds_request_timeout,
            retryable_upstream_exception=retryable_upstream_exception,
            upstream_rate_limit_retry_message=upstream_rate_limit_retry_message,
            upstream_retry_http_codes=UPSTREAM_RETRY_HTTP_CODES,
            upstream_retry_message=upstream_retry_message,
            upstream_retry_wait_seconds=upstream_retry_wait_seconds,
        ),
        keys=UpstreamRetryKeys(
            key_from_request_headers=key_from_request_headers,
            provider_api_key_count=provider_api_key_count,
            provider_has_live_api_key=provider_has_live_api_key,
            provider_headers=provider_headers,
            register_api_key_cooldown=register_api_key_cooldown,
        ),
        rate_limit=UpstreamRetryRateLimit(
            learn_headers=learn_router_rate_limit_headers,
            log=router_log,
            register_backoff=register_router_rate_limit_backoff,
            write_activity=write_router_activity,
        ),
        http=UpstreamRetryHttp(
            estimate_tokens=estimate_tokens,
            provider_urlopen=provider_urlopen,
            set_stream_read_timeout=set_upstream_stream_read_timeout,
            stream_idle_timeout_seconds=provider_stream_idle_timeout_seconds,
            upstream_http_error_message=upstream_http_error_message,
        ),
    )


def post_json_with_rate_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    retry_notice: Callable[[str], None] | None = None,
    *,
    retry_rate_limits: bool = True,
) -> Any:
    return retry_post_json(
        url,
        req_body,
        headers,
        timeout,
        provider,
        pcfg,
        model,
        retry_notice,
        retry_rate_limits=retry_rate_limits,
        services=upstream_retry_services(),
    )


def open_provider_request_with_key_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    *,
    stream: bool = False,
    retry_rate_limits: bool = True,
) -> Any:
    return retry_provider_request(
        url,
        req_body,
        headers,
        timeout,
        provider,
        pcfg,
        model,
        stream=stream,
        retry_rate_limits=retry_rate_limits,
        services=upstream_retry_services(),
    )


def open_openai_stream_with_rate_retry(
    url: str,
    req_body: Any,
    headers: dict[str, str],
    timeout: float,
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    retry_notice: Callable[[str], None] | None = None,
    *,
    retry_rate_limits: bool = True,
) -> Any:
    return retry_openai_stream(
        url,
        req_body,
        headers,
        timeout,
        provider,
        pcfg,
        model,
        retry_notice,
        retry_rate_limits=retry_rate_limits,
        services=upstream_retry_services(),
    )


def openai_forward_services() -> OpenAIForwardServices:
    return OpenAIForwardServices(
        policy=OpenAIForwardPolicy(
            compatibility_test_header=COMPATIBILITY_TEST_HEADER,
            provider_requires_streaming=provider_requires_streaming,
        ),
        request=OpenAIForwardRequest(
            update_tool_schema_registry=_update_tool_schema_registry,
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            resolve_model=resolve_requested_model,
            provider_upstream_model=provider_upstream_model,
            body_with_advisor_tool=body_with_advisor_tool,
            advisor_provider_supported=advisor_provider_supported,
            join_url=join_url,
            upstream_request_base=provider_upstream_request_base,
            build_chat_request=openai_compatible_chat_request,
            provider_headers=provider_headers,
        ),
        rate_limit=OpenAIForwardRateLimit(
            apply=apply_router_rate_limit,
            notice=rate_limit_notice,
            estimate_tokens=estimate_tokens,
            request_timeout_seconds=provider_request_timeout_seconds,
        ),
        advisor=OpenAIForwardAdvisor(
            model_enabled=advisor_model_enabled,
            gate_possible_for_body=advisor_gate_possible_for_body,
            gate_reason_for_body=advisor_gate_reason_for_body,
            refine_message=refine_message_with_advisor,
        ),
        streaming=OpenAIForwardStreaming(
            write_open_start=write_anthropic_open_stream_start,
            write_blocks=write_anthropic_stream_blocks,
            open_with_retry=open_openai_stream_with_rate_retry,
            post_json_with_retry=post_json_with_rate_retry,
            stream_to_anthropic_sse=stream_openai_chat_to_anthropic_sse,
            write_open_stop=write_anthropic_open_stream_stop,
        ),
        response=OpenAIForwardResponse(
            mark_delivery_success=mark_pending_channel_delivery_success,
            mark_delivery_failed=mark_pending_channel_delivery_failed,
            write_activity=write_router_activity,
            chat_to_anthropic=openai_chat_to_anthropic,
            remember_tool_uses=remember_channel_injected_tool_uses,
            prepend_text=prepend_anthropic_text,
            write_message=write_anthropic_message_response,
            write_json=write_json,
        ),
        log=router_log,
    )


def forward_openai_compatible_chat(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> None:
    run_openai_forward(handler, provider, pcfg, body, services=openai_forward_services())


def response_collection_services() -> ResponseCollectionServices:
    return ResponseCollectionServices(
        compatibility_test_header=COMPATIBILITY_TEST_HEADER,
        request=ResponseCollectionRequest(
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            resolve_model=resolve_requested_model,
            body_with_advisor_tool=body_with_advisor_tool,
            advisor_provider_supported=advisor_provider_supported,
            provider_endpoint=provider_endpoint,
            provider_headers=provider_headers,
        ),
        rate_limit=ResponseCollectionRateLimit(
            apply=apply_router_rate_limit,
            effective_rpm=router_rate_limit_effective_rpm,
            notice=rate_limit_notice,
        ),
        projection=ResponseCollectionProjection(
            refine_with_advisor=refine_message_with_advisor,
            remember_tool_uses=remember_channel_injected_tool_uses,
            prepend_text=prepend_anthropic_text,
        ),
        post_json_with_retry=post_json_with_rate_retry,
    )


def _identity_upstream_model(provider: str, pcfg: dict[str, Any], model: str) -> str:
    del provider, pcfg
    return model


def _build_ollama_collection_request(
    provider: str,
    model: str,
    body: dict[str, Any],
    pcfg: dict[str, Any],
    *,
    stream: bool,
) -> dict[str, Any]:
    del provider
    return ollama_chat_request(model, body, pcfg, stream=stream)


def collect_ollama_message_for_responses(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    strategy = ChatCollectionStrategy(
        operation="ollama_chat",
        build_request=_build_ollama_collection_request,
        decode_response=ollama_chat_to_anthropic,
        request_timeout_seconds=ollama_request_timeout_seconds,
        normalize_upstream_model=_identity_upstream_model,
        skip_rate_limit_during_compatibility_test=True,
    )
    return collect_chat_response(handler, provider, pcfg, body, strategy=strategy, services=response_collection_services())


def collect_openai_chat_message_for_responses(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    strategy = ChatCollectionStrategy(
        operation="openai_chat",
        build_request=openai_compatible_chat_request,
        decode_response=openai_chat_to_anthropic,
        request_timeout_seconds=provider_request_timeout_seconds,
        normalize_upstream_model=provider_upstream_model,
    )
    return collect_chat_response(handler, provider, pcfg, body, strategy=strategy, services=response_collection_services())


def collect_anthropic_message_for_responses(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    services = AnthropicCollectionServices(
        request=AnthropicCollectionRequest(
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            normalize_system_roles=normalize_anthropic_system_role_messages,
            cap_body=cap_anthropic_body_for_provider,
            apply_options=apply_provider_request_options,
            rehydrate_thinking=rehydrate_suppressed_thinking_passback,
            resolve_model=resolve_requested_model,
            normalize_upstream_model=provider_upstream_model,
            resolve_tool_models=resolve_tool_model_references,
            normalize_model_options=normalize_anthropic_model_request_options,
            strip_internal_metadata=body_without_ciel_runtime_internal_metadata,
        ),
        transport=AnthropicCollectionTransport(
            native_compat_enabled=provider_native_compat_enabled,
            native_base_url=native_anthropic_base_url,
            upstream_request_base=provider_upstream_request_base,
            join_url=join_url,
            messages_query=upstream_messages_query,
            provider_headers=provider_headers,
            apply_rate_limit=apply_router_rate_limit,
            open_request_with_retry=open_provider_request_with_key_retry,
            request_timeout_seconds=provider_request_timeout_seconds,
        ),
        projection=AnthropicCollectionProjection(
            normalize_response_thinking=normalize_response_thinking_for_non_anthropic_provider,
            append_synthetic_tasklist=append_synthetic_tasklist_to_message,
            prepend_text=prepend_anthropic_text,
            rate_limit_notice=rate_limit_notice,
        ),
        forwarded_headers=("anthropic-beta", "anthropic-dangerous-direct-browser-access"),
    )
    return collect_anthropic_response(handler, provider, pcfg, body, services=services)


def collect_provider_message_for_responses(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    upstream_model = resolve_requested_model(provider, pcfg, body.get("model"))
    protocol = select_provider_protocol(provider, pcfg, "openai_responses", upstream_model)
    collectors = {
        "ollama_chat": collect_ollama_message_for_responses,
        "openai_chat": collect_openai_chat_message_for_responses,
        "anthropic_messages": collect_anthropic_message_for_responses,
    }
    collector = collectors.get(protocol)
    if collector is None:
        provider_label = PROVIDER_LABELS.get(provider, provider)
        endpoint_family = protocol.replace("_", "-")
        raise RuntimeError(
            f"{provider_label} model {upstream_model!r} uses the {endpoint_family} endpoint family. "
            f"ciel-runtime currently routes {provider_label} /v1/messages and /v1/chat/completions models."
        )
    return collector(handler, provider, pcfg, body)


def codex_routed_upstream_headers(pcfg: dict[str, Any], inbound_headers: Any | None = None) -> dict[str, str]:
    del pcfg
    headers: dict[str, str] = {}
    hop_by_hop = {
        "connection",
        "content-length",
        "host",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    if inbound_headers is not None:
        for name, value in inbound_headers.items():
            low = str(name).lower()
            if low in hop_by_hop:
                continue
            if low == "accept-encoding":
                headers["accept-encoding"] = "identity"
                continue
            if low == "content-type":
                headers["content-type"] = str(value)
                continue
            if value:
                headers[str(name)] = str(value)
    if not any(str(k).lower() == "content-type" for k in headers):
        headers["content-type"] = "application/json"
    headers = with_upstream_user_agent(headers)
    if not any(str(k).lower() == "authorization" for k in headers):
        raise RuntimeError("Codex routed mode did not receive native Codex auth headers from the Codex CLI.")
    return headers


def codex_routed_auth_error_message(message: str) -> str:
    low = str(message or "").lower()
    if "api.responses.write" not in low and "insufficient permissions" not in low and "unauthorized" not in low:
        return message
    guidance = (
        " Codex routed is expected to forward Codex CLI native auth to the ChatGPT Codex backend. "
        "If this mentions api.responses.write, the request is still using the OpenAI Platform /v1 endpoint; "
        "upgrade ciel-runtime and relaunch Codex routed so the local base URL is /backend-api/codex."
    )
    return f"{message}{guidance}"


def _responses_input_as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": value}]}]
    if isinstance(value, dict):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def _responses_message_item(role: str, text: str) -> dict[str, Any]:
    role = role if role in ("user", "assistant", "system", "developer") else "user"
    text_type = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": text_type, "text": text}]}


def codex_responses_body_with_channel_context(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    delivery_body = openai_responses_to_anthropic_messages(body, str(body.get("model") or ""))
    original_count = len(delivery_body.get("messages") or [])
    delivery_body = body_with_pending_channel_messages(delivery_body)
    delivery_body = body_with_channel_tool_result_context(delivery_body)
    messages = [m for m in delivery_body.get("messages") or [] if isinstance(m, dict)]
    additions = messages[original_count:]
    metadata = delivery_body.get("metadata") if isinstance(delivery_body.get("metadata"), dict) else {}
    out = dict(body)
    # ChatGPT Codex backend rejects metadata. Keep Ciel Runtime delivery cursors
    # in delivery_body only; do not forward them upstream.
    out.pop("metadata", None)
    if not additions and not metadata:
        return out, delivery_body
    input_items = _responses_input_as_list(body.get("input", []))
    for message in additions:
        text = anthropic_content_to_text(message.get("content"))
        if text.strip():
            input_items.append(_responses_message_item(str(message.get("role") or "user"), text))
    if input_items:
        out["input"] = input_items
    return out, delivery_body


def _copy_upstream_response_headers(handler: BaseHTTPRequestHandler, headers: Any) -> None:
    skipped = {"connection", "content-length", "transfer-encoding", "content-encoding"}
    try:
        items = headers.items()
    except Exception:
        items = []
    wrote_content_type = False
    for key, value in items:
        low = str(key).lower()
        if low in skipped:
            continue
        if low == "content-type":
            wrote_content_type = True
        handler.send_header(str(key), str(value))
    if not wrote_content_type:
        handler.send_header("content-type", "application/json")
    handler.send_header("connection", "close")


def codex_backend_upstream_url(request_path: str, query: str = "") -> str:
    parsed_path = urllib.parse.urlparse(request_path).path
    prefixes = ("/backend-api/codex", "/v1")
    suffix = parsed_path
    for prefix in prefixes:
        if parsed_path == prefix:
            suffix = ""
            break
        if parsed_path.startswith(prefix + "/"):
            suffix = parsed_path[len(prefix):]
            break
    url = join_url(CODEX_ROUTED_UPSTREAM_BASE, suffix)
    if query:
        url = f"{url}?{query}"
    return url


def forward_codex_backend_json(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    mutate_responses: bool = False,
) -> dict[str, Any] | None:
    upstream_body = body
    delivery_body: dict[str, Any] | None = None
    if mutate_responses:
        upstream_body, delivery_body = codex_responses_body_with_channel_context(body)
        begin_pending_channel_delivery(handler, delivery_body)
    parsed = urllib.parse.urlparse(handler.path)
    url = codex_backend_upstream_url(parsed.path, parsed.query)
    headers = codex_routed_upstream_headers(pcfg, handler.headers)
    data = json.dumps(upstream_body).encode("utf-8")
    max_capacity_retries = codex_capacity_retry_limit() if mutate_responses else 0
    for attempt in range(max_capacity_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with provider_urlopen(req, timeout=provider_request_timeout_seconds(pcfg), provider=provider, pcfg=pcfg) as resp:
            preamble = read_codex_response_preamble(resp) if mutate_responses else None
            if preamble is not None and preamble.capacity_error_code and attempt < max_capacity_retries:
                retry_no = attempt + 1
                wait = upstream_retry_wait_seconds(retry_no)
                model = str(upstream_body.get("model") or "")
                router_log(
                    "WARN",
                    "codex_capacity_retry model=%s attempt=%d/%d code=%s wait=%.2fs"
                    % (model, retry_no, max_capacity_retries, preamble.capacity_error_code, wait),
                )
                EVENT_BUS.publish(
                    level="warn",
                    category="router.retry",
                    message="Codex model capacity retry",
                    provider=provider,
                    model=model,
                    data={
                        "attempt": retry_no,
                        "total": max_capacity_retries,
                        "code": preamble.capacity_error_code,
                        "wait_seconds": wait,
                    },
                )
                time.sleep(wait)
                continue

            handler.send_response(getattr(resp, "status", 200))
            _copy_upstream_response_headers(handler, resp.headers)
            handler.end_headers()
            if preamble is not None and preamble.payload:
                handler.wfile.write(preamble.payload)
                handler.wfile.flush()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
            break
    return delivery_body


def codex_capacity_retry_limit() -> int:
    raw = str(os.environ.get("CIEL_RUNTIME_CODEX_CAPACITY_RETRIES") or "3").strip()
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return 3


def forward_codex_backend_get(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any]) -> None:
    parsed = urllib.parse.urlparse(handler.path)
    url = codex_backend_upstream_url(parsed.path, parsed.query)
    headers = codex_routed_upstream_headers(pcfg, handler.headers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    with provider_urlopen(req, timeout=provider_request_timeout_seconds(pcfg), provider=provider, pcfg=pcfg) as resp:
        handler.send_response(getattr(resp, "status", 200))
        _copy_upstream_response_headers(handler, resp.headers)
        handler.end_headers()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()


def forward_codex_responses(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> None:
    delivery_body = forward_codex_backend_json(handler, provider, pcfg, body, mutate_responses=True)
    if delivery_body is None:
        return
    mark_pending_channel_delivery_success(handler, "codex_responses_proxy")
    commit_pending_channel_delivery_cursors(delivery_body, handler)


def handle_openai_responses_post(
    handler: BaseHTTPRequestHandler,
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> None:
    handle_openai_responses_request(
        handler,
        cfg,
        provider,
        pcfg,
        body,
        OpenAIResponsesServices(
            core=OpenAIResponsesCore(
                event_bus=EVENT_BUS,
                request_id=lambda: f"{os.getpid()}-{time.time_ns()}",
                input_as_list=_responses_input_as_list,
                is_client_disconnect=is_client_disconnect_error,
                log=router_log,
            ),
            conversion=OpenAIResponsesConversion(
                to_anthropic=openai_responses_to_anthropic_messages,
                current_alias=current_alias,
                update_tool_schema=_update_tool_schema_registry,
                normalize_thinking=normalize_thinking_for_non_anthropic_provider,
                filter_blocked_tools=filter_blocked_tools,
                normalize_tool_choice=normalize_tool_choice_for_provider,
                write_context_usage=write_context_usage,
                strip_advisor_tools=strip_autonomous_advisor_server_tools,
                inject_channel_context=body_with_pending_channel_messages,
                inject_tool_result_context=body_with_channel_tool_result_context,
            ),
            routing=OpenAIResponsesRouting(
                maybe_import_session=maybe_handle_import_session_request,
                codex_routed_enabled=codex_routed_enabled,
                forward_codex=forward_codex_responses,
                dump_request=dump_request_for_trace,
                normalize_provider_wire=normalize_request_for_provider_wire,
                collect_message=collect_provider_message_for_responses,
            ),
            delivery=OpenAIResponsesDelivery(
                begin=begin_pending_channel_delivery,
                mark_success=mark_pending_channel_delivery_success,
                mark_failed=mark_pending_channel_delivery_failed,
                commit=commit_pending_channel_delivery_cursors,
            ),
            output=OpenAIResponsesOutput(
                write_response=write_openai_responses_response,
                write_error=write_openai_responses_error,
                upstream_error_message=upstream_http_error_message,
                codex_auth_error_message=codex_routed_auth_error_message,
                event_preview=router_event_message_preview,
            ),
        ),
    )


def handle_codex_backend_passthrough_post(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
) -> None:
    try:
        forward_codex_backend_json(handler, provider, pcfg, body, mutate_responses=False)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        write_openai_responses_error(handler, upstream_http_error_message(exc, raw), stream=False, status=exc.code)
    except Exception as exc:
        if is_client_disconnect_error(exc):
            return
        write_openai_responses_error(handler, f"{type(exc).__name__}: {exc}", stream=False)


def handle_codex_backend_passthrough_get(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any]) -> None:
    try:
        forward_codex_backend_get(handler, provider, pcfg)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        write_json(handler, {"error": {"message": upstream_http_error_message(exc, raw)}}, status=exc.code)
    except Exception as exc:
        if is_client_disconnect_error(exc):
            return
        write_json(handler, {"error": {"message": f"{type(exc).__name__}: {exc}"}}, status=502)


def build_claude_router_services() -> ClaudeRouterServices:
    return ClaudeRouterServices(
        core=ClaudeRouterCore(
            event_bus=EVENT_BUS,
            log=router_log,
            try_write_json=try_write_json,
        ),
        count_tokens=ClaudeRouterCountTokens(
            estimate_tokens=estimate_tokens,
            write_context_usage=write_context_usage,
            write_json=write_json,
        ),
        pipeline=ClaudeRouterPipeline(
            update_tool_schema_registry=_update_tool_schema_registry,
            router_event_message_preview=router_event_message_preview,
            dump_request_for_trace=dump_request_for_trace,
            filter_blocked_tools=filter_blocked_tools,
            normalize_tool_choice=normalize_tool_choice_for_provider,
            write_context_usage=write_context_usage,
            strip_advisor_tools=strip_autonomous_advisor_server_tools,
            inject_channel_context=body_with_pending_channel_messages,
            inject_tool_result_context=body_with_channel_tool_result_context,
        ),
        shortcuts=ClaudeRouterShortcuts(
            plan_mode=maybe_handle_plan_mode_tool_choice,
            router_debug=maybe_handle_router_debug_request,
            version=maybe_handle_version_request,
            channel_clear=maybe_handle_channel_clear_request,
            import_session=maybe_handle_import_session_request,
            llm_options=maybe_handle_live_llm_options_request,
            api_keys=maybe_handle_live_api_keys_request,
            advisor=maybe_handle_advisor_request,
        ),
        delivery=ClaudeRouterDelivery(
            begin=begin_pending_channel_delivery,
            commit=commit_pending_channel_delivery_cursors,
            mark_failed=mark_pending_channel_delivery_failed,
            mark_success=mark_pending_channel_delivery_success,
            is_client_disconnect=is_client_disconnect_error,
            write_activity=write_router_activity,
        ),
        routing=ClaudeRouterRouting(
            forward_ollama=forward_ollama_api_chat,
            forward_openai=forward_openai_compatible_chat,
            select_protocol=select_provider_protocol,
            request_policy=provider_request_policy,
            resolve_model=resolve_requested_model,
            provider_labels=PROVIDER_LABELS,
            write_json=write_json,
        ),
        normalization=ClaudeRouterNativeNormalization(
            normalize_provider_wire=normalize_request_for_provider_wire,
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            normalize_system_roles=normalize_anthropic_system_role_messages,
            cap_body=cap_anthropic_body_for_provider,
            apply_request_options=apply_provider_request_options,
            rehydrate_thinking=rehydrate_suppressed_thinking_passback,
            ncp_model_id=ncp_model_id_for_nvidia_hosted,
            resolve_tool_models=resolve_tool_model_references,
            normalize_model_options=normalize_anthropic_model_request_options,
            strip_internal_metadata=body_without_ciel_runtime_internal_metadata,
        ),
        transport=ClaudeRouterTransport(
            native_base_url=native_anthropic_base_url,
            native_compat_enabled=provider_native_compat_enabled,
            upstream_base=provider_upstream_request_base,
            join_url=join_url,
            upstream_query=upstream_messages_query,
            provider_headers=provider_headers,
            apply_rate_limit=apply_router_rate_limit,
            open_request=open_provider_request_with_key_retry,
            request_timeout=provider_request_timeout_seconds,
            idle_timeout=provider_stream_idle_timeout_seconds,
        ),
        response=ClaudeRouterResponse(
            rebatch_sse=_rebatch_anthropic_sse_text,
            preserves_thinking=preserves_anthropic_thinking_contract,
            normalize_stream_tool_use=should_normalize_anthropic_stream_tool_use,
            set_stream_timeout=set_upstream_stream_read_timeout,
            normalize_thinking=normalize_response_thinking_for_non_anthropic_provider,
            append_tasklist=append_synthetic_tasklist_to_message,
            prepend_text=prepend_anthropic_text,
            rate_limit_notice=rate_limit_notice,
            register_key_cooldown=register_api_key_cooldown,
            key_from_headers=key_from_request_headers,
        ),
    )


def build_runtime_routers() -> tuple[Any, ...]:
    return (
        CodexRouter(
            routed_enabled=codex_routed_enabled,
            handle_responses_post=handle_openai_responses_post,
            handle_backend_passthrough_post=handle_codex_backend_passthrough_post,
            handle_backend_passthrough_get=handle_codex_backend_passthrough_get,
        ),
        ClaudeRouter(services=build_claude_router_services()),
    )


def runtime_router_capability_matrix() -> dict[str, dict[str, Any]]:
    return router_capability_matrix(build_runtime_routers())


def runtime_router_capability_gaps() -> dict[str, list[str]]:
    return missing_common_capabilities(build_runtime_routers())


def route_runtime_get(handler: BaseHTTPRequestHandler, path: str, provider: str, pcfg: dict[str, Any]) -> bool:
    for router in build_runtime_routers():
        if router.can_handle_get(path, provider, pcfg):
            return bool(router.handle_get(handler, path, provider, pcfg))
    return False


def route_runtime_post(
    handler: BaseHTTPRequestHandler,
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
    path: str,
    body: dict[str, Any],
) -> bool:
    for router in build_runtime_routers():
        if router.can_handle_post(path, provider, pcfg):
            return bool(router.handle_post(handler, cfg, provider, pcfg, path, body))
    return False


def router_health_payload(
    cfg: dict[str, Any],
    provider: str,
    pcfg: dict[str, Any],
) -> dict[str, Any]:
    del pcfg
    return {
        "ok": True,
        "version": VERSION,
        "source_fingerprint": SOURCE_FINGERPRINT,
        "pid": os.getpid(),
        "user": getpass.getuser(),
        "home": str(HOME),
        "config_dir": str(CONFIG_DIR),
        "router_port": ROUTER_PORT,
        "provider": provider,
        "model": current_alias(cfg),
        "web_chat": "/ca/web/chat",
        "chat": "/ca/chat/health",
        "plan": "/ca/plan/artifacts",
        "events": "/ca/events",
    }


def build_router_http_services() -> RouterHttpServices:
    return RouterHttpServices(
        core=RouterHttpCore(
            load_config=load_config,
            reject_external=reject_external_router_request,
            get_current_provider=get_current_provider,
            parse_json_body=parse_json_body,
            is_client_disconnect=is_client_disconnect_error,
            log=router_log,
        ),
        get=RouterHttpGetEndpoints(
            codex_mcp_split=handle_codex_mcp_split_proxy_get,
            events=handle_events_get,
            llm_config=handle_llm_config_get,
            channel_mcp=handle_channel_mcp_get,
            web=handle_web_get,
            chat=handle_chat_get,
            plan=handle_plan_get,
            runtime=route_runtime_get,
        ),
        post=RouterHttpPostEndpoints(
            codex_mcp_split=handle_codex_mcp_split_proxy_request,
            llm_config=handle_llm_config_post,
            channel_mcp=handle_channel_mcp_post,
            chat=handle_chat_post,
            plan=handle_plan_post,
            runtime=route_runtime_post,
        ),
        presentation=RouterHttpPresentation(
            home_html=render_router_home_html,
            health_payload=router_health_payload,
            write_text=write_text_response,
            write_json=write_json,
            list_models=list_model_objects_for_request,
            resolve_model=resolve_requested_model,
            model_object=model_object,
        ),
        errors=RouterHttpErrors(
            write_responses_error=write_openai_responses_error,
            try_write_json=try_write_json,
        ),
    )


class RouterHandler(RouterHttpHandler):
    services_factory = staticmethod(build_router_http_services)


def serve(_: argparse.Namespace) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    reset_api_key_cooldowns_for_router_start()
    bind_host = router_bind_host(cfg)
    PID_PATH.write_text(str(os.getpid()))
    os.chmod(PID_PATH, 0o600)
    lvl = current_log_level()
    src = "file" if LOG_LEVEL_PATH.exists() else ("env" if os.environ.get("CIEL_RUNTIME_LOG_LEVEL") else "default")
    sys.stderr.write(
        f"ciel-runtime router starting on {bind_host}:{ROUTER_PORT} "
        f"(client base {ROUTER_BASE}, log level {LOG_LEVEL_NAMES.get(lvl, lvl)}, source={src})\n"
    )
    sys.stderr.flush()
    server = ThreadingHTTPServer((bind_host, ROUTER_PORT), RouterHandler)
    start_managed_router_lifetime_watchdog(server)
    channel_start_thread = threading.Thread(
        target=lambda: start_router_managed_channel_sse(cfg),
        daemon=True,
        name="ca-router-channel-sse-start",
    )
    channel_start_thread.start()
    try:
        server.serve_forever()
    finally:
        stop_channel_sse_connection(None)
        try:
            PID_PATH.unlink()
        except FileNotFoundError:
            pass


def router_health() -> dict[str, Any] | None:
    try:
        data = http_json(f"{ROUTER_BASE}/health", timeout=1.0)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def router_port_connectivity_summary(timeout: float = 0.5) -> str:
    parsed = urllib.parse.urlparse(ROUTER_BASE)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or ROUTER_PORT
    try:
        with socket.create_connection((host, port), timeout=max(0.1, float(timeout))):
            return f"tcp={host}:{port}:ok"
    except Exception as exc:
        return f"tcp={host}:{port}:{type(exc).__name__}: {exc}"


def router_health_summary(health: dict[str, Any] | None = None) -> str:
    if health is None:
        health = router_health()
    if isinstance(health, dict):
        return (
            "health=ok "
            f"base={ROUTER_BASE} "
            f"pid={health.get('pid') or '-'} "
            f"version={health.get('version') or '-'} "
            f"source={health.get('source_fingerprint') or '-'} "
            f"provider={health.get('provider') or '-'} "
            f"model={health.get('model') or '-'} "
            f"config_dir={health.get('config_dir') or '-'}"
        )
    pid_state = "missing"
    try:
        if PID_PATH.exists():
            pid_state = PID_PATH.read_text(encoding="utf-8").strip() or "empty"
    except Exception as exc:
        pid_state = f"read_failed:{type(exc).__name__}"
    return f"health=down base={ROUTER_BASE} pid_file={pid_state} {router_port_connectivity_summary()}"


def router_up() -> bool:
    return router_health() is not None


def router_health_matches_current(health: dict[str, Any] | None) -> bool:
    if health is None:
        return False
    if str(health.get("version") or "") != VERSION:
        return False
    if str(health.get("source_fingerprint") or "") != SOURCE_FINGERPRINT:
        return False
    if str(health.get("user") or "") != getpass.getuser():
        return False
    if not router_health_config_matches_current(health):
        return False
    return True


def _path_identity_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve(strict=False))
    except Exception:
        return text


def router_health_config_matches_current(health: dict[str, Any] | None) -> bool:
    if not isinstance(health, dict):
        return False
    return _path_identity_text(health.get("config_dir")) == _path_identity_text(CONFIG_DIR)


def router_health_has_foreign_config(health: dict[str, Any] | None) -> bool:
    if not isinstance(health, dict):
        return False
    config_dir = _path_identity_text(health.get("config_dir"))
    return bool(config_dir) and config_dir != _path_identity_text(CONFIG_DIR)


def invalid_nvidia_hosted_base_url(value: str | None) -> bool:
    text = (value or "").strip()
    if not text or text.startswith("nv" + "api-") or not text.startswith(("http://", "https://")):
        return True
    parsed = urllib.parse.urlparse(text)
    return (parsed.hostname or "") in ("127.0.0.1", "localhost")


def ensure_nvidia_hosted_base_url(pcfg: dict[str, Any]) -> bool:
    if invalid_nvidia_hosted_base_url(pcfg.get("base_url")):
        pcfg["base_url"] = nvidia_upstream_base_url()
        return True
    return False


def store_nvidia_api_key(key: str) -> None:
    env = read_env_file(NCP_ENV)
    env["NVIDIA_API_KEY"] = key
    env.setdefault("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    env.setdefault("PROXY_HOST", "127.0.0.1")
    env.setdefault("PROXY_PORT", "8788")
    env.setdefault("STORAGE_ENGINE", "sqlite")
    NCP_ENV.parent.mkdir(parents=True, exist_ok=True)
    NCP_ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    os.chmod(NCP_ENV, 0o600)


def clear_nvidia_api_key() -> None:
    env = read_env_file(NCP_ENV)
    if "NVIDIA_API_KEY" not in env:
        return
    env.pop("NVIDIA_API_KEY", None)
    NCP_ENV.parent.mkdir(parents=True, exist_ok=True)
    NCP_ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    os.chmod(NCP_ENV, 0o600)


def set_provider_config(provider: str) -> list[str]:
    cfg = load_config()
    cfg["current_provider"] = provider
    pcfg = cfg["providers"][provider]
    adapter = configured_provider_adapter(provider, pcfg)
    contract = provider_contract_config(provider, pcfg)
    pcfg.update(adapter.selection_config_updates(contract))
    fixed_base = ensure_nvidia_hosted_base_url(pcfg) if provider == "nvidia-hosted" else False
    save_config(cfg)
    clear_model_cache()
    lines = [f"Provider set to {provider} ({PROVIDER_LABELS[provider]})."]
    lines.extend(adapter.selection_status_lines(provider_contract_config(provider, pcfg)))
    if fixed_base:
        lines.append(f"Base URL set to {pcfg['base_url']} for NVIDIA hosted.")
    return lines


def set_provider_choice_config(choice: str) -> list[str]:
    cfg = load_config()
    normalized_choice = normalize_provider_choice(choice) or choice
    if normalized_choice in (ANTHROPIC_NATIVE_PROVIDER_CHOICE, ANTHROPIC_ROUTED_PROVIDER_CHOICE):
        cfg["current_provider"] = "anthropic"
        pcfg = cfg["providers"]["anthropic"]
        routed = normalized_choice == ANTHROPIC_ROUTED_PROVIDER_CHOICE
        pcfg["route_through_router"] = routed
        save_config(cfg)
        clear_model_cache()
        if routed:
            lines = [
                "Provider set to anthropic (Anthropic routed).",
                "mode: anthropic-routed",
            ]
            if not provider_has_api_key("anthropic", pcfg):
                lines.append("Anthropic routed mode will use Claude Code OAuth/API auth headers when available.")
            return lines
        return [
            "Provider set to anthropic (Claude Native).",
            "mode: anthropic-native",
            "Claude Code OAuth/Max can be used directly, but ciel-runtime router features such as /advisor are unavailable.",
        ]
    if normalized_choice in (AGY_NATIVE_PROVIDER_CHOICE, AGY_ROUTED_PROVIDER_CHOICE):
        cfg["current_provider"] = "agy"
        pcfg = cfg["providers"]["agy"]
        routed = normalized_choice == AGY_ROUTED_PROVIDER_CHOICE
        pcfg["route_through_router"] = routed
        save_config(cfg)
        clear_model_cache()
        if routed:
            return [
                "Provider set to agy (AGY routed).",
                "mode: agy-routed",
                "AGY uses native Google Antigravity auth/settings; Ciel Runtime adds channel/PTY wake support only.",
            ]
        return [
            "Provider set to agy (AGY).",
            "mode: agy-native",
            "AGY runs with its own native settings; Ciel Runtime router features are unavailable.",
        ]
    if normalized_choice in (CODEX_NATIVE_PROVIDER_CHOICE, CODEX_ROUTED_PROVIDER_CHOICE):
        cfg["current_provider"] = "codex"
        pcfg = cfg["providers"]["codex"]
        routed = normalized_choice == CODEX_ROUTED_PROVIDER_CHOICE
        pcfg["route_through_router"] = routed
        save_config(cfg)
        clear_model_cache()
        if routed:
            return [
                "Provider set to codex (Codex routed).",
                "mode: codex-routed",
                "Codex uses its native OpenAI account/config, with base URL routed through ciel-runtime.",
            ]
        return [
            "Provider set to codex (Codex Native).",
            "mode: codex-native",
            "Codex runs with its own native settings; ciel-runtime router features are unavailable.",
        ]
    return set_provider_config(normalized_choice)


def set_base_url_config(provider: str, url: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    if provider == "nvidia-hosted" and invalid_nvidia_hosted_base_url(url):
        url = nvidia_upstream_base_url()
    old_url = str(pcfg.get("base_url") or "").rstrip("/")
    new_url = url.rstrip("/")
    pcfg["base_url"] = new_url
    reset_model = old_url != new_url
    if reset_model:
        pcfg["current_model"] = ""
        pcfg["custom_models"] = []
    lines = [f"Base URL for {provider} set to {pcfg['base_url']}."]
    if reset_model:
        clear_model_cache()
        lines.append("Model selection was reset because the provider endpoint changed.")
        detected_native, detect_reason = auto_detect_native_compat_for_base_url(provider, pcfg)
        if detected_native is None:
            if provider in AUTO_DETECT_NATIVE_COMPAT_PROVIDERS:
                pcfg["native_compat"] = True
                lines.append(f"Endpoint auto-detect inconclusive ({detect_reason}); Native compatibility kept on as the Anthropic default.")
        else:
            pcfg["native_compat"] = bool(detected_native)
            mode = "enabled" if detected_native else "disabled"
            lines.append(f"Endpoint auto-detected ({detect_reason}); Native compatibility {mode}.")
        selected, selection_lines = ensure_current_model_from_provider_list(provider, pcfg, force_refresh=True)
        lines.extend(selection_lines)
        if not selected:
            lines.append("Choose a model before running compatibility test or Launch Claude Code.")
    save_config(cfg)
    if not reset_model:
        clear_model_cache()
    return lines


def set_model_config(value: str) -> list[str]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    mmap = model_map_for(provider, pcfg, fetch=False)
    model_id = normalize_model_id(provider, unslug_provider_alias(provider, value, mmap) or value)
    pcfg["current_model"] = model_id
    model_profile_msgs = apply_provider_model_profile(provider, pcfg)
    if provider == "zai":
        # Claude Code primarily selects through its default Haiku/Sonnet/Opus
        # model environment variables. Keep those aligned when the user
        # explicitly changes the Z.AI model from the menu/CLI; otherwise a
        # Flash selection can still run through the previous Sonnet/Opus model.
        pcfg["haiku_model"] = model_id
        pcfg["opus_model"] = model_id
        pcfg["sonnet_model"] = model_id
    selected_info = read_model_info_cache(provider, pcfg).get(model_id) or {}
    selected_context = positive_int(selected_info.get("max_model_len"))
    if selected_context:
        pcfg["max_model_len"] = selected_context
    preset = model_preset(model_id)
    if preset.get("num_ctx_min"):
        pcfg["num_ctx_min"] = preset["num_ctx_min"]
    if preset.get("num_ctx_max"):
        pcfg["num_ctx_max"] = preset["num_ctx_max"]
    context_msgs = sync_ollama_library_context_limit(provider, pcfg, model_id)
    context_msgs.extend(cap_context_settings_to_model_capacity(provider, pcfg))
    preset_msgs = auto_apply_recommended_llm_preset_for_model(provider, pcfg, cfg.get("language", "en"))
    timeout_msgs = apply_recommended_timeout_for_model_context(provider, pcfg, use_context_fallback=False)
    known = read_model_list_cache(provider, pcfg) or []
    custom = pcfg.setdefault("custom_models", [])
    if model_id not in custom and model_id not in known:
        custom.append(model_id)
    save_config(cfg)
    clear_model_cache()
    msgs = [f"Model for {provider} set to {model_id}.", f"Claude Code alias: {alias_for(provider, model_id)}"]
    msgs.extend(model_profile_msgs)
    if selected_context:
        msgs.append(f"Model context size: {format_context_tokens(selected_context)} ({selected_context:,} tokens).")
    msgs.extend(context_msgs)
    msgs.extend(preset_msgs)
    msgs.extend(timeout_msgs)
    if preset.get("thinking"):
        msgs.append("Note: this is a thinking model; compatibility test uses extended token budget.")
    return msgs


def set_advisor_model_config(value: str) -> list[str]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    if provider == "anthropic":
        return [
            "Anthropic modes use Claude Code's built-in /advisor; "
            "run /advisor in the session to pick its model."
        ]
    model_id = normalize_model_id(provider, value.strip()) if value.strip() else ""
    pcfg["advisor_model"] = model_id
    if model_id:
        known = read_model_list_cache(provider, pcfg) or []
        custom = pcfg.setdefault("custom_models", [])
        if model_id not in custom and model_id not in known:
            custom.append(model_id)
    save_config(cfg)
    clear_model_cache()
    if not model_id:
        return [f"Advisor Model for {provider} disabled."]
    return [f"Advisor Model for {provider} set to {model_id}."]


def store_api_key_config(provider: str, key: str) -> list[str]:
    if api_key_clear_requested(key):
        return clear_api_key_config(provider)
    if provider == "nvidia-hosted":
        store_nvidia_api_key(key)
        cfg = load_config()
        cfg["providers"][provider].pop("api_keys", None)
        if ensure_nvidia_hosted_base_url(cfg["providers"][provider]):
            save_config(cfg)
        location = str(NCP_ENV)
    else:
        cfg = load_config()
        cfg["providers"][provider]["api_key"] = key
        cfg["providers"][provider].pop("api_keys", None)
        save_config(cfg)
        location = str(CONFIG_PATH)
    clear_model_cache()
    return [
        f"Stored API key for {provider}.",
        f"Saved: {mask_secret(key)}; fp {secret_fingerprint(key)} in {location}",
    ]


def clear_api_key_config(provider: str) -> list[str]:
    cfg = load_config()
    providers = cfg["providers"]
    missing = object()
    other_key_fields: dict[str, tuple[Any, Any]] = {}
    for name, other_pcfg in providers.items():
        if name == provider or not isinstance(other_pcfg, dict):
            continue
        api_key_value = other_pcfg.get("api_key", missing)
        api_keys_value = json.loads(json.dumps(other_pcfg.get("api_keys"))) if "api_keys" in other_pcfg else missing
        other_key_fields[name] = (api_key_value, api_keys_value)
    pcfg = cfg["providers"][provider]
    had_config_key = bool(parse_api_key_list(pcfg.get("api_key")) or parse_api_key_list(pcfg.get("api_keys")))
    pcfg.pop("api_key", None)
    pcfg.pop("api_keys", None)
    if provider == "nvidia-hosted":
        had_config_key = had_config_key or bool(parse_api_key_list(read_env_file(NCP_ENV).get("NVIDIA_API_KEY")))
        clear_nvidia_api_key()
        ensure_nvidia_hosted_base_url(pcfg)
    for name, (api_key_value, api_keys_value) in other_key_fields.items():
        other_pcfg = providers.get(name)
        if not isinstance(other_pcfg, dict):
            continue
        if api_key_value is missing:
            other_pcfg.pop("api_key", None)
        else:
            other_pcfg["api_key"] = api_key_value
        if api_keys_value is missing:
            other_pcfg.pop("api_keys", None)
        else:
            other_pcfg["api_keys"] = api_keys_value
    save_config(cfg)
    clear_model_cache()
    with _API_KEY_ROTATION_LOCK:
        _API_KEY_ROTATION_CURSOR.pop(provider_api_key_rotation_name(provider, pcfg), None)
    if had_config_key:
        return [f"Cleared stored API key(s) for {provider}. Other providers unchanged."]
    return [f"No stored API key(s) for {provider}; other providers unchanged."]


def store_api_keys_config(provider: str, keys: list[str]) -> list[str]:
    parsed = parse_api_key_list(keys)
    if len(parsed) == 1 and api_key_clear_requested(parsed[0]):
        return clear_api_key_config(provider)
    if not parsed:
        raise SystemExit("No API keys provided; unchanged.")
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    pcfg["api_key"] = parsed[0]
    if len(parsed) > 1:
        pcfg["api_keys"] = parsed
    else:
        pcfg.pop("api_keys", None)
    if provider == "nvidia-hosted":
        store_nvidia_api_key(parsed[0])
        ensure_nvidia_hosted_base_url(pcfg)
    save_config(cfg)
    clear_model_cache()
    with _API_KEY_ROTATION_LOCK:
        _API_KEY_ROTATION_CURSOR.pop(provider_api_key_rotation_name(provider, pcfg), None)
    return [
        f"Stored {len(parsed)} API key{'s' if len(parsed) != 1 else ''} for {provider}.",
        f"Round-robin: {'enabled' if len(parsed) > 1 else 'disabled'}",
        f"Primary: {mask_secret(parsed[0])}; fp {secret_fingerprint(parsed[0])}",
    ]


def mask_secret(value: str | None) -> str:
    text = value or ""
    if not text:
        return "not set"
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


def secret_fingerprint(value: str | None, length: int = 12) -> str:
    text = value or ""
    if not text:
        return "-"
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return digest[: max(4, length)]


SECRET_TEXT_PATTERNS = (
    re.compile(r"ak_key_[A-Za-z0-9_-]+_secret_[A-Za-z0-9_-]+"),
    re.compile(r"(AINET_API_KEY\s*=\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(Authorization\s*:\s*Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(token=)(ak_key_[A-Za-z0-9_-]+_secret_[A-Za-z0-9_-]+)", re.IGNORECASE),
)


def redact_sensitive_text(text: str) -> str:
    redacted = text
    redacted = SECRET_TEXT_PATTERNS[0].sub(lambda m: mask_secret(m.group(0)), redacted)
    for pattern in SECRET_TEXT_PATTERNS[1:]:
        redacted = pattern.sub(lambda m: f"{m.group(1)}{mask_secret(m.group(2))}", redacted)
    return redacted


def redact_sensitive_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, list):
        return [redact_sensitive_obj(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"api_key", "api_keys", "apikey", "token", "authorization", "bearer_token"}:
                redacted[key] = mask_secret(str(item))
            else:
                redacted[key] = redact_sensitive_obj(item)
        return redacted
    return value


def stored_api_key_mask(provider: str, pcfg: dict[str, Any]) -> str:
    keys = provider_config_api_keys(provider, pcfg)
    if not keys:
        return "not set"
    primary = f"{mask_secret(keys[0])}; fp {secret_fingerprint(keys[0])}"
    if len(keys) == 1:
        return primary
    return f"{len(keys)} keys (round-robin; primary {primary})"


def store_api_key_input_config(provider: str, raw_value: str) -> list[str]:
    if api_key_clear_requested(raw_value):
        return clear_api_key_config(provider)
    keys = parse_api_key_list(raw_value)
    if len(keys) > 1:
        return store_api_keys_config(provider, keys)
    if len(keys) == 1:
        return store_api_key_config(provider, keys[0])
    raise SystemExit("No API key provided; unchanged.")


def read_clipboard_text() -> str:
    commands: list[list[str]] = []
    if os.name == "nt":
        commands.append(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"])
    elif sys.platform == "darwin":
        commands.append(["pbpaste"])
    else:
        commands.extend([["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]])
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass
    return ""


def cmd_provider(args: argparse.Namespace) -> None:
    cfg = load_config()
    if not args.name:
        cur = cfg["current_provider"]
        pcfg = cfg["providers"].get(cur, {})
        rows, _values = provider_panel_rows(cfg)
        print("Available providers (current: %s)" % provider_menu_label(cur, pcfg))
        for i, row in enumerate(rows, 1):
            print(f" {i:>2}. {row}")
        print("\nUse: /provider <name>")
        print("Examples: /provider codex, /provider codex-routed, /provider ollama")
        print("Then run /model to choose a model for the selected provider.")
        return
    choice = normalize_provider_choice(args.name)
    if choice:
        lines = set_provider_choice_config(choice)
    else:
        provider = normalize_provider(args.name)
        lines = set_provider_config(provider)
    for line in lines:
        print(line)
    print("Gateway model cache cleared. Run /model to refresh the model picker.")


def cmd_set_api_key(args: argparse.Namespace) -> None:
    provider = normalize_provider(args.provider)
    key = args.key.strip()
    if not key:
        raise SystemExit("No key provided; unchanged.")
    for line in store_api_key_input_config(provider, key):
        print(line)


def cmd_set_api_keys(args: argparse.Namespace) -> None:
    provider = normalize_provider(args.provider)
    raw = "\n".join(str(item) for item in getattr(args, "keys", []) if str(item).strip())
    keys = parse_api_key_list(raw)
    if not keys:
        raise SystemExit("No API keys provided; unchanged.")
    for line in store_api_keys_config(provider, keys):
        print(line)


def cmd_api_key(args: argparse.Namespace) -> None:
    cfg = load_config()
    if not args.provider:
        print("API key status:")
        for p, pcfg in cfg["providers"].items():
            needs = p in ("anthropic", "ollama-cloud", "deepseek", "opencode", "opencode-go", "kimi", "nvidia-hosted", "openrouter", "fireworks")
            count = provider_api_key_count(p, pcfg)
            label = f"{count} keys (round-robin)" if count > 1 else ("set" if count == 1 else ("missing" if needs else "not required"))
            primary = provider_primary_api_key(p, pcfg)
            suffix = f" (primary {mask_secret(primary)}; fp {secret_fingerprint(primary)})" if count else ""
            print(f" {p:<15} {label}{suffix}")
        print("\nSet securely from terminal: ciel-runtimectl api-key anthropic")
        print("Set multiple keys: ciel-runtimectl set-api-keys deepseek KEY1,KEY2")
        print("For NVIDIA hosted, use: ciel-runtimectl api-key nvidia-hosted")
        return
    provider = normalize_provider(args.provider)
    action = str(getattr(args, "action", "") or "").strip()
    if api_key_clear_requested(action):
        for line in clear_api_key_config(provider):
            print(line)
        return
    if not sys.stdin.isatty():
        print("For security, do not paste API keys into Claude Code chat.")
        print(f"Run this in the SSH terminal instead: ciel-runtimectl api-key {provider}")
        return
    key = getpass.getpass(f"API key for {provider}: ").strip()
    if not key:
        raise SystemExit("No key entered; unchanged.")
    for line in store_api_key_input_config(provider, key):
        print(line)


def cmd_base_url(args: argparse.Namespace) -> None:
    provider = normalize_provider(args.provider)
    for line in set_base_url_config(provider, args.url):
        print(line)


def cmd_model(args: argparse.Namespace) -> None:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    if not args.value:
        print(f"Model menu for {provider} (current: {pcfg.get('current_model')})")
        models = cached_or_configured_model_ids(provider, pcfg)
        for i, mid in enumerate(models[:100], 1):
            mark = "*" if mid == pcfg.get("current_model") else " "
            print(f" {mark} {i:>3}. {alias_for(provider, mid)}    [{mid}]")
        if len(models) > 100:
            print(f" ... {len(models) - 100} more")
        if read_model_list_cache(provider, pcfg) is None:
            print("\nProvider model list is not cached yet. Use the menu refresh row or run: ciel-runtimectl models")
        print("\nSet direct/custom model with: /set-model MODEL_ID")
        print("Or from terminal: ciel-runtimectl model MODEL_ID")
        return
    value = " ".join(args.value).strip()
    if value.startswith("add "):
        value = value[4:].strip()
    if not value:
        raise SystemExit("Missing model id")
    for line in set_model_config(value):
        print(line)
    print("Gateway model cache cleared. Run /model to refresh if needed.")


def cmd_advisor_model(args: argparse.Namespace) -> None:
    if not args.value:
        cfg = load_config()
        provider, pcfg = get_current_provider(cfg)
        if provider == "anthropic":
            print("Anthropic modes use Claude Code's built-in /advisor; run /advisor in the session to pick its model.")
            return
        current = pcfg.get("advisor_model") or "off"
        print(f"Advisor Model for {provider}: {current}")
        print("Set with: ciel-runtimectl advisor-model deepseek-v4-pro")
        print("Disable with: ciel-runtimectl advisor-model off")
        return
    value = " ".join(args.value).strip()
    if value.lower() in ("off", "unset", "disable", "disabled", "none", "null"):
        value = ""
    for line in set_advisor_model_config(value):
        print(line)


def cmd_models(args: argparse.Namespace) -> None:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    if args.provider:
        provider = normalize_provider(args.provider)
        pcfg = cfg["providers"][provider]
    models = upstream_model_ids(provider, pcfg)
    print(f"{provider}: {len(models)} models")
    for mid in models:
        print(f"{alias_for(provider, mid)}\t{mid}")


def cmd_ollama_catalog(args: argparse.Namespace) -> None:
    include_contexts = not bool(getattr(args, "no_contexts", False))
    catalog = refresh_ollama_model_catalog(include_contexts=include_contexts, timeout=float(getattr(args, "timeout", 10.0)))
    models = catalog.get("models") if isinstance(catalog.get("models"), dict) else {}
    context_count = 0
    for entry in models.values():
        if isinstance(entry, dict) and isinstance(entry.get("context_windows"), dict) and entry["context_windows"]:
            context_count += 1
    print(f"Ollama catalog saved: {OLLAMA_MODEL_CATALOG_PATH}")
    print(f"API models: {catalog.get('model_count', 0)}")
    print(f"Base models: {len(models)}")
    print(f"Context windows: {context_count}/{len(models)}")


def provider_mode_label(provider: str, pcfg: dict[str, Any]) -> str:
    if direct_native_anthropic_enabled(provider, pcfg):
        return "anthropic-native"
    if anthropic_routed_enabled(provider, pcfg):
        return "anthropic-routed"
    if direct_native_agy_enabled(provider, pcfg):
        return "agy-native"
    if agy_routed_enabled(provider, pcfg):
        return "agy-routed"
    if direct_native_codex_enabled(provider, pcfg):
        return "codex-native"
    if codex_routed_enabled(provider, pcfg):
        return "codex-routed"
    return "ciel-runtime-router"


def status_lines() -> list[str]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    mode = provider_mode_label(provider, pcfg)
    direct_native = direct_native_anthropic_enabled(provider, pcfg)
    adapter = configured_provider_adapter(provider, pcfg)
    configuration = adapter.configuration_policy(provider_contract_config(provider, pcfg))
    provider_lines: list[str] = []
    if configuration.uses_ollama_status:
        provider_lines.extend(
            (
                f"num_ctx: {ollama_num_ctx_status(pcfg)}",
                f"ollama_options: {ollama_options_status(pcfg)}",
                f"keep_alive: {pcfg.get('keep_alive', 'default')}",
                f"think: {ollama_think_status(current_upstream_model_id(provider, pcfg), pcfg)}",
                f"request_timeout_ms: {pcfg.get('request_timeout_ms', 'default')}",
                f"stream_idle_timeout_ms: {pcfg.get('stream_idle_timeout_ms', 'auto')}",
            )
        )
    for field_name in configuration.status_fields:
        default = "auto" if field_name == "stream_idle_timeout_ms" else "default"
        provider_lines.append(f"{field_name}: {pcfg.get(field_name, default)}")
    claude_model = (
        "disabled for native runtime provider"
        if configuration.runtime_owns_model
        else current_upstream_model_id(provider, pcfg)
        if direct_native
        else current_alias(cfg)
    )
    return [
        f"provider: {provider}",
        f"language: {cfg.get('language', 'en')}",
        f"mode: {mode}",
        f"base_url: {pcfg.get('base_url')}",
        f"model: {pcfg.get('current_model')}",
        *provider_lines,
        f"claude_model: {claude_model}",
        f"log_level: {log_level_status()}",
        f"channels: {channel_status_text(cfg)}",
        f"channel_delivery: {channel_delivery_mode(cfg)}",
        f"router: {'bypassed for native provider compatibility' if direct_native else (('up' if router_up() else 'down') + ' ' + ROUTER_BASE)}",
        f"config: {CONFIG_PATH}",
    ]


def cmd_status(_: argparse.Namespace) -> None:
    print("\n".join(status_lines()))


def cmd_log_level(args: argparse.Namespace) -> None:
    value = getattr(args, "value", None)
    if not value:
        print(f"log_level: {log_level_status()}")
        for numeric in sorted(LOG_LEVEL_NAMES):
            name = LOG_LEVEL_NAMES[numeric]
            mark = "*" if name == log_level_name() else " "
            print(f" {mark} {name:<6} {numeric}")
        print("   DEFAULT reset to environment/default")
        return
    for line in set_log_level_config(str(value)):
        print(line)


def cmd_language(args: argparse.Namespace) -> None:
    cfg = load_config()
    if not args.value:
        current = cfg.get("language", "en")
        print(f"language: {current} ({LANGUAGES.get(current, current)})")
        for code, label in LANGUAGES.items():
            mark = "*" if code == current else " "
            print(f" {mark} {code:<2} {label}")
        return
    value = args.value.strip().lower()
    aliases = {
        "english": "en",
        "korean": "ko",
        "한국어": "ko",
        "japanese": "ja",
        "日本語": "ja",
        "chinese": "zh",
        "中文": "zh",
        "zh-cn": "zh",
        "cn": "zh",
    }
    value = aliases.get(value, value)
    if value not in LANGUAGES:
        raise SystemExit(f"Unknown language: {args.value}\nKnown: {', '.join(LANGUAGES)}")
    cfg["language"] = value
    save_config(cfg)
    print(f"Language set to {value} ({LANGUAGES[value]}).")


def set_web_search_enabled(enabled: bool) -> None:
    cfg = load_config()
    cfg.setdefault("web_search", {})["auto_for_non_native"] = enabled
    save_config(cfg)


def cmd_web_search(args: argparse.Namespace) -> None:
    cfg = load_config()
    web = cfg.setdefault("web_search", {})
    if args.value:
        value = args.value.lower()
        if value in ("on", "enable", "enabled", "true", "1"):
            web["auto_for_non_native"] = True
            save_config(cfg)
        elif value in ("off", "disable", "disabled", "false", "0"):
            web["auto_for_non_native"] = False
            save_config(cfg)
        else:
            raise SystemExit("Use: ciel-runtime web-search on|off|status")
    state = "on" if web.get("auto_for_non_native", True) else "off"
    package = web.get("package", "ddg-mcp-search")
    print(f"web_search: {state}")
    print(f"search_provider: {web.get('provider', 'duckduckgo')}")
    print(f"search_package: {package}")
    print(f"web_fetch: {'on' if web.get('fetch_enabled', True) else 'off'}")
    print(f"fetch_package: {web.get('fetch_package', 'mcp-server-fetch')}")
    print(f"mcp_config: {WEB_TOOLS_MCP_CONFIG}")


def cmd_web_fetch(args: argparse.Namespace) -> None:
    cfg = load_config()
    web = cfg.setdefault("web_search", {})
    if args.value:
        value = args.value.lower()
        if value in ("on", "enable", "enabled", "true", "1"):
            web["fetch_enabled"] = True
            save_config(cfg)
        elif value in ("off", "disable", "disabled", "false", "0"):
            web["fetch_enabled"] = False
            save_config(cfg)
        elif value == "ignore-robots-on":
            web["fetch_ignore_robots_txt"] = True
            save_config(cfg)
        elif value == "ignore-robots-off":
            web["fetch_ignore_robots_txt"] = False
            save_config(cfg)
        else:
            raise SystemExit("Use: ciel-runtime web-fetch on|off|ignore-robots-on|ignore-robots-off")
    print(f"web_fetch: {'on' if web.get('fetch_enabled', True) else 'off'}")
    print(f"fetch_package: {web.get('fetch_package', 'mcp-server-fetch')}")
    print(f"ignore_robots_txt: {bool(web.get('fetch_ignore_robots_txt', False))}")
    print(f"user_agent: {web.get('fetch_user_agent') or 'default'}")
    print(f"mcp_config: {WEB_TOOLS_MCP_CONFIG}")


def channel_specs(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    raw = cfg.setdefault("claude_code", {}).get("channels", [])
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    channels: list[str] = [BUILTIN_CHANNEL_SPEC]
    seen: set[str] = {BUILTIN_CHANNEL_SPEC}
    for item in items:
        spec = str(item).strip()
        if not spec or spec in seen:
            continue
        seen.add(spec)
        channels.append(spec)
    return channels


def _read_mcp_server_names_from_json(path: Path, cwd: Path) -> list[str]:
    return read_mcp_config_items(
        path,
        cwd,
        _mcp_server_names_from_mapping,
        str,
        router_log,
    )


def _read_mcp_servers_from_json(path: Path, cwd: Path) -> list[tuple[str, dict[str, Any]]]:
    return read_mcp_config_items(
        path,
        cwd,
        _mcp_servers_from_mapping,
        lambda item: item[0],
        router_log,
    )


def _mcp_server_is_stdio(server: dict[str, Any]) -> bool:
    if not isinstance(server, dict):
        return False
    server_type = str(server.get("type") or "").strip().lower()
    if server_type and server_type not in ("stdio", "command"):
        return False
    command = resolve_executable_for_subprocess(str(server.get("command") or "").strip())
    if not command:
        return False
    args = [str(item) for item in server.get("args", []) if item is not None] if isinstance(server.get("args", []), list) else []
    return "mcp-proxy" not in args


def _mcp_server_is_streamable_http(server: dict[str, Any]) -> bool:
    if not isinstance(server, dict):
        return False
    server_type = str(server.get("type") or server.get("transport") or "").strip().lower()
    if server_type not in {"http", "streamable-http"}:
        return False
    url = str(server.get("url") or server.get("endpoint") or "").strip()
    return url.startswith(("http://", "https://"))


def _mcp_server_force_proxy(server: dict[str, Any]) -> bool:
    if not isinstance(server, dict):
        return False
    return parse_bool(
        server.get(
            "ciel_runtime_mcp_proxy",
            server.get("ciel_runtime_force_mcp_proxy", server.get("force_mcp_proxy", False)),
        ),
        False,
    )


def _mcp_server_disable_proxy_notification_stream(server: dict[str, Any]) -> bool:
    if not isinstance(server, dict):
        return False
    return parse_bool(
        server.get(
            "ciel_runtime_disable_notification_stream",
            server.get("ciel_runtime_disable_mcp_notifications", False),
        ),
        False,
    )


def _channel_probe_initialize_payload() -> bytes:
    return _mcp_probe_initialize_payload_bytes(VERSION)


CHANNEL_PROBE_DEFAULT_TIMEOUT_SECONDS = 15.0


def channel_probe_default_timeout() -> float:
    """Default per-server probe timeout. Configurable via
    CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS so users with slow MCP servers
    (npx cold start, remote API init) can extend it without code changes."""
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_PROBE_TIMEOUT_SECONDS")
    if raw is None:
        return CHANNEL_PROBE_DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return CHANNEL_PROBE_DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return CHANNEL_PROBE_DEFAULT_TIMEOUT_SECONDS
    return value


CHANNEL_PROBE_STDERR_CAP_BYTES = 4096
CHANNEL_PROBE_STDOUT_PREVIEW_BYTES = 200
CHANNEL_PROBE_STDERR_PREVIEW_CHARS = 500
CHANNEL_PROBE_SSE_OPEN_TIMEOUT_SECONDS = 5.0
CHANNEL_PROBE_SSE_INIT_POST_TIMEOUT_SECONDS = 5.0


def mcp_probe_services() -> McpProbeServices:
    return McpProbeServices(
        codec=McpProbeCodec(
            initialize_bytes=_channel_probe_initialize_payload,
            initialize_dict=_channel_probe_initialize_payload_dict,
            decode_sse_events=_decode_sse_events,
            capability_present=_channel_probe_capability_present,
            decode_preview=_decode_preview,
        ),
        http=McpProbeHttp(
            runtime_headers=mcp_server_runtime_headers,
            urlopen=urllib.request.urlopen,
            streamable_post_json=_mcp_streamable_post_json,
            delete_streamable_session=_channel_streamable_http_delete_session,
        ),
        policy=McpProbePolicy(
            default_timeout=channel_probe_default_timeout,
            stderr_preview_chars=CHANNEL_PROBE_STDERR_PREVIEW_CHARS,
            stdout_preview_bytes=CHANNEL_PROBE_STDOUT_PREVIEW_BYTES,
            sse_open_timeout_seconds=CHANNEL_PROBE_SSE_OPEN_TIMEOUT_SECONDS,
            sse_init_post_timeout_seconds=CHANNEL_PROBE_SSE_INIT_POST_TIMEOUT_SECONDS,
            streamable_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
        ),
        log=router_log,
    )


def probe_sse_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, Any]:
    return run_sse_mcp_probe(
        server_name,
        server,
        timeout,
        services=mcp_probe_services(),
    )


def _channel_probe_initialize_payload_dict(protocol_version: str) -> dict[str, Any]:
    return _mcp_probe_initialize_payload(VERSION, protocol_version)


def probe_streamable_http_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, Any]:
    return run_streamable_http_mcp_probe(
        server_name,
        server,
        timeout,
        services=mcp_probe_services(),
    )


def _decode_preview(buf: bytes | bytearray, limit_chars: int) -> str:
    text = bytes(buf).decode("utf-8", errors="replace")
    text = text.replace("\x00", " ")
    text = " ".join(text.split())
    if len(text) > limit_chars:
        text = text[:limit_chars] + "..."
    return text


def stdio_mcp_probe_services() -> StdioProbeServices:
    return StdioProbeServices(
        codec=StdioProbeCodec(
            initialize_payload=_channel_probe_initialize_payload,
            strategy_for=_channel_probe_strategy_for,
            find_initialize_response=_channel_probe_find_initialize_response,
            capability_present=_channel_probe_capability_present,
            decode_preview=_decode_preview,
        ),
        process=StdioProbeProcess(
            is_stdio=_mcp_server_is_stdio,
            resolve_server_process=resolve_mcp_server_process,
            popen=subprocess.Popen,
        ),
        policy=StdioProbePolicy(
            default_timeout=channel_probe_default_timeout,
            stderr_cap_bytes=CHANNEL_PROBE_STDERR_CAP_BYTES,
            stderr_preview_chars=CHANNEL_PROBE_STDERR_PREVIEW_CHARS,
            stdout_preview_bytes=CHANNEL_PROBE_STDOUT_PREVIEW_BYTES,
        ),
        log=router_log,
    )


def probe_stdio_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, Any]:
    return run_stdio_mcp_probe(
        server_name,
        server,
        timeout,
        services=stdio_mcp_probe_services(),
    )


def probe_stdio_mcp_for_channel_capability(server_name: str, server: dict[str, Any], timeout: float | None = None) -> bool:
    """Thin bool wrapper around the detailed probe. Preserves the older API
    for `detect_channel_capable_mcp_servers` and any external callers."""
    return probe_stdio_mcp_for_channel_capability_detailed(server_name, server, timeout=timeout)["capable"]


def detect_channel_capable_mcp_servers(
    mcp_config_paths: Iterable[str],
    cwd: Path,
    *,
    include_router_self: bool = True,
    timeout_per_server: float = 3.0,
) -> list[str]:
    """Probe MCP servers declared in given config files; return names that declare experimental['claude/channel']."""
    records = _probe_mcp_servers_to_records(
        mcp_config_paths,
        cwd,
        include_router_self=include_router_self,
        timeout_per_server=timeout_per_server,
    )
    return [str(record.get("name")) for record in records if record.get("capable") and record.get("name")]


def _mcp_config_passthrough_values(passthrough: list[str]) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(passthrough):
        arg = passthrough[i]
        if arg == "--mcp-config":
            i += 1
            while i < len(passthrough) and not passthrough[i].startswith("-"):
                values.append(passthrough[i])
                i += 1
            continue
        if arg.startswith("--mcp-config="):
            value = arg.split("=", 1)[1].strip()
            if value:
                values.append(value)
        i += 1
    return values


def strip_mcp_config_passthrough(passthrough: list[str]) -> list[str]:
    stripped: list[str] = []
    i = 0
    while i < len(passthrough):
        arg = passthrough[i]
        if arg == "--mcp-config":
            i += 1
            while i < len(passthrough) and not passthrough[i].startswith("-"):
                i += 1
            continue
        if arg.startswith("--mcp-config="):
            i += 1
            continue
        stripped.append(arg)
        i += 1
    return stripped


def _safe_mcp_proxy_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe[:80] or "server"


def _mcp_config_paths_from_passthrough(passthrough: list[str]) -> list[Path]:
    return [Path(value).expanduser() for value in _mcp_config_passthrough_values(passthrough)]


def claude_mcp_config_paths(passthrough: list[str] | None = None, cwd: Path | None = None, home: Path | None = None) -> list[Path]:
    cwd = cwd or Path.cwd()
    home = home or HOME
    paths: list[Path] = []
    paths.extend(_mcp_config_paths_from_passthrough(passthrough or []))
    current = cwd
    visited: set[str] = set()
    while True:
        key = _path_for_compare(current)
        if key in visited:
            break
        visited.add(key)
        paths.append(current / ".mcp.json")
        if current == current.parent:
            break
        current = current.parent
    paths.extend([
        home / ".mcp.json",
        home / ".claude" / "settings.json",
        home / ".claude.json",
    ])
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _path_for_compare(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def existing_claude_mcp_config_paths(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Return existing Claude MCP config files that should be passed to Claude.

    This is intentionally transport-agnostic. Channel-capable MCP servers are
    discovered separately by the channel probe cache; this helper is only for
    preserving Claude Code's normal MCP tool surface when ciel-runtime launches it.
    """
    return [
        path
        for path in claude_mcp_config_paths(passthrough, cwd, home)
        if path.exists() and path.is_file()
    ]


def discovered_claude_mcp_servers(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> dict[str, dict[str, Any]]:
    cwd = cwd or Path.cwd()
    servers: dict[str, dict[str, Any]] = {}
    for path in existing_claude_mcp_config_paths(passthrough, cwd, home):
        for name, server in _read_mcp_servers_from_json(path, cwd):
            servers.setdefault(name, server)
    return servers


def _read_mcp_servers_from_generated_file(path: Path, cwd: Path) -> dict[str, dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return {}
    servers: dict[str, dict[str, Any]] = {}
    for name, server in _read_mcp_servers_from_json(path, cwd):
        if name.strip().lower() in _NATIVE_ROUTER_CHANNEL_NAMES:
            continue
        servers.setdefault(name, server)
    return servers


def discovered_ciel_runtime_managed_mcp_servers(cwd: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return MCP servers that only exist in ciel-runtime generated config.

    Direct Claude Native launches should restore the user's MCP tool surface when
    switching back from a routed/non-native session.  The generated channel MCP
    bridge itself is intentionally skipped, but ordinary generated tools and
    original servers wrapped by mcp-proxy are safe to pass back to Claude Code.
    """
    cwd = cwd or Path.cwd()
    servers: dict[str, dict[str, Any]] = {}
    servers.update(_read_mcp_servers_from_generated_file(WEB_TOOLS_MCP_CONFIG, cwd))

    if MCP_PROXY_CONFIG.exists() and MCP_PROXY_CONFIG.is_file():
        try:
            proxy_data = json.loads(MCP_PROXY_CONFIG.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
            router_log(
                "WARN",
                f"managed_mcp_proxy_config_read_failed path={MCP_PROXY_CONFIG} "
                f"error={type(exc).__name__}: {exc}",
            )
            proxy_data = {}
        proxy_servers = proxy_data.get("mcpServers") if isinstance(proxy_data, dict) else None
        if isinstance(proxy_servers, dict):
            for raw_name, raw_entry in proxy_servers.items():
                name = str(raw_name or "").strip()
                if not name or name.strip().lower() in _NATIVE_ROUTER_CHANNEL_NAMES or not isinstance(raw_entry, dict):
                    continue
                args = raw_entry.get("args")
                if isinstance(args, list):
                    args_s = [str(item) for item in args]
                    if "mcp-proxy" in args_s and "--server-config" in args_s:
                        try:
                            cfg_path = Path(args_s[args_s.index("--server-config") + 1]).expanduser()
                            wrapped_name = (
                                args_s[args_s.index("--server-name") + 1].strip()
                                if "--server-name" in args_s and args_s.index("--server-name") + 1 < len(args_s)
                                else name
                            )
                            wrapped_server = json.loads(cfg_path.read_text(encoding="utf-8"))
                        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                            router_log(
                                "WARN",
                                f"managed_mcp_wrapped_config_read_failed server={name} "
                                f"error={type(exc).__name__}: {exc}",
                            )
                            continue
                        if wrapped_name and isinstance(wrapped_server, dict):
                            restored = dict(wrapped_server)
                            restored.pop("ciel_runtime_disable_notification_stream", None)
                            if wrapped_name.strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES:
                                servers.setdefault(wrapped_name, restored)
                        continue
                servers.setdefault(name, dict(raw_entry))
    return servers


def write_native_mcp_config_from_discovery(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    """Write a Claude Code --mcp-config compatible file for native launches.

    Discovery may read files that are not directly valid --mcp-config inputs
    (notably ~/.claude/settings.json and project-scoped ~/.claude.json).  Claude
    Code expects a top-level mcpServers record, so native launches receive this
    normalized generated file instead of the source files.
    """
    cwd = cwd or Path.cwd()
    servers = discovered_claude_mcp_servers(passthrough, cwd, home)
    for name, server in discovered_ciel_runtime_managed_mcp_servers(cwd).items():
        if name in servers:
            router_log("INFO", f"native_mcp_managed_duplicate_skipped server={name}")
            continue
        servers[name] = server
    if not servers:
        return None
    json_artifact_repository(NATIVE_MCP_CONFIG).save(
        {"mcpServers": servers},
        "native_mcp_config",
    )
    router_log("INFO", f"native_mcp_config_written servers={','.join(sorted(servers))}")
    return NATIVE_MCP_CONFIG


def auto_discovered_mcp_channel_specs(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> list[str]:
    cwd = cwd or Path.cwd()
    specs: list[str] = []
    for path in claude_mcp_config_paths(passthrough, cwd, home):
        if not path.exists() or not path.is_file():
            continue
        for name in _read_mcp_server_names_from_json(path, cwd):
            if re.search(r"\s", name):
                continue
            specs.append(f"server:{name}" if not is_channel_spec_tagged(name) else name)
    return _dedupe_strings(specs)


CHANNEL_PROBE_CACHE_VERSION = 1


def channel_probe_service() -> ChannelProbeService:
    artifact = json_artifact_repository(CHANNEL_PROBE_CACHE_PATH)
    return ChannelProbeService(
        ROUTER_BASE,
        ChannelProbeCacheRepository(
            CHANNEL_PROBE_CACHE_PATH,
            CHANNEL_PROBE_CACHE_VERSION,
            artifact.save,
            router_log,
        ),
        ChannelProbePorts(
            read_servers=_read_mcp_servers_from_json,
            is_stdio=_mcp_server_is_stdio,
            probe_stdio=probe_stdio_mcp_for_channel_capability_detailed,
            probe_sse=probe_sse_mcp_for_channel_capability_detailed,
            probe_http=probe_streamable_http_mcp_for_channel_capability_detailed,
            log=router_log,
        ),
        claude_mcp_config_paths,
        _dedupe_strings,
        _path_for_compare,
        frozenset(_NATIVE_ROUTER_CHANNEL_NAMES),
    )


def _builtin_router_probe_record() -> dict[str, Any]:
    return channel_probe_service().builtin_record()


def _server_transport_label(server: dict[str, Any]) -> str:
    return channel_probe_service().transport_label(server)


def _probe_mcp_servers_to_records(
    paths: Iterable[str],
    cwd: Path,
    *,
    include_router_self: bool = True,
    timeout_per_server: float | None = None,
) -> list[dict[str, Any]]:
    return channel_probe_service().probe(
        paths,
        cwd,
        include_router_self=include_router_self,
        timeout_per_server=timeout_per_server,
    )


def read_channel_probe_cache() -> dict[str, Any]:
    return channel_probe_service().repository.read()


def _write_channel_probe_cache(cache: dict[str, Any]) -> None:
    channel_probe_service().repository.write(cache)


def refresh_channel_probe_cache(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    timeout_per_server: float | None = None,
    extra_config_paths: list[Path | str] | None = None,
) -> dict[str, Any]:
    return channel_probe_service().refresh(
        passthrough,
        cwd,
        home,
        timeout_per_server,
        extra_config_paths,
    )


def cached_channel_probe_servers() -> list[dict[str, Any]]:
    return channel_probe_service().servers()


def channel_probe_record_bucket(record: dict[str, Any]) -> str:
    return channel_probe_service().bucket(record)


def cached_channel_capable_server_names() -> list[str]:
    return channel_probe_service().capable_names()


def cached_external_channel_capable_server_names() -> list[str]:
    return channel_probe_service().external_capable_names()


def native_auto_channel_capable_server_names(passthrough: list[str] | None = None) -> list[str]:
    """External channel-capable servers that are also in current MCP discovery."""
    discovered = set(discovered_claude_mcp_servers(passthrough or []).keys())
    if not discovered:
        return []
    return [name for name in cached_external_channel_capable_server_names() if name in discovered]


def cached_channel_source_paths_for_specs(specs: Iterable[str]) -> list[Path]:
    return channel_probe_service().source_paths(specs)


def _server_names_from_channel_specs(specs: Iterable[str]) -> list[str]:
    return channel_probe_service().server_names_from_specs(specs)


def channel_candidate_server_names_for_launch(
    cfg: dict[str, Any],
    passthrough: list[str],
    extra_config_paths: list[Path | str] | None = None,
) -> list[str]:
    return channel_probe_service().candidate_names(
        channel_specs_for_launch(cfg, passthrough),
        lambda: external_mcp_channel_server_names_from_configs(
            passthrough,
            extra_config_paths=extra_config_paths,
        ),
    )


def channel_probe_cache_needs_launch_refresh(
    cfg: dict[str, Any],
    passthrough: list[str],
    extra_config_paths: list[Path | str] | None = None,
) -> bool:
    cache = read_channel_probe_cache()
    records = cached_channel_probe_servers()
    candidate_names = channel_candidate_server_names_for_launch(cfg, passthrough, extra_config_paths=extra_config_paths)
    return channel_probe_service().needs_refresh(cache, records, candidate_names)


def ensure_channel_probe_cache_for_launch(
    cfg: dict[str, Any],
    passthrough: list[str],
    extra_config_paths: list[Path | str] | None = None,
) -> bool:
    needed = channel_probe_cache_needs_launch_refresh(
        cfg,
        passthrough,
        extra_config_paths=extra_config_paths,
    )
    return channel_probe_service().ensure_refresh(
        needed,
        lambda: refresh_channel_probe_cache(
            passthrough,
            **(
                {"extra_config_paths": extra_config_paths}
                if extra_config_paths is not None
                else {}
            ),
        ),
    )


def start_codex_mcp_channel_sse_for_launch(
    cfg: dict[str, Any],
    codex_mcp_config: Path | None,
    allowed_server_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    if not codex_mcp_config or channel_delivery_mode(cfg) != "llm":
        return []
    if not codex_mcp_config.exists() or not codex_mcp_config.is_file():
        return []
    extra_paths: list[Path | str] = [codex_mcp_config]
    explicit = {
        name
        for name in _server_names_from_channel_specs(channel_specs_for_launch(cfg, []))
        if name.strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES
    }
    if allowed_server_names is None:
        names = codex_channel_capable_mcp_server_names(cfg, codex_mcp_config)
    else:
        names = _dedupe_strings(
            name
            for name in allowed_server_names
            if str(name or "").strip()
            and str(name or "").strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES
        )
    names = [name for name in names if name not in explicit]
    if not names:
        router_log(
            "INFO",
            "codex_channel_sse_skipped reason=no_capable_unowned_codex_mcp allowed=%s explicit=%s"
            % (",".join(names) or "-", ",".join(sorted(explicit)) or "-"),
        )
        return []
    started = auto_start_sse_channels_from_mcp_configs(
        [],
        extra_config_paths=extra_paths,
        allowed_server_names=names,
        include_default_paths=False,
    )
    router_log(
        "INFO",
        "codex_channel_sse_started count=%d servers=%s"
        % (len(started), ",".join(str(item.get("name") or "") for item in started) or "-"),
    )
    return started


def channel_probe_summary_message(prefix: str, cache: dict[str, Any]) -> str:
    records = [r for r in cache.get("servers") or [] if isinstance(r, dict)]
    capable = [r for r in records if channel_probe_record_bucket(r) == "capable"]
    inconclusive = [r for r in records if channel_probe_record_bucket(r) == "inconclusive"]
    non_capable = [r for r in records if channel_probe_record_bucket(r) == "non_capable"]
    return (
        f"{prefix}: {len(capable)} channel-capable, "
        f"{len(inconclusive)} inconclusive, {len(non_capable)} non-capable server(s)."
    )


def channel_panel_rows_for_menu(cfg: dict[str, Any], passthrough: list[str]) -> tuple[list[str], list[str], list[str]]:
    messages: list[str] = []
    if channel_probe_cache_needs_launch_refresh(cfg, passthrough):
        try:
            router_log("INFO", "channel_probe_menu_refresh reason=missing_cache_or_selected_server")
            result = refresh_channel_probe_cache(passthrough)
            messages = [channel_probe_summary_message("Probe complete", result)]
        except Exception as exc:
            router_log("WARN", f"channel_probe_menu_refresh_failed error={type(exc).__name__}: {exc}")
            messages = [f"Channel probe failed: {type(exc).__name__}: {exc}"]
    rows, values = channel_panel_rows(cfg)
    return rows, values, messages


def channel_config_service() -> ChannelConfigService:
    return ChannelConfigService(
        BUILTIN_CHANNEL_SPEC,
        ChannelConfigPorts(
            load=load_config,
            save=save_config,
            invalidate=invalidate_config_cache,
            configured_specs=channel_specs,
            dedupe=_dedupe_strings,
            log=router_log,
            environment=os.environ,
        ),
    )


def parse_passthrough_channel_specs(passthrough: list[str]) -> list[str]:
    return channel_config_service().parse_passthrough(passthrough)


def auto_import_passthrough_channels(passthrough: list[str]) -> list[str]:
    return channel_config_service().auto_import(passthrough)


def channel_mcp_discovery_service() -> ChannelMcpDiscoveryService:
    return ChannelMcpDiscoveryService(
        ChannelMcpDiscoveryPorts(
            environment=os.environ,
            config_paths=claude_mcp_config_paths,
            path_key=_path_for_compare,
            read_config=_read_mcp_sse_servers_from_json,
            dedupe=_dedupe_strings,
            native_router_names=frozenset(_NATIVE_ROUTER_CHANNEL_NAMES),
            public_name=_channel_sse_public_mcp_name,
            start_connection=start_channel_sse_connection,
            log=router_log,
        )
    )


def mcp_server_runtime_headers(server: dict[str, Any]) -> dict[str, str]:
    return channel_mcp_discovery_service().runtime_headers(server)


def _mcp_sse_servers_from_mapping(mapping: Any) -> list[dict[str, Any]]:
    return channel_mcp_discovery_service().servers_from_mapping(mapping)


def _read_mcp_sse_servers_from_json(path: Path, cwd: Path) -> list[dict[str, Any]]:
    return read_mcp_config_items(
        path,
        cwd,
        _mcp_sse_servers_from_mapping,
        lambda server: f"{server.get('name')}|{server.get('url')}",
        router_log,
    )


def external_mcp_channel_server_names_from_configs(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_config_paths: list[Path | str] | None = None,
) -> list[str]:
    return channel_mcp_discovery_service().external_names(
        passthrough,
        cwd,
        home,
        extra_config_paths,
    )


def auto_start_sse_channels_from_mcp_configs(
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_config_paths: list[Path | str] | None = None,
    allowed_server_names: Iterable[str] | None = None,
    include_default_paths: bool = True,
) -> list[dict[str, Any]]:
    return channel_mcp_discovery_service().auto_start(
        passthrough,
        cwd,
        home,
        extra_config_paths,
        allowed_server_names,
        include_default_paths,
    )


def channel_proxy_ownership_repository() -> ChannelProxyOwnershipRepository:
    return ChannelProxyOwnershipRepository(
        MCP_PROXY_CONFIG,
        _mcp_server_disable_proxy_notification_stream,
        router_log,
    )


def proxy_owned_channel_server_names() -> set[str]:
    return channel_proxy_ownership_repository().owned_names()


def _proxy_server_config_disables_notifications(args_s: list[str]) -> bool:
    return channel_proxy_ownership_repository().server_config_disables_notifications(
        args_s
    )


def channel_router_lifecycle() -> ChannelRouterLifecycle:
    return ChannelRouterLifecycle(
        frozenset(_NATIVE_ROUTER_CHANNEL_NAMES),
        ChannelRouterLifecyclePorts(
            delivery_enabled=should_use_channel_llm_delivery,
            launch_specs=channel_specs_for_launch,
            server_names=_server_names_from_channel_specs,
            owned_names=proxy_owned_channel_server_names,
            public_name=_channel_sse_public_mcp_name,
            ensure_probe=ensure_channel_probe_cache_for_launch,
            source_paths=cached_channel_source_paths_for_specs,
            auto_start=auto_start_sse_channels_from_mcp_configs,
            log=router_log,
        ),
    )


def router_managed_channel_server_names(cfg: dict[str, Any]) -> list[str]:
    return channel_router_lifecycle().managed_names(cfg)


def start_router_managed_channel_sse(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return channel_router_lifecycle().start(cfg)


def channel_specs_for_launch(cfg: dict[str, Any], passthrough: list[str], extra_specs: list[str] | None = None) -> list[str]:
    return channel_config_service().launch_specs(cfg, extra_specs)


def is_channel_spec_tagged(spec: str) -> bool:
    return channel_config_service().is_tagged(spec)


def channel_status_text(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    channels = channel_specs(cfg)
    if not channels:
        return "off"
    return f"{len(channels)} channel{'s' if len(channels) != 1 else ''}"


def set_channel_development_enabled(enabled: bool) -> list[str]:
    return ["Channel wake delivery is always enabled by Ciel Runtime."]


def normalize_channel_delivery(value: Any) -> str:
    return channel_config_service().normalize_delivery(value)


def channel_delivery_mode(cfg: dict[str, Any] | None = None) -> str:
    return channel_config_service().delivery_mode(cfg)


def set_channel_delivery_config(value: Any) -> list[str]:
    return channel_config_service().set_delivery(value)


def add_channel_spec(spec: str, *, development: bool = False) -> list[str]:
    return channel_config_service().add(spec)


def remove_channel_spec(spec: str) -> list[str]:
    return channel_config_service().remove(spec)


def clear_channel_specs() -> list[str]:
    return channel_config_service().clear()


def cmd_channels(args: argparse.Namespace) -> None:
    cfg = load_config()
    values = list(getattr(args, "values", []) or [])
    if not values:
        print(f"channels: {channel_status_text(cfg)}")
        print(f"delivery: {channel_delivery_mode(cfg)}")
        for name, spec in OFFICIAL_CHANNEL_PLUGINS.items():
            mark = "*" if spec in channel_specs(cfg) else " "
            print(f" {mark} {name:<10} {spec}")
        for spec in channel_specs(cfg):
            if spec not in OFFICIAL_CHANNEL_PLUGINS.values():
                print(f" * custom    {spec}")
        return
    head = values[0].strip().lower()
    if head in ("on", "enable", "add"):
        if len(values) < 2:
            raise SystemExit("Usage: ciel-runtime channels add CHANNEL_SPEC")
        for line in add_channel_spec(values[1]):
            print(line)
        return
    if head in ("dev", "development"):
        if len(values) >= 2 and values[1].lower() in ("on", "off", "true", "false", "1", "0"):
            for line in set_channel_development_enabled(True):
                print(line)
            return
        if len(values) < 2:
            raise SystemExit("Usage: ciel-runtime channels add CHANNEL_SPEC")
        for line in add_channel_spec(values[1]):
            print(line)
        return
    if head in ("off", "disable", "remove", "rm"):
        if len(values) < 2:
            raise SystemExit("Usage: ciel-runtime channels remove CHANNEL_SPEC")
        for line in remove_channel_spec(values[1]):
            print(line)
        return
    if head in ("clear", "reset"):
        for line in clear_channel_specs():
            print(line)
        return
    if head in ("detect", "probe", "refresh"):
        try:
            result = refresh_channel_probe_cache()
        except Exception as exc:
            raise SystemExit(f"Channel probe failed: {type(exc).__name__}: {exc}")
        lines = channel_probe_report_lines(
            result,
            channel_probe_default_timeout(),
            ChannelProbeReportServices(
                bucket=channel_probe_record_bucket,
                format_timestamp=lambda value: time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(value)
                ),
            ),
        )
        for line in lines:
            print(line)
        return
    if head in ("delivery", "mode"):
        if len(values) < 2:
            print(f"channel_delivery: {channel_delivery_mode(cfg)}")
            return
        for line in set_channel_delivery_config(values[1]):
            print(line)
        return
    if head in OFFICIAL_CHANNEL_PLUGINS:
        spec = OFFICIAL_CHANNEL_PLUGINS[head]
        if spec in channel_specs(cfg):
            for line in remove_channel_spec(spec):
                print(line)
        else:
            for line in add_channel_spec(spec):
                print(line)
        return
    for line in add_channel_spec(values[0]):
        print(line)


def cmd_channel_delivery(args: argparse.Namespace) -> None:
    value = getattr(args, "value", None)
    if value:
        for line in set_channel_delivery_config(value):
            print(line)
    else:
        print(f"channel_delivery: {channel_delivery_mode()}")


def cmd_ollama_native(args: argparse.Namespace) -> None:
    cfg = load_config()
    pcfg = cfg["providers"]["ollama"]
    if args.value:
        value = args.value.lower()
        if value in ("on", "enable", "enabled", "true", "1"):
            pcfg["native_compat"] = True
            save_config(cfg)
        elif value in ("off", "disable", "disabled", "false", "0"):
            pcfg["native_compat"] = False
            save_config(cfg)
        else:
            raise SystemExit("Use: ciel-runtime ollama-native on|off|status")
    state = "on" if pcfg.get("native_compat", True) else "off"
    print(f"ollama_native_compat: {state}")
    print(f"base_url: {pcfg.get('base_url')}")
    print(f"model: {pcfg.get('current_model')}")
    print("launch_env: ANTHROPIC_BASE_URL=<ollama>, ANTHROPIC_AUTH_TOKEN=ollama, ANTHROPIC_API_KEY=\"\"")


def provider_option_policy() -> ProviderOptionPolicy:
    return ProviderOptionPolicy(
        normalize_claude_code_supported_capabilities=normalize_claude_code_supported_capabilities,
        normalize_ip_family=normalize_ip_family,
        normalize_model_id=normalize_model_id,
        normalize_opencode_endpoint_kind=normalize_opencode_endpoint_kind,
        parse_bool=parse_bool,
        parse_config_value=parse_config_value,
        positive_int=positive_int,
        sampling_option_key=sampling_option_key,
        validate_sampling_option=validate_sampling_option,
    )


def apply_ollama_option(pcfg: dict[str, Any], token: str) -> None:
    mutate_ollama_option(pcfg, token, policy=provider_option_policy())


def cmd_ollama_options(args: argparse.Namespace) -> None:
    cfg = load_config()
    values = list(getattr(args, "values", []) or [])
    provider = cfg.get("current_provider", "ollama")
    if provider not in ("ollama", "ollama-cloud"):
        provider = "ollama"
    if values:
        try:
            maybe_provider = normalize_provider(values[0])
            if maybe_provider in ("ollama", "ollama-cloud"):
                provider = maybe_provider
                values = values[1:]
        except SystemExit:
            pass
    pcfg = cfg["providers"][provider]
    if values:
        context_changed = any(
            token.split("=", 1)[0].replace("unset:", "").strip() in ("num_ctx", "ctx", "num_ctx_min", "ctx_min", "min", "num_ctx_max", "ctx_max", "max")
            for token in values
        )
        explicit_timeout = any(
            token.split("=", 1)[0].replace("unset:", "").strip() in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms", "stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms")
            for token in values
        )
        for token in values:
            apply_ollama_option(pcfg, token)
        timeout_lines = apply_recommended_timeout_for_model_context(provider, pcfg) if context_changed and not explicit_timeout else []
        save_config(cfg)
        clear_model_cache()
        print(f"Ollama options updated for {provider}.")
        for line in timeout_lines:
            print(line)
    print(f"provider: {provider}")
    print(f"num_ctx: {ollama_num_ctx_status(pcfg)}")
    print(f"keep_alive: {pcfg.get('keep_alive', 'default')}")
    print(f"think: {bool(pcfg.get('think', False))}")
    print(f"request_timeout_ms: {pcfg.get('request_timeout_ms', 'default')}")
    used, limit = router_rate_limit_usage(provider, pcfg)
    if limit is not None:
        print(f"rate_limit_rpm: {limit}")
        if bool(pcfg.get("rate_limit_status", False)):
            suffix = f"{used}/{limit}" if limit > 0 else f"{used}/min (unmanaged)"
            print(f"rpm_used: {suffix}")
    print(f"ollama_options: {ollama_options_status(pcfg)}")
    print("Examples:")
    print("  ciel-runtimectl ollama-options num_ctx=auto min=32768 max=131072")
    print("  ciel-runtimectl ollama-options num_ctx=65536 temperature=0.7 top_p=0.8 max_tokens=32768 timeout=300000")
    print("  ciel-runtime --ca-ollama-option temperature=0.7 --ca-ollama-num-ctx 65536")


PROVIDER_OPTION_PROVIDERS = ("anthropic", "agy", "codex", "vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "ollama", "ollama-cloud", "deepseek", "opencode", "opencode-go", "kimi", "openrouter", "fireworks", "zai")
PROVIDER_SAMPLING_OPTION_PROVIDERS = ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter")
PROVIDER_SAMPLING_OPTIONS = ("temperature", "top_p", "top_k")


def sampling_option_key(key: str) -> str | None:
    normalized = key.strip().lower().replace("-", "_")
    aliases = {
        "temp": "temperature",
        "temperature": "temperature",
        "top": "top_p",
        "top_p": "top_p",
        "topp": "top_p",
        "topk": "top_k",
        "top_k": "top_k",
    }
    return aliases.get(normalized)


def validate_sampling_option(key: str, value: Any) -> float | int:
    if key == "temperature":
        fixed = finite_float(value)
        if fixed is None or fixed < 0 or fixed > 2:
            raise SystemExit("temperature must be a number from 0 to 2")
        return fixed
    if key == "top_p":
        fixed = finite_float(value)
        if fixed is None or fixed <= 0 or fixed > 1:
            raise SystemExit("top_p must be a number greater than 0 and up to 1")
        return fixed
    if key == "top_k":
        fixed = positive_int(value)
        if not fixed:
            raise SystemExit("top_k must be a positive integer")
        return fixed
    raise SystemExit(f"Unknown provider option: {key}")


def provider_sampling_status(pcfg: dict[str, Any]) -> list[str]:
    return [f"{key}={pcfg.get(key, 'default')}" for key in PROVIDER_SAMPLING_OPTIONS]


def provider_options_status(provider: str, pcfg: dict[str, Any]) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    config = provider_contract_config(provider, pcfg)
    presentation = adapter.option_presentation_policy(config)
    context_strategy = adapter.context_policy(config).settings_strategy
    timeout = pcfg.get("request_timeout_ms", "default")
    timeout_text = f"{timeout}ms" if timeout != "default" else "default"
    parts = [
        f"max_output_tokens={pcfg.get('max_output_tokens', 'default')}",
        f"timeout={timeout_text}",
    ]
    if pcfg.get("stream_idle_timeout_ms") is not None:
        parts.append(f"stream_idle_timeout={pcfg.get('stream_idle_timeout_ms')}ms")
    if presentation.show_rate_limit:
        parts.append(f"rate_limit_rpm={pcfg.get('rate_limit_rpm', 0)}")
        if bool(pcfg.get("rate_limit_status", False)):
            used, limit = router_rate_limit_usage(provider, pcfg)
            if limit is not None:
                suffix = f"{used}/{limit}" if limit > 0 else f"{used}/min(unmanaged)"
                parts.append(f"rpm_used={suffix}")
    if context_strategy == "ollama":
        parts.insert(0, f"num_ctx={ollama_num_ctx_status(pcfg)}")
        parts.append(f"ollama_options={ollama_options_status(pcfg)}")
    if context_strategy == "standard":
        parts.insert(0, f"context_window={pcfg.get('context_window', 'default')}")
        parts.insert(1, f"reserve={pcfg.get('context_reserve_tokens', 'default')}")
    if presentation.show_native:
        parts.append(f"native={bool(pcfg.get('native_compat', True))}")
    if presentation.show_ip_family:
        overrides = pcfg.get("model_endpoints")
        count = len(overrides) if isinstance(overrides, dict) else 0
        parts.append(f"ip_family={provider_ip_family(provider, pcfg)}")
        parts.append(f"endpoint_overrides={count}")
    if presentation.show_route:
        routed = parse_bool(pcfg.get("route_through_router"), default=False)
        parts.append(f"routed={'on' if routed else 'off'}")
    elif presentation.show_tool_choice:
        parts.append(f"tool_choice={provider_tool_choice_status(provider, pcfg)}")
    forced_query = str(pcfg.get("force_query_string") or "").strip()
    if forced_query:
        parts.append(f"query={forced_query}")
    if presentation.show_sampling:
        parts.extend(provider_sampling_status(pcfg))
    if presentation.show_stream:
        parts.append(f"stream={'on' if bool(pcfg.get('stream_enabled', True)) else 'off'}")
        if bool(pcfg.get("stream_word_chunking", False)):
            parts.append("word_chunk=on")
    return ", ".join(parts)


def llm_options_status(provider: str, pcfg: dict[str, Any]) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    config = provider_contract_config(provider, pcfg)
    presentation = adapter.option_presentation_policy(config)
    if adapter.context_policy(config).settings_strategy == "ollama":
        opts = ollama_extra_options(pcfg)
        pieces = [
            f"ctx {ollama_num_ctx_status(pcfg)}",
            f"keep {pcfg.get('keep_alive', 'default')}",
            f"think {bool(pcfg.get('think', False))}",
            f"timeout {pcfg.get('request_timeout_ms', 'default')}ms",
        ]
        if pcfg.get("stream_idle_timeout_ms") is not None:
            pieces.append(f"stream_idle_timeout={pcfg.get('stream_idle_timeout_ms')}ms")
        for key in ("num_predict", "temperature", "top_p", "top_k"):
            if key in opts:
                pieces.append(f"{key}={opts[key]}")
        return "; ".join(pieces)
    if presentation.show_route and not adapter.configuration_policy(config).runtime_owns_model:
        return (
            f"max_output_tokens={pcfg.get('max_output_tokens', 'Claude Code default')}, "
            f"timeout={pcfg.get('request_timeout_ms', 'Claude Code default')}ms, "
            f"routed={'on' if anthropic_routed_enabled(provider, pcfg) else 'off'}"
        )
    if presentation.show_tool_choice or presentation.show_route:
        return provider_options_status(provider, pcfg)
    return "provider defaults"


def model_option_family(provider: str, pcfg: dict[str, Any]) -> str:
    return classify_model_family(
        pcfg,
        provider_context_policy(provider, pcfg),
        provider_model_context_capacity(provider, pcfg),
        context_preset_services(provider),
    )


def recommended_preset_id(provider: str, pcfg: dict[str, Any]) -> str:
    return recommended_preset(
        model_option_family(provider, pcfg), provider_model_context_capacity(provider, pcfg)
    )






def llm_slider_preset_ids() -> list[str]:
    return list(LLM_PRESETS)


def llm_preset_command_name(preset_id: str) -> str:
    return "llm-" + re.sub(r"[^a-z0-9]+", "-", str(preset_id or "").lower()).strip("-")


def llm_preset_slash_command(preset_id: str) -> str:
    label, description = llm_preset_text(preset_id, "en")
    return f"""---
description: Apply ciel-runtime live preset: {label}
argument-hint: [ignored]
---

CIEL_RUNTIME_LIVE_LLM_OPTIONS

Value: {preset_id}

Apply the ciel-runtime live LLM preset `{preset_id}` ({description}) to this routed session. The original options are captured before the first live preset change and can be restored with `/llm-restore`.
"""


def normalize_llm_preset_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def resolve_llm_preset_id(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = normalize_llm_preset_token(raw)
    aliases = {
        "65k": "long-context-65k",
        "long-65k": "long-context-65k",
        "context-65k": "long-context-65k",
        "128k": "long-context-128k",
        "long-128k": "long-context-128k",
        "context-128k": "long-context-128k",
        "256k": "long-context-256k",
        "long-256k": "long-context-256k",
        "context-256k": "long-context-256k",
        "300k": "long-context-300k",
        "long-300k": "long-context-300k",
        "context-300k": "long-context-300k",
        "512k": "long-context-512k",
        "long-512k": "long-context-512k",
        "context-512k": "long-context-512k",
        "1m": "million-context-1m",
        "million": "million-context-1m",
        "million-context": "million-context-1m",
        "ultra": "million-context-1m",
        "ultra-context": "million-context-1m",
        "output": "large-output",
        "large": "large-output",
        "report": "large-output",
    }
    if normalized in aliases:
        return aliases[normalized]
    for preset_id, (label, _description) in LLM_PRESETS.items():
        candidates = {
            normalize_llm_preset_token(preset_id),
            normalize_llm_preset_token(label),
            normalize_llm_preset_token(llm_preset_command_name(preset_id)),
            normalize_llm_preset_token(llm_preset_command_name(preset_id).removeprefix("llm-")),
        }
        if normalized in candidates:
            return preset_id
    return None










def llm_preset_timeout_ms(preset_id: str) -> int:
    return LLM_PRESET_TIMEOUT_MS.get(preset_id, DEFAULT_REQUEST_TIMEOUT_MS)


def active_llm_preset_timeout_ms(pcfg: dict[str, Any]) -> int | None:
    preset_id = str(pcfg.get("llm_preset") or "").strip()
    if not preset_id:
        return None
    return positive_int(LLM_PRESET_TIMEOUT_MS.get(preset_id))


def timeout_profile_id_for_ms(ms: int | None) -> str | None:
    if not ms:
        return None
    for preset_id, (preset_ms, _, _) in TIMEOUT_PRESETS.items():
        if ms == preset_ms:
            return preset_id
    return None


def timeout_profile_text(profile_id: str, lang: str | None = None) -> tuple[str, str]:
    lang = lang or load_config().get("language", "en")
    if profile_id == "__custom__":
        return {
            "ko": ("사용자 지정", "직접 입력한 timeout 값"),
            "ja": ("カスタム", "直接入力した timeout 値"),
            "zh": ("自定义", "手动输入的 timeout 值"),
        }.get(lang, ("Custom", "manually entered timeout value"))
    fallback = TIMEOUT_PRESETS[profile_id]
    return TIMEOUT_PRESET_I18N.get(lang, {}).get(profile_id, (fallback[1], fallback[2]))


def timeout_profile_status(pcfg: dict[str, Any], lang: str | None = None) -> str:
    ms = positive_int(pcfg.get("request_timeout_ms")) or DEFAULT_REQUEST_TIMEOUT_MS
    profile_id = timeout_profile_id_for_ms(ms)
    if profile_id:
        label = timeout_profile_text(profile_id, lang)[0]
    else:
        label = timeout_profile_text("__custom__", lang)[0]
    idle = positive_int(pcfg.get("stream_idle_timeout_ms"))
    idle_text = f"; idle {idle}ms" if idle and idle != ms else ""
    return f"{label}; {ms}ms{idle_text}"


def timeout_profile_idle_ms(request_timeout_ms: int) -> int:
    return min(request_timeout_ms, 300000)


def timeout_profile_panel_rows(pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
    lang = lang or load_config().get("language", "en")
    current_ms = positive_int(pcfg.get("request_timeout_ms")) or DEFAULT_REQUEST_TIMEOUT_MS
    rows = [f"Current timeout: {current_ms} ms = {format_timeout_minutes(current_ms, lang)}"]
    values = ["__info__"]
    current_profile = timeout_profile_id_for_ms(current_ms)
    for profile_id, (ms, _, _) in TIMEOUT_PRESETS.items():
        label, description = timeout_profile_text(profile_id, lang)
        mark = "*" if profile_id == current_profile else " "
        rows.append(f"{mark} {pad_cells(label, 22)} {ms:>7} ms  {description}")
        values.append(profile_id)
    rows.append(ui_text("back", lang))
    values.append("back")
    return rows, values


def apply_timeout_profile_to_provider(pcfg: dict[str, Any], profile_id: str, lang: str | None = None) -> list[str]:
    if profile_id not in TIMEOUT_PRESETS:
        raise SystemExit(f"Unknown timeout preset: {profile_id}")
    ms, _, _ = TIMEOUT_PRESETS[profile_id]
    idle_ms = timeout_profile_idle_ms(ms)
    pcfg["request_timeout_ms"] = ms
    pcfg["stream_idle_timeout_ms"] = idle_ms
    label = timeout_profile_text(profile_id, lang)[0]
    return [f"Timeout preset: {label}", f"request_timeout_ms: {ms}", f"stream_idle_timeout_ms: {idle_ms}"]


def with_preset_timeout_tokens(tokens: list[str], preset_id: str) -> list[str]:
    filtered = [
        token
        for token in tokens
        if not token.startswith(("timeout=", "timeout_ms=", "request_timeout=", "request_timeout_ms=", "stream_idle_timeout=", "stream_idle_timeout_ms="))
    ]
    timeout_ms = llm_preset_timeout_ms(preset_id)
    idle_ms = timeout_profile_idle_ms(timeout_ms)
    filtered.append(f"timeout={timeout_ms}")
    filtered.append(f"stream_idle_timeout_ms={idle_ms}")
    return filtered




def is_qwen36_plus_model_id(model_id: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", (model_id or "").lower())
    return "qwen36plus" in compact


def is_kimi_k3_model_id(model_id: str) -> bool:
    normalized = strip_claude_context_suffix(model_id).strip().lower().replace("_", "-")
    if normalized.startswith("ciel-runtime-kimi-"):
        normalized = normalized[len("ciel-runtime-kimi-"):]
    return normalized in {"k3", "kimi-k3", "kimi/k3", "kimi-code/k3"}


def apply_kimi_model_profile(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return apply_provider_model_profile(provider, pcfg)


def apply_provider_model_profile(provider: str, pcfg: dict[str, Any]) -> list[str]:
    adapter = configured_provider_adapter(provider, pcfg)
    updates, notice = adapter.model_configuration_profile(
        provider_contract_config(provider, pcfg)
    )
    if not updates:
        return []
    changed = any(pcfg.get(key) != value for key, value in updates.items())
    pcfg.update(updates)
    return [notice] if changed and notice else []


def zai_model_context_hint(model_id: str) -> int | None:
    model = strip_claude_context_suffix(model_id).strip().lower().replace("_", "-")
    if not model:
        return None
    for prefix, limit in ZAI_MODEL_CONTEXT_HINTS:
        if model == prefix or model.startswith(prefix + "-"):
            return limit
    return None


def model_context_hint_from_model_id(model_id: str) -> int | None:
    model = (model_id or "").lower()
    if not model:
        return None
    zai_hint = zai_model_context_hint(model_id)
    if zai_hint:
        return zai_hint
    if is_qwen36_plus_model_id(model_id):
        return 1048576
    if is_kimi_k3_model_id(model_id):
        return 1048576
    catalog_limit, _, _ = ollama_catalog_context_for_model(model_id)
    if catalog_limit:
        return catalog_limit
    if any(marker in model for marker in ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4", "v4-pro", "v4-flash", "1m", "million")):
        return 1048576
    if any(marker in model for marker in ("kimi-for-coding", "kimi-code", "kimi-k2.7", "kimi_k2.7", "kimi2.7", "k2.7", "kimi-k2.6", "kimi_k2.6", "kimi2.6", "kimi-k2")):
        return 262144
    if "qwen3.6" in model:
        return 262144
    if "glm-4.7" in model or "glm-5.1" in model:
        return 200000
    if "deepseek-r1" in model or "llama3.3" in model:
        return 131072
    preset = model_preset(model_id)
    return positive_int(preset.get("num_ctx_max"))


def provider_context_policy(provider: str, pcfg: dict[str, Any]):
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.context_policy(provider_contract_config(provider, pcfg))


def provider_model_context_capacity(provider: str, pcfg: dict[str, Any]) -> int | None:
    return resolve_context_capacity(
        provider,
        pcfg,
        provider_context_policy(provider, pcfg),
        ProviderContextServices(
            positive_int=positive_int,
            model_context_hint=model_context_hint_from_model_id,
            anthropic_context_hint=lambda model: positive_int(
                anthropic_model_limit_hints(model).get("context_window")
            ),
            nvidia_context_default=nvidia_hosted_context_default,
            upstream_context_limit=upstream_model_context_limit,
            ollama_context_limit=ollama_provider_context_limit,
        ),
    )


def cap_context_settings_to_model_capacity(provider: str, pcfg: dict[str, Any]) -> list[str]:
    capacity = provider_model_context_capacity(provider, pcfg)
    return apply_context_capacity_cap(
        pcfg,
        capacity,
        provider_context_policy(provider, pcfg),
        positive_int=positive_int,
    )


def small_context_output_token_cap(context_window: int | None) -> int | None:
    context = positive_int(context_window)
    if not context or context > 262144:
        return None
    divisor = 16 if context <= 131072 else 32
    cap = max(1024, min(8192, context // divisor))
    return max(1024, (cap // 1024) * 1024)


def cap_output_tokens_to_context_ratio(provider: str, pcfg: dict[str, Any], configured: int | None) -> int | None:
    value = positive_int(configured)
    if not value or provider_context_policy(provider, pcfg).settings_strategy == "managed":
        return value
    context = context_limit_for_status(provider, pcfg)
    cap = small_context_output_token_cap(context)
    if not cap:
        return value
    return min(value, cap)


def cap_output_settings_to_context_ratio(provider: str, pcfg: dict[str, Any]) -> list[str]:
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "managed":
        return []
    context = context_limit_for_status(provider, pcfg)
    cap = small_context_output_token_cap(context)
    if not cap:
        return []
    messages: list[str] = []
    if settings_strategy == "ollama":
        opts = pcfg.setdefault("ollama_options", {})
        current = positive_int(opts.get("num_predict")) or positive_int(pcfg.get("max_output_tokens"))
        if current and current > cap:
            opts["num_predict"] = cap
            pcfg["max_output_tokens"] = cap
            messages.append(
                f"Max output capped to {cap:,} tokens for context {format_context_tokens(context)}."
            )
        return messages
    if settings_strategy == "standard":
        current = positive_int(pcfg.get("max_output_tokens"))
        if current and current > cap:
            pcfg["max_output_tokens"] = cap
            messages.append(
                f"Max output capped to {cap:,} tokens for context {format_context_tokens(context)}."
            )
    return messages


def cached_current_model_info(provider: str, pcfg: dict[str, Any]) -> dict[str, Any]:
    info = read_model_info_cache(provider, pcfg)
    if not info:
        return {}
    candidates = [
        normalize_model_id(provider, current_upstream_model_id(provider, pcfg)),
        normalize_model_id(provider, str(pcfg.get("current_model") or "")),
        strip_claude_context_suffix(normalize_model_id(provider, str(pcfg.get("current_model") or ""))),
    ]
    for model_id in candidates:
        if model_id and model_id in info:
            return info[model_id]
    current = normalize_model_id(provider, current_upstream_model_id(provider, pcfg)).casefold()
    for model_id, model_info in info.items():
        if normalize_model_id(provider, model_id).casefold() == current:
            return model_info
    return {}


def apply_current_model_specs_to_provider(provider: str, pcfg: dict[str, Any]) -> list[str]:
    messages = apply_provider_model_profile(provider, pcfg)
    info = cached_current_model_info(provider, pcfg)
    max_context = positive_int(info.get("max_model_len")) if info else None
    if not max_context:
        return messages
    model = normalize_model_id(provider, current_upstream_model_id(provider, pcfg))
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "ollama":
        if not ollama_context_model_matches(model, str(pcfg.get("model_context_model") or "")) or positive_int(pcfg.get("model_context_max")) != max_context:
            pcfg["model_context_max"] = max_context
            pcfg["model_context_model"] = model
            messages.append(f"Model context size from provider specs: {format_context_tokens(max_context)} ({max_context:,} tokens).")
        current_max = positive_int(pcfg.get("num_ctx_max"))
        if current_max and current_max <= max_context and ollama_preserve_configured_context_cap(pcfg):
            pass
        else:
            pcfg["num_ctx_max"] = min(current_max, max_context) if current_max and current_max > max_context else max_context
        return messages
    if settings_strategy == "standard":
        if positive_int(pcfg.get("max_model_len")) != max_context:
            pcfg["max_model_len"] = max_context
            messages.append(f"Model context size from provider specs: {format_context_tokens(max_context)} ({max_context:,} tokens).")
    return messages


def refresh_current_model_specs_for_auto_llm(provider: str, pcfg: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    try:
        models = upstream_model_ids(provider, pcfg, force_refresh=True)
        if models:
            messages.append(f"Model specs refreshed from provider: {len(models)} model(s).")
    except Exception as exc:
        messages.append(f"Model specs refresh failed: {type(exc).__name__}: {exc}")
    messages.extend(apply_current_model_specs_to_provider(provider, pcfg))
    return messages


def apply_lm_studio_loaded_context_guard(pcfg: dict[str, Any], load: bool = False) -> list[str]:
    if load:
        try:
            return ensure_lm_studio_model_loaded_for_context(pcfg, timeout=1.5)
        except Exception as exc:
            pcfg["native_compat"] = False
            return [
                "LM Studio could not automatically load the selected model with the recommended context.",
                f"LM Studio load error: {type(exc).__name__}: {exc}",
            ]

    info = None
    target = lm_studio_target_context(pcfg, info)
    loaded = positive_int((info or {}).get("loaded_context_len"))
    state = str((info or {}).get("state") or "")
    max_len = positive_int((info or {}).get("max_model_len"))
    messages: list[str] = []
    if max_len:
        messages.append(f"LM Studio model max context: {max_len:,} tokens.")
    if target:
        messages.append(f"LM Studio target context: {target:,} tokens.")
    if max_len and max_len < LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT:
        pcfg["native_compat"] = False
        pcfg["context_window"] = max_len
        messages.append(
            "LM Studio selected model cannot provide enough context for Claude Code "
            f"({max_len:,} < {LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT:,})."
        )
        return messages
    pcfg["native_compat"] = True
    if loaded:
        messages.append(f"LM Studio currently loaded context: {loaded:,} tokens.")
        if target and loaded < target:
            messages.append("LM Studio will reload this model with the target context when you launch or test.")
    elif state and state != "loaded":
        messages.append("LM Studio will load this model with the target context when you launch or test.")
    elif target:
        messages.append("LM Studio will prepare this model with the target context when you launch or test.")
    return messages


def required_context_for_preset(preset_id: str, provider: str | None = None) -> int | None:
    provider = provider or "anthropic"
    return context_required_for_preset(
        preset_id, provider_context_policy(provider, {})
    )


def preset_available_for_model(provider: str, pcfg: dict[str, Any], preset_id: str) -> bool:
    required = required_context_for_preset(preset_id, provider)
    if not required:
        return True
    capacity = provider_model_context_capacity(provider, pcfg)
    if not capacity:
        return True
    return required <= capacity


def format_context_tokens(value: int | None) -> str:
    if not value:
        return "unknown"
    if value >= 1024 * 1024 and value % (1024 * 1024) == 0:
        return f"{value // (1024 * 1024)}M"
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024}K"
    return f"{value:,}"


def format_parameter_count(value: Any) -> str:
    fixed = positive_int(value)
    if not fixed:
        return ""
    units = (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000))
    for suffix, scale in units:
        if fixed >= scale:
            scaled = fixed / scale
            text = f"{scaled:.1f}".rstrip("0").rstrip(".")
            return f"{text}{suffix}"
    return str(fixed)


def context_setting_status(provider: str, pcfg: dict[str, Any]) -> str:
    capacity = provider_model_context_capacity(provider, pcfg)
    cap_text = format_context_tokens(capacity)
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "ollama":
        return f"model max {cap_text}; {ollama_num_ctx_status(pcfg)}"
    if settings_strategy == "standard":
        window = positive_int(pcfg.get("context_window"))
        reserve = positive_int(pcfg.get("context_reserve_tokens"))
        reserve_text = f"; reserve {format_context_tokens(reserve)}" if reserve else ""
        return f"model max {cap_text}; window {format_context_tokens(window)}{reserve_text}"
    if settings_strategy == "managed":
        return "managed by Claude Code"
    return f"model max {cap_text}"


def configured_context_window_for_timeout(provider: str, pcfg: dict[str, Any]) -> int | None:
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "ollama":
        raw_ctx = pcfg.get("num_ctx")
        fixed_ctx = positive_int(raw_ctx)
        if fixed_ctx:
            return fixed_ctx
        return (
            provider_model_context_capacity(provider, pcfg)
            or positive_int(pcfg.get("num_ctx_max"))
        )
    if settings_strategy == "standard":
        return positive_int(pcfg.get("context_window")) or provider_model_context_capacity(provider, pcfg)
    return provider_model_context_capacity(provider, pcfg)


def configured_output_tokens_for_timeout(provider: str, pcfg: dict[str, Any]) -> int | None:
    if provider_context_policy(provider, pcfg).settings_strategy == "ollama":
        opts = ollama_extra_options(pcfg)
        configured = positive_int(opts.get("num_predict")) or positive_int(pcfg.get("max_output_tokens"))
        return cap_output_tokens_to_context_ratio(provider, pcfg, configured)
    configured = positive_int(pcfg.get("max_output_tokens")) or positive_int(pcfg.get("num_predict"))
    return cap_output_tokens_to_context_ratio(provider, pcfg, configured)


def clamp_auto_timeout_ms(ms: int | float | None) -> int:
    value = positive_int(ms) or DEFAULT_REQUEST_TIMEOUT_MS
    value = max(AUTO_TIMEOUT_MIN_MS, min(AUTO_TIMEOUT_MAX_MS, value))
    return int(math.ceil(value / AUTO_TIMEOUT_ROUND_MS) * AUTO_TIMEOUT_ROUND_MS)


def calculated_request_timeout_ms(
    provider: str,
    pcfg: dict[str, Any],
    timeout_candidates: list[int] | None = None,
) -> int:
    context_policy = provider_context_policy(provider, pcfg)
    context_tokens = positive_int(configured_context_window_for_timeout(provider, pcfg))
    output_tokens = positive_int(configured_output_tokens_for_timeout(provider, pcfg))
    timeout_ms = AUTO_TIMEOUT_MIN_MS
    if context_tokens:
        # 64K -> +0, 128K -> +1m, 256K -> +2m, 512K -> +3m, 1M+ -> +4m.
        context_score = max(0.0, min(1.0, math.log2(max(context_tokens, 65536) / 65536) / 4.0))
        timeout_ms += int(240000 * context_score)
    if output_tokens:
        # 2K output is cheap; 8K+ output gets the full +2m allowance.
        output_score = max(0.0, min(1.0, (output_tokens - 2048) / 6144))
        timeout_ms += int(120000 * output_score)
    if context_policy.hosted_timeout:
        timeout_ms += 60000
    for candidate in timeout_candidates or []:
        fixed = positive_int(candidate)
        if fixed:
            timeout_ms = max(timeout_ms, fixed)
    timeout_ms *= context_policy.timeout_weight
    return clamp_auto_timeout_ms(timeout_ms)


def recommended_request_timeout_ms(provider: str, pcfg: dict[str, Any], use_context_fallback: bool = True) -> int:
    model = str(pcfg.get("current_model") or "")
    candidates: list[int] = []
    context_policy = provider_context_policy(provider, pcfg)
    active_preset_timeout = active_llm_preset_timeout_ms(pcfg)
    if active_preset_timeout:
        candidates.append(active_preset_timeout)
    if context_policy.uses_catalog_timeout:
        catalog_timeout = ollama_catalog_timeout_for_model(model)
        if catalog_timeout:
            candidates.append(catalog_timeout)
    model_timeout = positive_int(model_preset(model).get("recommended_timeout_ms"))
    if model_timeout:
        candidates.append(model_timeout)
    if candidates:
        return calculated_request_timeout_ms(provider, pcfg, candidates)
    if not use_context_fallback:
        return DEFAULT_REQUEST_TIMEOUT_MS
    context_timeout = recommended_timeout_ms_for_context(configured_context_window_for_timeout(provider, pcfg))
    return calculated_request_timeout_ms(provider, pcfg, [context_timeout])


def apply_recommended_timeout_for_model_context(
    provider: str,
    pcfg: dict[str, Any],
    use_context_fallback: bool = True,
) -> list[str]:
    timeout_ms = recommended_request_timeout_ms(provider, pcfg, use_context_fallback=use_context_fallback)
    idle_ms = timeout_profile_idle_ms(timeout_ms)
    changed = positive_int(pcfg.get("request_timeout_ms")) != timeout_ms or positive_int(pcfg.get("stream_idle_timeout_ms")) != idle_ms
    pcfg["request_timeout_ms"] = timeout_ms
    pcfg["stream_idle_timeout_ms"] = idle_ms
    context = configured_context_window_for_timeout(provider, pcfg)
    if not changed:
        return []
    return [
        f"Auto timeout: {timeout_ms}ms for context {format_context_tokens(context)}.",
        f"stream_idle_timeout_ms: {idle_ms}",
    ]


def context_mode_values_for_capacity(capacity: int | None) -> dict[str, tuple[int, int, int]]:
    cap = capacity or 131072

    def clamp(value: int) -> int:
        return max(8192, min(cap, value))

    compact = clamp(32768)
    balanced = clamp(65536 if cap <= 131072 else 131072)
    project = clamp(262144 if cap >= 262144 else cap)
    full = clamp(cap)
    return {
        "context-compact": (compact, min(2048, max(1024, compact // 16)), 4096),
        "context-balanced": (balanced, min(4096, max(2048, balanced // 16)), 4096),
        "context-project": (project, min(8192, max(4096, project // 16)), 8192),
        "context-full": (full, min(16384, max(4096, full // 16)), 8192),
    }


def context_setup_text(key: str, lang: str | None = None) -> tuple[str, str]:
    lang = lang or load_config().get("language", "en")
    entries = {
        "en": {
            "context-compact": ("Compact / fast", "small context, faster and cheaper"),
            "context-balanced": ("Balanced", "good default for normal coding sessions"),
            "context-project": ("Large project", "more files/history, slower but safer for big work"),
            "context-full": ("Full model window", "use the selected model's maximum context"),
        },
        "ko": {
            "context-compact": ("컴팩트/빠름", "작은 컨텍스트, 빠르고 가벼움"),
            "context-balanced": ("균형형", "일반 코딩 세션의 권장 기본값"),
            "context-project": ("대형 프로젝트", "파일/히스토리를 더 많이 사용, 큰 작업에 안정적"),
            "context-full": ("모델 최대 컨텍스트", "선택한 모델의 최대 컨텍스트 사용"),
        },
        "ja": {
            "context-compact": ("コンパクト/高速", "小さなコンテキストで高速かつ軽量"),
            "context-balanced": ("バランス", "通常のコーディングセッション向けの既定値"),
            "context-project": ("大規模プロジェクト", "より多くのファイル/履歴を使う大型作業向け"),
            "context-full": ("モデル最大コンテキスト", "選択モデルの最大コンテキストを使用"),
        },
        "zh": {
            "context-compact": ("紧凑/快速", "较小上下文，更快更轻"),
            "context-balanced": ("均衡", "普通编码会话的推荐默认值"),
            "context-project": ("大型项目", "使用更多文件/历史，适合大任务"),
            "context-full": ("模型最大上下文", "使用所选模型的最大上下文"),
        },
    }
    return entries.get(lang, entries["en"]).get(key, entries["en"][key])


def context_setup_panel_rows(provider: str, pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
    lang = lang or load_config().get("language", "en")
    capacity = provider_model_context_capacity(provider, pcfg)
    rows = [f"Model context capacity: {format_context_tokens(capacity)}"]
    values = ["__info__"]
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "managed":
        rows.append("Claude Code manages Anthropic context automatically.")
        values.append("__info__")
        rows.append(ui_text("back", lang))
        values.append("back")
        return rows, values
    current_window = positive_int(
        pcfg.get("num_ctx_max" if settings_strategy == "ollama" else "context_window")
    )
    choices = context_mode_values_for_capacity(capacity)
    ordered_keys = ["context-compact", "context-balanced", "context-project", "context-full"]
    visible_keys: list[str] = []
    seen_windows: set[int] = set()
    for key in reversed(ordered_keys):
        window = choices[key][0]
        if window in seen_windows:
            continue
        seen_windows.add(window)
        visible_keys.append(key)
    for key in reversed(visible_keys):
        window, reserve, output = choices[key]
        label, description = context_setup_text(key, lang)
        mark = "*" if current_window == window else " "
        rows.append(
            f"{mark} {pad_cells(label, 22)} "
            f"{format_context_tokens(window):>6}  out {format_context_tokens(output):>5}  {description}"
        )
        values.append(key)
    rows.append(ui_text("back", lang))
    values.append("back")
    return rows, values


def apply_context_setup_to_provider(provider: str, pcfg: dict[str, Any], mode: str, lang: str | None = None) -> list[str]:
    choices = context_mode_values_for_capacity(provider_model_context_capacity(provider, pcfg))
    if mode not in choices:
        raise SystemExit(f"Unknown context mode: {mode}")
    window, reserve, output = choices[mode]
    label = context_setup_text(mode, lang)[0]
    settings_strategy = provider_context_policy(provider, pcfg).settings_strategy
    if settings_strategy == "ollama":
        pcfg["num_ctx"] = "auto"
        pcfg["num_ctx_max"] = window
        pcfg["num_ctx_min"] = min(window, 32768 if window <= 65536 else 65536)
        pcfg.setdefault("ollama_options", {})["num_predict"] = output
    elif settings_strategy == "standard":
        pcfg["context_window"] = window
        pcfg["context_reserve_tokens"] = reserve
        pcfg["max_output_tokens"] = output
    else:
        return ["Context setup is managed by Claude Code for this provider."]
    messages = cap_context_settings_to_model_capacity(provider, pcfg)
    messages.extend(cap_output_settings_to_context_ratio(provider, pcfg))
    messages.extend(apply_recommended_timeout_for_model_context(provider, pcfg))
    return [
        f"{ui_text('context_setup', lang)}: {label}",
        f"Applied context: {context_setting_status(provider, pcfg)}",
        *messages,
    ]


def apply_context_setup_config(provider: str, mode: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    lines = apply_context_setup_to_provider(provider, pcfg, mode, cfg.get("language", "en"))
    save_config(cfg)
    clear_model_cache()
    return lines




def applied_preset_id(provider: str, pcfg: dict[str, Any]) -> str:
    preset_id = str(pcfg.get("llm_preset") or "").strip()
    if preset_id in LLM_PRESETS:
        return preset_id
    inferred = infer_preset_id_from_options(provider, pcfg)
    if inferred and preset_available_for_model(provider, pcfg, inferred):
        return inferred
    recommended = recommended_preset_id(provider, pcfg)
    if preset_available_for_model(provider, pcfg, recommended):
        return recommended
    return "balanced"


def infer_preset_id_from_options(provider: str, pcfg: dict[str, Any]) -> str | None:
    return infer_context_preset(
        pcfg,
        provider_context_policy(provider, pcfg),
        context_preset_services(provider),
    )


def context_preset_services(provider: str) -> ContextPresetServices:
    return ContextPresetServices(
        positive_int=positive_int,
        ollama_options=ollama_extra_options,
        ollama_thinking_enabled=lambda _model, config: ollama_request_think_enabled(
            current_upstream_model_id(provider, config), config
        ),
    )


def llm_preset_text(preset_id: str, lang: str | None = None) -> tuple[str, str]:
    lang = lang or load_config().get("language", "en")
    return LLM_PRESET_I18N.get(lang, {}).get(preset_id, LLM_PRESETS[preset_id])


def model_family_text(family: str, lang: str | None = None) -> str:
    lang = lang or load_config().get("language", "en")
    return MODEL_FAMILY_I18N.get(lang, {}).get(family, family)


def llm_preset_panel_rows(provider: str, pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
    lang = lang or load_config().get("language", "en")
    recommended = recommended_preset_id(provider, pcfg)
    applied = applied_preset_id(provider, pcfg)
    family = model_option_family(provider, pcfg)
    recommended_label, _ = llm_preset_text(recommended, lang)
    rows = [
        f"{ui_text('model_family', lang)}: {model_family_text(family, lang)}; "
        f"{ui_text('recommended_preset_is', lang)} {recommended_label}"
    ]
    values = ["__info__"]
    for preset_id in LLM_PRESETS:
        label, description = llm_preset_text(preset_id, lang)
        mark = "*" if preset_id == applied else " "
        suffix = ""
        required = required_context_for_preset(preset_id, provider)
        capacity = provider_model_context_capacity(provider, pcfg) if required else None
        if required and capacity and required > capacity:
            suffix = f" (requires {format_context_tokens(required)}; server {format_context_tokens(capacity)})"
        rows.append(f"{mark} {pad_cells(label, 24)} {description}{suffix}")
        values.append(preset_id)
    rows.append(ui_text("back", lang))
    values.append("back")
    return rows, values


def apply_llm_preset_to_provider(
    provider: str,
    pcfg: dict[str, Any],
    preset_id: str,
    lang: str | None = None,
    sync_ollama_context: bool = True,
    load_lm_studio: bool = False,
) -> list[str]:
    return apply_preset_to_provider(
        provider, pcfg, preset_id, lang,
        sync_ollama_context=sync_ollama_context,
        load_lm_studio=load_lm_studio,
        services=PresetServices(
            definition=PresetDefinition(
                CONTEXT_HEAVY_PRESETS=CONTEXT_HEAVY_PRESETS,
                LLM_PRESETS=LLM_PRESETS,
                llm_preset_text=llm_preset_text,
                load_config=load_config,
                model_family_text=model_family_text,
                model_option_family=model_option_family,
                positive_int=positive_int,
                required_context_for_preset=required_context_for_preset,
                ui_text=ui_text,
            ),
            context_policy=PresetContextPolicy(
                apply_lm_studio_loaded_context_guard=apply_lm_studio_loaded_context_guard,
                apply_ollama_runtime_output_guard=apply_ollama_runtime_output_guard,
                apply_recommended_timeout_for_model_context=apply_recommended_timeout_for_model_context,
                cap_context_settings_to_model_capacity=cap_context_settings_to_model_capacity,
                cap_output_settings_to_context_ratio=cap_output_settings_to_context_ratio,
                ollama_num_ctx_status=ollama_num_ctx_status,
                provider_model_context_capacity=provider_model_context_capacity,
                sync_ollama_library_context_limit=sync_ollama_library_context_limit,
                upstream_model_context_limit=upstream_model_context_limit,
                with_preset_timeout_tokens=with_preset_timeout_tokens,
            ),
            provider_mutation=PresetProviderMutation(
                apply_ollama_option=apply_ollama_option,
                apply_provider_option=apply_provider_option,
                ollama_extra_options=ollama_extra_options,
            ),
        ),
    )


def auto_apply_recommended_llm_preset_for_model(provider: str, pcfg: dict[str, Any], lang: str | None = None) -> list[str]:
    preset_id = recommended_preset_id(provider, pcfg)
    if not preset_available_for_model(provider, pcfg, preset_id):
        return []
    label = llm_preset_text(preset_id, lang)[0]
    lines = [f"Auto-applied recommended LLM preset for selected model: {label}."]
    lines.extend(apply_llm_preset_to_provider(provider, pcfg, preset_id, lang, sync_ollama_context=False))
    return lines


def apply_auto_llm_options_config(model_id: str | None = None) -> list[str]:
    lines: list[str] = []
    if model_id and model_id.strip():
        lines.extend(set_model_config(model_id.strip()))
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    model = str(pcfg.get("current_model") or "").strip()
    lines.extend(refresh_current_model_specs_for_auto_llm(provider, pcfg))
    if model:
        context_msgs = sync_ollama_library_context_limit(provider, pcfg, model)
        context_msgs.extend(cap_context_settings_to_model_capacity(provider, pcfg))
    else:
        context_msgs = []
    preset_id = recommended_preset_id(provider, pcfg)
    if not preset_available_for_model(provider, pcfg, preset_id):
        preset_id = applied_preset_id(provider, pcfg)
    label = llm_preset_text(preset_id, cfg.get("language", "en"))[0]
    lines.append(f"Auto LLM options applied for {provider}{f' model {model}' if model else ''}: {label}.")
    lines.extend(context_msgs)
    lines.extend(apply_llm_preset_to_provider(provider, pcfg, preset_id, cfg.get("language", "en")))
    save_config(cfg)
    invalidate_config_cache()
    return lines


def apply_llm_preset_config(provider: str, preset_id: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    lines = apply_llm_preset_to_provider(provider, pcfg, preset_id, cfg.get("language", "en"))
    save_config(cfg)
    clear_model_cache()
    return lines




def runtime_llm_snapshot_from_provider(provider: str, pcfg: dict[str, Any]) -> dict[str, Any]:
    values = {
        key: json.loads(json.dumps(pcfg[key]))
        for key in sorted(RUNTIME_LLM_OPTION_KEYS)
        if key in pcfg
    }
    return {
        "version": 1,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": provider,
        "model": str(pcfg.get("current_model") or ""),
        "values": values,
    }


def ensure_runtime_llm_original_snapshot(provider: str, pcfg: dict[str, Any]) -> bool:
    existing = pcfg.get(RUNTIME_LLM_ORIGINAL_KEY)
    if isinstance(existing, dict) and isinstance(existing.get("values"), dict):
        return False
    pcfg[RUNTIME_LLM_ORIGINAL_KEY] = runtime_llm_snapshot_from_provider(provider, pcfg)
    return True


def restore_runtime_llm_original_options(provider: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    snapshot = pcfg.get(RUNTIME_LLM_ORIGINAL_KEY)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("values"), dict):
        return ["No captured live LLM options to restore."]
    values = json.loads(json.dumps(snapshot.get("values") or {}))
    for key in RUNTIME_LLM_OPTION_KEYS:
        pcfg.pop(key, None)
    pcfg.update(values)
    pcfg.pop(RUNTIME_LLM_ORIGINAL_KEY, None)
    save_config(cfg)
    clear_model_cache()
    return [
        "Restored live LLM options to the values captured before the first runtime preset change.",
        f"Captured provider/model: {snapshot.get('provider') or provider} / {snapshot.get('model') or 'unknown'}",
    ]


def apply_runtime_llm_preset_config(provider: str, preset_id: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    captured = ensure_runtime_llm_original_snapshot(provider, pcfg)
    lines = apply_llm_preset_to_provider(provider, pcfg, preset_id, cfg.get("language", "en"))
    if captured:
        lines.insert(0, "Captured current live LLM options for /llm-restore.")
    save_config(cfg)
    clear_model_cache()
    return lines


def runtime_llm_slider_line(provider: str, pcfg: dict[str, Any]) -> str:
    current = applied_preset_id(provider, pcfg)
    parts: list[str] = []
    for preset_id in llm_slider_preset_ids():
        label = LLM_SLIDER_LABELS.get(preset_id, preset_id)
        parts.append(f"[{label}]" if preset_id == current else label)
    return "< " + " | ".join(parts) + " >"


def apply_runtime_llm_slider_delta_config(provider: str, delta: int) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    presets = llm_slider_preset_ids()
    current = applied_preset_id(provider, pcfg)
    try:
        current_idx = presets.index(current)
    except ValueError:
        current_idx = 0
    next_idx = max(0, min(len(presets) - 1, current_idx + delta))
    next_preset = presets[next_idx]
    if next_idx == current_idx:
        label = llm_preset_text(next_preset, cfg.get("language", "en"))[0]
        return [f"Live LLM preset remains at {label}.", f"Slider: {runtime_llm_slider_line(provider, pcfg)}"]
    captured = ensure_runtime_llm_original_snapshot(provider, pcfg)
    lines = apply_llm_preset_to_provider(provider, pcfg, next_preset, cfg.get("language", "en"))
    if captured:
        lines.insert(0, "Captured current live LLM options for /llm-restore.")
    save_config(cfg)
    clear_model_cache()
    label = llm_preset_text(next_preset, cfg.get("language", "en"))[0]
    return [f"Live LLM preset moved to {label}."] + lines + [f"Slider: {runtime_llm_slider_line(provider, pcfg)}"]


def runtime_llm_status_lines(provider: str, pcfg: dict[str, Any]) -> list[str]:
    cfg = load_config()
    lang = cfg.get("language", "en")
    applied = applied_preset_id(provider, pcfg)
    lines = [
        f"Provider: {provider_mode_label(provider, pcfg)}",
        f"Model: {pcfg.get('current_model') or 'unknown'}",
        f"Preset: {applied} ({llm_preset_text(applied, lang)[0]})",
        f"Slider: {runtime_llm_slider_line(provider, pcfg)}",
        f"Context: {context_setting_status(provider, pcfg)}",
        f"Timeout: {timeout_profile_status(pcfg, lang)}",
    ]
    if provider in ("ollama", "ollama-cloud"):
        opts = ollama_extra_options(pcfg)
        lines.append(f"Output tokens: {opts.get('num_predict', 'default')}")
    else:
        lines.append(f"Output tokens: {pcfg.get('max_output_tokens', 'default')}")
    lines.append(f"Restore available: {'yes' if isinstance(pcfg.get(RUNTIME_LLM_ORIGINAL_KEY), dict) else 'no'}")
    return lines


def runtime_llm_preset_list_lines(provider: str, pcfg: dict[str, Any]) -> list[str]:
    lang = load_config().get("language", "en")
    applied = applied_preset_id(provider, pcfg)
    lines = runtime_llm_status_lines(provider, pcfg)
    lines.append("")
    lines.append("Use `/llm left` or `/llm right` to move one step, or `/llm <preset-id>` to jump directly.")
    lines.append("Preset ids:")
    for preset_id in llm_slider_preset_ids():
        label, description = llm_preset_text(preset_id, lang)
        mark = "*" if preset_id == applied else " "
        lines.append(f"{mark} {preset_id} — {label}: {description}")
    lines.append("  /llm-restore  restore captured original options")
    lines.append("  /llm <left|right|preset-id|status|list|restore>")
    return lines




def llm_option_description(provider: str, key: str, lang: str | None = None) -> str:
    lang = lang or load_config().get("language", "en")
    entry = LLM_OPTION_DESCRIPTIONS.get(key)
    if not entry:
        return ""
    return entry.get(lang) or entry.get("en", "")


def format_timeout_minutes(ms: int, lang: str = "en") -> str:
    seconds = ms / 1000
    minutes = seconds / 60
    labels = {
        "ko": "분",
        "ja": "分",
        "zh": "分钟",
    }
    label = labels.get(lang, "minutes")
    if abs(minutes - round(minutes)) < 0.01:
        return f"{int(round(minutes))}{label if lang in labels else ' ' + label}"
    return f"{minutes:.1f}{label if lang in labels else ' ' + label}"


def llm_option_description_for_value(provider: str, pcfg: dict[str, Any], key: str, lang: str | None = None) -> str:
    text = llm_option_description(provider, key, lang)
    if key not in ("request_timeout_ms", "stream_idle_timeout_ms"):
        return text
    value = positive_int(pcfg.get(key))
    if not value:
        return text
    lang = lang or load_config().get("language", "en")
    suffix = {
        "ko": f" 현재값: {value} ms = {format_timeout_minutes(value, lang)}.",
        "ja": f" 現在値: {value} ms = {format_timeout_minutes(value, lang)}.",
        "zh": f" 当前值：{value} ms = {format_timeout_minutes(value, lang)}。",
    }.get(lang, f" Current value: {value} ms = {format_timeout_minutes(value, lang)}.")
    return text + suffix


# Boolean keys whose Enter handler should flip on/off in place instead of
# prompting for a value. Covers both on/off labels (stream_*) and True/False
# labels (native_compat, think).


def llm_option_current_bool(provider: str, pcfg: dict[str, Any], key: str) -> bool:
    adapter = configured_provider_adapter(provider, pcfg)
    config = provider_contract_config(provider, pcfg)
    return current_option_bool(
        provider,
        pcfg,
        key,
        OptionValuePolicy(
            context_strategy=adapter.context_policy(config).settings_strategy,
            native_default=adapter.option_presentation_policy(config).show_native,
        ),
        option_panel_services(),
    )


def rate_limit_status_label(provider: str, pcfg: dict[str, Any]) -> str:
    rpm = router_rate_limit_configured_rpm(provider, pcfg)
    return f"on ({rpm} rpm)" if rpm else "off"


def rate_limit_rpm_label(provider: str, pcfg: dict[str, Any]) -> str:
    rpm = router_rate_limit_configured_rpm(provider, pcfg)
    return str(rpm) if rpm else "0 (off)"


def llm_option_panel_rows(provider: str, pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
    lang = lang or load_config().get("language", "en")
    adapter = configured_provider_adapter(provider, pcfg)
    config = provider_contract_config(provider, pcfg)
    return build_option_panel_rows(
        provider,
        pcfg,
        OptionPanelPolicy(
            presentation=adapter.option_presentation_policy(config),
            context_strategy=adapter.context_policy(config).settings_strategy,
            shows_workflows=adapter.shows_claude_workflow_options(config),
            timeout_default=adapter.option_timeout_default(),
        ),
        option_panel_services(),
        language=lang,
    )


def option_panel_services() -> OptionPanelServices:
    return OptionPanelServices(
        text=OptionPanelText(
            compact_text=compact_text,
            ui_text=ui_text,
            context_status=context_setting_status,
            applied_preset=applied_preset_id,
            preset_text=llm_preset_text,
            timeout_status=timeout_profile_status,
        ),
        runtime=OptionPanelRuntime(
            router_debug_external=router_debug_external_access_enabled,
            message_preview_chars=router_debug_message_preview_chars,
            direct_native=direct_native_anthropic_enabled,
            capability_string=claude_code_capability_string,
            current_model=current_upstream_model_id,
            workflows_enabled=claude_code_workflows_enabled,
            ultracode_enabled=claude_code_ultracode_enabled,
        ),
        provider=OptionPanelProvider(
            ollama_options=ollama_extra_options,
            ollama_context_status=ollama_num_ctx_status,
            ollama_think_status=ollama_think_status,
            query_status=upstream_query_string_status,
            tool_choice_status=provider_tool_choice_status,
            rate_limit_status=rate_limit_status_label,
            rate_limit_rpm=rate_limit_rpm_label,
            ip_family=provider_ip_family,
            parse_bool=parse_bool,
            configured_rate_limit=router_rate_limit_configured_rpm,
        ),
    )


def llm_option_prompt_default(provider: str, pcfg: dict[str, Any], key: str) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    config = provider_contract_config(provider, pcfg)
    return option_prompt_default(
        provider,
        pcfg,
        key,
        OptionValuePolicy(
            context_strategy=adapter.context_policy(config).settings_strategy,
            native_default=adapter.option_presentation_policy(config).show_native,
        ),
        option_panel_services(),
    )


def llm_option_config_services() -> LlmOptionConfigServices:
    return LlmOptionConfigServices(
        repository=LlmOptionRepository(
            clear_model_cache=clear_model_cache,
            load_config=load_config,
            save_config=save_config,
        ),
        mutation=LlmOptionMutation(
            apply_ollama_option=apply_ollama_option,
            apply_provider_option=apply_provider_option,
            configuration_policy=provider_configuration_policy,
            normalize_capabilities=normalize_claude_code_supported_capabilities,
            parse_bool=parse_bool,
            positive_int=positive_int,
            routing_mode_update=provider_routing_mode_update,
            set_router_debug_external_access=set_router_debug_external_access_config,
        ),
        policy=LlmOptionPolicy(
            apply_recommended_timeout=apply_recommended_timeout_for_model_context,
            cap_context_settings=cap_context_settings_to_model_capacity,
            cap_output_settings=cap_output_settings_to_context_ratio,
            configured_rate_limit_rpm=router_rate_limit_configured_rpm,
            provider_labels=PROVIDER_LABELS,
        ),
    )


def provider_routing_mode_update(provider: str, enabled: bool) -> tuple[str, ...]:
    return PROVIDER_ADAPTERS.create(provider).routing_mode_update(enabled)


def provider_configuration_policy(provider: str, pcfg: dict[str, Any]):
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.configuration_policy(provider_contract_config(provider, pcfg))


def set_llm_option_config(provider: str, key: str, raw_value: str) -> list[str]:
    return apply_llm_option_config(
        provider,
        key,
        raw_value,
        services=llm_option_config_services(),
    )


def apply_provider_option(provider: str, pcfg: dict[str, Any], token: str) -> None:
    adapter = configured_provider_adapter(provider, pcfg)
    capabilities = adapter.configuration_policy(provider_contract_config(provider, pcfg))
    mutate_provider_option(
        provider,
        pcfg,
        token,
        policy=provider_option_policy(),
        capabilities=capabilities,
    )


def cmd_provider_options(args: argparse.Namespace) -> None:
    cfg = load_config()
    values = list(getattr(args, "values", []) or [])
    provider = cfg.get("current_provider", "vllm")
    if values:
        try:
            maybe_provider = normalize_provider(values[0])
            if maybe_provider in PROVIDER_OPTION_PROVIDERS:
                provider = maybe_provider
                values = values[1:]
        except SystemExit:
            pass
    if provider not in PROVIDER_OPTION_PROVIDERS:
        raise SystemExit("Provider options are available for anthropic, ollama, ollama-cloud, deepseek, opencode, opencode-go, kimi, z.ai, fireworks, vllm, lm-studio, nvidia-hosted, self-hosted-nim, and openrouter.")
    pcfg = cfg["providers"][provider]
    if values:
        context_changed = any(
            token.split("=", 1)[0].replace("unset:", "").strip() in ("context_window", "context", "max_model_len")
            for token in values
        )
        explicit_timeout = any(
            token.split("=", 1)[0].replace("unset:", "").strip() in ("timeout", "timeout_ms", "request_timeout", "request_timeout_ms", "stream_idle_timeout", "stream_idle_timeout_ms", "idle_timeout", "idle_timeout_ms")
            for token in values
        )
        for token in values:
            apply_provider_option(provider, pcfg, token)
        cap_lines = cap_context_settings_to_model_capacity(provider, pcfg)
        cap_lines.extend(cap_output_settings_to_context_ratio(provider, pcfg))
        timeout_lines = apply_recommended_timeout_for_model_context(provider, pcfg) if context_changed and not explicit_timeout else []
        save_config(cfg)
        clear_model_cache()
        print(f"Provider options updated for {provider}.")
        for line in cap_lines:
            print(line)
        for line in timeout_lines:
            print(line)
    print(f"provider: {provider}")
    print(f"provider_options: {provider_options_status(provider, pcfg)}")
    print("Notes:")
    print("  max_output_tokens is passed to Claude Code as CLAUDE_CODE_MAX_OUTPUT_TOKENS.")
    print("  context_window is a ciel-runtime/router cap; native mode still cannot raise the real server limit.")
    print("  temperature/top_p/top_k are injected by ciel-runtime router mode when the provider supports them.")
    if provider in OPENCODE_PROVIDER_NAMES:
        print("  OpenCode endpoint override: endpoint:<model-id>=messages|chat|responses|gemini")
        print("  OpenCode ip_family options: auto, ipv4, ipv6, ipv4-preferred, ipv6-preferred")
    if provider == "fireworks":
        print("  Fireworks model list options: account_id=fireworks, model_api_base_url=https://api.fireworks.ai")
    print("Examples:")
    print("  ciel-runtimectl provider-options deepseek max_output_tokens=8192 context_window=1048576")
    print("  ciel-runtimectl provider-options opencode-go endpoint:custom-model=chat")
    print("  ciel-runtimectl provider-options opencode ip_family=ipv6-preferred")
    print("  ciel-runtimectl provider-options fireworks account_id=fireworks model_api_base_url=https://api.fireworks.ai")
    print("  ciel-runtimectl provider-options nvidia-hosted max_output_tokens=4096 temperature=0.7 top_p=0.8 timeout=300000 rate_limit_rpm=40")
    print("  ciel-runtimectl provider-options vllm max_output_tokens=4096 context_window=65536 timeout=300000")
    print("  ciel-runtimectl provider-options self-hosted-nim native=true max_output_tokens=4096")


COMPAT_TOOL_NAME = "compat_echo"
COMPATIBILITY_TEST_HEADER = "x-ciel-runtime-compatibility-test"


def compatibility_tool_schema() -> dict[str, Any]:
    return {
        "name": COMPAT_TOOL_NAME,
        "description": "A minimal compatibility test tool. It echoes one required text argument.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    }


def compatibility_text_request(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": compat_max_tokens_for_model(model),
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "Compatibility text test. Reply with exactly OK and do not call tools.",
            }
        ],
    }


def compatibility_tool_request(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 128,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "Compatibility tool test. Use the compat_echo tool exactly once with text set to ping.",
            }
        ],
        "tools": [compatibility_tool_schema()],
        "tool_choice": {"type": "tool", "name": COMPAT_TOOL_NAME},
    }


def compatibility_tool_result_request(model: str, tool_use: dict[str, Any]) -> dict[str, Any]:
    tool_id = str(tool_use.get("id") or "toolu_compat_echo_1")
    tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {"text": "ping"}
    return {
        "model": model,
        "max_tokens": 64,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "Compatibility tool test. Use the compat_echo tool exactly once with text set to ping.",
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": COMPAT_TOOL_NAME,
                        "input": tool_input,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "pong",
                    },
                    {
                        "type": "text",
                        "text": "Now reply with FINAL_OK and do not call tools.",
                    },
                ],
            },
        ],
        "tools": [compatibility_tool_schema()],
    }


def response_content_blocks(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    content = data.get("content")
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def response_content_types(data: Any) -> list[str]:
    return [str(block.get("type", "?")) for block in response_content_blocks(data)]


def response_text_preview(data: Any) -> str:
    parts: list[str] = []
    for block in response_content_blocks(data):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"].strip())
    return " ".join(parts).strip()[:300]


def find_compat_tool_use(data: Any) -> tuple[dict[str, Any] | None, str]:
    for block in response_content_blocks(data):
        if block.get("type") != "tool_use":
            continue
        if block.get("name") != COMPAT_TOOL_NAME:
            return None, f"unexpected tool name {block.get('name')!r}"
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            return None, "tool input was not a JSON object"
        if tool_input.get("text") != "ping":
            return None, f"tool input text was {tool_input.get('text')!r}, expected 'ping'"
        if not block.get("id"):
            return None, "tool_use block did not include an id"
        return block, ""
    types = ", ".join(response_content_types(data)) or "none"
    preview = response_text_preview(data)
    suffix = f"; text={preview!r}" if preview else ""
    return None, f"no compat_echo tool_use block returned; content blocks: {types}{suffix}"


def summarize_compat_response(data: Any, label: str) -> list[str]:
    lines = [f"{label}: OK"]
    if isinstance(data, dict):
        stop = data.get("stop_reason")
        if stop:
            lines.append(f"Stop reason: {stop}")
        types = response_content_types(data)
        if types:
            lines.append("Content blocks: " + ", ".join(types[:6]))
        usage = data.get("usage")
        if isinstance(usage, dict):
            tokens = []
            if "input_tokens" in usage:
                tokens.append(f"in={usage['input_tokens']}")
            if "output_tokens" in usage:
                tokens.append(f"out={usage['output_tokens']}")
            if tokens:
                lines.append("Tokens: " + ", ".join(tokens))
    return lines


def compatibility_failure_diagnosis(provider: str, code: int | None, msg: str) -> str | None:
    lower = msg.lower()
    if "does not support tools" in lower:
        return "Diagnosis: selected model does not support tool calling, so it is not suitable for normal Claude Code use."
    return PROVIDER_COMPATIBILITY.resolve(provider).failure_diagnosis(code, msg)


def known_compatibility_tool_use_blocker(provider: str, model: str) -> str:
    normalized = strip_claude_context_suffix(str(model or "")).strip()
    return PROVIDER_COMPATIBILITY.resolve(provider).tool_use_blocker(normalized)


class CompatibilityApiKeyProbeError(Exception):
    def __init__(self, message: str, code: int | None = None, diagnosis: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.diagnosis = diagnosis


def compatibility_http_error_message(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="ignore")
    msg = raw.strip()
    error_type = ""
    try:
        err = json.loads(raw)
        if isinstance(err, dict):
            if isinstance(err.get("error"), dict):
                error_obj = err["error"]
                error_type = str(error_obj.get("type") or "").strip()
                msg = str(error_obj.get("message") or json.dumps(error_obj, ensure_ascii=False))
            elif err.get("message"):
                msg = str(err["message"])
                error_type = str(err.get("type") or "").strip()
    except Exception:
        pass
    if error_type and error_type not in msg:
        msg = f"{error_type}: {msg}"
    retry_after = first_header(exc.headers, ["Retry-After", "retry-after"])
    if retry_after:
        retry_after_text = retry_after.strip()
        retry_after_seconds = parse_retry_after_seconds(retry_after_text)
        if retry_after_seconds is not None:
            retry_after_display = format_duration_seconds(retry_after_seconds)
            if retry_after_text:
                if re.fullmatch(r"\d+(?:\.\d+)?", retry_after_text):
                    suffix = f"{retry_after_display} ({retry_after_text}s)"
                else:
                    suffix = f"{retry_after_display} ({retry_after_text})"
                msg = f"{msg} Retry-After: {suffix}"
            else:
                msg = f"{msg} Retry-After: {retry_after_display}"
        else:
            msg = f"{msg} Retry-After: {retry_after_text}"
    return msg


def provider_config_for_single_api_key(pcfg: dict[str, Any], key: str) -> dict[str, Any]:
    keyed = dict(pcfg)
    keyed["api_key"] = key
    keyed["api_keys"] = []
    return keyed


def compatibility_api_key_probe_request(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    request_body: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, str]]:
    body = normalize_thinking_for_non_anthropic_provider(provider, pcfg, request_body)
    body = normalize_tool_choice_for_provider(provider, pcfg, body)
    upstream_model = resolve_requested_model(provider, pcfg, model)
    headers = provider_headers(provider, pcfg)
    if provider in ("ollama", "ollama-cloud"):
        req_body = ollama_chat_request(upstream_model, body, pcfg, stream=False, provider=provider)
        return provider_endpoint(provider, pcfg, "ollama_chat"), req_body, headers
    if provider in OPENCODE_PROVIDER_NAMES:
        endpoint_kind = opencode_endpoint_kind(provider, upstream_model, pcfg)
        if endpoint_kind == "openai-chat":
            req_body = openai_compatible_chat_request(provider, upstream_model, body, pcfg, stream=False)
            return join_url(provider_upstream_request_base(provider, pcfg), "/v1/chat/completions"), req_body, headers
        if endpoint_kind != "anthropic-messages":
            raise CompatibilityApiKeyProbeError(
                f"model {upstream_model!r} uses unsupported endpoint family {endpoint_kind!r} for API-key probing"
            )
    if provider_openai_router_enabled(provider, pcfg):
        upstream_model = ncp_model_id_for_nvidia_hosted(upstream_model) if provider == "nvidia-hosted" else upstream_model
        req_body = openai_compatible_chat_request(provider, upstream_model, body, pcfg, stream=False)
        return provider_endpoint(provider, pcfg, "openai_chat"), req_body, headers
    body = cap_anthropic_body_for_provider(provider, pcfg, body)
    body = apply_provider_request_options(provider, pcfg, body)
    body = dict(body)
    body["model"] = upstream_model
    body = resolve_tool_model_references(provider, pcfg, body)
    base = native_anthropic_base_url(provider, pcfg) if provider_native_compat_enabled(provider, pcfg) else provider_upstream_request_base(provider, pcfg)
    return join_url(base, "/v1/messages"), body, headers

def run_compatibility_api_key_probes(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    request_body: dict[str, Any],
    timeout: float,
) -> list[str]:
    keys = provider_config_api_keys(provider, pcfg)
    if len(keys) <= 1:
        return []
    lines = [f"API key checks: running {len(keys)} configured keys"]
    for index, key in enumerate(keys, start=1):
        label = f"API key {index}/{len(keys)} ({mask_secret(key)})"
        keyed_pcfg = provider_config_for_single_api_key(pcfg, key)
        try:
            url, probe_body, probe_headers = compatibility_api_key_probe_request(provider, keyed_pcfg, model, request_body)
            post_json(url, probe_body, headers=probe_headers, timeout=timeout, provider=provider, pcfg=keyed_pcfg)
        except CompatibilityApiKeyProbeError:
            raise
        except urllib.error.HTTPError as exc:
            msg = compatibility_http_error_message(exc)
            diagnosis = compatibility_failure_diagnosis(provider, exc.code, msg) or ""
            raise CompatibilityApiKeyProbeError(f"{label}: {msg}", exc.code, diagnosis) from exc
        except TimeoutError as exc:
            raise CompatibilityApiKeyProbeError(f"{label}: timed out before the {timeout:g}s API-key probe timeout") from exc
        except Exception as exc:
            raise CompatibilityApiKeyProbeError(f"{label}: {type(exc).__name__}: {exc}") from exc
        lines.append(f"{label}: OK")
    return lines


def vllm_tool_parser_hint(model: str) -> str | None:
    normalized = model.lower()
    if "qwen3-coder" in normalized or "qwen3_coder" in normalized:
        return "vLLM hint: Qwen3-Coder models should be served with --enable-auto-tool-choice --tool-call-parser qwen3_xml."
    if "qwen2.5" in normalized or "qwen2_5" in normalized or "qwq" in normalized:
        return "vLLM hint: Qwen2.5/QwQ tool templates usually use --enable-auto-tool-choice --tool-call-parser hermes."
    if "glm-4.7" in normalized or "glm4.7" in normalized:
        return "vLLM hint: GLM-4.7 models should be served with --enable-auto-tool-choice --tool-call-parser glm47."
    if "glm-4.5" in normalized or "glm4.5" in normalized or "glm-4.6" in normalized or "glm4.6" in normalized:
        return "vLLM hint: GLM-4.5/4.6 models should be served with --enable-auto-tool-choice --tool-call-parser glm45."
    if "deepseek-v3.1" in normalized:
        return "vLLM hint: DeepSeek-V3.1 models should be served with --enable-auto-tool-choice --tool-call-parser deepseek_v31."
    if "deepseek-v3" in normalized or "deepseek-r1" in normalized:
        return "vLLM hint: DeepSeek-V3/R1 models require the matching DeepSeek tool parser and chat template from vLLM examples."
    if "llama-3" in normalized or "llama3" in normalized:
        return "vLLM hint: Llama 3.x models usually need --enable-auto-tool-choice --tool-call-parser llama3_json and the matching tool chat template."
    if "hermes" in normalized:
        return "vLLM hint: Hermes models should be served with --enable-auto-tool-choice --tool-call-parser hermes."
    if "qwen3" in normalized or "qwen-3" in normalized:
        return (
            "vLLM hint: this looks like a Qwen3-family model. Verify its model card/tool format; "
            "Qwen3-Coder uses qwen3_xml, while older Hermes-style Qwen templates use hermes."
        )
    return None


def compatibility_runtime_lines(provider: str, pcfg: dict[str, Any], native: bool) -> list[str]:
    policy = PROVIDER_COMPATIBILITY.resolve(provider)
    if not policy.exposes_runtime_info:
        return []
    lines: list[str] = []
    info = upstream_model_runtime_info(provider, pcfg, timeout=4.0)
    configured_context = positive_int(pcfg.get("context_window"))
    configured_output = positive_int(pcfg.get("max_output_tokens"))
    if info:
        lines.append(f"Runtime models URL: {info.get('models_url')}")
        if info.get("runtime_model"):
            lines.append(f"Runtime model id: {info.get('runtime_model')}")
        runtime_limit = positive_int(info.get("max_model_len"))
        if runtime_limit:
            lines.append(f"Runtime max_model_len: {runtime_limit}")
        else:
            lines.append("Runtime max_model_len: not reported by /v1/models")
        lines.extend(policy.runtime_metadata(info))
    else:
        runtime_limit = None
        lines.append("Runtime max_model_len: unavailable (/v1/models did not return model metadata)")
    if configured_context:
        lines.append(f"Configured context_window: {configured_context}")
    if configured_output:
        lines.append(f"Configured max_output_tokens: {configured_output}")
    if runtime_limit and configured_context and configured_context != runtime_limit:
        lines.append(f"Context warning: configured context_window {configured_context} differs from runtime max_model_len {runtime_limit}.")
    if runtime_limit and configured_output and configured_output >= runtime_limit:
        lines.append("Context warning: max_output_tokens is greater than or equal to the full runtime context length.")
    if native:
        lines.append("Runtime mode note: native mode sends Claude Code requests directly; ciel-runtime cannot shrink max_tokens per request.")
    else:
        lines.append("Runtime mode note: router mode can cap max_tokens based on configured context_window.")
    return lines


def set_compatibility_cache(
    cfg: dict[str, Any],
    provider: str,
    model: str,
    ok: bool,
    code: int | None = None,
    message: str = "",
    diagnosis: str = "",
) -> None:
    cache = cfg.setdefault("compatibility_cache", {})
    if not isinstance(cache, dict):
        cache = {}
        cfg["compatibility_cache"] = cache
    provider_cache = cache.setdefault(provider, {})
    if not isinstance(provider_cache, dict):
        provider_cache = {}
        cache[provider] = provider_cache
    provider_cache[model] = {
        "ok": ok,
        "code": code,
        "message": message[:500],
        "diagnosis": diagnosis[:500],
        "tested_at": int(time.time()),
    }
    save_config(cfg)


def compatibility_test_services() -> CompatibilityTestServices:
    return CompatibilityTestServices(
        constants=CompatibilityTestConstants(
            api_key_probe_error=CompatibilityApiKeyProbeError,
            compatibility_test_header=COMPATIBILITY_TEST_HEADER,
            lm_studio_min_context=LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT,
            opencode_provider_names=OPENCODE_PROVIDER_NAMES,
            router_base=ROUTER_BASE,
        ),
        config=CompatibilityTestConfig(
            current_alias=current_alias,
            current_upstream_model_id=current_upstream_model_id,
            ensure_current_model=ensure_current_model_from_provider_list,
            get_current_provider=get_current_provider,
            launch_model_id=launch_model_id,
            load_config=load_config,
            normalize_model_id=normalize_model_id,
            positive_int=positive_int,
            save_config=save_config,
            upstream_model_runtime_info=upstream_model_runtime_info,
        ),
        mode=CompatibilityTestMode(
            ensure_lm_studio_model_loaded=ensure_lm_studio_model_loaded_for_context,
            lm_studio_native_enabled=lm_studio_native_compat_enabled,
            native_anthropic_base_url=native_anthropic_base_url,
            nim_native_enabled=nim_native_compat_enabled,
            nvidia_native_enabled=nvidia_hosted_native_compat_enabled,
            ollama_native_enabled=ollama_native_compat_enabled,
            provider_native_enabled=provider_native_compat_enabled,
            upstream_api_model_id=upstream_api_model_id,
            vllm_native_enabled=vllm_native_compat_enabled,
            vllm_tool_parser_hint=vllm_tool_parser_hint,
        ),
        request=CompatibilityTestRequest(
            compatibility_endpoint_probe_lines=compatibility_endpoint_probe_lines,
            compatibility_failure_diagnosis=compatibility_failure_diagnosis,
            compatibility_http_error_message=compatibility_http_error_message,
            post_json=post_json,
            provider_headers=provider_headers,
            provider_ip_family_probe_lines=provider_ip_family_probe_lines,
            run_api_key_probes=run_compatibility_api_key_probes,
            start_router=start_router_if_needed,
            stop_router=stop_router_processes,
        ),
        protocol=CompatibilityTestProtocol(
            compatibility_text_request=compatibility_text_request,
            compatibility_tool_request=compatibility_tool_request,
            compatibility_tool_result_request=compatibility_tool_result_request,
            find_compat_tool_use=find_compat_tool_use,
            known_tool_use_blocker=known_compatibility_tool_use_blocker,
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            normalize_tool_choice=normalize_tool_choice_for_provider,
            ollama_chat_request=ollama_chat_request,
            resolve_requested_model=resolve_requested_model,
            response_text_preview=response_text_preview,
        ),
        output=CompatibilityTestOutput(
            compatibility_runtime_lines=compatibility_runtime_lines,
            join_url=join_url,
            set_compatibility_cache=set_compatibility_cache,
            summarize_compat_response=summarize_compat_response,
        ),
    )


def _cmd_test(args: argparse.Namespace) -> None:
    run_provider_compatibility_test(args, services=compatibility_test_services())

def cmd_test(args: argparse.Namespace) -> None:
    try:
        _cmd_test(args)
    except SystemExit:
        raise
    except Exception as exc:
        print("Compatibility: FAIL")
        print(f"Reason: {type(exc).__name__}: {exc}")
        raise SystemExit(1)


def claude_code_output_token_limit(provider: str, pcfg: dict[str, Any]) -> int | None:
    configured = positive_int(pcfg.get("max_output_tokens"))
    if configured:
        return cap_output_tokens_to_context_ratio(provider, pcfg, configured)
    if provider in ("ollama", "ollama-cloud"):
        opts = ollama_extra_options(pcfg)
        configured = positive_int(opts.get("num_predict"))
        if configured:
            return cap_output_tokens_to_context_ratio(provider, pcfg, configured)
    return None


def claude_code_auto_compact_window(provider: str, pcfg: dict[str, Any]) -> int | None:
    configured = positive_int(pcfg.get("auto_compact_window"))
    limit = context_limit_for_status(provider, pcfg)
    if configured:
        return min(configured, limit) if limit else configured
    if limit:
        return limit
    return None


def claude_code_model_claims_one_million_context(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    *,
    include_current: bool = True,
) -> bool:
    candidates = [str(model or "")]
    if include_current:
        candidates.extend([
            str(pcfg.get("current_model") or ""),
            str(current_upstream_model_id(provider, pcfg) or ""),
        ])
    explicit_unknown_one_million = False
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if not candidate:
            continue
        if candidate.startswith(f"ciel-runtime-{provider}-"):
            resolved = unslug_provider_alias(provider, candidate, model_map_for(provider, pcfg, fetch=False))
            if not resolved:
                continue
            candidate = resolved
        hint = model_context_hint_from_model_id(strip_claude_context_suffix(candidate))
        if hint is None and provider == "anthropic":
            hint = positive_int(anthropic_model_limit_hints(candidate).get("context_window"))
        if hint is not None:
            if hint >= 1_000_000:
                return True
            continue
        if "[1m]" in candidate.lower():
            explicit_unknown_one_million = True
    if explicit_unknown_one_million:
        return True
    if include_current:
        limit = context_limit_for_status(provider, pcfg)
        return bool(limit and limit >= 1_000_000)
    return False


def claude_code_context_model_alias(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    upstream_model: str | None = None,
) -> str:
    model = strip_claude_context_suffix(model)
    # Claude Code treats [1m] as a real one-million-context model marker. Do
    # not use it as a generic long-context hint for 256K/512K routed models.
    probe_model = upstream_model if upstream_model is not None else model
    include_current = upstream_model is None
    if (
        claude_code_model_claims_one_million_context(provider, pcfg, probe_model, include_current=include_current)
        and "[1m]" not in model.lower()
    ):
        return f"{model}[1m]"
    return model


def _model_id_matches_claude_family(model_id: str, family: str) -> bool:
    normalized = strip_claude_context_suffix(model_id).strip().lower()
    family = family.strip().lower()
    if not normalized or family not in ("opus", "sonnet", "haiku"):
        return False
    return bool(re.search(rf"(?:^|[-_./]){re.escape(family)}(?:[-_./]|$)", normalized))


def claude_code_default_model_aliases(provider: str, pcfg: dict[str, Any], current_model_alias: str) -> dict[str, str]:
    current_upstream = current_upstream_model_id(provider, pcfg)
    candidates = cached_or_configured_model_ids(provider, pcfg)
    if current_upstream and current_upstream not in candidates:
        candidates.insert(0, current_upstream)
    out: dict[str, str] = {}
    for family, key in (
        ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
        ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
        ("sonnet", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ):
        selected = ""
        selected_from_config = False
        configured_family_model = str(pcfg.get(f"{family}_model") or "").strip() if provider == "zai" else ""
        if configured_family_model:
            selected = normalize_model_id(provider, configured_family_model)
            selected_from_config = bool(selected)
        if not selected and _model_id_matches_claude_family(current_upstream, family):
            selected = current_upstream
        if not selected:
            for model_id in candidates:
                if _model_id_matches_claude_family(model_id, family):
                    selected = model_id
                    break
        alias = alias_for(provider, selected) if selected else current_model_alias
        if selected_from_config or provider == "anthropic":
            out[key] = claude_code_context_model_alias(provider, pcfg, alias, selected)
        else:
            out[key] = claude_code_context_model_alias(provider, pcfg, alias)
    return out


def apply_common_claude_env(provider: str, pcfg: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    # Claude Code's AI-generated terminal/session title can be persisted as
    # ai-title records and, in some resume/queued-command states, visually bleed
    # into the prompt area. Disable that side path for ciel-runtime launches.
    env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    output_tokens = claude_code_output_token_limit(provider, pcfg)
    if output_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(output_tokens)
    compact_window = claude_code_auto_compact_window(provider, pcfg)
    if compact_window:
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(compact_window)
    advisor_model = str(pcfg.get("advisor_model") or "").strip()
    if advisor_model:
        env["CIEL_RUNTIME_ADVISOR_MODEL"] = advisor_model
    claude_model = str(env.get("ANTHROPIC_MODEL") or env.get("CIEL_RUNTIME_MODEL_ALIAS") or "").strip()
    capability_string = claude_code_capability_string(provider, pcfg, current_upstream_model_id(provider, pcfg))
    if claude_model and capability_string:
        env["ANTHROPIC_CUSTOM_MODEL_OPTION"] = claude_model
        env["ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES"] = capability_string
    for key in (
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
    ):
        model_alias = str(env.get(key) or "").strip()
        if not model_alias:
            continue
        upstream_model = resolve_requested_model(provider, pcfg, model_alias)
        default_caps = claude_code_capability_string(provider, pcfg, upstream_model)
        if default_caps:
            env[f"{key}_SUPPORTS"] = default_caps
            env[f"{key}_SUPPORTED_CAPABILITIES"] = default_caps
    if claude_code_workflows_enabled(provider, pcfg):
        env.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)
    return env


def env_vars(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """Build the environment overrides ciel-runtime adds when spawning `claude`.

    Claude Native mode contract: when the selected provider is native
    Anthropic, ciel-runtime MUST NOT inject anything that would alter Claude
    Code's default model selection, backend URL, advisor flow, output-token
    cap, auto-compact window, or any other Claude-Code-visible behavior.
    The only override is an optional ``ANTHROPIC_API_KEY`` if the user has
    one stored in ciel-runtime's config (Claude Code's OAuth credentials win
    otherwise). ``CIEL_RUNTIME_PROVIDER=anthropic`` is set purely as a marker
    for ciel-runtime's own helpers (statusline, hooks) so they can self-suppress.
    """
    cfg = cfg or load_config()
    provider, pcfg = get_current_provider(cfg)
    if direct_native_anthropic_enabled(provider, pcfg):
        env = {"CIEL_RUNTIME_PROVIDER": provider}
        key = provider_primary_api_key(provider, pcfg)
        if meaningful_key(key):
            env["ANTHROPIC_API_KEY"] = str(key)
        return env
    alias = current_alias(cfg)
    claude_model = claude_code_context_model_alias(provider, pcfg, alias)
    auth_token = claude_code_router_auth_token(provider, pcfg)
    default_models = claude_code_default_model_aliases(provider, pcfg, claude_model)
    env = {
        "CIEL_RUNTIME_PROVIDER": provider,
        "ANTHROPIC_BASE_URL": ROUTER_BASE,
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        "ANTHROPIC_MODEL": claude_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": default_models["ANTHROPIC_DEFAULT_HAIKU_MODEL"],
        "ANTHROPIC_DEFAULT_OPUS_MODEL": default_models["ANTHROPIC_DEFAULT_OPUS_MODEL"],
        "ANTHROPIC_DEFAULT_SONNET_MODEL": default_models["ANTHROPIC_DEFAULT_SONNET_MODEL"],
        "CLAUDE_CODE_SUBAGENT_MODEL": claude_model,
        "CIEL_RUNTIME_MODEL_ALIAS": claude_model,
        "CIEL_RUNTIME_BYPASS_PERMISSIONS": "1",
    }
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    return apply_common_claude_env(provider, pcfg, env)


def claude_code_router_auth_token(provider: str, pcfg: dict[str, Any]) -> str:
    if provider == "anthropic":
        return ""
    key = provider_primary_api_key(provider, pcfg)
    if meaningful_key(key):
        return key
    if provider == "ollama":
        return "ollama"
    return "not-used"


def claude_code_runtime_settings(provider: str, pcfg: dict[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if claude_code_ultracode_enabled(provider, pcfg):
        settings["ultracode"] = True
    return settings


def append_claude_code_runtime_settings_args(extra_args: list[str], passthrough: list[str], provider: str, pcfg: dict[str, Any]) -> None:
    settings = claude_code_runtime_settings(provider, pcfg)
    if not settings:
        return
    if has_passthrough_option(passthrough, "--settings"):
        router_log("WARN", "claude_code_runtime_settings_skipped reason=passthrough_settings_present")
        return
    extra_args.extend(["--settings", json.dumps(settings, separators=(",", ":"))])


def cmd_env(_: argparse.Namespace) -> None:
    env = env_vars()
    for optional in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"):
        if optional in env:
            print(f"export {optional}={json.dumps(env[optional])}")
        else:
            print(f"unset {optional}")
    if "ANTHROPIC_AUTH_TOKEN" in env:
        print(f"export ANTHROPIC_AUTH_TOKEN={json.dumps(env['ANTHROPIC_AUTH_TOKEN'])}")
    else:
        print('unset ANTHROPIC_AUTH_TOKEN')
    for key in (
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
        "CLAUDE_CODE_ATTRIBUTION_HEADER",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_CODE_EFFORT_LEVEL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_CUSTOM_MODEL_OPTION",
        "ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTS",
        "ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CIEL_RUNTIME_MODEL_ALIAS",
        "CIEL_RUNTIME_PROVIDER",
    ):
        if key in env:
            print(f"export {key}={json.dumps(env[key])}")
        else:
            print(f"unset {key}")


def cmd_stop(_: argparse.Namespace) -> None:
    stopped = stop_router_processes(quiet=True)
    stopped = stop_ncp_proxy(quiet=True) or stopped
    print("ciel-runtime managed services stopped" if stopped else "ciel-runtime managed services were not running")


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            out = proc.stdout or ""
            return str(pid) in out and "No tasks" not in out and "INFO:" not in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError):
        return False


def register_router_client(pid: int | None = None) -> Path:
    client_pid = int(pid or os.getpid())
    ROUTER_CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = ROUTER_CLIENTS_DIR / f"{client_pid}.json"
    payload = {
        "pid": client_pid,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "router_port": ROUTER_PORT,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    router_log("INFO", f"router_client_registered pid={client_pid} path={path}")
    return path


def release_router_client(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
        router_log("INFO", f"router_client_released path={path}")
    except FileNotFoundError:
        pass
    except Exception as exc:
        router_log("WARN", f"router_client_release_failed path={path} error={type(exc).__name__}: {exc}")


def router_managed_idle_exit_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_ROUTER_IDLE_EXIT_SECONDS", "90")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 90.0
    return max(0.0, value)


def managed_router_stop_reason(started_at: float, owner_pid: int, idle_seconds: float) -> str | None:
    if os.environ.get("CIEL_RUNTIME_MANAGED_ROUTER") != "1":
        return None
    active = active_router_client_pids()
    if active:
        return None
    if owner_pid > 0 and not pid_is_running(owner_pid):
        return "owner_dead_no_clients"
    if idle_seconds > 0 and time.time() - started_at >= idle_seconds:
        return "idle_no_clients"
    return None


def start_managed_router_lifetime_watchdog(server: ThreadingHTTPServer) -> None:
    if os.environ.get("CIEL_RUNTIME_MANAGED_ROUTER") != "1":
        return
    try:
        owner_pid = int(os.environ.get("CIEL_RUNTIME_ROUTER_OWNER_PID") or "0")
    except ValueError:
        owner_pid = 0
    idle_seconds = router_managed_idle_exit_seconds()
    started_at = time.time()

    def watch() -> None:
        interval = min(5.0, max(0.5, idle_seconds / 3.0 if idle_seconds else 5.0))
        while True:
            time.sleep(interval)
            reason = managed_router_stop_reason(started_at, owner_pid, idle_seconds)
            if not reason:
                continue
            router_log("INFO", f"router_managed_lifetime_shutdown reason={reason} owner_pid={owner_pid or '-'}")
            try:
                server.shutdown()
            except Exception as exc:
                router_log("ERROR", f"router_managed_lifetime_shutdown_failed error={type(exc).__name__}: {exc}")
            return

    thread = threading.Thread(target=watch, daemon=True, name="ca-router-lifetime-watchdog")
    thread.start()


def active_router_client_pids() -> list[int]:
    if not ROUTER_CLIENTS_DIR.exists():
        return []
    active: list[int] = []
    for path in ROUTER_CLIENTS_DIR.glob("*.json"):
        pid = 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid") or path.stem)
        except Exception:
            try:
                pid = int(path.stem)
            except Exception:
                pid = 0
        if pid_is_running(pid):
            active.append(pid)
            continue
        try:
            path.unlink()
            router_log("INFO", f"router_client_stale_removed pid={pid or '-'} path={path}")
        except Exception:
            pass
    return sorted(set(active))


def stop_router_if_no_active_clients(reason: str, quiet: bool = True) -> bool:
    active = active_router_client_pids()
    if active:
        router_log("INFO", f"router_lifetime_keep_alive reason={reason} active_clients={','.join(map(str, active))}")
        return False
    try:
        stopped = stop_router_with_guarantee(reason, quiet=quiet)
        router_log("INFO", f"router_lifetime_stopped reason={reason} stopped={stopped}")
        return stopped
    except Exception as exc:
        router_log("ERROR", f"router_lifetime_stop_failed reason={reason} error={type(exc).__name__}: {exc}")
        return False


def router_client_supervisor_interval_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_ROUTER_SUPERVISOR_SECONDS", "0.5")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.5
    return max(0.5, min(30.0, value))


def ensure_managed_router_running_for_client() -> bool:
    health = router_health()
    if router_health_matches_current(health):
        return True
    if health is not None:
        router_log("WARN", f"router_lifetime_health_mismatch_active_client {router_health_summary(health)}")
        try:
            return bool(start_router_if_needed(replace_active_clients=False))
        except Exception as exc:
            router_log("ERROR", f"router_lifetime_restart_failed error={type(exc).__name__}: {exc}")
            return False
    for attempt in range(2):
        time.sleep(0.5)
        health = router_health()
        if router_health_matches_current(health):
            router_log("INFO", f"router_lifetime_keep_alive reason=transient_health_miss retry={attempt + 1} {router_health_summary(health)}")
            return True
    router_log("WARN", f"router_lifetime_restart reason=router_down_active_client base={ROUTER_BASE}")
    try:
        return bool(start_router_if_needed(replace_active_clients=False))
    except Exception as exc:
        router_log("ERROR", f"router_lifetime_restart_failed error={type(exc).__name__}: {exc}")
        return False


def start_router_client_supervisor(stop_event: threading.Event) -> threading.Thread:
    def watch() -> None:
        interval = router_client_supervisor_interval_seconds()
        while not stop_event.wait(interval):
            ensure_managed_router_running_for_client()

    thread = threading.Thread(target=watch, daemon=True, name="ca-router-client-supervisor")
    thread.start()
    return thread


def file_size_or_zero(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _read_text_file_from_offset(path: Path, offset: int = 0, max_bytes: int = 262_144) -> str:
    try:
        size = path.stat().st_size
        start = max(0, min(int(offset or 0), int(size)))
        if size - start > max_bytes:
            start = max(0, size - max_bytes)
        with path.open("rb") as f:
            f.seek(start)
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return ""


def router_recent_diagnostic_lines(since_offset: int = 0, limit: int = 8) -> list[str]:
    text = _read_text_file_from_offset(LOG_PATH, since_offset)
    if not text:
        return []
    markers = (
        "[ERROR]",
        "[WARN]",
        "ConnectionRefused",
        "connection refused",
        "URLError",
        "router_lifetime",
        "router_spawned",
        "router_check_state",
        "claude_exit",
        "upstream_",
        "ollama_",
        "anthropic_sse_forward_error",
    )
    lines = [line.strip() for line in text.splitlines() if line.strip() and any(marker in line for marker in markers)]
    return lines[-max(1, int(limit or 8)):]


def provider_upstream_summary_for_launch(provider: str, pcfg: dict[str, Any]) -> str:
    base = str(pcfg.get("base_url") or "").rstrip("/") or "-"
    try:
        if provider in ("ollama", "ollama-cloud"):
            return f"upstream={join_url(base, '/api/chat')}"
        if provider_openai_router_enabled(provider, pcfg) or codex_openai_router_enabled(provider, pcfg):
            return f"upstream={join_url(provider_upstream_request_base(provider, pcfg), '/v1/chat/completions')}"
        native_base = native_anthropic_base_url(provider, pcfg) if provider_native_compat_enabled(provider, pcfg) else provider_upstream_request_base(provider, pcfg)
        return f"upstream={join_url(native_base, '/v1/messages')}"
    except Exception:
        return f"upstream_base={base}"


def should_print_routed_claude_diagnostics(rc: int, recent_lines: list[str]) -> bool:
    if rc != 0:
        return True
    text = "\n".join(recent_lines).lower()
    return any(
        marker in text
        for marker in (
            "connectionrefused",
            "connection refused",
            "urlerror",
            "router_lifetime_restart_failed",
            "router_lifetime_health_mismatch",
            "anthropic_sse_forward_error",
        )
    )


def print_routed_claude_exit_diagnostics(
    rc: int,
    provider: str,
    pcfg: dict[str, Any],
    *,
    log_offset: int = 0,
) -> None:
    recent = router_recent_diagnostic_lines(log_offset)
    if not should_print_routed_claude_diagnostics(rc, recent):
        return
    health = router_health()
    lines = [
        f"Ciel Runtime diagnostic: Claude Code exited with code {rc} while routed through {ROUTER_BASE}.",
        f"Router: {router_health_summary(health)}",
        f"Provider: {provider} {provider_upstream_summary_for_launch(provider, pcfg)}",
        f"Router log: {LOG_PATH}",
    ]
    if recent:
        lines.append("Recent router events:")
        lines.extend(f"  {line}" for line in recent)
    for line in lines:
        print(line, flush=True)
    router_log("WARN", "claude_routed_exit_diagnostic " + " | ".join(lines[:4]))


def run_with_router_lifetime(runner: Callable[[], int], manage_router: bool) -> int:
    client_path: Path | None = None
    supervisor_stop: threading.Event | None = None
    if manage_router:
        try:
            client_path = register_router_client()
            supervisor_stop = threading.Event()
            start_router_client_supervisor(supervisor_stop)
        except Exception as exc:
            router_log("WARN", f"router_client_register_failed error={type(exc).__name__}: {exc}")
    try:
        return runner()
    finally:
        if supervisor_stop is not None:
            supervisor_stop.set()
        if manage_router:
            release_router_client(client_path)
            stop_router_if_no_active_clients("claude_exit", quiet=True)


def terminate_pid(pid: int, label: str, quiet: bool = False) -> bool:
    if not pid_is_running(pid):
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        else:
            os.kill(pid, signal.SIGTERM)
            deadline = time.time() + 4
            while time.time() < deadline and pid_is_running(pid):
                time.sleep(0.1)
            if pid_is_running(pid):
                os.kill(pid, signal.SIGKILL)
        if not quiet:
            print(f"Stopped existing {label} session (pid {pid}).")
        return True
    except Exception as exc:
        if not quiet:
            print(f"Could not stop existing {label} session ({type(exc).__name__}).")
        return False


def descendant_pids(pid: int) -> list[int]:
    if pid <= 0 or os.name == "nt":
        return []
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return []
    children: dict[int, list[int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        try:
            child = int(parts[0])
            parent = int(parts[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)
    out: list[int] = []
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        if child in out:
            continue
        out.append(child)
        stack.extend(children.get(child, []))
    return out


def parent_pid_and_command(pid: int) -> tuple[int, str] | None:
    if pid <= 0 or os.name == "nt":
        return None
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid=,command="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    line = proc.stdout.strip()
    if not line:
        return None
    parts = line.split(maxsplit=1)
    if not parts:
        return None
    try:
        parent = int(parts[0])
    except ValueError:
        return None
    command = parts[1] if len(parts) > 1 else ""
    return parent, command


def ciel_runtime_client_wrapper_parent_pids(pid: int) -> list[int]:
    wrappers: list[int] = []
    current = pid
    protected = {os.getpid(), os.getppid()}
    for _ in range(4):
        parent_info = parent_pid_and_command(current)
        if parent_info is None:
            break
        parent, command = parent_info
        if parent <= 0 or parent in protected:
            break
        if "ciel-runtime" not in command and "ciel_runtime.py" not in command:
            break
        if " serve" in command or " mcp-proxy" in command:
            break
        wrappers.append(parent)
        current = parent
    return wrappers


def terminate_pid_tree(pid: int, label: str, quiet: bool = False) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return terminate_pid(pid, label, quiet=quiet)
    protected = {os.getpid(), os.getppid()}
    targets = [p for p in [pid, *descendant_pids(pid)] if p > 0 and p not in protected]
    if not targets:
        return False
    stopped = False
    for target in targets:
        try:
            if pid_is_running(target):
                os.kill(target, signal.SIGTERM)
                stopped = True
        except Exception:
            pass
    deadline = time.time() + 4
    while time.time() < deadline:
        if not any(pid_is_running(target) for target in targets):
            break
        time.sleep(0.1)
    for target in targets:
        if pid_is_running(target):
            try:
                os.kill(target, signal.SIGKILL)
                stopped = True
            except Exception:
                pass
    if stopped and not quiet:
        print(f"Stopped existing {label} session(s): {', '.join(map(str, targets))}.")
    return stopped


def terminate_active_router_clients(reason: str, active_clients: list[int] | None = None, quiet: bool = True) -> bool:
    clients = active_clients if active_clients is not None else active_router_client_pids()
    stopped = False
    for pid in sorted(set(int(p) for p in clients if int(p or 0) > 0)):
        if pid in (os.getpid(), os.getppid()):
            continue
        wrapper_roots = ciel_runtime_client_wrapper_parent_pids(pid)
        root = wrapper_roots[-1] if wrapper_roots else pid
        if terminate_pid_tree(root, "previous ciel-runtime client", quiet=quiet):
            stopped = True
        try:
            (ROUTER_CLIENTS_DIR / f"{pid}.json").unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    router_log(
        "INFO",
        f"router_active_clients_terminated reason={reason} clients={','.join(map(str, clients)) or '-'} stopped={str(stopped).lower()}",
    )
    return stopped


def terminate_existing_router_clients_for_launch(reason: str, quiet: bool = True) -> bool:
    active_clients = active_router_client_pids()
    if not active_clients:
        return False
    router_log(
        "WARN",
        f"router_prelaunch_terminate_existing_clients reason={reason} active_clients={','.join(map(str, active_clients))}",
    )
    return terminate_active_router_clients(reason, active_clients, quiet=quiet)


def codex_process_record_path(kind: str = "client") -> Path:
    safe_kind = _safe_segment(kind, "client")
    return CODEX_PROCESS_DIR / f"{os.getpid()}-{safe_kind}.json"


def process_inspection_services() -> ProcessInspectionServices:
    return ProcessInspectionServices(
        run=subprocess.run,
        read_bytes=lambda path: path.read_bytes(),
        readlink=lambda path: os.readlink(path),
        username=getpass.getuser,
        log=router_log,
    )


def codex_process_lifecycle() -> CodexProcessLifecycle:
    return CodexProcessLifecycle(
        CodexProcessRepository(CODEX_PROCESS_DIR, router_log),
        CodexProcessPorts(
            pid_running=pid_is_running,
            command_line=_process_command_line,
            managed_process=_looks_like_ciel_managed_codex_process,
            terminate_tree=lambda pid, label, quiet: terminate_pid_tree(
                pid, label, quiet=quiet
            ),
            process_rows=lambda: posix_process_rows(process_inspection_services()),
            process_cwd=_posix_process_cwd,
            parent_info=parent_pid_and_command,
            log=router_log,
            current_pid=os.getpid,
            parent_pid=os.getppid,
        ),
    )


def _process_command_line(pid: int) -> str:
    return inspect_process_command_line(pid, process_inspection_services(), platform_name=os.name)


def _process_environ_contains(pid: int, key: str, value: str | None = None) -> bool:
    return inspect_process_environ_contains(
        pid,
        key,
        value,
        process_inspection_services(),
        platform_name=os.name,
    )


def _looks_like_ciel_managed_codex_process(pid: int, command: str | None = None) -> bool:
    return project_managed_codex_process(
        pid,
        command,
        managed_environment=_process_environ_contains,
        command_line=_process_command_line,
    )


def _write_codex_child_process_record(path: Path | None, pid: int, cmd: list[str], cwd: Path | None = None) -> None:
    CodexProcessRepository(CODEX_PROCESS_DIR, router_log).write(path, pid, cmd, cwd)


def _release_codex_child_process_record(path: Path | None, pid: int | None = None) -> None:
    CodexProcessRepository(CODEX_PROCESS_DIR, router_log).release(path, pid)


def _terminate_recorded_child_process(proc: Any, label: str) -> None:
    terminate_project_recorded_child(
        proc,
        label,
        terminate_tree=lambda pid, current_label, quiet: terminate_pid_tree(
            pid, current_label, quiet=quiet
        ),
        log=router_log,
    )


def terminate_tracked_codex_processes(reason: str, quiet: bool = True) -> bool:
    return codex_process_lifecycle().terminate_tracked(reason, quiet)


def _current_process_ancestor_pids(limit: int = 12) -> set[int]:
    return codex_process_lifecycle().ancestor_pids(os.name, limit)


def _posix_process_cwd(pid: int) -> Path | None:
    return inspect_process_cwd(pid, process_inspection_services(), platform_name=os.name)


def _untracked_codex_process_pids_for_cwd(cwd: Path | None = None) -> list[int]:
    return codex_process_lifecycle().untracked_pids(
        cwd,
        platform_name=os.name,
        enabled=env_bool(os.environ.get("CIEL_RUNTIME_CODEX_CLEAN_UNTRACKED"), True),
    )


def terminate_untracked_codex_processes_for_launch(reason: str, cwd: Path | None = None, quiet: bool = True) -> bool:
    return codex_process_lifecycle().terminate_untracked(
        reason,
        cwd,
        quiet,
        platform_name=os.name,
        enabled=env_bool(os.environ.get("CIEL_RUNTIME_CODEX_CLEAN_UNTRACKED"), True),
    )


def terminate_existing_codex_processes_for_launch(reason: str, cwd: Path | None = None, quiet: bool = True) -> bool:
    stopped = terminate_tracked_codex_processes(reason, quiet=quiet)
    stopped = terminate_untracked_codex_processes_for_launch(reason, cwd=cwd, quiet=quiet) or stopped
    return stopped


def router_process_config() -> RouterProcessConfig:
    return RouterProcessConfig(PID_PATH, ROUTER_PORT, ROUTER_BASE, CONFIG_DIR)


def router_process_state_ports() -> RouterStatePorts:
    return RouterStatePorts(
        health=router_health,
        foreign_config=router_health_has_foreign_config,
        current_config=router_health_config_matches_current,
        log=router_log,
    )


def router_termination_ports() -> RouterTerminationPorts:
    return RouterTerminationPorts(
        terminate_pid=lambda pid, label, quiet: terminate_pid(pid, label, quiet=quiet),
        terminate_pid_file=lambda path, label, quiet: terminate_pid_file(path, label, quiet=quiet),
        terminate_health=lambda health, quiet: terminate_router_health_pid(health, quiet=quiet),
        stop_processes=lambda quiet: stop_router_processes(quiet=quiet),
        listener_pids=router_port_listener_pids,
    )


def terminate_pid_file(path: Path, label: str, quiet: bool = False) -> bool:
    return terminate_project_pid_file(
        path,
        label,
        quiet,
        terminate_pid=lambda pid, current_label, current_quiet: terminate_pid(
            pid, current_label, quiet=current_quiet
        ),
        pid_is_running=pid_is_running,
    )








def posix_pids_on_port(port: int) -> list[int]:
    return project_posix_pids_on_port(port, linux_procfs_pids_on_port)


def terminate_posix_port(port: int, label: str, quiet: bool = False) -> bool:
    stopped = False
    pids = posix_pids_on_port(port)
    for pid in pids:
        stopped = terminate_pid(pid, label, quiet=True) or stopped
    if stopped and not quiet:
        print(f"Stopped existing {label} listener(s): {', '.join(map(str, pids))}.")
    return stopped


def router_port_listener_pids() -> list[int]:
    if os.name == "nt":
        return windows_pids_on_port(ROUTER_PORT)
    return posix_pids_on_port(ROUTER_PORT)


def terminate_router_health_pid(health: dict[str, Any] | None, quiet: bool = True) -> bool:
    return terminate_project_router_health_pid(
        health,
        quiet,
        config=router_process_config(),
        state=router_process_state_ports(),
        terminate_pid=lambda pid, label, current_quiet: terminate_pid(
            pid, label, quiet=current_quiet
        ),
        protected_pids=(os.getpid(), os.getppid()),
    )


def ensure_router_port_available_for_spawn(
    reason: str,
    health: dict[str, Any] | None = None,
    max_wait_seconds: float = 5.0,
) -> None:
    ensure_project_router_port_available(
        reason,
        health,
        max_wait_seconds,
        config=router_process_config(),
        state=router_process_state_ports(),
        termination=router_termination_ports(),
        clock=RouterProcessClock(now=time.time, sleep=time.sleep),
    )

def terminate_windows_port(port: int, label: str, quiet: bool = False) -> bool:
    pids = windows_pids_on_port(port)
    stopped = False
    for pid in pids:
        if pid in (os.getpid(), os.getppid()):
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            stopped = True
        except Exception:
            pass
    if stopped and not quiet:
        print(f"Stopped existing {label} session(s): {', '.join(map(str, pids))}.")
    return stopped


def terminate_matching_processes(needles: list[str], label: str, quiet: bool = False) -> bool:
    return run_terminate_matching_processes(
        needles,
        label,
        ProcessControlServices(
            query=ProcessQueryServices(),
            signals=ProcessSignalServices(kill=os.kill, pid_is_running=pid_is_running),
            log=router_log,
        ),
        quiet=quiet,
        platform_name=os.name,
    )


def stop_ncp_proxy(quiet: bool = False) -> bool:
    if os.name == "nt":
        port = positive_int(read_env_file(NCP_ENV).get("PROXY_PORT")) or 8788
        stopped = terminate_windows_port(port, "Nvidia NCP proxy", quiet=True)
        if stopped and not quiet:
            print("Stopped existing Nvidia NCP proxy session if one was running.")
        return stopped
    ncp = find_executable("ncp")
    stopped = False
    if not ncp:
        return terminate_matching_processes(["nvd_claude_proxy"], "Nvidia NCP proxy", quiet=quiet)
    try:
        subprocess.run([ncp, "kill"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        stopped = True
    except (OSError, subprocess.SubprocessError) as exc:
        router_log(
            "WARN",
            f"ncp_proxy_kill_failed executable={ncp} error={type(exc).__name__}: {exc}",
        )
    stopped = terminate_matching_processes(["nvd-claude-proxy"], "Nvidia NCP proxy", quiet=True) or stopped
    stopped = terminate_matching_processes(["ncp", "proxy"], "Nvidia NCP proxy", quiet=True) or stopped
    stopped = terminate_matching_processes(["nvd_claude_proxy"], "Nvidia NCP proxy", quiet=True) or stopped
    if stopped and not quiet:
        print("Stopped existing Nvidia NCP proxy session if one was running.")
    return stopped


def stop_router_processes(quiet: bool = False) -> bool:
    return stop_project_router_processes(
        quiet,
        config=router_process_config(),
        state=router_process_state_ports(),
        termination=router_termination_ports(),
    )


def stop_router_with_guarantee(reason: str, max_wait_seconds: float = 5.0, quiet: bool = True) -> bool:
    return stop_project_router_with_guarantee(
        reason,
        max_wait_seconds,
        quiet,
        config=router_process_config(),
        state=router_process_state_ports(),
        termination=router_termination_ports(),
        clock=RouterProcessClock(now=time.time, sleep=time.sleep),
    )

def cleanup_managed_services_for_provider(provider: str, pcfg: dict[str, Any], cfg: dict[str, Any], quiet: bool = False) -> None:
    if direct_native_anthropic_enabled(provider, pcfg):
        # Claude Native mode strips ciel-runtime routing env before spawning
        # `claude`. Clean up only this config's idle router; do not kill a
        # different folder/config or an active routed session.
        stop_router_if_no_active_clients("native_anthropic_launch", quiet=quiet)
        if provider != "nvidia-hosted" or provider_native_compat_enabled(provider, pcfg):
            stop_ncp_proxy(quiet=quiet)
        return
    if direct_native_codex_enabled(provider, pcfg):
        stop_router_if_no_active_clients("native_codex_launch", quiet=quiet)
        stop_ncp_proxy(quiet=quiet)
        return
    if direct_native_agy_enabled(provider, pcfg):
        stop_router_if_no_active_clients("native_agy_launch", quiet=quiet)
        stop_ncp_proxy(quiet=quiet)
        return
    if not cfg.get("cleanup", {}).get("managed_services_on_launch", True):
        return
    if provider != "nvidia-hosted" or provider_native_compat_enabled(provider, pcfg):
        stop_ncp_proxy(quiet=quiet)


def default_base_url(provider: str) -> str:
    if provider == "nvidia-hosted":
        return nvidia_upstream_base_url()
    if PROVIDER_ADAPTERS.contains(provider):
        configured = PROVIDER_ADAPTERS.create(provider).default_base_url()
        if configured:
            return configured
    return "http://localhost:8000"


def meaningful_key(value: str | None) -> bool:
    return meaningful_key_value(value)


def api_key_status_line(provider: str, pcfg: dict[str, Any]) -> str:
    key_count = provider_api_key_count(provider, pcfg)
    primary = provider_primary_api_key(provider, pcfg)
    primary_detail = (
        f"; primary {mask_secret(primary)}; fp {secret_fingerprint(primary)}"
        if key_count
        else ""
    )
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.api_key_status(
        provider_contract_config(provider, pcfg),
        key_count=key_count,
        primary_detail=primary_detail,
    )


def provider_status_services() -> ProviderStatusServices:
    return ProviderStatusServices(
        routing=ProviderStatusRouting(
            codex_routed=codex_routed_enabled,
            agy_routed=agy_routed_enabled,
            nvidia_native=nvidia_hosted_native_compat_enabled,
            native_anthropic_base=native_anthropic_base_url,
            router_up=router_up,
            router_base=ROUTER_BASE,
        ),
        catalog=ProviderStatusCatalog(
            model_headers=provider_model_list_headers,
            http_json=http_json,
            join_url=join_url,
            management_base=fireworks_management_base_url,
            model_ids=model_ids_from_response,
        ),
        generic=ProviderStatusGeneric(
            primary_api_key=provider_primary_api_key,
            meaningful_key=meaningful_key,
            with_user_agent=with_upstream_user_agent,
            provider_urlopen=provider_urlopen,
            model_context_limit=upstream_model_context_limit,
        ),
    )


def base_url_status_line(provider: str, pcfg: dict[str, Any]) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    policy = adapter.status_policy(provider_contract_config(provider, pcfg))
    return project_provider_base_url_status(
        provider,
        pcfg,
        policy,
        services=provider_status_services(),
    )


def preflight_lines() -> list[str]:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    lang = cfg.get("language", "en")
    notes = PROVIDER_NOTES.get(lang, PROVIDER_NOTES["en"]).get(provider, [])
    return [
        base_url_status_line(provider, pcfg),
        api_key_status_line(provider, pcfg),
        *notes,
    ]


def provider_readiness_services() -> ProviderReadinessServices:
    return ProviderReadinessServices(
        mode=ProviderReadinessMode(
            direct_native_anthropic=direct_native_anthropic_enabled,
            native_agy=native_agy_enabled,
            native_codex=native_codex_enabled,
        ),
        capabilities=ProviderReadinessCapabilities(
            ultracode_enabled=claude_code_ultracode_enabled,
            supported_capabilities=claude_code_supported_capabilities,
            current_model=current_upstream_model_id,
        ),
        lm_studio=ProviderReadinessLmStudio(
            ensure_model_loaded=ensure_lm_studio_model_loaded_for_context,
            save_config=save_config,
            runtime_info=upstream_model_runtime_info,
            positive_int=positive_int,
            minimum_context=LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT,
        ),
        base_url_status=base_url_status_line,
    )


def launch_readiness_errors(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    provider, pcfg = get_current_provider(cfg)
    adapter = configured_provider_adapter(provider, pcfg)
    contract_config = provider_contract_config(provider, pcfg)
    status_policy = adapter.status_policy(contract_config)
    return evaluate_provider_readiness(
        cfg,
        provider,
        pcfg,
        adapter,
        contract_config,
        status_policy,
        services=provider_readiness_services(),
    )


def launch_blockers_require_api_key(blockers: list[str]) -> bool:
    return any("requires" in line.lower() and "api key" in line.lower() for line in blockers)


def settings_ready_except_api_key() -> bool:
    cfg = load_config()
    provider, pcfg = get_current_provider(cfg)
    if provider == "codex":
        return True
    base = pcfg.get("base_url", "")
    model = pcfg.get("current_model", "")
    return bool(provider and base and model and "your-" not in base)


def self_cmd(args: list[str]) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return p.returncode, p.stdout


def enable_ansi() -> None:
    enable_terminal_ansi()


def ansi(text: str, code: str) -> str:
    return render_ansi(text, code)


def animated_ansi_text(text: str, *, phase: int | None = None, bold: bool = True) -> str:
    return render_animated_ansi_text(text, phase=phase, bold=bold)


def cell_width(text: str) -> int:
    return terminal_cell_width(text)


def fit_cells(value: Any, width: int) -> str:
    return fit_terminal_cells(value, width)


def pad_cells(value: Any, width: int) -> str:
    return pad_terminal_cells(value, width)


def color_line(text: str, code: str, width: int) -> str:
    fitted = fit_cells(text, width)
    return ansi(fitted, code)


def clean_render_lines(lines: list[str], width: int) -> list[str]:
    # All menu rows must stay single-line. Windows cmd corrupts redraws after
    # implicit line wrapping, even when ANSI clear-to-end is used.
    return [fit_cells(line, width) for line in lines]


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def intro_panel_lines(width: int) -> list[str]:
    return render_intro_panel_lines(width, APP_NAME, CREDITS)


def print_intro_panel(width: int) -> None:
    print("\n".join(intro_panel_lines(width)))


def append_menu_key_debug_log(line: str) -> None:
    write_menu_key_debug_log(MENU_KEY_DEBUG_PATH, line)


def read_menu_key(fd: int | None = None) -> str:
    return read_terminal_menu_key(fd, debug_log=append_menu_key_debug_log)


def portable_select(
    title: str,
    rows: list[str],
    current: int = 0,
    footer: str = "",
    info_lines: list[str] | None = None,
    show_intro: bool = False,
) -> int | None:
    return run_portable_select(
        title,
        rows,
        current,
        footer,
        info_lines,
        show_intro,
        services=TerminalSelectionServices(
            enable_ansi=enable_ansi,
            ansi=ansi,
            intro_panel_lines=intro_panel_lines,
            status_lines=status_lines,
            read_key=read_menu_key,
        ),
    )

def pause() -> None:
    input("Press Enter to continue...")


def compact_text(value: Any, width: int = 72) -> str:
    return fit_cells(value, width)


def provider_menu_label(provider: str, pcfg: dict[str, Any]) -> str:
    policy = provider_ui_policy(provider, pcfg)
    if pcfg.get("route_through_router") and policy.routed_menu_label:
        return policy.routed_menu_label
    return policy.menu_label or PROVIDER_LABELS.get(provider, provider)


def current_provider_panel_choice(provider: str, pcfg: dict[str, Any]) -> str:
    policy = provider_ui_policy(provider, pcfg)
    if pcfg.get("route_through_router") and policy.routed_choice:
        return policy.routed_choice
    if policy.native_choice:
        return policy.native_choice
    return provider


MAIN_MENU_ACTIONS: tuple[str, ...] = (
    "language",
    "provider",
    "api-key",
    "base-url",
    "model",
    "advisor-model",
    "options",
    "log-level",
    "test",
    "launch",
    "launch-codex",
    "launch-codex-app-server",
    "launch-agy",
    "quit",
)


def provider_ui_policy(provider: str, pcfg: dict[str, Any]):
    return configured_provider_adapter(provider, pcfg).ui_policy(provider_contract_config(provider, pcfg))


def claude_launch_enabled_for_provider(provider: str, pcfg: dict[str, Any] | None = None) -> bool:
    del pcfg
    return DEFAULT_RUNTIME_COMPATIBILITY.supports("claude", provider)


def agy_launch_enabled_for_provider(provider: str, pcfg: dict[str, Any] | None = None) -> bool:
    del pcfg
    return DEFAULT_RUNTIME_COMPATIBILITY.supports("agy", provider)


def codex_launch_enabled_for_provider(provider: str, pcfg: dict[str, Any] | None = None) -> bool:
    del pcfg
    return DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", provider)


def default_prelaunch_action(provider: str) -> str:
    if agy_launch_enabled_for_provider(provider):
        return "launch-agy"
    return "launch-codex" if codex_launch_enabled_for_provider(provider) else "launch"


def prelaunch_action_index(action: str) -> int:
    try:
        return list(MAIN_MENU_ACTIONS).index(action)
    except ValueError:
        return 0


def main_menu_rows(cfg: dict[str, Any], provider: str, pcfg: dict[str, Any], lang: str) -> list[str]:
    policy = provider_ui_policy(provider, pcfg)
    model_text = (
        policy.model_placeholder
        if policy.model_placeholder and not pcfg.get("current_model")
        else compact_text(pcfg.get("current_model", "unset"), 62)
    )
    advisor_text = (
        policy.advisor_placeholder
        if policy.advisor_placeholder
        else compact_text(pcfg.get("advisor_model") or "off", 62)
    )
    launch_label = ui_text("launch", lang)
    if not DEFAULT_RUNTIME_COMPATIBILITY.supports("claude", provider):
        family = DEFAULT_RUNTIME_COMPATIBILITY.provider_family(
            provider, provider_menu_label(provider, pcfg)
        )
        launch_label += f" [disabled: {family} provider selected]"
    launch_agy_label = ui_text("launch_agy", lang)
    if not DEFAULT_RUNTIME_COMPATIBILITY.supports("agy", provider):
        launch_agy_label += " [disabled: select AGY provider]"
    launch_codex_label = ui_text("launch_codex", lang)
    if not DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", provider):
        family = DEFAULT_RUNTIME_COMPATIBILITY.provider_family(
            provider, provider_menu_label(provider, pcfg)
        )
        launch_codex_label += f" [disabled: {family} provider selected]"
    launch_codex_app_server_label = ui_text("launch_codex_app_server", lang)
    if not DEFAULT_RUNTIME_COMPATIBILITY.supports("codex", provider):
        family = DEFAULT_RUNTIME_COMPATIBILITY.provider_family(
            provider, provider_menu_label(provider, pcfg)
        )
        launch_codex_app_server_label += f" [disabled: {family} provider selected]"
    return [
        f"0. {ui_text('language', lang)}  [{LANGUAGES.get(lang, lang)}]",
        f"1. {ui_text('provider', lang)}  [{provider_menu_label(provider, pcfg)}]",
        f"2. {ui_text('api_key', lang)}  [{stored_api_key_mask(provider, pcfg)}]",
        f"3. {ui_text('base_url', lang)}  [{compact_text(pcfg.get('base_url', 'unset'), 62)}]",
        f"4. {ui_text('model', lang)}  [{model_text}]",
        f"5. {ui_text('advisor_model', lang)}  [{advisor_text}]",
        f"6. {ui_text('options', lang)}  [{compact_text(llm_options_status(provider, pcfg), 62)}]",
        f"7. {ui_text('log_level', lang)}  [{log_level_status()}]",
        f"8. {ui_text('test', lang)}",
        f"9. {launch_label}",
        f"10. {launch_codex_label}",
        f"11. {launch_codex_app_server_label}",
        f"12. {launch_agy_label}",
        ui_text("quit", lang),
    ]


def provider_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    entries: list[tuple[str, str, str]] = []
    current = cfg.get("current_provider", "nvidia-hosted")
    for key, label in PROVIDER_LABELS.items():
        pcfg = cfg.get("providers", {}).get(key, {})
        if key == "anthropic":
            routed = anthropic_routed_enabled(key, pcfg)
            native_mark = "*" if current == key and not routed else " "
            routed_mark = "*" if current == key and routed else " "
            entries.append(
                (
                    "Claude Native",
                    f"{native_mark} {'Claude Native':<16} {'anthropic-native':<17} {compact_text(pcfg.get('base_url', ''), 52)}",
                    ANTHROPIC_NATIVE_PROVIDER_CHOICE,
                )
            )
            suffix = "router via Claude Code auth" if not provider_has_api_key(key, pcfg) else "router features"
            entries.append(
                (
                    "Anthropic routed",
                    f"{routed_mark} {'Anthropic routed':<16} {'anthropic-routed':<17} {suffix}",
                    ANTHROPIC_ROUTED_PROVIDER_CHOICE,
                )
            )
            continue
        if key == "agy":
            routed = agy_routed_enabled(key, pcfg)
            native_mark = "*" if current == key and not routed else " "
            routed_mark = "*" if current == key and routed else " "
            entries.append(("AGY", f"{native_mark} {'AGY':<16} {'agy-native':<17} native Antigravity settings", AGY_NATIVE_PROVIDER_CHOICE))
            entries.append(("AGY Routed", f"{routed_mark} {'AGY Routed':<16} {'agy-routed':<17} channel/PTY wake support", AGY_ROUTED_PROVIDER_CHOICE))
            continue
        if key == "codex":
            routed = codex_routed_enabled(key, pcfg)
            native_mark = "*" if current == key and not routed else " "
            routed_mark = "*" if current == key and routed else " "
            entries.append(("Codex Native", f"{native_mark} {'Codex Native':<16} {'codex-native':<17} native Codex settings", CODEX_NATIVE_PROVIDER_CHOICE))
            entries.append(("Codex routed", f"{routed_mark} {'Codex routed':<16} {'codex-routed':<17} router via native Codex auth", CODEX_ROUTED_PROVIDER_CHOICE))
            continue
        mark = "*" if key == current else " "
        entries.append((label, f"{mark} {label:<16} {key:<15} {compact_text(pcfg.get('base_url', ''), 54)}", key))
    entries.sort(key=lambda item: (item[0].casefold(), item[2].casefold()))
    return [row for _label, row, _value in entries], [value for _label, _row, value in entries]


def language_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    values: list[str] = []
    current = cfg.get("language", "en")
    for code, label in LANGUAGES.items():
        mark = "*" if code == current else " "
        rows.append(f"{mark} {code:<2} {label}")
        values.append(code)
    return rows, values


def log_level_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    values: list[str] = []
    current = log_level_name()
    descriptions = {
        "SILENT": "no router log writes",
        "ERROR": "errors only",
        "WARN": "warnings and errors",
        "INFO": "normal diagnostics",
        "DEBUG": "verbose diagnostics",
        "TRACE": "request/response trace detail",
    }
    for numeric in sorted(LOG_LEVEL_NAMES):
        name = LOG_LEVEL_NAMES[numeric]
        mark = "*" if name == current else " "
        rows.append(f"{mark} {name:<6} {numeric}  {descriptions.get(name, '')}")
        values.append(name)
    rows.append(f"Reset to default/env  [{log_level_status()}]")
    values.append("DEFAULT")
    rows.append(ui_text("back", cfg.get("language", "en")))
    values.append("back")
    return rows, values


def provider_model_panel_badge(provider: str, pcfg: dict[str, Any], model: str) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.model_panel_badge(provider_contract_config(provider, pcfg), model)


def provider_advisor_panel_notice(
    provider: str, pcfg: dict[str, Any]
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.advisor_panel_notice(provider_contract_config(provider, pcfg))


def provider_advisor_model_badge(provider: str, pcfg: dict[str, Any], model: str) -> str:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.advisor_model_badge(provider_contract_config(provider, pcfg), model)


def model_panel_services() -> ModelPanelServices:
    return ModelPanelServices(
        catalog=ModelPanelCatalog(
            alias_for=alias_for,
            cached_or_configured_model_ids=cached_or_configured_model_ids,
            read_model_info_cache=read_model_info_cache,
            read_model_list_cache=read_model_list_cache,
            unique_model_ids=unique_model_ids,
            upstream_model_ids=upstream_model_ids,
        ),
        presentation=ModelPanelPresentation(
            advisor_model_badge=provider_advisor_model_badge,
            advisor_panel_notice=provider_advisor_panel_notice,
            format_context_tokens=format_context_tokens,
            format_parameter_count=format_parameter_count,
            model_panel_badge=provider_model_panel_badge,
            normalize_model_id=normalize_model_id,
            positive_int=positive_int,
        ),
    )


def model_panel_rows(
    provider: str,
    pcfg: dict[str, Any],
    fetch: bool = True,
    force_refresh: bool = False,
) -> tuple[list[str], list[str]]:
    return project_model_panel_rows(
        provider,
        pcfg,
        fetch,
        force_refresh,
        services=model_panel_services(),
    )


def advisor_model_panel_rows(
    provider: str,
    pcfg: dict[str, Any],
    fetch: bool = True,
    force_refresh: bool = False,
) -> tuple[list[str], list[str]]:
    return project_advisor_model_panel_rows(
        provider,
        pcfg,
        fetch,
        force_refresh,
        services=model_panel_services(),
    )


def channel_panel_policy() -> ChannelPanelPolicy:
    return ChannelPanelPolicy(
        builtin_router_probe_record=_builtin_router_probe_record,
        channel_specs=channel_specs,
        delivery_mode=channel_delivery_mode,
        official_plugins=OFFICIAL_CHANNEL_PLUGINS,
        probe_record_bucket=channel_probe_record_bucket,
        read_probe_cache=read_channel_probe_cache,
    )


def channel_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return project_channel_panel_rows(cfg, policy=channel_panel_policy())


_channel_panel_first_selectable = first_selectable_channel_row
_channel_panel_step = step_channel_row


def channel_delivery_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return project_channel_delivery_panel_rows(cfg, policy=channel_panel_policy())


def api_key_panel_rows(provider: str, pcfg: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    rows = [
        "Type or paste API key as hidden input",
        "Type or paste multiple API keys (comma/newline separated)",
        "Read API key from an environment variable",
        "Read API keys from an environment variable",
        "Read API key from clipboard",
        "Read API keys from clipboard",
        "Back",
    ]
    values = ["input", "multi-input", "env", "multi-env", "clipboard", "multi-clipboard", "back"]
    if os.name != "nt":
        rows[4] = "Read API key from desktop clipboard if available"
        rows[5] = "Read API keys from desktop clipboard if available"
    if pcfg is not None and provider_api_key_count(provider, pcfg):
        rows.insert(-1, "Clear stored API key(s)")
        values.insert(-1, "clear")
    return rows, values


def base_url_panel_rows(provider: str, pcfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return (
        [
            f"Edit Base URL  [{compact_text(pcfg.get('base_url') or default_base_url(provider), 72)}]",
            f"Reset to provider default  [{default_base_url(provider)}]",
            "Back",
        ],
        ["edit", "default", "back"],
    )


def prelaunch_render_services() -> PrelaunchRenderServices:
    return PrelaunchRenderServices(
        brand=PrelaunchRenderBrand(
            animated_ansi_text=animated_ansi_text,
            credits=CREDITS,
            version=VERSION,
        ),
        data=PrelaunchRenderData(
            api_key_status_line=api_key_status_line,
            get_current_provider=get_current_provider,
            llm_option_description_for_value=llm_option_description_for_value,
            llm_option_panel_rows=llm_option_panel_rows,
            load_config=load_config,
            main_menu_rows=main_menu_rows,
            provider_mode_label=provider_mode_label,
        ),
        text=PrelaunchRenderText(
            ansi=ansi,
            cell_width=cell_width,
            fit_cells=fit_cells,
            pad_cells=pad_cells,
            ui_text=ui_text,
        ),
    )


def prelaunch_input_style() -> PrelaunchInputStyle:
    return PrelaunchInputStyle(ansi=ansi, log=router_log)


def render_prelaunch_screen(
    main_idx: int,
    panel: str | None,
    panel_idx: int,
    panel_rows: list[str],
    checks: list[str],
    messages: list[str],
    first_render: bool,
) -> bool:
    return render_prelaunch_terminal_screen(
        main_idx,
        panel,
        panel_idx,
        panel_rows,
        checks,
        messages,
        first_render,
        services=prelaunch_render_services(),
    )


def _prompt_menu_value_raw(label: str, default: str = "", secret: bool = False) -> str | None:
    return read_menu_value_raw(label, default, secret, style=prelaunch_input_style())


def prompt_menu_value(
    prompt: str,
    default: str = "",
    secret: bool = False,
    restore_tty: Callable[[], None] | None = None,
    raw_tty: Callable[[], None] | None = None,
) -> str:
    return read_menu_value(
        prompt,
        default,
        secret,
        restore_tty,
        raw_tty,
        style=prelaunch_input_style(),
    )


def _prompt_menu_multiline_value_raw(label: str, secret: bool = False) -> str | None:
    return read_menu_multiline_value_raw(label, secret, style=prelaunch_input_style())


def prompt_menu_multiline_value(
    prompt: str,
    restore_tty: Callable[[], None] | None = None,
    raw_tty: Callable[[], None] | None = None,
    secret: bool = True,
) -> str:
    return read_menu_multiline_value(
        prompt,
        restore_tty,
        raw_tty,
        secret,
        style=prelaunch_input_style(),
    )


def portable_provider_menu() -> int:
    cfg = load_config()
    rows, values = provider_panel_rows(cfg)
    selected = portable_select("Select ciel-runtime provider", rows, values.index(cfg.get("current_provider", "nvidia-hosted")))
    if selected is None:
        print("Cancelled.")
        return 1
    for line in set_provider_config(values[selected]):
        print(line)
    return 0


def portable_language_menu() -> int:
    cfg = load_config()
    rows, values = language_panel_rows(cfg)
    selected = portable_select("Select display language", rows, values.index(cfg.get("language", "en")))
    if selected is None:
        print("Cancelled.")
        return 1
    cfg["language"] = values[selected]
    save_config(cfg)
    print(f"Language set to {values[selected]} ({LANGUAGES[values[selected]]}).")
    return 0


def portable_prelaunch_menu(passthrough: list[str] | None = None) -> int:
    return execute_prelaunch_menu(
        passthrough,
        services=PrelaunchServices(
            constants=PrelaunchConstants(
                LANGUAGES=LANGUAGES,
                LLM_OPTION_TOGGLE_KEYS=LLM_OPTION_TOGGLE_KEYS,
                MAIN_MENU_ACTIONS=MAIN_MENU_ACTIONS,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
                PROVIDER_LABELS=PROVIDER_LABELS,
            ),
            terminal=PrelaunchTerminal(
                default_prelaunch_action=default_prelaunch_action,
                enable_ansi=enable_ansi,
                main_menu_rows=main_menu_rows,
                prelaunch_action_index=prelaunch_action_index,
                prompt_menu_multiline_value=prompt_menu_multiline_value,
                prompt_menu_value=prompt_menu_value,
                read_clipboard_text=read_clipboard_text,
                read_menu_key=read_menu_key,
                render_prelaunch_screen=render_prelaunch_screen,
                self_cmd=self_cmd,
            ),
            config=PrelaunchConfig(
                clear_model_cache=clear_model_cache,
                current_provider_panel_choice=current_provider_panel_choice,
                default_base_url=default_base_url,
                get_current_provider=get_current_provider,
                load_config=load_config,
                preflight_lines=preflight_lines,
                provider_menu_label=provider_menu_label,
                save_config=save_config,
                settings_ready_except_api_key=settings_ready_except_api_key,
                read_model_list_cache=read_model_list_cache,
            ),
            launch_policy=PrelaunchLaunchPolicy(
                agy_launch_enabled_for_provider=agy_launch_enabled_for_provider,
                claude_launch_enabled_for_provider=claude_launch_enabled_for_provider,
                codex_launch_enabled_for_provider=codex_launch_enabled_for_provider,
                launch_blockers_require_api_key=launch_blockers_require_api_key,
                launch_readiness_errors=launch_readiness_errors,
            ),
            panel_rows=PrelaunchPanelRows(
                advisor_model_panel_rows=advisor_model_panel_rows,
                api_key_panel_rows=api_key_panel_rows,
                base_url_panel_rows=base_url_panel_rows,
                context_setup_panel_rows=context_setup_panel_rows,
                language_panel_rows=language_panel_rows,
                llm_option_panel_rows=llm_option_panel_rows,
                llm_preset_panel_rows=llm_preset_panel_rows,
                log_level_panel_rows=log_level_panel_rows,
                model_panel_rows=model_panel_rows,
                provider_panel_rows=provider_panel_rows,
            ),
            channel_query=PrelaunchChannelQuery(
                _channel_panel_first_selectable=_channel_panel_first_selectable,
                _channel_panel_step=_channel_panel_step,
                channel_delivery_panel_rows=channel_delivery_panel_rows,
                channel_panel_rows=channel_panel_rows,
                channel_panel_rows_for_menu=channel_panel_rows_for_menu,
                channel_probe_summary_message=channel_probe_summary_message,
                channel_specs=channel_specs,
                refresh_channel_probe_cache=refresh_channel_probe_cache,
            ),
            channel_commands=PrelaunchChannelCommands(
                add_channel_spec=add_channel_spec,
                clear_channel_specs=clear_channel_specs,
                remove_channel_spec=remove_channel_spec,
                set_channel_delivery_config=set_channel_delivery_config,
            ),
            mutations=PrelaunchMutations(
                apply_context_setup_config=apply_context_setup_config,
                apply_llm_preset_config=apply_llm_preset_config,
                apply_timeout_profile_to_provider=apply_timeout_profile_to_provider,
                set_advisor_model_config=set_advisor_model_config,
                set_base_url_config=set_base_url_config,
                set_llm_option_config=set_llm_option_config,
                set_log_level_config=set_log_level_config,
                set_model_config=set_model_config,
                set_provider_choice_config=set_provider_choice_config,
            ),
            secrets=PrelaunchSecrets(
                clear_api_key_config=clear_api_key_config,
                mask_secret=mask_secret,
                parse_api_key_list=parse_api_key_list,
                secret_fingerprint=secret_fingerprint,
                store_api_key_input_config=store_api_key_input_config,
                store_api_keys_config=store_api_keys_config,
            ),
            options=PrelaunchOptions(
                llm_option_current_bool=llm_option_current_bool,
                llm_option_prompt_default=llm_option_prompt_default,
                timeout_profile_panel_rows=timeout_profile_panel_rows,
            ),
        ),
    )


def run_external_menu(name: str) -> int | None:
    if os.name == "nt":
        return None
    exe = find_executable(name)
    if not exe:
        return None
    return subprocess.call([exe])


def has_noninteractive_claude_args(passthrough: list[str]) -> bool:
    return any(arg == "-p" or arg == "--print" or arg.startswith("--print=") for arg in passthrough)


def run_prelaunch_menu(passthrough: list[str], skip_menu: bool = False, force_menu: bool = False) -> int:
    if not force_menu and (
        skip_menu or has_noninteractive_claude_args(passthrough) or os.environ.get("CIEL_RUNTIME_SKIP_MENU") == "1"
    ):
        return 0
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 0
    if os.environ.get("CIEL_RUNTIME_USE_LEGACY_MENU") == "1":
        rc = run_external_menu("ciel-runtime-menu")
        if rc is not None:
            return rc
    return portable_prelaunch_menu(passthrough)


def start_router_if_needed(*, replace_active_clients: bool = True) -> bool:
    health = router_health()
    if health is not None:
        active_clients = active_router_client_pids()
        if router_health_matches_current(health):
            if active_clients:
                if replace_active_clients:
                    router_log(
                        "WARN",
                        "router_prelaunch_replace_active_clients "
                        f"base={ROUTER_BASE} active_clients={','.join(map(str, active_clients))}",
                    )
                    terminate_active_router_clients("prelaunch_active_clients", active_clients, quiet=True)
                    ensure_router_port_available_for_spawn("prelaunch_active_clients", health)
                else:
                    router_log(
                        "INFO",
                        "router_check_state running=True spawn=False "
                        f"base={ROUTER_BASE} active_clients={','.join(map(str, active_clients))}",
                    )
                    return True
            else:
                if env_bool(os.environ.get("CIEL_RUNTIME_REUSE_ROUTER"), False):
                    router_log("INFO", f"router_check_state running=True spawn=False base={ROUTER_BASE} reuse=env")
                    return True
                router_log(
                    "INFO",
                    "router_prelaunch_replace "
                    f"running_version={health.get('version') or '-'} current_version={VERSION} "
                    f"running_source={health.get('source_fingerprint') or '-'} current_source={SOURCE_FINGERPRINT} "
                    f"pid={health.get('pid') or '-'}",
                )
                ensure_router_port_available_for_spawn("prelaunch_replace", health)
        else:
            if router_health_config_matches_current(health) and active_clients:
                if replace_active_clients:
                    router_log(
                        "WARN",
                        "router_version_mismatch_replace_active_clients "
                        f"running_version={health.get('version') or '-'} current_version={VERSION} "
                        f"active_clients={','.join(map(str, active_clients))}",
                    )
                    terminate_active_router_clients("version_mismatch_active_clients", active_clients, quiet=True)
                    ensure_router_port_available_for_spawn("version_mismatch_active_clients", health)
                else:
                    raise RuntimeError(
                        f"ciel-runtime router on {ROUTER_BASE} belongs to this config but has active clients "
                        f"({','.join(map(str, active_clients))}) and differs from this launch "
                        f"(running_version={health.get('version') or '-'}, current_version={VERSION}). "
                        "Stop the other Claude Code session or launch this instance with a different "
                        "CIEL_RUNTIME_ROUTER_PORT."
                    )
            else:
                running_version = str(health.get("version") or "")
                running_fingerprint = str(health.get("source_fingerprint") or "")
                router_log(
                    "WARN",
                    "router_version_mismatch_restart "
                    f"running_version={running_version or '-'} current_version={VERSION} "
                    f"running_source={running_fingerprint or '-'} current_source={SOURCE_FINGERPRINT}",
                )
                ensure_router_port_available_for_spawn("version_mismatch", health)
    else:
        ensure_router_port_available_for_spawn("pre_spawn", None)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "serve"]
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    router_log("INFO", f"router_check_state running=False spawn=True base={ROUTER_BASE}")
    router_env = os.environ.copy()
    router_env["CIEL_RUNTIME_MANAGED_ROUTER"] = "1"
    router_env["CIEL_RUNTIME_ROUTER_OWNER_PID"] = str(os.getpid())
    with open(LOG_PATH, "ab", buffering=0) as log:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=router_env, **kwargs)
    deadline = time.time() + 30
    while time.time() < deadline:
        if router_up():
            router_log("INFO", f"router_spawned running=True base={ROUTER_BASE} elapsed={time.time()-(deadline-30):.1f}s")
            return True
        time.sleep(0.5)
    raise RuntimeError(f"ciel-runtime router did not start. See {LOG_PATH}")


def should_attach_web_search(provider: str, cfg: dict[str, Any], override: bool | None) -> bool:
    if override is not None:
        return override
    pcfg = cfg.get("providers", {}).get(provider, {}) if isinstance(cfg.get("providers"), dict) else {}
    contract = provider_contract_config(provider, pcfg)
    return PROVIDER_COMPATIBILITY.resolve(provider).auto_web_search(contract) and bool(
        cfg.get("web_search", {}).get("auto_for_non_native", True)
    )


def should_append_compat_prompt(provider: str, pcfg: dict[str, Any], cfg: dict[str, Any]) -> bool:
    return PROVIDER_COMPATIBILITY.resolve(provider).requires_compat_prompt and bool(
        cfg.get("claude_code", {}).get("compat_prompt_for_non_anthropic", True)
    )


_CLAUDE_PERMISSION_MODE_SUPPORT_CACHE: dict[str, bool] = {}


def claude_supports_permission_mode_arg(claude: str) -> bool:
    cache_key = str(claude or "")
    if cache_key in _CLAUDE_PERMISSION_MODE_SUPPORT_CACHE:
        return _CLAUDE_PERMISSION_MODE_SUPPORT_CACHE[cache_key]
    supported = False
    try:
        proc = subprocess.run(
            [claude, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
            check=False,
        )
        help_text = proc.stdout or ""
        supported = "--permission-mode" in help_text and "bypassPermissions" in help_text
    except Exception:
        supported = False
    _CLAUDE_PERMISSION_MODE_SUPPORT_CACHE[cache_key] = supported
    return supported


def has_passthrough_option(passthrough: list[str], *names: str) -> bool:
    return any(arg in names or any(arg.startswith(name + "=") for name in names) for arg in passthrough)


def should_disallow_claude_server_side_web_tools(
    provider: str,
    pcfg: dict[str, Any],
    use_native_anthropic: bool,
) -> bool:
    return not use_native_anthropic and not anthropic_routed_enabled(provider, pcfg)


CLAUDE_CODE_GENERATED_GREEDY_OPTIONS = {
    "--mcp-config",
    "--dangerously-load-development-channels",
}


def should_insert_passthrough_option_boundary(extra_args: list[str], passthrough: list[str]) -> bool:
    if not passthrough:
        return False
    first = passthrough[0]
    if first == "--" or first.startswith("-"):
        return False
    return any(arg in CLAUDE_CODE_GENERATED_GREEDY_OPTIONS for arg in extra_args)


def claude_session_control_requested(passthrough: list[str]) -> bool:
    return project_session_control_requested(passthrough)


def current_launch_cwd_key() -> str:
    return project_current_launch_cwd_key()


def launch_mode_name(provider: str, pcfg: dict[str, Any], use_native_anthropic: bool) -> str:
    return project_launch_mode_name(
        provider,
        use_native_anthropic=use_native_anthropic,
        anthropic_routed=anthropic_routed_enabled(provider, pcfg),
    )


def launch_state_repository() -> LaunchStateRepository:
    return LaunchStateRepository(
        path=LAUNCH_STATE_PATH,
        config_dir=CONFIG_DIR,
        log=router_log,
        process_id=os.getpid,
        clock=time.time,
        clock_ns=time.time_ns,
    )


def read_launch_state() -> dict[str, Any]:
    return launch_state_repository().read()


def write_launch_state(state: dict[str, Any]) -> None:
    launch_state_repository().write(state)


def previous_launch_state_for_cwd(cwd_key: str) -> dict[str, Any]:
    return launch_state_repository().previous_for_cwd(cwd_key)


def last_launch_runtime() -> str:
    return project_last_launch_runtime(launch_state_repository(), current_launch_cwd_key())


def record_launch_state_for_cwd(cwd_key: str, provider: str, mode: str, model: str) -> None:
    launch_state_repository().record(cwd_key, provider, mode, model)


def should_fork_native_session_after_mode_switch(
    provider: str,
    pcfg: dict[str, Any],
    use_native_anthropic: bool,
    passthrough: list[str],
    cwd_key: str,
) -> tuple[bool, str]:
    return project_should_fork_native_session(
        current_mode=launch_mode_name(provider, pcfg, use_native_anthropic),
        passthrough=passthrough,
        cwd_key=cwd_key,
        use_native_anthropic=use_native_anthropic,
        repository=launch_state_repository(),
    )


def normalize_channel_passthrough(passthrough: list[str]) -> list[str]:
    normalized: list[str] = []
    i = 0
    while i < len(passthrough):
        arg = passthrough[i]
        if arg == "--channels":
            normalized.append("--dangerously-load-development-channels")
            i += 1
            while i < len(passthrough) and is_channel_spec_tagged(passthrough[i]):
                normalized.append(passthrough[i])
                i += 1
            continue
        if arg.startswith("--channels="):
            value = arg.split("=", 1)[1].strip()
            if value:
                normalized.extend(["--dangerously-load-development-channels", value])
            else:
                normalized.append("--dangerously-load-development-channels")
            i += 1
            continue
        normalized.append(arg)
        i += 1
    return normalized


def native_channel_passthrough_requested(passthrough: list[str]) -> bool:
    return has_passthrough_option(passthrough, "--channels", "--dangerously-load-development-channels")


def claude_channel_args(
    cfg: dict[str, Any],
    passthrough: list[str],
    extra_specs: list[str] | None = None,
    *,
    native_channel_bridge: bool = False,
) -> list[str]:
    if not native_channel_bridge:
        return []
    if native_channel_passthrough_requested(passthrough):
        return []
    specs = [
        spec
        for spec in channel_specs_for_launch(cfg, passthrough, extra_specs)
        if not (str(spec).startswith("server:") and str(spec).split(":", 1)[1].strip().lower() in _NATIVE_ROUTER_CHANNEL_NAMES)
    ]
    if not specs:
        return []
    return ["--dangerously-load-development-channels", *specs]


def claude_channels_requested(cfg: dict[str, Any], passthrough: list[str], extra_specs: list[str] | None = None) -> bool:
    return native_channel_passthrough_requested(passthrough)


def should_use_native_channel_bridge(use_router_mode: bool, cfg: dict[str, Any], passthrough: list[str]) -> bool:
    return bool(
        not use_router_mode
        and channel_delivery_mode(cfg) == "native"
        and not native_channel_passthrough_requested(passthrough)
    )


def should_use_channel_llm_delivery(use_router_mode: bool, passthrough: list[str], cfg: dict[str, Any] | None = None) -> bool:
    if not use_router_mode or native_channel_passthrough_requested(passthrough):
        return False
    return True


def channel_specs_include_external_server(specs: list[str]) -> bool:
    for spec in specs:
        text = str(spec or "").strip()
        if not text:
            continue
        name = text.split(":", 1)[1] if ":" in text else text
        if name.strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES:
            return True
    return False


def claude_code_channels_auth_available(claude: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [claude, "auth", "status"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return True, f"auth_status_unavailable:{type(exc).__name__}"
    if proc.returncode != 0:
        return True, f"auth_status_rc_{proc.returncode}"
    try:
        data = json.loads(proc.stdout or "{}")
    except (json.JSONDecodeError, TypeError):
        return True, "auth_status_unparseable"
    if not bool(data.get("loggedIn")):
        return False, "not_logged_in"
    return True, str(data.get("authMethod") or "logged_in")


def write_web_tools_mcp_config(cfg: dict[str, Any]) -> Path:
    web = cfg.get("web_search", {})
    package = web.get("package") or "ddg-mcp-search"
    npx = find_executable("npx") or ("npx.cmd" if os.name == "nt" else "npx")
    servers: dict[str, Any] = {
        "duckduckgo": {
            "command": npx,
            "args": ["-y", package],
        }
    }
    if web.get("fetch_enabled", True):
        fetch_args = [web.get("fetch_package") or "mcp-server-fetch"]
        if web.get("fetch_user_agent"):
            fetch_args.extend(["--user-agent", str(web["fetch_user_agent"])])
        if web.get("fetch_ignore_robots_txt", False):
            fetch_args.append("--ignore-robots-txt")
        fetch_command = find_executable("uvx")
        fetch_command_args = fetch_args
        if not fetch_command:
            uv = find_executable("uv")
            if uv:
                fetch_command = uv
                fetch_command_args = ["tool", "run", *fetch_args]
            elif importlib.util.find_spec("uv") is not None:
                fetch_command = sys.executable
                fetch_command_args = ["-m", "uv", "tool", "run", *fetch_args]
            else:
                pipx = find_executable("pipx")
                if pipx:
                    fetch_command = pipx
                    fetch_command_args = ["run", *fetch_args]
        if fetch_command:
            servers["web_fetch"] = {
                "command": fetch_command,
                "args": fetch_command_args,
                "ciel_runtime_stdio": "jsonl",
            }
        else:
            router_log("WARN", "web_fetch_disabled_missing_runner install=uvx_or_uv")
    data = {"mcpServers": servers}
    json_artifact_repository(WEB_TOOLS_MCP_CONFIG).save(data, "web_tools_mcp_config")
    return WEB_TOOLS_MCP_CONFIG


def write_duckduckgo_mcp_config(cfg: dict[str, Any]) -> Path:
    path = write_web_tools_mcp_config(cfg)
    try:
        DUCKDUCKGO_MCP_CONFIG.write_text(path.read_text())
    except (OSError, UnicodeError) as exc:
        router_log(
            "WARN",
            f"duckduckgo_mcp_compat_config_write_failed error={type(exc).__name__}: {exc}",
        )
    return path


def write_zai_mcp_config(provider: str, pcfg: dict[str, Any]) -> Path | None:
    if provider != "zai" or not bool(pcfg.get("managed_mcp", True)):
        return None
    key = provider_primary_api_key(provider, pcfg)
    if not meaningful_key(key):
        router_log("WARN", "zai_mcp_config_skipped_missing_api_key")
        return None
    npx = find_executable("npx") or ("npx.cmd" if os.name == "nt" else "npx")
    servers: dict[str, Any] = {
        "zai-mcp-server": {
            "type": "stdio",
            "command": npx,
            "args": ["-y", "@z_ai/mcp-server@latest"],
            "env": {
                "Z_AI_API_KEY": key,
                "Z_AI_MODE": "ZAI",
            },
        }
    }
    auth_header = {"Authorization": f"Bearer {key}"}
    for name, url in ZAI_MANAGED_MCP_SERVERS:
        servers[name] = {
            "type": "http",
            "url": url,
            "headers": dict(auth_header),
        }
    data = {"mcpServers": servers}
    json_artifact_repository(ZAI_MCP_CONFIG).save(data, "zai_mcp_config")
    router_log("INFO", f"zai_mcp_config_written servers={','.join(sorted(servers))}")
    return ZAI_MCP_CONFIG


def reset_zai_mcp_config_if_inactive(provider: str) -> None:
    if provider == "zai":
        return
    try:
        ZAI_MCP_CONFIG.unlink()
        router_log("INFO", "zai_mcp_config_removed inactive_provider")
    except FileNotFoundError:
        pass
    except Exception as exc:
        router_log("WARN", f"zai_mcp_config_remove_failed error={type(exc).__name__}: {exc}")


def write_channel_mcp_config() -> Path:
    data = {
        "mcpServers": {
            "ciel-runtime-router": {
                "type": "sse",
                "url": f"{ROUTER_BASE}/ca/mcp/sse",
            }
        }
    }
    json_artifact_repository(CHANNEL_MCP_CONFIG).save(data, "channel_mcp_config")
    _channel_mcp_ensure_cursor_initialized()
    return CHANNEL_MCP_CONFIG


def write_mcp_proxy_config(
    passthrough: list[str],
    *,
    extra_config_paths: list[Path | str] | None = None,
    force_proxy_server_names: set[str] | None = None,
    disable_proxy_notification_stream_names: set[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    cwd = cwd or Path.cwd()
    force_proxy_server_names = set(force_proxy_server_names or set())
    disable_proxy_notification_stream_names = set(disable_proxy_notification_stream_names or set())
    extra = [Path(item).expanduser() for item in (extra_config_paths or [])]
    paths = [*extra, *claude_mcp_config_paths(passthrough, cwd, home)]
    servers: dict[str, Any] = {}
    server_dir = CONFIG_DIR / "mcp-proxy-servers"
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        for name, server in _read_mcp_servers_from_json(path, cwd):
            if name in servers:
                router_log("INFO", f"mcp_proxy_config_duplicate_overwritten server={name} source={path}")
            streamable_http = _mcp_server_is_streamable_http(server)
            force_streamable_proxy = streamable_http and (name in force_proxy_server_names or _mcp_server_force_proxy(server))
            if _mcp_server_is_stdio(server) or force_streamable_proxy:
                server_dir.mkdir(parents=True, exist_ok=True)
                server_path = server_dir / f"{_safe_mcp_proxy_name(name)}.json"
                saved_server = dict(server)
                if streamable_http and name in disable_proxy_notification_stream_names:
                    saved_server["ciel_runtime_disable_notification_stream"] = True
                json_artifact_repository(server_path).save(
                    saved_server,
                    f"mcp_proxy_server:{name}",
                )
                servers[name] = {
                    "command": sys.executable,
                    "args": [
                        str(Path(__file__).resolve()),
                        "mcp-proxy",
                        "--server-name",
                        name,
                        "--server-config",
                        str(server_path),
                    ],
                }
            else:
                servers[name] = server
    if not servers:
        return None
    json_artifact_repository(MCP_PROXY_CONFIG).save(
        {"mcpServers": servers},
        "mcp_proxy_config",
    )
    router_log("INFO", f"mcp_proxy_config_written servers={','.join(sorted(servers))}")
    return MCP_PROXY_CONFIG


def should_use_channel_stdin_proxy(use_router_mode: bool, passthrough: list[str], cfg: dict[str, Any] | None = None) -> bool:
    if not use_router_mode or native_channel_passthrough_requested(passthrough):
        return False
    if has_passthrough_option(passthrough, "-p", "--print"):
        return False
    ccfg = (cfg or {}).get("claude_code") if isinstance(cfg, dict) else {}
    if isinstance(ccfg, dict) and ccfg.get("web_chat_session_bridge") is False:
        return False
    return channel_delivery_mode(cfg) == "llm"


def should_launch_process_start_channel_sse(
    stdin_channel_proxy: bool,
    native_channel_bridge: bool,
    llm_channel_delivery: bool,
) -> bool:
    return bool((stdin_channel_proxy or native_channel_bridge) and not llm_channel_delivery)


def _channel_pending_scan_limit() -> int:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_PENDING_SCAN_LIMIT", "500")
    try:
        return max(100, min(5000, int(str(raw).strip())))
    except (TypeError, ValueError):
        return 500


def _channel_stdin_wake_batch_limit() -> int:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_BATCH_LIMIT", "8")
    try:
        return max(1, min(50, int(str(raw).strip())))
    except (TypeError, ValueError):
        return 8


_CHANNEL_LLM_TOOL_CONTEXT_LOCK = threading.Lock()
_CHANNEL_LLM_TOOL_CONTEXT: dict[str, dict[str, Any]] = {}
_CHANNEL_LLM_TOOL_CONTEXT_LIMIT = 200
_CHANNEL_LLM_TOOL_CONTEXT_MAX_INJECT = 8
_CHANNEL_LLM_TOOL_CONTEXT_PROMPT_LIMIT = 4000


def _channel_injected_prompt_text(body: dict[str, Any]) -> str:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = anthropic_content_to_text(message.get("content", ""))
        if metadata.get("ciel_runtime_channel_injected") and text:
            return truncate_for_prompt(text, _CHANNEL_LLM_TOOL_CONTEXT_PROMPT_LIMIT)
        if "[external channel input]" in text or "[ciel-runtime channel inbox]" in text:
            return truncate_for_prompt(text, _CHANNEL_LLM_TOOL_CONTEXT_PROMPT_LIMIT)
    return ""


def _remember_channel_injected_tool_use(source_body: dict[str, Any] | None, tool_use_id: str, tool_name: str, tool_input: Any) -> None:
    if not isinstance(source_body, dict) or not tool_use_id:
        return
    metadata = source_body.get("metadata") if isinstance(source_body.get("metadata"), dict) else {}
    if not metadata.get("ciel_runtime_channel_injected"):
        return
    context = {
        "created_at": time.time(),
        "channel_message_ids": str(metadata.get("ciel_runtime_channel_message_ids") or ""),
        "prompt": _channel_injected_prompt_text(source_body),
        "tool_name": tool_name,
        "tool_input": tool_input if isinstance(tool_input, (dict, list, str, int, float, bool)) or tool_input is None else str(tool_input),
    }
    with _CHANNEL_LLM_TOOL_CONTEXT_LOCK:
        _CHANNEL_LLM_TOOL_CONTEXT[tool_use_id] = context
        if len(_CHANNEL_LLM_TOOL_CONTEXT) > _CHANNEL_LLM_TOOL_CONTEXT_LIMIT:
            for old_id, _old in sorted(_CHANNEL_LLM_TOOL_CONTEXT.items(), key=lambda item: item[1].get("created_at", 0))[
                : len(_CHANNEL_LLM_TOOL_CONTEXT) - _CHANNEL_LLM_TOOL_CONTEXT_LIMIT
            ]:
                _CHANNEL_LLM_TOOL_CONTEXT.pop(old_id, None)
    router_log(
        "INFO",
        f"channel_llm_tool_context_stored tool_use_id={tool_use_id} tool={tool_name} message_ids={context['channel_message_ids']}",
    )


def remember_channel_injected_tool_uses(source_body: dict[str, Any] | None, message: dict[str, Any]) -> None:
    if not isinstance(message, dict):
        return
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        _remember_channel_injected_tool_use(
            source_body,
            str(block.get("id") or ""),
            str(block.get("name") or "tool"),
            block.get("input"),
        )


def _take_channel_tool_result_contexts_for_body(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    with _CHANNEL_LLM_TOOL_CONTEXT_LOCK:
        for message in body.get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id") or "")
                context = _CHANNEL_LLM_TOOL_CONTEXT.pop(tool_use_id, None)
                if context:
                    found.append((tool_use_id, dict(context)))
                    if len(found) >= _CHANNEL_LLM_TOOL_CONTEXT_MAX_INJECT:
                        return found
    return found


def body_with_channel_tool_result_context(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if metadata.get("ciel_runtime_channel_tool_result_followup"):
        return body
    contexts = _take_channel_tool_result_contexts_for_body(body)
    if not contexts:
        return body
    parts = [
        "[ciel-runtime channel tool_result follow-up]",
        "tool_result data from a previous channel-injected tool call.",
    ]
    for tool_use_id, context in contexts:
        parts.append(
            "\n".join(
                [
                    f"tool_use_id={tool_use_id}",
                    f"tool={context.get('tool_name') or 'tool'}",
                    f"channel_message_ids={context.get('channel_message_ids') or ''}",
                    f"tool_input={json.dumps(context.get('tool_input'), ensure_ascii=False)}",
                    f"original_channel_prompt:\n{context.get('prompt') or '(not captured)'}",
                ]
            )
        )
    out = dict(body)
    messages = [m for m in body.get("messages", []) if isinstance(m, dict)]
    messages.append({"role": "user", "content": [{"type": "text", "text": "\n\n".join(parts)}]})
    out["messages"] = messages
    out_metadata = dict(metadata)
    out_metadata["ciel_runtime_channel_tool_result_followup"] = True
    out["metadata"] = out_metadata
    router_log(
        "INFO",
        "channel_llm_tool_result_context_injected tool_use_ids="
        + ",".join(tool_use_id for tool_use_id, _context in contexts),
    )
    return out


def _channel_llm_write_cursor_locked(last_id: int) -> None:
    channel_cursor_repository(CHANNEL_LLM_CURSOR_PATH).write(last_id)


def _channel_llm_read_cursor_locked() -> int:
    global _CHANNEL_LLM_CURSOR_LAST_ID
    file_cursor = channel_cursor_repository(CHANNEL_LLM_CURSOR_PATH).read()
    if file_cursor is not None:
        if _CHANNEL_LLM_CURSOR_LAST_ID is None or file_cursor > _CHANNEL_LLM_CURSOR_LAST_ID:
            _CHANNEL_LLM_CURSOR_LAST_ID = file_cursor
        return _CHANNEL_LLM_CURSOR_LAST_ID
    if _CHANNEL_LLM_CURSOR_LAST_ID is not None:
        return _CHANNEL_LLM_CURSOR_LAST_ID
    _CHANNEL_LLM_CURSOR_LAST_ID = max(0, _chat_scan_max_id())
    _channel_llm_write_cursor_locked(_CHANNEL_LLM_CURSOR_LAST_ID)
    return _CHANNEL_LLM_CURSOR_LAST_ID


def _channel_llm_clear_floor_read() -> int:
    return channel_cursor_repository(CHANNEL_LLM_CLEAR_FLOOR_PATH).read() or 0


def _channel_llm_clear_floor_write(last_id: int) -> None:
    channel_cursor_repository(CHANNEL_LLM_CLEAR_FLOOR_PATH).write(
        last_id,
        metadata={"updated_at": time.time()},
    )


def _channel_llm_clamp_to_clear_floor(recovered: int) -> int:
    clear_floor = _channel_llm_clear_floor_read()
    if clear_floor > 0 and recovered < clear_floor:
        router_log(
            "INFO",
            f"channel_stdin_proxy_recovery_clamped recovered_cursor={recovered} clear_floor={clear_floor}",
        )
        return clear_floor
    return recovered


def reset_channel_llm_delivery_cursor(last_id: int | None = None) -> int:
    global _CHANNEL_LLM_CURSOR_LAST_ID
    with _CHANNEL_LLM_CURSOR_LOCK:
        _CHANNEL_LLM_CURSOR_LAST_ID = max(0, int(last_id if last_id is not None else _chat_scan_max_id()))
        _channel_llm_write_cursor_locked(_CHANNEL_LLM_CURSOR_LAST_ID)
        return _CHANNEL_LLM_CURSOR_LAST_ID


def ensure_channel_llm_delivery_cursor_initialized() -> int:
    with _CHANNEL_LLM_CURSOR_LOCK:
        return _channel_llm_read_cursor_locked()


def prepare_channel_llm_delivery_for_launch() -> int:
    # chat-messages.jsonl is a transient bridge queue, not the durable MCP inbox.
    # On a new Claude Code process, replaying old rows left by a previous process
    # surfaces stale "one more" channel messages at startup. Do not fast-forward
    # over very recent rows, though: users often restart immediately after an
    # injected event, and those fresh rows still need to be delivered.
    current = ensure_channel_llm_delivery_cursor_initialized()
    recent_seconds = _channel_launch_recent_seconds()
    if recent_seconds <= 0:
        target = _chat_scan_max_id()
    else:
        target = _chat_scan_max_id_before_epoch(time.time() - recent_seconds)
    last_id = reset_channel_llm_delivery_cursor(max(current, target))
    _write_channel_llm_launch_guard(last_id)
    router_log(
        "INFO",
        "channel_llm_cursor_fast_forward_on_launch "
        f"last_id={last_id} previous_cursor={current} recent_seconds={recent_seconds:g}",
    )
    return last_id


def clear_channel_backlog() -> dict[str, Any]:
    global _CHANNEL_LLM_CURSOR_LAST_ID, _CHANNEL_MCP_CURSOR_LAST_ID
    chat_tail = max(0, _chat_scan_max_id())

    with _CHANNEL_LLM_CURSOR_LOCK:
        old_llm = _channel_llm_read_cursor_locked()
        _CHANNEL_LLM_CURSOR_LAST_ID = chat_tail
        try:
            _channel_llm_write_cursor_locked(chat_tail)
        except Exception as exc:
            router_log("WARN", f"channel_llm_cursor_write_failed error={type(exc).__name__}: {exc}")
        try:
            _channel_llm_clear_floor_write(chat_tail)
        except Exception as exc:
            router_log("WARN", f"channel_llm_clear_floor_write_failed error={type(exc).__name__}: {exc}")
    _CHANNEL_STDIN_RECOVERY_CACHE.clear()

    with _CHANNEL_MCP_CURSOR_LOCK:
        old_mcp = _channel_mcp_read_cursor_locked()
        _CHANNEL_MCP_CURSOR_LAST_ID = chat_tail
        try:
            _channel_mcp_write_cursor_locked(chat_tail)
        except Exception as exc:
            router_log("WARN", f"channel_mcp_cursor_write_failed error={type(exc).__name__}: {exc}")

    with _CHANNEL_MCP_LOCK:
        for state in _CHANNEL_MCP_SESSIONS.values():
            try:
                state["last_id"] = max(int(state.get("last_id") or 0), chat_tail)
            except Exception:
                state["last_id"] = chat_tail

    with _CHAT_CONDITION:
        _CHAT_CONDITION.notify_all()
    stats = {
        "chat_tail": chat_tail,
        "discarded_llm": max(0, chat_tail - int(old_llm or 0)),
        "discarded_mcp": max(0, chat_tail - int(old_mcp or 0)),
        "mcp_sessions_updated": len(_CHANNEL_MCP_SESSIONS),
    }
    router_log(
        "INFO",
        "channel_backlog_cleared "
        f"chat_tail={chat_tail} "
        f"discarded_llm={stats['discarded_llm']} discarded_mcp={stats['discarded_mcp']} "
        f"mcp_sessions_updated={stats['mcp_sessions_updated']}",
    )
    return stats


def channel_backlog_status() -> dict[str, Any]:
    chat_tail = max(0, _chat_scan_max_id())
    with _CHANNEL_LLM_CURSOR_LOCK:
        llm_cursor = _channel_llm_read_cursor_locked()
    with _CHANNEL_MCP_CURSOR_LOCK:
        mcp_cursor = _channel_mcp_read_cursor_locked()
    return {
        "chat_tail": chat_tail,
        "pending_llm": max(0, chat_tail - int(llm_cursor or 0)),
        "pending_mcp": max(0, chat_tail - int(mcp_cursor or 0)),
        "mcp_sessions": len(_CHANNEL_MCP_SESSIONS),
    }


def _metadata_int(metadata: dict[str, Any], key: str) -> int | None:
    try:
        value = metadata.get(key)
        if value is None or value == "":
            return None
        return max(0, int(value))
    except Exception:
        return None


def _commit_channel_llm_cursor_if_newer(last_id: int | None) -> None:
    global _CHANNEL_LLM_CURSOR_LAST_ID
    if last_id is None:
        return
    with _CHANNEL_LLM_CURSOR_LOCK:
        current = _channel_llm_read_cursor_locked()
        if last_id <= current:
            return
        _CHANNEL_LLM_CURSOR_LAST_ID = last_id
        try:
            _channel_llm_write_cursor_locked(last_id)
        except Exception as exc:
            router_log("WARN", f"channel_llm_cursor_write_failed error={type(exc).__name__}: {exc}")


CIEL_RUNTIME_INTERNAL_METADATA_PREFIX = "ciel_runtime_"


def body_without_ciel_runtime_internal_metadata(body: dict[str, Any]) -> dict[str, Any]:
    """Return an upstream-safe copy with ciel-runtime private metadata removed."""
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else None
    if not metadata:
        return body
    internal_keys = [
        key
        for key in metadata
        if str(key).startswith(CIEL_RUNTIME_INTERNAL_METADATA_PREFIX)
    ]
    if not internal_keys:
        return body
    public_metadata = {
        key: value
        for key, value in metadata.items()
        if not str(key).startswith(CIEL_RUNTIME_INTERNAL_METADATA_PREFIX)
    }
    out = dict(body)
    if public_metadata:
        out["metadata"] = public_metadata
    else:
        out.pop("metadata", None)
    return out


def commit_pending_channel_delivery_cursors(
    body: dict[str, Any],
    handler: BaseHTTPRequestHandler | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not isinstance(metadata, dict):
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if not metadata:
        return
    if handler is not None:
        status = _handler_response_status(handler)
        if status is None or status < 200 or status >= 400:
            router_log(
                "INFO",
                f"channel_delivery_cursor_deferred status={status if status is not None else '-'}",
            )
            return
        if _channel_delivery_metadata(metadata) and not pending_channel_delivery_confirmed(handler):
            reason = str(getattr(handler, "_ciel_runtime_channel_delivery_reason", "unconfirmed") or "unconfirmed")
            router_log("INFO", f"channel_delivery_cursor_deferred reason={reason}")
            return
    message_cursor = _metadata_int(metadata, "ciel_runtime_channel_cursor_last_id")
    _commit_channel_llm_cursor_if_newer(message_cursor)


def _channel_stdin_wake_claim_ttl_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_CLAIM_TTL_SECONDS")
    if raw is None:
        return 300.0
    try:
        return max(5.0, min(1800.0, float(raw)))
    except (TypeError, ValueError):
        return 300.0


def channel_wake_claim_repository() -> ChannelWakeClaimRepository:
    return ChannelWakeClaimRepository(
        path=CHANNEL_STDIN_WAKE_CLAIMS_PATH,
        file_lock=_chat_messages_file_lock,
        now=time.time,
        ttl_seconds=_channel_stdin_wake_claim_ttl_seconds,
        log=router_log,
    )


def _channel_stdin_wake_claim_prompt(message_id: int) -> str:
    if message_id <= 0:
        return ""
    with _CHANNEL_STDIN_WAKE_LOCK:
        prompt = _CHANNEL_STDIN_WAKE_PROMPTS.get(message_id)
    if prompt:
        return prompt
    return channel_wake_claim_repository().prompt(message_id)


def _channel_stdin_claim_wake_prompt(message_id: int, prompt: str) -> bool:
    return channel_wake_claim_repository().claim(message_id, prompt)


def _channel_stdin_clear_wake_claim(message_id: int) -> None:
    channel_wake_claim_repository().clear(message_id)


def _channel_prompt_references_message_id(text: str, message_id: int, prompt_texts: list[str] | tuple[str, ...] | None = None) -> bool:
    prompts: list[str] = []
    if prompt_texts:
        prompts.extend(str(item) for item in prompt_texts if str(item or "").strip())
    if prompt_texts is None:
        claimed_prompt = _channel_stdin_wake_claim_prompt(message_id)
        if claimed_prompt:
            prompts.append(claimed_prompt)
    return analyze_prompt_message_reference(text, message_id, prompts)


def _channel_message_ids_already_in_request(body: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        text = anthropic_content_to_text(message.get("content"))
        if "ciel-runtime external channel message" not in text and "[external channel input]" not in text:
            continue
        ids.update(_channel_prompt_message_ids(text))
    return ids


def _channel_llm_commit_cursor_locked(last_id: int) -> None:
    global _CHANNEL_LLM_CURSOR_LAST_ID
    _CHANNEL_LLM_CURSOR_LAST_ID = last_id
    try:
        _channel_llm_write_cursor_locked(last_id)
    except Exception as exc:
        router_log("WARN", f"channel_llm_cursor_write_failed error={type(exc).__name__}: {exc}")


def _channel_llm_stdin_skip_reason(message_id: int) -> str:
    with _CHANNEL_STDIN_WAKE_LOCK:
        delivered = message_id in _CHANNEL_STDIN_WAKE_DELIVERED
    if delivered:
        return "stdin_wake_delivered"
    return "stdin_wake_claimed" if _channel_stdin_wake_claim_prompt(message_id) else ""


def body_with_pending_channel_messages(body: dict[str, Any]) -> dict[str, Any]:
    return inject_pending_channel_context(
        body,
        ChannelLlmContextServices(
            policy=ChannelLlmContextPolicy(
                wake_request=channel_llm_wake_request,
                plan_mode_active=plan_mode_active,
                delivery_mode=lambda: channel_delivery_mode(load_config()),
                ids_in_request=_channel_message_ids_already_in_request,
                scan_limit=_channel_pending_scan_limit,
                skip_reason=_channel_llm_message_skip_reason,
                stdin_skip_reason=_channel_llm_stdin_skip_reason,
            ),
            repository=ChannelLlmContextRepository(
                lock=lambda: _CHANNEL_LLM_CURSOR_LOCK,
                read_cursor=_channel_llm_read_cursor_locked,
                commit_cursor=_channel_llm_commit_cursor_locked,
                read_messages=lambda last_id, limit: read_chat_messages(last_id, None, None, limit),
                superseded_ids=_channel_superseded_message_ids,
            ),
            projection=ChannelLlmContextProjection(
                remove_wake_prompt=body_without_channel_llm_wake_prompt,
                format_prompt=format_channel_llm_batch_prompt,
            ),
            log=router_log,
        ),
    )


def _write_fd_all(fd: int, data: bytes) -> None:
    writer = getattr(fd, "write", None)
    if callable(writer):
        writer(data)
        return
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _channel_wake_enter_bytes(value: str | bytes | None = None) -> bytes:
    configured = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_ENTER") if value is None else value
    return resolve_channel_enter_bytes(configured, _channel_platform_default_enter_bytes())


def _channel_wake_input_bytes(prompt: str, enter_bytes: bytes | None = None) -> bytes:
    return build_channel_wake_input_bytes(prompt, _channel_wake_enter_bytes(enter_bytes))


def _channel_current_tmux_pane_text() -> str | None:
    pane = str(os.environ.get("TMUX_PANE") or "").strip()
    if not pane or not find_executable("tmux"):
        return None
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", pane, "-S", "-200"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        router_log(
            "WARN",
            f"channel_tmux_capture_failed pane={pane} error={type(exc).__name__}: {exc}",
        )
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""


def _codex_channel_wake_submit_retries() -> int:
    raw = os.environ.get("CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_RETRIES")
    if raw is None:
        return 4
    try:
        return max(1, min(8, int(str(raw).strip())))
    except (TypeError, ValueError):
        return 4


def _codex_channel_wake_submit_delay_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CODEX_CHANNEL_WAKE_SUBMIT_DELAY_MS")
    if raw is None:
        return 0.25
    return _bounded_delay_seconds(raw, 0.25, minimum=0.13, maximum=5.0)


def _windows_channel_startup_grace_seconds() -> float:
    """Allow an interactive Windows TUI to begin reading console input."""
    raw = os.environ.get("CIEL_RUNTIME_WINDOWS_CHANNEL_STARTUP_GRACE_MS")
    if raw is None:
        return 8.0
    return _bounded_delay_seconds(raw, 8.0, minimum=0.0, maximum=60.0)


def _write_channel_wake_prompt(
    master_fd: int,
    prompt: str,
    enter_bytes: bytes | None = None,
    *,
    submit_retry_count: int = 1,
    confirm_submit: bool = False,
    bracketed_paste: bool = False,
    submit_delay_seconds: float | None = None,
) -> None:
    delay = _channel_wake_submit_delay_seconds() if submit_delay_seconds is None else max(0.0, float(submit_delay_seconds))
    submit_bytes = _channel_wake_enter_bytes(enter_bytes)
    retry_count = max(1, min(8, int(submit_retry_count or 1)))
    injector = ChannelPromptInjector(
        sleep=time.sleep,
        retry_delay_seconds=_channel_wake_submit_retry_delay_seconds,
        snapshot=_channel_current_tmux_pane_text,
        log=router_log,
    )
    injector.inject(
        CallableInputTransport(master_fd, _write_fd_all),
        PromptInjection(
            prompt=prompt,
            policy=RuntimeInjectionPolicy(
                runtime="interactive-cli",
                clear_input=b"\x15",
                submit_input=submit_bytes,
                submit_delay_seconds=delay,
                submit_attempts=retry_count,
                confirm_submission=confirm_submit,
                bracketed_paste=bracketed_paste,
            ),
        ),
    )


_CHANNEL_TRANSCRIPT_CACHE: dict[str, Any] = {"checked_at": 0.0, "path": None}
_CHANNEL_TRANSCRIPT_SCOPE: dict[str, Any] = {
    "runtime": "",
    "started_at": 0.0,
    "codex_home": None,
}
_CHANNEL_STDIN_RECOVERY_CACHE: dict[str, Any] = {
    "checked_at": 0.0,
    "last_id": None,
    "marker": None,
    "recovered_last_id": None,
}


def _set_channel_transcript_scope(runtime: str, *, started_at: float | None = None, codex_home: Path | None = None) -> None:
    _CHANNEL_TRANSCRIPT_SCOPE["runtime"] = str(runtime or "").strip().lower()
    _CHANNEL_TRANSCRIPT_SCOPE["started_at"] = time.time() if started_at is None else float(started_at)
    _CHANNEL_TRANSCRIPT_SCOPE["codex_home"] = Path(codex_home).expanduser() if codex_home is not None else None
    _CHANNEL_TRANSCRIPT_CACHE.clear()
    _CHANNEL_TRANSCRIPT_CACHE.update({"checked_at": 0.0, "path": None})


def _channel_transcript_roots() -> tuple[tuple[Path, str], ...]:
    runtime = str(_CHANNEL_TRANSCRIPT_SCOPE.get("runtime") or "").strip().lower()
    claude_root = (HOME / ".claude" / "projects", "*/*.jsonl")
    configured_codex_home = _CHANNEL_TRANSCRIPT_SCOPE.get("codex_home")
    codex_home = Path(configured_codex_home) if isinstance(configured_codex_home, Path) else HOME / ".codex"
    codex_root = (codex_home / "sessions", "**/*.jsonl")
    if runtime == "codex":
        return (codex_root,)
    if runtime == "claude":
        return (claude_root,)
    return (claude_root, codex_root)


def _latest_claude_transcript_path(ttl_seconds: float = 2.0) -> Path | None:
    now = time.time()
    cached_at = float(_CHANNEL_TRANSCRIPT_CACHE.get("checked_at") or 0.0)
    cached_path = _CHANNEL_TRANSCRIPT_CACHE.get("path")
    if now - cached_at < ttl_seconds:
        return cached_path if isinstance(cached_path, Path) else None
    latest: Path | None = None
    latest_mtime = -1.0
    transcript_roots = _channel_transcript_roots()
    scope_started_at = float(_CHANNEL_TRANSCRIPT_SCOPE.get("started_at") or 0.0)
    for root, pattern in transcript_roots:
        try:
            paths = root.glob(pattern)
        except Exception:
            continue
        for path in paths:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            # A previous interrupted run can end with task_started and no matching
            # completion event. It must not make a newly launched idle client look
            # permanently busy and suppress external input injection.
            if scope_started_at > 0 and mtime < scope_started_at - 1.0:
                continue
            if mtime > latest_mtime:
                latest = path
                latest_mtime = mtime
    _CHANNEL_TRANSCRIPT_CACHE["checked_at"] = now
    _CHANNEL_TRANSCRIPT_CACHE["path"] = latest
    return latest


def _read_file_tail_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
            return f.read(max_bytes).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _channel_stdin_wake_state(message_id: int) -> str:
    if message_id <= 0:
        return "completed"
    path = _latest_claude_transcript_path()
    if path is None:
        return "unknown"
    text = _read_file_tail_text(path)
    if not text:
        return "unknown"
    return _channel_stdin_wake_state_from_text(message_id, text)


def _channel_stdin_wake_state_for_message(message: dict[str, Any], prompt: str | None = None) -> str:
    try:
        message_id = int(message.get("id") or 0)
    except Exception:
        message_id = 0
    if message_id <= 0:
        return "completed"
    path = _latest_claude_transcript_path()
    if path is None:
        return "unknown"
    text = _read_file_tail_text(path)
    if not text:
        return "unknown"
    prompt_candidates: list[str] = []
    if prompt:
        prompt_candidates.append(prompt)
    body = str(message.get("message") if message.get("message") is not None else "")
    if body:
        prompt_candidates.append(body)
    return _channel_stdin_wake_state_from_text(message_id, text, prompt_candidates)


def _channel_stdin_wake_queued_age_seconds_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None = None,
    *,
    now: float | None = None,
) -> float | None:
    return analyze_channel_queued_age(
        message_id,
        text,
        prompt_texts,
        channel_wake_transcript_services(),
        now=now,
    )


def _channel_stdin_wake_queued_is_stale_for_message(message: dict[str, Any], prompt: str | None = None) -> bool:
    try:
        message_id = int(message.get("id") or 0)
    except Exception:
        message_id = 0
    if message_id <= 0:
        return False
    path = _latest_claude_transcript_path()
    if path is None:
        return False
    text = _read_file_tail_text(path)
    if not text:
        return False
    prompt_candidates: list[str] = []
    if prompt:
        prompt_candidates.append(prompt)
    body = str(message.get("message") if message.get("message") is not None else "")
    if body:
        prompt_candidates.append(body)
    age = _channel_stdin_wake_queued_age_seconds_from_text(message_id, text, prompt_candidates)
    return age is not None and age >= _channel_stdin_inflight_stale_seconds()


def _channel_stdin_wake_state_from_text(
    message_id: int,
    text: str,
    prompt_texts: list[str] | tuple[str, ...] | None = None,
) -> str:
    return analyze_channel_wake_state(
        message_id, text, prompt_texts, channel_wake_transcript_services()
    )


def _channel_stdin_active_tool_call() -> bool:
    path = _latest_claude_transcript_path()
    if path is None:
        return False
    text = _read_file_tail_text(path)
    if not text:
        return False
    return _channel_stdin_active_tool_call_from_text(text)


def _channel_stdin_active_turn() -> bool:
    path = _latest_claude_transcript_path()
    if path is None:
        return False
    text = _read_file_tail_text(path)
    if not text:
        return False
    return _channel_stdin_active_turn_from_text(text)


def _channel_stdin_wake_completed(message_id: int) -> bool:
    return _channel_stdin_wake_state(message_id) == "completed"


def _channel_stdin_queued_command_ids_from_text(text: str) -> set[int]:
    return analyze_channel_queued_ids(text, channel_wake_transcript_services())


def channel_wake_transcript_services() -> ChannelWakeTranscriptServices:
    return ChannelWakeTranscriptServices(
        claim_prompt=_channel_stdin_wake_claim_prompt,
        prompt_references_message_id=_channel_prompt_references_message_id,
        prompt_message_ids=_channel_prompt_message_ids,
        now=time.time,
    )


def _channel_stdin_recover_cursor_from_queued_only(last_id: int) -> int:
    if last_id <= 0:
        return last_id
    path = _latest_claude_transcript_path()
    if path is None:
        return last_id
    try:
        stat = path.stat()
        marker = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return last_id
    now = time.time()
    cached_marker = _CHANNEL_STDIN_RECOVERY_CACHE.get("marker")
    if (
        _CHANNEL_STDIN_RECOVERY_CACHE.get("last_id") == last_id
        and cached_marker == marker
        and now - float(_CHANNEL_STDIN_RECOVERY_CACHE.get("checked_at") or 0.0) < 5.0
    ):
        cached = _CHANNEL_STDIN_RECOVERY_CACHE.get("recovered_last_id")
        recovered = int(cached) if isinstance(cached, int) else last_id
        return _channel_llm_clamp_to_clear_floor(recovered)
    text = _read_file_tail_text(path, max_bytes=8 * 1024 * 1024)
    recovered = last_id
    if text:
        for message_id in sorted(_channel_stdin_queued_command_ids_from_text(text)):
            if message_id > last_id:
                continue
            if _channel_stdin_wake_state_from_text(message_id, text) == "missing":
                recovered = max(0, message_id - 1)
                router_log(
                    "WARN",
                    f"channel_stdin_proxy_recover_queued_only message_id={message_id} cursor={last_id} recovered_cursor={recovered}",
                )
                break
    _CHANNEL_STDIN_RECOVERY_CACHE.update(
        {
            "checked_at": now,
            "last_id": last_id,
            "marker": marker,
            "recovered_last_id": recovered,
        }
    )
    return _channel_llm_clamp_to_clear_floor(recovered)


def _channel_stdin_unseen_retry_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_UNSEEN_RETRY_SECONDS")
    if raw is None:
        return 20.0
    try:
        return max(2.0, min(300.0, float(raw)))
    except (TypeError, ValueError):
        return 20.0


def _channel_stdin_inflight_stale_seconds() -> float:
    raw = os.environ.get("CIEL_RUNTIME_CHANNEL_WAKE_INFLIGHT_STALE_SECONDS")
    if raw is None:
        return 180.0
    try:
        return max(30.0, min(1800.0, float(raw)))
    except (TypeError, ValueError):
        return 180.0


def _channel_stdin_inflight_is_stale(state: str, started_at: float, now: float | None = None) -> bool:
    if state not in {"queued", "unknown"} or started_at <= 0:
        return False
    current = time.time() if now is None else float(now)
    return current - started_at >= _channel_stdin_inflight_stale_seconds()


def _channel_stdin_should_check_pending(
    marker: tuple[float, int],
    last_marker: tuple[float, int],
    force_recheck: bool,
    channel_inflight_id: int | None,
) -> bool:
    return force_recheck or marker != last_marker


def _channel_wake_store_release_stale(message_id: int, commit_cursor: bool) -> None:
    with _CHANNEL_STDIN_WAKE_LOCK:
        _CHANNEL_STDIN_WAKE_DELIVERED.discard(message_id)
        _CHANNEL_STDIN_WAKE_PROMPTS.pop(message_id, None)
    _channel_stdin_clear_wake_claim(message_id)
    if commit_cursor:
        _commit_channel_llm_cursor_if_newer(message_id)


def _channel_inflight_complete_wake(message_id: int) -> None:
    with _CHANNEL_STDIN_WAKE_LOCK:
        _CHANNEL_STDIN_WAKE_PROMPTS.pop(message_id, None)
    _channel_stdin_clear_wake_claim(message_id)


def _channel_inflight_release_wake(message_id: int) -> None:
    _channel_wake_store_release_stale(message_id, False)


def channel_inflight_effects() -> ChannelInflightEffects:
    return ChannelInflightEffects(
        commit_cursor=_commit_channel_llm_cursor_if_newer,
        complete_wake=_channel_inflight_complete_wake,
        release_wake=_channel_inflight_release_wake,
        ensure_cursor=ensure_channel_llm_delivery_cursor_initialized,
        log=router_log,
    )


def _channel_wake_store_mark_delivered(message_id: int) -> bool:
    with _CHANNEL_STDIN_WAKE_LOCK:
        if message_id in _CHANNEL_STDIN_WAKE_DELIVERED:
            return False
        _CHANNEL_STDIN_WAKE_DELIVERED.add(message_id)
        if len(_CHANNEL_STDIN_WAKE_DELIVERED) > 1000:
            for old_id in sorted(_CHANNEL_STDIN_WAKE_DELIVERED)[:500]:
                _CHANNEL_STDIN_WAKE_DELIVERED.discard(old_id)
    return True


def _channel_wake_store_record_prompts(messages: list[dict[str, Any]], prompt: str) -> None:
    with _CHANNEL_STDIN_WAKE_LOCK:
        for message in messages:
            try:
                message_id = int(message.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if message_id > 0:
                _CHANNEL_STDIN_WAKE_PROMPTS[message_id] = prompt
        if len(_CHANNEL_STDIN_WAKE_PROMPTS) > 1000:
            for old_id in sorted(_CHANNEL_STDIN_WAKE_PROMPTS)[:500]:
                _CHANNEL_STDIN_WAKE_PROMPTS.pop(old_id, None)


def _channel_wake_store_rollback(messages: list[dict[str, Any]], claimed_ids: list[int]) -> None:
    with _CHANNEL_STDIN_WAKE_LOCK:
        for message in messages:
            try:
                message_id = int(message.get("id") or 0)
            except (TypeError, ValueError):
                continue
            _CHANNEL_STDIN_WAKE_DELIVERED.discard(message_id)
            _CHANNEL_STDIN_WAKE_PROMPTS.pop(message_id, None)
            _channel_stdin_clear_wake_claim(message_id)
    for message_id in claimed_ids:
        _channel_stdin_clear_wake_claim(message_id)


def pending_channel_injection_services() -> ChannelInjectionServices:
    return ChannelInjectionServices(
        state=ChannelInjectionState(
            active_tool_call=_channel_stdin_active_tool_call,
            active_turn=_channel_stdin_active_turn,
            recover_cursor=_channel_stdin_recover_cursor_from_queued_only,
            pending_scan_limit=_channel_pending_scan_limit,
            superseded_ids=_channel_superseded_message_ids,
            message_is_web_chat=_channel_message_is_web_chat_request,
            message_skip_reason=_channel_llm_message_skip_reason,
            event_identity_key=_channel_message_event_identity_key,
            wake_state_for_message=_channel_stdin_wake_state_for_message,
            queued_wake_is_stale=_channel_stdin_wake_queued_is_stale_for_message,
        ),
        prompts=ChannelInjectionPrompts(
            llm_delivery=format_channel_llm_delivery_wake_prompt,
            web_chat=format_channel_web_chat_wake_batch_prompt,
            standard=format_channel_wake_batch_prompt,
            enter_bytes=_channel_wake_enter_bytes,
            enter_label=_channel_enter_label,
        ),
        wake_store=ChannelInjectionWakeStore(
            claim_for_nonblocking_scan=_channel_stdin_wake_claim_prompt,
            claim_prompt=_channel_stdin_claim_wake_prompt,
            clear_claim=_channel_stdin_clear_wake_claim,
            release_stale=_channel_wake_store_release_stale,
            mark_delivered=_channel_wake_store_mark_delivered,
            record_prompts=_channel_wake_store_record_prompts,
            rollback=_channel_wake_store_rollback,
            commit_cursor=_commit_channel_llm_cursor_if_newer,
        ),
        io=ChannelInjectionIO(
            inject_lock=_CHANNEL_STDIN_INJECT_LOCK,
            read_messages=read_chat_messages,
            write_prompt=_write_channel_wake_prompt,
            log=router_log,
        ),
        policy=ChannelInjectionPolicy(wake_batch_limit=_channel_stdin_wake_batch_limit),
    )


def _inject_pending_channel_messages(
    master_fd: int,
    last_id: int,
    enter_bytes: bytes | None = None,
    *,
    web_chat_only: bool = False,
    wake_for_llm_delivery: bool = False,
    commit_cursor: bool = True,
    injected_message_ids: list[int] | None = None,
    submit_retry_count: int = 1,
    confirm_submit: bool = False,
    bracketed_paste: bool = False,
    submit_delay_seconds: float | None = None,
    skip_blocking_wake_states: bool = False,
) -> int:
    return run_pending_channel_injection(
        master_fd,
        last_id,
        enter_bytes,
        web_chat_only=web_chat_only,
        wake_for_llm_delivery=wake_for_llm_delivery,
        commit_cursor=commit_cursor,
        injected_message_ids=injected_message_ids,
        submit_retry_count=submit_retry_count,
        confirm_submit=confirm_submit,
        bracketed_paste=bracketed_paste,
        submit_delay_seconds=submit_delay_seconds,
        skip_blocking_wake_states=skip_blocking_wake_states,
        services=pending_channel_injection_services(),
    )


def _inject_pending_compact_request(
    master_fd: int,
    enter_bytes: bytes | None = None,
    *,
    log_defer: bool = True,
    submit_retry_count: int = 1,
    confirm_submit: bool = False,
    bracketed_paste: bool = False,
    submit_delay_seconds: float | None = None,
) -> str:
    request = _read_channel_compact_request()
    if not request:
        return "none"
    request_id = str(request.get("id") or "")
    if _channel_stdin_active_tool_call():
        if log_defer:
            router_log("INFO", f"channel_compact_request_deferred id={request_id or '-'} reason=active_tool_call")
        return "deferred"
    if _channel_stdin_active_turn():
        if log_defer:
            router_log("INFO", f"channel_compact_request_deferred id={request_id or '-'} reason=active_turn")
        return "deferred"
    command = str(request.get("command") or "/compact").strip() or "/compact"
    if command != "/compact":
        command = "/compact"
    submit_bytes = _channel_wake_enter_bytes(enter_bytes)
    _write_channel_wake_prompt(
        master_fd,
        command,
        submit_bytes,
        submit_retry_count=submit_retry_count,
        confirm_submit=confirm_submit,
        bracketed_paste=bracketed_paste,
        submit_delay_seconds=submit_delay_seconds,
    )
    _clear_channel_compact_request(request_id or None)
    router_log(
        "INFO",
        f"channel_compact_request_injected id={request_id or '-'} enter={_channel_enter_label(submit_bytes)}",
    )
    return "injected"


def _chat_messages_file_marker() -> tuple[float, int]:
    try:
        stat = CHAT_MESSAGES_PATH.stat()
        return (stat.st_mtime, stat.st_size)
    except Exception:
        return (0.0, 0)


def _terminal_winsize_from_fd(fd: int) -> tuple[int, int]:
    """Return terminal size as (rows, columns), never 0x0."""
    try:
        size = os.get_terminal_size(fd)
        rows = int(size.lines)
        cols = int(size.columns)
    except Exception:
        rows = 0
        cols = 0
    if rows > 0 and cols > 0:
        return rows, cols
    fallback = shutil.get_terminal_size((80, 24))
    rows = int(getattr(fallback, "lines", 0) or 0)
    cols = int(getattr(fallback, "columns", 0) or 0)
    if rows <= 0:
        rows = 24
    if cols <= 0:
        cols = 80
    return rows, cols


def _apply_pty_winsize(pty_fd: int, rows: int, cols: int) -> bool:
    if os.name != "posix" or rows <= 0 or cols <= 0:
        return False
    try:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(pty_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        return True
    except Exception:
        return False


TERMINAL_INPUT_MODE_RESET = "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1004l\x1b[?1005l\x1b[?1006l\x1b[?1015l"


def _terminal_input_mode_reset_enabled() -> bool:
    # On Windows cmd/conhost, DECSET mouse reset sequences can be printed
    # literally. The Windows launch path uses SetConsoleMode instead.
    if os.name == "nt" and os.environ.get("CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET") is None:
        return False
    return parse_bool(os.environ.get("CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET"), True)


def _terminal_input_mode_reset_interval_seconds(default: float = 2.0) -> float:
    raw = os.environ.get("CIEL_RUNTIME_TERMINAL_INPUT_MODE_RESET_INTERVAL_SECONDS")
    if raw is None:
        return default
    try:
        return max(0.25, min(60.0, float(raw)))
    except Exception:
        return default


def _write_terminal_input_mode_reset(stream: Any | None = None) -> None:
    if not _terminal_input_mode_reset_enabled():
        return
    target = stream if stream is not None else sys.stdout
    try:
        if hasattr(target, "isatty") and not target.isatty():
            return
        target.write(TERMINAL_INPUT_MODE_RESET)
        target.flush()
    except Exception:
        return


class _TerminalMouseInputFilter:
    """Strip terminal mouse reports that can leak into TUI prompt buffers."""

    def __init__(self) -> None:
        self._pending = b""

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
        buf = self._pending + data
        self._pending = b""
        out = bytearray()
        i = 0
        while i < len(buf):
            if buf[i] != 0x1B:
                out.append(buf[i])
                i += 1
                continue
            if i + 1 >= len(buf):
                self._pending = buf[i:]
                break
            if buf[i + 1] != ord("["):
                out.append(buf[i])
                i += 1
                continue
            if i + 2 >= len(buf):
                self._pending = buf[i:]
                break
            marker = buf[i + 2]
            if marker == ord("<"):
                j = i + 3
                while j < len(buf) and (48 <= buf[j] <= 57 or buf[j] == ord(";")):
                    j += 1
                if j >= len(buf):
                    self._pending = buf[i:]
                    break
                if j > i + 3 and buf[j] in (ord("M"), ord("m")):
                    i = j + 1
                    continue
                out.append(buf[i])
                i += 1
                continue
            if marker == ord("M"):
                if i + 6 <= len(buf):
                    i += 6
                    continue
                self._pending = buf[i:]
                break
            if 48 <= marker <= 57:
                j = i + 2
                semicolons = 0
                while j < len(buf) and (48 <= buf[j] <= 57 or buf[j] == ord(";")):
                    if buf[j] == ord(";"):
                        semicolons += 1
                    j += 1
                if j >= len(buf):
                    self._pending = buf[i:]
                    break
                if semicolons >= 2 and buf[j] in (ord("M"), ord("m")):
                    i = j + 1
                    continue
                out.append(buf[i])
                i += 1
                continue
            out.append(buf[i])
            i += 1
        return bytes(out)

    def flush(self) -> bytes:
        pending = self._pending
        self._pending = b""
        return pending


def _strip_terminal_mouse_input_reports(data: bytes) -> bytes:
    filt = _TerminalMouseInputFilter()
    return filt.feed(data) + filt.flush()


_WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE: Any = None


def _windows_console_input_handle() -> Any:
    global _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF))
        handle_value = int(handle) if isinstance(handle, int) else int(getattr(handle, "value", 0) or 0)
        invalid_handle = int(ctypes.c_void_p(-1).value or -1)
        if handle_value and handle_value != invalid_handle:
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return handle

        cached = _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE
        cached_value = int(cached) if isinstance(cached, int) else int(getattr(cached, "value", 0) or 0)
        if cached_value and cached_value != invalid_handle:
            mode = wintypes.DWORD(0)
            if kernel32.GetConsoleMode(cached, ctypes.byref(mode)):
                return cached

        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        console_handle = kernel32.CreateFileW(
            "CONIN$",
            0x80000000 | 0x40000000,
            0x00000001 | 0x00000002,
            None,
            3,
            0,
            None,
        )
        console_value = (
            int(console_handle)
            if isinstance(console_handle, int)
            else int(getattr(console_handle, "value", 0) or 0)
        )
        if not console_value or console_value == invalid_handle:
            return None
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(console_handle, ctypes.byref(mode)):
            return None
        _WINDOWS_CONSOLE_INPUT_FALLBACK_HANDLE = console_handle
        return console_handle
    except Exception:
        return None


def _windows_console_input_supported() -> bool:
    # Python's isatty() can be false through npm.cmd -> py.exe launch chains even
    # when the process is attached to a real Windows console. GetConsoleMode is
    # the authoritative check and rejects redirected pipe/file handles.
    return _windows_console_input_mode() is not None


def _windows_console_mouse_input_filter_enabled() -> bool:
    return parse_bool(os.environ.get("CIEL_RUNTIME_WINDOWS_CONSOLE_MOUSE_FILTER"), True)


def _windows_console_input_mode() -> int | None:
    handle = _windows_console_input_handle()
    if handle is None:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        mode = wintypes.DWORD(0)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        ok = kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        if not ok:
            return None
        return int(mode.value)
    except Exception:
        return None


def _set_windows_console_input_mode(mode: int) -> bool:
    handle = _windows_console_input_handle()
    if handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL
        ok = kernel32.SetConsoleMode(handle, wintypes.DWORD(int(mode)))
        return bool(ok)
    except Exception:
        return False


class _WindowsConsoleMouseInputGuard:
    ENABLE_MOUSE_INPUT = 0x0010

    def __init__(self) -> None:
        self.original_mode: int | None = None

    def apply(self) -> None:
        if os.name != "nt" or not _windows_console_mouse_input_filter_enabled():
            return
        current = _windows_console_input_mode()
        if current is None:
            return
        if self.original_mode is None:
            self.original_mode = current
        filtered = current & ~self.ENABLE_MOUSE_INPUT
        if filtered != current and _set_windows_console_input_mode(filtered):
            router_log("INFO", f"windows_console_mouse_input_disabled mode={current:#x}->{filtered:#x}")

    def restore(self) -> None:
        if os.name != "nt" or self.original_mode is None:
            return
        _set_windows_console_input_mode(self.original_mode)


def _windows_console_utf16_units(chars: Iterable[str]) -> list[str]:
    return project_windows_console_utf16_units(chars)


class _WindowsConsoleInputWriter(WindowsConsoleInputWriter):
    def __init__(self) -> None:
        super().__init__(_windows_console_input_handle, _TerminalMouseInputFilter)


def _retry_windows_console_channel_submit(
    writer: Any,
    enter_bytes: bytes,
    state: str,
    attempts: int,
    retry_count: int,
    last_attempt_at: float,
    now: float,
    *,
    confirm_submit: bool,
    turn_active: bool = False,
) -> tuple[int, float]:
    if not confirm_submit or turn_active or state != "missing" or attempts >= retry_count:
        return attempts, last_attempt_at
    if now - last_attempt_at < _channel_wake_submit_retry_delay_seconds():
        return attempts, last_attempt_at
    _write_fd_all(writer, enter_bytes)
    next_attempt = attempts + 1
    router_log("INFO", f"channel_windows_console_submit_retry attempt={next_attempt}/{retry_count}")
    return next_attempt, now


def subprocess_call_with_windows_console_wake_proxy(
    cmd: list[str],
    env: dict[str, str],
    *,
    inject_channel_messages: bool = True,
    inject_web_chat_only: bool = False,
    wake_for_llm_delivery: bool = False,
    synthetic_enter_bytes: str | bytes | None = None,
    normalize_bare_cr_for_synthetic_enter: bool = True,
    channel_wake_submit_retries: int = 1,
    channel_wake_confirm_submit: bool = False,
    channel_wake_bracketed_paste: bool = False,
    channel_wake_submit_delay_seconds: float | None = None,
    tracked_child_pid_path: Path | None = None,
) -> int:
    del normalize_bare_cr_for_synthetic_enter, channel_wake_bracketed_paste
    return run_windows_channel_terminal_proxy(
        cmd,
        env,
        build_channel_windows_services(),
        inject_channel_messages=inject_channel_messages,
        inject_web_chat_only=inject_web_chat_only,
        wake_for_llm_delivery=wake_for_llm_delivery,
        synthetic_enter_bytes=synthetic_enter_bytes,
        channel_wake_submit_retries=channel_wake_submit_retries,
        channel_wake_confirm_submit=channel_wake_confirm_submit,
        channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
        tracked_child_pid_path=tracked_child_pid_path,
    )


def build_channel_terminal_process() -> ChannelTerminalProcess:
    return ChannelTerminalProcess(
        popen=subprocess.Popen,
        write_child_record=_write_codex_child_process_record,
        terminate_child=_terminate_recorded_child_process,
        release_child_record=_release_codex_child_process_record,
    )


def build_channel_terminal_policy() -> ChannelTerminalPolicy:
    return ChannelTerminalPolicy(
        initial_cursor=ensure_channel_llm_delivery_cursor_initialized,
        enter_bytes=_channel_wake_enter_bytes,
        enter_label=_channel_enter_label,
        enter_is_fixed=_channel_wake_enter_env_is_fixed,
        unseen_retry_seconds=_channel_stdin_unseen_retry_seconds,
        inflight_is_stale=_channel_stdin_inflight_is_stale,
        log=router_log,
    )


def build_channel_terminal_polling() -> ChannelTerminalPolling:
    return ChannelTerminalPolling(
        inject_compact=_inject_pending_compact_request,
        file_marker=_chat_messages_file_marker,
        should_check=_channel_stdin_should_check_pending,
        active_tool_call=_channel_stdin_active_tool_call,
        inject_pending=_inject_pending_channel_messages,
        wake_state=_channel_stdin_wake_state,
        inflight_effects=channel_inflight_effects,
    )


def build_channel_terminal_services() -> ChannelTerminalServices:
    return ChannelTerminalServices(
        process=build_channel_terminal_process(),
        io=ChannelTerminalIO(
            terminal_size=_terminal_winsize_from_fd,
            apply_terminal_size=_apply_pty_winsize,
            write_all=_write_fd_all,
            mouse_filter=_TerminalMouseInputFilter,
            observed_enter=_channel_synthetic_enter_bytes_from_user_input,
            reset_input_mode=_write_terminal_input_mode_reset,
        ),
        policy=build_channel_terminal_policy(),
        polling=build_channel_terminal_polling(),
    )


def build_channel_windows_services() -> ChannelWindowsServices:
    return ChannelWindowsServices(
        process=build_channel_terminal_process(),
        policy=build_channel_terminal_policy(),
        polling=build_channel_terminal_polling(),
        console=ChannelWindowsConsole(
            reset_input_mode=_write_terminal_input_mode_reset,
            mouse_guard=_WindowsConsoleMouseInputGuard,
            input_writer=_WindowsConsoleInputWriter,
            startup_grace_seconds=_windows_channel_startup_grace_seconds,
            reset_interval_seconds=_terminal_input_mode_reset_interval_seconds,
            active_turn=_channel_stdin_active_turn,
            retry_submit=_retry_windows_console_channel_submit,
            sleep=time.sleep,
        ),
    )


def subprocess_call_with_channel_wake_proxy(
    cmd: list[str],
    env: dict[str, str],
    *,
    inject_channel_messages: bool = True,
    inject_web_chat_only: bool = False,
    wake_for_llm_delivery: bool = False,
    synthetic_enter_bytes: str | bytes | None = None,
    normalize_bare_cr_for_synthetic_enter: bool = True,
    channel_wake_submit_retries: int = 1,
    channel_wake_confirm_submit: bool = False,
    channel_wake_bracketed_paste: bool = False,
    channel_wake_submit_delay_seconds: float | None = None,
    tracked_child_pid_path: Path | None = None,
) -> int:
    if os.name == "nt" and _windows_console_input_supported():
        try:
            return subprocess_call_with_windows_console_wake_proxy(
                cmd,
                env,
                inject_channel_messages=inject_channel_messages,
                inject_web_chat_only=inject_web_chat_only,
                wake_for_llm_delivery=wake_for_llm_delivery,
                synthetic_enter_bytes=synthetic_enter_bytes,
                normalize_bare_cr_for_synthetic_enter=normalize_bare_cr_for_synthetic_enter,
                channel_wake_submit_retries=channel_wake_submit_retries,
                channel_wake_confirm_submit=channel_wake_confirm_submit,
                channel_wake_bracketed_paste=channel_wake_bracketed_paste,
                channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
                tracked_child_pid_path=tracked_child_pid_path,
            )
        except Exception as exc:
            router_log(
                "WARN",
                f"channel_windows_console_proxy_failed error={type(exc).__name__}: {exc}; using direct subprocess call",
            )
    if os.name != "posix" or not sys.stdin.isatty() or not sys.stdout.isatty():
        router_log("INFO", "channel_stdin_proxy_unavailable; using direct subprocess call")
        if tracked_child_pid_path is None:
            return subprocess.call(cmd, env=env)
        proc = subprocess.Popen(cmd, env=env)
        _write_codex_child_process_record(tracked_child_pid_path, proc.pid, cmd)
        try:
            return proc.wait()
        finally:
            _terminate_recorded_child_process(proc, "current Codex")
            _release_codex_child_process_record(tracked_child_pid_path, proc.pid)
    return run_posix_channel_terminal_proxy(
        cmd,
        env,
        build_channel_terminal_services(),
        inject_channel_messages=inject_channel_messages,
        inject_web_chat_only=inject_web_chat_only,
        wake_for_llm_delivery=wake_for_llm_delivery,
        synthetic_enter_bytes=synthetic_enter_bytes,
        normalize_bare_cr_for_synthetic_enter=normalize_bare_cr_for_synthetic_enter,
        channel_wake_submit_retries=channel_wake_submit_retries,
        channel_wake_confirm_submit=channel_wake_confirm_submit,
        channel_wake_bracketed_paste=channel_wake_bracketed_paste,
        channel_wake_submit_delay_seconds=channel_wake_submit_delay_seconds,
        tracked_child_pid_path=tracked_child_pid_path,
    )


def subprocess_call_with_child_pid_record(cmd: list[str], env: dict[str, str], pid_path: Path | None = None) -> int:
    if pid_path is None:
        return subprocess.call(cmd, env=env)
    proc = subprocess.Popen(cmd, env=env)
    _write_codex_child_process_record(pid_path, proc.pid, cmd)
    try:
        return proc.wait()
    finally:
        _terminate_recorded_child_process(proc, "current Codex")
        _release_codex_child_process_record(pid_path, proc.pid)


def _mcp_proxy_notification_payload(server_name: str, message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "").strip()
    if not method.startswith("notifications/"):
        return None
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    payload = params.get("payload") if isinstance(params.get("payload"), dict) else {}
    data = params.get("data") if isinstance(params.get("data"), dict) else {}
    event = params.get("event") if isinstance(params.get("event"), dict) else {}
    meta: dict[str, Any] = {
        "mcp_server": server_name,
        "mcp_method": method,
        "mcp_json": _json_safe_metadata(message),
    }
    if message.get("jsonrpc") is not None:
        meta["jsonrpc"] = message.get("jsonrpc")
    if message.get("id") is not None:
        meta["rpc_id"] = message.get("id")
    meta.update(_event_meta_from_sources(message, params, payload, data, event))
    content = (
        _event_payload_text(params)
        or _event_payload_text(payload)
        or _event_payload_text(data)
        or _event_payload_text(event)
    )
    if not content and params:
        content = json.dumps(params, ensure_ascii=False, separators=(",", ":"), default=str)
    if not content:
        return None
    raw_message = _pretty_json_value(message)
    channel = str(meta.get("channel") or meta.get("room_id") or meta.get("room") or server_name)
    return {
        "channel": channel,
        "sender_id": str(meta.get("sender_id") or meta.get("agent_id") or server_name),
        "recipients": meta.get("recipient_id") or "all",
        "thread_id": meta.get("thread_id"),
        "parent_id": meta.get("parent_id"),
        "kind": method.replace("notifications/claude/", "").replace("notifications/", "").replace("/", "."),
        "message": raw_message,
        "meta": meta,
    }


def _mcp_proxy_stable_event_identity(chat_payload: dict[str, Any]) -> tuple[str, str] | None:
    meta = chat_payload.get("meta") if isinstance(chat_payload.get("meta"), dict) else {}
    for key in (
        "stream_id",
        "sse_id",
        "message_id",
        "source_message_id",
        "event_id",
        "cursor",
        "assignment_id",
        "poll_id",
        "task_id",
        "sequence",
        "seq",
    ):
        value = meta.get(key)
        if value is not None and str(value).strip():
            return key, str(value).strip()
    return None


def _mcp_proxy_notification_dedupe_key(server_name: str, chat_payload: dict[str, Any]) -> tuple[str, bool]:
    meta = chat_payload.get("meta") if isinstance(chat_payload.get("meta"), dict) else {}
    body_source = (
        _notification_semantic_text_from_envelope(meta.get("mcp_json"))
        or _notification_semantic_text_from_envelope(meta.get("sse_json"))
        or str(chat_payload.get("message") or "")
    )
    body = re.sub(r"\s+", " ", body_source).strip()
    room = str(meta.get("room_id") or meta.get("room") or chat_payload.get("channel") or server_name)
    kind = str(meta.get("kind") or chat_payload.get("kind") or "")
    stable_identity = _mcp_proxy_stable_event_identity(chat_payload)
    if stable_identity:
        stable_key, stable_value = stable_identity
        return (
            json.dumps(
                ["stable", room, kind, stable_key, stable_value],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            True,
        )
    sender = str(chat_payload.get("sender_id") or meta.get("sender_id") or meta.get("agent_id") or server_name)
    thread = str(chat_payload.get("thread_id") or meta.get("thread_id") or "")
    parent = str(chat_payload.get("parent_id") or meta.get("parent_id") or "")
    return json.dumps(
        [server_name, room, sender, thread, parent, body],
        ensure_ascii=False,
        separators=(",", ":"),
    ), False


def _mcp_proxy_should_skip_duplicate_notification(server_name: str, chat_payload: dict[str, Any]) -> tuple[bool, str | None]:
    meta = chat_payload.get("meta") if isinstance(chat_payload.get("meta"), dict) else {}
    method = str(meta.get("mcp_method") or "")
    if not method.startswith("notifications/"):
        return False, None
    key, has_stable_identity = _mcp_proxy_notification_dedupe_key(server_name, chat_payload)
    now = time.time()
    with _MCP_NOTIFICATION_DEDUP_LOCK:
        stale = [
            item_key
            for item_key, (_, seen_at) in _MCP_NOTIFICATION_DEDUP_RECENT.items()
            if now - seen_at > _MCP_NOTIFICATION_DEDUP_TTL_SECONDS
        ]
        for item_key in stale:
            _MCP_NOTIFICATION_DEDUP_RECENT.pop(item_key, None)
        previous = _MCP_NOTIFICATION_DEDUP_RECENT.get(key)
        _MCP_NOTIFICATION_DEDUP_RECENT[key] = (method, now)
    if not previous:
        return False, None
    previous_method, previous_seen_at = previous
    if has_stable_identity and now - previous_seen_at <= _MCP_NOTIFICATION_DEDUP_TTL_SECONDS:
        return True, previous_method
    is_native_pair = _NATIVE_CHANNEL_NOTIFICATION_METHOD in {previous_method, method}
    if previous_method != method and is_native_pair and now - previous_seen_at <= _MCP_NOTIFICATION_DEDUP_TTL_SECONDS:
        return True, previous_method
    return False, None


def _mcp_proxy_observe_json_message(server_name: str, payload: Any, *, schedule_direct: bool = True) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    chat_payload = _mcp_proxy_notification_payload(server_name, payload)
    if not chat_payload:
        return None
    skip_duplicate, previous_method = _mcp_proxy_should_skip_duplicate_notification(server_name, chat_payload)
    if skip_duplicate:
        router_log(
            "INFO",
            f"mcp_proxy_notification_skipped_duplicate server={server_name} method={payload.get('method')} previous_method={previous_method}",
        )
        return None
    try:
        saved = append_chat_message(chat_payload)
        if saved.get("_ciel_runtime_duplicate"):
            router_log(
                "INFO",
                f"mcp_proxy_notification_skipped_duplicate_persisted server={server_name} method={payload.get('method')} existing_id={saved.get('id')}",
            )
            return saved
        router_log(
            "INFO",
            f"mcp_proxy_notification server={server_name} method={payload.get('method')} message_id={saved.get('id')}",
        )
        return saved
    except Exception as exc:
        router_log("WARN", f"mcp_proxy_notification_failed server={server_name} error={type(exc).__name__}: {exc}")
    return None


class _McpStdoutObserver(McpStdoutObserver):
    def __init__(self, server_name: str) -> None:
        super().__init__(server_name, _mcp_proxy_observe_json_message)


def _mcp_proxy_forward_stdin(proc: subprocess.Popen[bytes]) -> None:
    proxy_forward_stdin(proc, log=router_log)


def _mcp_proxy_forward_stdin_jsonl(proc: subprocess.Popen[bytes]) -> None:
    proxy_forward_stdin_jsonl(proc, log=router_log)


def _mcp_proxy_forward_stdout_jsonl(server_name: str, proc: subprocess.Popen[bytes]) -> None:
    proxy_forward_stdout_jsonl(
        server_name,
        proc,
        observe_json_message=_mcp_proxy_observe_json_message,
        log=router_log,
    )


def _mcp_proxy_forward_stderr(proc: subprocess.Popen[bytes]) -> None:
    proxy_forward_stderr(proc, log=router_log)


def _mcp_proxy_streamable_http_request(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    protocol_version: str,
    session_id: str | None,
) -> tuple[Any, str | None]:
    return proxy_streamable_http_request(
        endpoint,
        headers,
        payload,
        timeout,
        protocol_version,
        session_id,
        post_json=_mcp_streamable_post_json,
    )


_MCP_PROXY_CODEC_POLICY = McpProxyCodecPolicy(
    default_tool_result_max_chars=MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT,
    item_text_chars=MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS,
    positive_env_int=positive_env_int,
    router_log=router_log,
    tool_leaf_name=_mcp_tool_leaf_name,
    truncate_for_prompt=truncate_for_prompt,
)


def _mcp_proxy_compact_tool_result_response(
    server_name: str, tool_name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    return compact_mcp_tool_result_response(
        server_name,
        tool_name,
        payload,
        policy=_MCP_PROXY_CODEC_POLICY,
    )




def mcp_http_proxy_services() -> McpHttpProxyServices:
    return McpHttpProxyServices(
        codec=McpHttpProxyCodec(
            compact_tool_result_response=_mcp_proxy_compact_tool_result_response,
            drain_input_messages=_mcp_proxy_drain_input_messages,
            error_response=_mcp_proxy_error_response,
            notification_payload=_mcp_proxy_notification_payload,
            notification_wait_response=_mcp_proxy_notification_wait_response,
            observe_json_message=_mcp_proxy_observe_json_message,
            tool_call_arguments=_mcp_proxy_tool_call_arguments,
            tool_call_name=_mcp_proxy_tool_call_name,
            tool_is_notification_wait=_mcp_proxy_tool_is_notification_wait,
            wait_timeout_seconds=_mcp_proxy_wait_timeout_seconds,
        ),
        transport=McpHttpProxyTransport(
            http_error_body_text=_http_error_body_text,
            session_not_found=_streamable_http_session_not_found,
            stream_read_timeout_error=_mcp_stream_read_timeout_error,
            streamable_headers=_mcp_streamable_headers,
            streamable_http_request=_mcp_proxy_streamable_http_request,
        ),
        runtime=McpHttpProxyRuntime(
            default_protocol_version=MCP_STREAMABLE_HTTP_PROTOCOL_VERSION,
            disable_notification_stream=_mcp_server_disable_proxy_notification_stream,
            is_streamable_http=_mcp_server_is_streamable_http,
            json_safe_metadata=_json_safe_metadata,
            log=router_log,
            parse_bool=parse_bool,
            server_runtime_headers=mcp_server_runtime_headers,
            write_json_response=_mcp_proxy_write_json_response,
        ),
    )


def run_mcp_streamable_http_proxy(server_name: str, server_config_path: Path) -> int:
    return run_streamable_http_mcp_proxy(
        server_name,
        server_config_path,
        services=mcp_http_proxy_services(),
    )


def run_mcp_stdio_proxy(server_name: str, server_config_path: Path) -> int:
    try:
        server = json.loads(server_config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        router_log("ERROR", f"mcp_proxy_config_read_failed server={server_name} error={type(exc).__name__}: {exc}")
        print(f"ciel-runtime mcp-proxy: cannot read server config: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    if not isinstance(server, dict) or not _mcp_server_is_stdio(server):
        router_log("ERROR", f"mcp_proxy_invalid_config server={server_name}")
        print("ciel-runtime mcp-proxy: server config is not a stdio MCP server", file=sys.stderr, flush=True)
        return 2
    command = str(server.get("command") or "").strip()
    args = [str(item) for item in server.get("args", [])] if isinstance(server.get("args"), list) else []
    command, args = resolve_mcp_server_process(command, args)
    env = os.environ.copy()
    raw_env = server.get("env")
    if isinstance(raw_env, dict):
        env.update({str(k): str(v) for k, v in raw_env.items() if str(k)})
    cwd_value = server.get("cwd") or server.get("workingDirectory")
    cwd = str(cwd_value) if cwd_value else None
    try:
        proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            bufsize=0,
        )
    except Exception as exc:
        router_log("ERROR", f"mcp_proxy_start_failed server={server_name} command={command} error={type(exc).__name__}: {exc}")
        print(f"ciel-runtime mcp-proxy: failed to start {command}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 127
    stdio_mode = _mcp_proxy_stdio_mode(server)
    router_log("INFO", f"mcp_proxy_started server={server_name} command={command} stdio={stdio_mode}")
    stdin_target = _mcp_proxy_forward_stdin_jsonl if stdio_mode == "jsonl" else _mcp_proxy_forward_stdin
    threading.Thread(target=stdin_target, args=(proc,), daemon=True, name=f"mcp-proxy-stdin-{server_name}").start()
    threading.Thread(target=_mcp_proxy_forward_stderr, args=(proc,), daemon=True, name=f"mcp-proxy-stderr-{server_name}").start()
    try:
        if stdio_mode == "jsonl":
            _mcp_proxy_forward_stdout_jsonl(server_name, proc)
        elif proc.stdout:
            observer = _McpStdoutObserver(server_name)
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                observer.feed(chunk)
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        rc = proc.wait()
        level = "INFO" if rc == 0 else "WARN"
        router_log(level, f"mcp_proxy_exited server={server_name} rc={rc}")
        return rc
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                router_log(
                    "WARN",
                    f"mcp_stdio_proxy_terminate_failed server={server_name} "
                    f"error={type(exc).__name__}: {exc}",
                )


def cmd_mcp_proxy(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ciel-runtime mcp-proxy")
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--server-config", required=True)
    args = parser.parse_args(argv)
    server_config_path = Path(args.server_config).expanduser()
    try:
        server = json.loads(server_config_path.read_text(encoding="utf-8"))
    except Exception:
        server = None
    if isinstance(server, dict) and _mcp_server_is_streamable_http(server):
        return run_mcp_streamable_http_proxy(args.server_name, server_config_path)
    return run_mcp_stdio_proxy(args.server_name, server_config_path)


def npm_install_runtime_command(npm: str, package_spec: str, prefix: Path | None = None) -> list[str]:
    cmd = npm_global_install_command(npm, package_spec, prefix)
    cmd.insert(3, "--prefer-online")
    return cmd


def forced_yes_upgrade_env() -> dict[str, str]:
    env = os.environ.copy()
    env["CI"] = "1"
    env["NPM_CONFIG_YES"] = "true"
    env["npm_config_yes"] = "true"
    env.setdefault("NPM_CONFIG_UPDATE_NOTIFIER", "false")
    env.setdefault("npm_config_update_notifier", "false")
    return env


def add_npm_prefix_bin_to_path(prefix: Path | None) -> None:
    if prefix is None:
        return
    bin_dir = str(npm_global_bin_dir_from_prefix(prefix))
    path = os.environ.get("PATH", "")
    if bin_dir and bin_dir not in path.split(os.pathsep):
        os.environ["PATH"] = bin_dir + (os.pathsep + path if path else "")


def install_runtime_package_if_missing(
    *,
    executable_name: str,
    label: str,
    package_spec: str,
    skip_env: str,
) -> str | None:
    executable = find_executable(executable_name)
    if executable:
        return executable
    if os.environ.get(skip_env) == "1":
        return None
    npm = find_executable("npm")
    if not npm:
        print(f"{label} executable was not found, and npm is not available to install {package_spec}.", flush=True)
        return None
    install_prefix = current_npm_install_prefix()
    cmd = npm_install_runtime_command(npm, package_spec, install_prefix)
    print(f"{label} executable was not found; installing {package_spec}...", flush=True)
    if install_prefix is not None:
        print(f"Installing {label} into active npm prefix: {install_prefix}", flush=True)
    rc, out = run_command_for_upgrade(cmd, timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        print(f"{label} install failed ({rc}).", flush=True)
        if install_prefix is not None:
            print(
                f"Install targeted the active install prefix ({install_prefix}). "
                "If this prefix is not writable, install with the permissions used for that prefix.",
                flush=True,
            )
        return None
    add_npm_prefix_bin_to_path(install_prefix)
    executable = find_executable(executable_name)
    if executable:
        print(f"{label} installed: {executable}", flush=True)
    else:
        print(f"{label} install completed, but the {executable_name} executable is still not visible in PATH.", flush=True)
    return executable


def run_runtime_npm_update_check(
    executable: str,
    *,
    executable_name: str,
    label: str,
    package_spec: str,
    skip_env: str,
    current_version: Callable[[str], str],
    enabled: bool = True,
) -> str:
    if not enabled:
        return executable
    if os.environ.get(skip_env) == "1":
        return executable
    print(f"Checking {label} update before launch...", flush=True)
    current = current_version(executable)
    if current:
        print(f"Current {label} version: {current}", flush=True)
    npm = find_executable("npm")
    if not npm:
        print(f"{label} update check skipped: npm was not found.", flush=True)
        return executable
    latest = npm_latest_package_version(npm, package_spec)
    if not latest:
        print(f"{label} update check could not read the latest npm version; continuing.", flush=True)
        return executable
    if current and not version_newer(latest, current):
        print(f"{label} is up to date ({current}).", flush=True)
        return executable
    current_label = current or "unknown"
    print(f"{label} update available: {current_label} -> {latest}; upgrading automatically.", flush=True)
    install_prefix = current_npm_install_prefix()
    if install_prefix is not None:
        print(f"Updating {label} in active npm prefix: {install_prefix}", flush=True)
    rc, out = run_command_for_upgrade(npm_install_runtime_command(npm, package_spec, install_prefix), timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        print(f"{label} update failed ({rc}); continuing with current version.", flush=True)
        return executable
    add_npm_prefix_bin_to_path(install_prefix)
    updated = find_executable(executable_name) or executable
    new_version = current_version(updated)
    if new_version:
        print(f"{label} version after update: {new_version}", flush=True)
    return updated


def run_claude_update_check(claude: str, enabled: bool = True) -> str:
    package_spec = os.environ.get("CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest")
    return run_runtime_npm_update_check(
        claude,
        executable_name="claude",
        label="Claude Code",
        package_spec=package_spec,
        skip_env="CIEL_RUNTIME_SKIP_CLAUDE_UPDATE",
        current_version=claude_code_current_version,
        enabled=enabled,
    )


def parse_version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in re.split(r"[^0-9]+", value.strip()):
        if item:
            parts.append(int(item))
    return tuple(parts)


def version_newer(latest: str, current: str) -> bool:
    left = list(parse_version_tuple(latest))
    right = list(parse_version_tuple(current))
    size = max(len(left), len(right), 1)
    left.extend([0] * (size - len(left)))
    right.extend([0] * (size - len(right)))
    return tuple(left) > tuple(right)


def npm_latest_package_version(npm: str, package_spec: str, timeout: float = 8.0) -> str:
    try:
        p = subprocess.run(
            [npm, "view", package_spec, "version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    out = (p.stdout or "").strip()
    return out.splitlines()[-1].strip() if out else ""


def npm_global_package_root(npm: str, package_name: str = "@oneciel-ai/ciel-runtime", timeout: float = 8.0) -> Path | None:
    try:
        p = subprocess.run(
            [npm, "root", "-g"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    root = (p.stdout or "").strip()
    if not root:
        return None
    package_path = Path(root)
    for part in package_name.split("/"):
        if part:
            package_path /= part
    return package_path


def npm_prefix_from_package_root(package_root: Path) -> Path | None:
    """Infer the npm install prefix from an installed package root.

    npm global installs normally land under either:
    - <prefix>/lib/node_modules/@scope/name on POSIX
    - <prefix>/node_modules/@scope/name on Windows

    Updating without this prefix can write to npm's current default global
    prefix, which may not be the prefix that supplied the running executable.
    """
    parts = package_root.parts
    for idx, part in enumerate(parts):
        if part != "node_modules":
            continue
        try:
            node_modules = Path(*parts[: idx + 1])
        except Exception:
            return None
        parent = node_modules.parent
        if parent.name == "lib":
            return parent.parent
        return parent
    return None


def current_npm_install_prefix() -> Path | None:
    root = current_npm_package_root()
    return npm_prefix_from_package_root(root) if root else None


def npm_global_install_command(npm: str, package_spec: str, prefix: Path | None = None) -> list[str]:
    cmd = [npm, "install", "-g"]
    if prefix is not None:
        cmd.extend(["--prefix", str(prefix)])
    cmd.append(package_spec)
    return cmd


def npm_global_bin_dir_from_prefix(prefix: Path) -> Path:
    if os.name == "nt":
        return prefix
    return prefix / "bin"


def claude_code_current_version(claude: str) -> str:
    try:
        p = subprocess.run(
            [claude, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", p.stdout or "")
    return match.group(0) if match else ""


def codex_current_version(codex: str) -> str:
    try:
        p = subprocess.run(
            [codex, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    match = re.search(r"\d+(?:\.\d+)+", p.stdout or "")
    return match.group(0) if match else ""


def running_from_npm_package() -> bool:
    if os.environ.get("CIEL_RUNTIME_NPM_MODE") is not None:
        return True
    path = str(Path(__file__).resolve()).replace("\\", "/")
    return "/node_modules/@oneciel-ai/ciel-runtime/" in path


def package_root_from_installed_path(path: Path) -> Path | None:
    """Return the npm package root when a path lives inside this package."""
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        resolved = path
    parts = resolved.parts
    for idx in range(0, max(0, len(parts) - 2)):
        if parts[idx] == "node_modules" and parts[idx + 1] == "@oneciel-ai" and parts[idx + 2] == "ciel-runtime":
            try:
                return Path(*parts[: idx + 3])
            except Exception:
                return None
    return None


def current_npm_package_root() -> Path | None:
    return package_root_from_installed_path(Path(__file__))


def ciel_runtime_launcher_candidate_dirs() -> list[Path]:
    raw_dirs: list[Path] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            raw_dirs.append(Path(entry))
    raw_dirs.extend(executable_extra_dirs())
    raw_dirs.extend([HOME / ".npm-global" / "bin", HOME / "bin"])
    if os.name != "nt":
        raw_dirs.extend([Path("/usr/local/bin"), Path("/usr/bin")])
    seen: set[str] = set()
    out: list[Path] = []
    for directory in raw_dirs:
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        out.append(directory)
    return out


def ciel_runtime_launcher_candidates() -> list[Path]:
    names = ["ciel-runtime"]
    if os.name == "nt":
        names.extend(["ciel-runtime.cmd", "ciel-runtime.exe"])
    out: list[Path] = []
    seen: set[str] = set()
    for directory in ciel_runtime_launcher_candidate_dirs():
        for name in names:
            candidate = directory / name
            if not candidate.exists():
                continue
            try:
                key = str(candidate.resolve(strict=False))
            except Exception:
                key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def ciel_runtime_launcher_version(path: Path, timeout: float = 5.0) -> str:
    env = os.environ.copy()
    env["CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC"] = "1"
    env["CIEL_RUNTIME_SKIP_SELF_UPDATE"] = "1"
    env["CIEL_RUNTIME_SELF_UPDATE_CHECK"] = "off"
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    match = re.search(r"ciel-runtime\s+(.+)", proc.stdout or "", re.IGNORECASE)
    return match.group(1).strip() if match else (proc.stdout or "").strip().splitlines()[-1].strip()


def ciel_runtime_install_diagnostics() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for launcher in ciel_runtime_launcher_candidates():
        root = package_root_from_installed_path(launcher)
        rows.append(
            {
                "launcher": str(launcher),
                "resolved": str(launcher.resolve(strict=False)),
                "package_root": str(root) if root else "",
                "version": ciel_runtime_launcher_version(launcher),
            }
        )
    return rows


def warn_if_multiple_ciel_runtime_installs() -> None:
    if os.environ.get("CIEL_RUNTIME_SKIP_INSTALL_DIAGNOSTIC") == "1":
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    rows = ciel_runtime_install_diagnostics()
    roots = {row["package_root"] for row in rows if row.get("package_root")}
    if len(roots) <= 1:
        return
    current_root = str(current_npm_package_root() or "")
    first = rows[0] if rows else {}
    newest = max((row for row in rows if row.get("version")), key=lambda row: parse_version_tuple(row["version"]), default=None)
    print("Ciel Runtime warning: multiple ciel-runtime npm installs are visible.", file=sys.stderr, flush=True)
    if first:
        print(
            f"  shell resolves ciel-runtime to: {first.get('launcher')} ({first.get('version') or 'unknown version'})",
            file=sys.stderr,
            flush=True,
        )
    if current_root:
        print(f"  current package root: {current_root}", file=sys.stderr, flush=True)
    if newest and newest is not first:
        print(
            f"  newer visible install: {newest.get('launcher')} ({newest.get('version')})",
            file=sys.stderr,
            flush=True,
        )
    print("  Fix by keeping one install prefix: update or uninstall the stale higher-priority install.", file=sys.stderr, flush=True)


def ciel_runtime_restart_user_args() -> list[str]:
    args = list(sys.argv[1:])
    if args and args[0] == "cli":
        return args[1:]
    return args


def restart_ciel_runtime_after_update(npm: str, package_root: Path | None = None) -> None:
    os.environ["CIEL_RUNTIME_SKIP_SELF_UPDATE"] = "1"
    user_args = ciel_runtime_restart_user_args()
    package_root = package_root or current_npm_package_root() or npm_global_package_root(npm)
    package_script = package_root / "ciel_runtime.py" if package_root else None
    if package_script and package_script.exists():
        os.execv(sys.executable, [sys.executable, str(package_script), "cli", *user_args])
    launcher = find_executable("ciel-runtime")
    if launcher:
        raise SystemExit(subprocess.call([launcher, *user_args], env=os.environ.copy()))
    os.execv(sys.executable, [sys.executable, *sys.argv])


def run_ciel_runtime_update_check(enabled: bool = True) -> bool:
    if not enabled:
        return False
    if os.environ.get("CIEL_RUNTIME_SKIP_SELF_UPDATE") == "1":
        return False
    if env_bool(os.environ.get("CIEL_RUNTIME_SELF_UPDATE_CHECK")) is False:
        return False
    if not running_from_npm_package():
        return False
    npm = find_executable("npm")
    if not npm:
        return False
    latest = npm_latest_package_version(npm, "@oneciel-ai/ciel-runtime@latest")
    if not latest or not version_newer(latest, VERSION):
        return False
    print(f"Ciel Runtime update available: {VERSION} -> {latest}; upgrading automatically.", flush=True)
    package_root = current_npm_package_root()
    install_prefix = npm_prefix_from_package_root(package_root) if package_root else None
    update_cmd = npm_global_install_command(npm, "@oneciel-ai/ciel-runtime@latest", install_prefix)
    if install_prefix is not None:
        print(f"Updating current Ciel Runtime install prefix: {install_prefix}", flush=True)
    try:
        update = subprocess.run(
            update_cmd,
            text=True,
            input="y\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=forced_yes_upgrade_env(),
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("Ciel Runtime update timed out; continuing with current version.", flush=True)
        return False
    except Exception as exc:
        print(f"Ciel Runtime update failed ({type(exc).__name__}); continuing.", flush=True)
        return False
    out = (update.stdout or "").strip()
    if out:
        print(out, flush=True)
    if update.returncode != 0:
        print(f"Ciel Runtime update exited with {update.returncode}; continuing with current version.", flush=True)
        if install_prefix is not None:
            print(
                f"Update targeted the active install prefix ({install_prefix}). "
                "If this prefix is not writable, reinstall or update with the permissions used for that prefix.",
                flush=True,
            )
        return False
    print("Ciel Runtime updated. Restarting with the new version...", flush=True)
    try:
        restart_ciel_runtime_after_update(npm, package_root=package_root)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Restart failed ({type(exc).__name__}); continuing with the current process.", flush=True)
    return True


def run_command_for_upgrade(cmd: list[str], timeout: float = 300.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            input="y\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=forced_yes_upgrade_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, (proc.stdout or "").strip()


def quiet_upgrade_ciel_runtime() -> int:
    npm = find_executable("npm")
    if not npm:
        print("Ciel Runtime update skipped: npm was not found.", flush=True)
        return 1
    latest = npm_latest_package_version(npm, "@oneciel-ai/ciel-runtime@latest")
    if latest and not version_newer(latest, VERSION):
        print(f"Ciel Runtime is up to date ({VERSION}).", flush=True)
        return 0
    target = latest or "latest"
    print(f"Updating Ciel Runtime to {target}...", flush=True)
    package_root = current_npm_package_root()
    install_prefix = npm_prefix_from_package_root(package_root) if package_root else None
    if install_prefix is not None:
        print(f"Updating current Ciel Runtime install prefix: {install_prefix}", flush=True)
    rc, out = run_command_for_upgrade(npm_global_install_command(npm, "@oneciel-ai/ciel-runtime@latest", install_prefix), timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        print(f"Ciel Runtime update failed ({rc}).", flush=True)
        if install_prefix is not None:
            print(
                f"Update targeted the active install prefix ({install_prefix}). "
                "If this prefix is not writable, reinstall or update with the permissions used for that prefix.",
                flush=True,
            )
    return rc


def quiet_upgrade_claude_code() -> int:
    claude = find_executable("claude")
    if not claude:
        claude = install_claude_code_if_missing()
        return 0 if claude else 1
    current = claude_code_current_version(claude)
    npm = find_executable("npm")
    latest = ""
    if npm:
        package_spec = os.environ.get("CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest")
        latest = npm_latest_package_version(npm, package_spec)
    if current and latest and not version_newer(latest, current):
        print(f"Claude Code is up to date ({current}).", flush=True)
        return 0
    target = latest or "latest"
    current_label = current or "unknown"
    print(f"Updating Claude Code ({current_label} -> {target})...", flush=True)
    install_prefix = current_npm_install_prefix()
    if install_prefix is not None:
        print(f"Updating Claude Code in active npm prefix: {install_prefix}", flush=True)
    rc, out = run_command_for_upgrade(npm_install_runtime_command(npm, package_spec, install_prefix), timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        print(f"Claude Code update failed ({rc}).", flush=True)
    return rc


def quiet_upgrade_codex() -> int:
    codex = find_executable("codex")
    if not codex:
        codex = install_codex_if_missing()
        return 0 if codex else 1
    current = codex_current_version(codex)
    npm = find_executable("npm")
    if not npm:
        print("Codex update skipped: npm was not found.", flush=True)
        return 1
    package_spec = os.environ.get("CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest")
    latest = npm_latest_package_version(npm, package_spec)
    if current and latest and not version_newer(latest, current):
        print(f"Codex is up to date ({current}).", flush=True)
        return 0
    target = latest or "latest"
    current_label = current or "unknown"
    print(f"Updating Codex ({current_label} -> {target})...", flush=True)
    install_prefix = current_npm_install_prefix()
    if install_prefix is not None:
        print(f"Updating Codex in active npm prefix: {install_prefix}", flush=True)
    rc, out = run_command_for_upgrade(npm_install_runtime_command(npm, package_spec, install_prefix), timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        print(f"Codex update failed ({rc}).", flush=True)
    return rc


def quiet_upgrade_agy() -> int:
    agy = find_executable("agy")
    if not agy:
        agy = install_agy_if_missing()
        return 0 if agy else 1
    updated = run_agy_update_check(agy, enabled=True)
    return 0 if updated else 1


def install_claude_code_if_missing() -> str | None:
    package_spec = os.environ.get("CIEL_RUNTIME_CLAUDE_CODE_PACKAGE", "@anthropic-ai/claude-code@latest")
    return install_runtime_package_if_missing(
        executable_name="claude",
        label="Claude Code",
        package_spec=package_spec,
        skip_env="CIEL_RUNTIME_SKIP_CLAUDE_INSTALL",
    )


def install_codex_if_missing() -> str | None:
    package_spec = os.environ.get("CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest")
    return install_runtime_package_if_missing(
        executable_name="codex",
        label="Codex",
        package_spec=package_spec,
        skip_env="CIEL_RUNTIME_SKIP_CODEX_INSTALL",
    )


def run_codex_update_check(codex: str, enabled: bool = True) -> str:
    package_spec = os.environ.get("CIEL_RUNTIME_CODEX_PACKAGE", "@openai/codex@latest")
    return run_runtime_npm_update_check(
        codex,
        executable_name="codex",
        label="Codex",
        package_spec=package_spec,
        skip_env="CIEL_RUNTIME_SKIP_CODEX_UPDATE",
        current_version=codex_current_version,
        enabled=enabled,
    )


AGY_MANIFEST_BASE_URL = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"


def agy_manifest_name() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    if os.name == "nt":
        platform_name = "windows"
    elif sys.platform == "darwin":
        platform_name = "darwin"
    else:
        platform_name = "linux"
    return f"{platform_name}_{arch}.json"


def agy_manifest_url() -> str:
    override = str(os.environ.get("CIEL_RUNTIME_AGY_MANIFEST_URL") or "").strip()
    if override:
        return override
    return f"{AGY_MANIFEST_BASE_URL}/manifests/{agy_manifest_name()}"


def agy_download_file(url: str, target: Path, timeout: float = 120.0) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as resp, target.open("wb") as out:
        shutil.copyfileobj(resp, out)


def agy_latest_manifest(timeout: float = 15.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(agy_manifest_url(), timeout=timeout) as resp:
            data = json.loads(resp.read(65536).decode("utf-8", errors="replace"))
        if isinstance(data, dict) and data.get("url") and data.get("version"):
            return data
    except Exception as exc:
        print(f"AGY manifest check failed ({type(exc).__name__}); continuing.", flush=True)
    return None


def agy_current_version(agy: str) -> str:
    try:
        proc = subprocess.run(
            [agy, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    match = re.search(r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9_.-]+)?", proc.stdout or "")
    return match.group(0) if match else (proc.stdout or "").strip()


def verify_sha512(path: Path, expected: str) -> bool:
    expected = str(expected or "").strip().lower()
    if not expected:
        return True
    digest = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower() == expected


def install_agy_from_manifest(manifest: dict[str, Any]) -> str | None:
    url = str(manifest.get("url") or "").strip()
    version = str(manifest.get("version") or "").strip()
    sha512 = str(manifest.get("sha512") or "").strip()
    if not url:
        print("AGY install failed: manifest did not include a download URL.", flush=True)
        return None
    install_dir = agy_user_bin_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    exe_name = "agy.exe" if os.name == "nt" else "agy"
    target = install_dir / exe_name
    suffix = ".exe" if os.name == "nt" else ".tar.gz"
    with tempfile.TemporaryDirectory(prefix="ciel-runtime-agy-") as td:
        download = Path(td) / f"agy{suffix}"
        print(f"Downloading AGY {version or 'latest'} from Google official manifest...", flush=True)
        agy_download_file(url, download)
        if not verify_sha512(download, sha512):
            print("AGY install failed: sha512 verification did not match manifest.", flush=True)
            return None
        if os.name == "nt":
            shutil.copy2(download, target)
        else:
            with tarfile.open(download, "r:gz") as archive:
                member = next((item for item in archive.getmembers() if Path(item.name).name == "agy" and item.isfile()), None)
                if member is None:
                    member = next((item for item in archive.getmembers() if item.isfile()), None)
                if member is None:
                    print("AGY install failed: archive did not contain an executable file.", flush=True)
                    return None
                extracted = archive.extractfile(member)
                if extracted is None:
                    print("AGY install failed: could not extract executable from archive.", flush=True)
                    return None
                with target.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
            target.chmod(target.stat().st_mode | 0o755)
    try:
        subprocess.run([str(target), "install"], input="y\n", text=True, env=forced_yes_upgrade_env(), timeout=120, check=False)
    except Exception as exc:
        print(f"AGY post-install setup skipped ({type(exc).__name__}); continuing.", flush=True)
    print(f"AGY installed: {target}", flush=True)
    return str(target)


def install_agy_if_missing() -> str | None:
    agy = find_executable("agy")
    if agy:
        return agy
    if os.environ.get("CIEL_RUNTIME_SKIP_AGY_INSTALL") == "1":
        return None
    manifest = agy_latest_manifest()
    if not manifest:
        print("AGY executable was not found, and the official AGY manifest could not be read.", flush=True)
        return None
    return install_agy_from_manifest(manifest)


def run_agy_update_check(agy: str, enabled: bool = True) -> str:
    if not enabled or os.environ.get("CIEL_RUNTIME_SKIP_AGY_UPDATE") == "1":
        return agy
    print("Checking AGY update before launch...", flush=True)
    current = agy_current_version(agy)
    if current:
        print(f"Current AGY version: {current}", flush=True)
    manifest = agy_latest_manifest()
    latest = str((manifest or {}).get("version") or "").strip()
    if current and latest and not version_newer(latest, current):
        print(f"AGY is up to date ({current}).", flush=True)
        return agy
    if latest:
        print(f"AGY update available: {current or 'unknown'} -> {latest}; upgrading automatically.", flush=True)
    else:
        print("AGY update version could not be confirmed; running native updater.", flush=True)
    rc, out = run_command_for_upgrade([agy, "update"], timeout=300)
    if out:
        print(out, flush=True)
    if rc != 0:
        if manifest and latest and (not current or version_newer(latest, current)):
            installed = install_agy_from_manifest(manifest)
            return installed or agy
        print(f"AGY update failed ({rc}); continuing with current version.", flush=True)
        return agy
    updated = find_executable("agy") or agy
    new_version = agy_current_version(updated)
    if new_version:
        print(f"AGY version after update: {new_version}", flush=True)
    return updated


def run_quiet_upgrade_and_exit() -> int:
    any_rc = quiet_upgrade_ciel_runtime()
    claude_rc = quiet_upgrade_claude_code()
    codex_rc = quiet_upgrade_codex()
    agy_rc = quiet_upgrade_agy()
    return 0 if any_rc == 0 and claude_rc == 0 and codex_rc == 0 and agy_rc == 0 else 1


def launch_claude(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    web_search_override: bool | None = None,
    update_check: bool = True,
    self_update_check: bool = True,
) -> int:
    return run_claude(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        web_search_override=web_search_override, update_check=update_check,
        self_update_check=self_update_check,
        services=ClaudeLaunchServices(
            constants=ClaudeLaunchConstants(
                CLAUDE_SERVER_SIDE_WEB_TOOLS=CLAUDE_SERVER_SIDE_WEB_TOOLS,
                LOG_PATH=LOG_PATH,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
                ROUTED_COMPAT_PROMPT=ROUTED_COMPAT_PROMPT,
                _NATIVE_ROUTER_CHANNEL_NAMES=_NATIVE_ROUTER_CHANNEL_NAMES,
            ),
            process=ClaudeLaunchProcess(
                _log_claude_command_for_diagnostics=_log_claude_command_for_diagnostics,
                _subprocess_call_capturing_stderr=_subprocess_call_capturing_stderr,
                env_bool=env_bool,
                env_vars=env_vars,
                file_size_or_zero=file_size_or_zero,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                print_routed_claude_exit_diagnostics=print_routed_claude_exit_diagnostics,
                subprocess_call_with_channel_wake_proxy=subprocess_call_with_channel_wake_proxy,
            ),
            installation=ClaudeLaunchInstallation(
                find_executable=find_executable,
                install_ciel_runtime_slash_commands=install_ciel_runtime_slash_commands,
                install_ciel_runtime_statusline=install_ciel_runtime_statusline,
                install_claude_code_if_missing=install_claude_code_if_missing,
                install_tool_guard_hooks=install_tool_guard_hooks,
                disable_ciel_runtime_slash_commands_for_native=disable_ciel_runtime_slash_commands_for_native,
                launch_readiness_errors=launch_readiness_errors,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=ClaudeLaunchDispatch(
                launch_agy=launch_agy,
                launch_codex=launch_codex,
                launch_codex_app_server=launch_codex_app_server,
                materialize_runtime_command=materialize_runtime_command,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_claude_update_check=run_claude_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
                claude_launch_enabled_for_provider=claude_launch_enabled_for_provider,
            ),
            config=ClaudeLaunchConfig(
                load_config=load_config,
                save_config=save_config,
                get_current_provider=get_current_provider,
                ensure_current_model_from_provider_list=ensure_current_model_from_provider_list,
                ensure_model_cache_for_launch=ensure_model_cache_for_launch,
                apply_launch_endpoint_policy=apply_launch_endpoint_policy,
                provider_menu_label=provider_menu_label,
                launch_mode_name=launch_mode_name,
                current_launch_cwd_key=current_launch_cwd_key,
            ),
            routing=ClaudeLaunchRouting(
                anthropic_routed_enabled=anthropic_routed_enabled,
                direct_native_anthropic_enabled=direct_native_anthropic_enabled,
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                ensure_managed_router_running_for_client=ensure_managed_router_running_for_client,
                reset_zai_mcp_config_if_inactive=reset_zai_mcp_config_if_inactive,
                router_health_summary=router_health_summary,
                router_log=router_log,
                start_router_if_needed=start_router_if_needed,
                run_with_router_lifetime=run_with_router_lifetime,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
            ),
            policy=ClaudeLaunchPolicy(
                append_claude_code_runtime_settings_args=append_claude_code_runtime_settings_args,
                claude_supports_permission_mode_arg=claude_supports_permission_mode_arg,
                has_noninteractive_claude_args=has_noninteractive_claude_args,
                has_passthrough_option=has_passthrough_option,
                should_append_compat_prompt=should_append_compat_prompt,
                should_attach_web_search=should_attach_web_search,
                should_disallow_claude_server_side_web_tools=should_disallow_claude_server_side_web_tools,
                should_fork_native_session_after_mode_switch=should_fork_native_session_after_mode_switch,
                should_insert_passthrough_option_boundary=should_insert_passthrough_option_boundary,
                strip_mcp_config_passthrough=strip_mcp_config_passthrough,
            ),
            channel_discovery=ClaudeLaunchChannelDiscovery(
                auto_import_passthrough_channels=auto_import_passthrough_channels,
                cached_channel_capable_server_names=cached_channel_capable_server_names,
                cached_channel_source_paths_for_specs=cached_channel_source_paths_for_specs,
                channel_candidate_server_names_for_launch=channel_candidate_server_names_for_launch,
                channel_specs_for_launch=channel_specs_for_launch,
                claude_channels_requested=claude_channels_requested,
                claude_code_channels_auth_available=claude_code_channels_auth_available,
                ensure_channel_probe_cache_for_launch=ensure_channel_probe_cache_for_launch,
                external_mcp_channel_server_names_from_configs=external_mcp_channel_server_names_from_configs,
                read_channel_probe_cache=read_channel_probe_cache,
            ),
            channel_delivery=ClaudeLaunchChannelDelivery(
                auto_start_sse_channels_from_mcp_configs=auto_start_sse_channels_from_mcp_configs,
                claude_channel_args=claude_channel_args,
                native_channel_passthrough_requested=native_channel_passthrough_requested,
                normalize_channel_passthrough=normalize_channel_passthrough,
                prepare_channel_llm_delivery_for_launch=prepare_channel_llm_delivery_for_launch,
                should_launch_process_start_channel_sse=should_launch_process_start_channel_sse,
                should_use_channel_llm_delivery=should_use_channel_llm_delivery,
                should_use_channel_stdin_proxy=should_use_channel_stdin_proxy,
                should_use_native_channel_bridge=should_use_native_channel_bridge,
                write_channel_mcp_config=write_channel_mcp_config,
            ),
            mcp_config=ClaudeLaunchMcpConfig(
                write_duckduckgo_mcp_config=write_duckduckgo_mcp_config,
                write_mcp_proxy_config=write_mcp_proxy_config,
                write_native_mcp_config_from_discovery=write_native_mcp_config_from_discovery,
                write_zai_mcp_config=write_zai_mcp_config,
            ),
        ),
    )


CODEX_RUNTIME_PROVIDER_ID = "ciel-runtime"
CODEX_RUNTIME_API_KEY_ENV = "CIEL_RUNTIME_CODEX_API_KEY"
CODEX_NATIVE_PROVIDER_ID_ENV = "CIEL_RUNTIME_CODEX_NATIVE_PROVIDER_ID"
CODEX_ROUTED_PROVIDER_ID = "ciel-runtime-codex"
CODEX_ROUTED_UPSTREAM_BASE = "https://chatgpt.com/backend-api/codex"
CODEX_TUI_ALTERNATE_SCREEN_KEY = "tui.alternate_screen"


def toml_string(value: str) -> str:
    return project_toml_string(value)


def _codex_config_override_keys(passthrough: list[str]) -> set[str]:
    return project_codex_config_override_keys(passthrough)


def _toml_scalar_without_comment(raw: str) -> str:
    return project_toml_scalar_without_comment(raw)


def _unquote_toml_string(raw: str) -> str:
    return project_unquote_toml_string(raw)


def codex_alternate_screen_value_from_config_text(text: str) -> str | None:
    return project_codex_alternate_screen_value(text)


def codex_config_paths_for_launch(passthrough: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> list[Path]:
    return project_codex_config_paths(passthrough, env=env, cwd=cwd)


def _normalize_codex_mcp_server(raw_name: Any, raw_server: Any) -> tuple[str, dict[str, Any]] | None:
    return project_normalize_codex_mcp_server(raw_name, raw_server)


def _codex_mcp_servers_from_toml_data(data: Any) -> dict[str, dict[str, Any]]:
    return project_codex_mcp_servers_from_toml(data)


def _toml_table_parts(raw: str) -> list[str]:
    return project_toml_table_parts(raw)


def _parse_simple_toml_value(raw: str) -> Any:
    return project_parse_simple_toml_value(raw)


def _fallback_codex_mcp_servers_from_config_text(text: str) -> dict[str, dict[str, Any]]:
    return project_fallback_codex_mcp_servers(text)


def codex_mcp_servers_from_config_text(text: str) -> dict[str, dict[str, Any]]:
    return project_codex_mcp_servers(text)


def discovered_codex_mcp_servers(
    passthrough: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> dict[str, dict[str, Any]]:
    return project_discover_codex_mcp_servers(
        passthrough,
        env,
        cwd,
        log=router_log,
    )


def write_codex_mcp_config_for_channel_discovery(
    passthrough: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path | None:
    servers = discovered_codex_mcp_servers(passthrough or [], env=env, cwd=cwd)
    if not servers:
        try:
            CODEX_MCP_CONFIG.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            router_log("WARN", f"codex_mcp_config_remove_failed error={type(exc).__name__}: {exc}")
        return None
    json_artifact_repository(CODEX_MCP_CONFIG).save(
        {"mcpServers": servers},
        "codex_mcp_config",
    )
    router_log("INFO", f"codex_mcp_config_written servers={','.join(sorted(servers))}")
    return CODEX_MCP_CONFIG


def _codex_config_bare_key(name: str) -> str | None:
    text = str(name or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return text
    return None


def codex_channel_capable_mcp_server_names(cfg: dict[str, Any], codex_mcp_config: Path | None) -> list[str]:
    if not codex_mcp_config or not codex_mcp_config.exists() or not codex_mcp_config.is_file():
        return []
    extra_paths: list[Path | str] = [codex_mcp_config]
    ensure_channel_probe_cache_for_launch(cfg, [], extra_config_paths=extra_paths)
    candidate_names = [
        str(server.get("channel") or "").strip()
        for server in _read_mcp_sse_servers_from_json(codex_mcp_config, Path.cwd())
        if str(server.get("channel") or "").strip()
    ]
    source_key = _path_for_compare(codex_mcp_config)
    capable = {
        str(record.get("name") or "").strip()
        for record in cached_channel_probe_servers()
        if record.get("capable")
        and str(record.get("name") or "").strip()
        and _path_for_compare(Path(str(record.get("source_path") or ""))) == source_key
    }
    names = [
        name
        for name in candidate_names
        if name in capable and name.strip().lower() not in _NATIVE_ROUTER_CHANNEL_NAMES
    ]
    return _dedupe_strings(names)


def codex_streamable_http_mcp_servers(codex_mcp_config: Path | None) -> dict[str, dict[str, Any]]:
    if not codex_mcp_config or not codex_mcp_config.exists() or not codex_mcp_config.is_file():
        return {}
    try:
        data = json.loads(codex_mcp_config.read_text(encoding="utf-8"))
    except Exception as exc:
        router_log("WARN", f"codex_mcp_compat_source_read_failed error={type(exc).__name__}: {exc}")
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for raw_name, raw_server in servers.items():
        name = str(raw_name).strip()
        if name and isinstance(raw_server, dict) and _mcp_server_is_streamable_http(raw_server):
            out[name] = raw_server
    return out


def codex_mcp_native_http_compat_args(
    codex_mcp_config: Path | None,
    *,
    split_http_proxy: bool = False,
    channel_owned_server_names: Iterable[str] | None = None,
) -> list[str]:
    servers = codex_streamable_http_mcp_servers(codex_mcp_config)
    if not servers:
        return []
    args: list[str] = []
    active: list[str] = []
    channel_owned = {
        _channel_sse_public_mcp_name(str(name or "").strip())
        for name in channel_owned_server_names or []
        if str(name or "").strip()
    }
    for name, server in sorted(servers.items()):
        key = _codex_config_bare_key(name)
        if not key:
            router_log("WARN", f"codex_mcp_compat_skipped_unsafe_name server={name}")
            continue
        if split_http_proxy or _channel_sse_public_mcp_name(name) in channel_owned:
            args.extend(["-c", f"mcp_servers.{key}.url={toml_string(codex_mcp_split_proxy_url(name))}"])
        active.append(name)
    if active:
        router_log(
            "INFO",
            "codex_mcp_native_http_compat servers=%s split_http_proxy=%s"
            % (",".join(active), str(bool(split_http_proxy)).lower()),
        )
    return args


def codex_alternate_screen_compat_args(passthrough: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> list[str]:
    if has_passthrough_option(passthrough, "--no-alt-screen") or CODEX_TUI_ALTERNATE_SCREEN_KEY in _codex_config_override_keys(passthrough):
        return []
    for path in codex_config_paths_for_launch(passthrough, env=env, cwd=cwd):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        value = codex_alternate_screen_value_from_config_text(text)
        if value:
            router_log("WARN", f"codex_compat_alternate_screen_override path={path} value={value}")
            print(f'Ciel Runtime warning: applying Codex config compatibility override {CODEX_TUI_ALTERNATE_SCREEN_KEY}="{value}".', flush=True)
            return ["-c", f"{CODEX_TUI_ALTERNATE_SCREEN_KEY}={toml_string(value)}"]
    return []


def codex_runtime_config_args(router_base: str = ROUTER_BASE) -> list[str]:
    provider = CODEX_RUNTIME_PROVIDER_ID
    base = router_base.rstrip("/") + "/v1"
    return [
        "-c",
        f"model_provider={toml_string(provider)}",
        "-c",
        f"model_providers.{provider}.name={toml_string('Ciel Runtime')}",
        "-c",
        f"model_providers.{provider}.base_url={toml_string(base)}",
        "-c",
        f"model_providers.{provider}.wire_api={toml_string('responses')}",
        "-c",
        f"model_providers.{provider}.env_key={toml_string(CODEX_RUNTIME_API_KEY_ENV)}",
        "-c",
        f"model_providers.{provider}.request_max_retries=0",
        "-c",
        f"model_providers.{provider}.stream_max_retries=0",
    ]


def write_codex_runtime_model_catalog(codex: str, cfg: dict[str, Any]) -> Path | None:
    """Add the routed model alias to Codex's own version-matched model catalog."""
    provider, pcfg = get_current_provider(cfg)
    if native_codex_enabled(provider):
        return None
    alias = current_alias(cfg)
    if not alias:
        return None
    context_window = (
        context_limit_for_status(provider, pcfg)
        or provider_model_context_capacity(provider, pcfg)
        or 272000
    )
    catalog_env = os.environ.copy()
    catalog_env["PATH"] = path_with_ciel_runtime_user_dirs(catalog_env)
    return CodexModelCatalogService(CONFIG_DIR, subprocess.run, router_log).write(
        codex,
        CodexModelCatalogSpec(
            alias=alias,
            provider_label=PROVIDER_LABELS.get(provider, provider),
            context_window=context_window,
            effort=str(pcfg.get("effort_level") or "").strip().lower(),
        ),
        catalog_env,
    )


def codex_runtime_model_catalog_args(codex: str, cfg: dict[str, Any]) -> list[str]:
    path = write_codex_runtime_model_catalog(codex, cfg)
    if path is None:
        return []
    return ["-c", f"model_catalog_json={toml_string(str(path.resolve()))}"]


def codex_native_routed_config_args(router_base: str = ROUTER_BASE) -> list[str]:
    provider = (os.environ.get(CODEX_NATIVE_PROVIDER_ID_ENV) or CODEX_ROUTED_PROVIDER_ID).strip() or CODEX_ROUTED_PROVIDER_ID
    return project_codex_native_routed_config_args(
        router_base,
        provider,
        toml_string=toml_string,
    )


def codex_passthrough_has_model_override(passthrough: list[str]) -> bool:
    return project_codex_model_overridden(
        passthrough,
        has_option=has_passthrough_option,
        config_override_keys=_codex_config_override_keys,
    )


def codex_current_model_cli_args(pcfg: dict[str, Any], passthrough: list[str]) -> list[str]:
    return project_codex_current_model_args(
        pcfg,
        passthrough,
        overridden=codex_passthrough_has_model_override,
    )


def codex_current_model_config_args(pcfg: dict[str, Any], passthrough: list[str]) -> list[str]:
    return project_codex_current_model_args(
        pcfg,
        passthrough,
        overridden=codex_passthrough_has_model_override,
        config_style=True,
        toml_string=toml_string,
    )


def log_codex_passthrough_mapping(notes: list[str]) -> None:
    if not notes:
        return
    deduped = _dedupe_strings(notes)
    router_log("INFO", "codex_passthrough_mapping " + "; ".join(deduped))
    print("Ciel Runtime Codex passthrough mapping:", flush=True)
    for note in deduped:
        print(f"- {note}", flush=True)


def codex_help_requested(passthrough: list[str]) -> bool:
    return project_codex_help_requested(passthrough)


def codex_yolo_launch_args(passthrough: list[str]) -> list[str]:
    return project_codex_yolo_launch_args(
        passthrough,
        has_option=has_passthrough_option,
    )


def codex_sqlite_home_for_launch(
    passthrough: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    return codex_sqlite_home(passthrough, env=env, cwd=cwd)


def codex_local_resume_sessions(
    env: dict[str, str] | None = None,
    limit: int = 200,
    include_non_interactive: bool = False,
    passthrough: list[str] | None = None,
    cwd: Path | None = None,
) -> list[dict[str, Any]]:
    database = codex_sqlite_home_for_launch(passthrough, env=env, cwd=cwd) / "state_5.sqlite"
    return CodexSessionRepository(database, router_log).resumable(
        limit,
        include_non_interactive=include_non_interactive,
    )


def codex_resume_session_row(session: dict[str, Any]) -> str:
    title = str(session.get("title") or session.get("first_user_message") or "Untitled session").strip()
    title = re.sub(r"\s+", " ", title)
    cwd = str(session.get("cwd") or "").strip()
    folder = Path(cwd).name if cwd else "-"
    provider = str(session.get("model_provider") or "-").strip()
    try:
        activity = datetime.fromtimestamp(int(session.get("activity_ms") or 0) / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        activity = "unknown time"
    return f"{compact_text(title, 66)}  [{folder} | {provider} | {activity}]"


def select_codex_resume_session(
    env: dict[str, str] | None = None,
    include_non_interactive: bool = False,
    passthrough: list[str] | None = None,
) -> str | None:
    sessions = codex_local_resume_sessions(
        env,
        include_non_interactive=include_non_interactive,
        passthrough=passthrough,
    )
    if not sessions:
        database = codex_sqlite_home_for_launch(passthrough, env=env) / "state_5.sqlite"
        print(f"Ciel Runtime could not find resumable Codex sessions in: {database}", flush=True)
        return None
    selected = portable_select(
        "Resume Codex session",
        [codex_resume_session_row(session) for session in sessions],
        footer="Up/Down moves. Enter resumes. Esc/q cancels.",
    )
    if selected is None:
        return ""
    return str(sessions[selected].get("id") or "").strip()


def launch_codex(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
) -> int:
    return run_codex(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=CodexLaunchServices(
            constants=CodexLaunchConstants(
                CODEX_RUNTIME_API_KEY_ENV=CODEX_RUNTIME_API_KEY_ENV,
                CONFIG_DIR=CONFIG_DIR,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=CodexLaunchProcess(
                _channel_wake_enter_env_is_fixed=_channel_wake_enter_env_is_fixed,
                _codex_channel_wake_submit_delay_seconds=_codex_channel_wake_submit_delay_seconds,
                _codex_channel_wake_submit_retries=_codex_channel_wake_submit_retries,
                _log_codex_command_for_diagnostics=_log_codex_command_for_diagnostics,
                _set_channel_transcript_scope=_set_channel_transcript_scope,
                codex_process_record_path=codex_process_record_path,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                subprocess_call_with_channel_wake_proxy=subprocess_call_with_channel_wake_proxy,
                terminate_existing_codex_processes_for_launch=terminate_existing_codex_processes_for_launch,
                terminate_existing_router_clients_for_launch=terminate_existing_router_clients_for_launch,
            ),
            cli_policy=CodexLaunchCliPolicy(
                codex_alternate_screen_compat_args=codex_alternate_screen_compat_args,
                codex_current_model_cli_args=codex_current_model_cli_args,
                codex_help_requested=codex_help_requested,
                codex_native_routed_config_args=codex_native_routed_config_args,
                codex_passthrough_args_for_launch=codex_passthrough_args_for_launch,
                codex_passthrough_has_command=codex_passthrough_has_command,
                codex_resume_picker_requested=codex_resume_picker_requested,
                codex_resume_with_session_id=codex_resume_with_session_id,
                codex_runtime_config_args=codex_runtime_config_args,
                codex_yolo_launch_args=codex_yolo_launch_args,
            ),
            config=CodexLaunchConfig(
                apply_launch_endpoint_policy=apply_launch_endpoint_policy,
                current_alias=current_alias,
                current_launch_cwd_key=current_launch_cwd_key,
                ensure_model_cache_for_launch=ensure_model_cache_for_launch,
                get_current_provider=get_current_provider,
                load_config=load_config,
                provider_mode_label=provider_mode_label,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
                codex_runtime_model_catalog_args=codex_runtime_model_catalog_args,
            ),
            installation=CodexLaunchInstallation(
                disable_ciel_runtime_codex_prompts_for_native=disable_ciel_runtime_codex_prompts_for_native,
                find_executable=find_executable,
                has_passthrough_option=has_passthrough_option,
                install_ciel_runtime_codex_prompts=install_ciel_runtime_codex_prompts,
                install_codex_if_missing=install_codex_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=CodexLaunchDispatch(
                launch_agy=launch_agy,
                launch_claude=launch_claude,
                launch_codex_app_server=launch_codex_app_server,
                log_codex_passthrough_mapping=log_codex_passthrough_mapping,
                materialize_runtime_command=materialize_runtime_command,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_codex_update_check=run_codex_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=CodexLaunchRouting(
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                codex_routed_enabled=codex_routed_enabled,
                direct_native_codex_enabled=direct_native_codex_enabled,
                launch_readiness_errors=launch_readiness_errors,
                native_codex_enabled=native_codex_enabled,
                run_with_router_lifetime=run_with_router_lifetime,
                start_router_if_needed=start_router_if_needed,
            ),
            channel=CodexLaunchChannel(
                auto_import_passthrough_channels=auto_import_passthrough_channels,
                channel_delivery_mode=channel_delivery_mode,
                codex_channel_capable_mcp_server_names=codex_channel_capable_mcp_server_names,
                codex_mcp_native_http_compat_args=codex_mcp_native_http_compat_args,
                codex_mcp_split_proxy_enabled=codex_mcp_split_proxy_enabled,
                select_codex_resume_session=select_codex_resume_session,
                start_codex_mcp_channel_sse_for_launch=start_codex_mcp_channel_sse_for_launch,
                write_codex_mcp_config_for_channel_discovery=write_codex_mcp_config_for_channel_discovery,
            ),
        ),
    )


def codex_app_server_default_listen_url() -> str:
    configured = str(os.environ.get("CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN") or "").strip()
    if configured:
        return configured
    port = ROUTER_PORT + 20 if ROUTER_PORT <= 65515 else ROUTER_PORT - 20
    return f"ws://127.0.0.1:{port}"


def _log_codex_app_server_command_for_diagnostics(cmd: list[str], env: dict[str, str]) -> None:
    provider_args = [arg for arg in cmd if arg.startswith("model_provider=") or arg.startswith("model_providers.")]
    listen = ""
    for i, arg in enumerate(cmd):
        if arg == "--listen" and i + 1 < len(cmd):
            listen = str(cmd[i + 1])
            break
        if arg.startswith("--listen="):
            listen = arg.split("=", 1)[1]
            break
    router_log(
        "INFO",
        "codex_app_server_launch_cmd argv_len=%d provider_overrides=%d listen=%s"
        % (len(cmd), len(provider_args), listen or "stdio/default"),
    )
    env_summary = []
    for key in ("CIEL_RUNTIME_PROVIDER", "CIEL_RUNTIME_MODEL_ALIAS", CODEX_RUNTIME_API_KEY_ENV):
        if key in env:
            value = mask_secret(env[key]) if "KEY" in key or "TOKEN" in key else env[key]
            env_summary.append(f"{key}={value}")
    if env_summary:
        router_log("INFO", "codex_app_server_launch_env " + " ".join(env_summary))


def launch_codex_app_server(
    passthrough: list[str],
    skip_menu: bool = True,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
) -> int:
    return run_codex_app_server(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=CodexAppServerLaunchServices(
            constants=CodexLaunchConstants(
                CODEX_RUNTIME_API_KEY_ENV=CODEX_RUNTIME_API_KEY_ENV,
                CONFIG_DIR=CONFIG_DIR,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=CodexAppServerProcess(
                _log_codex_app_server_command_for_diagnostics=_log_codex_app_server_command_for_diagnostics,
                codex_process_record_path=codex_process_record_path,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                subprocess_call_with_child_pid_record=subprocess_call_with_child_pid_record,
                terminate_existing_codex_processes_for_launch=terminate_existing_codex_processes_for_launch,
                terminate_existing_router_clients_for_launch=terminate_existing_router_clients_for_launch,
            ),
            config=CodexAppServerConfig(
                apply_launch_endpoint_policy=apply_launch_endpoint_policy,
                current_alias=current_alias,
                current_launch_cwd_key=current_launch_cwd_key,
                ensure_model_cache_for_launch=ensure_model_cache_for_launch,
                get_current_provider=get_current_provider,
                load_config=load_config,
                provider_mode_label=provider_mode_label,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
            ),
            cli_policy=CodexAppServerCliPolicy(
                codex_app_server_default_listen_url=codex_app_server_default_listen_url,
                codex_app_server_launch_args=codex_app_server_launch_args,
                codex_current_model_config_args=codex_current_model_config_args,
                codex_native_routed_config_args=codex_native_routed_config_args,
                codex_passthrough_has_model_override=codex_passthrough_has_model_override,
                codex_runtime_config_args=codex_runtime_config_args,
                toml_string=toml_string,
            ),
            installation=CodexAppServerInstallation(
                find_executable=find_executable,
                install_codex_if_missing=install_codex_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=CodexAppServerDispatch(
                launch_agy=launch_agy,
                launch_claude=launch_claude,
                launch_codex=launch_codex,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_codex_update_check=run_codex_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=CodexAppServerRouting(
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                codex_launch_enabled_for_provider=codex_launch_enabled_for_provider,
                codex_routed_enabled=codex_routed_enabled,
                direct_native_codex_enabled=direct_native_codex_enabled,
                launch_readiness_errors=launch_readiness_errors,
                native_codex_enabled=native_codex_enabled,
                run_with_router_lifetime=run_with_router_lifetime,
                start_router_if_needed=start_router_if_needed,
            ),
            channel=CodexAppServerChannel(
                auto_import_passthrough_channels=auto_import_passthrough_channels,
                channel_delivery_mode=channel_delivery_mode,
                codex_channel_capable_mcp_server_names=codex_channel_capable_mcp_server_names,
                codex_mcp_native_http_compat_args=codex_mcp_native_http_compat_args,
                codex_mcp_split_proxy_enabled=codex_mcp_split_proxy_enabled,
                start_codex_mcp_channel_sse_for_launch=start_codex_mcp_channel_sse_for_launch,
                write_codex_mcp_config_for_channel_discovery=write_codex_mcp_config_for_channel_discovery,
            ),
        ),
    )


def agy_help_requested(passthrough: list[str]) -> bool:
    return any(arg in ("--help", "-h", "help") for arg in passthrough)


def log_agy_passthrough_mapping(notes: list[str]) -> None:
    if not notes:
        return
    print("Ciel Runtime AGY passthrough mapping:", flush=True)
    for note in notes:
        print(f"- {note}", flush=True)


def _log_agy_command_for_diagnostics(cmd: list[str], env: dict[str, str]) -> None:
    launch_command_diagnostics().agy(cmd, env)


def launch_agy(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
) -> int:
    return run_agy(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=AgyLaunchServices(
            constants=AgyLaunchConstants(
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=AgyLaunchProcess(
                _codex_channel_wake_submit_delay_seconds=_codex_channel_wake_submit_delay_seconds,
                _codex_channel_wake_submit_retries=_codex_channel_wake_submit_retries,
                _log_agy_command_for_diagnostics=_log_agy_command_for_diagnostics,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                subprocess_call_with_channel_wake_proxy=subprocess_call_with_channel_wake_proxy,
            ),
            cli_policy=AgyLaunchCliPolicy(
                agy_dangerous_launch_args=agy_dangerous_launch_args,
                agy_help_requested=agy_help_requested,
                agy_passthrough_args_for_launch=agy_passthrough_args_for_launch,
                agy_passthrough_has_command=agy_passthrough_has_command,
            ),
            channel=AgyLaunchChannel(
                auto_import_passthrough_channels=auto_import_passthrough_channels,
                channel_delivery_mode=channel_delivery_mode,
            ),
            config=AgyLaunchConfig(
                current_launch_cwd_key=current_launch_cwd_key,
                get_current_provider=get_current_provider,
                load_config=load_config,
                provider_mode_label=provider_mode_label,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
            ),
            installation=AgyLaunchInstallation(
                find_executable=find_executable,
                install_agy_if_missing=install_agy_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=AgyLaunchDispatch(
                launch_claude=launch_claude,
                launch_codex=launch_codex,
                launch_codex_app_server=launch_codex_app_server,
                log_agy_passthrough_mapping=log_agy_passthrough_mapping,
                materialize_runtime_command=materialize_runtime_command,
                run_agy_update_check=run_agy_update_check,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=AgyLaunchRouting(
                agy_routed_enabled=agy_routed_enabled,
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                launch_readiness_errors=launch_readiness_errors,
                native_agy_enabled=native_agy_enabled,
                run_with_router_lifetime=run_with_router_lifetime,
                start_router_if_needed=start_router_if_needed,
            ),
        ),
    )


CLAUDE_CODE_STDERR_LOG = CONFIG_DIR / "claude-code-stderr.log"


def launch_command_diagnostics() -> LaunchCommandDiagnostics:
    return LaunchCommandDiagnostics(router_log, mask_secret, CODEX_RUNTIME_API_KEY_ENV)


def _log_claude_command_for_diagnostics(cmd: list[str], env: dict[str, str]) -> None:
    launch_command_diagnostics().claude(cmd, env)


def _log_codex_command_for_diagnostics(cmd: list[str], env: dict[str, str]) -> None:
    launch_command_diagnostics().codex(cmd, env)


def _subprocess_call_capturing_stderr(cmd: list[str], env: dict[str, str]) -> int:
    """Like subprocess.call but tees Claude Code's stderr into
    ~/.config/ciel-runtime/claude-code-stderr.log so the user can collect
    the exact context around messages like
    `--dangerously-load-development-channels ignored (server:...)`.

    Enabled via CIEL_RUNTIME_CAPTURE_CC_STDERR=1."""
    return StderrCaptureAdapter(CONFIG_DIR, CLAUDE_CODE_STDERR_LOG, router_log).call(cmd, env)


def cli_usage() -> str:
    return cli_usage_text()


def pop_headless_env_file_args(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--ca-env-file" or arg.startswith("--ca-env-file="):
            value = arg.split("=", 1)[1] if "=" in arg else None
            if value is None:
                if i + 1 >= len(argv):
                    raise SystemExit("Missing path for --ca-env-file")
                value = argv[i + 1]
                i += 2
            else:
                i += 1
            path = Path(value).expanduser()
            if not path.exists():
                raise SystemExit(f"--ca-env-file not found: {path}")
            load_dotenv_into_environ(path, override=True)
        else:
            cleaned.append(arg)
            i += 1
    return cleaned


def apply_headless_env_config() -> tuple[bool, bool | None, bool | None, bool | None, bool]:
    result = apply_headless_config(
        HeadlessConfigServices(
            environ=os.environ,
            parse_bool=env_bool,
            current_provider=lambda: get_current_provider(load_config())[0],
            commands=HeadlessConfigCommands(
                set_language=lambda value: cmd_language(argparse.Namespace(value=value)),
                set_web_fetch=lambda enabled: cmd_web_fetch(
                    argparse.Namespace(value="on" if enabled else "off")
                ),
                set_provider=lambda provider: cmd_provider(argparse.Namespace(name=provider)),
                set_api_keys=lambda provider, keys: cmd_set_api_keys(
                    argparse.Namespace(provider=provider, keys=keys)
                ),
                set_api_key=lambda provider, key: cmd_set_api_key(
                    argparse.Namespace(provider=provider, key=key)
                ),
                set_base_url=lambda provider, url: cmd_base_url(
                    argparse.Namespace(provider=provider, url=url)
                ),
                set_model=lambda model: cmd_model(argparse.Namespace(value=[model])),
                set_advisor_model=set_advisor_model_config,
                set_provider_options=lambda values: cmd_provider_options(argparse.Namespace(values=values)),
                set_ollama_options=lambda values: cmd_ollama_options(argparse.Namespace(values=values)),
            ),
            channels=HeadlessChannelCommands(
                add_channel=add_channel_spec,
                set_delivery=set_channel_delivery_config,
            ),
        )
    )
    return result.as_tuple()


def run_cli(argv: list[str]) -> int:
    services = CliServices(
        core=CliCore(
            VERSION=VERSION,
            cli_usage=cli_usage,
            find_executable=find_executable,
            get_current_provider=get_current_provider,
            load_config=load_config,
            pop_headless_env_file_args=pop_headless_env_file_args,
            portable_provider_menu=portable_provider_menu,
            run_external_menu=run_external_menu,
            run_quiet_upgrade_and_exit=run_quiet_upgrade_and_exit,
        ),
        runtime=CliRuntime(
            agy_passthrough_has_command=agy_passthrough_has_command,
            codex_passthrough_has_command=codex_passthrough_has_command,
            last_launch_runtime=last_launch_runtime,
            launch_agy=launch_agy,
            launch_claude=launch_claude,
            launch_codex=launch_codex,
            launch_codex_app_server=launch_codex_app_server,
            native_agy_enabled=native_agy_enabled,
            native_codex_enabled=native_codex_enabled,
        ),
        provider_commands=CliProviderCommands(
            cmd_advisor_model=cmd_advisor_model,
            cmd_api_key=cmd_api_key,
            cmd_base_url=cmd_base_url,
            cmd_language=cmd_language,
            cmd_log_level=cmd_log_level,
            cmd_model=cmd_model,
            cmd_models=cmd_models,
            cmd_provider=cmd_provider,
            cmd_provider_options=cmd_provider_options,
            cmd_set_api_key=cmd_set_api_key,
        ),
        channel_commands=CliChannelCommands(
            add_channel_spec=add_channel_spec,
            channel_delivery_mode=channel_delivery_mode,
            clear_channel_specs=clear_channel_specs,
            cmd_channels=cmd_channels,
            cmd_mcp_proxy=cmd_mcp_proxy,
            set_channel_delivery_config=set_channel_delivery_config,
            set_channel_development_enabled=set_channel_development_enabled,
        ),
        special_commands=CliSpecialCommands(
            cmd_ollama_catalog=cmd_ollama_catalog,
            cmd_ollama_native=cmd_ollama_native,
            cmd_ollama_options=cmd_ollama_options,
            cmd_web_fetch=cmd_web_fetch,
            cmd_web_search=cmd_web_search,
        ),
        operations=CliOperations(cmd_status=cmd_status, cmd_stop=cmd_stop, cmd_test=cmd_test),
        configuration=CliConfiguration(
            apply_auto_llm_options_config=apply_auto_llm_options_config,
            apply_headless_env_config=apply_headless_env_config,
            set_advisor_model_config=set_advisor_model_config,
            set_log_level_config=set_log_level_config,
            cmd_set_api_keys=cmd_set_api_keys,
        ),
    )
    return dispatch_cli(argv, services)


def cmd_cli(args: argparse.Namespace) -> None:
    raise SystemExit(run_cli(args.argv))


def cmd_launch(args: argparse.Namespace) -> None:
    raise SystemExit(launch_claude(args.argv))


def cmd_launch_codex(args: argparse.Namespace) -> None:
    raise SystemExit(launch_codex(args.argv))


def cmd_launch_codex_app_server(args: argparse.Namespace) -> None:
    raise SystemExit(launch_codex_app_server(args.argv))


def cmd_launch_agy(args: argparse.Namespace) -> None:
    raise SystemExit(launch_agy(args.argv))


def cmd_version(args: argparse.Namespace) -> None:
    print(f"ciel-runtime {VERSION}")


def build_parser() -> argparse.ArgumentParser:
    return build_cli_parser(
        CliParserServices(
            launch=CliParserLaunch(
                cli=cmd_cli,
                launch=cmd_launch,
                launch_codex=cmd_launch_codex,
                launch_codex_app_server=cmd_launch_codex_app_server,
                launch_agy=cmd_launch_agy,
                serve=serve,
            ),
            runtime=CliParserRuntime(
                version=cmd_version,
                status=cmd_status,
                env=cmd_env,
                stop=cmd_stop,
                test=cmd_test,
            ),
            settings=CliParserSettings(
                language=cmd_language,
                web_search=cmd_web_search,
                web_fetch=cmd_web_fetch,
                log_level=cmd_log_level,
                channels=cmd_channels,
                channel_delivery=cmd_channel_delivery,
            ),
            provider=CliParserProvider(
                ollama_native=cmd_ollama_native,
                ollama_options=cmd_ollama_options,
                provider_options=cmd_provider_options,
                ollama_catalog=cmd_ollama_catalog,
                provider=cmd_provider,
                api_key=cmd_api_key,
                set_api_key=cmd_set_api_key,
                set_api_keys=cmd_set_api_keys,
                base_url=cmd_base_url,
            ),
            models=CliParserModels(
                model=cmd_model,
                advisor_model=cmd_advisor_model,
                models=cmd_models,
            ),
        )
    )


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "mcp-proxy":
        raise SystemExit(cmd_mcp_proxy(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "cli":
        raise SystemExit(run_cli(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "launch":
        raise SystemExit(launch_claude(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] in ("codex", "launch-codex"):
        raise SystemExit(launch_codex(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] in ("codex-app", "codex-app-server", "codex-appserver", "launch-codex-app-server"):
        raise SystemExit(launch_codex_app_server(sys.argv[2:]))
    if len(sys.argv) >= 2 and sys.argv[1] in ("agy", "launch-agy", "antigravity"):
        raise SystemExit(launch_agy(sys.argv[2:]))
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
