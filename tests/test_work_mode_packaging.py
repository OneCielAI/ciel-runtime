"""The npm package must ship the Claude Work mod that the launcher points at."""

import json
import unittest
from pathlib import Path

from ciel_runtime_support import work_mode_launch as w

ROOT = Path(__file__).resolve().parents[1]
MODS_ENTRY = "ciel_runtime_support/claude_mods/"


class WorkModePackagingTests(unittest.TestCase):
    def test_package_files_include_claude_mods_directory(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertIn(MODS_ENTRY, package["files"])

    def test_launcher_mod_dir_is_inside_packaged_directory(self):
        relative = w.CLAUDE_MOD_DIR.relative_to(ROOT).as_posix()
        self.assertEqual(relative, MODS_ENTRY + w.CLAUDE_MOD_NAME)

    def test_mod_manifest_hooks_and_register_are_present(self):
        manifest = w.CLAUDE_MOD_DIR / ".claude-plugin" / "plugin.json"
        hooks = w.CLAUDE_MOD_DIR / "hooks" / "hooks.json"
        register = w.CLAUDE_MOD_DIR / "hooks" / "register.js"
        for path in (manifest, hooks, register):
            self.assertTrue(path.is_file(), path)
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["name"], w.CLAUDE_MOD_NAME)
        self.assertIn("./register.js", json.loads(hooks.read_text(encoding="utf-8"))["modules"])
        source = register.read_text(encoding="utf-8")
        self.assertIn("export function register(", source)
        self.assertIn("name: 'work-mode'", source)

    def test_packaged_mod_dir_yields_plugin_dir_argument(self):
        self.assertEqual(
            w.claude_work_mode_args({}, native=False, router_mcp_attached=True),
            ["--plugin-dir", str(w.CLAUDE_MOD_DIR)],
        )


if __name__ == "__main__":
    unittest.main()
