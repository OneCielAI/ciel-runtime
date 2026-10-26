import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from ciel_runtime_support import work_mode_launch as w


class WorkModeLaunchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="work-mod-test-")
        self.addCleanup(temp.cleanup)
        self.mod = Path(temp.name) / "mod with spaces"
        manifest = self.mod / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"name": "ciel-work", "version": "1.0.0"}), encoding="utf-8")

    def test_defaults_and_explicit_settings(self):
        self.assertTrue(w.work_mode_enabled({}))
        self.assertFalse(w.work_mode_enabled({"work_mode": False}))
        self.assertFalse(w.work_plan_approval_ask({}))
        self.assertTrue(w.work_plan_approval_ask({"work_plan_approval_ask": True}))
        self.assertFalse(w.work_plan_approval_ask({"work_plan_approval_ask": "true"}))
        self.assertEqual(w.work_mode_menu_label({}), "on (default)")
        self.assertEqual(w.work_mode_menu_label({"work_mode": False}), "off")
        self.assertEqual(w.work_plan_approval_menu_label({}), "auto (default)")
        self.assertEqual(w.work_plan_approval_menu_label({"work_plan_approval_ask": True}), "ask user")

    def test_native_no_router_and_disabled_skip_both_clients(self):
        for enabled in (False, True):
            for native in (False, True):
                for attached in (False, True):
                    with self.subTest(enabled=enabled, native=native, attached=attached):
                        config = {"work_mode": enabled}
                        kwargs = {"native": native, "router_mcp_attached": attached}
                        applies = enabled and not native and attached
                        self.assertEqual(w.work_mode_applies(config, **kwargs), applies)
                        self.assertEqual(bool(w.claude_work_mode_args(config, mod_dir=self.mod, **kwargs)), applies)
                        self.assertEqual(bool(w.codex_work_mode_args(config, **kwargs)), applies)

    def test_claude_flag_keeps_space_containing_path_in_single_argument(self):
        self.assertEqual(w.claude_work_mode_args({}, native=False, router_mcp_attached=True, mod_dir=self.mod),
                         ["--plugin-dir", str(self.mod)])

    def test_missing_claude_plugin_skips_flag(self):
        self.assertEqual(w.claude_work_mode_args({}, native=False, router_mcp_attached=True,
                                                mod_dir=self.mod / "missing"), [])

    def test_codex_contract_is_exact_valid_toml(self):
        args = w.codex_work_mode_args({}, native=False, router_mcp_attached=True)
        self.assertEqual(args[0::2], ["-c"] * 9)
        self.assertEqual(tomllib.loads(args[1])["developer_instructions"], w.WORK_MODE_INSTRUCTIONS)
        approvals = {item.split("=", 1)[0]: tomllib.loads("v=" + item.split("=", 1)[1])["v"] for item in args[3::2]}
        from ciel_runtime_support.work_mcp_tools import WORK_TOOL_NAMES
        self.assertEqual(approvals, {
            f"mcp_servers.ciel-runtime-router.tools.{tool}.approval_mode": "approve" for tool in WORK_TOOL_NAMES
        })
        for tool in ("work_plan_set", "work_execute", "work_node_add", "work_status"):
            self.assertIn(tool, w.WORK_MODE_INSTRUCTIONS)
        self.assertIn("user_confirmed=true", w.WORK_MODE_INSTRUCTIONS)

    def test_codex_explicit_developer_override_wins(self):
        for passthrough in (["-c", 'developer_instructions="mine"'],
                            ["--config", 'developer_instructions="mine"'],
                            ['--config=developer_instructions="mine"'],
                            ["-c", 'developer_instructions = ""']):
            with self.subTest(passthrough=passthrough):
                before = list(passthrough)
                args = w.codex_work_mode_args({}, native=False, router_mcp_attached=True, passthrough=passthrough)
                self.assertFalse(any(item.startswith("developer_instructions=") for item in args))
                self.assertEqual(16, len(args))
                self.assertEqual(passthrough, before)

    def test_codex_explicit_tool_approval_override_wins(self):
        key = "mcp_servers.ciel-runtime-router.tools.work_execute.approval_mode"
        args = w.codex_work_mode_args({}, native=False, router_mcp_attached=True,
                                      passthrough=["-c", f'{key}="prompt"'])
        self.assertNotIn(f'{key}="approve"', args)
        self.assertEqual(16, len(args))

    def test_codex_unrelated_override_preserves_work(self):
        for passthrough in ([], ["-c", 'model="other"'], ["--config", 'developer_instructions_extra="x"'],
                            ("resume", "--last")):
            with self.subTest(passthrough=passthrough):
                self.assertTrue(w.codex_work_mode_args({}, native=False, router_mcp_attached=True,
                                                      passthrough=passthrough))


if __name__ == "__main__":
    unittest.main()
