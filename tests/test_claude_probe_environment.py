from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
KEY = "CLAUDE_CODE_DISABLE_BG_SHELL_PRESSURE_REAP"


def load_script(name: str):
    path = SCRIPTS / name
    spec = importlib.util.spec_from_file_location("a18_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    prior = list(sys.path)
    try:
        sys.path.insert(0, str(SCRIPTS))
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = prior
    return module


class ClaudeProbeEnvironmentTests(unittest.TestCase):
    def test_helper_copies_input_and_overrides_only_required_key(self):
        helper = load_script("claude_probe_environment.py")
        source = {"PATH": "fixture", KEY: "0", "ANTHROPIC_API_KEY": "untouched"}
        projected = helper.claude_probe_environment(source)
        self.assertEqual("1", projected[KEY])
        self.assertEqual("fixture", projected["PATH"])
        self.assertEqual("untouched", projected["ANTHROPIC_API_KEY"])
        self.assertEqual("0", source[KEY], "the caller's environment is never mutated")
        self.assertIsNot(projected, source)

    def test_every_direct_claude_probe_uses_the_shared_helper(self):
        expected = {
            "probe_windows_conpty_first_input.py",
            "probe_claude_raw_prompt_render.py",
            "probe_windows_conpty_tui_resize.py",
        }
        for name in expected:
            with self.subTest(name=name):
                source = (SCRIPTS / name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
                helper_calls = [node for node in calls if isinstance(node.func, ast.Name)
                                and node.func.id == "claude_probe_environment"]
                self.assertTrue(helper_calls, name)
                self.assertNotIn(f'{KEY} =', source)
                self.assertNotIn(f'"{KEY}"', source)
                self.assertNotIn(f"'{KEY}'", source)

    def test_resize_probe_sets_claude_and_does_not_change_codex_or_agy(self):
        resize = load_script("probe_windows_conpty_tui_resize.py")
        inherited = {"PATH": "fixture"}
        with mock.patch.dict(os.environ, inherited, clear=True):
            claude = resize.probe_environment("claude")
            codex = resize.probe_environment("codex")
            agy = resize.probe_environment("agy")
        self.assertEqual("1", claude[KEY])
        self.assertNotIn(KEY, codex)
        self.assertNotIn(KEY, agy)
        self.assertEqual(inherited, codex)
        self.assertEqual(inherited, agy)

    def test_no_other_tracked_script_starts_claude_with_ambient_environment(self):
        offenders = []
        for path in SCRIPTS.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if ("shutil.which(\"claude\")" in source or "shutil.which('claude')" in source):
                if path.name not in {
                    "probe_windows_conpty_first_input.py",
                    "probe_claude_raw_prompt_render.py",
                }:
                    offenders.append(path.name)
                self.assertNotRegex(source, r"WindowsConPtySession\([^\n]*dict\(os\.environ\)")
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
