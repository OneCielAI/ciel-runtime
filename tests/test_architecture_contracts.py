import ast
import unittest
from dataclasses import fields
from pathlib import Path

from ciel_runtime_support.architecture import (
    LaunchSpec,
    ModelInfo,
    ProviderAdapter,
    ProviderConfigurationPolicy,
    ProviderConfig,
    ProviderRequestPolicy,
    RuntimeAdapter,
    RuntimeCommand,
    RuntimeConfig,
    ToolDialect,
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
from ciel_runtime_support.channel_panel import ChannelPanelPolicy
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
from ciel_runtime_support.mcp_proxy_codec import McpProxyCodecPolicy
from ciel_runtime_support.model_panel import (
    ModelPanelCatalog,
    ModelPanelPresentation,
    ModelPanelServices,
)
from ciel_runtime_support.ollama_forwarding import (
    OllamaForwardAdvisor,
    OllamaForwardConstants,
    OllamaForwardRateLimit,
    OllamaForwardRequest,
    OllamaForwardResponse,
    OllamaForwardServices,
    OllamaForwardStreaming,
)
from ciel_runtime_support.openai_forwarding import (
    OpenAIForwardAdvisor,
    OpenAIForwardPolicy,
    OpenAIForwardRateLimit,
    OpenAIForwardRequest,
    OpenAIForwardResponse,
    OpenAIForwardServices,
    OpenAIForwardStreaming,
)
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

    def test_channel_panel_policy_stays_below_dependency_limit(self):
        self.assertLessEqual(len(fields(ChannelPanelPolicy)), 10)

    def test_model_panel_ports_stay_below_dependency_limit(self):
        for port in (ModelPanelCatalog, ModelPanelPresentation, ModelPanelServices):
            with self.subTest(port=port.__name__):
                self.assertLessEqual(len(fields(port)), 10)

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
        )
        critical_names = {
            "subprocess_call_with_channel_wake_proxy",
            "_mcp_proxy_forward_stdin",
            "_mcp_proxy_forward_stdin_jsonl",
            "_mcp_proxy_emit_jsonl_stdout_line",
            "_mcp_proxy_forward_stderr",
            "run_mcp_streamable_http_proxy",
            "run_mcp_stdio_proxy",
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
        self.assertNotIn("def _legacy_openai_responses_to_anthropic_messages", source)
        self.assertNotIn("def _legacy_anthropic_message_to_openai_response", source)

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
