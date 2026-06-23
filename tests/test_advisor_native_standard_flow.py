import tempfile
import unittest
from pathlib import Path
from unittest import mock

import claude_any


class AnthropicAdvisorPassthroughTests(unittest.TestCase):
    """Claude native and Anthropic routed modes follow Claude Code's built-in
    /advisor flow; claude-any must not intercept or alter it."""

    def test_advisor_request_not_intercepted_for_anthropic(self):
        handler = mock.Mock()
        pcfg = {"advisor_model": "claude-sonnet-4-6", "route_through_router": True}
        body = {
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "CLAUDE_ANY_ADVISOR_CALL\nFocus: x"}]}],
        }
        self.assertFalse(claude_any.maybe_handle_advisor_request(handler, "anthropic", pcfg, body))
        handler.assert_not_called()

    def test_advisor_request_still_intercepted_for_other_providers(self):
        handler = mock.Mock()
        body = {
            "model": "some-model",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "CLAUDE_ANY_ADVISOR_CALL"}]}],
        }
        with mock.patch.object(claude_any, "write_anthropic_text_response") as write:
            self.assertTrue(claude_any.maybe_handle_advisor_request(handler, "ollama", {"advisor_model": ""}, body))
        write.assert_called_once()


class AdvisorSlashCommandInstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(claude_any, "CLAUDE_COMMANDS_DIR", Path(self._tmp.name))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_install_with_advisor_writes_both_commands(self):
        claude_any.install_claude_any_slash_commands(include_advisor=True)
        self.assertTrue((Path(self._tmp.name) / "advisor.md").exists())
        self.assertTrue((Path(self._tmp.name) / "router-debug.md").exists())
        self.assertTrue((Path(self._tmp.name) / "channel-clear.md").exists())

    def test_install_without_advisor_skips_advisor_command(self):
        claude_any.install_claude_any_slash_commands(include_advisor=False)
        self.assertFalse((Path(self._tmp.name) / "advisor.md").exists())
        self.assertTrue((Path(self._tmp.name) / "router-debug.md").exists())
        self.assertTrue((Path(self._tmp.name) / "channel-clear.md").exists())

    def test_install_without_advisor_removes_claude_any_owned_command(self):
        # A previous non-anthropic launch installed claude-any's /advisor;
        # an anthropic launch must remove it so the native /advisor surfaces.
        claude_any.install_claude_any_slash_commands(include_advisor=True)
        claude_any.install_claude_any_slash_commands(include_advisor=False)
        self.assertFalse((Path(self._tmp.name) / "advisor.md").exists())

    def test_install_without_advisor_keeps_user_owned_command(self):
        path = Path(self._tmp.name) / "advisor.md"
        path.write_text("my own advisor command", encoding="utf-8")
        claude_any.install_claude_any_slash_commands(include_advisor=False)
        self.assertEqual("my own advisor command", path.read_text(encoding="utf-8"))


class AdvisorMenuSurfaceTests(unittest.TestCase):
    def test_advisor_panel_for_anthropic_is_informational(self):
        rows, values = claude_any.advisor_model_panel_rows("anthropic", {"advisor_model": "claude-sonnet-4-6"})
        self.assertEqual({"back"}, set(values))
        self.assertTrue(any("built-in /advisor" in row for row in rows))

    def test_main_menu_shows_native_advisor_for_anthropic(self):
        cfg = {"language": "en"}
        pcfg = {"advisor_model": "claude-sonnet-4-6", "base_url": "https://api.anthropic.com", "current_model": "claude-haiku-4-5"}
        rows = claude_any.main_menu_rows(cfg, "anthropic", pcfg, "en")
        advisor_row = next(row for row in rows if row.startswith("5."))
        self.assertIn("Claude Code native /advisor", advisor_row)
        self.assertNotIn("claude-sonnet-4-6", advisor_row)

    def test_set_advisor_model_refused_for_anthropic(self):
        cfg = {
            "current_provider": "anthropic",
            "providers": {"anthropic": {"advisor_model": "", "route_through_router": True}},
        }
        with mock.patch.object(claude_any, "load_config", lambda: cfg), \
             mock.patch.object(claude_any, "save_config") as save:
            messages = claude_any.set_advisor_model_config("claude-sonnet-4-6")
        save.assert_not_called()
        self.assertEqual("", cfg["providers"]["anthropic"]["advisor_model"])
        self.assertTrue(any("built-in /advisor" in m for m in messages))


if __name__ == "__main__":
    unittest.main()
