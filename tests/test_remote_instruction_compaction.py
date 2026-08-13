import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ciel_runtime
from ciel_runtime_support.remote_instructions import RemoteInstructionResult


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


if __name__ == "__main__":
    unittest.main()
