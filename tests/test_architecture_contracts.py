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
from ciel_runtime_support.runtime_adapters import CliRuntimeAdapter


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
