"""The side actor instruction: built-in default shown, operator Markdown appended (2026-11-15).

User requirement: the web dashboard showed an empty box while the built-in
policy was in use; the default must be visible, and editing opens a Markdown
editor with a preview.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_web_ui
from ciel_runtime_support.side_intake_settings import DEFAULT_SIDE_INSTRUCTION, instruction_view, set_value
from ciel_runtime_support.work_live_settings import settings
from ciel_runtime_support.work_parallel import WorkParallelStore

SIDE_JS = Path(__file__).resolve().parents[1] / "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js"


class SideInstructionViewTests(unittest.TestCase):
    def test_python_default_is_exactly_the_prompt_the_side_fork_receives(self):
        source = SIDE_JS.read_text(encoding="utf-8").replace("\r\n", "\n")
        prompt = re.search(r"const PROMPT = `([^`]*)`", source).group(1)
        self.assertEqual(prompt, DEFAULT_SIDE_INSTRUCTION)

    def test_view_shows_default_and_appends_the_operator_instruction(self):
        view = instruction_view({})
        self.assertEqual(("", DEFAULT_SIDE_INSTRUCTION, True), (view["stored"], view["effective"], view["using_default"]))
        view = instruction_view({"work_side_intake": {"instruction": "  Reply in Korean.  "}})
        self.assertEqual("Reply in Korean.", view["stored"])
        self.assertTrue(view["effective"].startswith(DEFAULT_SIDE_INSTRUCTION))
        self.assertTrue(view["effective"].endswith("\n\n## Operator-configured instruction\n\nReply in Korean."))
        self.assertFalse(view["using_default"])

    def test_empty_write_returns_to_the_default_only(self):
        config = {"work_side_intake": {"enabled": True, "instruction": "x"}}
        set_value(config, "work_side_intake_instruction", "   ")
        self.assertEqual({"enabled": True}, config["work_side_intake"])

    def test_settings_row_carries_the_markdown_editor_fields(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = WorkParallelStore(Path(root) / "ledger.json")
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=ledger), \
                 patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", Path(root)):
                rows = {row["key"]: row for row in settings({"work_side_intake": {"instruction": "Be brief."}}, {}, None)["settings"]}
        row = rows["work_side_intake_instruction"]
        self.assertEqual(("markdown", "Be brief.", "Be brief.", False),
                         (row["editor"], row["value"], row["stored_value"], row["using_default"]))
        self.assertEqual(DEFAULT_SIDE_INSTRUCTION, row["default_value"])
        self.assertTrue(row["effective_value"].endswith("Be brief."))
        # The value (the stored suffix) stays the fingerprinted setting value.
        self.assertNotIn("editor", rows["work_side_intake_max_active"])

    def test_web_console_has_the_editor_dialog_and_a_text_only_renderer(self):
        html = work_web_ui.render_work_web_ui()
        for marker in ('<dialog id="md-dialog"', 'id="md-text"', 'id="md-preview"', "function mdRender(text)",
                       "function openMarkdownEditor(setting,data)", "if(setting.editor==='markdown')",
                       "byId('md-clear').onclick=()=>apply('')", "#md-dialog .md-grid{"):
            self.assertIn(marker, html)
        renderer = html.split("function mdInline(", 1)[1].split("function openMarkdownEditor(", 1)[0]
        self.assertNotIn("innerHTML", renderer)


if __name__ == "__main__":
    unittest.main()
