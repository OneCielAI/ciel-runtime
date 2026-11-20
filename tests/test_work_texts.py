"""Work texts are data: one source, generated for the mod, editable without a restart (2026-11-19)."""
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_texts
from ciel_runtime_support import work_workflow_prompts as prompts

HOOKS = Path(__file__).resolve().parents[1] / "ciel_runtime_support/claude_mods/ciel-work/hooks"


class WorkTextsTests(unittest.TestCase):
    def test_the_mod_defaults_file_is_generated_from_the_registry(self):
        generated = (HOOKS / work_texts.MOD_TEXTS_FILE).read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertEqual(work_texts.mod_texts_js(), generated)

    def test_the_mod_keeps_no_literal_copy_of_a_registry_text(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(HOOKS.glob("*.js"))
                           if path.name != work_texts.MOD_TEXTS_FILE)
        for name, text in work_texts.mod_defaults().items():
            self.assertNotIn(text[:60], source, name)
            self.assertIn("T('" + name + "'", source, name)

    def test_every_work_text_is_a_managed_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            names = {row["name"] for row in prompts.listing(Path(raw))["prompts"]}
        self.assertTrue(set(work_texts.TEXTS) <= names)

    def test_a_stored_mod_text_reaches_the_mod_with_a_new_fingerprint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            before = prompts.mod_overrides(root)
            self.assertEqual({}, before["texts"])
            prompts.set_prompt(root, "mod_planning", "계획 승인 전에는 수정하지 않습니다.",
                               prompts.fingerprint(prompts.effective(root, "mod_planning")))
            prompts.set_prompt(root, "gate_recheck", "게이트 규칙", prompts.fingerprint(prompts.effective(root, "gate_recheck")))
            after = prompts.mod_overrides(root)
            # Only the mod's own texts travel to the mod.
            self.assertEqual({"mod_planning": "계획 승인 전에는 수정하지 않습니다."}, after["texts"])
            self.assertNotEqual(before["fingerprint"], after["fingerprint"])


if __name__ == "__main__":
    unittest.main()
