import unittest

import ciel_runtime
from ciel_runtime_support import slash_command_assets


class SlashCommandAssetsTests(unittest.TestCase):
    def test_version_document_and_markers_are_owned_by_asset_module(self):
        self.assertIn("CIEL_RUNTIME_VERSION_STATUS", slash_command_assets.VERSION_SLASH_COMMAND)
        self.assertEqual(
            ("CIEL_RUNTIME_VERSION_STATUS",),
            slash_command_assets.VERSION_REQUEST_MARKERS,
        )
        self.assertIs(
            ciel_runtime.VERSION_SLASH_COMMAND,
            slash_command_assets.VERSION_SLASH_COMMAND,
        )

    def test_restart_session_command_is_installed_and_handled_locally(self):
        # User decision 2026-11-17: a slash entry for the same safe restart.
        from ciel_runtime_support.router_shortcuts import RestartSessionShortcut, ShortcutResponsePorts
        self.assertIn("CIEL_RUNTIME_RESTART_SESSION", slash_command_assets.RESTART_SESSION_SLASH_COMMAND)
        self.assertIn("Value: $ARGUMENTS", slash_command_assets.RESTART_SESSION_SLASH_COMMAND)
        source = (ciel_runtime.Path(ciel_runtime.__file__).parent / "ciel_runtime_support" / "runtime_asset_assembly.py").read_text(encoding="utf-8")
        self.assertIn('"restart-session.md": CommandAsset(', source)
        writes, events, queued = [], [], []
        ports = ShortcutResponsePorts(lambda: {}, lambda _config: "alias", lambda _config: ("p", {}),
                                      lambda handler, model, text, stream: writes.append((model, text, stream)),
                                      lambda **event: events.append(event))
        body = {"model": "m", "stream": False, "messages": [{"role": "user", "content": "x"}]}
        shortcut = RestartSessionShortcut(ports, lambda value: value is body, lambda _body: "deploy",
                                          lambda reason: queued.append(reason) or {"ok": True, "request": {"id": "a" * 32}})
        self.assertFalse(shortcut.handle(None, {"messages": []}))
        self.assertEqual([], queued)
        self.assertTrue(shortcut.handle(None, body))
        self.assertEqual(["deploy"], queued)
        self.assertEqual("m", writes[0][0])
        self.assertIn("session restart queued (request aaaaaaaaaaaa)", writes[0][1])
        self.assertEqual("restart_session.slash", events[0]["category"])
        refused = RestartSessionShortcut(ports, lambda _value: True, lambda _body: "",
                                         lambda reason: {"ok": False, "detail": "no active client"})
        self.assertTrue(refused.handle(None, body))
        self.assertIn("not queued: no active client", writes[-1][1])
        # The router's own binding: the real marker check and value line, a Claude restart from /restart-session.
        requests = []
        slash = RestartSessionShortcut.for_slash(ports, ciel_runtime.latest_user_text_has_marker,
                                                 ciel_runtime.project_single_shortcut_value,
                                                 ciel_runtime.shortcut_text_services,
                                                 lambda **request: requests.append(request) or {"ok": True, "request": {}})
        typed = slash_command_assets.RESTART_SESSION_SLASH_COMMAND.replace("$ARGUMENTS", "deploy now")
        self.assertTrue(slash.matches({"messages": [{"role": "user", "content": typed}]}))
        self.assertFalse(slash.matches({"messages": [{"role": "user", "content": "restart please"}]}))
        self.assertTrue(slash.handle(None, {"model": "m", "stream": False,
                                            "messages": [{"role": "user", "content": typed}]}))
        self.assertEqual([{"source": "slash", "reason": "deploy now", "runtime": "claude"}], requests)

    def test_command_marker_sets_include_legacy_compatibility_tokens(self):
        self.assertIn(
            slash_command_assets.LEGACY_ADVISOR_CALL_MARKER,
            slash_command_assets.CIEL_RUNTIME_ADVISOR_COMMAND_MARKERS,
        )
        self.assertIn(
            slash_command_assets.LEGACY_LIVE_LLM_OPTIONS_MARKER,
            slash_command_assets.CIEL_RUNTIME_LLM_OPTIONS_COMMAND_MARKERS,
        )
        self.assertEqual(
            slash_command_assets.CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS,
            ciel_runtime.CIEL_RUNTIME_CHANNEL_CLEAR_COMMAND_MARKERS,
        )


if __name__ == "__main__":
    unittest.main()
