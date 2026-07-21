import ast
import json
import unittest
from dataclasses import fields
from pathlib import Path

from ciel_runtime_support.architecture import (
    LaunchSpec,
    ModelInfo,
    ProviderAdapter,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderContextPolicy,
    ProviderOptionPresentationPolicy,
    ProviderRequestPolicy,
    ProviderStatusPolicy,
    ProviderUiPolicy,
    RuntimeAdapter,
    RuntimeCommand,
    RuntimeConfig,
    ToolDialect,
)
from ciel_runtime_support.anthropic_tool_turns import AnthropicToolTurnServices
from ciel_runtime_support.agy_installer import AgyInstaller, AgyInstallerPorts
from ciel_runtime_support.api_key_cooldown import (
    ApiKeyCooldownCompatibilityApi,
    ApiKeyCooldownPorts,
    ApiKeyCooldownService,
)
from ciel_runtime_support.codex_mcp_integration import (
    CodexMcpArtifactPorts,
    CodexMcpCapabilityPorts,
    CodexMcpConfigPorts,
    CodexMcpIntegrationService,
    CodexMcpProjectionPorts,
)
from ciel_runtime_support.codex_channel_sse_launch import (
    CodexChannelSseEffects,
    CodexChannelSseLaunchService,
    CodexChannelSseQueryPorts,
)
from ciel_runtime_support.codex_launch_configuration import (
    CodexLaunchCatalogPorts,
    CodexLaunchConfigurationConstants,
    CodexLaunchConfigurationEffects,
    CodexLaunchConfigurationService,
    CodexLaunchModelPorts,
    CodexLaunchPolicyPorts,
    build_default_codex_launch_constants as build_default_codex_configuration_constants,
    build_default_codex_launch_policy,
)
from ciel_runtime_support.codex_session_selection import (
    CodexSessionPresentationPorts,
    CodexSessionRepositoryPorts,
    CodexSessionSelectionService,
)
from ciel_runtime_support.tool_exposure_policy import ToolExposurePolicy, ToolExposurePorts
from ciel_runtime_support.synthetic_tool_policy import (
    ForcedPlanModeController,
    ForcedPlanModePorts,
    SyntheticTasklistPolicy,
    SyntheticTasklistPorts,
)
from ciel_runtime_support.advisor_policy import (
    AdvisorDecisionServices,
    AdvisorServices,
    AdvisorTextServices,
)
from ciel_runtime_support.advisor_request_builder import (
    AdvisorAnthropicSystemPolicy,
    AdvisorBudgetPorts,
    AdvisorEndpointPorts,
    AdvisorProjectionPorts,
)
from ciel_runtime_support.advisor_refinement import (
    AdvisorRefinementIO,
    AdvisorRefinementPolicy,
    AdvisorRefinementText,
)
from ciel_runtime_support.advisor_client import (
    AdvisorClientIO,
    AdvisorClientPolicy,
    ProviderChatIO,
    ProviderChatPolicy,
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
)
from ciel_runtime_support.cli_parser import (
    CliParserLaunch,
    CliParserModels,
    CliParserProvider,
    CliParserRuntime,
    CliParserServices,
    CliParserSettings,
)
from ciel_runtime_support.channel_panel import ChannelPanelPolicy
from ciel_runtime_support.channel_backlog import (
    ChannelBacklogCursors,
    ChannelBacklogRuntime,
    ChannelBacklogService,
)
from ciel_runtime_support.context_setup import ContextSetupPorts
from ciel_runtime_support.model_context_hints import ModelContextHintPorts
from ciel_runtime_support.provider_catalog_sources import (
    AnthropicCatalogPolicy,
    FireworksCatalogPolicy,
    ModelCatalogProjectionPorts as CatalogSourceProjectionPorts,
    ProviderCatalogHttpPorts,
    ProviderCatalogPolicyPorts,
    ProviderCatalogSourceService,
    build_default_provider_catalog_source_service,
)
from ciel_runtime_support.provider_endpoint_policy import (
    ProviderEndpointPolicy as ModelEndpointPolicy,
    ProviderEndpointPorts as ModelEndpointPorts,
    ProviderEndpointPresentation as ModelEndpointPresentation,
    build_default_provider_endpoint_policy,
)
from ciel_runtime_support.provider_request_access import (
    ProviderRequestAccessEffects,
    ProviderRequestAccessPorts,
    ProviderRequestAccessService,
)
from ciel_runtime_support.provider_query_policy import ProviderQueryPolicy
from ciel_runtime_support.router_access import RouterAccessHttpController
from ciel_runtime_support.router_health_policy import RouterHealthPolicy
from ciel_runtime_support.web_ui_controller import (
    WebUiConstants,
    WebUiController,
    WebUiDisplayPorts,
    WebUiHttpPorts,
    WebUiProjectionPorts,
)
from ciel_runtime_support.tool_request_projection import (
    UltracodeSessionPolicy,
)
from ciel_runtime_support.provider_tool_policy import ProviderToolPolicy
from ciel_runtime_support.provider_launch_endpoint import (
    ProviderLaunchEndpointGroups,
    ProviderLaunchEndpointPolicy,
    ProviderLaunchEndpointQueries,
    build_default_provider_launch_endpoint_policy,
)
from ciel_runtime_support.provider_endpoint_probe import (
    ProviderEndpointProbePolicy,
    ProviderEndpointProbeProjection,
    ProviderEndpointProbeQueries,
    ProviderEndpointRouteAdapter,
    ProviderEndpointRoutePorts,
)
from ciel_runtime_support.provider_runtime_modes import (
    ProviderNativeCompatibilityPolicy,
    RuntimeModePolicy,
    build_default_native_compatibility_policy,
    build_default_runtime_mode_policy,
)
from ciel_runtime_support.claude_router import (
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
from ciel_runtime_support.channel_compact_poll import (
    ChannelCompactInjectionOptions,
    ChannelCompactPollPolicy,
    ChannelCompactPollServices,
    ChannelCompactPollState,
)
from ciel_runtime_support.channel_compact_injection import (
    ChannelCompactInjectionService,
    ChannelCompactRequestPorts,
    ChannelCompactRuntimePorts,
)
from ciel_runtime_support.channel_config_service import ChannelConfigPorts
from ciel_runtime_support.channel_cursor_service import ChannelDeliveryCursorPorts
from ciel_runtime_support.channel_cli import ChannelCliCommands, ChannelCliView
from ciel_runtime_support.channel_inflight import (
    ChannelInflightEffects,
    ChannelInflightPolicy,
    ChannelInflightSnapshot,
    ChannelInflightUpdate,
)
from ciel_runtime_support.channel_llm_context import (
    ChannelLlmContextPolicy,
    ChannelLlmContextProjection,
    ChannelLlmContextRepository,
    ChannelLlmContextServices,
)
from ciel_runtime_support.channel_mcp_tools import ChannelMcpToolServices
from ciel_runtime_support.channel_mcp_discovery import ChannelMcpDiscoveryPorts
from ciel_runtime_support.channel_mcp_ownership import ChannelRouterLifecyclePorts
from ciel_runtime_support.channel_pending_injection import (
    ChannelInjectionIO,
    ChannelInjectionPolicy,
    ChannelInjectionPrompts,
    ChannelInjectionServices,
    ChannelInjectionState,
    ChannelInjectionWakeStore,
)
from ciel_runtime_support.channel_pending_poll import (
    ChannelPendingInjectionOptions,
    ChannelPendingPollPolicy,
    ChannelPendingPollServices,
    ChannelPendingPollState,
)
from ciel_runtime_support.channel_terminal_proxy import (
    ChannelTerminalIO,
    ChannelTerminalPolicy,
    ChannelTerminalPolling,
    ChannelTerminalProcess,
    ChannelTerminalServices,
    ChannelWindowsConsole,
    ChannelWindowsServices,
)
from ciel_runtime_support.channel_terminal_dispatch import (
    ChannelDirectProcessPorts,
    ChannelTerminalDispatchService,
    ChannelTerminalDispatchSettings,
    ChannelTerminalProxyPorts,
)
from ciel_runtime_support.terminal_platform_io import (
    TerminalInputModeResetPolicy,
)
from ciel_runtime_support.windows_console_mode import (
    WindowsConsoleModePorts,
    WindowsConsoleModeService,
)
from ciel_runtime_support.channel_tool_context import (
    ChannelToolContextPolicy,
    ChannelToolContextPorts,
    ChannelToolContextService,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeStateReaderPorts,
    ChannelWakeTranscriptServices,
)
from ciel_runtime_support.channel_transcript_repository import (
    ChannelTranscriptRepository,
)
from ciel_runtime_support.channel_message_repository import ChannelMessageAppendPorts, ChannelMessageRepository
from ciel_runtime_support.channel_message_dedupe import (
    ChannelMessageDedupePorts,
    ChannelMessageDedupeService,
)
from ciel_runtime_support.channel_wake_claim_repository import ChannelWakeClaimRepository
from ciel_runtime_support.channel_wake_delivery_repository import (
    ChannelWakeDeliveryRepository,
)
from ciel_runtime_support.llm_option_config import (
    AutoLlmModelPolicy,
    AutoLlmOptionsRepository,
    AutoLlmOptionsService,
    AutoLlmPresetPolicy,
)
from ciel_runtime_support.provider_option_status import (
    ProviderContextStatusPorts,
    ProviderContextStatusProjection,
)
from ciel_runtime_support.router_server_runtime import (
    RouterServerConfig,
    RouterServerEffects,
    RouterServerRuntime,
    RouterServerStatePorts,
)
from ciel_runtime_support.channel_launch_guard_repository import ChannelLaunchGuardRepository
from ciel_runtime_support.channel_launch_policy import (
    ChannelLaunchPolicy,
    ChannelLaunchPorts,
)
from ciel_runtime_support.channel_runtime_environment import (
    ChannelRuntimeEnvironmentPolicy,
)
from ciel_runtime_support.channel_cursor_repository import (
    ChannelCursorRepository,
    ChannelCursorStatePolicy,
    CursorReadResolution,
)
from ciel_runtime_support.channel_cursor_recovery import (
    ChannelCursorRecoveryPolicy,
    ChannelCursorRecoveryPorts,
    ChannelCursorRecoveryService,
)
from ciel_runtime_support.channel_session_repository import ChannelSessionRepository
from ciel_runtime_support.channel_session_lifecycle import ChannelSessionLifecycleServices
from ciel_runtime_support.channel_probe_report import ChannelProbeReportServices
from ciel_runtime_support.channel_probe_cache import ChannelProbePorts
from ciel_runtime_support.config_migrations import ConfigMigrationPolicy
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
)
from ciel_runtime_support.compatibility_protocol import CompatibilityProtocolPorts
from ciel_runtime_support.compatibility_probe import (
    CompatibilityApiKeyProbeBuilder,
    CompatibilityApiKeyProbeRunner,
    CompatibilityApiKeyProbeRunnerPorts,
    CompatibilityProbeAnthropicPorts,
    CompatibilityProbeProjectionPorts,
    CompatibilityProbeRoutingPorts,
)
from ciel_runtime_support.compatibility_runtime import (
    CompatibilityCachePorts,
    CompatibilityRuntimePorts,
)
from ciel_runtime_support.claude_environment import (
    ClaudeEnvironmentFeaturePorts,
    ClaudeEnvironmentSourcePorts,
    ClaudeLimitPorts,
    ClaudeModelPorts,
    ClaudeRuntimeSettingsPorts,
)
from ciel_runtime_support.router_client_lifecycle import (
    ManagedRouterLifetimePorts,
    RoutedLaunchDiagnosticPorts,
    RouterClientRegistryPorts,
    RouterClientSupervisorPorts,
    RouterLifetimeRunnerPorts,
)
from ciel_runtime_support.router_process_lifecycle import (
    RouterSpawnPorts,
    RouterStartupStatePorts,
)
from ciel_runtime_support.provider_choice import ProviderChoicePorts
from ciel_runtime_support.provider_model_selection import (
    AdvisorModelMutationPorts,
    ModelMutationConfigPorts,
    ModelMutationEffectPorts,
    ModelMutationPolicyPorts,
)
from ciel_runtime_support.router_http import (
    CodexBackendRequestPorts,
    CodexBackendRetryPorts,
    CodexRoutedHeaderPolicy,
    EventHttpPorts,
)
from ciel_runtime_support.chat_files import ChatFilePorts
from ciel_runtime_support.package_lifecycle import NpmPackageLifecyclePorts, SelfUpdatePorts
from ciel_runtime_support.headless_config import (
    HeadlessChannelCommands,
    HeadlessConfigCommands,
    HeadlessConfigResult,
    HeadlessConfigServices,
    HeadlessEnvFileLoader,
)
from ciel_runtime_support.mcp_proxy_codec import McpProxyCodecPolicy
from ciel_runtime_support.mcp_config_reader import (
    ClaudeMcpConfigPathPolicy,
)
from ciel_runtime_support.managed_mcp_discovery import (
    ManagedMcpDiscoveryPaths,
    ManagedMcpDiscoveryPorts,
    ManagedMcpDiscoveryService,
    NativeMcpConfigWriter,
    NativeMcpConfigWriterPorts,
)
from ciel_runtime_support.mcp_proxy_process import (
    McpStdioConfigPorts,
    McpStdioEffects,
    McpStdioProxyService,
    McpStdioTransportPorts,
)
from ciel_runtime_support.mcp_notification_wait_policy import (
    McpNotificationWaitPolicy,
    McpNotificationWaitPorts,
    McpNotificationWaitService,
)
from ciel_runtime_support.mcp_probe_transport import (
    McpProbeCodec,
    McpProbeHttp,
    McpProbePolicy,
    McpProbeServices,
)
from ciel_runtime_support.mcp_stdio_probe import (
    StdioProbeCodec,
    StdioProbePolicy,
    StdioProbeProcess,
    StdioProbeServices,
)
from ciel_runtime_support.model_panel import (
    ModelPanelCatalog,
    ModelPanelPresentation,
    ModelPanelServices,
)
from ciel_runtime_support.model_catalog_projection import ModelCatalogProjectionServices
from ciel_runtime_support.lm_studio_runtime import LmStudioRuntimeServices
from ciel_runtime_support.provider_request_builder import (
    OllamaRequestPorts,
    OpenAIRequestPorts,
    ProviderOptionPorts,
    ProviderRequestBudget,
)
from ciel_runtime_support.provider_option_status import ProviderOptionStatusPorts
from ciel_runtime_support.provider_option_cli import (
    OllamaOptionCommands,
    ProviderOptionCliConfig,
    ProviderOptionCommands,
)
from ciel_runtime_support.provider_timeout_policy import (
    ProviderTimeoutPorts,
    ProviderTimeoutSettings,
)
from ciel_runtime_support.provider_model_specs import (
    ModelSpecLookupPorts,
    ModelSpecMutationPorts,
    ModelSpecRefreshPorts,
)
from ciel_runtime_support.timeout_profile import (
    TimeoutProfilePorts,
    TimeoutProfileSettings,
)
from ciel_runtime_support.runtime_llm_options import (
    RuntimeLlmConfigPorts,
    RuntimeLlmMutationPorts,
    RuntimeLlmPresentationPorts,
    RuntimeLlmSettings,
)
from ciel_runtime_support.runtime_activity_repository import (
    RuntimeActivityClock,
    RuntimeActivityEffects,
    RuntimeActivityPaths,
    RuntimeActivityRepository,
)
from ciel_runtime_support.live_api_key_controller import LiveApiKeyPorts
from ciel_runtime_support.credential_management import (
    CredentialManagementService,
    CredentialPersistencePorts,
    CredentialPresentationPorts,
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
)
from ciel_runtime_support.tool_side_effect_dedupe import (
    ToolSideEffectDedupePolicy,
    ToolSideEffectDedupePorts,
    ToolSideEffectDedupeService,
)
from ciel_runtime_support.process_control import (
    ProcessControlServices,
    ProcessInspectionServices,
    ProcessQueryServices,
    ProcessSignalServices,
)
from ciel_runtime_support.settings_repository import JsonSettingsRepository, SettingsFileEffects
from ciel_runtime_support.secure_json_repository import SecureJsonEffects, SecureJsonRepository
from ciel_runtime_support.request_trace import (
    RequestTracePolicy,
    RequestTraceProjection,
    RequestTraceServices,
    ResponseTraceController,
    RouterMessagePreviewPolicy,
)
from ciel_runtime_support.statusline_settings import StatusLineServices
from ciel_runtime_support.ollama_forwarding import (
    OllamaForwardAdvisor,
    OllamaForwardConstants,
    OllamaForwardRateLimit,
    OllamaForwardRequest,
    OllamaForwardResponse,
    OllamaForwardServices,
    OllamaForwardStreaming,
)
from ciel_runtime_support.ollama_catalog import OllamaCatalogRefreshServices
from ciel_runtime_support.ollama_catalog_cli import OllamaCatalogCliController
from ciel_runtime_support.openai_forwarding import (
    OpenAIForwardAdvisor,
    OpenAIForwardPolicy,
    OpenAIForwardRateLimit,
    OpenAIForwardRequest,
    OpenAIForwardResponse,
    OpenAIForwardServices,
    OpenAIForwardStreaming,
)
from ciel_runtime_support.openai_responses_router import (
    OpenAIResponsesConversion,
    OpenAIResponsesCore,
    OpenAIResponsesDelivery,
    OpenAIResponsesOutput,
    OpenAIResponsesRouting,
    OpenAIResponsesServices,
)
from ciel_runtime_support.openai_responses_stream import OpenAIResponsesStreamServices
from ciel_runtime_support.mcp_http_proxy import (
    McpHttpProxyCodec,
    McpHttpProxyRuntime,
    McpHttpProxyServices,
    McpHttpProxyTransport,
)
from ciel_runtime_support.mcp_proxy_config import McpProxyConfigPaths, McpProxyConfigPorts, McpProxyConfigService
from ciel_runtime_support.managed_mcp_config import (
    ManagedMcpConfigPaths,
    ManagedMcpConfigPolicy,
    ManagedMcpConfigPorts,
    ManagedMcpConfigService,
)
from ciel_runtime_support.mcp_split_proxy_http import McpSplitProxyHttpPorts
from ciel_runtime_support.provider_config_mutations import ProviderOptionPolicy
from ciel_runtime_support.provider_sampling_policy import ProviderSamplingPolicy
from ciel_runtime_support.provider_configuration_service import (
    ProviderEndpointPolicy,
    ProviderEndpointPorts,
    ProviderEndpointService,
    ProviderStatusProjectionPorts,
    ProviderStatusService,
    RuntimeStatusPorts,
)
from ciel_runtime_support.llm_presets import (
    PresetContextPolicy,
    PresetDefinition,
    PresetIdentityPolicy,
    PresetProviderMutation,
    PresetServices,
)
from ciel_runtime_support.llm_option_config import (
    LlmOptionConfigServices,
    LlmOptionMutation,
    LlmOptionPolicy,
    LlmOptionRepository,
)
from ciel_runtime_support.llm_config_http import (
    LlmConfigHttpController,
    LlmConfigHttpIO,
    LlmConfigIdentity,
    LlmConfigMutations,
    LlmConfigPanels,
)
from ciel_runtime_support.protocols import PROTOCOL_ADAPTERS, OpenAIResponsesProtocolAdapter
from ciel_runtime_support.protocols.chat_projection import (
    ChatProjectionPolicy,
    ChatProjectionServices,
    ChatProjectionText,
    ChatProjectionTools,
    OpenAiHistoryServices,
)
from ciel_runtime_support.protocols.tool_result_projection import ToolResultProjectionServices
from ciel_runtime_support.protocols.pseudo_tool_history import PseudoToolHistoryServices
from ciel_runtime_support.protocols.ollama_response import (
    OllamaResponseOutput,
    OllamaResponseRecovery,
    OllamaResponseServices,
    OllamaResponseText,
    OllamaResponseTools,
)
from ciel_runtime_support.provider_models import (
    ModelCatalogHttp,
    ModelCatalogPolicy,
    ModelCatalogResponseCodec,
    ModelCatalogStorage,
    ProviderCatalogSources,
    ProviderModelServices,
)
from ciel_runtime_support.provider_limits import (
    RateLimitApplyPolicy,
    RateLimitApplyServices,
    RateLimitBackoffPolicy,
    RateLimitBackoffServices,
    RateLimitLearningPolicy,
    RateLimitLearningServices,
    RateLimitStateStore,
)
from ciel_runtime_support.provider_readiness import (
    ProviderReadinessCapabilities,
    ProviderReadinessLmStudio,
    ProviderReadinessMode,
    ProviderReadinessServices,
)
from ciel_runtime_support.provider_runtime_info import ProviderRuntimeInfoPorts, ProviderRuntimeInfoService
from ciel_runtime_support.managed_service_cleanup import (
    ManagedServiceCleanupPolicy,
    ManagedServiceCleanupPorts,
)
from ciel_runtime_support.providers.nvidia_runtime import (
    NvidiaProxyRuntime,
    NvidiaProxyRuntimeConfig,
    NvidiaProxyRuntimePorts,
    NvidiaProxyStopper,
    NvidiaProxyStopPorts,
)
from ciel_runtime_support.provider_status import (
    ProviderStatusCatalog,
    ProviderStatusGeneric,
    ProviderStatusRouting,
    ProviderStatusServices,
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
)
from ciel_runtime_support.sse_stream import SseRetryState, SseStreamServices
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
    build_default_prelaunch_constants,
)
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
)
from ciel_runtime_support.prompt_compaction import (
    PromptCompactionRuntime,
    PromptCompactionServices,
    PromptCompactionText,
)
from ciel_runtime_support.provider_context import ContextPresetServices, ProviderContextServices
from ciel_runtime_support.provider_option_panel import (
    OptionPanelPolicy,
    OptionPanelProvider,
    OptionPanelRuntime,
    OptionPanelServices,
    OptionPanelText,
    OptionValuePolicy,
)
from ciel_runtime_support.context_compaction import (
    ContextCompactionProjection,
    ContextCompactionServices,
    ContextCompactionTransport,
    ContextCompactionWorkflow,
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
    build_default_agy_launch_constants,
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
    build_default_claude_launch_constants,
    CodexLaunchChannel,
    CodexLaunchCliPolicy,
    CodexLaunchConfig,
    CodexLaunchConstants,
    CodexLaunchDispatch,
    CodexLaunchInstallation,
    CodexLaunchProcess,
    CodexLaunchRouting,
    CodexLaunchServices,
    build_default_codex_launch_constants,
    CodexAppServerChannel,
    CodexAppServerCliPolicy,
    CodexAppServerConfig,
    CodexAppServerDispatch,
    CodexAppServerInstallation,
    CodexAppServerLaunchServices,
    CodexAppServerProcess,
    CodexAppServerRouting,
)
from ciel_runtime_support.runtime_command_factory import RuntimeCommandFactory, RuntimeCommandFactoryPorts
from ciel_runtime_support.router_http import (
    RouterHttpCore,
    RouterHttpErrors,
    RouterHttpGetEndpoints,
    RouterHttpPostEndpoints,
    RouterHttpPresentation,
    RouterHttpServices,
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
)
from ciel_runtime_support.upstream_retry import (
    UpstreamRetryHttp,
    UpstreamRetryKeys,
    UpstreamRetryPolicy,
    UpstreamRetryRateLimit,
    UpstreamRetryServices,
)
from ciel_runtime_support.provider_adapters import (
    PROVIDER_ADAPTERS,
    AgyProviderAdapter,
    AnthropicProviderAdapter,
    CodexProviderAdapter,
    DeepSeekProviderAdapter,
    FireworksProviderAdapter,
    HttpBearerProviderAdapter,
    KimiProviderAdapter,
    LMStudioProviderAdapter,
    NvidiaHostedProviderAdapter,
    OllamaCloudProviderAdapter,
    OllamaProviderAdapter,
    OpenCodeGoProviderAdapter,
    OpenCodeProviderAdapter,
    OpenRouterProviderAdapter,
    SelfHostedNimProviderAdapter,
    VllmProviderAdapter,
    ZaiProviderAdapter,
)
from ciel_runtime_support.provider_compatibility import ProviderCompatibilityPolicy
from ciel_runtime_support.registry import AdapterRegistry
from ciel_runtime_support.runtime_adapters import (
    RUNTIME_ADAPTERS,
    ClaudeRuntimeAdapter,
    CliRuntimeAdapter,
    CodexRuntimeAdapter,
)
from ciel_runtime_support.tool_dialects import TOOL_DIALECTS, ClaudeToolDialect


class DummyRuntime(RuntimeAdapter):
    name = "dummy"

    def find_executable(self):
        return Path("dummy")

    def build_command(self, spec):
        return RuntimeCommand(
            argv=("dummy", "--model", spec.provider.model),
            env={"DUMMY_PROVIDER": spec.provider.name},
            cwd=spec.cwd,
        )

    def mcp_config_paths(self, spec):
        return spec.runtime.mcp_config_paths

    def supports_channel_injection(self, spec):
        return spec.runtime.enable_channels


class DummyProvider(ProviderAdapter):
    name = "dummy-provider"

    def default_base_url(self):
        return "https://example.invalid"

    def list_models(self, config):
        return [ModelInfo(id=config.model, context_window=1234)]

    def build_headers(self, config, api_key):
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}


class DummyDialect(ToolDialect):
    name = "dummy-tools"

    def normalize_tool_name(self, name):
        return name.strip()

    def repair_tool_input(self, tool_name, value):
        return dict(value)


class ArchitectureContractTests(unittest.TestCase):
    def test_provider_model_ports_stay_below_dependency_limit(self):
        ports = (
            ProviderModelServices,
            ModelCatalogStorage,
            ModelCatalogHttp,
            ProviderCatalogSources,
            ModelCatalogResponseCodec,
            ModelCatalogPolicy,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_provider_request_builder_ports_stay_below_dependency_limit(self):
        for port in (
            ProviderRequestBudget,
            OllamaRequestPorts,
            OpenAIRequestPorts,
            ProviderOptionPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_provider_option_status_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ProviderOptionStatusPorts)), 10)

    def test_provider_option_cli_ports_stay_below_dependency_limit(self):
        for port in (
            ProviderOptionCliConfig,
            OllamaOptionCommands,
            ProviderOptionCommands,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_provider_timeout_policy_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ProviderTimeoutSettings)), 10)
        self.assertLessEqual(len(fields(ProviderTimeoutPorts)), 10)

    def test_context_setup_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ContextSetupPorts)), 10)

    def test_provider_model_spec_ports_stay_below_dependency_limit(self):
        for port in (
            ModelSpecLookupPorts,
            ModelSpecMutationPorts,
            ModelSpecRefreshPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_timeout_profile_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(TimeoutProfileSettings)), 10)
        self.assertLessEqual(len(fields(TimeoutProfilePorts)), 10)

    def test_model_context_hint_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ModelContextHintPorts)), 10)

    def test_runtime_llm_option_ports_stay_below_dependency_limit(self):
        for port in (
            RuntimeLlmSettings,
            RuntimeLlmConfigPorts,
            RuntimeLlmPresentationPorts,
            RuntimeLlmMutationPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_live_api_key_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(LiveApiKeyPorts)), 10)

    def test_secret_projection_is_owned_by_credentials_module(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        aliases = {
            node.targets[0].id: node.value.id
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Name)
            and node.targets[0].id in {
                "mask_secret",
                "secret_fingerprint",
                "redact_sensitive_text",
                "redact_sensitive_obj",
            }
        }
        self.assertEqual(4, len(aliases))
        for name, target in aliases.items():
            with self.subTest(function=name):
                self.assertEqual(f"project_{name}", target)

    def test_credential_management_owns_persistence_transactions(self):
        for port in (
            CredentialManagementService,
            CredentialPersistencePorts,
            CredentialPresentationPorts,
            ExternalCredentialPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.unparse(node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "store_api_key_config",
                "clear_api_key_config",
                "store_api_keys_config",
                "store_api_key_input_config",
            }
        }
        self.assertEqual(4, len(functions))
        for function_source in functions.values():
            self.assertIn("credential_management_service", function_source)
            self.assertNotIn("save_config", function_source)
            self.assertNotIn("_API_KEY_ROTATION_CURSOR", function_source)

    def test_credential_cli_owns_terminal_workflow(self):
        for port in (CredentialCliController, CredentialCliIO, CredentialCliPolicy, CredentialCliPorts):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.unparse(node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"cmd_set_api_key", "cmd_set_api_keys", "cmd_api_key"}
        }
        self.assertEqual(3, len(functions))
        for function_source in functions.values():
            self.assertIn("credential_cli_controller", function_source)
            self.assertNotIn("getpass", function_source)
            self.assertNotIn("sys.stdin", function_source)

    def test_runtime_paths_live_outside_facade(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertFalse(
            function_names
            & {
                "platform_path",
                "windows_appdata_root",
                "windows_local_appdata_root",
                "platform_config_dir",
                "ciel_runtime_user_bin_dir",
                "agy_user_bin_dir",
                "path_with_ciel_runtime_user_dirs",
                "default_router_port",
            }
        )
        path_source = (root / "ciel_runtime_support" / "runtime_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", path_source)

    def test_runtime_constants_live_outside_facade(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        assigned_names = {
            target.id
            for node in ast.parse(source).body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else (node.target,)
            )
            if isinstance(target, ast.Name)
        }
        self.assertFalse(
            assigned_names
            & {
                "PROVIDER_ALIASES",
                "OPENCODE_ENDPOINT_ALIASES",
                "OFFICIAL_CHANNEL_PLUGINS",
                "ROUTED_COMPAT_PROMPT",
                "MODEL_PRESETS",
                "DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC",
            }
        )
        constants_source = (root / "ciel_runtime_support" / "runtime_constants.py").read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", constants_source)

    def test_mcp_notification_wait_policy_owns_timeout_projection(self):
        for port in (McpNotificationWaitPolicy, McpNotificationWaitPorts, McpNotificationWaitService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.unparse(node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_is_mcp_notification_wait_tool",
                "_mcp_notification_wait_effective_cap_ms",
                "cap_mcp_notification_wait_tool_input",
            }
        }
        self.assertEqual(3, len(functions))
        for function_source in functions.values():
            self.assertIn("mcp_notification_wait_service", function_source)
            self.assertNotIn("os.environ", function_source)
            self.assertNotIn("_MCP_NOTIFICATION_WAIT_RECENT", function_source)

    def test_tool_side_effect_dedupe_owns_hash_and_ttl_state(self):
        for port in (ToolSideEffectDedupePolicy, ToolSideEffectDedupePorts, ToolSideEffectDedupeService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.unparse(node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"side_effect_tool_call_dedupe_key", "should_drop_duplicate_side_effect_tool_call"}
        }
        self.assertEqual(2, len(functions))
        for function_source in functions.values():
            self.assertIn("tool_side_effect_dedupe_service", function_source)
            self.assertNotIn("hashlib", function_source)
            self.assertNotIn("_TOOL_SIDE_EFFECT_DEDUP_RECENT", function_source)

    def test_config_repository_provider_owns_path_aware_cache(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned_names = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
            if isinstance(target, ast.Name)
        }
        self.assertNotIn("_CONFIG_REPOSITORY", assigned_names)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "config_repository"
        )
        function_source = ast.unparse(function)
        self.assertIn("_CONFIG_REPOSITORY_PROVIDER.get", function_source)
        self.assertNotIn("JsonConfigRepository(", function_source)

    def test_claude_launch_ports_stay_below_dependency_limit(self):
        ports = (
            ClaudeLaunchServices,
            ClaudeLaunchConstants,
            ClaudeLaunchProcess,
            ClaudeLaunchInstallation,
            ClaudeLaunchDispatch,
            ClaudeLaunchConfig,
            ClaudeLaunchRouting,
            ClaudeLaunchPolicy,
            ClaudeLaunchChannelDiscovery,
            ClaudeLaunchChannelDelivery,
            ClaudeLaunchMcpConfig,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

        self.assertEqual(
            "ciel_runtime_support.runtime_launch",
            build_default_claude_launch_constants.__module__,
        )

    def test_claude_router_ports_stay_below_dependency_limit(self):
        for port in (
            ClaudeRouterCore,
            ClaudeRouterCountTokens,
            ClaudeRouterPipeline,
            ClaudeRouterShortcuts,
            ClaudeRouterDelivery,
            ClaudeRouterRouting,
            ClaudeRouterNativeNormalization,
            ClaudeRouterTransport,
            ClaudeRouterResponse,
            ClaudeRouterServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_prelaunch_ports_stay_below_dependency_limit(self):
        ports = (
            PrelaunchServices,
            PrelaunchConstants,
            PrelaunchTerminal,
            PrelaunchConfig,
            PrelaunchLaunchPolicy,
            PrelaunchPanelRows,
            PrelaunchChannelQuery,
            PrelaunchChannelCommands,
            PrelaunchMutations,
            PrelaunchSecrets,
            PrelaunchOptions,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

        self.assertEqual(
            "ciel_runtime_support.prelaunch",
            build_default_prelaunch_constants.__module__,
        )

        source = (
            Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("constants=prelaunch.PrelaunchConstants(", source)
        self.assertNotIn("constants=runtime_launch.ClaudeLaunchConstants(", source)

    def test_prelaunch_panel_projection_owns_panel_row_policy(self):
        for port in (
            MainMenuProjectionPorts,
            ProviderPanelConstants,
            ProviderPanelPorts,
            ConfigurationPanelPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(1, len(fields(MainMenuProjection)))
        self.assertEqual(2, len(fields(ProviderPanelProjection)))
        self.assertEqual(1, len(fields(ConfigurationPanelProjection)))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        expected = {
            "main_menu_rows": "main_menu_projection",
            "provider_panel_rows": "provider_panel_projection",
            "language_panel_rows": "configuration_panel_projection",
            "log_level_panel_rows": "configuration_panel_projection",
            "api_key_panel_rows": "configuration_panel_projection",
            "base_url_panel_rows": "configuration_panel_projection",
        }
        for name, projection in expected.items():
            function_source = ast.unparse(functions[name])
            self.assertIn(projection, function_source)
            self.assertNotIn("for ", function_source)

    def test_prelaunch_terminal_ports_stay_below_dependency_limit(self):
        for port in (
            PrelaunchRenderBrand,
            PrelaunchRenderText,
            PrelaunchRenderData,
            PrelaunchRenderServices,
            PrelaunchInputStyle,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_codex_launch_ports_stay_below_dependency_limit(self):
        ports = (
            CodexLaunchServices,
            CodexLaunchConstants,
            CodexLaunchProcess,
            CodexLaunchCliPolicy,
            CodexLaunchConfig,
            CodexLaunchInstallation,
            CodexLaunchDispatch,
            CodexLaunchRouting,
            CodexLaunchChannel,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

        self.assertEqual(
            "ciel_runtime_support.runtime_launch",
            build_default_codex_launch_constants.__module__,
        )

    def test_codex_app_server_ports_stay_below_dependency_limit(self):
        ports = (
            CodexAppServerLaunchServices,
            CodexAppServerProcess,
            CodexAppServerConfig,
            CodexAppServerCliPolicy,
            CodexAppServerInstallation,
            CodexAppServerDispatch,
            CodexAppServerRouting,
            CodexAppServerChannel,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_agy_launch_ports_stay_below_dependency_limit(self):
        ports = (
            AgyLaunchServices,
            AgyLaunchConstants,
            AgyLaunchProcess,
            AgyLaunchCliPolicy,
            AgyLaunchChannel,
            AgyLaunchConfig,
            AgyLaunchInstallation,
            AgyLaunchDispatch,
            AgyLaunchRouting,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

        self.assertEqual(
            "ciel_runtime_support.runtime_launch",
            build_default_agy_launch_constants.__module__,
        )
        source = (
            Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("constants=runtime_launch.CodexLaunchConstants(", source)
        self.assertNotIn("constants=runtime_launch.AgyLaunchConstants(", source)

    def test_router_http_ports_stay_below_dependency_limit(self):
        for port in (
            RouterHttpCore,
            RouterHttpGetEndpoints,
            RouterHttpPostEndpoints,
            RouterHttpPresentation,
            RouterHttpErrors,
            RouterHttpServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_router_http_adapter_has_no_silent_exception_handlers(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "router_http.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        silent_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ]
        self.assertEqual([], silent_handlers)

    def test_compatibility_test_ports_stay_below_dependency_limit(self):
        for port in (
            CompatibilityTestConstants,
            CompatibilityTestConfig,
            CompatibilityTestMode,
            CompatibilityTestRequest,
            CompatibilityTestProtocol,
            CompatibilityTestOutput,
            CompatibilityTestServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_compatibility_protocol_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(CompatibilityProtocolPorts)), 10)

    def test_compatibility_probe_ports_stay_below_dependency_limit(self):
        for port in (
            CompatibilityProbeProjectionPorts,
            CompatibilityProbeRoutingPorts,
            CompatibilityProbeAnthropicPorts,
            CompatibilityApiKeyProbeRunnerPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(3, len(fields(CompatibilityApiKeyProbeBuilder)))
        self.assertEqual(1, len(fields(CompatibilityApiKeyProbeRunner)))

        root = Path(__file__).resolve().parents[1]
        probe_source = (
            root / "ciel_runtime_support" / "compatibility_probe.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('provider == "', probe_source)
        self.assertNotIn("provider in (", probe_source)

        facade_source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(facade_source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "compatibility_api_key_probe_request"
        )
        function_source = ast.unparse(function)
        self.assertIn("CompatibilityApiKeyProbeBuilder", function_source)
        self.assertNotIn("if provider", function_source)

    def test_compatibility_runtime_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(CompatibilityRuntimePorts)), 10)
        self.assertLessEqual(len(fields(CompatibilityCachePorts)), 10)

    def test_claude_environment_ports_stay_below_dependency_limit(self):
        for port in (
            ClaudeLimitPorts,
            ClaudeModelPorts,
            ClaudeEnvironmentSourcePorts,
            ClaudeEnvironmentFeaturePorts,
            ClaudeRuntimeSettingsPorts,
        ):
            self.assertLessEqual(len(fields(port)), 10)

    def test_router_client_lifecycle_ports_stay_below_dependency_limit(self):
        for port in (
            RouterClientRegistryPorts,
            ManagedRouterLifetimePorts,
            RouterClientSupervisorPorts,
            RouterLifetimeRunnerPorts,
            RoutedLaunchDiagnosticPorts,
        ):
            self.assertLessEqual(len(fields(port)), 10)

    def test_router_startup_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(RouterStartupStatePorts)), 10)
        self.assertLessEqual(len(fields(RouterSpawnPorts)), 10)

    def test_provider_choice_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ProviderChoicePorts)), 10)
        self.assertLessEqual(len(fields(AdvisorModelMutationPorts)), 10)

    def test_model_selection_mutation_ports_stay_below_dependency_limit(self):
        for port in (
            ModelMutationConfigPorts,
            ModelMutationPolicyPorts,
            ModelMutationEffectPorts,
        ):
            self.assertLessEqual(len(fields(port)), 10)

    def test_codex_backend_http_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(CodexBackendRequestPorts)), 10)
        self.assertLessEqual(len(fields(CodexBackendRetryPorts)), 10)
        self.assertLessEqual(len(fields(EventHttpPorts)), 10)
        self.assertEqual(2, len(fields(CodexRoutedHeaderPolicy)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "codex_routed_upstream_headers"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("CodexRoutedHeaderPolicy", function_source)
        self.assertNotIn("hop_by_hop", function_source)
        policy_source = (
            root / "ciel_runtime_support" / "router_http.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)

    def test_chat_file_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChatFilePorts)), 10)

    def test_npm_package_lifecycle_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(NpmPackageLifecyclePorts)), 10)
        self.assertLessEqual(len(fields(SelfUpdatePorts)), 10)

    def test_agy_manifest_lifecycle_lives_in_installer_adapter(self):
        self.assertLessEqual(len(fields(AgyInstallerPorts)), 10)
        self.assertLessEqual(len(fields(AgyInstaller)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in ("install_agy_from_manifest", "run_agy_update_check"):
            function = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            with self.subTest(function=function_name):
                function_source = ast.unparse(function)
                self.assertIn("agy_installer", function_source)
                self.assertNotIn("subprocess", function_source)
                self.assertNotIn("tarfile", function_source)

    def test_tool_exposure_policy_owns_blocked_tool_projection(self):
        self.assertLessEqual(len(fields(ToolExposurePorts)), 10)
        self.assertLessEqual(len(fields(ToolExposurePolicy)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "filter_blocked_tools"
        )
        function_source = ast.unparse(function)
        self.assertIn("tool_exposure_policy", function_source)
        self.assertNotIn("tool_choice", function_source)
        self.assertNotIn("EnterPlanMode", function_source)

    def test_synthetic_tool_generation_lives_outside_facade(self):
        for port in (
            SyntheticTasklistPorts,
            SyntheticTasklistPolicy,
            ForcedPlanModePorts,
            ForcedPlanModeController,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name, delegate in (
            ("append_synthetic_tasklist_to_message", "synthetic_tasklist_policy"),
            ("maybe_handle_plan_mode_tool_choice", "forced_plan_mode_controller"),
        ):
            function = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.unparse(function)
            with self.subTest(function=function_name):
                self.assertIn(delegate, function_source)
                self.assertNotIn("toolu_ciel_runtime", function_source)
                self.assertNotIn("EnterPlanMode", function_source)

    def test_terminal_mouse_filter_lives_outside_composition_root(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertNotIn("class _TerminalMouseInputFilter", source)
        self.assertIn("TerminalMouseInputFilter as _TerminalMouseInputFilter", source)

    def test_ollama_forwarding_ports_stay_below_dependency_limit(self):
        for port in (
            OllamaForwardConstants,
            OllamaForwardRequest,
            OllamaForwardRateLimit,
            OllamaForwardStreaming,
            OllamaForwardAdvisor,
            OllamaForwardResponse,
            OllamaForwardServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_ollama_catalog_refresh_port_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(OllamaCatalogRefreshServices)), 10)
        self.assertEqual(3, len(fields(OllamaCatalogCliController)))
        source = (
            Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def model_lookup_ids(", source)
        self.assertIn(
            "model_lookup_ids = ollama_catalog_policy.model_lookup_ids",
            source,
        )
        self.assertNotIn("def nvidia_hosted_context_default(", source)
        self.assertIn(
            "hosted_context_default as nvidia_hosted_context_default",
            source,
        )

    def test_ollama_response_projection_ports_stay_below_dependency_limit(self):
        for port in (
            OllamaResponseText,
            OllamaResponseTools,
            OllamaResponseRecovery,
            OllamaResponseOutput,
            OllamaResponseServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_upstream_retry_ports_stay_below_dependency_limit(self):
        for port in (
            UpstreamRetryPolicy,
            UpstreamRetryKeys,
            UpstreamRetryRateLimit,
            UpstreamRetryHttp,
            UpstreamRetryServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_openai_forwarding_ports_stay_below_dependency_limit(self):
        for port in (
            OpenAIForwardPolicy,
            OpenAIForwardRequest,
            OpenAIForwardRateLimit,
            OpenAIForwardAdvisor,
            OpenAIForwardStreaming,
            OpenAIForwardResponse,
            OpenAIForwardServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_openai_responses_router_ports_stay_below_dependency_limit(self):
        for port in (
            OpenAIResponsesCore,
            OpenAIResponsesConversion,
            OpenAIResponsesRouting,
            OpenAIResponsesDelivery,
            OpenAIResponsesOutput,
            OpenAIResponsesServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertLessEqual(len(fields(OpenAIResponsesStreamServices)), 10)

    def test_response_collection_ports_stay_below_dependency_limit(self):
        for port in (
            ChatCollectionStrategy,
            ResponseCollectionRequest,
            ResponseCollectionRateLimit,
            ResponseCollectionProjection,
            ResponseCollectionServices,
            AnthropicCollectionRequest,
            AnthropicCollectionTransport,
            AnthropicCollectionProjection,
            AnthropicCollectionServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_stdio_mcp_probe_has_no_silent_exception_handlers(self):
        source = (
            Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "mcp_stdio_probe.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "probe_stdio_mcp_for_channel_capability_detailed"
        )
        silent_handlers = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ]
        self.assertEqual([], silent_handlers)

    def test_stdio_mcp_probe_ports_stay_below_dependency_limit(self):
        for port in (StdioProbeCodec, StdioProbeProcess, StdioProbePolicy, StdioProbeServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_mcp_probe_transport_ports_stay_below_dependency_limit(self):
        for port in (McpProbeCodec, McpProbeHttp, McpProbePolicy, McpProbeServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_mcp_probe_transport_has_no_silent_exception_handlers(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "mcp_probe_transport.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        silent_handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        ]
        self.assertEqual([], silent_handlers)

    def test_chat_projection_ports_stay_below_dependency_limit(self):
        for port in (
            ChatProjectionText,
            ChatProjectionTools,
            ChatProjectionPolicy,
            ChatProjectionServices,
            OpenAiHistoryServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_tool_result_projection_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ToolResultProjectionServices)), 10)

    def test_pseudo_tool_history_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(PseudoToolHistoryServices)), 10)

    def test_pending_channel_injection_ports_stay_below_dependency_limit(self):
        for port in (
            ChannelInjectionState,
            ChannelInjectionPrompts,
            ChannelInjectionWakeStore,
            ChannelInjectionIO,
            ChannelInjectionPolicy,
            ChannelInjectionServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_pending_channel_poll_ports_stay_below_dependency_limit(self):
        for port in (
            ChannelPendingPollState,
            ChannelPendingInjectionOptions,
            ChannelPendingPollPolicy,
            ChannelPendingPollServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_inflight_state_stays_below_dependency_limit(self):
        for port in (
            ChannelInflightSnapshot,
            ChannelInflightPolicy,
            ChannelInflightEffects,
            ChannelInflightUpdate,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_llm_context_ports_stay_below_dependency_limit(self):
        for port in (
            ChannelLlmContextPolicy,
            ChannelLlmContextRepository,
            ChannelLlmContextProjection,
            ChannelLlmContextServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_compact_poll_ports_stay_below_dependency_limit(self):
        for port in (
            ChannelCompactPollState,
            ChannelCompactInjectionOptions,
            ChannelCompactPollPolicy,
            ChannelCompactPollServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_compact_injection_is_application_service_owned(self):
        self.assertEqual(3, len(fields(ChannelCompactInjectionService)))
        self.assertEqual(2, len(fields(ChannelCompactRequestPorts)))
        self.assertEqual(5, len(fields(ChannelCompactRuntimePorts)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_inject_pending_compact_request"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("ChannelCompactInjectionService", function_source)
        self.assertNotIn("request.get(", function_source)
        service_source = (
            root
            / "ciel_runtime_support"
            / "channel_compact_injection.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)

    def test_channel_mcp_tool_services_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelMcpToolServices)), 10)

    def test_channel_probe_report_services_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelProbeReportServices)), 10)

    def test_channel_probe_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelProbePorts)), 10)

    def test_channel_mcp_discovery_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelMcpDiscoveryPorts)), 10)

    def test_channel_router_lifecycle_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelRouterLifecyclePorts)), 10)

    def test_channel_config_ports_stay_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelConfigPorts)), 10)

    def test_channel_cli_ports_stay_below_dependency_limit(self):
        for port in (ChannelCliView, ChannelCliCommands):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_sse_stream_state_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(SseStreamServices)), 10)
        self.assertLessEqual(len(fields(SseRetryState)), 10)

    def test_cli_ports_stay_below_dependency_limit(self):
        ports = (
            CliServices,
            CliCore,
            CliRuntime,
            CliProviderCommands,
            CliChannelCommands,
            CliSpecialCommands,
            CliOperations,
            CliConfiguration,
        )

        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_configuration_cli_controller_owns_command_flow(self):
        for port in (
            ConfigurationCliConfigPorts,
            ConfigurationCliProviderPorts,
            ConfigurationCliModelPorts,
            ConfigurationCliDisplayPorts,
            ConfigurationCliIO,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(5, len(fields(ConfigurationCliController)))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "cmd_provider",
            "cmd_base_url",
            "cmd_model",
            "cmd_advisor_model",
            "cmd_models",
            "cmd_log_level",
            "cmd_language",
            "cmd_web_search",
            "cmd_web_fetch",
            "portable_provider_menu",
            "portable_language_menu",
        ):
            function_source = ast.unparse(functions[name])
            self.assertIn("configuration_cli_controller", function_source)
            self.assertNotIn("load_config", function_source)
            self.assertNotIn("for ", function_source)

    def test_headless_config_ports_stay_below_dependency_limit(self):
        for port in (
            HeadlessConfigCommands,
            HeadlessChannelCommands,
            HeadlessConfigServices,
            HeadlessConfigResult,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(1, len(fields(HeadlessEnvFileLoader)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "pop_headless_env_file_args"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("HeadlessEnvFileLoader", function_source)
        self.assertNotIn("while ", function_source)

    def test_preset_ports_stay_below_dependency_limit(self):
        for port in (PresetServices, PresetDefinition, PresetContextPolicy, PresetProviderMutation):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(2, len(fields(PresetIdentityPolicy)))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        resolver_source = ast.unparse(functions["resolve_llm_preset_id"])
        self.assertIn("PresetIdentityPolicy", resolver_source)
        self.assertNotIn("aliases", resolver_source)

    def test_llm_option_config_ports_stay_below_dependency_limit(self):
        for port in (
            LlmOptionRepository,
            LlmOptionMutation,
            LlmOptionPolicy,
            LlmOptionConfigServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_llm_config_http_controller_owns_projection_and_dispatch(self):
        for port in (
            LlmConfigIdentity,
            LlmConfigPanels,
            LlmConfigMutations,
            LlmConfigHttpIO,
            LlmConfigHttpController,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn('if action == "model"', source)
        self.assertIn("LlmConfigHttpController", source)

    def test_runtime_activity_repository_owns_atomic_snapshot_writes(self):
        for port in (
            RuntimeActivityPaths,
            RuntimeActivityClock,
            RuntimeActivityEffects,
            RuntimeActivityRepository,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "write_router_activity",
            "write_context_compact_activity",
            "write_context_usage",
        ):
            function = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            with self.subTest(function=function_name):
                self.assertNotIn("except", ast.unparse(function))
                self.assertNotIn("write_text", ast.unparse(function))

    def test_rate_limit_ports_stay_below_dependency_limit(self):
        ports = (
            RateLimitStateStore,
            RateLimitLearningServices,
            RateLimitLearningPolicy,
            RateLimitBackoffServices,
            RateLimitBackoffPolicy,
            RateLimitApplyServices,
            RateLimitApplyPolicy,
        )
        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_openai_stream_ports_stay_below_dependency_limit(self):
        for port in (
            OpenAIChatStreamServices,
            OpenAIChatStreamIO,
            OpenAIChatToolProjection,
            OpenAIChatContinuationPolicy,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_ollama_stream_ports_stay_below_dependency_limit(self):
        for port in (
            OllamaStreamServices,
            OllamaStreamIO,
            OllamaStreamTrace,
            OllamaToolProjection,
            OllamaContinuationPolicy,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_anthropic_stream_ports_stay_below_dependency_limit(self):
        for port in (
            AnthropicStreamServices,
            AnthropicStreamIO,
            AnthropicToolProjection,
            AnthropicToolPolicy,
            AnthropicConversationContext,
            AnthropicContinuationPolicy,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_config_migration_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ConfigMigrationPolicy)), 10)

    def test_provider_option_policy_stays_below_dependency_limit(self):
        option_fields = fields(ProviderOptionPolicy)
        self.assertLessEqual(len(option_fields), 10)
        self.assertIn("sampling", {field.name for field in option_fields})
        self.assertNotIn("validate_sampling_option", {field.name for field in option_fields})
        self.assertLessEqual(len(fields(ProviderConfigurationPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderRequestPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderStatusPolicy)), 10)
        self.assertLessEqual(len(fields(AnthropicToolTurnServices)), 10)

    def test_provider_sampling_policy_owns_sampling_validation(self):
        policy = ProviderSamplingPolicy()
        self.assertEqual("temperature", policy.option_key("temp"))
        self.assertEqual(0.5, policy.validate("top_p", 0.5))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in ("sampling_option_key", "validate_sampling_option"):
            function_source = ast.get_source_segment(source, functions[name]) or ""
            self.assertIn("ProviderSamplingPolicy", function_source)
            self.assertLessEqual(len(function_source.splitlines()), 2)

    def test_channel_delivery_cursor_committer_has_bounded_ports(self):
        self.assertLessEqual(len(fields(ChannelDeliveryCursorPorts)), 5)

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "commit_pending_channel_delivery_cursors"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("ChannelDeliveryCursorCommitter", function_source)
        self.assertNotIn("status < 200", function_source)

    def test_provider_status_ports_stay_below_dependency_limit(self):
        for port in (
            ProviderStatusRouting,
            ProviderStatusCatalog,
            ProviderStatusGeneric,
            ProviderStatusServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_advisor_policy_ports_stay_below_dependency_limit(self):
        for port in (AdvisorTextServices, AdvisorDecisionServices, AdvisorServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_advisor_request_builder_ports_stay_below_dependency_limit(self):
        for port in (AdvisorProjectionPorts, AdvisorBudgetPorts, AdvisorEndpointPorts):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(2, len(fields(AdvisorAnthropicSystemPolicy)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "anthropic_system_with_advisor"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("AdvisorAnthropicSystemPolicy", function_source)
        self.assertNotIn("blocks.append", function_source)
        policy_source = (
            root
            / "ciel_runtime_support"
            / "advisor_request_builder.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)

    def test_advisor_refinement_ports_stay_below_dependency_limit(self):
        for port in (AdvisorRefinementText, AdvisorRefinementPolicy, AdvisorRefinementIO):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_advisor_client_ports_stay_below_dependency_limit(self):
        for port in (AdvisorClientPolicy, AdvisorClientIO, ProviderChatPolicy, ProviderChatIO):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_advisor_tool_stripping_has_no_provider_name_branch(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "strip_autonomous_advisor_server_tools"
        )
        comparisons = [
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(item, ast.Name) and item.id == "provider"
                for item in (node.left, *node.comparators)
            )
        ]
        self.assertEqual([], comparisons)

    def test_provider_readiness_ports_stay_below_dependency_limit(self):
        for port in (
            ProviderReadinessMode,
            ProviderReadinessCapabilities,
            ProviderReadinessLmStudio,
            ProviderReadinessServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_prompt_compaction_ports_stay_below_dependency_limit(self):
        for port in (PromptCompactionText, PromptCompactionRuntime, PromptCompactionServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_prompt_compaction_does_not_dispatch_on_provider_names(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "prompt_compaction.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertNotIn('provider == "', source)
        self.assertNotIn("provider in (", source)

    def test_context_compaction_ports_stay_below_dependency_limit(self):
        for port in (
            ContextCompactionTransport,
            ContextCompactionWorkflow,
            ContextCompactionProjection,
            ContextCompactionServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_provider_context_port_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ProviderContextPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderContextServices)), 10)
        self.assertLessEqual(len(fields(ContextPresetServices)), 10)
        self.assertLessEqual(len(fields(ProviderOptionPresentationPolicy)), 10)
        for port in (
            OptionPanelPolicy,
            OptionPanelText,
            OptionPanelRuntime,
            OptionPanelProvider,
            OptionPanelServices,
            OptionValuePolicy,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_context_compaction_does_not_dispatch_on_provider_names(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "context_compaction.py"
        source = source_path.read_text(encoding="utf-8")
        self.assertNotIn('provider == "', source)
        self.assertNotIn("provider in (", source)

    def test_launch_readiness_dispatches_through_provider_policy(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "launch_readiness_errors"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("status_policy", function_source)
        self.assertNotIn('provider == "', function_source)
        self.assertNotIn("provider in (", function_source)

    def test_base_url_status_dispatches_through_provider_policy(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "base_url_status_line"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("status_policy", function_source)
        self.assertNotIn('provider == "', function_source)
        self.assertNotIn("provider in (", function_source)

    def test_provider_option_mutations_do_not_branch_on_provider_names(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime_support" / "provider_config_mutations.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        provider_comparisons = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            compared_names = {
                item.id
                for item in (node.left, *node.comparators)
                if isinstance(item, ast.Name)
            }
            if "provider" in compared_names:
                provider_comparisons.append(node.lineno)

        self.assertEqual([], provider_comparisons)

    def test_provider_status_projection_does_not_branch_on_provider_names(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "provider_configuration_service.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        provider_comparisons = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            compared_names = {
                item.id
                for item in (node.left, *node.comparators)
                if isinstance(item, ast.Name)
            }
            if "provider" in compared_names:
                provider_comparisons.append(node.lineno)

        self.assertEqual([], provider_comparisons)

    def test_provider_configuration_services_stay_typed_and_outside_facade(self):
        for port in (
            ProviderEndpointPolicy,
            ProviderEndpointPorts,
            ProviderEndpointService,
            ProviderStatusProjectionPorts,
            ProviderStatusService,
            RuntimeStatusPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.unparse(node)
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"set_base_url_config", "status_lines"}
        }
        self.assertIn("provider_endpoint_service", functions["set_base_url_config"])
        self.assertIn("provider_status_service", functions["status_lines"])
        for function_source in functions.values():
            self.assertNotIn("save_config", function_source)
            self.assertNotIn("configuration_policy", function_source)

    def test_provider_menu_projection_does_not_branch_on_provider_names(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            "provider_menu_label",
            "current_provider_panel_choice",
            "main_menu_rows",
            "claude_launch_enabled_for_provider",
            "agy_launch_enabled_for_provider",
            "codex_launch_enabled_for_provider",
        }
        functions = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        offenders = [
            (function.name, node.lineno)
            for function in functions
            for node in ast.walk(function)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(item, ast.Name) and item.id == "provider"
                for item in (node.left, *node.comparators)
            )
        ]
        self.assertEqual([], offenders)
        self.assertEqual(names, {function.name for function in functions})

    def test_provider_ui_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ProviderUiPolicy)), 10)
        self.assertIn(
            "uses_native_advisor",
            {field.name for field in fields(ProviderUiPolicy)},
        )

    def test_provider_ui_policy_does_not_own_runtime_launch_flags(self):
        field_names = {field.name for field in fields(ProviderUiPolicy)}
        self.assertFalse(
            field_names
            & {
                "supports_claude_launch",
                "supports_codex_launch",
                "supports_agy_launch",
                "incompatible_runtime_family",
            }
        )

    def test_provider_selection_defaults_dispatch_through_adapter(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "provider_choice.py"
        )
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for class_node in tree.body
            if isinstance(class_node, ast.ClassDef)
            and class_node.name == "ProviderChoiceController"
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "select_standard"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("selection_config_updates", function_source)
        self.assertIn("selection_status_lines", function_source)
        for provider in ("anthropic", "agy", "codex"):
            self.assertNotIn(f'provider == "{provider}"', function_source)

    def test_advisor_transport_dispatches_through_compatibility_registry(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"advisor_provider_kind", "call_provider_chat_once"}
        }
        self.assertIn("PROVIDER_COMPATIBILITY.resolve(provider)", functions["advisor_provider_kind"])
        self.assertIn("advisor_transport", functions["advisor_provider_kind"])
        for function_source in functions.values():
            for provider in ("anthropic", "ollama", "ollama-cloud", "lm-studio", "nvidia-hosted"):
                self.assertNotIn(f'provider == "{provider}"', function_source)
            self.assertNotIn("provider in (", function_source)

    def test_context_status_limit_dispatches_through_provider_policy(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "context_limit_for_status"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("status_capacity_strategy", function_source)
        self.assertNotIn('provider == "', function_source)
        self.assertNotIn("provider in (", function_source)

    def test_compatibility_diagnosis_dispatches_through_compatibility_registry(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {"compatibility_failure_diagnosis", "known_compatibility_tool_use_blocker"}
        functions = [
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        for function in functions:
            function_source = ast.get_source_segment(source, function) or ""
            self.assertIn("PROVIDER_COMPATIBILITY.resolve(provider)", function_source)
            self.assertNotIn('provider == "', function_source)
            self.assertNotIn("provider in (", function_source)
        self.assertEqual(names, {function.name for function in functions})

    def test_compatibility_runtime_output_dispatches_through_compatibility_registry(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"compatibility_runtime_projection", "compatibility_runtime_lines"}
        }
        self.assertIn("provider_policy=PROVIDER_COMPATIBILITY.resolve", functions["compatibility_runtime_projection"])
        self.assertIn("compatibility_runtime_projection().lines", functions["compatibility_runtime_lines"])
        combined_source = "\n".join(functions.values())
        self.assertNotIn('provider == "', combined_source)
        self.assertNotIn("provider in (", combined_source)

    def test_main_module_imports_provider_labels_from_registry_module(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == "PROVIDER_LABELS"
                for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
            )
        ]
        self.assertEqual([], assignments)
        self.assertIn("from ciel_runtime_support.provider_adapters import", source)

    def test_default_config_hydrates_all_registered_providers(self):
        import ciel_runtime

        self.assertEqual(
            set(PROVIDER_ADAPTERS.names()),
            set(ciel_runtime.DEFAULT_CONFIG["providers"]),
        )
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertIn("build_default_config(provider_default_configurations())", source)
        repository_source = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "config_repository.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"providers": provider_defaults', repository_source)
        self.assertNotIn("for _registered_provider in PROVIDER_ADAPTERS.names()", source)

    def test_provider_model_identity_is_a_service_and_adapter_strategy(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        identity_source = (
            root / "ciel_runtime_support" / "provider_model_identity.py"
        ).read_text(encoding="utf-8")
        nvidia_source = (
            root / "ciel_runtime_support" / "providers" / "nvidia.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "normalize_provider",
            "normalize_provider_choice",
            "slug",
            "model_sort_key",
            "sorted_model_ids",
            "unique_model_ids",
            "normalize_model_id",
            "strip_claude_context_suffix",
            "upstream_api_model_id",
            "alias_for",
            "unslug_provider_alias",
            "display_name",
            "anthropic_model_family_from_id",
            "anthropic_model_limit_hints",
            "anthropic_model_runtime_hints",
            "normalize_claude_code_supported_capabilities",
            "anthropic_recommended_preset_for_model",
            "parse_retry_after_seconds",
            "format_duration_seconds",
            "first_header",
            "first_int_in_header",
            "rate_limit_reset_seconds",
        }

        self.assertIn("_PROVIDER_MODEL_IDENTITY = ProviderModelIdentityService(", source)
        self.assertIn("ProviderModelIdentityApi", source)
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertNotIn("__getattr__", identity_source)
        self.assertIn("class ProviderAdapterRegistryPort(Protocol):", identity_source)
        self.assertIn("def display_model_name", nvidia_source)

    def test_model_cache_lifecycle_is_not_orchestrated_by_the_facade(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name, delegation in (
            ("clear_model_cache", ".clear()"),
            ("cached_or_configured_model_ids", ".cached_or_configured_ids("),
            ("ensure_model_cache_for_launch", ".ensure_for_launch("),
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("model_cache_lifecycle_service()", function_source)
                self.assertIn(delegation, function_source)

    def test_api_key_cooldown_policy_and_state_are_service_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        delegated = {
            "_api_key_cooldown_state_key",
            "api_key_cooldown_reset_seconds",
            "register_api_key_cooldown",
            "api_key_cooldown_until",
            "provider_live_api_key_count",
            "provider_has_live_api_key",
            "reset_api_key_cooldowns_for_router_start",
            "retry_after_exceeds_request_timeout",
        }
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("ApiKeyCooldownCompatibilityApi", source)
        service_source = (
            root / "ciel_runtime_support" / "api_key_cooldown.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class ApiKeyCooldownService:", service_source)
        self.assertIn("hashlib.sha256", service_source)
        self.assertNotIn("__getattr__", service_source)
        self.assertLessEqual(len(fields(ApiKeyCooldownPorts)), 6)
        self.assertEqual(1, len(fields(ApiKeyCooldownService)))
        self.assertEqual(1, len(fields(ApiKeyCooldownCompatibilityApi)))

    def test_router_rate_limit_orchestration_is_service_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        delegated = {
            "router_rate_limit_legacy_key",
            "router_rate_limit_configured_rpm",
            "router_rate_limit_rpm",
            "router_rate_limit_key",
            "router_rate_limit_state_entry",
            "router_rate_limit_effective_rpm",
            "router_rate_limit_capacity",
            "router_rate_limit_recent",
            "router_rate_limit_usage",
            "record_router_rate_usage",
            "learn_router_rate_limit_headers",
            "register_router_rate_limit_backoff",
            "apply_router_rate_limit",
            "wait_for_router_rate_limit_penalty",
        }
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("RouterRateLimitApi", source)
        service_source = (
            root / "ciel_runtime_support" / "router_rate_limit_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class RouterRateLimitService:", service_source)
        self.assertIn("class RouterRateLimitApi:", service_source)
        self.assertIn("repository: RateLimitRepository", service_source)
        self.assertNotIn("__getattr__", service_source)

    def test_runtime_llm_options_uses_explicit_typed_api(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "handle_live_llm_options_action",
            "runtime_llm_snapshot_from_provider",
            "ensure_runtime_llm_original_snapshot",
            "restore_runtime_llm_original_options",
            "apply_runtime_llm_preset_config",
            "runtime_llm_slider_line",
            "apply_runtime_llm_slider_delta_config",
            "runtime_llm_status_lines",
            "runtime_llm_preset_list_lines",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("RuntimeLlmOptionsApi", source)
        service_source = (
            root / "ciel_runtime_support" / "runtime_llm_options.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", service_source)

    def test_router_model_metadata_is_projected_by_provider_adapters(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "model_object"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertNotIn('provider == "opencode', function_source)
        self.assertNotIn("OPENCODE_PROVIDER_NAMES", function_source)
        model_object = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "model_object"
        )
        self.assertIn(
            "adapter.project_router_model_metadata",
            ast.get_source_segment(source, model_object) or "",
        )

        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        endpoint_exports = {
            "opencode_zen_endpoint_kind",
            "opencode_zen_model_supported_by_router",
            "normalize_opencode_endpoint_kind",
            "opencode_endpoint_override",
            "opencode_go_endpoint_kind",
            "opencode_endpoint_kind",
            "opencode_model_supported_by_router",
            "opencode_endpoint_display",
        }
        self.assertTrue(endpoint_exports.isdisjoint(root_functions))
        endpoint_source = (
            root / "ciel_runtime_support" / "provider_endpoint_policy.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('provider == "opencode', endpoint_source)
        self.assertNotIn("import ciel_runtime", endpoint_source)

    def test_model_endpoint_policy_uses_small_typed_ports(self):
        self.assertLessEqual(len(fields(ModelEndpointPorts)), 4)
        self.assertLessEqual(len(fields(ModelEndpointPresentation)), 3)
        self.assertEqual(2, len(fields(ModelEndpointPolicy)))

    def test_provider_request_access_has_no_provider_name_branch(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        root_functions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "provider_upstream_model",
            "provider_requires_streaming",
            "key_from_request_headers",
            "provider_headers",
            "get_current_provider",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        service_source = (
            root / "ciel_runtime_support" / "provider_request_access.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('provider == "anthropic"', service_source)
        self.assertNotIn("import ciel_runtime", service_source)
        self.assertNotIn("__getattr__", service_source)
        anthropic_source = (
            root / "ciel_runtime_support" / "providers" / "anthropic.py"
        ).read_text(encoding="utf-8")
        self.assertIn('credential_strategy="anthropic_inbound"', anthropic_source)

    def test_provider_request_access_uses_small_typed_ports(self):
        self.assertLessEqual(len(fields(ProviderRequestAccessPorts)), 5)
        self.assertLessEqual(len(fields(ProviderRequestAccessEffects)), 3)
        self.assertEqual(2, len(fields(ProviderRequestAccessService)))
        self.assertEqual(2, len(fields(ProviderQueryPolicy)))

    def test_runtime_modes_are_policy_owned_without_facade_branches(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        root_functions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "native_anthropic_enabled",
            "anthropic_routed_enabled",
            "direct_native_anthropic_enabled",
            "native_agy_enabled",
            "agy_routed_enabled",
            "direct_native_agy_enabled",
            "native_codex_enabled",
            "codex_routed_enabled",
            "direct_native_codex_enabled",
            "provider_native_compat_enabled",
            "ollama_native_compat_enabled",
            "vllm_native_compat_enabled",
            "nim_native_compat_enabled",
            "lm_studio_native_compat_enabled",
            "nvidia_hosted_native_compat_enabled",
            "deepseek_native_compat_enabled",
            "opencode_native_compat_enabled",
            "kimi_native_compat_enabled",
            "zai_native_compat_enabled",
            "fireworks_native_compat_enabled",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        policy_source = (
            root / "ciel_runtime_support" / "provider_runtime_modes.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)
        self.assertNotIn("__getattr__", policy_source)
        self.assertEqual(2, len(fields(RuntimeModePolicy)))
        self.assertEqual(2, len(fields(ProviderNativeCompatibilityPolicy)))
        self.assertEqual(
            "ciel_runtime_support.provider_runtime_modes",
            build_default_runtime_mode_policy.__module__,
        )
        self.assertEqual(
            "ciel_runtime_support.provider_runtime_modes",
            build_default_native_compatibility_policy.__module__,
        )
        self.assertNotIn("_RUNTIME_MODE_POLICY = RuntimeModePolicy(", source)
        self.assertNotIn(
            "_PROVIDER_NATIVE_COMPATIBILITY = ProviderNativeCompatibilityPolicy(",
            source,
        )

    def test_launch_endpoint_preference_is_owned_by_typed_policy(self):
        self.assertEqual(2, len(fields(ProviderLaunchEndpointPolicy)))
        self.assertEqual(5, len(fields(ProviderLaunchEndpointGroups)))
        self.assertEqual(2, len(fields(ProviderLaunchEndpointQueries)))

        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name
            == "preferred_native_compat_for_launch_runtime"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("build_default_provider_launch_endpoint_policy", function_source)
        self.assertNotIn('runtime == "claude"', function_source)
        self.assertEqual(
            "ciel_runtime_support.provider_launch_endpoint",
            build_default_provider_launch_endpoint_policy.__module__,
        )
        policy_source = (
            root
            / "ciel_runtime_support"
            / "provider_launch_endpoint.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)
        self.assertNotIn("__getattr__", policy_source)

    def test_provider_endpoint_probe_uses_typed_policy_and_adapter(self):
        self.assertEqual(1, len(fields(ProviderEndpointRouteAdapter)))
        self.assertEqual(4, len(fields(ProviderEndpointRoutePorts)))
        self.assertEqual(2, len(fields(ProviderEndpointProbePolicy)))
        self.assertEqual(3, len(fields(ProviderEndpointProbeProjection)))
        self.assertEqual(3, len(fields(ProviderEndpointProbeQueries)))

        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn(
            "ProviderEndpointRouteAdapter",
            functions["provider_endpoint_route_adapter"],
        )
        self.assertIn(
            "ProviderEndpointProbePolicy",
            functions["provider_endpoint_probe_policy"],
        )
        self.assertNotIn(
            "urllib.request.Request(",
            functions["endpoint_route_exists"],
        )
        policy_source = (
            root
            / "ciel_runtime_support"
            / "provider_endpoint_probe.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)
        self.assertNotIn("__getattr__", policy_source)

    def test_npm_runtime_utilities_are_infrastructure_reexports(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        moved = {
            "parse_version_tuple",
            "version_newer",
            "npm_latest_package_version",
            "npm_global_package_root",
            "npm_prefix_from_package_root",
            "npm_global_install_command",
            "npm_global_bin_dir_from_prefix",
            "claude_code_current_version",
            "codex_current_version",
            "package_root_from_installed_path",
        }
        definitions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertFalse(moved & definitions)
        self.assertIn("from ciel_runtime_support.npm_runtime import (", source)

    def test_install_diagnostics_are_owned_by_an_application_service(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in (
            "ciel_runtime_launcher_candidate_dirs",
            "ciel_runtime_launcher_candidates",
            "ciel_runtime_launcher_version",
            "ciel_runtime_install_diagnostics",
            "warn_if_multiple_ciel_runtime_installs",
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("install_diagnostics_service()", function_source)
                self.assertNotIn("subprocess.run", function_source)
        service_source = (
            root / "ciel_runtime_support" / "install_diagnostics.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class InstallDiagnosticsService:", service_source)

    def test_quiet_toolchain_upgrades_are_service_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name, method in (
            ("quiet_upgrade_ciel_runtime", ".ciel_runtime()"),
            ("quiet_upgrade_claude_code", ".claude()"),
            ("quiet_upgrade_codex", ".codex()"),
            ("quiet_upgrade_agy", ".agy()"),
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            with self.subTest(function=function_name):
                self.assertIn("runtime_upgrade_service()", function_source)
                self.assertIn(method, function_source)
                self.assertNotIn("find_executable", function_source)
        command_runner = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_command_for_upgrade"
        )
        command_source = ast.get_source_segment(source, command_runner) or ""
        self.assertIn("run_upgrade_command", command_source)
        self.assertNotIn("subprocess.run", command_source)

    def test_runtime_restart_effects_are_service_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        restart = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "restart_ciel_runtime_after_update"
        )
        restart_source = ast.get_source_segment(source, restart) or ""
        self.assertIn("runtime_restart_service().restart", restart_source)
        self.assertNotIn("os.execv", restart_source)
        self.assertNotIn("subprocess.call", restart_source)
        definitions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("npm_install_runtime_command", definitions)

    def test_pure_codex_config_compatibility_is_reexported_without_wrappers(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        reexports = {
            "toml_string",
            "_codex_config_override_keys",
            "_toml_scalar_without_comment",
            "_unquote_toml_string",
            "codex_alternate_screen_value_from_config_text",
            "codex_config_paths_for_launch",
            "_normalize_codex_mcp_server",
            "_codex_mcp_servers_from_toml_data",
            "_toml_table_parts",
            "_parse_simple_toml_value",
            "_fallback_codex_mcp_servers_from_config_text",
            "codex_mcp_servers_from_config_text",
        }
        definitions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertFalse(reexports & definitions)
        self.assertIn("from ciel_runtime_support.codex_config import (", source)

    def test_visible_stream_state_machines_are_protocol_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        self.assertNotIn("VisibleThinkingMarkupFilter", names)
        self.assertNotIn("VisibleToolCallArtifactFilter", names)
        self.assertNotIn("strip_visible_thinking_markup", names)
        self.assertNotIn("strip_visible_tool_call_artifact_suffix", names)
        self.assertIn("from ciel_runtime_support.visible_stream_filters import (", source)

    def test_concrete_adapters_own_provider_specific_defaults(self):
        common_keys = {
            "base_url",
            "api_key",
            "current_model",
            "advisor_model",
            "custom_models",
        }
        for provider in PROVIDER_ADAPTERS.names():
            with self.subTest(provider=provider):
                defaults = PROVIDER_ADAPTERS.create(provider).default_configuration()
                self.assertTrue(common_keys.issubset(defaults))
                self.assertGreater(len(defaults), len(common_keys))

    def test_model_launch_and_runtime_info_dispatch_through_provider_adapter(self):
        root = Path(__file__).resolve().parents[1]
        checks = (
            (root / "ciel_runtime_support" / "provider_model_selection.py", "launch_id", "launch_model_strategy"),
            (root / "ciel_runtime.py", "provider_runtime_info_service", "runtime_model_info_strategy"),
        )
        for source_path, name, hook in checks:
            source = source_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            function = next(
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name
            )
            function_source = ast.get_source_segment(source, function) or ""
            self.assertIn(hook, function_source)
            self.assertNotIn('provider == "', function_source)
            self.assertNotIn("provider in (", function_source)

    def test_claude_launch_enrichment_dispatches_through_compatibility_registry(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        expected_hooks = {
            "should_attach_web_search": "auto_web_search",
            "should_append_compat_prompt": "requires_compat_prompt",
        }
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in expected_hooks
        }
        for name, hook in expected_hooks.items():
            self.assertIn(hook, functions[name])
            self.assertNotIn('provider == "', functions[name])
            self.assertNotIn("provider in (", functions[name])

    def test_provider_adapter_does_not_own_compatibility_workflows(self):
        forbidden = {
            "advisor_transport_kind",
            "compatibility_failure_diagnosis",
            "known_compatibility_tool_use_blocker",
            "exposes_compatibility_runtime_info",
            "runtime_model_info_strategy",
            "allows_auto_web_search",
            "requires_compat_prompt",
            "compatibility_runtime_metadata_lines",
        }
        self.assertFalse(forbidden & set(ProviderAdapter.__dict__))
        self.assertLessEqual(len(fields(ProviderCompatibilityPolicy)), 10)

    def test_channel_panel_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelPanelPolicy)), 10)

    def test_channel_terminal_proxy_ports_stay_below_dependency_limit(self):
        for port in (
            ChannelTerminalProcess,
            ChannelTerminalIO,
            ChannelTerminalPolicy,
            ChannelTerminalPolling,
            ChannelTerminalServices,
            ChannelWindowsConsole,
            ChannelWindowsServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_terminal_dispatch_uses_bounded_typed_ports(self):
        self.assertEqual(4, len(fields(ChannelTerminalDispatchService)))
        for port in (
            ChannelTerminalDispatchSettings,
            ChannelTerminalProxyPorts,
            ChannelDirectProcessPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 5)
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "subprocess_call_with_channel_wake_proxy"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn(
            "channel_terminal_dispatch_service().dispatch", function_source
        )
        self.assertNotIn("os.name", function_source)
        service_source = (
            root
            / "ciel_runtime_support"
            / "channel_terminal_dispatch.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)

    def test_terminal_platform_io_is_adapter_owned(self):
        self.assertEqual(4, len(fields(TerminalInputModeResetPolicy)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        root_functions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("_terminal_winsize_from_fd", root_functions)
        self.assertNotIn("_apply_pty_winsize", root_functions)
        reset_function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_terminal_input_mode_reset_enabled"
        )
        reset_source = ast.get_source_segment(source, reset_function) or ""
        self.assertIn("terminal_input_mode_reset_policy().enabled", reset_source)
        self.assertNotIn("os.environ", reset_source)
        adapter_source = (
            root / "ciel_runtime_support" / "terminal_platform_io.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", adapter_source)

    def test_windows_console_mode_is_adapter_owned(self):
        self.assertEqual(1, len(fields(WindowsConsoleModeService)))
        self.assertEqual(3, len(fields(WindowsConsoleModePorts)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wrappers = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name
            in {
                "_windows_console_input_mode",
                "_set_windows_console_input_mode",
            }
        }
        self.assertEqual(
            {
                "_windows_console_input_mode",
                "_set_windows_console_input_mode",
            },
            set(wrappers),
        )
        self.assertTrue(
            all("ctypes" not in wrapper for wrapper in wrappers.values())
        )
        adapter_source = (
            root / "ciel_runtime_support" / "windows_console_mode.py"
        ).read_text(encoding="utf-8")
        self.assertIn('ctypes.WinDLL("kernel32"', adapter_source)
        self.assertNotIn("import ciel_runtime", adapter_source)

    def test_channel_session_repository_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelSessionRepository)), 10)

    def test_channel_session_lifecycle_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelSessionLifecycleServices)), 10)

    def test_tool_guard_hook_ports_stay_below_dependency_limit(self):
        for port in (ToolGuardHookPolicy, ToolGuardHookServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(1, len(fields(LegacyToolGuardShimInstaller)))
        self.assertEqual(4, len(fields(LegacyToolGuardShimServices)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "install_legacy_tool_guard_compat_shim"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("LegacyToolGuardShimInstaller", function_source)
        self.assertNotIn(".symlink_to(", function_source)
        installer_source = (
            root / "ciel_runtime_support" / "tool_guard_hooks.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", installer_source)

    def test_settings_repository_ports_stay_below_dependency_limit(self):
        for port in (
            SettingsFileEffects,
            JsonSettingsRepository,
            SecureJsonEffects,
            SecureJsonRepository,
            StatusLineServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_codex_mcp_integration_uses_small_typed_ports(self):
        for port in (
            CodexMcpConfigPorts,
            CodexMcpArtifactPorts,
            CodexMcpCapabilityPorts,
            CodexMcpProjectionPorts,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 5)
        self.assertLessEqual(len(fields(CodexMcpIntegrationService)), 5)

    def test_codex_channel_sse_launch_is_owned_by_typed_service(self):
        self.assertEqual(3, len(fields(CodexChannelSseLaunchService)))
        self.assertEqual(5, len(fields(CodexChannelSseQueryPorts)))
        self.assertEqual(2, len(fields(CodexChannelSseEffects)))

        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "start_codex_mcp_channel_sse_for_launch"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("CodexChannelSseLaunchService", function_source)
        self.assertNotIn("names = [", function_source)
        self.assertNotIn("reason=no_capable_unowned_codex_mcp", function_source)
        service_source = (
            root
            / "ciel_runtime_support"
            / "codex_channel_sse_launch.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)

    def test_codex_session_selection_is_owned_by_a_typed_service(self):
        for port in (CodexSessionRepositoryPorts, CodexSessionPresentationPorts):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 3)
        self.assertEqual(2, len(fields(CodexSessionSelectionService)))

        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "codex_sqlite_home_for_launch",
            "codex_local_resume_sessions",
            "codex_resume_session_row",
            "select_codex_resume_session",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        service_source = (
            root / "ciel_runtime_support" / "codex_session_selection.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)
        self.assertNotIn("__getattr__", service_source)

    def test_codex_launch_configuration_is_owned_by_typed_ports(self):
        ports = (
            CodexLaunchConfigurationConstants,
            CodexLaunchPolicyPorts,
            CodexLaunchModelPorts,
            CodexLaunchCatalogPorts,
            CodexLaunchConfigurationEffects,
        )
        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 5)
        self.assertEqual(5, len(fields(CodexLaunchConfigurationService)))
        self.assertEqual(
            "ciel_runtime_support.codex_launch_configuration",
            build_default_codex_configuration_constants.__module__,
        )
        self.assertEqual(
            "ciel_runtime_support.codex_launch_configuration",
            build_default_codex_launch_policy.__module__,
        )

        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("CodexLaunchConfigurationConstants(", source)
        self.assertNotIn("CodexLaunchPolicyPorts(", source)
        root_functions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "codex_alternate_screen_compat_args",
            "codex_runtime_config_args",
            "write_codex_runtime_model_catalog",
            "codex_runtime_model_catalog_args",
            "codex_native_routed_config_args",
            "codex_passthrough_has_model_override",
            "codex_current_model_cli_args",
            "codex_current_model_config_args",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        service_source = (
            root / "ciel_runtime_support" / "codex_launch_configuration.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)
        self.assertNotIn("__getattr__", service_source)

    def test_mcp_json_artifacts_use_secure_repository(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        target_names = {"write_native_mcp_config_from_discovery"}
        functions = {
            node.name: ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in target_names
        }
        self.assertEqual(target_names, set(functions))
        for name, source in functions.items():
            with self.subTest(function=name):
                self.assertIn("json_artifact_repository", source)
                self.assertNotIn("os.chmod", source)

        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn(
            "write_codex_mcp_config_for_channel_discovery", root_functions
        )
        self.assertIn("CodexMcpIntegrationService", source_path.read_text(encoding="utf-8"))
        integration_source = (
            source_path.parent
            / "ciel_runtime_support"
            / "codex_mcp_integration.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("os.chmod", integration_source)
        self.assertNotIn("import ciel_runtime", integration_source)

        managed_factory = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "managed_mcp_config_service"
        )
        managed_source = ast.unparse(managed_factory)
        self.assertIn("json_artifact_repository", managed_source)
        self.assertNotIn("os.chmod", managed_source)

        proxy_factory = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "mcp_proxy_config_service"
        )
        proxy_source = ast.unparse(proxy_factory)
        self.assertIn("json_artifact_repository", proxy_source)
        self.assertNotIn("os.chmod", proxy_source)

        compact_factory = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "channel_compact_request_repository"
        )
        compact_source = ast.unparse(compact_factory)
        self.assertIn("json_artifact_repository", compact_source)
        self.assertNotIn("os.chmod", compact_source)

        probe_factory = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "channel_probe_service"
        )
        probe_source = ast.unparse(probe_factory)
        self.assertIn("json_artifact_repository", probe_source)
        self.assertNotIn("os.chmod", probe_source)

    def test_mcp_config_readers_delegate_io_and_project_scope(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        target_names = {
            "_read_mcp_server_names_from_json",
            "_read_mcp_servers_from_json",
            "_read_mcp_sse_servers_from_json",
        }
        functions = {
            node.name: ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in target_names
        }
        self.assertEqual(target_names, set(functions))
        for name, source in functions.items():
            with self.subTest(function=name):
                self.assertIn("read_mcp_config_items", source)
                self.assertNotIn("read_text", source)
                self.assertNotIn("json.loads", source)

    def test_claude_mcp_path_discovery_is_policy_owned(self):
        self.assertTrue(hasattr(ClaudeMcpConfigPathPolicy, "paths"))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(
            {
                "_mcp_config_passthrough_values",
                "_mcp_config_paths_from_passthrough",
                "strip_mcp_config_passthrough",
            }.isdisjoint(root_functions)
        )
        for function_name in (
            "claude_mcp_config_paths",
            "existing_claude_mcp_config_paths",
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == function_name
            )
            function_source = ast.get_source_segment(source, function) or ""
            self.assertIn("ClaudeMcpConfigPathPolicy", function_source)
            self.assertNotIn("while True", function_source)
        policy_source = (
            root / "ciel_runtime_support" / "mcp_config_reader.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", policy_source)
        self.assertNotIn("__getattr__", policy_source)

    def test_managed_mcp_config_service_owns_server_projection(self):
        for port in (
            ManagedMcpConfigPaths,
            ManagedMcpConfigPolicy,
            ManagedMcpConfigPorts,
            ManagedMcpConfigService,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in ("write_web_tools_mcp_config", "write_zai_mcp_config", "write_channel_mcp_config"):
            function = next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.unparse(function)
            with self.subTest(function=function_name):
                self.assertIn("managed_mcp_config_service", function_source)
                self.assertNotIn("mcpServers", function_source)
                self.assertNotIn("find_executable", function_source)

    def test_mcp_proxy_config_service_owns_server_materialization(self):
        for port in (McpProxyConfigPaths, McpProxyConfigPorts, McpProxyConfigService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "write_mcp_proxy_config"
        )
        function_source = ast.unparse(function)
        self.assertIn("mcp_proxy_config_service", function_source)
        self.assertNotIn("mcp-proxy", function_source)
        self.assertNotIn("mcpServers", function_source)

    def test_channel_tool_context_service_owns_stateful_projection(self):
        for port in (ChannelToolContextPolicy, ChannelToolContextPorts, ChannelToolContextService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for function_name in (
            "_channel_injected_prompt_text",
            "_remember_channel_injected_tool_use",
            "remember_channel_injected_tool_uses",
            "body_with_channel_tool_result_context",
        ):
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            function_source = ast.unparse(function)
            with self.subTest(function=function_name):
                self.assertIn("channel_tool_context_service", function_source)
                self.assertNotIn("router_log", function_source)
                self.assertNotIn("json.dumps", function_source)

    def test_channel_cursor_recovery_service_owns_transcript_recovery(self):
        for port in (ChannelCursorRecoveryPolicy, ChannelCursorRecoveryPorts, ChannelCursorRecoveryService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_channel_stdin_recover_cursor_from_queued_only"
        )
        function_source = ast.unparse(function)
        self.assertIn("channel_cursor_recovery_service", function_source)
        self.assertNotIn("_read_file_tail_text", function_source)
        self.assertNotIn("_CHANNEL_STDIN_RECOVERY_CACHE", function_source)

    def test_request_trace_ports_stay_below_dependency_limit(self):
        for port in (
            RequestTracePolicy,
            RequestTraceProjection,
            RequestTraceServices,
            ResponseTraceController,
            RouterMessagePreviewPolicy,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_process_control_ports_stay_below_dependency_limit(self):
        for port in (
            ProcessQueryServices,
            ProcessSignalServices,
            ProcessControlServices,
            ProcessInspectionServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_process_inspection_effects_live_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name
            in {
                "_process_command_line",
                "_process_environ_contains",
                "_posix_process_cwd",
                "_untracked_codex_process_pids_for_cwd",
            }
        }
        self.assertEqual(4, len(functions))
        for name, function_source in functions.items():
            with self.subTest(function=name):
                self.assertNotIn("subprocess.run", function_source)
                self.assertNotIn('Path("/proc")', function_source)
                self.assertNotIn("os.readlink", function_source)

    def test_cli_parser_ports_stay_below_dependency_limit(self):
        for port in (
            CliParserLaunch,
            CliParserRuntime,
            CliParserSettings,
            CliParserProvider,
            CliParserModels,
            CliParserServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_model_panel_ports_stay_below_dependency_limit(self):
        for port in (ModelPanelCatalog, ModelPanelPresentation, ModelPanelServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_model_catalog_projection_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ModelCatalogProjectionServices)), 10)

    def test_model_info_projection_has_no_provider_name_branch(self):
        root = Path(__file__).resolve().parents[1]
        source_path = root / "ciel_runtime_support" / "provider_catalog_sources.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        service = next(
            node for node in tree.body if isinstance(node, ast.ClassDef)
            and node.name == "ProviderCatalogSourceService"
        )
        function = next(
            node for node in service.body if isinstance(node, ast.FunctionDef)
            and node.name == "model_info_from_response"
        )
        provider_comparisons = [
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(item, ast.Name) and item.id == "provider"
                for item in (node.left, *node.comparators)
            )
        ]
        self.assertEqual([], provider_comparisons)

        main_tree = ast.parse((root / "ciel_runtime.py").read_text(encoding="utf-8"))
        root_functions = {
            node.name for node in main_tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "model_ids_from_response",
            "model_info_from_response",
            "fireworks_account_id",
            "fireworks_management_base_url",
            "fetch_fireworks_model_ids",
            "fetch_text_url",
            "anthropic_model_ids_from_docs_text",
            "filter_anthropic_default_model_ids",
            "fetch_anthropic_public_model_ids",
            "fetch_anthropic_api_model_ids",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))

    def test_provider_catalog_sources_use_small_typed_ports(self):
        ports = (
            CatalogSourceProjectionPorts,
            ProviderCatalogHttpPorts,
            ProviderCatalogPolicyPorts,
            AnthropicCatalogPolicy,
            FireworksCatalogPolicy,
        )
        for port in ports:
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 5)
        self.assertEqual(5, len(fields(ProviderCatalogSourceService)))

    def test_provider_default_policy_factories_live_outside_the_facade(self):
        catalog = build_default_provider_catalog_source_service
        endpoint = build_default_provider_endpoint_policy
        self.assertEqual(
            "ciel_runtime_support.provider_catalog_sources",
            catalog.__module__,
        )
        self.assertEqual(
            "ciel_runtime_support.provider_endpoint_policy",
            endpoint.__module__,
        )
        source = (
            Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("anthropic=provider_catalog_sources.AnthropicCatalogPolicy", source)
        self.assertNotIn("presentation=ModelEndpointPresentation", source)

    def test_lm_studio_runtime_port_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(LmStudioRuntimeServices)), 10)

    def test_nvidia_proxy_lifecycle_lives_in_provider_runtime(self):
        for port in (
            NvidiaProxyRuntimeConfig,
            NvidiaProxyRuntimePorts,
            NvidiaProxyRuntime,
            NvidiaProxyStopPorts,
            NvidiaProxyStopper,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        root_functions = {
            node.name
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("ensure_ncp", root_functions)
        self.assertIn("ensure_ncp = _NVIDIA_RUNTIME_API.ensure", source)
        runtime_source = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "providers"
            / "nvidia_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class NvidiaRuntimeApi:", runtime_source)
        self.assertIn("class NvidiaProxyStopper:", runtime_source)
        self.assertIn("subprocess.Popen", runtime_source)

        stop_function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "stop_ncp_proxy"
        )
        stop_source = ast.unparse(stop_function)
        self.assertIn("NvidiaProxyStopper", stop_source)
        self.assertNotIn("if os.name", stop_source)

    def test_managed_service_cleanup_is_provider_independent(self):
        self.assertLessEqual(len(fields(ManagedServiceCleanupPorts)), 8)
        self.assertEqual(1, len(fields(ManagedServiceCleanupPolicy)))

        root = Path(__file__).resolve().parents[1]
        policy_source = (
            root / "ciel_runtime_support" / "managed_service_cleanup.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('provider == "', policy_source)
        self.assertNotIn("provider in (", policy_source)

        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "cleanup_managed_services_for_provider"
        )
        function_source = ast.unparse(function)
        self.assertIn("ManagedServiceCleanupPolicy", function_source)
        self.assertNotIn("direct_native_anthropic_enabled(provider", function_source)

    def test_provider_runtime_info_service_owns_catalog_projection(self):
        self.assertLessEqual(len(fields(ProviderRuntimeInfoPorts)), 10)
        self.assertLessEqual(len(fields(ProviderRuntimeInfoService)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "upstream_model_runtime_info"
        )
        function_source = ast.unparse(function)
        self.assertIn("provider_runtime_info_service", function_source)
        self.assertNotIn("http_json", function_source)
        self.assertNotIn("/v1/models", function_source)

    def test_lm_studio_context_guard_lives_in_provider_runtime(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "lm_studio_v1_model_info",
            "lm_studio_loaded_instance_ids",
            "lm_studio_target_context",
            "lm_studio_load_timeout_seconds",
            "lm_studio_load_model",
            "lm_studio_unload_loaded_instances",
            "lm_studio_load_response_context",
            "ensure_lm_studio_model_loaded_for_context",
            "apply_lm_studio_loaded_context_guard",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("LmStudioLifecycleApi", source)
        runtime_source = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "lm_studio_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", runtime_source)

    def test_timeout_and_model_selection_use_explicit_typed_apis(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "llm_preset_timeout_ms",
            "active_llm_preset_timeout_ms",
            "timeout_profile_id_for_ms",
            "timeout_profile_text",
            "timeout_profile_status",
            "timeout_profile_idle_ms",
            "timeout_profile_panel_rows",
            "apply_timeout_profile_to_provider",
            "with_preset_timeout_tokens",
            "current_upstream_model_id",
            "provider_placeholder_model_ids",
            "current_model_needs_provider_selection",
            "ensure_current_model_from_provider_list",
            "launch_model_id",
            "resolve_requested_model",
            "resolve_tool_model_references",
            "list_model_objects",
            "list_model_objects_for_request",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("TimeoutProfileApi", source)
        self.assertIn("ProviderModelSelectionApi", source)
        for relative_path in (
            Path("ciel_runtime_support/timeout_profile.py"),
            Path("ciel_runtime_support/provider_model_selection.py"),
        ):
            service_source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("__getattr__", service_source)

    def test_provider_contract_registry_and_nvidia_use_explicit_typed_apis(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "provider_endpoint",
            "provider_model_paths",
            "provider_request_policy",
            "provider_model_catalog_policy",
            "preserves_anthropic_thinking_contract",
            "context_compaction_available",
            "provider_context_policy",
            "provider_configuration_policy",
            "provider_model_panel_badge",
            "provider_advisor_panel_notice",
            "provider_advisor_model_badge",
            "read_model_registry",
            "read_model_registry_models",
            "read_model_registry_info",
            "write_model_registry",
            "read_model_list_cache",
            "read_model_info_cache",
            "write_model_list_cache",
            "nvidia_upstream_base_url",
            "nvidia_proxy_base_url",
            "nvidia_api_key",
            "install_ncp_proxy",
            "ncp_module_available",
            "ncp_proxy_executable",
            "ensure_ncp",
            "ncp_model_id_for_nvidia_hosted",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        for api_name in (
            "ProviderContractProjectionApi",
            "ModelRegistryApi",
            "NvidiaRuntimeApi",
        ):
            self.assertIn(api_name, source)
        for relative_path in (
            Path("ciel_runtime_support/provider_contract_projection.py"),
            Path("ciel_runtime_support/model_registry_repository.py"),
            Path("ciel_runtime_support/providers/nvidia_runtime.py"),
        ):
            service_source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("__getattr__", service_source)

    def test_mcp_proxy_codec_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(McpProxyCodecPolicy)), 10)

    def test_managed_mcp_discovery_is_service_owned(self):
        self.assertEqual(3, len(fields(ManagedMcpDiscoveryService)))
        self.assertEqual(2, len(fields(ManagedMcpDiscoveryPaths)))
        self.assertEqual(3, len(fields(ManagedMcpDiscoveryPorts)))
        self.assertEqual(2, len(fields(NativeMcpConfigWriter)))
        self.assertLessEqual(len(fields(NativeMcpConfigWriterPorts)), 10)
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name
            == "discovered_ciel_runtime_managed_mcp_servers"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("ManagedMcpDiscoveryService", function_source)
        self.assertNotIn("proxy_data", function_source)
        service_source = (
            root
            / "ciel_runtime_support"
            / "managed_mcp_discovery.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)

    def test_mcp_stdio_proxy_uses_bounded_typed_ports(self):
        self.assertEqual(3, len(fields(McpStdioProxyService)))
        for port in (
            McpStdioConfigPorts,
            McpStdioTransportPorts,
            McpStdioEffects,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 7)

    def test_mcp_http_proxy_ports_stay_below_dependency_limit(self):
        for port in (
            McpHttpProxyCodec,
            McpHttpProxyTransport,
            McpHttpProxyRuntime,
            McpHttpProxyServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_mcp_split_proxy_http_adapter_owns_transport_flow(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(fields(McpSplitProxyHttpPorts)), 10)
        self.assertNotIn("def _forward_codex_mcp_split_proxy_sse(", source)
        self.assertNotIn("urllib.request.Request(upstream_url", source)
        self.assertIn("McpSplitProxyHttpAdapter", source)

    def test_critical_mcp_and_process_paths_do_not_silence_exceptions(self):
        source_root = Path(__file__).resolve().parents[1]
        source_paths = (
            source_root / "ciel_runtime.py",
            source_root / "ciel_runtime_support" / "mcp_http_proxy.py",
            source_root / "ciel_runtime_support" / "mcp_proxy_process.py",
            source_root / "ciel_runtime_support" / "claude_router.py",
            source_root / "ciel_runtime_support" / "openai_responses_router.py",
            source_root / "ciel_runtime_support" / "channel_terminal_proxy.py",
            source_root / "ciel_runtime_support" / "process_control.py",
        )
        critical_names = {
            "subprocess_call_with_channel_wake_proxy",
            "_subprocess_call_capturing_stderr",
            "_write_codex_child_process_record",
            "_release_codex_child_process_record",
            "_terminate_recorded_child_process",
            "record_outgoing_sse_event",
            "finish_outgoing_sse_trace",
            "begin_pending_channel_delivery",
            "mark_pending_channel_delivery_success",
            "mark_pending_channel_delivery_failed",
            "run_posix_channel_terminal_proxy",
            "run_windows_channel_terminal_proxy",
            "terminate_matching_processes",
            "_mcp_proxy_forward_stdin",
            "_mcp_proxy_forward_stdin_jsonl",
            "_mcp_proxy_emit_jsonl_stdout_line",
            "_mcp_proxy_forward_stderr",
            "run_mcp_streamable_http_proxy",
            "run_mcp_stdio_proxy",
            "handle_claude_messages_post",
            "handle_openai_responses_request",
        }
        critical_functions = []
        for source_path in source_paths:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            critical_functions.extend(
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in critical_names
            )

        self.assertEqual({node.name for node in critical_functions}, critical_names)
        for function in critical_functions:
            silent_handlers = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.ExceptHandler)
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ]
            with self.subTest(function=function.name):
                self.assertEqual([], silent_handlers)

    def test_all_named_mcp_channel_process_paths_have_no_broad_silent_fallback(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        critical_tokens = ("mcp", "channel", "process", "subprocess", "proxy", "sse")
        violations = []
        for function in (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(token in node.name for token in critical_tokens)
        ):
            for handler in (node for node in ast.walk(function) if isinstance(node, ast.ExceptHandler)):
                broad = handler.type is None or (
                    isinstance(handler.type, ast.Name)
                    and handler.type.id in {"Exception", "BaseException"}
                )
                silent = len(handler.body) == 1 and isinstance(
                    handler.body[0],
                    (ast.Pass, ast.Continue, ast.Return),
                )
                if broad and silent:
                    violations.append((function.name, handler.lineno))
        self.assertEqual([], violations)

    def test_named_registries_produce_real_contract_implementations(self):
        protocol = PROTOCOL_ADAPTERS.create("openai-responses", fallback_model="fallback")
        provider = PROVIDER_ADAPTERS.create("openrouter")
        runtime = RUNTIME_ADAPTERS.create("codex", executable="codex")
        dialect = TOOL_DIALECTS.create("claude-code", available_tools={"WebSearch"})

        self.assertIsInstance(protocol, OpenAIResponsesProtocolAdapter)
        self.assertIsInstance(provider, HttpBearerProviderAdapter)
        self.assertIsInstance(runtime, CodexRuntimeAdapter)
        self.assertIsInstance(dialect, ClaudeToolDialect)
        self.assertEqual("WebSearch", dialect.normalize_tool_name("web_search"))

    def test_runtime_command_factory_owns_normalized_launch_spec_creation(self):
        self.assertLessEqual(len(fields(RuntimeCommandFactoryPorts)), 10)
        self.assertLessEqual(len(fields(RuntimeCommandFactory)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "materialize_runtime_command"
        )
        function_source = ast.unparse(function)
        self.assertIn("runtime_command_factory", function_source)
        self.assertNotIn("LaunchSpec", function_source)
        self.assertNotIn("ProviderConfig", function_source)
        self.assertNotIn("RUNTIME_ADAPTERS", function_source)

    def test_registry_rejects_duplicate_and_unknown_names(self):
        registry: AdapterRegistry[object] = AdapterRegistry()
        registry.register("one", object)

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register("one", object)
        with self.assertRaisesRegex(KeyError, "unknown adapter"):
            registry.create("missing")

    def test_each_configurable_provider_has_a_concrete_adapter(self):
        expected = {
            "agy": AgyProviderAdapter,
            "anthropic": AnthropicProviderAdapter,
            "codex": CodexProviderAdapter,
            "deepseek": DeepSeekProviderAdapter,
            "fireworks": FireworksProviderAdapter,
            "kimi": KimiProviderAdapter,
            "lm-studio": LMStudioProviderAdapter,
            "nvidia-hosted": NvidiaHostedProviderAdapter,
            "ollama": OllamaProviderAdapter,
            "ollama-cloud": OllamaCloudProviderAdapter,
            "opencode": OpenCodeProviderAdapter,
            "opencode-go": OpenCodeGoProviderAdapter,
            "openrouter": OpenRouterProviderAdapter,
            "self-hosted-nim": SelfHostedNimProviderAdapter,
            "vllm": VllmProviderAdapter,
            "zai": ZaiProviderAdapter,
        }
        for provider, adapter_type in expected.items():
            with self.subTest(provider=provider):
                self.assertIsInstance(PROVIDER_ADAPTERS.create(provider), adapter_type)

    def test_provider_registry_does_not_define_concrete_provider_adapters(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "ciel_runtime_support"
            / "provider_adapters.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        concrete_adapters = [
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.endswith("ProviderAdapter")
        ]

        self.assertEqual([], concrete_adapters)

    def test_npm_package_includes_concrete_provider_modules(self):
        package_path = Path(__file__).resolve().parents[1] / "package.json"
        package_files = json.loads(package_path.read_text(encoding="utf-8"))["files"]

        self.assertIn("ciel_runtime_support/providers/*.py", package_files)

    def test_provider_adapters_own_protocol_endpoints_and_model_paths(self):
        ollama = PROVIDER_ADAPTERS.create("ollama")
        ollama_config = ProviderConfig(name="ollama", base_url="http://localhost:11434", model="qwen")
        openrouter = PROVIDER_ADAPTERS.create("openrouter")
        openrouter_config = ProviderConfig(name="openrouter", base_url="https://openrouter.ai/api/v1", model="model")

        self.assertEqual("ollama_chat", ollama.capabilities(ollama_config).upstream_protocol)
        self.assertEqual("/api/chat", ollama.resolve_endpoint("chat", ollama_config))
        self.assertEqual(("/api/tags", "/v1/models"), ollama.model_paths(ollama_config))
        self.assertEqual("openai_chat", openrouter.capabilities(openrouter_config).upstream_protocol)
        self.assertEqual("/v1/chat/completions", openrouter.resolve_endpoint("chat", openrouter_config))

    def test_provider_adapters_own_model_panel_annotations(self):
        opencode = PROVIDER_ADAPTERS.create("opencode")
        opencode_config = ProviderConfig(
            name="opencode",
            base_url="https://opencode.ai/zen/v1",
            model="gpt-5",
            options={"model_endpoints": {"gpt-5": "chat"}},
        )
        anthropic = PROVIDER_ADAPTERS.create("anthropic")
        anthropic_config = ProviderConfig(
            name="anthropic", base_url="https://api.anthropic.com", model="claude-sonnet-4-6"
        )
        deepseek = PROVIDER_ADAPTERS.create("deepseek")
        deepseek_config = ProviderConfig(
            name="deepseek", base_url="https://api.deepseek.com", model="deepseek-v4-pro"
        )
        openrouter = PROVIDER_ADAPTERS.create("openrouter")
        openrouter_config = ProviderConfig(
            name="openrouter", base_url="https://openrouter.ai/api/v1", model="model"
        )

        self.assertEqual("chat override", opencode.model_panel_badge(opencode_config, "gpt-5"))
        self.assertIsNotNone(anthropic.advisor_panel_notice(anthropic_config))
        self.assertEqual(
            "recommended for long context",
            deepseek.advisor_model_badge(deepseek_config, "deepseek-v4-pro"),
        )
        self.assertEqual("", openrouter.model_panel_badge(openrouter_config, "model"))

    def test_provider_adapters_own_configuration_mutation_capabilities(self):
        cases = {
            "anthropic": {"supports_route_through_router": True},
            "fireworks": {"text_option": "account_id"},
            "nvidia-hosted": {"native_error": True},
            "ollama": {"mutation_strategy": "ollama"},
            "ollama-cloud": {"mutation_strategy": "ollama"},
            "opencode": {"supports_model_endpoint_overrides": True},
            "opencode-go": {"supports_model_endpoint_overrides": True},
        }
        for provider, expected in cases.items():
            adapter = PROVIDER_ADAPTERS.create(provider)
            config = ProviderConfig(name=provider, base_url=adapter.default_base_url(), model="model")
            policy = adapter.configuration_policy(config)
            with self.subTest(provider=provider):
                self.assertIsInstance(policy, ProviderConfigurationPolicy)
                for key, value in expected.items():
                    if key == "text_option":
                        self.assertEqual(value, policy.text_option_aliases["account"])
                    elif key == "native_error":
                        self.assertEqual(value, bool(policy.native_compat_error))
                    else:
                        self.assertEqual(value, getattr(policy, key))

    def test_openai_responses_adapter_normalizes_both_directions(self):
        adapter = PROTOCOL_ADAPTERS.create("openai_responses", fallback_model="fallback")
        anthropic = adapter.normalize_request({"input": "hello", "stream": False})
        response = adapter.normalize_response(
            {"model": "fallback", "content": [{"type": "text", "text": "world"}], "usage": {}}
        )

        self.assertEqual("fallback", anthropic["model"])
        self.assertEqual("hello", anthropic["messages"][0]["content"][0]["text"])
        self.assertEqual("response", response["object"])

    def test_runtime_specific_adapters_own_cli_syntax(self):
        provider = ProviderConfig(name="test", base_url="http://localhost", model="model")
        claude_spec = LaunchSpec(
            runtime=RuntimeConfig(
                name="claude",
                executable="claude",
                options={"bypass_permission_mode": True, "model": "alias", "extra_args": ("--debug",)},
            ),
            provider=provider,
            mode="routed",
            protocol="anthropic_messages",
            passthrough=("prompt",),
        )
        adapter = RUNTIME_ADAPTERS.create("claude", executable="claude")

        command = adapter.build_command(claude_spec)

        self.assertIsInstance(adapter, ClaudeRuntimeAdapter)
        self.assertEqual(
            ("claude", "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions", "--model", "alias", "--debug", "prompt"),
            command.argv,
        )

    def test_main_composition_root_has_no_globals_service_locator_or_legacy_protocol_copy(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")

        self.assertNotIn("runtime_deps=globals()", source)
        self.assertNotIn("build_claude_router_dependencies", source)
        self.assertNotIn("class RouterHandler(BaseHTTPRequestHandler)", source)
        self.assertNotIn("def _legacy_openai_responses_to_anthropic_messages", source)
        self.assertNotIn("def _legacy_anthropic_message_to_openai_response", source)

    def test_channel_transcript_parsers_live_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_channel_transcript_content_text",
            "_channel_transcript_user_text",
            "_channel_transcript_is_assistant_message",
            "_channel_stdin_active_tool_call_from_text",
            "_channel_stdin_active_turn_from_text",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)
        self.assertLessEqual(len(fields(ChannelWakeTranscriptServices)), 10)
        self.assertLessEqual(len(fields(ChannelWakeStateReaderPorts)), 10)

    def test_channel_transcript_filesystem_is_repository_owned(self):
        self.assertEqual(4, len(fields(ChannelTranscriptRepository)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        self.assertIn(
            "ChannelTranscriptRepository",
            functions["channel_transcript_repository"],
        )
        self.assertNotIn(
            ".glob(",
            functions["_latest_claude_transcript_path"],
        )
        self.assertNotIn("def _read_file_tail_text(", source)
        repository_source = (
            root
            / "ciel_runtime_support"
            / "channel_transcript_repository.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", repository_source)
        self.assertNotIn("__getattr__", repository_source)

    def test_channel_message_coalescing_policy_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_as_string_list",
            "_channel_message_meta_sources",
            "_channel_message_delivery_targets",
            "_channel_message_has_external_provenance",
            "_channel_message_has_unique_reference",
            "_channel_message_order_value",
            "_channel_message_coalesce_key",
            "_channel_superseded_message_ids",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)

    def test_channel_message_dedupe_is_service_owned(self):
        self.assertEqual(2, len(fields(ChannelMessageDedupeService)))
        self.assertEqual(6, len(fields(ChannelMessageDedupePorts)))
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_chat_message_duplicate_locked"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("ChannelMessageDedupeService", function_source)
        self.assertNotIn("for row in", function_source)
        service_source = (
            root
            / "ciel_runtime_support"
            / "channel_message_dedupe.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", service_source)
        self.assertNotIn("__getattr__", service_source)

    def test_anthropic_content_projection_has_one_protocol_owner(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("def anthropic_content_to_text(", source)
        self.assertIn(
            "content_to_text as anthropic_content_to_text",
            source,
        )
        ollama_source = (
            root
            / "ciel_runtime_support"
            / "protocols"
            / "ollama_chat.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def _anthropic_content_to_text(", ollama_source)
        codec_source = (
            root
            / "ciel_runtime_support"
            / "protocols"
            / "anthropic_content.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import ciel_runtime", codec_source)

    def test_channel_message_prompt_policy_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_channel_prompt_scalar",
            "_channel_prompt_metadata",
            "_channel_wake_message_noise_reason",
            "_channel_llm_message_skip_reason",
            "_channel_message_llm_display_text",
            "_channel_message_source_header",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)

    def test_channel_event_identity_policy_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_chat_message_payload_hash",
            "_channel_event_identity_room_key",
            "_channel_message_event_identity_key",
            "_chat_message_stable_dedupe_key",
            "_chat_message_fallback_dedupe_key",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)

    def test_channel_message_repository_owns_jsonl_scanning(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("def _chat_message_epoch_seconds(", source)
        self.assertNotIn("def _message_visible_to(", source)
        self.assertNotIn("def _chat_message_matches(", source)
        self.assertLessEqual(len(fields(ChannelMessageRepository)), 10)
        self.assertLessEqual(len(fields(ChannelMessageAppendPorts)), 10)
        self.assertNotIn('with CHAT_MESSAGES_PATH.open("a"', source)

    def test_channel_backlog_service_owns_multi_cursor_transaction(self):
        for port in (ChannelBacklogCursors, ChannelBacklogRuntime, ChannelBacklogService):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        clear_function = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "clear_channel_backlog"
        )
        clear_source = ast.unparse(clear_function)
        self.assertIn("channel_backlog_service", clear_source)
        self.assertNotIn("global", clear_source)
        self.assertNotIn("_CHANNEL_MCP_SESSIONS", clear_source)

    def test_channel_wake_claim_repository_owns_cross_process_claims(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_channel_prompt_match_text",
            "_channel_prompt_contains",
            "_channel_stdin_wake_claims_read_locked",
            "_channel_stdin_wake_claims_write_locked",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)
        self.assertLessEqual(len(fields(ChannelWakeClaimRepository)), 10)

    def test_channel_wake_delivery_repository_owns_in_memory_transitions(self):
        self.assertLessEqual(len(fields(ChannelWakeDeliveryRepository)), 10)
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("len(_CHANNEL_STDIN_WAKE_DELIVERED)", source)
        self.assertNotIn("len(_CHANNEL_STDIN_WAKE_PROMPTS)", source)

    def test_channel_cursor_policy_owns_monotonic_state_merge(self):
        self.assertLessEqual(len(fields(CursorReadResolution)), 10)
        self.assertTrue(callable(ChannelCursorStatePolicy.resolve_read))
        self.assertTrue(callable(ChannelCursorStatePolicy.newer))

    def test_auto_llm_options_service_owns_configuration_transaction(self):
        for port in (
            AutoLlmOptionsRepository,
            AutoLlmModelPolicy,
            AutoLlmPresetPolicy,
            AutoLlmOptionsService,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_provider_context_status_projection_owns_formatting_policy(self):
        self.assertLessEqual(len(fields(ProviderContextStatusPorts)), 10)
        self.assertLessEqual(len(fields(ProviderContextStatusProjection)), 10)

    def test_router_server_runtime_owns_serve_lifecycle(self):
        for port in (
            RouterServerConfig,
            RouterServerStatePorts,
            RouterServerEffects,
            RouterServerRuntime,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_channel_terminal_input_policy_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
            "_channel_platform_default_enter_bytes",
            "_channel_enter_bytes_from_user_input",
            "_channel_synthetic_enter_bytes_from_user_input",
            "_channel_enter_label",
            "_channel_wake_submit_delay_seconds",
            "_channel_wake_submit_retry_delay_seconds",
        ):
            with self.subTest(function=function_name):
                self.assertNotIn(f"def {function_name}(", source)
        self.assertNotIn("kernel32.CreateFileW", source)
        self.assertIn("windows_console_input_handle as _resolve_windows_console_input_handle", source)

    def test_channel_launch_guard_repository_owns_persistence(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("CHANNEL_LLM_LAUNCH_GUARD_PATH.read_text", source)
        self.assertNotIn("CHANNEL_LLM_LAUNCH_GUARD_PATH.with_suffix", source)
        self.assertLessEqual(len(fields(ChannelLaunchGuardRepository)), 10)

    def test_channel_launch_policy_owns_launch_decisions(self):
        self.assertEqual(2, len(fields(ChannelLaunchPolicy)))
        self.assertEqual(4, len(fields(ChannelLaunchPorts)))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "native_channel_passthrough_requested",
            "claude_channel_args",
            "should_use_native_channel_bridge",
            "should_use_channel_llm_delivery",
            "channel_specs_include_external_server",
            "claude_code_channels_auth_available",
            "should_use_channel_stdin_proxy",
        ):
            function_source = ast.unparse(functions[name])
            self.assertIn("channel_launch_policy", function_source)
            self.assertNotIn("subprocess.run", function_source)

    def test_channel_runtime_environment_policy_owns_threshold_parsing(self):
        self.assertEqual(3, len(fields(ChannelRuntimeEnvironmentPolicy)))

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        functions = {
            node.name: node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "_channel_launch_recent_seconds",
            "channel_probe_default_timeout",
            "_channel_pending_scan_limit",
            "_channel_stdin_wake_batch_limit",
            "_channel_stdin_wake_claim_ttl_seconds",
            "_channel_stdin_unseen_retry_seconds",
            "_channel_stdin_inflight_stale_seconds",
            "_codex_channel_wake_submit_retries",
            "_codex_channel_wake_submit_delay_seconds",
            "_windows_channel_startup_grace_seconds",
        ):
            function_source = ast.unparse(functions[name])
            self.assertIn("channel_runtime_environment_policy", function_source)
            self.assertNotIn("os.environ", function_source)

    def test_channel_cursor_repository_owns_atomic_cursor_persistence(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for path_name in (
            "CHANNEL_MCP_CURSOR_PATH",
            "CHANNEL_LLM_CURSOR_PATH",
            "CHANNEL_LLM_CLEAR_FLOOR_PATH",
        ):
            with self.subTest(path=path_name):
                self.assertNotIn(f"{path_name}.read_text", source)
                self.assertNotIn(f"{path_name}.with_suffix", source)
        self.assertLessEqual(len(fields(ChannelCursorRepository)), 10)

    def test_config_value_codec_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(
            {"positive_int", "finite_float", "parse_config_value", "parse_bool"}.isdisjoint(
                root_functions
            )
        )
        self.assertIn("from ciel_runtime_support.config_value_codec import (", source)

    def test_openai_reasoning_policy_uses_provider_adapter_strategy(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(
            {
                "anthropic_tool_choice_to_openai",
                "opencode_model_id_hint",
                "openai_chat_reasoning_passback_enabled",
                "openai_chat_reasoning_passback_enabled_for_body",
                "openai_reasoning_to_anthropic_thinking_block",
                "should_omit_openai_chat_tool_choice",
            }.isdisjoint(root_functions)
        )
        policy_source = (
            root / "ciel_runtime_support" / "protocols" / "openai_reasoning.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("OPENCODE_PROVIDER_NAMES", policy_source)
        self.assertIn("adapter.openai_reasoning_passback_enabled", policy_source)

    def test_router_access_policy_and_token_repository_live_outside_root(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertTrue(
            {
                "router_debug_external_access_enabled",
                "router_bind_host",
                "is_loopback_address",
                "router_external_access_token",
                "ensure_router_external_access_token",
                "router_request_bearer_token",
            }.isdisjoint(root_functions)
        )
        self.assertNotIn("hmac.compare_digest", source)
        self.assertNotIn("ROUTER_EXTERNAL_TOKEN_PATH.read_text", source)
        self.assertIn("from ciel_runtime_support.router_access import (", source)
        self.assertEqual(2, len(fields(RouterAccessHttpController)))

    def test_web_ui_controller_uses_small_typed_ports(self):
        for port in (
            WebUiConstants,
            WebUiProjectionPorts,
            WebUiDisplayPorts,
            WebUiHttpPorts,
            WebUiController,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)
        self.assertEqual(8, len(fields(RouterHealthPolicy)))
        self.assertEqual(1, len(fields(UltracodeSessionPolicy)))
        self.assertEqual(8, len(fields(ProviderToolPolicy)))

    def test_ollama_context_and_output_budget_policies_live_outside_root(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        extracted = {
            "ctx_bucket",
            "ollama_provider_context_limit",
            "ollama_preserve_configured_context_cap",
            "ollama_effective_context_limit",
            "ollama_num_ctx_for_payload",
            "ollama_num_ctx_status",
            "ollama_extra_options",
            "ollama_options_status",
            "ollama_request_timeout_seconds",
            "ollama_context_error_limit",
            "ollama_context_retry_config",
            "configured_output_tokens",
            "cap_output_tokens_for_context",
            "context_guard_reserve_tokens",
            "ollama_context_limit_for_budget",
        }
        self.assertTrue(extracted.isdisjoint(root_functions))
        self.assertIn("OllamaRequestContextPolicy", source)
        self.assertIn("OutputBudgetPolicy", source)

    def test_router_shortcut_workflows_live_outside_composition_root(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("Router restart scheduled so the bind address changes immediately.", source)
        self.assertNotIn("Ciel Runtime channel backlog discarded.", source)
        self.assertNotIn("live LLM options updated from slash command", source)
        self.assertIn("from ciel_runtime_support.router_shortcuts import (", source)
        tree = ast.parse(source)
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "maybe_handle_router_debug_request",
            "maybe_handle_version_request",
            "maybe_handle_channel_clear_request",
            "maybe_handle_live_llm_options_request",
            "maybe_handle_live_api_keys_request",
        ):
            with self.subTest(function=name):
                self.assertLessEqual(functions[name].end_lineno - functions[name].lineno + 1, 2)

    def test_import_and_advisor_http_controllers_live_outside_root(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("msg_ciel_runtime_import_", source)
        self.assertNotIn("Advisor returned no text.", source)
        self.assertIn("ImportSessionHttpController", source)
        self.assertIn("AdvisorShortcutController", source)
        tree = ast.parse(source)
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn(
            'provider == "anthropic"',
            ast.unparse(functions["maybe_handle_advisor_request"]),
        )
        self.assertLessEqual(
            functions["maybe_handle_advisor_request"].end_lineno
            - functions["maybe_handle_advisor_request"].lineno
            + 1,
            2,
        )
        self.assertIn(
            "adapter.intercepts_advisor_shortcut",
            ast.unparse(functions["advisor_shortcut_intercept_enabled"]),
        )

    def test_conversation_turn_compatibility_uses_explicit_typed_adapter(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "plan_mode_active",
            "channel_llm_wake_text",
            "channel_llm_wake_request",
            "body_without_channel_llm_wake_prompt",
            "has_plan_mode_exit",
            "latest_user_text",
            "should_auto_enter_plan_mode",
            "latest_user_tool_result_details",
            "should_keep_work_alive_with_tasklist",
            "empty_end_turn_notice",
            "empty_end_turn_notice_for_body",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("ConversationTurnCompatibilityApi", source)
        adapter_source = (
            root
            / "ciel_runtime_support"
            / "protocols"
            / "conversation_turn_policy.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", adapter_source)

    def test_context_summary_compatibility_uses_explicit_typed_adapter(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "is_claude_code_compact_request",
            "compact_request_text_only_body",
            "compact_tool_value_for_prompt",
            "tool_input_for_prompt",
            "compact_message_text_for_prompt",
            "compact_message_summary_line",
            "context_guard_chunk_count",
            "build_chunked_context_guard_summary",
            "context_compact_message_text",
            "context_compact_instruction_index",
            "context_compact_chunk_target_tokens",
            "context_compact_summary_output_tokens",
            "split_messages_for_context_compact",
            "build_context_compact_chunk_prompt",
            "context_compact_extract_text",
            "build_context_compact_reduce_prompt",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("ContextSummaryCompatibilityApi", source)
        adapter_source = (
            root / "ciel_runtime_support" / "context_summary_policy.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", adapter_source)

    def test_channel_probe_compatibility_keeps_launch_workflow_in_composition(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "_builtin_router_probe_record",
            "_server_transport_label",
            "_probe_mcp_servers_to_records",
            "read_channel_probe_cache",
            "_write_channel_probe_cache",
            "refresh_channel_probe_cache",
            "cached_channel_probe_servers",
            "channel_probe_record_bucket",
            "cached_channel_capable_server_names",
            "cached_external_channel_capable_server_names",
            "cached_channel_source_paths_for_specs",
            "_server_names_from_channel_specs",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("ChannelProbeCompatibilityApi", source)
        for composition_function in (
            "channel_candidate_server_names_for_launch",
            "channel_probe_cache_needs_launch_refresh",
            "ensure_channel_probe_cache_for_launch",
        ):
            self.assertIn(composition_function, root_functions)
        adapter_source = (
            root / "ciel_runtime_support" / "channel_probe_cache.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", adapter_source)

    def test_protocol_and_ollama_runtime_use_explicit_typed_adapters(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "compatibility_tool_schema",
            "compatibility_text_request",
            "compatibility_tool_request",
            "compatibility_tool_result_request",
            "response_content_blocks",
            "response_content_types",
            "response_text_preview",
            "find_compat_tool_use",
            "summarize_compat_response",
            "compatibility_http_error_message",
            "ollama_api_base",
            "ollama_provider_api_base",
            "ollama_show_parameters",
            "fetch_ollama_api_model_specs",
            "ollama_model_id_matches",
            "ollama_runtime_info",
            "ollama_output_cap_for_context",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("CompatibilityProtocolApi", source)
        self.assertIn("OllamaRuntimeApi", source)
        self.assertIn("apply_ollama_runtime_output_guard", root_functions)
        for relative_path in (
            Path("ciel_runtime_support/compatibility_protocol.py"),
            Path("ciel_runtime_support/providers/ollama_runtime.py"),
        ):
            adapter_source = (root / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("__getattr__", adapter_source)

    def test_network_exports_and_logging_api_remove_redundant_wrappers(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "upstream_user_agent",
            "with_upstream_user_agent",
            "normalize_ip_family",
            "default_provider_ip_family",
            "provider_ip_family",
            "socket_getaddrinfo_ip_family_policy",
            "ip_family_connectivity",
            "current_log_level",
            "reset_log_level_cache",
            "log_level_name",
            "log_level_source",
            "log_level_status",
            "normalize_log_level",
            "set_log_level_config",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("LogLevelApi", source)
        logging_source = (
            root / "ciel_runtime_support" / "runtime_logging.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", logging_source)
        for composition_function in (
            "provider_urlopen",
            "provider_ip_family_probe_lines",
            "log_level_repository",
            "router_log",
        ):
            self.assertIn(composition_function, root_functions)

    def test_channel_config_and_ollama_catalog_exports_are_explicit(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "ollama_library_model_parts",
            "context_label_to_tokens",
            "ollama_model_catalog_key",
            "context_tokens_from_ollama_snippet",
            "parse_ollama_library_context_map",
            "parse_ollama_library_context_limit",
            "ollama_context_model_matches",
            "parse_passthrough_channel_specs",
            "auto_import_passthrough_channels",
            "channel_specs_for_launch",
            "is_channel_spec_tagged",
            "normalize_channel_passthrough",
            "normalize_channel_delivery",
            "channel_delivery_mode",
            "set_channel_delivery_config",
            "add_channel_spec",
            "remove_channel_spec",
            "clear_channel_specs",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("ChannelConfigApi", source)
        self.assertIn(
            "normalize_channel_passthrough = "
            "_CHANNEL_CONFIG_API.normalize_channel_passthrough",
            source,
        )
        channel_source = (
            root / "ciel_runtime_support" / "channel_config_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("__getattr__", channel_source)
        for composition_function in (
            "recommended_timeout_ms_for_context",
            "ollama_catalog_is_stale",
            "ollama_catalog_context_for_model",
            "ollama_catalog_timeout_for_model",
            "update_ollama_catalog_context",
            "channel_config_service",
        ):
            self.assertIn(composition_function, root_functions)

    def test_support_modules_do_not_import_the_composition_root(self):
        support = Path(__file__).resolve().parents[1] / "ciel_runtime_support"
        for path in support.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imported = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported.update(
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            self.assertNotIn("ciel_runtime", imported, path.name)

    def test_support_module_import_graph_is_acyclic(self):
        support = Path(__file__).resolve().parents[1] / "ciel_runtime_support"
        module_by_path = {}
        for path in support.rglob("*.py"):
            relative = path.relative_to(support.parent).with_suffix("")
            parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
            module_by_path[path] = ".".join(parts)
        modules = set(module_by_path.values())
        graph = {module: set() for module in modules}

        for path, module in module_by_path.items():
            package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    graph[module].update(
                        alias.name for alias in node.names if alias.name in modules
                    )
                    continue
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.level:
                    package_parts = package.split(".")
                    parent = ".".join(
                        package_parts[: len(package_parts) - node.level + 1]
                    )
                    target = ".".join(
                        part for part in (parent, node.module or "") if part
                    )
                else:
                    target = node.module or ""
                if target in modules:
                    graph[module].add(target)
                graph[module].update(
                    child
                    for alias in node.names
                    if (child := f"{target}.{alias.name}" if target else alias.name)
                    in modules
                )

        visiting: list[str] = []
        active: set[str] = set()
        visited: set[str] = set()

        def visit(module: str) -> None:
            if module in visited:
                return
            if module in active:
                start = visiting.index(module)
                cycle = visiting[start:] + [module]
                self.fail("support import cycle: " + " -> ".join(cycle))
            active.add(module)
            visiting.append(module)
            for dependency in graph[module]:
                visit(dependency)
            visiting.pop()
            active.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)

    def test_mcp_proxy_notification_state_is_service_owned(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        root_functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        delegated = {
            "_mcp_proxy_notification_payload",
            "_mcp_proxy_stable_event_identity",
            "_mcp_proxy_notification_dedupe_key",
            "_mcp_proxy_should_skip_duplicate_notification",
            "_mcp_proxy_observe_json_message",
        }
        self.assertTrue(delegated.isdisjoint(root_functions))
        self.assertIn("McpProxyNotificationService", source)
        service_source = (
            root / "ciel_runtime_support" / "mcp_proxy_notifications.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class McpNotificationDedupeState:", service_source)
        self.assertNotIn("__getattr__", service_source)
        self.assertNotIn("import ciel_runtime", service_source)

    def test_composition_root_delegates_major_application_services(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("from ciel_runtime_support import runtime_launch", source)
        self.assertIn("from ciel_runtime_support import prelaunch", source)
        self.assertIn("from ciel_runtime_support import claude_router", source)
        self.assertIn("from ciel_runtime_support import cli_dispatch", source)
        self.assertIn("from ciel_runtime_support import cli_parser", source)
        self.assertIn(
            "from ciel_runtime_support import channel_injection", source
        )
        self.assertIn(
            "from ciel_runtime_support import channel_llm_context", source
        )
        self.assertIn("from ciel_runtime_support import llm_presets", source)
        self.assertIn(
            "from ciel_runtime_support import mcp_proxy_notifications", source
        )
        self.assertIn("from ciel_runtime_support import provider_models", source)
        self.assertIn(
            "from ciel_runtime_support import codex_launch_configuration",
            source,
        )
        self.assertIn(
            "from ciel_runtime_support import codex_mcp_integration", source
        )
        self.assertIn(
            "from ciel_runtime_support import openai_responses_router", source
        )
        self.assertIn(
            "from ciel_runtime_support import provider_catalog_sources", source
        )
        self.assertIn(
            "from ciel_runtime_support import streaming_anthropic", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.runtime_launch import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.prelaunch import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.streaming_anthropic import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.claude_router import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.cli_dispatch import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.cli_parser import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.channel_injection import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.channel_llm_context import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.llm_presets import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.mcp_proxy_notifications import",
            source,
        )
        self.assertNotIn(
            "from ciel_runtime_support.provider_models import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.codex_launch_configuration import",
            source,
        )
        self.assertNotIn(
            "from ciel_runtime_support.codex_mcp_integration import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.openai_responses_router import", source
        )
        self.assertNotIn(
            "from ciel_runtime_support.provider_catalog_sources import",
            source,
        )
        expected_calls = {
            "_rebatch_anthropic_sse_text": "rebatch_anthropic_sse_text",
            "_ollama_stream_to_anthropic_sse": "ollama_stream_to_anthropic_sse",
            "stream_openai_chat_to_anthropic_sse": "forward_openai_chat_to_anthropic_sse",
            "openai_chat_to_anthropic": "project_openai_chat_response",
            "provider_mode_label": "label",
            "cmd_ollama_catalog": "refresh_command",
            "fetch_ollama_library_context_limit": (
                "fetch_library_context_limit"
            ),
            "inbound_query_has_beta_flag": "inbound_has_beta",
            "upstream_messages_query": "upstream_query",
            "upstream_query_string_status": "status",
            "reject_external_router_request": (
                "reject_external_request"
            ),
            "render_router_home_html": "render_router_home",
            "render_web_chat_html": "render_web_chat",
            "handle_web_get": "handle_get",
            "router_health_summary": "summary",
            "router_health_matches_current": "matches_current",
            "router_health_config_matches_current": (
                "config_matches_current"
            ),
            "router_health_has_foreign_config": "has_foreign_config",
            "body_ultracode_runtime_enabled": "runtime_enabled",
            "ultracode_workflow_preferred": "workflow_preferred",
            "resolve_blocked_tools": "blocked_tools",
            "should_normalize_anthropic_stream_tool_use": (
                "normalize_anthropic_stream_tool_use"
            ),
            "provider_supports_tool_choice": "supports_tool_choice",
            "provider_tool_choice_status": "tool_choice_status",
            "normalize_tool_choice_for_provider": "normalize_tool_choice",
            "dump_response_for_trace": "write",
            "router_debug_message_preview_chars": "configured_chars",
            "router_event_message_preview": "project",
            "finish_outgoing_sse_trace": "finish_stream",
            "set_provider_config": "select_standard",
            "store_nvidia_api_key": "store",
            "clear_nvidia_api_key": "clear",
            "set_advisor_model_config": "select",
            "_channel_stdin_wake_state": "state",
            "_channel_stdin_wake_state_for_message": "state_for_message",
            "_channel_stdin_wake_queued_is_stale_for_message": "queued_is_stale",
            "write_native_mcp_config_from_discovery": "write",
            "_log_codex_app_server_command_for_diagnostics": "codex_app_server",
            "claude_supports_permission_mode_arg": "supports_permission_mode",
            "_chat_messages_file_lock": "exclusive_file_lock",
            "terminate_active_router_clients": "terminate_active",
            "channel_specs": "configured_specs",
            "auto_discovered_mcp_channel_specs": "discover_channel_specs",
            "_channel_current_tmux_pane_text": "capture",
            "codex_responses_body_with_channel_context": "project",
            "schedule_router_process_restart": "schedule_router_restart",
            "read_env_file": "parse_dotenv_file",
            "openai_context_limit_for_budget": "context_limit",
            "_channel_wake_store_release_stale": "release_stale",
            "_channel_inflight_complete_wake": "complete",
            "_channel_wake_store_record_prompts": "record_prompts",
            "_channel_wake_store_rollback": "rollback",
            "auto_apply_recommended_llm_preset_for_model": "apply_recommended",
            "apply_auto_llm_options_config": "apply_auto",
            "terminate_posix_port": "terminate_port",
            "terminate_windows_port": "terminate_port",
            "format_context_tokens": "project_format_context_tokens",
            "format_parameter_count": "project_format_parameter_count",
            "context_setting_status": "status",
            "_channel_llm_read_cursor_locked": "resolve_read",
            "_commit_channel_llm_cursor_if_newer": "newer",
            "serve": "run",
            "provider_wire_profile": "resolve_provider_wire_profile",
            "normalize_request_for_provider_wire": "normalize_provider_request",
            "apply_llm_preset_to_provider": "apply_preset_to_provider",
            "portable_prelaunch_menu": "execute_prelaunch_menu",
            "launch_claude": "run_claude",
            "launch_codex": "run_codex",
            "launch_codex_app_server": "run_codex_app_server",
            "launch_agy": "run_agy",
            "upstream_model_ids": "fetch_upstream_model_ids",
        }
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for wrapper, target in expected_calls.items():
            calls = {
                (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                )
                for node in ast.walk(functions[wrapper])
                if isinstance(node, ast.Call)
                and isinstance(node.func, (ast.Name, ast.Attribute))
            }
            self.assertIn(target, calls, wrapper)

    def test_stateless_compatibility_exports_are_direct_aliases(self):
        import ciel_runtime

        aliases = (
            ("meaningful_key_value", "project_meaningful_key_value"),
            ("api_key_clear_requested", "project_api_key_clear_requested"),
            ("command_file_is_ciel_runtime_owned", "is_owned_command_file"),
            ("_message_content_blocks", "project_message_content_blocks"),
            ("anthropic_thinking_requested", "project_anthropic_thinking_requested"),
            ("anthropic_thinking_block_count", "project_anthropic_thinking_block_count"),
            ("anthropic_tool_continuation_block_count", "project_anthropic_tool_continuation_block_count"),
            ("anthropic_assistant_history_count", "project_anthropic_assistant_history_count"),
            ("strip_anthropic_thinking_blocks_from_messages", "project_strip_thinking_blocks"),
            ("has_ciel_runtime_synthetic_tool_use", "project_has_synthetic_tool_use"),
            ("normalize_thinking_for_non_anthropic_native_provider", "normalize_thinking_for_non_anthropic_provider"),
            ("_copy_thinking_blocks", "project_copy_thinking_blocks"),
            ("_channel_mcp_tool_schemas", "channel_mcp_tool_schemas"),
            ("_channel_mcp_tool_response", "channel_mcp_tool_response"),
            ("_channel_mcp_parse_event_id", "parse_channel_event_id"),
            ("advisor_tool_schema", "project_advisor_tool_schema"),
            ("is_claude_code_advisor_server_tool", "project_is_advisor_server_tool"),
            ("advisor_tool_focus_from_message", "project_advisor_tool_focus"),
            ("anthropic_message_tool_names", "project_anthropic_message_tool_names"),
            ("missing_openai_tool_result_message", "project_missing_openai_tool_result_message"),
            ("orphan_openai_tool_message_to_user", "project_orphan_openai_tool_message_to_user"),
            ("anthropic_message_has_tool_result", "compacted_anthropic_message_has_tool_result"),
            ("anthropic_safe_tail_start", "compacted_anthropic_safe_tail_start"),
            ("anthropic_text_response", "project_anthropic_text_response"),
            ("prepend_anthropic_text", "project_prepend_anthropic_text"),
            ("normalize_import_session_source", "normalize_import_source"),
            ("_import_session_tool_text", "import_tool_text"),
            ("_import_session_record_to_line", "import_record_line"),
            ("upstream_retry_wait_seconds", "project_upstream_retry_wait_seconds"),
            ("mask_secret", "project_mask_secret"),
            ("secret_fingerprint", "project_secret_fingerprint"),
            ("redact_sensitive_text", "project_redact_sensitive_text"),
            ("redact_sensitive_obj", "project_redact_sensitive_obj"),
            ("apply_kimi_model_profile", "apply_provider_model_profile"),
            ("pid_is_running", "inspect_pid_is_running"),
            ("ansi", "render_ansi"),
            ("animated_ansi_text", "render_animated_ansi_text"),
            ("cell_width", "terminal_cell_width"),
            ("fit_cells", "fit_terminal_cells"),
            ("pad_cells", "pad_terminal_cells"),
            ("claude_session_control_requested", "project_session_control_requested"),
            ("current_launch_cwd_key", "project_current_launch_cwd_key"),
            ("_windows_console_input_handle", "_resolve_windows_console_input_handle"),
            ("_windows_console_utf16_units", "project_windows_console_utf16_units"),
            ("codex_help_requested", "project_codex_help_requested"),
        )
        for alias, target in aliases:
            with self.subTest(alias=alias):
                self.assertIs(getattr(ciel_runtime, alias), getattr(ciel_runtime, target))

        static_aliases = (
            ("_mcp_tool_leaf_name", ciel_runtime.McpNotificationWaitService.tool_leaf_name),
            ("executable_candidates", ciel_runtime.ExecutableDiscovery.candidates),
            ("model_context_field", ciel_runtime.ProviderRuntimeInfoService.model_context),
            ("endpoint_probe_status_label", ciel_runtime.ProviderEndpointProbePolicy.status_label),
            ("query_int", ciel_runtime.EventHttpAdapter.query_int),
            ("_safe_segment", ciel_runtime.ChatFileRepository.safe_segment),
            ("chat_file_markdown_lines", ciel_runtime.ChatFileRepository.markdown_lines),
            ("_channel_sse_status_public", ciel_runtime.ChannelConnectionRegistry.public_status),
            ("_channel_sse_public_mcp_name", ciel_runtime.ChannelConnectionRegistry.public_mcp_name),
            ("codex_mcp_local_sse_hold_seconds", ciel_runtime.McpSplitProxyHttpAdapter.local_sse_hold_seconds),
            ("truncate_for_prompt", ciel_runtime.ContextSummaryPolicy.truncate),
            ("is_claude_code_persisted_output_text", ciel_runtime.ContextSummaryPolicy.is_persisted_output),
            ("_message_tool_markers_for_summary", ciel_runtime.ContextSummaryPolicy.tool_markers),
            ("_compact_chunk_ranges", ciel_runtime.ContextSummaryPolicy.chunk_ranges),
            ("_path_identity_text", ciel_runtime.RouterHealthPolicy.path_identity),
            ("read_clipboard_text", ciel_runtime.terminal_platform_io.read_clipboard_text),
            ("normalize_llm_preset_token", ciel_runtime.llm_presets.normalize_preset_token),
            ("is_qwen36_plus_model_id", ciel_runtime.ModelContextHintPolicy.is_qwen36_plus),
            ("vllm_tool_parser_hint", ciel_runtime.CompatibilityRuntimeProjection.vllm_tool_parser_hint),
            ("router_managed_idle_exit_seconds", ciel_runtime.ManagedRouterLifetime.idle_exit_seconds),
            ("router_client_supervisor_interval_seconds", ciel_runtime.RouterClientSupervisor.interval_seconds),
            ("file_size_or_zero", ciel_runtime.RoutedLaunchDiagnostics.file_size),
            ("_read_text_file_from_offset", ciel_runtime.RoutedLaunchDiagnostics.read_from_offset),
            ("body_without_ciel_runtime_internal_metadata", ciel_runtime.channel_llm_context.strip_internal_metadata),
            ("verify_sha512", ciel_runtime.AgyInstaller.verify_sha512),
        )
        for alias, target in static_aliases:
            with self.subTest(alias=alias):
                self.assertIs(getattr(ciel_runtime, alias), target)
        delivered = ciel_runtime._channel_wake_store_mark_delivered
        self.assertIs(delivered.__self__, ciel_runtime._CHANNEL_WAKE_DELIVERY_REPOSITORY)
        self.assertIs(delivered.__func__, type(delivered.__self__).mark_delivered)
        for alias, method_name in (
            ("chat_file_max_bytes", "configured_max_bytes"),
            ("chat_file_message_text", "message_text"),
        ):
            bound = getattr(ciel_runtime, alias)
            target = getattr(ciel_runtime.ChatFileRepository, method_name)
            self.assertIs(bound.__self__, ciel_runtime.ChatFileRepository)
            self.assertIs(bound.__func__, target.__func__)

    def test_static_hook_and_slash_assets_live_outside_facade(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("TOOL_GUARD_EVENTS_WITH_TOOL_MATCHER: tuple", source)
        self.assertNotIn("TOOL_GUARD_EVENTS_WITHOUT_MATCHER: tuple", source)
        self.assertNotIn('VERSION_SLASH_COMMAND = """', source)
        self.assertNotIn('LEGACY_MARKER_PREFIX = "CLAUDE"', source)

    def test_bounded_context_static_configuration_lives_outside_facade(self):
        import ciel_runtime
        from ciel_runtime_support import prelaunch, runtime_launch
        from ciel_runtime_support.advisor_request_builder import ADVISOR_REVIEW_PROMPT
        from ciel_runtime_support.runtime_constants import (
            CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS,
        )

        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("ADVISOR_REVIEW_PROMPT = (", source)
        self.assertNotIn("MAIN_MENU_ACTIONS: tuple", source)
        self.assertNotIn("CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS = (", source)
        self.assertNotIn("CLAUDE_CODE_GENERATED_GREEDY_OPTIONS = {", source)
        self.assertIs(ciel_runtime.ADVISOR_REVIEW_PROMPT, ADVISOR_REVIEW_PROMPT)
        self.assertIs(ciel_runtime.MAIN_MENU_ACTIONS, prelaunch.MAIN_MENU_ACTIONS)
        self.assertIs(
            ciel_runtime.CLAUDE_CODE_GENERATED_GREEDY_OPTIONS,
            runtime_launch.CLAUDE_CODE_GENERATED_GREEDY_OPTIONS,
        )
        self.assertIs(
            ciel_runtime.CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS,
            CODEX_OPENAI_COMPATIBLE_ROUTER_PROVIDERS,
        )

    def test_cli_runtime_adapter_materializes_launch_spec(self):
        provider = ProviderConfig(name="test", base_url="http://localhost", model="model")
        runtime = RuntimeConfig(name="codex", executable="codex", enable_channels=True)
        spec = LaunchSpec(
            runtime=runtime,
            provider=provider,
            mode="routed",
            protocol="openai_responses",
            passthrough=("--model", "model"),
            cwd=Path("workspace"),
        )
        adapter = CliRuntimeAdapter(
            name="codex",
            executable="codex",
            environment={"CODEX_HOME": "state"},
            channel_injection=True,
        )

        command = adapter.build_command(spec)

        self.assertEqual(("codex", "--model", "model"), command.argv)
        self.assertEqual("state", command.env["CODEX_HOME"])
        self.assertEqual(Path("workspace"), command.cwd)
        self.assertTrue(adapter.supports_channel_injection(spec))
    def test_runtime_and_provider_are_separate_boundaries(self):
        provider = ProviderConfig(
            name="dummy-provider",
            base_url="https://example.invalid",
            model="model-a",
            api_keys=("key-a",),
        )
        runtime = RuntimeConfig(
            name="dummy",
            executable="dummy",
            mcp_config_paths=(Path("mcp.json"),),
            enable_channels=True,
        )
        spec = LaunchSpec(
            runtime=runtime,
            provider=provider,
            mode="router",
            protocol="anthropic_messages",
            cwd=Path("."),
        )

        adapter = DummyRuntime()
        command = adapter.build_command(spec)

        self.assertEqual(command.argv, ("dummy", "--model", "model-a"))
        self.assertEqual(command.env["DUMMY_PROVIDER"], "dummy-provider")
        self.assertEqual(adapter.mcp_config_paths(spec), (Path("mcp.json"),))
        self.assertTrue(adapter.supports_channel_injection(spec))

    def test_provider_contract_does_not_need_runtime_details(self):
        provider = DummyProvider()
        config = ProviderConfig(
            name="dummy-provider",
            base_url=provider.default_base_url(),
            model="model-a",
            api_keys=("secret",),
        )

        self.assertEqual(provider.build_headers(config, "secret"), {"Authorization": "Bearer secret"})
        self.assertEqual(provider.list_models(config)[0].id, "model-a")

    def test_tool_dialect_is_runtime_specific(self):
        dialect = DummyDialect()

        self.assertEqual(dialect.normalize_tool_name(" Read "), "Read")
        self.assertEqual(dialect.repair_tool_input("Read", {"limit": 10}), {"limit": 10})
        self.assertEqual(dialect.blocked_tools(), frozenset())


if __name__ == "__main__":
    unittest.main()
