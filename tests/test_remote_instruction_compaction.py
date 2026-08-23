import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.remote_instructions import RemoteInstructionResult
from ciel_runtime_support.remote_memory import MEMORY_POINTER_BEGIN


class RemoteInstructionCompactionTests(unittest.TestCase):
    def _result(self, runtime: str, path: Path):
        return RemoteInstructionResult(runtime, "https://config.example/instructions", path, "unchanged")

    def test_anthropic_compaction_refresh_replaces_previous_managed_block(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "CLAUDE.md"
            path.write_text("latest policy", encoding="utf-8")
            body = {"system": [{"type": "text", "text": (
                "identity\n\n<!-- ciel-runtime:remote-instructions:begin -->\nold policy\n"
                "<!-- ciel-runtime:remote-instructions:end -->"
            )}]}
            with mock.patch.object(ciel_runtime, "sync_remote_instruction", return_value=self._result("claude", path)):
                updated = ciel_runtime._refresh_anthropic_compact_body(body)
            rendered = ciel_runtime.anthropic_content_to_text(updated["system"])
            self.assertIn("identity", rendered)
            self.assertIn("latest policy", rendered)
            self.assertNotIn("old policy", rendered)
            self.assertEqual(1, rendered.count("ciel-runtime:remote-instructions:begin"))

    def test_responses_compaction_adds_latest_instruction_before_compacting(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "AGENTS.md"
            path.write_text("latest codex policy", encoding="utf-8")
            captured = {}
            def compact(body, budget, **_kwargs):
                captured.update(body)
                return body
            with (
                mock.patch.object(ciel_runtime, "sync_remote_instruction", return_value=self._result("codex", path)),
                mock.patch.object(ciel_runtime, "run_responses_prompt_compaction", side_effect=compact),
            ):
                ciel_runtime.compact_responses_with_remote_instruction({"instructions": "base", "input": []}, 8192)
            self.assertIn("base", captured["instructions"])
            self.assertIn("latest codex policy", captured["instructions"])

    def test_codex_chat_compaction_refreshes_agents_not_claude_instructions(self):
        captured = {}

        def compact(messages, _tools, _budget, **_kwargs):
            captured["messages"] = messages
            return messages

        with (
            mock.patch.object(
                ciel_runtime,
                "_latest_remote_instruction",
                side_effect=lambda runtime, **_kwargs: f"{runtime} policy",
            ) as latest,
            mock.patch.object(
                ciel_runtime, "run_chat_prompt_compaction", side_effect=compact
            ),
        ):
            ciel_runtime.compact_ollama_messages_for_budget(
                [
                    {"role": "system", "content": "identity"},
                    {"role": "user", "content": "checkpoint"},
                ],
                [],
                8192,
                full_compact_request=True,
                compact_runtime="codex",
            )

        latest.assert_called_once_with("codex", reason="pre-compact")
        system = captured["messages"][0]["content"]
        self.assertIn("codex policy", system)
        self.assertNotIn("claude policy", system)

    def test_codex_anthropic_compaction_refreshes_agents_not_claude_instructions(self):
        captured = {}

        def compact(body, _budget, **_kwargs):
            captured.update(body)
            return body

        with (
            mock.patch.object(
                ciel_runtime,
                "_latest_remote_instruction",
                side_effect=lambda runtime, **_kwargs: f"{runtime} policy",
            ) as latest,
            mock.patch.object(
                ciel_runtime, "run_anthropic_prompt_compaction", side_effect=compact
            ),
        ):
            ciel_runtime.compact_anthropic_body_for_budget(
                {
                    "system": "identity",
                    "messages": [{"role": "user", "content": "checkpoint"}],
                },
                8192,
                full_compact_request=True,
                compact_runtime="codex",
            )

        latest.assert_called_once_with("codex", reason="pre-compact")
        system = ciel_runtime.anthropic_content_to_text(captured["system"])
        self.assertIn("codex policy", system)
        self.assertNotIn("claude policy", system)

    def test_compaction_refresh_keeps_one_current_memory_block_at_each_protocol_tail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = root / "workspace"
            state = root / "state"
            index = workspace / ".ciel" / "memory" / "index.md"
            index.parent.mkdir(parents=True)
            index.write_text("# memory\n", encoding="utf-8")
            state.mkdir()
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
            stale = (
                "<!-- ciel-runtime:remote-memory:begin -->\n"
                "Memory root: C:/stale\n"
                "Memory index: C:/stale/index.md\n"
                "<!-- ciel-runtime:remote-memory:end -->"
            )
            path = workspace / "AGENTS.md"
            path.write_text(f"latest policy\n\n{stale}\n", encoding="utf-8")
            config = {"remote_memory": {"enabled": True}}
            with (
                mock.patch.object(ciel_runtime, "WORKSPACE_STATE_DIR", state),
                mock.patch.object(ciel_runtime, "ROUTER_WORKSPACE", str(workspace)),
                mock.patch.object(ciel_runtime, "load_config", return_value=config),
                mock.patch.object(
                    ciel_runtime,
                    "sync_remote_instruction",
                    return_value=self._result("codex", path),
                ),
                mock.patch.object(
                    ciel_runtime,
                    "run_responses_prompt_compaction",
                    side_effect=lambda body, _budget, **_kwargs: body,
                ),
            ):
                anthropic = ciel_runtime._refresh_anthropic_compact_body(
                    {"system": f"identity\n{stale}", "messages": []},
                    "codex",
                )
                chat = ciel_runtime._refresh_chat_compact_messages(
                    [{"role": "system", "content": f"identity\n{stale}"}],
                    "codex",
                )
                responses = ciel_runtime.compact_responses_with_remote_instruction(
                    {"instructions": f"identity\n{stale}", "input": []},
                    8192,
                )

            rendered = (
                ciel_runtime.anthropic_content_to_text(anthropic["system"]),
                chat[0]["content"],
                responses["instructions"],
            )
            for text in rendered:
                self.assertEqual(1, text.count(MEMORY_POINTER_BEGIN))
                self.assertNotIn("C:/stale", text)
                self.assertIn("Memory root: .ciel/memory", text)
                self.assertNotIn(str(workspace.resolve()), text)
                self.assertTrue(
                    text.rstrip().endswith(
                        "<!-- ciel-runtime:remote-memory:end -->"
                    )
                )


if __name__ == "__main__":
    unittest.main()
