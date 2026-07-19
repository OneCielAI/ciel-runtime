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
from ciel_runtime_support.advisor_policy import (
    AdvisorDecisionServices,
    AdvisorServices,
    AdvisorTextServices,
)
from ciel_runtime_support.advisor_request_builder import (
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
from ciel_runtime_support.channel_config_service import ChannelConfigPorts
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
from ciel_runtime_support.channel_transcript import ChannelWakeTranscriptServices
from ciel_runtime_support.channel_message_repository import ChannelMessageRepository
from ciel_runtime_support.channel_wake_claim_repository import ChannelWakeClaimRepository
from ciel_runtime_support.channel_launch_guard_repository import ChannelLaunchGuardRepository
from ciel_runtime_support.channel_cursor_repository import ChannelCursorRepository
from ciel_runtime_support.channel_session_repository import ChannelSessionRepository
from ciel_runtime_support.channel_session_lifecycle import ChannelSessionLifecycleServices
from ciel_runtime_support.channel_probe_report import ChannelProbeReportServices
from ciel_runtime_support.channel_probe_cache import ChannelProbePorts
from ciel_runtime_support.config_migrations import ConfigMigrationPolicy
from ciel_runtime_support.compatibility_test import (
    CompatibilityTestConfig,
    CompatibilityTestConstants,
    CompatibilityTestMode,
    CompatibilityTestOutput,
    CompatibilityTestProtocol,
    CompatibilityTestRequest,
    CompatibilityTestServices,
)
from ciel_runtime_support.headless_config import (
    HeadlessChannelCommands,
    HeadlessConfigCommands,
    HeadlessConfigResult,
    HeadlessConfigServices,
)
from ciel_runtime_support.mcp_proxy_codec import McpProxyCodecPolicy
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
from ciel_runtime_support.tool_guard_hooks import ToolGuardHookPolicy, ToolGuardHookServices
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
from ciel_runtime_support.provider_config_mutations import ProviderOptionPolicy
from ciel_runtime_support.llm_presets import (
    PresetContextPolicy,
    PresetDefinition,
    PresetProviderMutation,
    PresetServices,
)
from ciel_runtime_support.llm_option_config import (
    LlmOptionConfigServices,
    LlmOptionMutation,
    LlmOptionPolicy,
    LlmOptionRepository,
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
    CodexLaunchServices,
    CodexAppServerChannel,
    CodexAppServerCliPolicy,
    CodexAppServerConfig,
    CodexAppServerDispatch,
    CodexAppServerInstallation,
    CodexAppServerLaunchServices,
    CodexAppServerProcess,
    CodexAppServerRouting,
)
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

    def test_headless_config_ports_stay_below_dependency_limit(self):
        for port in (
            HeadlessConfigCommands,
            HeadlessChannelCommands,
            HeadlessConfigServices,
            HeadlessConfigResult,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_preset_ports_stay_below_dependency_limit(self):
        for port in (PresetServices, PresetDefinition, PresetContextPolicy, PresetProviderMutation):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

    def test_llm_option_config_ports_stay_below_dependency_limit(self):
        for port in (
            LlmOptionRepository,
            LlmOptionMutation,
            LlmOptionPolicy,
            LlmOptionConfigServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

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
        self.assertLessEqual(len(fields(ProviderOptionPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderConfigurationPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderRequestPolicy)), 10)
        self.assertLessEqual(len(fields(ProviderStatusPolicy)), 10)
        self.assertLessEqual(len(fields(AnthropicToolTurnServices)), 10)

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
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "status_lines"
        )
        provider_comparisons = []
        for node in ast.walk(function):
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
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "set_provider_config"
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
        function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "compatibility_runtime_lines"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("PROVIDER_COMPATIBILITY.resolve(provider)", function_source)
        self.assertIn("exposes_runtime_info", function_source)
        self.assertIn("runtime_metadata", function_source)
        self.assertNotIn('provider == "', function_source)
        self.assertNotIn("provider in (", function_source)

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
        self.assertIn('"providers": provider_default_configurations()', source)
        self.assertNotIn("for _registered_provider in PROVIDER_ADAPTERS.names()", source)

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
            (root / "ciel_runtime.py", "upstream_model_runtime_info", "runtime_model_info_strategy"),
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

    def test_channel_session_repository_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelSessionRepository)), 10)

    def test_channel_session_lifecycle_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelSessionLifecycleServices)), 10)

    def test_tool_guard_hook_ports_stay_below_dependency_limit(self):
        for port in (ToolGuardHookPolicy, ToolGuardHookServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

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

    def test_mcp_json_artifacts_use_secure_repository(self):
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        target_names = {
            "write_native_mcp_config_from_discovery",
            "write_web_tools_mcp_config",
            "write_zai_mcp_config",
            "write_channel_mcp_config",
            "write_mcp_proxy_config",
            "write_codex_mcp_config_for_channel_discovery",
        }
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

    def test_request_trace_ports_stay_below_dependency_limit(self):
        for port in (RequestTracePolicy, RequestTraceProjection, RequestTraceServices):
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
        source_path = Path(__file__).resolve().parents[1] / "ciel_runtime.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "model_info_from_response"
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

    def test_lm_studio_runtime_port_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(LmStudioRuntimeServices)), 10)

    def test_mcp_proxy_codec_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(McpProxyCodecPolicy)), 10)

    def test_mcp_http_proxy_ports_stay_below_dependency_limit(self):
        for port in (
            McpHttpProxyCodec,
            McpHttpProxyTransport,
            McpHttpProxyRuntime,
            McpHttpProxyServices,
        ):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

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

    def test_channel_message_coalescing_policy_lives_outside_composition_root(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        for function_name in (
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

    def test_channel_launch_guard_repository_owns_persistence(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("CHANNEL_LLM_LAUNCH_GUARD_PATH.read_text", source)
        self.assertNotIn("CHANNEL_LLM_LAUNCH_GUARD_PATH.with_suffix", source)
        self.assertLessEqual(len(fields(ChannelLaunchGuardRepository)), 10)

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

    def test_composition_root_delegates_major_application_services(self):
        source = (Path(__file__).resolve().parents[1] / "ciel_runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        expected_calls = {
            "_rebatch_anthropic_sse_text": "rebatch_anthropic_sse_text",
            "_ollama_stream_to_anthropic_sse": "ollama_stream_to_anthropic_sse",
            "stream_openai_chat_to_anthropic_sse": "forward_openai_chat_to_anthropic_sse",
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
                node.func.id
                for node in ast.walk(functions[wrapper])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertIn(target, calls, wrapper)

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
