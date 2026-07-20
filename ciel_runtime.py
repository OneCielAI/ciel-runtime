#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import getpass
import hashlib
import json
import os
import re
import shutil  # noqa: F401 - compatibility export
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from ciel_runtime_support.agent_router import missing_common_capabilities, router_capability_matrix
from ciel_runtime_support.advisor_policy import (
    AdvisorShortcutController,
    AdvisorShortcutPorts,
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
    AdvisorAnthropicSystemPolicy,
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
from ciel_runtime_support.architecture import MessageProtocol, ProviderConfig
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
from ciel_runtime_support.agy_installer import AgyInstaller, AgyInstallerPorts
from ciel_runtime_support import claude_router
from ciel_runtime_support import channel_injection
from ciel_runtime_support.chat_files import ChatFilePorts, ChatFileRepository
from ciel_runtime_support.chat_http_controller import (
    ChatHttpController,
    ChatHttpReadServices,
    ChatHttpWriteServices,
)
from ciel_runtime_support.channel_inflight import ChannelInflightEffects
from ciel_runtime_support.channel_backlog import (
    ChannelBacklogCursors,
    ChannelBacklogRuntime,
    ChannelBacklogService,
)
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
    ChannelConfigApi,
    ChannelConfigPorts,
    ChannelConfigService,
)
from ciel_runtime_support.channel_cli import (
    ChannelCliCommands,
    ChannelCliController,
    ChannelCliView,
)
from ciel_runtime_support.channel_compact_request_repository import (
    ChannelCompactRequestRepository,
    compact_request_ttl,
)
from ciel_runtime_support.channel_compact_injection import (
    ChannelCompactInjectionService,
    ChannelCompactRequestPorts,
    ChannelCompactRuntimePorts,
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
from ciel_runtime_support import channel_llm_context
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
from ciel_runtime_support.channel_terminal_dispatch import (
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)
from ciel_runtime_support.channel_tool_context import (
    ChannelToolContextPolicy,
    ChannelToolContextPorts,
    ChannelToolContextRepository,
    ChannelToolContextService,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices,
    active_tool_call_from_text as _channel_stdin_active_tool_call_from_text,
    active_turn_from_text as _channel_stdin_active_turn_from_text,
    queued_age_seconds_from_text as analyze_channel_queued_age,
    queued_command_ids_from_text as analyze_channel_queued_ids,
    wake_state_from_text as analyze_channel_wake_state,
)
from ciel_runtime_support.channel_transcript_repository import (
    ChannelTranscriptRepository,
)
from ciel_runtime_support.channel_message_policy import (
    message_has_external_provenance as _channel_message_has_external_provenance,
    message_is_web_chat_request as _channel_message_is_web_chat_request,
    string_list as _as_string_list,
    superseded_message_ids as _channel_superseded_message_ids,
)
from ciel_runtime_support.channel_message_dedupe import (
    ChannelMessageDedupePorts,
    ChannelMessageDedupeService,
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
from ciel_runtime_support.channel_message_repository import ChannelMessageAppendPorts, ChannelMessageRepository
from ciel_runtime_support.channel_launch_guard_repository import ChannelLaunchGuardRepository
from ciel_runtime_support.channel_launch_policy import (
    ChannelLaunchPolicy,
    ChannelLaunchPorts,
)
from ciel_runtime_support.channel_runtime_environment import (
    ChannelRuntimeEnvironmentPolicy,
)
from ciel_runtime_support.channel_cursor_repository import ChannelCursorRepository
from ciel_runtime_support.channel_cursor_service import (
    ChannelDeliveryCursorCommitter,
    ChannelDeliveryCursorPorts,
    ChannelCursorService,
    ChannelCursorServices,
    ChannelResumePolicy,
    ChannelResumeServices,
    parse_channel_event_id,
)
from ciel_runtime_support.channel_cursor_recovery import (
    ChannelCursorRecoveryPolicy,
    ChannelCursorRecoveryPorts,
    ChannelCursorRecoveryService,
)
from ciel_runtime_support.channel_wake_claim_repository import (
    ChannelWakeClaimRepository,
    prompt_message_ids as _channel_prompt_message_ids,
    prompt_references_message_id as analyze_prompt_message_reference,
)
from ciel_runtime_support.channel_terminal_input import (
    TerminalMouseInputFilter as _TerminalMouseInputFilter,
    enter_bytes_from_user_input as _channel_enter_bytes_from_user_input,  # noqa: F401 - compatibility export
    enter_label as _channel_enter_label,
    platform_default_enter_bytes as _channel_platform_default_enter_bytes,
    resolve_enter_bytes as resolve_channel_enter_bytes,
    synthetic_enter_bytes_from_user_input as _channel_synthetic_enter_bytes_from_user_input,
    wake_enter_env_is_fixed as _channel_wake_enter_env_is_fixed,
    wake_input_bytes as build_channel_wake_input_bytes,
    wake_submit_delay_seconds as _channel_wake_submit_delay_seconds,
    wake_submit_retry_delay_seconds as _channel_wake_submit_retry_delay_seconds,
    windows_console_input_handle as _resolve_windows_console_input_handle,
)
from ciel_runtime_support.channel_probe_report import (
    ChannelProbeReportServices,
    channel_probe_report_lines,
)
from ciel_runtime_support.channel_probe_cache import (
    ChannelProbeCacheRepository,
    ChannelProbeCompatibilityApi,
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
from ciel_runtime_support import provider_catalog_sources
from ciel_runtime_support.provider_endpoint_policy import (
    ProviderEndpointPolicy as ModelEndpointPolicy,
    ProviderEndpointPorts as ModelEndpointPorts,
    ProviderEndpointPresentation as ModelEndpointPresentation,
)
from ciel_runtime_support.provider_request_access import (
    ProviderRequestAccessEffects,
    ProviderRequestAccessPorts,
    ProviderRequestAccessService,
)
from ciel_runtime_support.provider_runtime_modes import (
    ProviderNativeCompatibilityPolicy,
    RuntimeModePolicy,
)
from ciel_runtime_support.provider_launch_endpoint import (
    ProviderLaunchEndpointGroups,
    ProviderLaunchEndpointPolicy,
    ProviderLaunchEndpointQueries,
)
from ciel_runtime_support.provider_endpoint_probe import (
    ProviderEndpointProbePolicy,
    ProviderEndpointProbeProjection,
    ProviderEndpointProbeQueries,
    ProviderEndpointRouteAdapter,
    ProviderEndpointRoutePorts,
)
from ciel_runtime_support.model_cache_lifecycle import (
    ModelCacheLifecyclePorts,
    ModelCacheLifecycleService,
)
from ciel_runtime_support.model_registry_repository import (
    ModelRegistryApi,
    ModelRegistryPaths,
    ModelRegistryPolicy,
    ModelRegistryRepository,
)
from ciel_runtime_support.lm_studio_runtime import (
    LmStudioLifecycleApi,
    LmStudioLifecyclePolicy,
    LmStudioModelLifecycle,
    LmStudioRuntimeServices,
    discover_lm_studio_runtime,
)
from ciel_runtime_support import cli_dispatch
from ciel_runtime_support.cli_usage import cli_usage_text
from ciel_runtime_support import cli_parser
from ciel_runtime_support.configuration_cli import (
    ConfigurationCliConfigPorts,
    ConfigurationCliController,
    ConfigurationCliDisplayPorts,
    ConfigurationCliIO,
    ConfigurationCliModelPorts,
    ConfigurationCliProviderPorts,
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
from ciel_runtime_support.compatibility_protocol import (
    CompatibilityProtocolApi,
    CompatibilityProtocolCodec,
    CompatibilityProtocolPorts,
)
from ciel_runtime_support.compatibility_probe import (
    CompatibilityApiKeyProbeBuilder,
    CompatibilityApiKeyProbeError,
    CompatibilityApiKeyProbeRunner,
    CompatibilityApiKeyProbeRunnerPorts,
    CompatibilityProbeAnthropicPorts,
    CompatibilityProbeProjectionPorts,
    CompatibilityProbeRoutingPorts,
)
from ciel_runtime_support.compatibility_runtime import (
    CompatibilityCachePorts,
    CompatibilityCacheRepository,
    CompatibilityRuntimePorts,
    CompatibilityRuntimeProjection,
)
from ciel_runtime_support.claude_environment import (
    ClaudeEnvironmentFeaturePorts,
    ClaudeEnvironmentProjection,
    ClaudeEnvironmentShellRenderer,
    ClaudeEnvironmentSourcePorts,
    ClaudeLimitPolicy,
    ClaudeLimitPorts,
    ClaudeModelAliasPolicy,
    ClaudeModelPorts,
    ClaudeRuntimeSettingsPolicy,
    ClaudeRuntimeSettingsPorts,
)
from ciel_runtime_support.headless_config import (
    HeadlessChannelCommands,
    HeadlessConfigCommands,
    HeadlessConfigServices,
    HeadlessEnvFileLoader,
    apply_headless_config,
)
from ciel_runtime_support.http_response import ChannelDeliveryGuard, HttpResponseAdapter
from ciel_runtime_support.config_repository import (
    ConfigRepositoryProvider,
    JsonConfigRepository,
    build_default_config,
    deep_merge as merge_config_values,
    normalize_loaded_config,
)
from ciel_runtime_support.config_value_codec import (
    parse_bool,
    parse_config_value,
    positive_int,
)
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
from ciel_runtime_support.context_summary_policy import (
    ContextSummaryCompatibilityApi,
    ContextSummaryPolicy,
)
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
    mask_secret as project_mask_secret,
    parse_api_key_list as project_parse_api_key_list,
    provider_config_api_keys as project_provider_config_api_keys,
    provider_contract_config as project_provider_contract_config,
    redact_sensitive_obj as project_redact_sensitive_obj,
    redact_sensitive_text as project_redact_sensitive_text,
    resolve_anthropic_credentials,
    secret_fingerprint as project_secret_fingerprint,
)
from ciel_runtime_support.credential_management import (
    CredentialManagementService,
    CredentialPersistencePorts,
    CredentialPresentationPorts,
    CredentialRotationRepository,
    ExternalCredentialPorts,
)
from ciel_runtime_support.credential_cli import (
    CredentialCliController,
    CredentialCliIO,
    CredentialCliPolicy,
    CredentialCliPorts,
)
from ciel_runtime_support.tool_guard_hooks import (
    LegacyToolGuardShimInstaller,
    LegacyToolGuardShimServices,
    ToolGuardHookPolicy,
    ToolGuardHookServices,
    install_tool_guard_hook_settings,
)
from ciel_runtime_support.tool_side_effect_dedupe import (
    ToolSideEffectDedupePolicy,
    ToolSideEffectDedupePorts,
    ToolSideEffectDedupeRepository,
    ToolSideEffectDedupeService,
)
from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessInspectionServices,
    ProcessQueryServices,
    ProcessSignalServices,
    ProcessTreeController,
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
    RouterSpawnPorts,
    RouterStartupIdentity,
    RouterStartupStatePorts,
    RouterStatePorts,
    RouterTerminationPorts,
    ensure_port_available as ensure_project_router_port_available,
    stop_router_processes as stop_project_router_processes,
    stop_with_guarantee as stop_project_router_with_guarantee,
    start_router_if_needed as start_project_router_if_needed,
    terminate_health_pid as terminate_project_router_health_pid,
    terminate_pid_file as terminate_project_pid_file,
)
from ciel_runtime_support.router_client_lifecycle import (
    ManagedRouterLifetime,
    ManagedRouterLifetimePorts,
    RoutedLaunchDiagnosticPorts,
    RoutedLaunchDiagnostics,
    RouterClientRegistry,
    RouterClientRegistryPorts,
    RouterClientSupervisor,
    RouterClientSupervisorPorts,
    RouterLifetimeRunner,
    RouterLifetimeRunnerPorts,
)
from ciel_runtime_support.provider_config_mutations import (
    ProviderOptionPolicy,
    apply_ollama_option as mutate_ollama_option,
    apply_provider_option as mutate_provider_option,
)
from ciel_runtime_support.provider_sampling_policy import ProviderSamplingPolicy
from ciel_runtime_support.provider_configuration_service import (
    ProviderEndpointPolicy,
    ProviderEndpointPorts,
    ProviderEndpointService,
    ProviderStatusProjectionPorts,
    ProviderStatusService,
    RuntimeStatusPorts,
)
from ciel_runtime_support.provider_choice import (
    AGY_NATIVE_PROVIDER_CHOICE,
    AGY_ROUTED_PROVIDER_CHOICE,
    ANTHROPIC_NATIVE_PROVIDER_CHOICE,
    ANTHROPIC_ROUTED_PROVIDER_CHOICE,
    CODEX_NATIVE_PROVIDER_CHOICE,
    CODEX_ROUTED_PROVIDER_CHOICE,
    ProviderChoiceController,
    ProviderChoicePorts,
    normalize_provider_choice as normalize_runtime_provider_choice,
)
from ciel_runtime_support.package_lifecycle import (
    NpmPackageLifecycle,
    NpmPackageLifecyclePorts,
    SelfUpdateLifecycle,
    SelfUpdatePorts,
)
from ciel_runtime_support.npm_runtime import (
    claude_code_current_version,
    codex_current_version,
    npm_global_bin_dir_from_prefix,
    npm_global_install_command,
    npm_global_package_root,
    npm_install_runtime_command,
    npm_latest_package_version,
    npm_prefix_from_package_root,
    package_root_from_installed_path,
    parse_version_tuple,
    run_upgrade_command,
    version_newer,
)
from ciel_runtime_support.runtime_restart import (
    RuntimeRestartPorts,
    RuntimeRestartService,
    RuntimeRestartSettings,
    forced_upgrade_environment,
    running_from_npm_package as detect_running_from_npm_package,
)
from ciel_runtime_support.router_access import (
    RouterAccessConfigService,
    RouterAccessMutationPorts,
    RouterAccessPolicy,
    RouterExternalTokenRepository,
    is_loopback_address,  # noqa: F401 - compatibility export
    router_request_bearer_token,  # noqa: F401 - compatibility export
)
from ciel_runtime_support.install_diagnostics import (
    InstallDiagnosticsPorts,
    InstallDiagnosticsService,
    InstallDiagnosticsSettings,
)
from ciel_runtime_support.runtime_upgrade import (
    RuntimeUpgradeNpmPorts,
    RuntimeUpgradeService,
    RuntimeUpgradeSettings,
    RuntimeUpgradeToolPorts,
)
from ciel_runtime_support.provider_option_cli import (
    OllamaOptionCommands,
    ProviderOptionCliConfig,
    ProviderOptionCliController,
    ProviderOptionCommands,
)
from ciel_runtime_support import llm_presets
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
from ciel_runtime_support.llm_config_http import (
    LlmConfigHttpController,
    LlmConfigHttpIO,
    LlmConfigIdentity,
    LlmConfigMutations,
    LlmConfigPanels,
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
    ClaudeMcpConfigPathPolicy,
    dedupe_strings as _dedupe_strings,
    path_for_compare as _path_for_compare,
    read_mcp_config_items,
    server_names_from_mapping as _mcp_server_names_from_mapping,
    servers_from_mapping as _mcp_servers_from_mapping,
)
from ciel_runtime_support.managed_mcp_config import (
    ManagedMcpConfigPaths,
    ManagedMcpConfigPolicy,
    ManagedMcpConfigPorts,
    ManagedMcpConfigService,
)
from ciel_runtime_support.managed_mcp_discovery import (
    ManagedMcpDiscoveryPaths,
    ManagedMcpDiscoveryPorts,
    ManagedMcpDiscoveryService,
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
from ciel_runtime_support.mcp_notification_wait_policy import (
    McpNotificationWaitPolicy,
    McpNotificationWaitPorts,
    McpNotificationWaitRepository,
    McpNotificationWaitService,
)
from ciel_runtime_support.mcp_proxy_config import McpProxyConfigPaths, McpProxyConfigPorts, McpProxyConfigService
from ciel_runtime_support.mcp_proxy_process import (
    McpStdioConfigPorts,
    McpStdioEffects,
    McpStdioProxyService,
    McpStdioTransportPorts,
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
from ciel_runtime_support import mcp_proxy_notifications
from ciel_runtime_support.mcp_http_proxy import (
    McpHttpProxyCodec,
    McpHttpProxyRuntime,
    McpHttpProxyServices,
    McpHttpProxyTransport,
    run_mcp_streamable_http_proxy as run_streamable_http_mcp_proxy,
)
from ciel_runtime_support.mcp_split_proxy_http import McpSplitProxyHttpAdapter, McpSplitProxyHttpPorts
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
    codex_alternate_screen_value_from_config_text,
    codex_config_override_keys as _codex_config_override_keys,
    codex_config_paths_for_launch,
    codex_mcp_servers_from_config_text,  # noqa: F401 - compatibility export
    codex_mcp_servers_from_toml_data as _codex_mcp_servers_from_toml_data,  # noqa: F401
    discover_codex_mcp_servers as project_discover_codex_mcp_servers,
    fallback_codex_mcp_servers_from_config_text as _fallback_codex_mcp_servers_from_config_text,  # noqa: F401
    normalize_codex_mcp_server as _normalize_codex_mcp_server,  # noqa: F401
    parse_simple_toml_value as _parse_simple_toml_value,  # noqa: F401
    toml_scalar_without_comment as _toml_scalar_without_comment,  # noqa: F401
    toml_string,
    toml_table_parts as _toml_table_parts,  # noqa: F401
    unquote_toml_string as _unquote_toml_string,  # noqa: F401
)
from ciel_runtime_support import codex_mcp_integration
from ciel_runtime_support.codex_channel_sse_launch import (
    CodexChannelSseEffects,
    CodexChannelSseLaunchService,
    CodexChannelSseQueryPorts,
)
from ciel_runtime_support.codex_launch_policy import (
    current_model_args as project_codex_current_model_args,
    help_requested as project_codex_help_requested,
    native_routed_config_args as project_codex_native_routed_config_args,
    yolo_launch_args as project_codex_yolo_launch_args,
)
from ciel_runtime_support import codex_launch_configuration
from ciel_runtime_support.codex_model_catalog import (
    CodexModelCatalogService,
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
from ciel_runtime_support.codex_session_selection import (
    CodexSessionPresentationPorts,
    CodexSessionRepositoryPorts,
    CodexSessionSelectionService,
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
from ciel_runtime_support.router_shortcuts import (
    ChannelShortcutPorts,
    LiveConfigShortcutPorts,
    RouterDebugShortcutPorts,
    RouterShortcutController,
    ShortcutPredicates,
    ShortcutResponsePorts,
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
from ciel_runtime_support import openai_responses_router
from ciel_runtime_support.openai_responses_stream import (
    OpenAIResponsesStreamServices,
    write_openai_responses as project_openai_responses_stream,
    write_openai_responses_error as project_openai_responses_error,
)
from ciel_runtime_support.protocols import PROTOCOL_ADAPTERS
from ciel_runtime_support.protocols.anthropic_content import (
    content_to_text as anthropic_content_to_text,
)
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
    ConversationTurnCompatibilityApi,
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
from ciel_runtime_support.protocols.openai_reasoning import (
    OpenAiReasoningPolicy,
    anthropic_tool_choice_to_openai,
    openai_reasoning_to_anthropic_thinking_block,
)
from ciel_runtime_support.provider_adapters import (
    PROVIDER_ADAPTERS,
    PROVIDER_LABELS,
    provider_default_configurations,
)
from ciel_runtime_support.provider_model_identity import (
    ProviderModelIdentityApi,
    ProviderModelIdentityService,
)
from ciel_runtime_support.provider_contract_projection import ProviderContractProjectionApi
from ciel_runtime_support.provider_compatibility import PROVIDER_COMPATIBILITY
from ciel_runtime_support.provider_context import (
    ContextPresetServices,
    ProviderContextServices,
    cap_context_settings as apply_context_capacity_cap,
    cap_output_settings as apply_output_context_cap,
    cap_output_tokens as apply_output_token_cap,
    classify_model_family,
    infer_context_preset,
    recommended_preset,
    required_context_for_preset as context_required_for_preset,
    resolve_context_capacity,
    small_context_output_token_cap as resolve_small_context_output_cap,
)
from ciel_runtime_support.context_setup import ContextSetupPorts, ContextSetupService
from ciel_runtime_support.model_context_hints import (
    ModelContextHintPolicy,
    ModelContextHintPorts,
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
from ciel_runtime_support.provider_option_status import (
    ProviderOptionStatusPorts,
    ProviderOptionStatusProjection,
)
from ciel_runtime_support.provider_model_specs import (
    ModelSpecLookupPorts,
    ModelSpecMutationPorts,
    ModelSpecRefreshPorts,
    ProviderModelSpecService,
)
from ciel_runtime_support.provider_timeout_policy import (
    ProviderTimeoutPolicy,
    ProviderTimeoutPorts,
    ProviderTimeoutSettings,
)
from ciel_runtime_support.timeout_profile import (
    TimeoutProfileApi,
    TimeoutProfilePorts,
    TimeoutProfileService,
    TimeoutProfileSettings,
)
from ciel_runtime_support.runtime_llm_options import (
    RuntimeLlmConfigPorts,
    RuntimeLlmMutationPorts,
    RuntimeLlmOptionsApi,
    RuntimeLlmOptionsController,
    RuntimeLlmPresentationPorts,
    RuntimeLlmSettings,
)
from ciel_runtime_support.live_api_key_controller import (
    LiveApiKeyController,
    LiveApiKeyPorts,
)
from ciel_runtime_support.provider_limits import (
    ProviderKeyServices,
    choose_provider_api_key,
)
from ciel_runtime_support import rate_limit_policy
from ciel_runtime_support.rate_limit_repository import RateLimitRepository
from ciel_runtime_support.router_rate_limit_service import (
    RouterRateLimitApi,
    RouterRateLimitPaths,
    RouterRateLimitPorts,
    RouterRateLimitService,
)
from ciel_runtime_support.api_key_cooldown import (
    API_KEY_COOLDOWN_DEFAULT_SECONDS,  # noqa: F401 - compatibility export
    API_KEY_COOLDOWN_MAX_SECONDS,  # noqa: F401 - compatibility export
    RATE_LIMIT_RESET_HEADER_NAMES as _RATE_LIMIT_RESET_HEADER_NAMES,  # noqa: F401 - compatibility export
    ApiKeyCooldownCompatibilityApi,
    ApiKeyCooldownPorts,
    ApiKeyCooldownService,
)
from ciel_runtime_support.plan_artifact_controller import (
    PlanArtifactController,
    PlanArtifactServices,
)
from ciel_runtime_support import provider_network
from ciel_runtime_support import provider_models
from ciel_runtime_support.provider_model_selection import (
    ModelCatalogPorts,
    ModelIdentityPorts,
    ModelMutationConfigPorts,
    ModelMutationEffectPorts,
    ModelMutationPolicyPorts,
    ModelSelectionController,
    ModelSelectionPorts,
    ProviderModelSelection,
    ProviderModelSelectionApi,
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
    CodexBackendHttpAdapter,
    CodexBackendRequestPorts,
    CodexBackendRetryPorts,
    CodexRoutedHeaderPolicy,
    EventHttpAdapter,
    EventHttpPorts,
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
from ciel_runtime_support.provider_runtime_info import ProviderRuntimeInfoPorts, ProviderRuntimeInfoService
from ciel_runtime_support.provider_request_builder import (
    OllamaRequestPorts,
    OpenAIRequestPorts,
    ProviderOptionPorts,
    ProviderRequestBudget,
    ProviderRequestBuilder,
)
from ciel_runtime_support.providers.ollama_runtime import (
    OllamaRuntimeApi,
    OllamaRuntimeService,
    OllamaRuntimeServices,
)
from ciel_runtime_support.providers.ollama_context import OllamaRequestContextPolicy
from ciel_runtime_support.output_budget import OutputBudgetPolicy
from ciel_runtime_support.providers.nvidia_runtime import (
    NvidiaRuntimeApi,
    NvidiaProxyRuntime,
    NvidiaProxyRuntimeConfig,
    NvidiaProxyRuntimePorts,
    NvidiaProxyStopper,
    NvidiaProxyStopPorts,
)
from ciel_runtime_support.managed_service_cleanup import (
    ManagedServiceCleanupPolicy,
    ManagedServiceCleanupPorts,
)
from ciel_runtime_support.providers.nvidia import (
    hosted_context_default as nvidia_hosted_context_default,
)
from ciel_runtime_support.provider_status import (
    ProviderStatusCatalog,
    ProviderStatusGeneric,
    ProviderStatusRouting,
    ProviderStatusServices,
    base_url_status_line as project_provider_base_url_status,
)
from ciel_runtime_support import prelaunch
from ciel_runtime_support.prelaunch_panel_projection import (
    ConfigurationPanelPorts,
    ConfigurationPanelProjection,
    MainMenuProjection,
    MainMenuProjectionPorts,
    ProviderPanelConstants,
    ProviderPanelPorts,
    ProviderPanelProjection,
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
from ciel_runtime_support.runtime_command_factory import RuntimeCommandFactory, RuntimeCommandFactoryPorts
from ciel_runtime_support.runtime_compatibility import DEFAULT_RUNTIME_COMPATIBILITY
from ciel_runtime_support.runtime_logging import (
    LOG_LEVEL_NAMES,
    LOG_LEVELS,
    LogLevelApi,
    LogLevelRepository,
    RouterFileLogger,
    normalize_log_level as normalize_runtime_log_level,
)
from ciel_runtime_support.runtime_activity_repository import (
    RuntimeActivityClock,
    RuntimeActivityEffects,
    RuntimeActivityPaths,
    RuntimeActivityRepository,
)
from ciel_runtime_support.sse_trace import (
    SseTraceConfig,
    SseTracePorts,
    SseTraceRepository,
    summarize_payload as summarize_sse_payload,
)
from ciel_runtime_support import runtime_launch
from ciel_runtime_support import streaming_anthropic
from ciel_runtime_support import terminal_platform_io
from ciel_runtime_support import windows_console_mode
from ciel_runtime_support.pseudo_tool_parser import (
    PseudoToolParserServices,
    infer_tool_name_from_args as project_infer_tool_name,
    normalize_tool_arguments as project_normalize_tool_arguments,
    parse_pseudo_tool_calls as project_parse_pseudo_tool_calls,
)
from ciel_runtime_support.stream_chunk_policy import split_word_buffer
from ciel_runtime_support.session_import import (
    ImportSessionHttpController,
    ImportSessionHttpPorts,
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
from ciel_runtime_support.tool_exposure_policy import ToolExposurePolicy, ToolExposurePorts
from ciel_runtime_support.visible_stream_filters import (
    VISIBLE_THINKING_MARKUP_PREFIXES,  # noqa: F401 - compatibility export
    VISIBLE_THINKING_MARKUP_TAG_RE,  # noqa: F401 - compatibility export
    VISIBLE_TOOL_CALL_ARTIFACT_HOLD_CHARS,  # noqa: F401 - compatibility export
    VISIBLE_TOOL_CALL_ARTIFACT_SUFFIX_RE,  # noqa: F401 - compatibility export
    VisibleThinkingMarkupFilter,
    VisibleToolCallArtifactFilter,
    strip_visible_thinking_markup,
    strip_visible_tool_call_artifact_suffix,  # noqa: F401 - compatibility export
    visible_thinking_markup_partial_start as _visible_thinking_markup_partial_start,  # noqa: F401
)
from ciel_runtime_support.synthetic_tool_policy import (
    ForcedPlanModeController,
    ForcedPlanModePorts,
    SyntheticTasklistPolicy,
    SyntheticTasklistPorts,
)
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
from ciel_runtime_support.runtime_paths import (
    CHANNEL_COMPACT_REQUEST_PATH,
    CHANNEL_LLM_CLEAR_FLOOR_PATH,
    CHANNEL_LLM_CURSOR_PATH,
    CHANNEL_LLM_LAUNCH_GUARD_PATH,
    CHANNEL_MCP_CONFIG,
    CHANNEL_MCP_CURSOR_PATH,
    CHANNEL_PROBE_CACHE_PATH,
    CHANNEL_STDIN_WAKE_CLAIMS_PATH,
    CHAT_FILES_DIR,
    CHAT_MESSAGES_PATH,
    CIEL_RUNTIME_STATUSLINE_PATH,
    CLAUDE_COMMANDS_DIR,
    CLAUDE_GATEWAY_CACHE,
    CLAUDE_SETTINGS_PATH,
    CODEX_MCP_CONFIG,
    CODEX_PROCESS_DIR,
    CODEX_PROMPTS_DIR_NAME,
    CONFIG_DIR,
    CONFIG_PATH,
    CONTEXT_COMPACT_ACTIVITY_PATH,
    CONTEXT_USAGE_PATH,
    DUCKDUCKGO_MCP_CONFIG,
    HOME,
    LAUNCH_STATE_PATH,
    LOG_LEVEL_PATH,
    LOG_PATH,
    MCP_PROXY_CONFIG,
    MENU_KEY_DEBUG_PATH,
    MODEL_LIST_CACHE_PATH,
    MODEL_REGISTRY_PATH,
    NATIVE_MCP_CONFIG,
    NCP_ENV,
    NCP_LOG,
    OLLAMA_MODEL_CATALOG_PATH,
    PID_PATH,
    PLAN_ARTIFACTS_DIR,
    RATE_LIMIT_STATE_PATH,
    REQUEST_DUMP_PATH,
    RESPONSE_DUMP_PATH,
    ROUTER_ACTIVITY_PATH,
    ROUTER_BASE,
    ROUTER_CLIENTS_DIR,
    ROUTER_EXTERNAL_TOKEN_PATH,
    ROUTER_HOST,  # noqa: F401 - compatibility export
    ROUTER_PORT,
    SSE_LAST_PATH,
    SSE_TRACE_PATH,
    TOOL_CALL_LOG_PATH,
    USAGE_EVENTS_PATH,
    WEB_TOOLS_MCP_CONFIG,
    ZAI_MCP_CONFIG,
    agy_user_bin_dir,
    ciel_runtime_user_bin_dir,
    default_router_port,  # noqa: F401 - compatibility export
    path_with_ciel_runtime_user_dirs,
    platform_config_dir,  # noqa: F401 - compatibility export
    platform_path,
    windows_appdata_root,  # noqa: F401 - compatibility export
    windows_local_appdata_root,  # noqa: F401 - compatibility export
)
from ciel_runtime_support.runtime_constants import (
    ADVISOR_FEEDBACK_MARKER,
    ANTHROPIC_LIMITED_ACCESS_MODEL_IDS,
    ANTHROPIC_MODEL_DOCS_URL,  # noqa: F401 - compatibility export
    ANTHROPIC_MODEL_DOCS_URLS,
    ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS,
    ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS,
    ANTHROPIC_THINKING_BLOCK_TYPES,
    APP_NAME,
    BUILTIN_CHANNEL_SPEC,
    CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT,
    CHANNEL_LLM_WAKE_LEGACY_PREFIXES,  # noqa: F401 - compatibility export
    CHANNEL_LLM_WAKE_PREFIX,  # noqa: F401 - compatibility export
    CHAT_MESSAGES_MAX_BYTES,
    CHAT_MESSAGE_DEDUPE_SCAN_LIMIT,
    CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS,
    CLAUDE_SERVER_SIDE_WEB_TOOLS,
    CREDITS,
    DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC,
    DEFAULT_REQUEST_TIMEOUT_MS,
    FIREWORKS_API_BASE_URL,
    FIREWORKS_DEFAULT_ACCOUNT_ID,
    FIREWORKS_INFERENCE_BASE_URL,
    KIMI_CODING_BASE_URL,  # noqa: F401 - compatibility export
    KIMI_DEFAULT_MODEL,  # noqa: F401 - compatibility export
    KIMI_K3_MODEL,
    KIMI_MODEL_FALLBACK_IDS,  # noqa: F401 - compatibility export
    LANGUAGES,
    LM_STUDIO_DEFAULT_CLAUDE_CODE_CONTEXT,
    LM_STUDIO_MIN_CLAUDE_CODE_CONTEXT,
    MCP_PROXY_TOOL_RESULT_ITEM_TEXT_CHARS,
    MCP_PROXY_TOOL_RESULT_MAX_CHARS_DEFAULT,
    MODEL_CACHE_TTL_SECONDS,
    MODEL_PRESETS,
    NCP_PYPI_PACKAGE,
    NON_ANTHROPIC_COMPAT_PROMPT,  # noqa: F401 - compatibility export
    OFFICIAL_CHANNEL_PLUGINS,
    OLLAMA_MODEL_CATALOG_TTL_SECONDS,
    OLLAMA_MODEL_CATALOG_URL,
    OPENCODE_ENDPOINT_ALIASES,
    OPENCODE_GO_BASE_URL,  # noqa: F401 - compatibility export
    OPENCODE_ZEN_BASE_URL,  # noqa: F401 - compatibility export
    PLAN_GUARD_MARKER,  # noqa: F401 - compatibility export
    PLAN_MODE_SELF_TOOLS,
    PRELAUNCH_CANCEL,
    PRELAUNCH_LAUNCH_AGY,
    PRELAUNCH_LAUNCH_CLAUDE,
    PRELAUNCH_LAUNCH_CODEX,
    PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
    PROVIDER_ALIASES,
    REQUEST_DUMP_MAX_BYTES,
    RESPONSE_DUMP_MAX_BYTES,
    RESPONSE_DUMP_TEXT_LIMIT,
    ROUTED_COMPAT_PROMPT,
    ROUTER_LOG_MAX_BYTES,
    SSE_TRACE_EVENT_LIMIT,
    SSE_TRACE_MAX_BYTES,
    SSE_TRACE_PAYLOAD_LIMIT,
    VERSION,
    ZAI_ANTHROPIC_BASE_URL,  # noqa: F401 - compatibility export
    ZAI_DEFAULT_MODEL,  # noqa: F401 - compatibility export
    ZAI_MANAGED_MCP_SERVERS,
    ZAI_MODEL_CONTEXT_HINTS,
)

execute_prelaunch_menu = prelaunch.run_prelaunch_menu
dispatch_cli = cli_dispatch.dispatch_cli
build_cli_parser = cli_parser.build_cli_parser
apply_preset_to_provider = llm_presets.apply_preset_to_provider
fetch_upstream_model_ids = provider_models.fetch_upstream_model_ids
inject_pending_channel_context = channel_llm_context.inject_pending_channel_context
TERMINAL_INPUT_MODE_RESET = terminal_platform_io.TERMINAL_INPUT_MODE_RESET
_terminal_winsize_from_fd = terminal_platform_io.terminal_winsize_from_fd
_apply_pty_winsize = terminal_platform_io.apply_pty_winsize

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

OPENCODE_PROVIDER_NAMES = provider_network.OPENCODE_PROVIDER_NAMES
DEFAULT_UPSTREAM_USER_AGENT = provider_network.DEFAULT_UPSTREAM_USER_AGENT
_PROVIDER_MODEL_IDENTITY = ProviderModelIdentityService(
    adapters=PROVIDER_ADAPTERS,
    aliases=PROVIDER_ALIASES,
    labels=PROVIDER_LABELS,
)
_PROVIDER_MODEL_IDENTITY_API = ProviderModelIdentityApi(_PROVIDER_MODEL_IDENTITY)


upstream_user_agent = provider_network.upstream_user_agent
with_upstream_user_agent = provider_network.with_upstream_user_agent


IP_FAMILY_ALIASES = provider_network.IP_FAMILY_ALIASES
IP_FAMILY_CHOICES = provider_network.IP_FAMILY_CHOICES


normalize_ip_family = provider_network.normalize_ip_family
default_provider_ip_family = provider_network.default_provider_ip_family
provider_ip_family = provider_network.provider_ip_family
socket_getaddrinfo_ip_family_policy = provider_network.socket_ip_family_policy


def provider_urlopen(
    req: urllib.request.Request,
    timeout: float,
    provider: str | None = None,
    pcfg: dict[str, Any] | None = None,
) -> Any:
    return provider_network.provider_urlopen(req, timeout, provider, pcfg, router_log)


ip_family_connectivity = provider_network.ip_family_connectivity


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
LOG_LEVEL_DEFAULT = LOG_LEVELS["ERROR"]
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
_MCP_NOTIFICATION_DEDUP_TTL_SECONDS = 3.0
_MCP_NOTIFICATION_WAIT_RECENT: dict[str, float] = {}
_MCP_NOTIFICATION_WAIT_RECENT_LOCK = threading.Lock()
_TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS = 10 * 60.0
_TOOL_SIDE_EFFECT_DEDUP_LOCK = threading.Lock()
_TOOL_SIDE_EFFECT_DEDUP_RECENT: dict[str, float] = {}
EVENT_BUS = EventBus()
USAGE_EVENT_SINK = JsonlUsageEventSink(
    USAGE_EVENTS_PATH,
    enabled=lambda: str(os.environ.get("CIEL_RUNTIME_USAGE_LOG", "1")).strip().lower()
    not in {"0", "false", "off", "no", ""},
)
# Tools Claude Code injects into every model's tool list that misfire when called
# by non-Anthropic models. See docs/notes from anthropics/claude-code issues
# #25720, #29950 and Piebald-AI/claude-code-system-prompts for tool semantics.


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

model_lookup_ids = ollama_catalog_policy.model_lookup_ids


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


ollama_library_model_parts = ollama_catalog_policy.library_model_parts
context_label_to_tokens = ollama_catalog_policy.context_label_to_tokens


def recommended_timeout_ms_for_context(context_tokens: int | None) -> int:
    return ollama_catalog_policy.recommended_timeout_ms(context_tokens, DEFAULT_REQUEST_TIMEOUT_MS)


ollama_model_catalog_key = ollama_catalog_policy.model_catalog_key


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


context_tokens_from_ollama_snippet = ollama_catalog_policy.context_tokens_from_snippet
parse_ollama_library_context_map = ollama_catalog_policy.parse_library_context_map


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


parse_ollama_library_context_limit = ollama_catalog_policy.parse_library_context_limit


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


ollama_context_model_matches = ollama_catalog_policy.context_model_matches


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


def side_effect_tool_call_dedupe_key(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    return tool_side_effect_dedupe_service().key(tool_name, tool_input)


def should_drop_duplicate_side_effect_tool_call(
    tool_name: str,
    tool_input: dict[str, Any],
    raw_name: str = "",
) -> bool:
    return tool_side_effect_dedupe_service().should_drop(tool_name, tool_input, raw_name)


def tool_side_effect_dedupe_service() -> ToolSideEffectDedupeService:
    return ToolSideEffectDedupeService(
        policy=ToolSideEffectDedupePolicy(
            frozenset(
                {"send_message", "send_dm", "send_file", "create_message", "create_dm", "post_message", "reply"}
            ),
            ttl_seconds=_TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS,
        ),
        repository=ToolSideEffectDedupeRepository(
            _TOOL_SIDE_EFFECT_DEDUP_RECENT,
            _TOOL_SIDE_EFFECT_DEDUP_LOCK,
        ),
        ports=ToolSideEffectDedupePorts(time.monotonic, append_tool_call_log, router_log),
    )


def _mcp_tool_leaf_name(tool_name: str) -> str:
    return McpNotificationWaitService.tool_leaf_name(tool_name)


def _is_mcp_notification_wait_tool(tool_name: str) -> bool:
    return mcp_notification_wait_service().is_wait_tool(tool_name)


def _mcp_notification_wait_timeout_cap_ms() -> int:
    return mcp_notification_wait_service().policy.timeout_cap_ms()


def _mcp_notification_wait_duplicate_cap_ms() -> int:
    return mcp_notification_wait_service().policy.duplicate_cap_ms()


def _mcp_notification_wait_duplicate_window_seconds() -> float:
    return mcp_notification_wait_service().policy.duplicate_window_seconds()


def _mcp_notification_wait_effective_cap_ms(tool_name: str) -> tuple[int, bool]:
    return mcp_notification_wait_service().effective_cap_ms(tool_name)


def cap_mcp_notification_wait_tool_input(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return mcp_notification_wait_service().cap_input(tool_name, tool_input)


def mcp_notification_wait_service() -> McpNotificationWaitService:
    return McpNotificationWaitService(
        policy=McpNotificationWaitPolicy(os.environ.get),
        repository=McpNotificationWaitRepository(
            _MCP_NOTIFICATION_WAIT_RECENT,
            _MCP_NOTIFICATION_WAIT_RECENT_LOCK,
        ),
        ports=McpNotificationWaitPorts(_lookup_tool_schema, time.time, router_log),
    )


def ui_text(key: str, lang: str | None = None) -> str:
    lang = lang or load_config().get("language", "en")
    return UI_TEXT.get(lang, UI_TEXT["en"]).get(key, UI_TEXT["en"].get(key, key))


DEFAULT_CONFIG: dict[str, Any] = build_default_config(provider_default_configurations())

def deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return merge_config_values(a, b)


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

_CONFIG_REPOSITORY_PROVIDER = ConfigRepositoryProvider()


def _normalize_loaded_config(cfg: dict[str, Any]) -> None:
    normalize_loaded_config(cfg, normalize_model_id)


def config_repository() -> JsonConfigRepository:
    return _CONFIG_REPOSITORY_PROVIDER.get(
        path=CONFIG_PATH,
        defaults=DEFAULT_CONFIG,
        merge=deep_merge,
        migrate=apply_config_migrations,
        normalize=_normalize_loaded_config,
    )


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


def model_cache_lifecycle_service() -> ModelCacheLifecycleService:
    return ModelCacheLifecycleService(
        ModelCacheLifecyclePorts(
            invalidate_config=invalidate_config_cache,
            artifact_paths=lambda: (
                CLAUDE_GATEWAY_CACHE,
                MODEL_LIST_CACHE_PATH,
                MODEL_REGISTRY_PATH,
            ),
            read_list_cache=read_model_list_cache,
            read_registry_models=read_model_registry_models,
            upstream_model_ids=upstream_model_ids,
            catalog_model_ids=ollama_catalog_model_ids,
            normalize_model_id=normalize_model_id,
            unique_model_ids=unique_model_ids,
            sorted_model_ids=sorted_model_ids,
            log=router_log,
        )
    )


def clear_model_cache() -> None:
    model_cache_lifecycle_service().clear()


normalize_provider = _PROVIDER_MODEL_IDENTITY_API.normalize_provider
normalize_provider_choice = normalize_runtime_provider_choice


slug = _PROVIDER_MODEL_IDENTITY_API.slug
model_sort_key = _PROVIDER_MODEL_IDENTITY_API.model_sort_key
sorted_model_ids = _PROVIDER_MODEL_IDENTITY_API.sorted_model_ids
unique_model_ids = _PROVIDER_MODEL_IDENTITY_API.unique_model_ids
normalize_model_id = _PROVIDER_MODEL_IDENTITY_API.normalize_model_id
strip_claude_context_suffix = _PROVIDER_MODEL_IDENTITY_API.strip_claude_context_suffix
upstream_api_model_id = _PROVIDER_MODEL_IDENTITY_API.upstream_api_model_id
alias_for = _PROVIDER_MODEL_IDENTITY_API.alias_for
unslug_provider_alias = _PROVIDER_MODEL_IDENTITY_API.unslug_provider_alias
display_name = _PROVIDER_MODEL_IDENTITY_API.display_name


def model_object(provider: str, model_id: str, pcfg: dict[str, Any] | None = None) -> dict[str, Any]:
    model_id = normalize_model_id(provider, model_id)
    alias = alias_for(provider, model_id)
    adapter = configured_provider_adapter(provider, pcfg or {})
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
    obj["ciel_runtime"].update(
        adapter.project_router_model_metadata(
            provider_contract_config(provider, pcfg or {}), model_id
        )
    )
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


_PROVIDER_CONTRACT_API = ProviderContractProjectionApi(
    adapter=configured_provider_adapter,
    contract=provider_contract_config,
    request_base=lambda provider, pcfg: provider_upstream_request_base(provider, pcfg),
    join_url=join_url,
)
provider_endpoint = _PROVIDER_CONTRACT_API.endpoint
provider_model_paths = _PROVIDER_CONTRACT_API.model_paths
provider_request_policy = _PROVIDER_CONTRACT_API.request_policy
provider_model_catalog_policy = _PROVIDER_CONTRACT_API.model_catalog_policy
preserves_anthropic_thinking_contract = _PROVIDER_CONTRACT_API.preserves_anthropic_thinking_contract
context_compaction_available = _PROVIDER_CONTRACT_API.context_compaction_available
provider_context_policy = _PROVIDER_CONTRACT_API.context_policy
provider_configuration_policy = _PROVIDER_CONTRACT_API.configuration_policy
provider_model_panel_badge = _PROVIDER_CONTRACT_API.model_panel_badge
provider_advisor_panel_notice = _PROVIDER_CONTRACT_API.advisor_panel_notice
provider_advisor_model_badge = _PROVIDER_CONTRACT_API.advisor_model_badge


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
    LegacyToolGuardShimInstaller(
        LegacyToolGuardShimServices(
            package_root=Path(__file__).resolve().parent,
            find_target=find_tool_guard_script,
            chmod=os.chmod,
            log=router_log,
        )
    ).install()


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


_LOG_LEVEL_API = LogLevelApi(log_level_repository)
current_log_level = _LOG_LEVEL_API.current
reset_log_level_cache = _LOG_LEVEL_API.reset_cache
log_level_name = _LOG_LEVEL_API.name
log_level_source = _LOG_LEVEL_API.source
log_level_status = _LOG_LEVEL_API.status
normalize_log_level = normalize_runtime_log_level
set_log_level_config = _LOG_LEVEL_API.set


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


_CONVERSATION_TURN_API = ConversationTurnCompatibilityApi(conversation_turn_policy)
plan_mode_active = _CONVERSATION_TURN_API.plan_mode_active
channel_llm_wake_text = _CONVERSATION_TURN_API.channel_llm_wake_text
channel_llm_wake_request = _CONVERSATION_TURN_API.channel_llm_wake_request
body_without_channel_llm_wake_prompt = (
    _CONVERSATION_TURN_API.body_without_channel_llm_wake_prompt
)
has_plan_mode_exit = _CONVERSATION_TURN_API.has_plan_mode_exit
allowed_prompt_tools_for_exit_plan_mode = (
    _CONVERSATION_TURN_API.allowed_prompt_tools_for_exit_plan_mode
)
exit_plan_mode_default_prompt_for_tool = (
    _CONVERSATION_TURN_API.exit_plan_mode_default_prompt_for_tool
)
backfill_exit_plan_mode_allowed_prompts = (
    _CONVERSATION_TURN_API.backfill_exit_plan_mode_allowed_prompts
)
plan_mode_tool_name_for_emit = _CONVERSATION_TURN_API.plan_mode_tool_name_for_emit
is_guard_feedback_text = _CONVERSATION_TURN_API.is_guard_feedback_text
strip_claude_code_system_reminders = (
    _CONVERSATION_TURN_API.strip_claude_code_system_reminders
)
is_claude_code_suggestion_mode_text = (
    _CONVERSATION_TURN_API.is_claude_code_suggestion_mode_text
)
user_intent_text_from_message = _CONVERSATION_TURN_API.user_intent_text_from_message
latest_user_text = _CONVERSATION_TURN_API.latest_user_text
latest_user_intent_message_index = (
    _CONVERSATION_TURN_API.latest_user_intent_message_index
)
latest_user_is_claude_code_suggestion_mode = (
    _CONVERSATION_TURN_API.latest_user_is_claude_code_suggestion_mode
)


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


likely_implementation_planning_request = (
    _CONVERSATION_TURN_API.likely_implementation_planning_request
)
non_actionable_short_response = _CONVERSATION_TURN_API.non_actionable_short_response
body_is_channel_prompt = _CONVERSATION_TURN_API.body_is_channel_prompt
should_auto_enter_plan_mode = _CONVERSATION_TURN_API.should_auto_enter_plan_mode
response_text_signals_plan_exit = _CONVERSATION_TURN_API.response_text_signals_plan_exit
should_auto_exit_plan_mode = _CONVERSATION_TURN_API.should_auto_exit_plan_mode
bash_command_looks_mutating = _CONVERSATION_TURN_API.bash_command_looks_mutating
latest_user_tool_result_details = _CONVERSATION_TURN_API.latest_user_tool_result_details
latest_tool_result_indicates_completed_work = (
    _CONVERSATION_TURN_API.latest_tool_result_indicates_completed_work
)
latest_user_tool_result_names = _CONVERSATION_TURN_API.latest_user_tool_result_names
latest_user_tool_result_text = _CONVERSATION_TURN_API.latest_user_tool_result_text
synthetic_tasklist_tool_use_id = _CONVERSATION_TURN_API.synthetic_tasklist_tool_use_id
recent_synthetic_tasklist_count = (
    _CONVERSATION_TURN_API.recent_synthetic_tasklist_count
)
tasklist_result_has_active_work = _CONVERSATION_TURN_API.tasklist_result_has_active_work
latest_tasklist_result_has_no_active_work = (
    _CONVERSATION_TURN_API.latest_tasklist_result_has_no_active_work
)
latest_assistant_text = _CONVERSATION_TURN_API.latest_assistant_text
short_resume_prompt = _CONVERSATION_TURN_API.short_resume_prompt
latest_user_looks_like_work_request = (
    _CONVERSATION_TURN_API.latest_user_looks_like_work_request
)
response_asks_for_user_choice_or_permission = (
    _CONVERSATION_TURN_API.response_asks_for_user_choice_or_permission
)
should_auto_continue_choice_question_with_tasklist = (
    _CONVERSATION_TURN_API.should_auto_continue_choice_question_with_tasklist
)
should_synthesize_tasklist_for_provider = (
    _CONVERSATION_TURN_API.should_synthesize_tasklist_for_provider
)
should_keep_work_alive_with_tasklist = (
    _CONVERSATION_TURN_API.should_keep_work_alive_with_tasklist
)
should_recover_empty_end_turn_with_tasklist = (
    _CONVERSATION_TURN_API.should_recover_empty_end_turn_with_tasklist
)
empty_end_turn_notice = _CONVERSATION_TURN_API.empty_end_turn_notice
empty_end_turn_notice_for_body = _CONVERSATION_TURN_API.empty_end_turn_notice_for_body


def append_synthetic_tasklist_to_message(
    message: dict[str, Any],
    model: str,
    source_body: dict[str, Any],
    reason: str,
    provider: str = "",
) -> dict[str, Any]:
    return synthetic_tasklist_policy().append(message, model, source_body, reason, provider)


def synthetic_tasklist_policy() -> SyntheticTasklistPolicy:
    return SyntheticTasklistPolicy(
        SyntheticTasklistPorts(
            should_synthesize_tasklist_for_provider,
            anthropic_content_to_text,
            should_auto_continue_choice_question_with_tasklist,
            lambda: int(time.time() * 1000),
            router_log,
        )
    )


def maybe_handle_plan_mode_tool_choice(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    return forced_plan_mode_controller().handle(handler, provider, pcfg, body)


def forced_plan_mode_controller() -> ForcedPlanModeController:
    return ForcedPlanModeController(
        ForcedPlanModePorts(
            forced_tool_choice_name,
            should_defer_forced_tool_choice_for_thinking,
            tool_names_in_body,
            plan_mode_active,
            synthetic_tool_use_response,
            write_json,
            router_log,
        )
    )


def filter_blocked_tools(provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    return tool_exposure_policy().filter(provider, pcfg, body)


def tool_exposure_policy() -> ToolExposurePolicy:
    return ToolExposurePolicy(
        ToolExposurePorts(
            resolve_blocked_tools,
            ultracode_workflow_preferred,
            plan_mode_active,
            router_log,
        )
    )


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


anthropic_model_family_from_id = anthropic_model_policy.model_family
anthropic_model_limit_hints = anthropic_model_policy.limit_hints
anthropic_model_runtime_hints = anthropic_model_policy.runtime_hints


CLAUDE_CODE_SUPPORTED_CAPABILITY_VALUES = anthropic_model_policy.SUPPORTED_CAPABILITIES


normalize_claude_code_supported_capabilities = anthropic_model_policy.normalize_capabilities


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


anthropic_recommended_preset_for_model = anthropic_model_policy.recommended_preset


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


_MODEL_REGISTRY_API = ModelRegistryApi(model_registry_repository)
read_model_registry = _MODEL_REGISTRY_API.read_registry
read_model_registry_models = _MODEL_REGISTRY_API.read_registry_models
read_model_registry_info = _MODEL_REGISTRY_API.read_registry_info
write_model_registry = _MODEL_REGISTRY_API.write_registry
read_model_list_cache = _MODEL_REGISTRY_API.read_list_cache
read_model_info_cache = _MODEL_REGISTRY_API.read_info_cache
write_model_list_cache = _MODEL_REGISTRY_API.write_list_cache




def cached_or_configured_model_ids(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return model_cache_lifecycle_service().cached_or_configured_ids(provider, pcfg)


def ensure_model_cache_for_launch(provider: str, pcfg: dict[str, Any]) -> None:
    """Populate the model list before building Claude Code launch env.

    Claude Code consumes ANTHROPIC_DEFAULT_*_MODEL only at process start. If
    those values are computed before the provider model list is available,
    family defaults collapse to the current model and /model cannot switch
    families reliably inside that session.
    """
    model_cache_lifecycle_service().ensure_for_launch(provider, pcfg)


_PROVIDER_CATALOG_SOURCES = provider_catalog_sources.ProviderCatalogSourceService(
    projection=provider_catalog_sources.ModelCatalogProjectionPorts(
        normalize_model_id=normalize_model_id,
        model_context=lambda item: model_context_field(item),
        positive_int=positive_int,
        provider_metadata=lambda provider: PROVIDER_ADAPTERS.create(
            provider
        ).project_model_metadata,
    ),
    http=provider_catalog_sources.ProviderCatalogHttpPorts(
        http_json=lambda *args, **kwargs: http_json(*args, **kwargs),
        join_url=join_url,
        upstream_base=lambda provider, pcfg: provider_upstream_request_base(
            provider, pcfg
        ),
        request_headers=lambda: with_upstream_user_agent(),
        urlopen=lambda *args, **kwargs: urllib.request.urlopen(*args, **kwargs),
    ),
    policy=provider_catalog_sources.ProviderCatalogPolicyPorts(
        unique_model_ids=unique_model_ids,
        log=lambda level, message: router_log(level, message),
    ),
    anthropic=provider_catalog_sources.AnthropicCatalogPolicy(
        docs_urls=tuple(ANTHROPIC_MODEL_DOCS_URLS),
        default_ids=tuple(ANTHROPIC_PUBLIC_MODEL_DEFAULT_IDS),
        limited_ids=tuple(ANTHROPIC_LIMITED_ACCESS_MODEL_IDS),
        fallback_ids=tuple(ANTHROPIC_PUBLIC_MODEL_FALLBACK_IDS),
        public_id_pattern=provider_catalog_sources.ANTHROPIC_PUBLIC_MODEL_ID_RE,
    ),
    fireworks=provider_catalog_sources.FireworksCatalogPolicy(
        default_account_id=FIREWORKS_DEFAULT_ACCOUNT_ID,
        api_base_url=FIREWORKS_API_BASE_URL,
        inference_base_url=FIREWORKS_INFERENCE_BASE_URL,
    ),
)
model_ids_from_response = _PROVIDER_CATALOG_SOURCES.model_ids_from_response
model_info_from_response = _PROVIDER_CATALOG_SOURCES.model_info_from_response
fireworks_account_id = _PROVIDER_CATALOG_SOURCES.fireworks_account_id
fireworks_management_base_url = (
    _PROVIDER_CATALOG_SOURCES.fireworks_management_base_url
)
fetch_fireworks_model_ids = _PROVIDER_CATALOG_SOURCES.fetch_fireworks_model_ids
fetch_text_url = _PROVIDER_CATALOG_SOURCES.fetch_text_url
anthropic_model_ids_from_docs_text = (
    _PROVIDER_CATALOG_SOURCES.anthropic_model_ids_from_docs_text
)
filter_anthropic_default_model_ids = (
    _PROVIDER_CATALOG_SOURCES.filter_anthropic_default_model_ids
)
fetch_anthropic_public_model_ids = (
    _PROVIDER_CATALOG_SOURCES.fetch_anthropic_public_model_ids
)


_PROVIDER_ENDPOINT_POLICY = ModelEndpointPolicy(
    ports=ModelEndpointPorts(
        normalize_model_id=normalize_model_id,
        strip_context_suffix=strip_claude_context_suffix,
        alias_for=alias_for,
        select_protocol=lambda provider, pcfg, model_id: configured_provider_adapter(
            provider, pcfg
        ).select_protocol(
            "anthropic_messages",
            provider_contract_config(provider, pcfg),
            model_id,
        ),
    ),
    presentation=ModelEndpointPresentation(
        aliases=OPENCODE_ENDPOINT_ALIASES,
        labels={
            "anthropic-messages": "messages",
            "openai-chat": "chat",
            "openai-responses": "responses",
            "google-generative": "gemini",
        },
        routed_protocols=frozenset({"anthropic-messages", "openai-chat"}),
    ),
)
opencode_zen_endpoint_kind = _PROVIDER_ENDPOINT_POLICY.zen_endpoint_kind
opencode_zen_model_supported_by_router = (
    _PROVIDER_ENDPOINT_POLICY.zen_model_supported
)
normalize_opencode_endpoint_kind = _PROVIDER_ENDPOINT_POLICY.normalize_endpoint_kind
opencode_endpoint_override = _PROVIDER_ENDPOINT_POLICY.endpoint_override
opencode_go_endpoint_kind = _PROVIDER_ENDPOINT_POLICY.go_endpoint_kind
opencode_endpoint_kind = _PROVIDER_ENDPOINT_POLICY.endpoint_kind
opencode_model_supported_by_router = _PROVIDER_ENDPOINT_POLICY.model_supported
opencode_endpoint_display = _PROVIDER_ENDPOINT_POLICY.endpoint_display


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


fetch_anthropic_api_model_ids = (
    _PROVIDER_CATALOG_SOURCES.fetch_anthropic_api_model_ids
)


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


def router_rate_limit_service() -> RouterRateLimitService:
    return RouterRateLimitService(
        paths=RouterRateLimitPaths(CONFIG_DIR, RATE_LIMIT_STATE_PATH, _RATE_LIMIT_LOCK),
        repository=rate_limit_repository(),
        ports=RouterRateLimitPorts(
            current_model_id=current_upstream_model_id,
            api_key_count=provider_api_key_count,
            positive_int=positive_int,
            log=router_log,
            now=time.time,
            sleep=time.sleep,
        ),
    )


_ROUTER_RATE_LIMIT_API = RouterRateLimitApi(router_rate_limit_service)
router_rate_limit_legacy_key = _ROUTER_RATE_LIMIT_API.legacy_key
router_rate_limit_configured_rpm = _ROUTER_RATE_LIMIT_API.configured_rpm
router_rate_limit_rpm = _ROUTER_RATE_LIMIT_API.rpm
router_rate_limit_key = _ROUTER_RATE_LIMIT_API.key
router_rate_limit_state_entry = _ROUTER_RATE_LIMIT_API.state_entry
router_rate_limit_effective_rpm = _ROUTER_RATE_LIMIT_API.effective_rpm
router_rate_limit_capacity = _ROUTER_RATE_LIMIT_API.capacity
router_rate_limit_recent = _ROUTER_RATE_LIMIT_API.recent
router_rate_limit_usage = _ROUTER_RATE_LIMIT_API.usage
record_router_rate_usage = _ROUTER_RATE_LIMIT_API.record_usage


parse_retry_after_seconds = rate_limit_policy.retry_after_seconds
format_duration_seconds = rate_limit_policy.format_duration
first_header = rate_limit_policy.first_header
first_int_in_header = rate_limit_policy.first_integer
rate_limit_reset_seconds = rate_limit_policy.reset_seconds


learn_router_rate_limit_headers = _ROUTER_RATE_LIMIT_API.learn_headers
register_router_rate_limit_backoff = _ROUTER_RATE_LIMIT_API.register_backoff


def api_key_cooldown_service() -> ApiKeyCooldownService:
    return ApiKeyCooldownService(
        ApiKeyCooldownPorts(
            repository=rate_limit_repository(),
            rotation_name=provider_api_key_rotation_name,
            config_keys=provider_config_api_keys,
            meaningful_key=meaningful_key,
            log=router_log,
        )
    )


_API_KEY_COOLDOWN_API = ApiKeyCooldownCompatibilityApi(api_key_cooldown_service)
_api_key_cooldown_state_key = _API_KEY_COOLDOWN_API.state_key
api_key_cooldown_reset_seconds = _API_KEY_COOLDOWN_API.reset_seconds
register_api_key_cooldown = _API_KEY_COOLDOWN_API.register
api_key_cooldown_until = _API_KEY_COOLDOWN_API.cooldown_until
provider_live_api_key_count = _API_KEY_COOLDOWN_API.live_key_count
provider_has_live_api_key = _API_KEY_COOLDOWN_API.has_live_key
reset_api_key_cooldowns_for_router_start = _API_KEY_COOLDOWN_API.reset_for_router_start
retry_after_exceeds_request_timeout = (
    _API_KEY_COOLDOWN_API.retry_after_exceeds_request_timeout
)


apply_router_rate_limit = _ROUTER_RATE_LIMIT_API.apply
wait_for_router_rate_limit_penalty = _ROUTER_RATE_LIMIT_API.wait_for_penalty


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


def nvidia_proxy_runtime() -> NvidiaProxyRuntime:
    return NvidiaProxyRuntime(
        NvidiaProxyRuntimeConfig(NCP_ENV, NCP_LOG, NCP_PYPI_PACKAGE),
        NvidiaProxyRuntimePorts(
            load_config,
            read_env_file,
            is_url_up,
            find_executable,
            positive_int,
            http_json,
            join_url,
        ),
    )


_NVIDIA_RUNTIME_API = NvidiaRuntimeApi(nvidia_proxy_runtime)
nvidia_upstream_base_url = _NVIDIA_RUNTIME_API.upstream_base_url
nvidia_proxy_base_url = _NVIDIA_RUNTIME_API.proxy_base_url
nvidia_api_key = _NVIDIA_RUNTIME_API.api_key
install_ncp_proxy = _NVIDIA_RUNTIME_API.install_proxy
ncp_module_available = _NVIDIA_RUNTIME_API.module_available
ncp_proxy_executable = _NVIDIA_RUNTIME_API.proxy_executable
ensure_ncp = _NVIDIA_RUNTIME_API.ensure
ncp_model_id_for_nvidia_hosted = _NVIDIA_RUNTIME_API.model_id


_PROVIDER_REQUEST_ACCESS = ProviderRequestAccessService(
    ports=ProviderRequestAccessPorts(
        request_policy=lambda provider, pcfg: provider_request_policy(
            provider, pcfg
        ),
        select_api_key=lambda provider, pcfg: select_provider_api_key(
            provider, pcfg
        ),
        meaningful_key=project_meaningful_key_value,
        adapter_headers=lambda provider, pcfg, key: configured_provider_adapter(
            provider, pcfg
        ).build_headers(provider_contract_config(provider, pcfg), key),
        inbound_credentials=lambda key, inbound: (
            credential.headers
            if (
                credential := resolve_anthropic_credentials(key, inbound)
            )
            is not None
            else None
        ),
    ),
    effects=ProviderRequestAccessEffects(
        user_agent_headers=with_upstream_user_agent,
        ncp_model_id=lambda model: ncp_model_id_for_nvidia_hosted(model),
        normalize_provider=normalize_provider,
    ),
)
provider_upstream_model = _PROVIDER_REQUEST_ACCESS.upstream_model
provider_requires_streaming = _PROVIDER_REQUEST_ACCESS.requires_streaming
key_from_request_headers = _PROVIDER_REQUEST_ACCESS.key_from_headers
provider_headers = _PROVIDER_REQUEST_ACCESS.headers
get_current_provider = _PROVIDER_REQUEST_ACCESS.current_provider


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
    return runtime_command_factory().materialize(
        runtime_name,
        executable,
        env,
        provider,
        pcfg,
        mode=mode,
        protocol=protocol,
        cwd=cwd,
        enable_channels=enable_channels,
        passthrough=passthrough,
        options=options,
    )


def runtime_command_factory() -> RuntimeCommandFactory:
    return RuntimeCommandFactory(RuntimeCommandFactoryPorts(parse_api_key_list, RUNTIME_ADAPTERS.create))


_RUNTIME_MODE_POLICY = RuntimeModePolicy(
    parse_bool=parse_bool,
    runtime_providers={
        "anthropic": "anthropic",
        "agy": "agy",
        "codex": "codex",
    },
)
native_anthropic_enabled = _RUNTIME_MODE_POLICY.native_anthropic
anthropic_routed_enabled = _RUNTIME_MODE_POLICY.anthropic_routed
direct_native_anthropic_enabled = _RUNTIME_MODE_POLICY.direct_anthropic
native_agy_enabled = _RUNTIME_MODE_POLICY.native_agy
agy_routed_enabled = _RUNTIME_MODE_POLICY.agy_routed
direct_native_agy_enabled = _RUNTIME_MODE_POLICY.direct_agy
native_codex_enabled = _RUNTIME_MODE_POLICY.native_codex
codex_routed_enabled = _RUNTIME_MODE_POLICY.codex_routed
direct_native_codex_enabled = _RUNTIME_MODE_POLICY.direct_codex


def upstream_model_ids(provider: str, pcfg: dict[str, Any], force_refresh: bool = False) -> list[str]:
    return fetch_upstream_model_ids(
        provider, pcfg, force_refresh,
        services=provider_models.ProviderModelServices(
            storage=provider_models.ModelCatalogStorage(
                read_model_list_cache=read_model_list_cache,
                write_model_list_cache=write_model_list_cache,
                write_model_registry=write_model_registry,
                router_log=router_log,
            ),
            http=provider_models.ModelCatalogHttp(
                http_json=http_json,
                join_url=join_url,
                with_upstream_user_agent=with_upstream_user_agent,
                lm_studio_api_base=lm_studio_api_base,
                nvidia_hosted_list_headers=nvidia_hosted_list_headers,
                nvidia_upstream_base_url=nvidia_upstream_base_url,
            ),
            sources=provider_models.ProviderCatalogSources(
                ANTHROPIC_MODEL_DOCS_URLS=ANTHROPIC_MODEL_DOCS_URLS,
                fetch_anthropic_api_model_ids=fetch_anthropic_api_model_ids,
                fetch_anthropic_public_model_ids=fetch_anthropic_public_model_ids,
                fetch_fireworks_model_ids=fetch_fireworks_model_ids,
                fireworks_account_id=fireworks_account_id,
                fireworks_management_base_url=fireworks_management_base_url,
            ),
            response_codec=provider_models.ModelCatalogResponseCodec(
                model_ids_from_response=model_ids_from_response,
                model_info_from_response=model_info_from_response,
            ),
            policy=provider_models.ModelCatalogPolicy(
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
    return ProviderRuntimeInfoService.model_context(item)


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


_OLLAMA_RUNTIME_API = OllamaRuntimeApi(ollama_runtime_service)
ollama_api_base = _OLLAMA_RUNTIME_API.api_base
ollama_provider_api_base = _OLLAMA_RUNTIME_API.provider_api_base
ollama_show_parameters = _OLLAMA_RUNTIME_API.show_parameters
fetch_ollama_api_model_specs = _OLLAMA_RUNTIME_API.fetch_model_specs
ollama_model_id_matches = _OLLAMA_RUNTIME_API.model_id_matches
ollama_runtime_info = _OLLAMA_RUNTIME_API.runtime_info
ollama_output_cap_for_context = _OLLAMA_RUNTIME_API.output_cap


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


_LM_STUDIO_LIFECYCLE_API = LmStudioLifecycleApi(lm_studio_model_lifecycle)


def lm_studio_runtime_info(pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    return discover_lm_studio_runtime(
        pcfg,
        lm_studio_runtime_services(),
        timeout=timeout,
    )


lm_studio_v1_model_info = _LM_STUDIO_LIFECYCLE_API.v1_model_info
lm_studio_loaded_instance_ids = _LM_STUDIO_LIFECYCLE_API.loaded_instance_ids
lm_studio_target_context = _LM_STUDIO_LIFECYCLE_API.target_context
lm_studio_load_timeout_seconds = _LM_STUDIO_LIFECYCLE_API.load_timeout_seconds
lm_studio_load_model = _LM_STUDIO_LIFECYCLE_API.load_model
lm_studio_unload_loaded_instances = _LM_STUDIO_LIFECYCLE_API.unload_loaded_instances
lm_studio_load_response_context = _LM_STUDIO_LIFECYCLE_API.load_response_context
ensure_lm_studio_model_loaded_for_context = _LM_STUDIO_LIFECYCLE_API.ensure_loaded_for_context


def upstream_model_runtime_info(provider: str, pcfg: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    return provider_runtime_info_service().discover(provider, pcfg, timeout)


def upstream_model_context_limit(provider: str, pcfg: dict[str, Any], timeout: float = 3.0) -> int | None:
    return provider_runtime_info_service().context_limit(provider, pcfg, timeout)


def provider_runtime_info_service() -> ProviderRuntimeInfoService:
    return ProviderRuntimeInfoService(
        ProviderRuntimeInfoPorts(
            lambda provider: PROVIDER_COMPATIBILITY.resolve(provider).runtime_model_info_strategy,
            lm_studio_runtime_info,
            provider_upstream_request_base,
            current_upstream_model_id,
            http_json,
            join_url,
            provider_model_list_headers,
            positive_int,
            router_log,
        )
    )


def model_map_for(provider: str, pcfg: dict[str, Any], fetch: bool = True) -> dict[str, str]:
    ids = upstream_model_ids(provider, pcfg) if fetch else cached_or_configured_model_ids(provider, pcfg)
    return {alias_for(provider, mid): mid for mid in ids}


def current_alias(cfg: dict[str, Any]) -> str:
    provider, pcfg = get_current_provider(cfg)
    cur = normalize_model_id(provider, pcfg.get("current_model") or "model")
    if cur.startswith(f"ciel-runtime-{provider}-"):
        return cur
    return alias_for(provider, cur)


_PROVIDER_NATIVE_COMPATIBILITY = ProviderNativeCompatibilityPolicy(
    native_enabled=lambda provider, pcfg: configured_provider_adapter(
        provider, pcfg
    ).router_native_anthropic_enabled(
        provider_contract_config(provider, pcfg),
        str(pcfg.get("current_model") or ""),
    ),
    compatibility_groups={
        "ollama": frozenset({"ollama"}),
        "vllm": frozenset({"vllm"}),
        "nim": frozenset({"self-hosted-nim"}),
        "lm_studio": frozenset({"lm-studio"}),
        "nvidia": frozenset({"nvidia-hosted"}),
        "deepseek": frozenset({"deepseek"}),
        "opencode": frozenset(OPENCODE_PROVIDER_NAMES),
        "kimi": frozenset({"kimi"}),
        "zai": frozenset({"zai"}),
        "fireworks": frozenset({"fireworks"}),
    },
)
provider_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.native_enabled
ollama_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.ollama
vllm_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.vllm
nim_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.nim
lm_studio_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.lm_studio
nvidia_hosted_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.nvidia
deepseek_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.deepseek
opencode_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.opencode
kimi_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.kimi
zai_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.zai
fireworks_native_compat_enabled = _PROVIDER_NATIVE_COMPATIBILITY.fireworks


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


_PROVIDER_MODEL_SELECTION_API = ProviderModelSelectionApi(provider_model_selection)
current_upstream_model_id = _PROVIDER_MODEL_SELECTION_API.current_upstream_model_id
provider_placeholder_model_ids = _PROVIDER_MODEL_SELECTION_API.provider_placeholder_model_ids
current_model_needs_provider_selection = _PROVIDER_MODEL_SELECTION_API.current_model_needs_provider_selection
ensure_current_model_from_provider_list = _PROVIDER_MODEL_SELECTION_API.ensure_current_model_from_provider_list
launch_model_id = _PROVIDER_MODEL_SELECTION_API.launch_model_id
resolve_requested_model = _PROVIDER_MODEL_SELECTION_API.resolve_requested_model
resolve_tool_model_references = _PROVIDER_MODEL_SELECTION_API.resolve_tool_model_references
list_model_objects = _PROVIDER_MODEL_SELECTION_API.list_model_objects
list_model_objects_for_request = _PROVIDER_MODEL_SELECTION_API.list_model_objects_for_request


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
    policy = ProviderLaunchEndpointPolicy(
        groups=ProviderLaunchEndpointGroups(
            native_runtimes=frozenset(
                {"anthropic", "codex", "agy", "ollama", "ollama-cloud"}
            ),
            auto_detect=frozenset(AUTO_DETECT_NATIVE_COMPAT_PROVIDERS),
            claude_anthropic=frozenset(CLAUDE_ANTHROPIC_ENDPOINT_PROVIDERS),
            codex_openai=frozenset(CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS),
            model_specific=frozenset(OPENCODE_PROVIDER_NAMES),
        ),
        query=ProviderLaunchEndpointQueries(
            detect_native_compat=auto_detect_native_compat_for_base_url,
            endpoint_kind=opencode_endpoint_kind,
        ),
    )
    return policy.preferred_native_compat(runtime, provider, pcfg)


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


def provider_endpoint_route_adapter() -> ProviderEndpointRouteAdapter:
    return ProviderEndpointRouteAdapter(
        ProviderEndpointRoutePorts(
            decorate_headers=with_upstream_user_agent,
            request=urllib.request.Request,
            urlopen=urllib.request.urlopen,
            http_error=urllib.error.HTTPError,
        )
    )


def endpoint_route_exists(url: str, headers: dict[str, str], timeout: float = 1.5) -> bool | None:
    return provider_endpoint_route_adapter().exists(url, headers, timeout)


def provider_endpoint_probe_policy() -> ProviderEndpointProbePolicy:
    return ProviderEndpointProbePolicy(
        projection=ProviderEndpointProbeProjection(
            upstream_base=provider_upstream_request_base,
            native_base=native_anthropic_base_url,
            join_url=join_url,
        ),
        query=ProviderEndpointProbeQueries(
            primary_headers=provider_headers,
            fallback_headers=provider_model_list_headers,
            route_exists=endpoint_route_exists,
        ),
    )


def auto_detect_native_compat_for_base_url(provider: str, pcfg: dict[str, Any]) -> tuple[bool | None, str]:
    return provider_endpoint_probe_policy().detect_native_compat(
        provider,
        pcfg,
        frozenset(AUTO_DETECT_NATIVE_COMPAT_PROVIDERS),
    )


def endpoint_probe_status_label(value: bool | None) -> str:
    return ProviderEndpointProbePolicy.status_label(value)


def compatibility_endpoint_probe_headers(provider: str, pcfg: dict[str, Any]) -> dict[str, str]:
    return provider_endpoint_probe_policy().headers_for(provider, pcfg)


def compatibility_endpoint_probe_lines(provider: str, pcfg: dict[str, Any], timeout: float = 1.5) -> list[str]:
    return provider_endpoint_probe_policy().report(
        provider,
        pcfg,
        frozenset({"agy", "codex", "ollama", "ollama-cloud"}),
        timeout,
    )


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


def runtime_activity_repository() -> RuntimeActivityRepository:
    return RuntimeActivityRepository(
        RuntimeActivityPaths(ROUTER_ACTIVITY_PATH, CONTEXT_COMPACT_ACTIVITY_PATH, CONTEXT_USAGE_PATH),
        RuntimeActivityClock(
            time.time,
            lambda: time.strftime("%Y-%m-%dT%H:%M:%S"),
            lambda: f"{os.getpid()}.{time.time_ns()}",
        ),
        RuntimeActivityEffects(EVENT_BUS.publish, router_log),
    )


def write_router_activity(event: str, provider: str, model: str | None = None, **fields: Any) -> None:
    runtime_activity_repository().router_activity(event, provider, model, **fields)


def write_context_compact_activity(provider: str, model: str | None = None, **fields: Any) -> None:
    runtime_activity_repository().context_compact(provider, model, **fields)


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
    runtime_activity_repository().context_usage(
        provider,
        pcfg,
        body,
        source,
        estimate_tokens=estimate_tokens,
        context_limit=context_limit_for_status,
    )


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


def llm_config_http_controller() -> LlmConfigHttpController:
    return LlmConfigHttpController(
        LlmConfigIdentity(
            load_config,
            get_current_provider,
            current_alias,
            applied_preset_id,
            context_setting_status,
            timeout_profile_status,
            PROVIDER_LABELS,
        ),
        LlmConfigPanels(
            llm_option_panel_rows,
            llm_option_prompt_default,
            llm_preset_panel_rows,
            context_setup_panel_rows,
            timeout_profile_panel_rows,
        ),
        LlmConfigMutations(
            set_model_config,
            set_advisor_model_config,
            apply_llm_preset_config,
            apply_context_setup_config,
            apply_timeout_profile_config,
            set_llm_option_config,
        ),
        LlmConfigHttpIO(EVENT_BUS.publish, write_json, router_log),
    )


def llm_config_payload(messages: list[str] | None = None) -> dict[str, Any]:
    return llm_config_http_controller().payload(messages)


def apply_timeout_profile_config(provider: str, profile_id: str) -> list[str]:
    cfg = load_config()
    pcfg = cfg["providers"][provider]
    lines = apply_timeout_profile_to_provider(pcfg, profile_id, cfg.get("language", "en"))
    save_config(cfg)
    clear_model_cache()
    return lines


def handle_llm_config_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    return llm_config_http_controller().handle_get(handler, path)


def handle_llm_config_post(handler: BaseHTTPRequestHandler, path: str, body: dict[str, Any]) -> bool:
    return llm_config_http_controller().handle_post(handler, path, body)


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
    return EventHttpAdapter.query_int(params, name, default)


def event_http_adapter() -> EventHttpAdapter:
    return EventHttpAdapter(
        EventHttpPorts(
            EVENT_BUS.recent, EVENT_BUS.wait_after, render_events_html,
            write_text_response, write_json, router_log,
        )
    )


def handle_events_get(handler: BaseHTTPRequestHandler, path: str, query: dict[str, list[str]]) -> bool:
    return event_http_adapter().handle_get(handler, path, query)


def _safe_segment(value: str, fallback: str = "item") -> str:
    return ChatFileRepository.safe_segment(value, fallback)


def chat_file_max_bytes() -> int:
    return ChatFileRepository.configured_max_bytes()


def chat_file_repository() -> ChatFileRepository:
    return ChatFileRepository(
        CHAT_FILES_DIR,
        ROUTER_BASE,
        ChatFilePorts(timestamp=time.time, timestamp_ns=time.time_ns),
    )


def store_chat_file_upload(body: dict[str, Any]) -> dict[str, Any]:
    return chat_file_repository().store_upload(body)


def store_chat_file_from_path(path_value: Any, name: str | None = None, content_type: str | None = None) -> dict[str, Any]:
    return chat_file_repository().store_path(path_value, name, content_type)


def chat_file_markdown_lines(uploads: list[dict[str, Any]]) -> list[str]:
    return ChatFileRepository.markdown_lines(uploads)


def chat_file_message_text(message: str, uploads: list[dict[str, Any]]) -> str:
    return ChatFileRepository.message_text(message, uploads)


def _chat_init_next_id() -> int:
    global _CHAT_NEXT_ID
    if _CHAT_NEXT_ID is not None:
        return _CHAT_NEXT_ID
    _CHAT_NEXT_ID = _chat_scan_max_id() + 1
    return _CHAT_NEXT_ID


def channel_message_repository() -> ChannelMessageRepository:
    return ChannelMessageRepository(path=CHAT_MESSAGES_PATH, log=router_log, max_bytes=CHAT_MESSAGES_MAX_BYTES)


def _chat_scan_max_id() -> int:
    return channel_message_repository().max_id()


def _channel_launch_recent_seconds() -> float:
    return channel_runtime_environment_policy().launch_recent_seconds()


def channel_runtime_environment_policy() -> ChannelRuntimeEnvironmentPolicy:
    return ChannelRuntimeEnvironmentPolicy(
        environment=os.environ,
        launch_recent_default=CHANNEL_LLM_LAUNCH_RECENT_SECONDS_DEFAULT,
        probe_timeout_default=CHANNEL_PROBE_DEFAULT_TIMEOUT_SECONDS,
    )


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
    return ChannelMessageDedupeService(
        ports=ChannelMessageDedupePorts(
            stable_key=_chat_message_stable_dedupe_key,
            fallback_key=_chat_message_fallback_dedupe_key,
            recent_rows=_chat_message_recent_rows_locked,
            launch_guard=_channel_llm_launch_guard,
            timestamp_seconds=_chat_message_time_seconds,
            now=time.time,
        ),
        fallback_ttl_seconds=CHAT_MESSAGE_FALLBACK_DEDUPE_TTL_SECONDS,
    ).duplicate(message)


def append_chat_message(payload: dict[str, Any]) -> dict[str, Any]:
    global _CHAT_NEXT_ID
    message = channel_message_repository().append(
        payload,
        ChannelMessageAppendPorts(_CHAT_CONDITION, _chat_messages_file_lock, _chat_message_duplicate_locked, _as_string_list),
    )
    _CHAT_NEXT_ID = int(message.get("id") or 0) + 1
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


def codex_mcp_local_sse_hold_seconds() -> float:
    return McpSplitProxyHttpAdapter.local_sse_hold_seconds()


def codex_mcp_split_proxy_enabled() -> bool:
    return env_bool(os.environ.get("CIEL_RUNTIME_CODEX_MCP_SPLIT_PROXY"), False)


def mcp_split_proxy_http_adapter() -> McpSplitProxyHttpAdapter:
    return McpSplitProxyHttpAdapter(
        McpSplitProxyHttpPorts(
            codex_mcp_split_proxy_server,
            _codex_mcp_split_proxy_upstream_url,
            mcp_server_runtime_headers,
            _copy_upstream_response_headers,
            is_client_disconnect_error,
            write_json,
            router_log,
        ),
        _NATIVE_CHANNEL_NOTIFICATION_METHOD,
    )


def handle_codex_mcp_split_proxy_get(handler: BaseHTTPRequestHandler, path: str) -> bool:
    return mcp_split_proxy_http_adapter().handle_get(handler, path)


def handle_codex_mcp_split_proxy_request(
    handler: BaseHTTPRequestHandler,
    path: str,
    raw_body: bytes,
    method: str,
) -> bool:
    return mcp_split_proxy_http_adapter().handle_request(handler, path, raw_body, method)


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


COMPACT_TEXT_ONLY_SYSTEM_PROMPT = (
    "Claude Code is compacting the conversation. Return only the requested summary text. "
    "Do not call tools, browse, inspect files, or request external data during compaction."
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


_CONTEXT_SUMMARY_API = ContextSummaryCompatibilityApi(
    policy_factory=context_summary_policy,
    compact_system_prompt=COMPACT_TEXT_ONLY_SYSTEM_PROMPT,
    append_system=project_append_anthropic_system_texts,
    log=router_log,
)
is_claude_code_compact_request = _CONTEXT_SUMMARY_API.is_compact_request
compact_request_text_only_body = _CONTEXT_SUMMARY_API.text_only_body
compact_tool_value_for_prompt = _CONTEXT_SUMMARY_API.compact_tool_value
tool_input_for_prompt = _CONTEXT_SUMMARY_API.tool_input
compact_message_text_for_prompt = _CONTEXT_SUMMARY_API.message_text
compact_message_summary_line = _CONTEXT_SUMMARY_API.summary_line
context_guard_chunk_count = _CONTEXT_SUMMARY_API.guard_chunk_count
build_chunked_context_guard_summary = _CONTEXT_SUMMARY_API.guard_summary
context_compact_message_text = _CONTEXT_SUMMARY_API.compact_message
context_compact_instruction_index = _CONTEXT_SUMMARY_API.instruction_index
context_compact_chunk_target_tokens = _CONTEXT_SUMMARY_API.chunk_target_tokens
context_compact_summary_output_tokens = _CONTEXT_SUMMARY_API.summary_output_tokens
split_messages_for_context_compact = _CONTEXT_SUMMARY_API.split_messages
build_context_compact_chunk_prompt = _CONTEXT_SUMMARY_API.chunk_prompt
context_compact_extract_text = _CONTEXT_SUMMARY_API.extract_response_text
build_context_compact_reduce_prompt = _CONTEXT_SUMMARY_API.reduce_prompt


def truncate_for_prompt(text: str, limit: int) -> str:
    return ContextSummaryPolicy.truncate(text, limit)


def is_claude_code_persisted_output_text(text: str) -> bool:
    return ContextSummaryPolicy.is_persisted_output(text)


def _message_tool_markers_for_summary(message: dict[str, Any]) -> list[str]:
    return ContextSummaryPolicy.tool_markers(message)


def _compact_chunk_ranges(count: int, chunks: int) -> list[tuple[int, int]]:
    return ContextSummaryPolicy.chunk_ranges(count, chunks)


def context_compact_parallel_sessions(pcfg: dict[str, Any] | None, chunks: int) -> int:
    return 1


CONTEXT_COMPACT_MAP_SYSTEM_PROMPT = (
    "You are compacting one segment of a larger Claude Code conversation. "
    "Return only a concise but durable summary of this segment. Preserve user goals, "
    "decisions, file paths, tool results, unresolved tasks, errors, and any facts needed "
    "to continue later. Do not call tools."
)


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


def advisor_shortcut_intercept_enabled(
    provider: str, pcfg: dict[str, Any]
) -> bool:
    adapter = configured_provider_adapter(provider, pcfg)
    return adapter.intercepts_advisor_shortcut(
        provider_contract_config(provider, pcfg)
    )


def anthropic_system_with_advisor(system: Any, extra_system_texts: list[str] | None = None) -> list[dict[str, Any]]:
    """Build the advisor request system blocks, keeping the session identity first.

    Anthropic rejects OAuth-authenticated requests whose first system block is
    not the original Claude Code identity block (HTTP 429 ``rate_limit_error``
    with message "Error"), so the inbound session's first system block stays
    first and verbatim; the advisor instruction rides behind it.
    """
    return AdvisorAnthropicSystemPolicy(
        review_prompt=ADVISOR_REVIEW_PROMPT,
        content_to_text=anthropic_content_to_text,
    ).project(system, extra_system_texts)


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


_OPENAI_REASONING_POLICY = OpenAiReasoningPolicy(
    adapter_for=configured_provider_adapter,
    config_for=provider_contract_config,
)
openai_chat_reasoning_passback_enabled = _OPENAI_REASONING_POLICY.passback_enabled
openai_chat_reasoning_passback_enabled_for_body = (
    _OPENAI_REASONING_POLICY.passback_enabled_for_body
)
should_omit_openai_chat_tool_choice = _OPENAI_REASONING_POLICY.should_omit_tool_choice


_ROUTER_ACCESS_POLICY = RouterAccessPolicy(
    environ=os.environ,
    parse_bool=parse_bool,
    parse_env_bool=env_bool,
    load_config=load_config,
)
_ROUTER_EXTERNAL_TOKEN_REPOSITORY = RouterExternalTokenRepository(
    path=ROUTER_EXTERNAL_TOKEN_PATH,
    config_dir=CONFIG_DIR,
    environ=os.environ,
)
router_debug_external_access_enabled = _ROUTER_ACCESS_POLICY.external_access_enabled
router_bind_host = _ROUTER_ACCESS_POLICY.bind_host
router_external_access_token = _ROUTER_EXTERNAL_TOKEN_REPOSITORY.get
ensure_router_external_access_token = _ROUTER_EXTERNAL_TOKEN_REPOSITORY.ensure


def router_request_allowed(handler: BaseHTTPRequestHandler, cfg: dict[str, Any] | None = None) -> bool:
    return _ROUTER_ACCESS_POLICY.request_allowed(
        handler, cfg, router_external_access_token
    )


def set_router_debug_external_access_config(value: Any) -> list[str]:
    return RouterAccessConfigService(
        policy=_ROUTER_ACCESS_POLICY,
        ports=RouterAccessMutationPorts(
            load_config=load_config,
            save_config=save_config,
            clear_model_cache=clear_model_cache,
            ensure_token=ensure_router_external_access_token,
        ),
    ).set_external_access(value)


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


_OLLAMA_CONTEXT_POLICY = OllamaRequestContextPolicy(
    environ=os.environ,
    positive_int=positive_int,
    estimate_tokens=estimate_tokens,
    model_matches=ollama_context_model_matches,
    preset_names=frozenset(LLM_PRESETS),
    default_request_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
)
ctx_bucket = _OLLAMA_CONTEXT_POLICY.context_bucket
ollama_provider_context_limit = _OLLAMA_CONTEXT_POLICY.provider_context_limit
ollama_preserve_configured_context_cap = (
    _OLLAMA_CONTEXT_POLICY.preserve_configured_context_cap
)
ollama_effective_context_limit = _OLLAMA_CONTEXT_POLICY.effective_context_limit
ollama_num_ctx_for_payload = _OLLAMA_CONTEXT_POLICY.num_ctx_for_payload
ollama_num_ctx_status = _OLLAMA_CONTEXT_POLICY.num_ctx_status
ollama_extra_options = _OLLAMA_CONTEXT_POLICY.extra_options
ollama_options_status = _OLLAMA_CONTEXT_POLICY.options_status
ollama_request_timeout_seconds = _OLLAMA_CONTEXT_POLICY.request_timeout_seconds
ollama_context_error_limit = _OLLAMA_CONTEXT_POLICY.context_error_limit
ollama_context_retry_config = _OLLAMA_CONTEXT_POLICY.context_retry_config
ollama_context_limit_for_budget = _OLLAMA_CONTEXT_POLICY.context_limit_for_budget

_OUTPUT_BUDGET_POLICY = OutputBudgetPolicy(
    positive_int=positive_int,
    estimate_tokens=estimate_tokens,
    provider_options=ollama_extra_options,
)
configured_output_tokens = _OUTPUT_BUDGET_POLICY.configured_tokens
cap_output_tokens_for_context = _OUTPUT_BUDGET_POLICY.cap_tokens_for_context
context_guard_reserve_tokens = _OUTPUT_BUDGET_POLICY.reserve_tokens


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


def import_session_http_controller() -> ImportSessionHttpController:
    return ImportSessionHttpController(
        ImportSessionHttpPorts(
            is_request=is_import_session_request,
            response_text=import_session_response_text,
            load_config=load_config,
            current_alias=current_alias,
            current_provider=get_current_provider,
            estimate_tokens=estimate_tokens,
            write_openai=write_openai_responses_response,
            write_anthropic=write_anthropic_text_response,
            publish_event=EVENT_BUS.publish,
        )
    )


def maybe_handle_import_session_request(
    handler: BaseHTTPRequestHandler,
    body: dict[str, Any],
    *,
    client_runtime: str,
    response_format: str = "anthropic",
    source_body: dict[str, Any] | None = None,
) -> bool:
    return import_session_http_controller().handle(
        handler,
        body,
        client_runtime=client_runtime,
        response_format=response_format,
        source_body=source_body,
    )


def advisor_shortcut_controller() -> AdvisorShortcutController:
    return AdvisorShortcutController(
        AdvisorShortcutPorts(
            should_intercept=advisor_shortcut_intercept_enabled,
            is_request=is_advisor_request,
            provider_supported=advisor_provider_supported,
            call_text=call_advisor_text,
            write_anthropic=write_anthropic_text_response,
            load_config=load_config,
            current_alias=current_alias,
        )
    )


def maybe_handle_advisor_request(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any], body: dict[str, Any]) -> bool:
    return advisor_shortcut_controller().handle(handler, provider, pcfg, body)


def router_shortcut_controller() -> RouterShortcutController:
    return RouterShortcutController(
        response=ShortcutResponsePorts(
            load_config=load_config,
            current_alias=current_alias,
            current_provider=get_current_provider,
            write_anthropic=write_anthropic_text_response,
            publish_event=EVENT_BUS.publish,
        ),
        predicates=ShortcutPredicates(
            router_debug=is_router_debug_request,
            version=is_version_request,
            channel_clear=is_channel_clear_request,
            live_llm_options=is_live_llm_options_request,
            live_api_keys=is_live_api_keys_request,
        ),
        debug=RouterDebugShortcutPorts(
            value=router_debug_value_from_body,
            external_enabled=router_debug_external_access_enabled,
            bind_host=router_bind_host,
            set_external=set_router_debug_external_access_config,
            schedule_restart=schedule_router_process_restart,
            version=VERSION,
            source_fingerprint=SOURCE_FINGERPRINT,
            config_dir=CONFIG_DIR,
        ),
        channel=ChannelShortcutPorts(
            value=channel_clear_value_from_body,
            clear=clear_channel_backlog,
            status=channel_backlog_status,
        ),
        live=LiveConfigShortcutPorts(
            llm_value=live_llm_options_value_from_body,
            handle_llm=handle_live_llm_options_action,
            api_key_value=live_api_keys_value_from_body,
            handle_api_keys=handle_live_api_keys_action,
            api_key_count=provider_api_key_count,
        ),
    )


def maybe_handle_router_debug_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    return router_shortcut_controller().handle_router_debug(handler, body)


def maybe_handle_version_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    return router_shortcut_controller().handle_version(handler, body)


_format_channel_backlog_status_lines = RouterShortcutController.channel_status_lines


def maybe_handle_channel_clear_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    return router_shortcut_controller().handle_channel_clear(handler, body)


def maybe_handle_live_llm_options_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    return router_shortcut_controller().handle_live_llm_options(handler, body)


def live_api_key_status_lines(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return live_api_key_controller().status(provider, pcfg)


def live_api_key_controller() -> LiveApiKeyController:
    return LiveApiKeyController(
        LiveApiKeyPorts(
            load_config=load_config,
            current_provider=get_current_provider,
            status_line=api_key_status_line,
            stored_mask=stored_api_key_mask,
            store_input=store_api_key_input_config,
        )
    )


def handle_live_api_keys_action(value: str) -> tuple[list[str], bool]:
    return live_api_key_controller().handle(value)


def maybe_handle_live_api_keys_request(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> bool:
    return router_shortcut_controller().handle_live_api_keys(handler, body)


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
    return streaming_anthropic.rebatch_anthropic_sse_text(
        handler,
        resp,
        model=model,
        word_chunking=word_chunking,
        source_body=source_body,
        preserve_thinking=preserve_thinking,
        normalize_tool_use=normalize_tool_use,
        provider=provider,
        services=streaming_anthropic.AnthropicStreamServices(
            io=streaming_anthropic.AnthropicStreamIO(
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
            tool_projection=streaming_anthropic.AnthropicToolProjection(
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
            tool_policy=streaming_anthropic.AnthropicToolPolicy(
                should_drop_duplicate_side_effect_tool_call=should_drop_duplicate_side_effect_tool_call,
                should_drop_emitted_tool_call=should_drop_emitted_tool_call,
                should_repair_anthropic_passthrough_tool_input=should_repair_anthropic_passthrough_tool_input,
            ),
            conversation=streaming_anthropic.AnthropicConversationContext(
                backfill_exit_plan_mode_allowed_prompts=backfill_exit_plan_mode_allowed_prompts,
                body_ultracode_runtime_enabled=body_ultracode_runtime_enabled,
                empty_end_turn_notice_for_body=empty_end_turn_notice_for_body,
                has_tool=has_tool,
                latest_user_intent_message_index=latest_user_intent_message_index,
                latest_user_is_claude_code_suggestion_mode=latest_user_is_claude_code_suggestion_mode,
                latest_user_tool_result_names=latest_user_tool_result_names,
                recent_synthetic_tasklist_count=recent_synthetic_tasklist_count,
            ),
            continuation=streaming_anthropic.AnthropicContinuationPolicy(
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
    return streaming_anthropic.ollama_stream_to_anthropic_sse(
        handler, resp, model, word_chunking=word_chunking, provider=provider,
        source_body=source_body, idle_timeout=idle_timeout,
        services=streaming_anthropic.OllamaStreamServices(
            io=streaming_anthropic.OllamaStreamIO(
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
            trace=streaming_anthropic.OllamaStreamTrace(
                dump_response_for_trace=dump_response_for_trace,
                finish_outgoing_sse_trace=finish_outgoing_sse_trace,
                make_outgoing_sse_trace=make_outgoing_sse_trace,
                record_outgoing_sse_event=record_outgoing_sse_event,
            ),
            tool_projection=streaming_anthropic.OllamaToolProjection(
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
            continuation=streaming_anthropic.OllamaContinuationPolicy(
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
    return streaming_anthropic.forward_openai_chat_to_anthropic_sse(
        handler, resp, model, provider, source_body=source_body,
        start_index=start_index, word_chunking=word_chunking,
        input_tokens=input_tokens, input_bytes=input_bytes,
        services=streaming_anthropic.OpenAIChatStreamServices(
            io=streaming_anthropic.OpenAIChatStreamIO(
                PSEUDO_TOOL_END=PSEUDO_TOOL_END,
                PSEUDO_TOOL_START=PSEUDO_TOOL_START,
                _split_word_buffer=_split_word_buffer,
                positive_int=positive_int,
                router_log=router_log,
                write_anthropic_open_stream_stop=write_anthropic_open_stream_stop,
                write_router_activity=write_router_activity,
            ),
            tool_projection=streaming_anthropic.OpenAIChatToolProjection(
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
            continuation=streaming_anthropic.OpenAIChatContinuationPolicy(
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
    return CodexRoutedHeaderPolicy(
        decorate=with_upstream_user_agent
    ).project(inbound_headers)


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
    CodexBackendHttpAdapter.copy_response_headers(handler, headers)


def codex_backend_http_adapter() -> CodexBackendHttpAdapter:
    return CodexBackendHttpAdapter(
        CODEX_ROUTED_UPSTREAM_BASE,
        CodexBackendRequestPorts(
            codex_responses_body_with_channel_context, begin_pending_channel_delivery,
            codex_routed_upstream_headers, provider_urlopen, provider_request_timeout_seconds,
        ),
        CodexBackendRetryPorts(
            codex_capacity_retry_limit, read_codex_response_preamble, upstream_retry_wait_seconds,
            router_log, EVENT_BUS.publish, time.sleep,
        ),
    )


def codex_backend_upstream_url(request_path: str, query: str = "") -> str:
    return codex_backend_http_adapter().upstream_url(request_path, query)


def forward_codex_backend_json(
    handler: BaseHTTPRequestHandler,
    provider: str,
    pcfg: dict[str, Any],
    body: dict[str, Any],
    *,
    mutate_responses: bool = False,
) -> dict[str, Any] | None:
    return codex_backend_http_adapter().forward_json(
        handler, provider, pcfg, body, mutate_responses=mutate_responses
    )


def codex_capacity_retry_limit() -> int:
    raw = str(os.environ.get("CIEL_RUNTIME_CODEX_CAPACITY_RETRIES") or "3").strip()
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return 3


def forward_codex_backend_get(handler: BaseHTTPRequestHandler, provider: str, pcfg: dict[str, Any]) -> None:
    codex_backend_http_adapter().forward_get(handler, provider, pcfg)


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
    openai_responses_router.handle_openai_responses_request(
        handler,
        cfg,
        provider,
        pcfg,
        body,
        openai_responses_router.OpenAIResponsesServices(
            core=openai_responses_router.OpenAIResponsesCore(
                event_bus=EVENT_BUS,
                request_id=lambda: f"{os.getpid()}-{time.time_ns()}",
                input_as_list=_responses_input_as_list,
                is_client_disconnect=is_client_disconnect_error,
                log=router_log,
            ),
            conversion=openai_responses_router.OpenAIResponsesConversion(
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
            routing=openai_responses_router.OpenAIResponsesRouting(
                maybe_import_session=maybe_handle_import_session_request,
                codex_routed_enabled=codex_routed_enabled,
                forward_codex=forward_codex_responses,
                dump_request=dump_request_for_trace,
                normalize_provider_wire=normalize_request_for_provider_wire,
                collect_message=collect_provider_message_for_responses,
            ),
            delivery=openai_responses_router.OpenAIResponsesDelivery(
                begin=begin_pending_channel_delivery,
                mark_success=mark_pending_channel_delivery_success,
                mark_failed=mark_pending_channel_delivery_failed,
                commit=commit_pending_channel_delivery_cursors,
            ),
            output=openai_responses_router.OpenAIResponsesOutput(
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


def build_claude_router_services() -> claude_router.ClaudeRouterServices:
    return claude_router.ClaudeRouterServices(
        core=claude_router.ClaudeRouterCore(
            event_bus=EVENT_BUS,
            log=router_log,
            try_write_json=try_write_json,
        ),
        count_tokens=claude_router.ClaudeRouterCountTokens(
            estimate_tokens=estimate_tokens,
            write_context_usage=write_context_usage,
            write_json=write_json,
        ),
        pipeline=claude_router.ClaudeRouterPipeline(
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
        shortcuts=claude_router.ClaudeRouterShortcuts(
            plan_mode=maybe_handle_plan_mode_tool_choice,
            router_debug=maybe_handle_router_debug_request,
            version=maybe_handle_version_request,
            channel_clear=maybe_handle_channel_clear_request,
            import_session=maybe_handle_import_session_request,
            llm_options=maybe_handle_live_llm_options_request,
            api_keys=maybe_handle_live_api_keys_request,
            advisor=maybe_handle_advisor_request,
        ),
        delivery=claude_router.ClaudeRouterDelivery(
            begin=begin_pending_channel_delivery,
            commit=commit_pending_channel_delivery_cursors,
            mark_failed=mark_pending_channel_delivery_failed,
            mark_success=mark_pending_channel_delivery_success,
            is_client_disconnect=is_client_disconnect_error,
            write_activity=write_router_activity,
        ),
        routing=claude_router.ClaudeRouterRouting(
            forward_ollama=forward_ollama_api_chat,
            forward_openai=forward_openai_compatible_chat,
            select_protocol=select_provider_protocol,
            request_policy=provider_request_policy,
            resolve_model=resolve_requested_model,
            provider_labels=PROVIDER_LABELS,
            write_json=write_json,
        ),
        normalization=claude_router.ClaudeRouterNativeNormalization(
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
        transport=claude_router.ClaudeRouterTransport(
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
        response=claude_router.ClaudeRouterResponse(
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
        claude_router.ClaudeRouter(services=build_claude_router_services()),
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
    return provider_choice_controller().select(choice)


def provider_choice_controller() -> ProviderChoiceController:
    return ProviderChoiceController(
        ProviderChoicePorts(
            load_config=load_config,
            save_config=save_config,
            clear_model_cache=clear_model_cache,
            provider_has_api_key=provider_has_api_key,
            select_standard_provider=set_provider_config,
        )
    )


def set_base_url_config(provider: str, url: str) -> list[str]:
    return provider_endpoint_service().set_base_url(provider, url)


def normalize_provider_base_url(provider: str, pcfg: dict[str, Any], url: str) -> str:
    return configured_provider_adapter(provider, pcfg).normalize_base_url(url)


def provider_endpoint_service() -> ProviderEndpointService:
    return ProviderEndpointService(
        policy=ProviderEndpointPolicy(frozenset(AUTO_DETECT_NATIVE_COMPAT_PROVIDERS).__contains__),
        ports=ProviderEndpointPorts(
            load_config=load_config,
            save_config=save_config,
            clear_model_cache=clear_model_cache,
            normalize_base_url=normalize_provider_base_url,
            detect_native_compat=auto_detect_native_compat_for_base_url,
            ensure_current_model=ensure_current_model_from_provider_list,
        ),
    )


def set_model_config(value: str) -> list[str]:
    return model_selection_controller().select(value)


def apply_provider_model_selection_updates(
    provider: str,
    pcfg: dict[str, Any],
    model_id: str,
) -> None:
    adapter = configured_provider_adapter(provider, pcfg)
    contract = provider_contract_config(provider, pcfg)
    pcfg.update(adapter.model_selection_config_updates(contract, model_id))


def model_selection_controller() -> ModelSelectionController:
    return ModelSelectionController(
        ModelMutationConfigPorts(load_config, get_current_provider, save_config, clear_model_cache),
        ModelMutationPolicyPorts(
            model_map_for, unslug_provider_alias, normalize_model_id, apply_provider_model_profile,
            read_model_info_cache, positive_int, model_preset, apply_provider_model_selection_updates,
            alias_for, format_context_tokens,
        ),
        ModelMutationEffectPorts(
            sync_ollama_library_context_limit, cap_context_settings_to_model_capacity,
            auto_apply_recommended_llm_preset_for_model, apply_recommended_timeout_for_model_context,
            read_model_list_cache,
        ),
    )


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
    return credential_management_service().store_one(provider, key)


def clear_api_key_config(provider: str) -> list[str]:
    return credential_management_service().clear(provider)


def store_api_keys_config(provider: str, keys: list[str]) -> list[str]:
    return credential_management_service().store_many(provider, keys)


def mask_secret(value: str | None) -> str:
    return project_mask_secret(value)


def secret_fingerprint(value: str | None, length: int = 12) -> str:
    return project_secret_fingerprint(value, length)


def redact_sensitive_text(text: str) -> str:
    return project_redact_sensitive_text(text)


def redact_sensitive_obj(value: Any) -> Any:
    return project_redact_sensitive_obj(value)


def stored_api_key_mask(provider: str, pcfg: dict[str, Any]) -> str:
    keys = provider_config_api_keys(provider, pcfg)
    if not keys:
        return "not set"
    primary = f"{mask_secret(keys[0])}; fp {secret_fingerprint(keys[0])}"
    if len(keys) == 1:
        return primary
    return f"{len(keys)} keys (round-robin; primary {primary})"


def store_api_key_input_config(provider: str, raw_value: str) -> list[str]:
    return credential_management_service().store_input(provider, raw_value)


def credential_management_service() -> CredentialManagementService:
    return CredentialManagementService(
        persistence=CredentialPersistencePorts(
            load_config=load_config,
            save_config=save_config,
            clear_model_cache=clear_model_cache,
            parse_keys=parse_api_key_list,
            clear_requested=api_key_clear_requested,
            rotation_name=provider_api_key_rotation_name,
        ),
        external=ExternalCredentialPorts(
            enabled=frozenset({"nvidia-hosted"}).__contains__,
            store=store_nvidia_api_key,
            clear=clear_nvidia_api_key,
            has_key=lambda: bool(parse_api_key_list(read_env_file(NCP_ENV).get("NVIDIA_API_KEY"))),
            normalize_provider_config=ensure_nvidia_hosted_base_url,
            location=NCP_ENV,
        ),
        presentation=CredentialPresentationPorts(mask_secret, secret_fingerprint),
        rotation=CredentialRotationRepository(_API_KEY_ROTATION_CURSOR, _API_KEY_ROTATION_LOCK),
        config_location=CONFIG_PATH,
    )


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


def configuration_cli_controller() -> ConfigurationCliController:
    return ConfigurationCliController(
        config=ConfigurationCliConfigPorts(
            load=load_config,
            save=save_config,
            current_provider=get_current_provider,
        ),
        provider=ConfigurationCliProviderPorts(
            normalize_choice=normalize_provider_choice,
            normalize_provider=normalize_provider,
            panel_rows=provider_panel_rows,
            menu_label=provider_menu_label,
            set_choice=set_provider_choice_config,
            set_provider=set_provider_config,
            set_base_url=set_base_url_config,
        ),
        model=ConfigurationCliModelPorts(
            cached_ids=cached_or_configured_model_ids,
            alias_for=alias_for,
            read_cache=read_model_list_cache,
            set_model=set_model_config,
            upstream_ids=upstream_model_ids,
            set_advisor=set_advisor_model_config,
            advisor_uses_builtin=lambda provider, pcfg: provider_ui_policy(provider, pcfg).uses_native_advisor,
        ),
        display=ConfigurationCliDisplayPorts(
            log_level_names=LOG_LEVEL_NAMES,
            log_level_status=log_level_status,
            log_level_name=log_level_name,
            set_log_level=set_log_level_config,
            languages=LANGUAGES,
            web_tools_config_path=WEB_TOOLS_MCP_CONFIG,
        ),
        io=ConfigurationCliIO(output=print),
    )


def cmd_provider(args: argparse.Namespace) -> None:
    configuration_cli_controller().provider_command(args.name)


def cmd_set_api_key(args: argparse.Namespace) -> None:
    credential_cli_controller().set_one(args)


def cmd_set_api_keys(args: argparse.Namespace) -> None:
    credential_cli_controller().set_many(args)


def cmd_api_key(args: argparse.Namespace) -> None:
    credential_cli_controller().manage(args)


def credential_cli_controller() -> CredentialCliController:
    return CredentialCliController(
        policy=CredentialCliPolicy(
            frozenset(
                {
                    "anthropic", "ollama-cloud", "deepseek", "opencode", "opencode-go",
                    "kimi", "nvidia-hosted", "openrouter", "fireworks",
                }
            )
        ),
        ports=CredentialCliPorts(
            normalize_provider=normalize_provider,
            load_config=load_config,
            key_count=provider_api_key_count,
            primary_key=provider_primary_api_key,
            mask=mask_secret,
            fingerprint=secret_fingerprint,
            clear_requested=api_key_clear_requested,
            clear=clear_api_key_config,
            store_input=store_api_key_input_config,
            store_many=store_api_keys_config,
        ),
        io=CredentialCliIO(sys.stdin.isatty, getpass.getpass, print),
    )


def cmd_base_url(args: argparse.Namespace) -> None:
    configuration_cli_controller().base_url_command(
        args.provider,
        args.url,
    )


def cmd_model(args: argparse.Namespace) -> None:
    configuration_cli_controller().model_command(args.value)


def cmd_advisor_model(args: argparse.Namespace) -> None:
    configuration_cli_controller().advisor_model_command(args.value)


def cmd_models(args: argparse.Namespace) -> None:
    configuration_cli_controller().models_command(args.provider)


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
    return provider_status_service().lines()


def provider_status_service() -> ProviderStatusService:
    return ProviderStatusService(
        projection=ProviderStatusProjectionPorts(
            get_current_provider=get_current_provider,
            mode_label=provider_mode_label,
            direct_native_anthropic=direct_native_anthropic_enabled,
            configured_adapter=configured_provider_adapter,
            contract_config=provider_contract_config,
            ollama_num_ctx_status=ollama_num_ctx_status,
            ollama_options_status=ollama_options_status,
            ollama_think_status=ollama_think_status,
            current_upstream_model=current_upstream_model_id,
            current_alias=current_alias,
        ),
        runtime=RuntimeStatusPorts(
            load_config=load_config,
            log_level_status=log_level_status,
            channel_status_text=channel_status_text,
            channel_delivery_mode=channel_delivery_mode,
            router_up=router_up,
            router_base=ROUTER_BASE,
            config_path=CONFIG_PATH,
        ),
    )


def cmd_status(_: argparse.Namespace) -> None:
    print("\n".join(status_lines()))


def cmd_log_level(args: argparse.Namespace) -> None:
    configuration_cli_controller().log_level_command(
        getattr(args, "value", None)
    )


def cmd_language(args: argparse.Namespace) -> None:
    configuration_cli_controller().language_command(args.value)


def set_web_search_enabled(enabled: bool) -> None:
    cfg = load_config()
    cfg.setdefault("web_search", {})["auto_for_non_native"] = enabled
    save_config(cfg)


def cmd_web_search(args: argparse.Namespace) -> None:
    configuration_cli_controller().web_search_command(args.value)


def cmd_web_fetch(args: argparse.Namespace) -> None:
    configuration_cli_controller().web_fetch_command(args.value)


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
    return channel_runtime_environment_policy().probe_timeout_seconds()


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


_mcp_config_passthrough_values = (
    ClaudeMcpConfigPathPolicy.passthrough_values
)
strip_mcp_config_passthrough = (
    ClaudeMcpConfigPathPolicy.strip_passthrough
)


def _safe_mcp_proxy_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return safe[:80] or "server"


_mcp_config_paths_from_passthrough = (
    ClaudeMcpConfigPathPolicy.passthrough_paths
)


def claude_mcp_config_paths(passthrough: list[str] | None = None, cwd: Path | None = None, home: Path | None = None) -> list[Path]:
    return ClaudeMcpConfigPathPolicy.paths(
        passthrough or [],
        cwd or Path.cwd(),
        home or HOME,
    )


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
    return ClaudeMcpConfigPathPolicy.existing_paths(
        passthrough or [],
        cwd or Path.cwd(),
        home or HOME,
    )


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
    service = ManagedMcpDiscoveryService(
        paths=ManagedMcpDiscoveryPaths(
            web_tools=WEB_TOOLS_MCP_CONFIG,
            proxy=MCP_PROXY_CONFIG,
        ),
        ports=ManagedMcpDiscoveryPorts(
            read_generated=_read_mcp_servers_from_generated_file,
            load_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
            log=router_log,
        ),
        native_channel_names=frozenset(
            name.casefold() for name in _NATIVE_ROUTER_CHANNEL_NAMES
        ),
    )
    return service.discover(cwd or Path.cwd())


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


def native_auto_channel_capable_server_names(passthrough: list[str] | None = None) -> list[str]:
    """External channel-capable servers that are also in current MCP discovery."""
    discovered = set(discovered_claude_mcp_servers(passthrough or []).keys())
    if not discovered:
        return []
    return [name for name in cached_external_channel_capable_server_names() if name in discovered]


def start_codex_mcp_channel_sse_for_launch(
    cfg: dict[str, Any],
    codex_mcp_config: Path | None,
    allowed_server_names: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    service = CodexChannelSseLaunchService(
        query=CodexChannelSseQueryPorts(
            delivery_mode=channel_delivery_mode,
            channel_specs=channel_specs_for_launch,
            server_names=_server_names_from_channel_specs,
            capable_names=codex_channel_capable_mcp_server_names,
            dedupe=_dedupe_strings,
        ),
        effects=CodexChannelSseEffects(
            auto_start=auto_start_sse_channels_from_mcp_configs,
            log=router_log,
        ),
        native_channel_names=frozenset(
            name.casefold() for name in _NATIVE_ROUTER_CHANNEL_NAMES
        ),
    )
    return service.start(cfg, codex_mcp_config, allowed_server_names)


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


_CHANNEL_CONFIG_API = ChannelConfigApi(channel_config_service)
parse_passthrough_channel_specs = _CHANNEL_CONFIG_API.parse_passthrough_channel_specs
auto_import_passthrough_channels = _CHANNEL_CONFIG_API.auto_import_passthrough_channels


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


channel_specs_for_launch = _CHANNEL_CONFIG_API.channel_specs_for_launch


_CHANNEL_PROBE_API = ChannelProbeCompatibilityApi(
    service_factory=channel_probe_service,
)
_builtin_router_probe_record = _CHANNEL_PROBE_API.builtin_record
_server_transport_label = _CHANNEL_PROBE_API.transport_label
_probe_mcp_servers_to_records = _CHANNEL_PROBE_API.probe
read_channel_probe_cache = _CHANNEL_PROBE_API.read_cache
_write_channel_probe_cache = _CHANNEL_PROBE_API.write_cache
refresh_channel_probe_cache = _CHANNEL_PROBE_API.refresh
cached_channel_probe_servers = _CHANNEL_PROBE_API.servers
channel_probe_record_bucket = _CHANNEL_PROBE_API.bucket
cached_channel_capable_server_names = _CHANNEL_PROBE_API.capable_names
cached_external_channel_capable_server_names = _CHANNEL_PROBE_API.external_capable_names
cached_channel_source_paths_for_specs = _CHANNEL_PROBE_API.source_paths
_server_names_from_channel_specs = _CHANNEL_PROBE_API.server_names_from_specs


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
    candidate_names = channel_candidate_server_names_for_launch(
        cfg, passthrough, extra_config_paths=extra_config_paths
    )
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


is_channel_spec_tagged = _CHANNEL_CONFIG_API.is_channel_spec_tagged
normalize_channel_passthrough = _CHANNEL_CONFIG_API.normalize_channel_passthrough


def channel_status_text(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    channels = channel_specs(cfg)
    if not channels:
        return "off"
    return f"{len(channels)} channel{'s' if len(channels) != 1 else ''}"


def set_channel_development_enabled(enabled: bool) -> list[str]:
    return ["Channel wake delivery is always enabled by Ciel Runtime."]


normalize_channel_delivery = _CHANNEL_CONFIG_API.normalize_channel_delivery
channel_delivery_mode = _CHANNEL_CONFIG_API.channel_delivery_mode
set_channel_delivery_config = _CHANNEL_CONFIG_API.set_channel_delivery_config
add_channel_spec = _CHANNEL_CONFIG_API.add_channel_spec
remove_channel_spec = _CHANNEL_CONFIG_API.remove_channel_spec
clear_channel_specs = _CHANNEL_CONFIG_API.clear_channel_specs


def channel_cli_controller() -> ChannelCliController:
    return ChannelCliController(
        ChannelCliView(
            load_config=load_config,
            status_text=channel_status_text,
            delivery_mode=channel_delivery_mode,
            configured_specs=channel_specs,
            official_plugins=OFFICIAL_CHANNEL_PLUGINS,
            output=print,
        ),
        ChannelCliCommands(
            add=add_channel_spec,
            development=set_channel_development_enabled,
            remove=remove_channel_spec,
            clear=clear_channel_specs,
            refresh=refresh_channel_probe_cache,
            report=lambda result: channel_probe_report_lines(
                result,
                channel_probe_default_timeout(),
                ChannelProbeReportServices(
                    bucket=channel_probe_record_bucket,
                    format_timestamp=lambda value: time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(value)
                    ),
                ),
            ),
            set_delivery=set_channel_delivery_config,
        ),
    )


def cmd_channels(args: argparse.Namespace) -> None:
    channel_cli_controller().run(args)


def cmd_channel_delivery(args: argparse.Namespace) -> None:
    channel_cli_controller().delivery(args)


def cmd_ollama_native(args: argparse.Namespace) -> None:
    provider_option_cli_controller().native(args)


def provider_option_policy() -> ProviderOptionPolicy:
    return ProviderOptionPolicy(
        normalize_claude_code_supported_capabilities=normalize_claude_code_supported_capabilities,
        normalize_ip_family=normalize_ip_family,
        normalize_model_id=normalize_model_id,
        normalize_opencode_endpoint_kind=normalize_opencode_endpoint_kind,
        parse_bool=parse_bool,
        parse_config_value=parse_config_value,
        positive_int=positive_int,
        sampling=ProviderSamplingPolicy(),
    )


def apply_ollama_option(pcfg: dict[str, Any], token: str) -> None:
    mutate_ollama_option(pcfg, token, policy=provider_option_policy())


def cmd_ollama_options(args: argparse.Namespace) -> None:
    provider_option_cli_controller().ollama_options(args)


PROVIDER_OPTION_PROVIDERS = ("anthropic", "agy", "codex", "vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "ollama", "ollama-cloud", "deepseek", "opencode", "opencode-go", "kimi", "openrouter", "fireworks", "zai")
PROVIDER_SAMPLING_OPTION_PROVIDERS = ("vllm", "lm-studio", "nvidia-hosted", "self-hosted-nim", "openrouter")
PROVIDER_SAMPLING_OPTIONS = ("temperature", "top_p", "top_k")


def sampling_option_key(key: str) -> str | None:
    return ProviderSamplingPolicy().option_key(key)


def validate_sampling_option(key: str, value: Any) -> float | int:
    return ProviderSamplingPolicy().validate(key, value)


def provider_option_status_projection() -> ProviderOptionStatusProjection:
    return ProviderOptionStatusProjection(
        tuple(PROVIDER_SAMPLING_OPTIONS),
        ProviderOptionStatusPorts(
            configured_adapter=configured_provider_adapter,
            contract_config=provider_contract_config,
            rate_usage=router_rate_limit_usage,
            ollama_num_ctx=ollama_num_ctx_status,
            ollama_options_status=ollama_options_status,
            ip_family=provider_ip_family,
            parse_bool=parse_bool,
            tool_choice_status=provider_tool_choice_status,
            ollama_extra_options=ollama_extra_options,
            anthropic_routed=anthropic_routed_enabled,
        ),
    )


def provider_sampling_status(pcfg: dict[str, Any]) -> list[str]:
    return provider_option_status_projection().sampling(pcfg)


def provider_options_status(provider: str, pcfg: dict[str, Any]) -> str:
    return provider_option_status_projection().provider(provider, pcfg)


def llm_options_status(provider: str, pcfg: dict[str, Any]) -> str:
    return provider_option_status_projection().llm(provider, pcfg)


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
    return llm_presets.normalize_preset_token(value)


def resolve_llm_preset_id(value: str) -> str | None:
    return llm_presets.PresetIdentityPolicy(
        LLM_PRESETS,
        llm_preset_command_name,
    ).resolve(value)










def timeout_profile_service() -> TimeoutProfileService:
    return TimeoutProfileService(
        TimeoutProfileSettings(
            default_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            profiles=TIMEOUT_PRESETS,
            localized_profiles=TIMEOUT_PRESET_I18N,
            llm_preset_timeouts=LLM_PRESET_TIMEOUT_MS,
        ),
        TimeoutProfilePorts(
            positive_int=positive_int,
            pad_cells=pad_cells,
            ui_text=ui_text,
            format_minutes=format_timeout_minutes,
        ),
    )


_TIMEOUT_PROFILE_API = TimeoutProfileApi(
    timeout_profile_service,
    lambda: str(load_config().get("language", "en")),
)
llm_preset_timeout_ms = _TIMEOUT_PROFILE_API.llm_preset_timeout_ms
active_llm_preset_timeout_ms = _TIMEOUT_PROFILE_API.active_llm_preset_timeout_ms
timeout_profile_id_for_ms = _TIMEOUT_PROFILE_API.timeout_profile_id_for_ms
timeout_profile_text = _TIMEOUT_PROFILE_API.timeout_profile_text
timeout_profile_status = _TIMEOUT_PROFILE_API.timeout_profile_status
timeout_profile_idle_ms = _TIMEOUT_PROFILE_API.timeout_profile_idle_ms
timeout_profile_panel_rows = _TIMEOUT_PROFILE_API.timeout_profile_panel_rows
apply_timeout_profile_to_provider = _TIMEOUT_PROFILE_API.apply_timeout_profile_to_provider
with_preset_timeout_tokens = _TIMEOUT_PROFILE_API.with_preset_timeout_tokens




def is_qwen36_plus_model_id(model_id: str) -> bool:
    return ModelContextHintPolicy.is_qwen36_plus(model_id)


def is_kimi_k3_model_id(model_id: str) -> bool:
    return model_context_hint_policy().is_kimi_k3(model_id)


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
    return model_context_hint_policy().zai_hint(model_id)


def model_context_hint_policy() -> ModelContextHintPolicy:
    return ModelContextHintPolicy(
        ZAI_MODEL_CONTEXT_HINTS,
        ModelContextHintPorts(
            strip_context_suffix=strip_claude_context_suffix,
            catalog_context=ollama_catalog_context_for_model,
            model_preset=model_preset,
            positive_int=positive_int,
        ),
    )


def model_context_hint_from_model_id(model_id: str) -> int | None:
    return model_context_hint_policy().resolve(model_id)


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
    return resolve_small_context_output_cap(
        context_window,
        positive_int=positive_int,
    )


def cap_output_tokens_to_context_ratio(provider: str, pcfg: dict[str, Any], configured: int | None) -> int | None:
    return apply_output_token_cap(
        configured,
        provider_context_policy(provider, pcfg),
        context_limit_for_status(provider, pcfg),
        positive_int=positive_int,
    )


def cap_output_settings_to_context_ratio(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return apply_output_context_cap(
        pcfg,
        provider_context_policy(provider, pcfg),
        context_limit_for_status(provider, pcfg),
        positive_int=positive_int,
        format_context=format_context_tokens,
    )


def cached_current_model_info(provider: str, pcfg: dict[str, Any]) -> dict[str, Any]:
    return provider_model_spec_service().current_info(provider, pcfg)


def provider_model_spec_service() -> ProviderModelSpecService:
    return ProviderModelSpecService(
        ModelSpecLookupPorts(
            read_cache=read_model_info_cache,
            normalize_model=normalize_model_id,
            upstream_model=current_upstream_model_id,
            strip_context_suffix=strip_claude_context_suffix,
        ),
        ModelSpecMutationPorts(
            positive_int=positive_int,
            apply_model_profile=apply_provider_model_profile,
            context_policy=provider_context_policy,
            ollama_model_matches=ollama_context_model_matches,
            preserve_ollama_cap=ollama_preserve_configured_context_cap,
            format_context=format_context_tokens,
        ),
        ModelSpecRefreshPorts(refresh_models=upstream_model_ids),
    )


def apply_current_model_specs_to_provider(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return provider_model_spec_service().apply(provider, pcfg)


def refresh_current_model_specs_for_auto_llm(provider: str, pcfg: dict[str, Any]) -> list[str]:
    return provider_model_spec_service().refresh(provider, pcfg)


apply_lm_studio_loaded_context_guard = _LM_STUDIO_LIFECYCLE_API.apply_loaded_context_guard


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


def provider_timeout_policy() -> ProviderTimeoutPolicy:
    return ProviderTimeoutPolicy(
        ProviderTimeoutSettings(
            default_ms=DEFAULT_REQUEST_TIMEOUT_MS,
            minimum_ms=AUTO_TIMEOUT_MIN_MS,
            maximum_ms=AUTO_TIMEOUT_MAX_MS,
            round_ms=AUTO_TIMEOUT_ROUND_MS,
            idle_max_ms=300000,
            preset_timeouts=LLM_PRESET_TIMEOUT_MS,
        ),
        ProviderTimeoutPorts(
            positive_int=positive_int,
            context_policy=provider_context_policy,
            context_capacity=provider_model_context_capacity,
            output_token_cap=cap_output_tokens_to_context_ratio,
            ollama_options=ollama_extra_options,
            catalog_timeout=ollama_catalog_timeout_for_model,
            model_preset=model_preset,
            timeout_for_context=recommended_timeout_ms_for_context,
            format_context=format_context_tokens,
        ),
    )


def configured_context_window_for_timeout(provider: str, pcfg: dict[str, Any]) -> int | None:
    return provider_timeout_policy().configured_context(provider, pcfg)


def configured_output_tokens_for_timeout(provider: str, pcfg: dict[str, Any]) -> int | None:
    return provider_timeout_policy().configured_output(provider, pcfg)


def clamp_auto_timeout_ms(ms: int | float | None) -> int:
    return provider_timeout_policy().clamp(ms)


def calculated_request_timeout_ms(
    provider: str,
    pcfg: dict[str, Any],
    timeout_candidates: list[int] | None = None,
) -> int:
    return provider_timeout_policy().calculated(provider, pcfg, timeout_candidates)


def recommended_request_timeout_ms(provider: str, pcfg: dict[str, Any], use_context_fallback: bool = True) -> int:
    return provider_timeout_policy().recommended(
        provider,
        pcfg,
        use_context_fallback=use_context_fallback,
    )


def apply_recommended_timeout_for_model_context(
    provider: str,
    pcfg: dict[str, Any],
    use_context_fallback: bool = True,
) -> list[str]:
    return provider_timeout_policy().apply(
        provider,
        pcfg,
        use_context_fallback=use_context_fallback,
    )


def context_mode_values_for_capacity(capacity: int | None) -> dict[str, tuple[int, int, int]]:
    return ContextSetupService.mode_values(capacity)


def context_setup_text(key: str, lang: str | None = None) -> tuple[str, str]:
    lang = lang or load_config().get("language", "en")
    return ContextSetupService.text(key, lang)


def context_setup_service() -> ContextSetupService:
    return ContextSetupService(
        ContextSetupPorts(
            context_capacity=provider_model_context_capacity,
            context_policy=provider_context_policy,
            positive_int=positive_int,
            format_context=format_context_tokens,
            ui_text=ui_text,
            pad_cells=pad_cells,
            cap_context=cap_context_settings_to_model_capacity,
            cap_output=cap_output_settings_to_context_ratio,
            apply_timeout=apply_recommended_timeout_for_model_context,
            context_status=context_setting_status,
        )
    )


def context_setup_panel_rows(provider: str, pcfg: dict[str, Any], lang: str | None = None) -> tuple[list[str], list[str]]:
    lang = lang or load_config().get("language", "en")
    return context_setup_service().panel_rows(provider, pcfg, lang)


def apply_context_setup_to_provider(provider: str, pcfg: dict[str, Any], mode: str, lang: str | None = None) -> list[str]:
    lang = lang or load_config().get("language", "en")
    return context_setup_service().apply(provider, pcfg, mode, lang)


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
        services=llm_presets.PresetServices(
            definition=llm_presets.PresetDefinition(
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
            context_policy=llm_presets.PresetContextPolicy(
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
            provider_mutation=llm_presets.PresetProviderMutation(
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




def runtime_llm_options_controller() -> RuntimeLlmOptionsController:
    return RuntimeLlmOptionsController(
        RuntimeLlmSettings(
            option_keys=frozenset(RUNTIME_LLM_OPTION_KEYS),
            original_key=RUNTIME_LLM_ORIGINAL_KEY,
            slider_labels=LLM_SLIDER_LABELS,
        ),
        RuntimeLlmConfigPorts(
            load=load_config,
            save=save_config,
            clear_model_cache=clear_model_cache,
            deep_copy=lambda value: json.loads(json.dumps(value)),
            current_provider=get_current_provider,
            normalize_preset=normalize_llm_preset_token,
            resolve_preset=resolve_llm_preset_id,
        ),
        RuntimeLlmPresentationPorts(
            applied_preset=applied_preset_id,
            slider_presets=llm_slider_preset_ids,
            preset_text=llm_preset_text,
            provider_label=provider_mode_label,
            context_status=context_setting_status,
            timeout_status=timeout_profile_status,
            ollama_options=ollama_extra_options,
        ),
        RuntimeLlmMutationPorts(apply_preset=apply_llm_preset_to_provider),
    )


_RUNTIME_LLM_OPTIONS_API = RuntimeLlmOptionsApi(runtime_llm_options_controller)
handle_live_llm_options_action = _RUNTIME_LLM_OPTIONS_API.handle_live_llm_options_action
runtime_llm_snapshot_from_provider = _RUNTIME_LLM_OPTIONS_API.snapshot_from_provider
ensure_runtime_llm_original_snapshot = _RUNTIME_LLM_OPTIONS_API.ensure_original_snapshot
restore_runtime_llm_original_options = _RUNTIME_LLM_OPTIONS_API.restore_original_options
apply_runtime_llm_preset_config = _RUNTIME_LLM_OPTIONS_API.apply_preset_config
runtime_llm_slider_line = _RUNTIME_LLM_OPTIONS_API.slider_line
apply_runtime_llm_slider_delta_config = _RUNTIME_LLM_OPTIONS_API.apply_slider_delta_config
runtime_llm_status_lines = _RUNTIME_LLM_OPTIONS_API.status_lines
runtime_llm_preset_list_lines = _RUNTIME_LLM_OPTIONS_API.preset_list_lines




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


def provider_option_cli_controller() -> ProviderOptionCliController:
    return ProviderOptionCliController(
        ProviderOptionCliConfig(
            load=load_config,
            save=save_config,
            normalize_provider=normalize_provider,
            clear_model_cache=clear_model_cache,
            output=print,
        ),
        OllamaOptionCommands(
            apply=apply_ollama_option,
            apply_timeout=apply_recommended_timeout_for_model_context,
            context_status=ollama_num_ctx_status,
            rate_usage=router_rate_limit_usage,
            options_status=ollama_options_status,
        ),
        ProviderOptionCommands(
            apply=apply_provider_option,
            cap_context=cap_context_settings_to_model_capacity,
            cap_output=cap_output_settings_to_context_ratio,
            apply_timeout=apply_recommended_timeout_for_model_context,
            status=provider_options_status,
        ),
        supported_providers=PROVIDER_OPTION_PROVIDERS,
        ollama_providers=("ollama", "ollama-cloud"),
        provider_notes={
            "opencode": (
                "  OpenCode endpoint override: endpoint:<model-id>=messages|chat|responses|gemini",
                "  OpenCode ip_family options: auto, ipv4, ipv6, ipv4-preferred, ipv6-preferred",
            ),
            "opencode-go": (
                "  OpenCode endpoint override: endpoint:<model-id>=messages|chat|responses|gemini",
                "  OpenCode ip_family options: auto, ipv4, ipv6, ipv4-preferred, ipv6-preferred",
            ),
            "fireworks": (
                "  Fireworks model list options: account_id=fireworks, model_api_base_url=https://api.fireworks.ai",
            ),
        },
        unsupported_message=(
            "Provider options are available for anthropic, ollama, ollama-cloud, "
            "deepseek, opencode, opencode-go, kimi, z.ai, fireworks, vllm, "
            "lm-studio, nvidia-hosted, self-hosted-nim, and openrouter."
        ),
    )


def cmd_provider_options(args: argparse.Namespace) -> None:
    provider_option_cli_controller().provider_options(args)


COMPAT_TOOL_NAME = "compat_echo"
COMPATIBILITY_TEST_HEADER = "x-ciel-runtime-compatibility-test"


def compatibility_protocol_codec() -> CompatibilityProtocolCodec:
    return CompatibilityProtocolCodec(
        COMPAT_TOOL_NAME,
        CompatibilityProtocolPorts(
            max_tokens_for_model=compat_max_tokens_for_model,
            first_header=first_header,
            parse_retry_after=parse_retry_after_seconds,
            format_duration=format_duration_seconds,
        ),
    )


_COMPATIBILITY_PROTOCOL_API = CompatibilityProtocolApi(compatibility_protocol_codec)
compatibility_tool_schema = _COMPATIBILITY_PROTOCOL_API.tool_schema
compatibility_text_request = _COMPATIBILITY_PROTOCOL_API.text_request
compatibility_tool_request = _COMPATIBILITY_PROTOCOL_API.tool_request
compatibility_tool_result_request = _COMPATIBILITY_PROTOCOL_API.tool_result_request
response_content_blocks = CompatibilityProtocolCodec.content_blocks
response_content_types = _COMPATIBILITY_PROTOCOL_API.content_types
response_text_preview = _COMPATIBILITY_PROTOCOL_API.text_preview
find_compat_tool_use = _COMPATIBILITY_PROTOCOL_API.find_tool_use
summarize_compat_response = _COMPATIBILITY_PROTOCOL_API.summarize_response


def compatibility_failure_diagnosis(provider: str, code: int | None, msg: str) -> str | None:
    lower = msg.lower()
    if "does not support tools" in lower:
        return "Diagnosis: selected model does not support tool calling, so it is not suitable for normal Claude Code use."
    return PROVIDER_COMPATIBILITY.resolve(provider).failure_diagnosis(code, msg)


def known_compatibility_tool_use_blocker(provider: str, model: str) -> str:
    normalized = strip_claude_context_suffix(str(model or "")).strip()
    return PROVIDER_COMPATIBILITY.resolve(provider).tool_use_blocker(normalized)


compatibility_http_error_message = _COMPATIBILITY_PROTOCOL_API.http_error_message


def provider_config_for_single_api_key(pcfg: dict[str, Any], key: str) -> dict[str, Any]:
    return CompatibilityApiKeyProbeRunner.single_key_config(pcfg, key)


def compatibility_api_key_probe_request(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    request_body: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, str]]:
    return CompatibilityApiKeyProbeBuilder(
        CompatibilityProbeProjectionPorts(
            normalize_thinking=normalize_thinking_for_non_anthropic_provider,
            normalize_tool_choice=normalize_tool_choice_for_provider,
            resolve_model=resolve_requested_model,
            headers=provider_headers,
            request_policy=provider_request_policy,
        ),
        CompatibilityProbeRoutingPorts(
            ollama_request=ollama_chat_request,
            openai_request=openai_compatible_chat_request,
            endpoint=provider_endpoint,
            opencode_endpoint_kind=opencode_endpoint_kind,
            openai_router_enabled=provider_openai_router_enabled,
            request_base=provider_upstream_request_base,
            join_url=join_url,
            ncp_model_id=ncp_model_id_for_nvidia_hosted,
        ),
        CompatibilityProbeAnthropicPorts(
            cap_body=cap_anthropic_body_for_provider,
            apply_options=apply_provider_request_options,
            resolve_tool_models=resolve_tool_model_references,
            native_compat_enabled=provider_native_compat_enabled,
            native_base_url=native_anthropic_base_url,
        ),
    ).build(provider, pcfg, model, request_body)

def run_compatibility_api_key_probes(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    request_body: dict[str, Any],
    timeout: float,
) -> list[str]:
    return CompatibilityApiKeyProbeRunner(
        CompatibilityApiKeyProbeRunnerPorts(
            api_keys=provider_config_api_keys,
            mask_secret=mask_secret,
            build_request=compatibility_api_key_probe_request,
            post=post_json,
            http_error_message=compatibility_http_error_message,
            failure_diagnosis=compatibility_failure_diagnosis,
        )
    ).run(provider, pcfg, model, request_body, timeout)


def vllm_tool_parser_hint(model: str) -> str | None:
    return CompatibilityRuntimeProjection.vllm_tool_parser_hint(model)


def compatibility_runtime_projection() -> CompatibilityRuntimeProjection:
    return CompatibilityRuntimeProjection(
        CompatibilityRuntimePorts(
            provider_policy=PROVIDER_COMPATIBILITY.resolve,
            runtime_info=upstream_model_runtime_info,
            positive_int=positive_int,
        )
    )


def compatibility_runtime_lines(provider: str, pcfg: dict[str, Any], native: bool) -> list[str]:
    return compatibility_runtime_projection().lines(provider, pcfg, native)


def compatibility_cache_repository() -> CompatibilityCacheRepository:
    return CompatibilityCacheRepository(
        CompatibilityCachePorts(
            save_config=save_config,
            timestamp=lambda: int(time.time()),
        )
    )


def set_compatibility_cache(
    cfg: dict[str, Any],
    provider: str,
    model: str,
    ok: bool,
    code: int | None = None,
    message: str = "",
    diagnosis: str = "",
) -> None:
    compatibility_cache_repository().record(
        cfg,
        provider,
        model,
        ok,
        code,
        message,
        diagnosis,
    )


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
    return claude_limit_policy().output_token_limit(provider, pcfg)


def claude_code_auto_compact_window(provider: str, pcfg: dict[str, Any]) -> int | None:
    return claude_limit_policy().auto_compact_window(provider, pcfg)


def claude_limit_policy() -> ClaudeLimitPolicy:
    return ClaudeLimitPolicy(
        ClaudeLimitPorts(
            positive_int=positive_int,
            cap_output_tokens=cap_output_tokens_to_context_ratio,
            ollama_options=ollama_extra_options,
            context_limit=context_limit_for_status,
        )
    )


def claude_model_alias_policy() -> ClaudeModelAliasPolicy:
    return ClaudeModelAliasPolicy(
        ClaudeModelPorts(
            strip_context_suffix=strip_claude_context_suffix,
            current_upstream_model=current_upstream_model_id,
            unslug_alias=unslug_provider_alias,
            model_map=model_map_for,
            context_hint=model_context_hint_from_model_id,
            anthropic_limit_hints=anthropic_model_limit_hints,
            positive_int=positive_int,
            configured_model_ids=cached_or_configured_model_ids,
            normalize_model_id=normalize_model_id,
            alias_for=alias_for,
        )
    )


def claude_code_model_claims_one_million_context(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    *,
    include_current: bool = True,
) -> bool:
    return claude_model_alias_policy().claims_one_million_context(
        provider,
        pcfg,
        model,
        include_current=include_current,
        context_limit=context_limit_for_status(provider, pcfg) if include_current else None,
    )


def claude_code_context_model_alias(
    provider: str,
    pcfg: dict[str, Any],
    model: str,
    upstream_model: str | None = None,
) -> str:
    return claude_model_alias_policy().context_model_alias(
        provider,
        pcfg,
        model,
        upstream_model,
        context_limit=context_limit_for_status(provider, pcfg) if upstream_model is None else None,
    )


def _model_id_matches_claude_family(model_id: str, family: str) -> bool:
    return claude_model_alias_policy().matches_family(model_id, family)


def claude_code_default_model_aliases(provider: str, pcfg: dict[str, Any], current_model_alias: str) -> dict[str, str]:
    return claude_model_alias_policy().default_model_aliases(
        provider,
        pcfg,
        current_model_alias,
        context_limit=context_limit_for_status(provider, pcfg),
    )


def apply_common_claude_env(provider: str, pcfg: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    return claude_environment_projection().apply_common(provider, pcfg, env)


def claude_environment_projection() -> ClaudeEnvironmentProjection:
    return ClaudeEnvironmentProjection(
        ROUTER_BASE,
        claude_limit_policy(),
        claude_model_alias_policy(),
        ClaudeEnvironmentSourcePorts(
            load_config=load_config,
            current_provider=get_current_provider,
            direct_native=direct_native_anthropic_enabled,
            primary_api_key=provider_primary_api_key,
            meaningful_key=meaningful_key,
            current_alias=current_alias,
        ),
        ClaudeEnvironmentFeaturePorts(
            capability_string=claude_code_capability_string,
            current_upstream_model=current_upstream_model_id,
            resolve_requested_model=resolve_requested_model,
            workflows_enabled=claude_code_workflows_enabled,
            router_auth_token=claude_code_router_auth_token,
            context_limit=context_limit_for_status,
        ),
    )


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
    return claude_environment_projection().build(cfg)


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
    return claude_runtime_settings_policy().settings(provider, pcfg)


def claude_runtime_settings_policy() -> ClaudeRuntimeSettingsPolicy:
    return ClaudeRuntimeSettingsPolicy(
        ClaudeRuntimeSettingsPorts(
            ultracode_enabled=claude_code_ultracode_enabled,
            has_passthrough_option=has_passthrough_option,
            log=router_log,
        )
    )


def append_claude_code_runtime_settings_args(extra_args: list[str], passthrough: list[str], provider: str, pcfg: dict[str, Any]) -> None:
    claude_runtime_settings_policy().append_args(extra_args, passthrough, provider, pcfg)


def cmd_env(_: argparse.Namespace) -> None:
    for line in ClaudeEnvironmentShellRenderer.lines(env_vars()):
        print(line)


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
    return router_client_registry().register(pid)


def release_router_client(path: Path | None) -> None:
    router_client_registry().release(path)


def router_client_registry() -> RouterClientRegistry:
    return RouterClientRegistry(
        ROUTER_CLIENTS_DIR,
        ROUTER_PORT,
        RouterClientRegistryPorts(pid_is_running=pid_is_running, log=router_log),
    )


def router_managed_idle_exit_seconds() -> float:
    return ManagedRouterLifetime.idle_exit_seconds()


def managed_router_lifetime() -> ManagedRouterLifetime:
    return ManagedRouterLifetime(
        ManagedRouterLifetimePorts(
            active_client_pids=active_router_client_pids,
            pid_is_running=pid_is_running,
            stop_router=stop_router_with_guarantee,
            log=router_log,
        )
    )


def managed_router_stop_reason(started_at: float, owner_pid: int, idle_seconds: float) -> str | None:
    return managed_router_lifetime().stop_reason(started_at, owner_pid, idle_seconds)


def start_managed_router_lifetime_watchdog(server: ThreadingHTTPServer) -> None:
    managed_router_lifetime().start_watchdog(server)


def active_router_client_pids() -> list[int]:
    return router_client_registry().active_pids()


def stop_router_if_no_active_clients(reason: str, quiet: bool = True) -> bool:
    return managed_router_lifetime().stop_if_idle(reason, quiet)


def router_client_supervisor_interval_seconds() -> float:
    return RouterClientSupervisor.interval_seconds()


def router_client_supervisor() -> RouterClientSupervisor:
    return RouterClientSupervisor(
        ROUTER_BASE,
        RouterClientSupervisorPorts(
            router_health=router_health,
            health_matches_current=router_health_matches_current,
            health_summary=router_health_summary,
            start_router=start_router_if_needed,
            log=router_log,
        ),
    )


def ensure_managed_router_running_for_client() -> bool:
    return router_client_supervisor().ensure_running()


def start_router_client_supervisor(stop_event: threading.Event) -> threading.Thread:
    return router_client_supervisor().start(stop_event)


def file_size_or_zero(path: Path) -> int:
    return RoutedLaunchDiagnostics.file_size(path)


def _read_text_file_from_offset(path: Path, offset: int = 0, max_bytes: int = 262_144) -> str:
    return RoutedLaunchDiagnostics.read_from_offset(path, offset, max_bytes)


def routed_launch_diagnostics() -> RoutedLaunchDiagnostics:
    return RoutedLaunchDiagnostics(
        ROUTER_BASE,
        LOG_PATH,
        RoutedLaunchDiagnosticPorts(
            router_health=router_health,
            health_summary=router_health_summary,
            provider_summary=provider_upstream_summary_for_launch,
            log=router_log,
        ),
    )


def router_recent_diagnostic_lines(since_offset: int = 0, limit: int = 8) -> list[str]:
    return routed_launch_diagnostics().recent_lines(since_offset, limit)


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
    return RoutedLaunchDiagnostics.should_print(rc, recent_lines)


def print_routed_claude_exit_diagnostics(
    rc: int,
    provider: str,
    pcfg: dict[str, Any],
    *,
    log_offset: int = 0,
) -> None:
    routed_launch_diagnostics().print_exit(rc, provider, pcfg, log_offset=log_offset)


def router_lifetime_runner() -> RouterLifetimeRunner:
    return RouterLifetimeRunner(
        RouterLifetimeRunnerPorts(
            register_client=register_router_client,
            release_client=release_router_client,
            start_supervisor=start_router_client_supervisor,
            stop_if_idle=stop_router_if_no_active_clients,
            log=router_log,
        )
    )


def run_with_router_lifetime(runner: Callable[[], int], manage_router: bool) -> int:
    return router_lifetime_runner().run(runner, manage_router)


def terminate_pid(pid: int, label: str, quiet: bool = False) -> bool:
    return process_tree_controller().terminate_pid(pid, label, quiet=quiet)


def process_control_services() -> ProcessControlServices:
    return ProcessControlServices(
        query=ProcessQueryServices(),
        signals=ProcessSignalServices(kill=os.kill, pid_is_running=pid_is_running),
        log=router_log,
    )


def process_tree_controller() -> ProcessTreeController:
    return ProcessTreeController(process_control_services(), platform_name=os.name)


def descendant_pids(pid: int) -> list[int]:
    return process_tree_controller().descendant_pids(pid)


def parent_pid_and_command(pid: int) -> tuple[int, str] | None:
    return process_tree_controller().parent_pid_and_command(pid)


def ciel_runtime_client_wrapper_parent_pids(pid: int) -> list[int]:
    return process_tree_controller().client_wrapper_parent_pids(pid)


def terminate_pid_tree(pid: int, label: str, quiet: bool = False) -> bool:
    return process_tree_controller().terminate_tree(pid, label, quiet=quiet)


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
    return NvidiaProxyStopper(
        NCP_ENV,
        NvidiaProxyStopPorts(
            read_env_file=read_env_file,
            positive_int=positive_int,
            terminate_windows_port=terminate_windows_port,
            find_executable=find_executable,
            terminate_matching_processes=terminate_matching_processes,
            run=subprocess.run,
            log=router_log,
            output=print,
        ),
    ).stop(quiet, platform_name=os.name)


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

def cleanup_managed_services_for_provider(
    provider: str,
    pcfg: dict[str, Any],
    cfg: dict[str, Any],
    quiet: bool = False,
) -> None:
    ManagedServiceCleanupPolicy(
        ManagedServiceCleanupPorts(
            direct_native_anthropic=direct_native_anthropic_enabled,
            direct_native_codex=direct_native_codex_enabled,
            direct_native_agy=direct_native_agy_enabled,
            request_policy=provider_request_policy,
            native_compat_enabled=provider_native_compat_enabled,
            stop_idle_router=stop_router_if_no_active_clients,
            stop_nvidia_proxy=stop_ncp_proxy,
        )
    ).cleanup(provider, pcfg, cfg, quiet)


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


def main_menu_projection() -> MainMenuProjection:
    return MainMenuProjection(
        MainMenuProjectionPorts(
            languages=LANGUAGES,
            ui_text=ui_text,
            compact_text=compact_text,
            provider_label=provider_menu_label,
            stored_api_key_mask=stored_api_key_mask,
            llm_options_status=llm_options_status,
            log_level_status=log_level_status,
            supports_runtime=DEFAULT_RUNTIME_COMPATIBILITY.supports,
            provider_family=DEFAULT_RUNTIME_COMPATIBILITY.provider_family,
            provider_ui_policy=provider_ui_policy,
        )
    )


def main_menu_rows(cfg: dict[str, Any], provider: str, pcfg: dict[str, Any], lang: str) -> list[str]:
    return main_menu_projection().rows(cfg, provider, pcfg, lang)


def provider_panel_projection() -> ProviderPanelProjection:
    return ProviderPanelProjection(
        ProviderPanelConstants(
            labels=PROVIDER_LABELS,
            anthropic_native_choice=ANTHROPIC_NATIVE_PROVIDER_CHOICE,
            anthropic_routed_choice=ANTHROPIC_ROUTED_PROVIDER_CHOICE,
            agy_native_choice=AGY_NATIVE_PROVIDER_CHOICE,
            agy_routed_choice=AGY_ROUTED_PROVIDER_CHOICE,
            codex_native_choice=CODEX_NATIVE_PROVIDER_CHOICE,
            codex_routed_choice=CODEX_ROUTED_PROVIDER_CHOICE,
        ),
        ProviderPanelPorts(
            anthropic_routed=anthropic_routed_enabled,
            agy_routed=agy_routed_enabled,
            codex_routed=codex_routed_enabled,
            has_api_key=provider_has_api_key,
            compact_text=compact_text,
        ),
    )


def provider_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return provider_panel_projection().rows(cfg)


def configuration_panel_projection() -> ConfigurationPanelProjection:
    return ConfigurationPanelProjection(
        ConfigurationPanelPorts(
            languages=LANGUAGES,
            log_level_names=LOG_LEVEL_NAMES,
            log_level_name=log_level_name,
            log_level_status=log_level_status,
            ui_text=ui_text,
            compact_text=compact_text,
            default_base_url=default_base_url,
            api_key_count=provider_api_key_count,
            platform_name=os.name,
        )
    )


def language_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return configuration_panel_projection().language_rows(cfg)


def log_level_panel_rows(cfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return configuration_panel_projection().log_level_rows(cfg)


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
    return configuration_panel_projection().api_key_rows(provider, pcfg)


def base_url_panel_rows(provider: str, pcfg: dict[str, Any]) -> tuple[list[str], list[str]]:
    return configuration_panel_projection().base_url_rows(provider, pcfg)


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
        services=prelaunch.PrelaunchServices(
            constants=prelaunch.PrelaunchConstants(
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
            terminal=prelaunch.PrelaunchTerminal(
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
            config=prelaunch.PrelaunchConfig(
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
            launch_policy=prelaunch.PrelaunchLaunchPolicy(
                agy_launch_enabled_for_provider=agy_launch_enabled_for_provider,
                claude_launch_enabled_for_provider=claude_launch_enabled_for_provider,
                codex_launch_enabled_for_provider=codex_launch_enabled_for_provider,
                launch_blockers_require_api_key=launch_blockers_require_api_key,
                launch_readiness_errors=launch_readiness_errors,
            ),
            panel_rows=prelaunch.PrelaunchPanelRows(
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
            channel_query=prelaunch.PrelaunchChannelQuery(
                _channel_panel_first_selectable=_channel_panel_first_selectable,
                _channel_panel_step=_channel_panel_step,
                channel_delivery_panel_rows=channel_delivery_panel_rows,
                channel_panel_rows=channel_panel_rows,
                channel_panel_rows_for_menu=channel_panel_rows_for_menu,
                channel_probe_summary_message=channel_probe_summary_message,
                channel_specs=channel_specs,
                refresh_channel_probe_cache=refresh_channel_probe_cache,
            ),
            channel_commands=prelaunch.PrelaunchChannelCommands(
                add_channel_spec=add_channel_spec,
                clear_channel_specs=clear_channel_specs,
                remove_channel_spec=remove_channel_spec,
                set_channel_delivery_config=set_channel_delivery_config,
            ),
            mutations=prelaunch.PrelaunchMutations(
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
            secrets=prelaunch.PrelaunchSecrets(
                clear_api_key_config=clear_api_key_config,
                mask_secret=mask_secret,
                parse_api_key_list=parse_api_key_list,
                secret_fingerprint=secret_fingerprint,
                store_api_key_input_config=store_api_key_input_config,
                store_api_keys_config=store_api_keys_config,
            ),
            options=prelaunch.PrelaunchOptions(
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
    return start_project_router_if_needed(
        replace_active_clients=replace_active_clients,
        config=router_process_config(),
        identity=RouterStartupIdentity(version=VERSION, source_fingerprint=SOURCE_FINGERPRINT),
        state=RouterStartupStatePorts(
            health=router_health,
            active_client_pids=active_router_client_pids,
            health_matches_current=router_health_matches_current,
            health_config_matches_current=router_health_config_matches_current,
            terminate_active_clients=terminate_active_router_clients,
            ensure_port_available=ensure_router_port_available_for_spawn,
            reuse_enabled=lambda: env_bool(os.environ.get("CIEL_RUNTIME_REUSE_ROUTER"), False),
            log=router_log,
        ),
        spawn=RouterSpawnPorts(
            popen=subprocess.Popen,
            router_up=router_up,
            now=time.time,
            sleep=time.sleep,
            process_id=os.getpid,
            environment=os.environ.copy,
        ),
        executable=sys.executable,
        entrypoint=Path(__file__).resolve(),
        log_path=LOG_PATH,
        platform_name=os.name,
    )


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


def native_channel_passthrough_requested(passthrough: list[str]) -> bool:
    return channel_launch_policy().native_passthrough_requested(passthrough)


def channel_launch_policy() -> ChannelLaunchPolicy:
    return ChannelLaunchPolicy(
        native_router_names=frozenset(_NATIVE_ROUTER_CHANNEL_NAMES),
        ports=ChannelLaunchPorts(
            has_option=has_passthrough_option,
            channel_specs=channel_specs_for_launch,
            delivery_mode=channel_delivery_mode,
            run_auth_status=subprocess.run,
        ),
    )


def claude_channel_args(
    cfg: dict[str, Any],
    passthrough: list[str],
    extra_specs: list[str] | None = None,
    *,
    native_channel_bridge: bool = False,
) -> list[str]:
    return channel_launch_policy().claude_args(
        cfg,
        passthrough,
        extra_specs,
        native_channel_bridge=native_channel_bridge,
    )


def claude_channels_requested(cfg: dict[str, Any], passthrough: list[str], extra_specs: list[str] | None = None) -> bool:
    return native_channel_passthrough_requested(passthrough)


def should_use_native_channel_bridge(use_router_mode: bool, cfg: dict[str, Any], passthrough: list[str]) -> bool:
    return channel_launch_policy().native_bridge(
        use_router_mode,
        cfg,
        passthrough,
    )


def should_use_channel_llm_delivery(use_router_mode: bool, passthrough: list[str], cfg: dict[str, Any] | None = None) -> bool:
    del cfg
    return channel_launch_policy().llm_delivery(use_router_mode, passthrough)


def channel_specs_include_external_server(specs: list[str]) -> bool:
    return channel_launch_policy().specs_include_external_server(specs)


def claude_code_channels_auth_available(claude: str) -> tuple[bool, str]:
    return channel_launch_policy().claude_auth_available(claude)


def write_web_tools_mcp_config(cfg: dict[str, Any]) -> Path:
    return managed_mcp_config_service().write_web_tools(cfg)


def write_duckduckgo_mcp_config(cfg: dict[str, Any]) -> Path:
    return managed_mcp_config_service().write_duckduckgo_compat(cfg)


def write_zai_mcp_config(provider: str, pcfg: dict[str, Any]) -> Path | None:
    return managed_mcp_config_service().write_zai(provider, pcfg)


def reset_zai_mcp_config_if_inactive(provider: str) -> None:
    managed_mcp_config_service().reset_zai_if_inactive(provider)


def write_channel_mcp_config() -> Path:
    return managed_mcp_config_service().write_channel()


def managed_mcp_config_service() -> ManagedMcpConfigService:
    return ManagedMcpConfigService(
        ManagedMcpConfigPaths(
            WEB_TOOLS_MCP_CONFIG,
            DUCKDUCKGO_MCP_CONFIG,
            ZAI_MCP_CONFIG,
            CHANNEL_MCP_CONFIG,
        ),
        ManagedMcpConfigPolicy(ROUTER_BASE, tuple(ZAI_MANAGED_MCP_SERVERS)),
        ManagedMcpConfigPorts(
            find_executable,
            lambda path, data, operation: json_artifact_repository(path).save(data, operation),
            provider_primary_api_key,
            meaningful_key,
            _channel_mcp_ensure_cursor_initialized,
            router_log,
        ),
    )


def write_mcp_proxy_config(
    passthrough: list[str],
    *,
    extra_config_paths: list[Path | str] | None = None,
    force_proxy_server_names: set[str] | None = None,
    disable_proxy_notification_stream_names: set[str] | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path | None:
    return mcp_proxy_config_service().write(
        passthrough,
        extra_config_paths=extra_config_paths,
        force_proxy_server_names=force_proxy_server_names,
        disable_proxy_notification_stream_names=disable_proxy_notification_stream_names,
        cwd=cwd,
        home=home,
    )


def mcp_proxy_config_service() -> McpProxyConfigService:
    return McpProxyConfigService(
        McpProxyConfigPaths(
            MCP_PROXY_CONFIG,
            CONFIG_DIR / "mcp-proxy-servers",
            Path(__file__).resolve(),
        ),
        McpProxyConfigPorts(
            claude_mcp_config_paths,
            _read_mcp_servers_from_json,
            _mcp_server_is_streamable_http,
            _mcp_server_force_proxy,
            _mcp_server_is_stdio,
            _safe_mcp_proxy_name,
            lambda path, data, operation: json_artifact_repository(path).save(data, operation),
            router_log,
        ),
    )


def should_use_channel_stdin_proxy(use_router_mode: bool, passthrough: list[str], cfg: dict[str, Any] | None = None) -> bool:
    return channel_launch_policy().stdin_proxy(use_router_mode, passthrough, cfg)


def should_launch_process_start_channel_sse(
    stdin_channel_proxy: bool,
    native_channel_bridge: bool,
    llm_channel_delivery: bool,
) -> bool:
    return ChannelLaunchPolicy.process_starts_sse(
        stdin_channel_proxy,
        native_channel_bridge,
        llm_channel_delivery,
    )


def _channel_pending_scan_limit() -> int:
    return channel_runtime_environment_policy().pending_scan_limit()


def _channel_stdin_wake_batch_limit() -> int:
    return channel_runtime_environment_policy().wake_batch_limit()


_CHANNEL_LLM_TOOL_CONTEXT_LOCK = threading.Lock()
_CHANNEL_LLM_TOOL_CONTEXT: dict[str, dict[str, Any]] = {}
_CHANNEL_LLM_TOOL_CONTEXT_LIMIT = 200
_CHANNEL_LLM_TOOL_CONTEXT_MAX_INJECT = 8
_CHANNEL_LLM_TOOL_CONTEXT_PROMPT_LIMIT = 4000


def channel_tool_context_service() -> ChannelToolContextService:
    return ChannelToolContextService(
        repository=ChannelToolContextRepository(
            contexts=_CHANNEL_LLM_TOOL_CONTEXT,
            lock=_CHANNEL_LLM_TOOL_CONTEXT_LOCK,
            limit=_CHANNEL_LLM_TOOL_CONTEXT_LIMIT,
        ),
        policy=ChannelToolContextPolicy(
            max_inject=_CHANNEL_LLM_TOOL_CONTEXT_MAX_INJECT,
            prompt_limit=_CHANNEL_LLM_TOOL_CONTEXT_PROMPT_LIMIT,
        ),
        ports=ChannelToolContextPorts(
            content_to_text=anthropic_content_to_text,
            truncate=truncate_for_prompt,
            now=time.time,
            log=router_log,
        ),
    )


def _channel_injected_prompt_text(body: dict[str, Any]) -> str:
    return channel_tool_context_service().prompt_text(body)


def _remember_channel_injected_tool_use(source_body: dict[str, Any] | None, tool_use_id: str, tool_name: str, tool_input: Any) -> None:
    channel_tool_context_service().remember(source_body, tool_use_id, tool_name, tool_input)


def remember_channel_injected_tool_uses(source_body: dict[str, Any] | None, message: dict[str, Any]) -> None:
    channel_tool_context_service().remember_message(source_body, message)


def _take_channel_tool_result_contexts_for_body(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return channel_tool_context_service().repository.take_for_body(
        body,
        _CHANNEL_LLM_TOOL_CONTEXT_MAX_INJECT,
    )


def body_with_channel_tool_result_context(body: dict[str, Any]) -> dict[str, Any]:
    return channel_tool_context_service().inject_followup(body)


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


def _cache_channel_llm_cursor(last_id: int) -> None:
    global _CHANNEL_LLM_CURSOR_LAST_ID
    _CHANNEL_LLM_CURSOR_LAST_ID = last_id


def _cache_channel_mcp_cursor(last_id: int) -> None:
    global _CHANNEL_MCP_CURSOR_LAST_ID
    _CHANNEL_MCP_CURSOR_LAST_ID = last_id


def channel_backlog_service() -> ChannelBacklogService:
    return ChannelBacklogService(
        ChannelBacklogCursors(
            _chat_scan_max_id,
            _CHANNEL_LLM_CURSOR_LOCK,
            _channel_llm_read_cursor_locked,
            _channel_llm_write_cursor_locked,
            _cache_channel_llm_cursor,
            _channel_llm_clear_floor_write,
            _CHANNEL_MCP_CURSOR_LOCK,
            _channel_mcp_read_cursor_locked,
            _channel_mcp_write_cursor_locked,
            _cache_channel_mcp_cursor,
        ),
        ChannelBacklogRuntime(
            _CHANNEL_STDIN_RECOVERY_CACHE,
            _CHANNEL_MCP_LOCK,
            _CHANNEL_MCP_SESSIONS,
            _CHAT_CONDITION,
            router_log,
        ),
    )


def clear_channel_backlog() -> dict[str, Any]:
    return channel_backlog_service().clear()


def channel_backlog_status() -> dict[str, Any]:
    return channel_backlog_service().status()


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
    ChannelDeliveryCursorCommitter(
        ChannelDeliveryCursorPorts(
            response_status=_handler_response_status,
            metadata_enabled=_channel_delivery_metadata,
            delivery_confirmed=pending_channel_delivery_confirmed,
            commit_if_newer=_commit_channel_llm_cursor_if_newer,
            log=router_log,
        )
    ).commit(body, handler, metadata)


def _channel_stdin_wake_claim_ttl_seconds() -> float:
    return channel_runtime_environment_policy().wake_claim_ttl_seconds()


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
        channel_llm_context.ChannelLlmContextServices(
            policy=channel_llm_context.ChannelLlmContextPolicy(
                wake_request=channel_llm_wake_request,
                plan_mode_active=plan_mode_active,
                delivery_mode=lambda: channel_delivery_mode(load_config()),
                ids_in_request=_channel_message_ids_already_in_request,
                scan_limit=_channel_pending_scan_limit,
                skip_reason=_channel_llm_message_skip_reason,
                stdin_skip_reason=_channel_llm_stdin_skip_reason,
            ),
            repository=channel_llm_context.ChannelLlmContextRepository(
                lock=lambda: _CHANNEL_LLM_CURSOR_LOCK,
                read_cursor=_channel_llm_read_cursor_locked,
                commit_cursor=_channel_llm_commit_cursor_locked,
                read_messages=lambda last_id, limit: read_chat_messages(last_id, None, None, limit),
                superseded_ids=_channel_superseded_message_ids,
            ),
            projection=channel_llm_context.ChannelLlmContextProjection(
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
    return channel_runtime_environment_policy().codex_submit_retries()


def _codex_channel_wake_submit_delay_seconds() -> float:
    return channel_runtime_environment_policy().codex_submit_delay_seconds()


def _windows_channel_startup_grace_seconds() -> float:
    """Allow an interactive Windows TUI to begin reading console input."""
    return channel_runtime_environment_policy().windows_startup_grace_seconds()


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
    injector = channel_injection.ChannelPromptInjector(
        sleep=time.sleep,
        retry_delay_seconds=_channel_wake_submit_retry_delay_seconds,
        snapshot=_channel_current_tmux_pane_text,
        log=router_log,
    )
    injector.inject(
        channel_injection.CallableInputTransport(master_fd, _write_fd_all),
        channel_injection.PromptInjection(
            prompt=prompt,
            policy=channel_injection.RuntimeInjectionPolicy(
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


def channel_transcript_repository() -> ChannelTranscriptRepository:
    return ChannelTranscriptRepository(
        HOME,
        _CHANNEL_TRANSCRIPT_CACHE,
        _CHANNEL_TRANSCRIPT_SCOPE,
        time.time,
    )


def _set_channel_transcript_scope(runtime: str, *, started_at: float | None = None, codex_home: Path | None = None) -> None:
    channel_transcript_repository().set_scope(
        runtime,
        started_at=started_at,
        codex_home=codex_home,
    )


def _channel_transcript_roots() -> tuple[tuple[Path, str], ...]:
    return channel_transcript_repository().roots()


def _latest_claude_transcript_path(ttl_seconds: float = 2.0) -> Path | None:
    return channel_transcript_repository().latest(ttl_seconds)


_read_file_tail_text = ChannelTranscriptRepository.read_tail_text


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
    return channel_cursor_recovery_service().recover(last_id)


def channel_cursor_recovery_service() -> ChannelCursorRecoveryService:
    return ChannelCursorRecoveryService(
        cache=_CHANNEL_STDIN_RECOVERY_CACHE,
        policy=ChannelCursorRecoveryPolicy(),
        ports=ChannelCursorRecoveryPorts(
            latest_transcript=_latest_claude_transcript_path,
            read_tail=_read_file_tail_text,
            queued_command_ids=_channel_stdin_queued_command_ids_from_text,
            wake_state=_channel_stdin_wake_state_from_text,
            clamp_to_clear_floor=_channel_llm_clamp_to_clear_floor,
            now=time.time,
            log=router_log,
        ),
    )


def _channel_stdin_unseen_retry_seconds() -> float:
    return channel_runtime_environment_policy().unseen_retry_seconds()


def _channel_stdin_inflight_stale_seconds() -> float:
    return channel_runtime_environment_policy().inflight_stale_seconds()


def _channel_stdin_inflight_is_stale(state: str, started_at: float, now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    return ChannelRuntimeEnvironmentPolicy.inflight_is_stale(
        state,
        started_at,
        current,
        _channel_stdin_inflight_stale_seconds(),
    )


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
    service = ChannelCompactInjectionService(
        request=ChannelCompactRequestPorts(
            read=_read_channel_compact_request,
            clear=_clear_channel_compact_request,
        ),
        runtime=ChannelCompactRuntimePorts(
            active_tool_call=_channel_stdin_active_tool_call,
            active_turn=_channel_stdin_active_turn,
            enter_bytes=_channel_wake_enter_bytes,
            write_prompt=_write_channel_wake_prompt,
            enter_label=_channel_enter_label,
        ),
        log=router_log,
    )
    return service.inject(
        master_fd,
        enter_bytes,
        log_defer=log_defer,
        submit_retry_count=submit_retry_count,
        confirm_submit=confirm_submit,
        bracketed_paste=bracketed_paste,
        submit_delay_seconds=submit_delay_seconds,
    )


def _chat_messages_file_marker() -> tuple[float, int]:
    try:
        stat = CHAT_MESSAGES_PATH.stat()
        return (stat.st_mtime, stat.st_size)
    except Exception:
        return (0.0, 0)


def terminal_input_mode_reset_policy() -> terminal_platform_io.TerminalInputModeResetPolicy:
    return terminal_platform_io.TerminalInputModeResetPolicy(
        platform_name=os.name,
        environment=os.environ,
        parse_bool=parse_bool,
        default_stream=lambda: sys.stdout,
    )


def _terminal_input_mode_reset_enabled() -> bool:
    return terminal_input_mode_reset_policy().enabled()


def _terminal_input_mode_reset_interval_seconds(default: float = 2.0) -> float:
    return terminal_input_mode_reset_policy().interval_seconds(default)


def _write_terminal_input_mode_reset(stream: Any | None = None) -> None:
    terminal_input_mode_reset_policy().write(stream)


def _strip_terminal_mouse_input_reports(data: bytes) -> bytes:
    filt = _TerminalMouseInputFilter()
    return filt.feed(data) + filt.flush()


def _windows_console_input_handle() -> Any:
    return _resolve_windows_console_input_handle()


def windows_console_mode_service() -> windows_console_mode.WindowsConsoleModeService:
    return windows_console_mode.WindowsConsoleModeService(
        windows_console_mode.WindowsConsoleModePorts(
            input_handle=_windows_console_input_handle,
            parse_bool=parse_bool,
            environment=os.environ,
        )
    )


def _windows_console_input_supported() -> bool:
    return windows_console_mode_service().input_supported()


def _windows_console_mouse_input_filter_enabled() -> bool:
    return windows_console_mode_service().mouse_filter_enabled()


def _windows_console_input_mode() -> int | None:
    return windows_console_mode_service().current()


def _set_windows_console_input_mode(mode: int) -> bool:
    return windows_console_mode_service().set(mode)


class _WindowsConsoleMouseInputGuard(
    windows_console_mode.WindowsConsoleMouseInputGuard
):
    def __init__(self) -> None:
        super().__init__(
            platform_name=os.name,
            filter_enabled=_windows_console_mouse_input_filter_enabled,
            current_mode=_windows_console_input_mode,
            set_mode=_set_windows_console_input_mode,
            log=router_log,
        )


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


def channel_terminal_dispatch_service() -> ChannelTerminalDispatchService:
    return ChannelTerminalDispatchService(
        settings=ChannelTerminalDispatchSettings(
            platform_name=os.name,
            stdin_isatty=sys.stdin.isatty,
            stdout_isatty=sys.stdout.isatty,
        ),
        proxy=ChannelTerminalProxyPorts(
            windows_supported=_windows_console_input_supported,
            run_windows=subprocess_call_with_windows_console_wake_proxy,
            run_posix=run_posix_channel_terminal_proxy,
            posix_services=build_channel_terminal_services,
        ),
        direct=ChannelDirectProcessPorts(
            call=subprocess.call,
            popen=subprocess.Popen,
            write_record=_write_codex_child_process_record,
            terminate=_terminate_recorded_child_process,
            release_record=_release_codex_child_process_record,
        ),
        log=router_log,
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
    return channel_terminal_dispatch_service().dispatch(
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


def subprocess_call_with_child_pid_record(cmd: list[str], env: dict[str, str], pid_path: Path | None = None) -> int:
    return channel_terminal_dispatch_service().call_direct(cmd, env, pid_path)


_MCP_NOTIFICATION_DEDUP_LOCK = threading.Lock()
_MCP_NOTIFICATION_DEDUP_RECENT: dict[str, tuple[str, float]] = {}
_MCP_PROXY_NOTIFICATION_SERVICE = mcp_proxy_notifications.McpProxyNotificationService(
    projection=mcp_proxy_notifications.McpNotificationProjectionPorts(
        json_safe_metadata=_json_safe_metadata,
        event_meta=_event_meta_from_sources,
        event_text=_event_payload_text,
        pretty_json=_pretty_json_value,
        semantic_text=_notification_semantic_text_from_envelope,
    ),
    effects=mcp_proxy_notifications.McpNotificationEffects(
        append_chat_message=lambda payload: append_chat_message(payload),
        log=lambda level, message: router_log(level, message),
    ),
    dedupe=mcp_proxy_notifications.McpNotificationDedupeState(
        lock=_MCP_NOTIFICATION_DEDUP_LOCK,
        recent=_MCP_NOTIFICATION_DEDUP_RECENT,
        ttl_seconds=_MCP_NOTIFICATION_DEDUP_TTL_SECONDS,
        native_method=_NATIVE_CHANNEL_NOTIFICATION_METHOD,
    ),
)
_mcp_proxy_notification_payload = _MCP_PROXY_NOTIFICATION_SERVICE.notification_payload


_mcp_proxy_stable_event_identity = _MCP_PROXY_NOTIFICATION_SERVICE.stable_event_identity


_mcp_proxy_notification_dedupe_key = _MCP_PROXY_NOTIFICATION_SERVICE.dedupe_key


_mcp_proxy_should_skip_duplicate_notification = (
    _MCP_PROXY_NOTIFICATION_SERVICE.should_skip_duplicate
)


_mcp_proxy_observe_json_message = _MCP_PROXY_NOTIFICATION_SERVICE.observe_json_message


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
    service = McpStdioProxyService(
        config=McpStdioConfigPorts(
            read=lambda path: json.loads(path.read_text(encoding="utf-8")),
            is_stdio=_mcp_server_is_stdio,
            resolve_process=resolve_mcp_server_process,
            environment=os.environ.copy,
        ),
        transport=McpStdioTransportPorts(
            popen=subprocess.Popen,
            stdio_mode=_mcp_proxy_stdio_mode,
            forward_stdin=_mcp_proxy_forward_stdin,
            forward_stdin_jsonl=_mcp_proxy_forward_stdin_jsonl,
            forward_stdout_jsonl=_mcp_proxy_forward_stdout_jsonl,
            forward_stderr=_mcp_proxy_forward_stderr,
            observer=_McpStdoutObserver,
        ),
        effects=McpStdioEffects(
            log=router_log,
            error=lambda message: print(message, file=sys.stderr, flush=True),
            start_thread=lambda target, args, name: threading.Thread(
                target=target, args=args, daemon=True, name=name
            ).start(),
            write_stdout=sys.stdout.buffer.write,
            flush_stdout=sys.stdout.buffer.flush,
        ),
    )
    return service.run(server_name, server_config_path)


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


def forced_yes_upgrade_env() -> dict[str, str]:
    return forced_upgrade_environment(os.environ)


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
    return npm_package_lifecycle().install_if_missing(
        executable_name=executable_name,
        label=label,
        package_spec=package_spec,
        skip_env=skip_env,
    )


def npm_package_lifecycle() -> NpmPackageLifecycle:
    return NpmPackageLifecycle(
        NpmPackageLifecyclePorts(
            find_executable, current_npm_install_prefix, npm_install_runtime_command,
            run_command_for_upgrade, add_npm_prefix_bin_to_path,
            npm_latest_package_version, version_newer, print,
        )
    )


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
    return npm_package_lifecycle().update_check(
        executable,
        executable_name=executable_name,
        label=label,
        package_spec=package_spec,
        skip_env=skip_env,
        current_version=current_version,
        enabled=enabled,
    )


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


def current_npm_install_prefix() -> Path | None:
    root = current_npm_package_root()
    return npm_prefix_from_package_root(root) if root else None


def running_from_npm_package() -> bool:
    return detect_running_from_npm_package(Path(__file__), os.environ)


def current_npm_package_root() -> Path | None:
    return package_root_from_installed_path(Path(__file__))


def install_diagnostics_service() -> InstallDiagnosticsService:
    return InstallDiagnosticsService(
        settings=InstallDiagnosticsSettings(HOME, os.environ, os.name == "nt"),
        ports=InstallDiagnosticsPorts(
            extra_dirs=executable_extra_dirs,
            package_root=package_root_from_installed_path,
            current_root=current_npm_package_root,
            parse_version=parse_version_tuple,
            diagnostics=ciel_runtime_install_diagnostics,
            stdin_isatty=sys.stdin.isatty,
            stdout_isatty=sys.stdout.isatty,
            write_error=lambda line: print(line, file=sys.stderr, flush=True),
        ),
    )


def ciel_runtime_launcher_candidate_dirs() -> list[Path]:
    return install_diagnostics_service().candidate_dirs()


def ciel_runtime_launcher_candidates() -> list[Path]:
    return install_diagnostics_service().candidates()


def ciel_runtime_launcher_version(path: Path, timeout: float = 5.0) -> str:
    return install_diagnostics_service().launcher_version(path, timeout)


def ciel_runtime_install_diagnostics() -> list[dict[str, str]]:
    return install_diagnostics_service().diagnostics()


def warn_if_multiple_ciel_runtime_installs() -> None:
    install_diagnostics_service().warn_if_multiple()


def ciel_runtime_restart_user_args() -> list[str]:
    return runtime_restart_service().user_args()


def runtime_restart_service() -> RuntimeRestartService:
    return RuntimeRestartService(
        settings=RuntimeRestartSettings(sys.argv, sys.executable, os.environ),
        ports=RuntimeRestartPorts(
            current_package_root=current_npm_package_root,
            global_package_root=npm_global_package_root,
            find_executable=find_executable,
            execv=os.execv,
            call=subprocess.call,
        ),
    )


def restart_ciel_runtime_after_update(npm: str, package_root: Path | None = None) -> None:
    runtime_restart_service().restart(npm, package_root)


def run_ciel_runtime_update_check(enabled: bool = True) -> bool:
    return self_update_lifecycle().run(enabled)


def self_update_lifecycle() -> SelfUpdateLifecycle:
    return SelfUpdateLifecycle(
        VERSION,
        SelfUpdatePorts(
            running_from_npm_package, find_executable, npm_latest_package_version,
            version_newer, current_npm_package_root, npm_prefix_from_package_root,
            npm_global_install_command, forced_yes_upgrade_env,
            restart_ciel_runtime_after_update, print,
        ),
    )


def run_command_for_upgrade(cmd: list[str], timeout: float = 300.0) -> tuple[int, str]:
    return run_upgrade_command(cmd, forced_yes_upgrade_env(), timeout)


def runtime_upgrade_service() -> RuntimeUpgradeService:
    return RuntimeUpgradeService(
        settings=RuntimeUpgradeSettings(VERSION, os.environ),
        npm=RuntimeUpgradeNpmPorts(
            find_executable=find_executable,
            latest_version=npm_latest_package_version,
            version_newer=version_newer,
            current_package_root=current_npm_package_root,
            package_prefix=npm_prefix_from_package_root,
            current_prefix=current_npm_install_prefix,
            global_install_command=npm_global_install_command,
            runtime_install_command=npm_install_runtime_command,
            run_command=run_command_for_upgrade,
        ),
        tools=RuntimeUpgradeToolPorts(
            claude_version=claude_code_current_version,
            codex_version=codex_current_version,
            install_claude=install_claude_code_if_missing,
            install_codex=install_codex_if_missing,
            install_agy=install_agy_if_missing,
            update_agy=run_agy_update_check,
        ),
        output=lambda message: print(message, flush=True),
    )


def quiet_upgrade_ciel_runtime() -> int:
    return runtime_upgrade_service().ciel_runtime()


def quiet_upgrade_claude_code() -> int:
    return runtime_upgrade_service().claude()


def quiet_upgrade_codex() -> int:
    return runtime_upgrade_service().codex()


def quiet_upgrade_agy() -> int:
    return runtime_upgrade_service().agy()


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


def agy_installer() -> AgyInstaller:
    return AgyInstaller(
        AGY_MANIFEST_BASE_URL,
        AgyInstallerPorts(
            agy_user_bin_dir,
            forced_yes_upgrade_env,
            find_executable,
            version_newer,
            run_command_for_upgrade,
            print,
        ),
    )


def agy_manifest_name() -> str:
    return agy_installer().manifest_name()


def agy_manifest_url() -> str:
    return agy_installer().manifest_url()


def agy_download_file(url: str, target: Path, timeout: float = 120.0) -> None:
    AgyInstaller.download_file(url, target, timeout)


def agy_latest_manifest(timeout: float = 15.0) -> dict[str, Any] | None:
    return agy_installer().latest_manifest(timeout)


def agy_current_version(agy: str) -> str:
    return AgyInstaller.current_version(agy)


def verify_sha512(path: Path, expected: str) -> bool:
    return AgyInstaller.verify_sha512(path, expected)


def install_agy_from_manifest(manifest: dict[str, Any]) -> str | None:
    return agy_installer().install_from_manifest(manifest)


def install_agy_if_missing() -> str | None:
    return agy_installer().install_if_missing()


def run_agy_update_check(agy: str, enabled: bool = True) -> str:
    return agy_installer().update_check(agy, enabled)


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
    return runtime_launch.run_claude(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        web_search_override=web_search_override, update_check=update_check,
        self_update_check=self_update_check,
        services=runtime_launch.ClaudeLaunchServices(
            constants=runtime_launch.ClaudeLaunchConstants(
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
            process=runtime_launch.ClaudeLaunchProcess(
                _log_claude_command_for_diagnostics=_log_claude_command_for_diagnostics,
                _subprocess_call_capturing_stderr=_subprocess_call_capturing_stderr,
                env_bool=env_bool,
                env_vars=env_vars,
                file_size_or_zero=file_size_or_zero,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                print_routed_claude_exit_diagnostics=print_routed_claude_exit_diagnostics,
                subprocess_call_with_channel_wake_proxy=subprocess_call_with_channel_wake_proxy,
            ),
            installation=runtime_launch.ClaudeLaunchInstallation(
                find_executable=find_executable,
                install_ciel_runtime_slash_commands=install_ciel_runtime_slash_commands,
                install_ciel_runtime_statusline=install_ciel_runtime_statusline,
                install_claude_code_if_missing=install_claude_code_if_missing,
                install_tool_guard_hooks=install_tool_guard_hooks,
                disable_ciel_runtime_slash_commands_for_native=disable_ciel_runtime_slash_commands_for_native,
                launch_readiness_errors=launch_readiness_errors,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=runtime_launch.ClaudeLaunchDispatch(
                launch_agy=launch_agy,
                launch_codex=launch_codex,
                launch_codex_app_server=launch_codex_app_server,
                materialize_runtime_command=materialize_runtime_command,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_claude_update_check=run_claude_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
                claude_launch_enabled_for_provider=claude_launch_enabled_for_provider,
            ),
            config=runtime_launch.ClaudeLaunchConfig(
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
            routing=runtime_launch.ClaudeLaunchRouting(
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
            policy=runtime_launch.ClaudeLaunchPolicy(
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
            channel_discovery=runtime_launch.ClaudeLaunchChannelDiscovery(
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
            channel_delivery=runtime_launch.ClaudeLaunchChannelDelivery(
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
            mcp_config=runtime_launch.ClaudeLaunchMcpConfig(
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


_CODEX_MCP_INTEGRATION = codex_mcp_integration.CodexMcpIntegrationService(
    config=codex_mcp_integration.CodexMcpConfigPorts(
        discover=lambda *args, **kwargs: project_discover_codex_mcp_servers(
            *args, **kwargs
        ),
        log=lambda level, message: router_log(level, message),
    ),
    artifact=codex_mcp_integration.CodexMcpArtifactPorts(
        config_path=lambda: CODEX_MCP_CONFIG,
        save_json=lambda path, payload, label: json_artifact_repository(path).save(
            payload, label
        ),
        unlink=lambda path: path.unlink(),
        load_json=lambda path: json.loads(path.read_text(encoding="utf-8")),
    ),
    capability=codex_mcp_integration.CodexMcpCapabilityPorts(
        ensure_probe_cache=lambda *args, **kwargs: ensure_channel_probe_cache_for_launch(
            *args, **kwargs
        ),
        read_servers=lambda path, cwd: _read_mcp_sse_servers_from_json(path, cwd),
        cached_probe_servers=lambda: cached_channel_probe_servers(),
        path_key=lambda path: _path_for_compare(path),
        cwd=Path.cwd,
    ),
    projection=codex_mcp_integration.CodexMcpProjectionPorts(
        dedupe_strings=_dedupe_strings,
        public_name=lambda name: _channel_sse_public_mcp_name(name),
        is_streamable_http=lambda server: _mcp_server_is_streamable_http(server),
        split_proxy_url=lambda name: codex_mcp_split_proxy_url(name),
        toml_string=toml_string,
    ),
    native_channel_names=frozenset(_NATIVE_ROUTER_CHANNEL_NAMES),
)
discovered_codex_mcp_servers = _CODEX_MCP_INTEGRATION.discovered_servers
write_codex_mcp_config_for_channel_discovery = (
    _CODEX_MCP_INTEGRATION.write_discovery_config
)
_codex_config_bare_key = _CODEX_MCP_INTEGRATION.config_bare_key
codex_channel_capable_mcp_server_names = (
    _CODEX_MCP_INTEGRATION.channel_capable_server_names
)
codex_streamable_http_mcp_servers = _CODEX_MCP_INTEGRATION.streamable_http_servers
codex_mcp_native_http_compat_args = _CODEX_MCP_INTEGRATION.native_http_compat_args


_CODEX_LAUNCH_CONFIGURATION = codex_launch_configuration.CodexLaunchConfigurationService(
    constants=codex_launch_configuration.CodexLaunchConfigurationConstants(
        runtime_provider_id=CODEX_RUNTIME_PROVIDER_ID,
        runtime_api_key_env=CODEX_RUNTIME_API_KEY_ENV,
        native_provider_id_env=CODEX_NATIVE_PROVIDER_ID_ENV,
        routed_provider_id=CODEX_ROUTED_PROVIDER_ID,
        alternate_screen_key=CODEX_TUI_ALTERNATE_SCREEN_KEY,
    ),
    policy=codex_launch_configuration.CodexLaunchPolicyPorts(
        has_option=has_passthrough_option,
        config_override_keys=_codex_config_override_keys,
        config_paths=codex_config_paths_for_launch,
        alternate_screen_value=codex_alternate_screen_value_from_config_text,
        toml_string=toml_string,
    ),
    model=codex_launch_configuration.CodexLaunchModelPorts(
        current_provider=lambda cfg: get_current_provider(cfg),
        native_enabled=lambda provider: native_codex_enabled(provider),
        current_alias=lambda cfg: current_alias(cfg),
        context_limit=lambda provider, pcfg: context_limit_for_status(provider, pcfg),
        context_capacity=lambda provider, pcfg: provider_model_context_capacity(
            provider, pcfg
        ),
    ),
    catalog=codex_launch_configuration.CodexLaunchCatalogPorts(
        write=lambda codex, spec, env: CodexModelCatalogService(
            CONFIG_DIR, subprocess.run, router_log
        ).write(codex, spec, env),
        provider_label=lambda provider: PROVIDER_LABELS.get(provider, provider),
        path_value=lambda env: path_with_ciel_runtime_user_dirs(env),
        current_model_args=project_codex_current_model_args,
        native_routed_args=project_codex_native_routed_config_args,
    ),
    effects=codex_launch_configuration.CodexLaunchConfigurationEffects(
        environ=lambda: os.environ,
        router_base=lambda: ROUTER_BASE,
        read_text=lambda path: path.read_text(encoding="utf-8"),
        log=lambda level, message: router_log(level, message),
        output=lambda message: print(message, flush=True),
    ),
)
codex_alternate_screen_compat_args = (
    _CODEX_LAUNCH_CONFIGURATION.alternate_screen_compat_args
)
codex_runtime_config_args = _CODEX_LAUNCH_CONFIGURATION.runtime_config_args
write_codex_runtime_model_catalog = (
    _CODEX_LAUNCH_CONFIGURATION.write_runtime_model_catalog
)
codex_runtime_model_catalog_args = (
    _CODEX_LAUNCH_CONFIGURATION.runtime_model_catalog_args
)
codex_native_routed_config_args = (
    _CODEX_LAUNCH_CONFIGURATION.native_routed_config_args
)
codex_passthrough_has_model_override = (
    _CODEX_LAUNCH_CONFIGURATION.passthrough_has_model_override
)
codex_current_model_cli_args = _CODEX_LAUNCH_CONFIGURATION.current_model_cli_args
codex_current_model_config_args = (
    _CODEX_LAUNCH_CONFIGURATION.current_model_config_args
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


_CODEX_SESSION_SELECTION = CodexSessionSelectionService(
    repository=CodexSessionRepositoryPorts(
        sqlite_home=lambda *args, **kwargs: codex_sqlite_home(*args, **kwargs),
        resumable=lambda database, limit, include_non_interactive: CodexSessionRepository(
            database, router_log
        ).resumable(limit, include_non_interactive=include_non_interactive),
    ),
    presentation=CodexSessionPresentationPorts(
        select=lambda *args, **kwargs: portable_select(*args, **kwargs),
        compact_text=compact_text,
        output=lambda message: print(message, flush=True),
    ),
)
codex_sqlite_home_for_launch = _CODEX_SESSION_SELECTION.sqlite_home_for_launch
codex_local_resume_sessions = _CODEX_SESSION_SELECTION.local_resume_sessions
codex_resume_session_row = _CODEX_SESSION_SELECTION.resume_session_row
select_codex_resume_session = _CODEX_SESSION_SELECTION.select_resume_session


def launch_codex(
    passthrough: list[str],
    skip_menu: bool = False,
    force_menu: bool = False,
    update_check: bool = True,
    self_update_check: bool = True,
) -> int:
    return runtime_launch.run_codex(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=runtime_launch.CodexLaunchServices(
            constants=runtime_launch.CodexLaunchConstants(
                CODEX_RUNTIME_API_KEY_ENV=CODEX_RUNTIME_API_KEY_ENV,
                CONFIG_DIR=CONFIG_DIR,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=runtime_launch.CodexLaunchProcess(
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
            cli_policy=runtime_launch.CodexLaunchCliPolicy(
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
            config=runtime_launch.CodexLaunchConfig(
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
            installation=runtime_launch.CodexLaunchInstallation(
                disable_ciel_runtime_codex_prompts_for_native=disable_ciel_runtime_codex_prompts_for_native,
                find_executable=find_executable,
                has_passthrough_option=has_passthrough_option,
                install_ciel_runtime_codex_prompts=install_ciel_runtime_codex_prompts,
                install_codex_if_missing=install_codex_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=runtime_launch.CodexLaunchDispatch(
                launch_agy=launch_agy,
                launch_claude=launch_claude,
                launch_codex_app_server=launch_codex_app_server,
                log_codex_passthrough_mapping=log_codex_passthrough_mapping,
                materialize_runtime_command=materialize_runtime_command,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_codex_update_check=run_codex_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=runtime_launch.CodexLaunchRouting(
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                codex_routed_enabled=codex_routed_enabled,
                direct_native_codex_enabled=direct_native_codex_enabled,
                launch_readiness_errors=launch_readiness_errors,
                native_codex_enabled=native_codex_enabled,
                run_with_router_lifetime=run_with_router_lifetime,
                start_router_if_needed=start_router_if_needed,
            ),
            channel=runtime_launch.CodexLaunchChannel(
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
    return runtime_launch.run_codex_app_server(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=runtime_launch.CodexAppServerLaunchServices(
            constants=runtime_launch.CodexLaunchConstants(
                CODEX_RUNTIME_API_KEY_ENV=CODEX_RUNTIME_API_KEY_ENV,
                CONFIG_DIR=CONFIG_DIR,
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=runtime_launch.CodexAppServerProcess(
                _log_codex_app_server_command_for_diagnostics=_log_codex_app_server_command_for_diagnostics,
                codex_process_record_path=codex_process_record_path,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                subprocess_call_with_child_pid_record=subprocess_call_with_child_pid_record,
                terminate_existing_codex_processes_for_launch=terminate_existing_codex_processes_for_launch,
                terminate_existing_router_clients_for_launch=terminate_existing_router_clients_for_launch,
            ),
            config=runtime_launch.CodexAppServerConfig(
                apply_launch_endpoint_policy=apply_launch_endpoint_policy,
                current_alias=current_alias,
                current_launch_cwd_key=current_launch_cwd_key,
                ensure_model_cache_for_launch=ensure_model_cache_for_launch,
                get_current_provider=get_current_provider,
                load_config=load_config,
                provider_mode_label=provider_mode_label,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
            ),
            cli_policy=runtime_launch.CodexAppServerCliPolicy(
                codex_app_server_default_listen_url=codex_app_server_default_listen_url,
                codex_app_server_launch_args=codex_app_server_launch_args,
                codex_current_model_config_args=codex_current_model_config_args,
                codex_native_routed_config_args=codex_native_routed_config_args,
                codex_passthrough_has_model_override=codex_passthrough_has_model_override,
                codex_runtime_config_args=codex_runtime_config_args,
                toml_string=toml_string,
            ),
            installation=runtime_launch.CodexAppServerInstallation(
                find_executable=find_executable,
                install_codex_if_missing=install_codex_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=runtime_launch.CodexAppServerDispatch(
                launch_agy=launch_agy,
                launch_claude=launch_claude,
                launch_codex=launch_codex,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_codex_update_check=run_codex_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=runtime_launch.CodexAppServerRouting(
                cleanup_managed_services_for_provider=cleanup_managed_services_for_provider,
                codex_launch_enabled_for_provider=codex_launch_enabled_for_provider,
                codex_routed_enabled=codex_routed_enabled,
                direct_native_codex_enabled=direct_native_codex_enabled,
                launch_readiness_errors=launch_readiness_errors,
                native_codex_enabled=native_codex_enabled,
                run_with_router_lifetime=run_with_router_lifetime,
                start_router_if_needed=start_router_if_needed,
            ),
            channel=runtime_launch.CodexAppServerChannel(
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
    return runtime_launch.run_agy(
        passthrough, skip_menu=skip_menu, force_menu=force_menu,
        update_check=update_check, self_update_check=self_update_check,
        services=runtime_launch.AgyLaunchServices(
            constants=runtime_launch.AgyLaunchConstants(
                PRELAUNCH_CANCEL=PRELAUNCH_CANCEL,
                PRELAUNCH_LAUNCH_AGY=PRELAUNCH_LAUNCH_AGY,
                PRELAUNCH_LAUNCH_CLAUDE=PRELAUNCH_LAUNCH_CLAUDE,
                PRELAUNCH_LAUNCH_CODEX=PRELAUNCH_LAUNCH_CODEX,
                PRELAUNCH_LAUNCH_CODEX_APP_SERVER=PRELAUNCH_LAUNCH_CODEX_APP_SERVER,
            ),
            process=runtime_launch.AgyLaunchProcess(
                _codex_channel_wake_submit_delay_seconds=_codex_channel_wake_submit_delay_seconds,
                _codex_channel_wake_submit_retries=_codex_channel_wake_submit_retries,
                _log_agy_command_for_diagnostics=_log_agy_command_for_diagnostics,
                path_with_ciel_runtime_user_dirs=path_with_ciel_runtime_user_dirs,
                subprocess_call_with_channel_wake_proxy=subprocess_call_with_channel_wake_proxy,
            ),
            cli_policy=runtime_launch.AgyLaunchCliPolicy(
                agy_dangerous_launch_args=agy_dangerous_launch_args,
                agy_help_requested=agy_help_requested,
                agy_passthrough_args_for_launch=agy_passthrough_args_for_launch,
                agy_passthrough_has_command=agy_passthrough_has_command,
            ),
            channel=runtime_launch.AgyLaunchChannel(
                auto_import_passthrough_channels=auto_import_passthrough_channels,
                channel_delivery_mode=channel_delivery_mode,
            ),
            config=runtime_launch.AgyLaunchConfig(
                current_launch_cwd_key=current_launch_cwd_key,
                get_current_provider=get_current_provider,
                load_config=load_config,
                provider_mode_label=provider_mode_label,
                record_launch_state_for_cwd=record_launch_state_for_cwd,
            ),
            installation=runtime_launch.AgyLaunchInstallation(
                find_executable=find_executable,
                install_agy_if_missing=install_agy_if_missing,
                warn_if_multiple_ciel_runtime_installs=warn_if_multiple_ciel_runtime_installs,
            ),
            dispatch=runtime_launch.AgyLaunchDispatch(
                launch_claude=launch_claude,
                launch_codex=launch_codex,
                launch_codex_app_server=launch_codex_app_server,
                log_agy_passthrough_mapping=log_agy_passthrough_mapping,
                materialize_runtime_command=materialize_runtime_command,
                run_agy_update_check=run_agy_update_check,
                run_ciel_runtime_update_check=run_ciel_runtime_update_check,
                run_prelaunch_menu=run_prelaunch_menu,
            ),
            routing=runtime_launch.AgyLaunchRouting(
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
    return HeadlessEnvFileLoader(
        load=load_dotenv_into_environ
    ).pop_args(argv)


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
    services = cli_dispatch.CliServices(
        core=cli_dispatch.CliCore(
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
        runtime=cli_dispatch.CliRuntime(
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
        provider_commands=cli_dispatch.CliProviderCommands(
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
        channel_commands=cli_dispatch.CliChannelCommands(
            add_channel_spec=add_channel_spec,
            channel_delivery_mode=channel_delivery_mode,
            clear_channel_specs=clear_channel_specs,
            cmd_channels=cmd_channels,
            cmd_mcp_proxy=cmd_mcp_proxy,
            set_channel_delivery_config=set_channel_delivery_config,
            set_channel_development_enabled=set_channel_development_enabled,
        ),
        special_commands=cli_dispatch.CliSpecialCommands(
            cmd_ollama_catalog=cmd_ollama_catalog,
            cmd_ollama_native=cmd_ollama_native,
            cmd_ollama_options=cmd_ollama_options,
            cmd_web_fetch=cmd_web_fetch,
            cmd_web_search=cmd_web_search,
        ),
        operations=cli_dispatch.CliOperations(cmd_status=cmd_status, cmd_stop=cmd_stop, cmd_test=cmd_test),
        configuration=cli_dispatch.CliConfiguration(
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
        cli_parser.CliParserServices(
            launch=cli_parser.CliParserLaunch(
                cli=cmd_cli,
                launch=cmd_launch,
                launch_codex=cmd_launch_codex,
                launch_codex_app_server=cmd_launch_codex_app_server,
                launch_agy=cmd_launch_agy,
                serve=serve,
            ),
            runtime=cli_parser.CliParserRuntime(
                version=cmd_version,
                status=cmd_status,
                env=cmd_env,
                stop=cmd_stop,
                test=cmd_test,
            ),
            settings=cli_parser.CliParserSettings(
                language=cmd_language,
                web_search=cmd_web_search,
                web_fetch=cmd_web_fetch,
                log_level=cmd_log_level,
                channels=cmd_channels,
                channel_delivery=cmd_channel_delivery,
            ),
            provider=cli_parser.CliParserProvider(
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
            models=cli_parser.CliParserModels(
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
