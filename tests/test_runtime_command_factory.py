from pathlib import Path
import unittest
from unittest import mock

from ciel_runtime_support.architecture import RuntimeCommand
from ciel_runtime_support.runtime_command_factory import RuntimeCommandFactory, RuntimeCommandFactoryPorts


class RuntimeCommandFactoryTests(unittest.TestCase):
    def test_materializes_normalized_spec_through_registered_adapter(self):
        adapter = mock.Mock()
        adapter.build_command.return_value = RuntimeCommand(
            argv=("codex", "--yolo", "prompt"),
            env={"ROUTED": "1"},
            cwd=Path("work"),
        )
        create_adapter = mock.Mock(return_value=adapter)
        factory = RuntimeCommandFactory(
            RuntimeCommandFactoryPorts(lambda value: str(value).split(","), create_adapter)
        )

        argv, environment = factory.materialize(
            "codex",
            "codex",
            {"BASE": "1"},
            "openrouter",
            {"base_url": "https://router", "current_model": "model", "api_keys": "a,b"},
            mode="routed",
            protocol="openai_chat",
            passthrough=["prompt"],
            enable_channels=True,
        )

        self.assertEqual(["codex", "--yolo", "prompt"], argv)
        self.assertEqual({"ROUTED": "1"}, environment)
        spec = adapter.build_command.call_args.args[0]
        self.assertEqual(("a", "b"), spec.provider.api_keys)
        self.assertEqual(("prompt",), spec.passthrough)
        create_adapter.assert_called_once_with(
            "codex", executable="codex", environment={"BASE": "1"}, channel_injection=True
        )

    def test_rejects_empty_runtime_command_before_registry_lookup(self):
        create_adapter = mock.Mock()
        factory = RuntimeCommandFactory(RuntimeCommandFactoryPorts(lambda _value: [], create_adapter))
        with self.assertRaisesRegex(RuntimeError, "runtime command is empty"):
            factory.materialize("agy", "", {}, "agy", {}, mode="native", protocol="anthropic")
        create_adapter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
