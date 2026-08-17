import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support.architecture import LaunchSpec, ProviderConfig, RuntimeConfig
from ciel_runtime_support.launch_state import LaunchStateRepository, last_launch_runtime
from ciel_runtime_support.runtime_adapters import GrokRuntimeAdapter, RUNTIME_ADAPTERS


class GrokRuntimeAdapterTests(unittest.TestCase):
    def test_runtime_is_registered_and_builds_tui_command(self) -> None:
        adapter = RUNTIME_ADAPTERS.create(
            "grok", executable="grok", environment={"XAI_API_KEY": "secret"}
        )
        self.assertIsInstance(adapter, GrokRuntimeAdapter)
        command = adapter.build_command(
            LaunchSpec(
                runtime=RuntimeConfig(
                    name="grok",
                    executable="grok",
                    options={"model": "grok-4.6", "reasoning_effort": "xhigh"},
                ),
                provider=ProviderConfig(
                    name="xai",
                    base_url="https://api.x.ai/v1",
                    model="grok-4.6",
                ),
                mode="native",
                protocol="openai_responses",
                passthrough=("--continue",),
            )
        )
        self.assertEqual(
            (
                "grok",
                "--model",
                "grok-4.6",
                "--reasoning-effort",
                "xhigh",
                "--continue",
            ),
            command.argv,
        )
        self.assertEqual("secret", command.env["XAI_API_KEY"])

    def test_acp_stdio_subcommand_is_passed_without_terminal_rewriting(self) -> None:
        command = GrokRuntimeAdapter(name="grok", executable="grok").build_command(
            LaunchSpec(
                runtime=RuntimeConfig(name="grok", executable="grok"),
                provider=ProviderConfig(name="xai", base_url="", model=""),
                mode="native",
                protocol="openai_responses",
                passthrough=("agent", "stdio"),
            )
        )
        self.assertEqual(("grok", "agent", "stdio"), command.argv)

    def test_launch_state_remembers_grok_per_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "launch-state.json"
            repository = LaunchStateRepository(
                path=path,
                config_dir=Path(tmp),
                log=mock.Mock(),
                process_id=lambda: 7,
                clock=lambda: 10.0,
                clock_ns=lambda: 11,
            )
            repository.record("C:/workspace", "xai", "grok-native", "grok-4.6")
            self.assertEqual("grok", last_launch_runtime(repository, "C:/workspace"))


if __name__ == "__main__":
    unittest.main()
