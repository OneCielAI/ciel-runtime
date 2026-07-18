import unittest
from pathlib import Path

from ciel_runtime_support.architecture import (
    LaunchSpec,
    ModelInfo,
    ProviderAdapter,
    ProviderConfig,
    RuntimeAdapter,
    RuntimeCommand,
    RuntimeConfig,
    ToolDialect,
)
from ciel_runtime_support.protocols import PROTOCOL_ADAPTERS, OpenAIResponsesProtocolAdapter
from ciel_runtime_support.provider_adapters import PROVIDER_ADAPTERS, HttpBearerProviderAdapter
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
