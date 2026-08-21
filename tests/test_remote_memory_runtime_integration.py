import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.remote_instructions import RemoteInstructionResult
from ciel_runtime_support.remote_memory import RemoteMemoryResult


class RemoteMemoryRuntimeIntegrationTests(unittest.TestCase):
    def test_every_interactive_launch_synchronizes_instructions_and_memory(self):
        for name in (
            "launch_claude",
            "launch_codex",
            "launch_codex_app_server",
            "launch_agy",
            "launch_kimi",
            "launch_grok",
        ):
            with self.subTest(name=name):
                launch = getattr(ciel_runtime, name)
                self.assertIs(ciel_runtime.sync_remote_launch_assets, launch.synchronize)

    def test_instruction_refresh_reprojects_the_current_memory_pointer(self):
        instruction_service = mock.Mock()
        instruction_service.sync.return_value = RemoteInstructionResult(
            "codex", "https://instructions.example/AGENTS.md", Path("AGENTS.md"), "updated"
        )
        memory_service = mock.Mock()
        with (
            mock.patch.object(
                ciel_runtime,
                "remote_instruction_synchronizer",
                return_value=instruction_service,
            ),
            mock.patch.object(
                ciel_runtime,
                "remote_memory_synchronizer",
                return_value=memory_service,
            ),
        ):
            result = ciel_runtime.sync_remote_instruction("codex", reason="pre-compact")

        self.assertEqual("updated", result.status)
        memory_service.project_current_pointer.assert_called_once_with("codex")

    def test_launch_assets_sync_instruction_before_replacing_memory(self):
        memory = RemoteMemoryResult(
            "https://memory.example/manifest.json",
            Path(".ciel/memory"),
            Path(".ciel/memory/index.okf"),
            ".ciel/memory/index.okf",
            "updated",
            1,
        )
        calls = []
        with (
            mock.patch.object(
                ciel_runtime,
                "sync_remote_instruction",
                side_effect=lambda *_args, **_kwargs: calls.append("instruction"),
            ),
            mock.patch.object(
                ciel_runtime,
                "sync_remote_memory",
                side_effect=lambda *_args, **_kwargs: calls.append("memory") or memory,
            ),
        ):
            result = ciel_runtime.sync_remote_launch_assets("codex", reason="launch")

        self.assertIs(memory, result)
        self.assertEqual(["instruction", "memory"], calls)


if __name__ == "__main__":
    unittest.main()
