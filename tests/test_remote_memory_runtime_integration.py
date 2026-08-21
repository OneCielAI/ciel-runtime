import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.remote_instructions import RemoteInstructionResult
from ciel_runtime_support.remote_memory import RemoteMemoryResult


class RemoteMemoryRuntimeIntegrationTests(unittest.TestCase):
    def test_synchronizers_use_the_router_launch_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "project"
            with (
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime.Path,
                    "cwd",
                    return_value=Path(td) / "router-process-directory",
                ),
            ):
                instruction = ciel_runtime.remote_instruction_synchronizer()
                memory = ciel_runtime.remote_memory_synchronizer()
                self.assertEqual(workspace, instruction.workspace())
                self.assertEqual(workspace, memory.workspace())

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

    def test_instruction_refresh_removes_the_obsolete_native_memory_pointer(self):
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

    def test_managed_memory_prompt_is_injected_for_all_router_protocol_families(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            prompt = (
                "<!-- ciel-runtime:remote-memory:begin -->\n"
                f"Memory index: {index.resolve()}\n"
                "<!-- ciel-runtime:remote-memory:end -->"
            )
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value={"remote_memory": {"enabled": True}},
                ),
            ):
                responses = ciel_runtime.body_with_remote_memory_prompt(
                    {"instructions": "base"}, "openai_responses"
                )
                chat = ciel_runtime.body_with_remote_memory_prompt(
                    {"messages": [{"role": "user", "content": "hello"}]},
                    "openai_chat",
                )
                anthropic = ciel_runtime.body_with_remote_memory_prompt(
                    {"system": "base", "messages": []}, "anthropic_messages"
                )

        self.assertIn(prompt, responses["instructions"])
        self.assertEqual("system", chat["messages"][0]["role"])
        self.assertEqual(prompt, chat["messages"][0]["content"])
        self.assertEqual(prompt, anthropic["system"][-1]["text"])

    def test_managed_memory_prompt_injection_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(
                    ciel_runtime,
                    "load_config",
                    return_value={"remote_memory": {"enabled": True}},
                ),
            ):
                once = ciel_runtime.body_with_remote_memory_prompt(
                    {"instructions": "base"}, "openai_responses"
                )
                twice = ciel_runtime.body_with_remote_memory_prompt(
                    once, "openai_responses"
                )

        self.assertEqual(once, twice)
        self.assertEqual(1, twice["instructions"].count("Memory index:"))

    def test_responses_memory_pointer_survives_to_ollama_system_wire_message(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "workspace-state"
            workspace = Path(td) / "workspace"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir(parents=True)
            (state / "remote-memory.json").write_text(
                json.dumps(
                    {
                        "workspace": str(workspace.resolve()),
                        "root": ".ciel/memory",
                        "index": "index.md",
                    }
                ),
                encoding="utf-8",
            )
            config = {"remote_memory": {"enabled": True}}
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(ciel_runtime, "load_config", return_value=config),
            ):
                responses = ciel_runtime.body_with_codex_compat_instructions(
                    config,
                    "ollama-cloud",
                    {},
                    {"model": "model", "instructions": "base", "input": "hello"},
                )
                anthropic = ciel_runtime.openai_responses_to_anthropic_messages(
                    responses,
                    "model",
                )
                wire = ciel_runtime.ollama_chat_request(
                    "model",
                    anthropic,
                    {},
                    stream=True,
                    provider="ollama-cloud",
                )

        first = wire["messages"][0]
        self.assertEqual("system", first["role"])
        self.assertIn(f"Memory index: {index.resolve()}", first["content"])
        self.assertEqual(1, first["content"].count("Memory index:"))

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
