import unittest
from pathlib import Path

from ciel_runtime_support.router_shortcuts import (
    ChannelShortcutPorts,
    LiveConfigShortcutPorts,
    RouterDebugShortcutPorts,
    RouterShortcutController,
    ShortcutPredicates,
    ShortcutResponsePorts,
)


class RouterShortcutControllerTests(unittest.TestCase):
    def controller(self):
        self.writes = []
        self.events = []
        self.restarts = []
        config = {
            "current_provider": "test",
            "providers": {"test": {}},
        }
        return RouterShortcutController(
            response=ShortcutResponsePorts(
                load_config=lambda: config,
                current_alias=lambda _config: "alias",
                current_provider=lambda _config: ("test", {}),
                write_anthropic=lambda *args: self.writes.append(args),
                publish_event=lambda **kwargs: self.events.append(kwargs),
            ),
            predicates=ShortcutPredicates(
                router_debug=lambda body: body.get("command") == "debug",
                version=lambda body: body.get("command") == "version",
                channel_clear=lambda body: body.get("command") == "clear",
                live_llm_options=lambda body: body.get("command") == "llm",
                live_api_keys=lambda body: body.get("command") == "keys",
            ),
            debug=RouterDebugShortcutPorts(
                value=lambda body: str(body.get("value") or "status"),
                external_enabled=lambda _config: False,
                bind_host=lambda _config: "127.0.0.1",
                set_external=lambda enabled: [f"external={enabled}"],
                schedule_restart=lambda: self.restarts.append(True),
                version="1.2.3",
                source_fingerprint="abcdefghijklmnop",
                config_dir=Path("config"),
            ),
            channel=ChannelShortcutPorts(
                value=lambda body: str(body.get("value") or "all"),
                clear=lambda: {
                    "chat_tail": 4,
                    "discarded_llm": 2,
                    "discarded_mcp": 1,
                    "mcp_sessions_updated": 3,
                },
                status=lambda: {
                    "chat_tail": 4,
                    "pending_llm": 2,
                    "pending_mcp": 1,
                    "mcp_sessions": 3,
                },
            ),
            live=LiveConfigShortcutPorts(
                llm_value=lambda body: str(body.get("value") or "status"),
                handle_llm=lambda value: ([f"llm={value}"], value != "status"),
                api_key_value=lambda body: str(body.get("value") or "status"),
                handle_api_keys=lambda value: ([f"keys={value}"], value != "status"),
                api_key_count=lambda _provider, _config: 2,
            ),
        )

    def test_router_debug_toggle_schedules_restart(self):
        controller = self.controller()
        self.assertTrue(
            controller.handle_router_debug(object(), {"command": "debug", "value": "toggle"})
        )
        self.assertEqual([True], self.restarts)
        self.assertIn("external=True", self.writes[0][2])

    def test_version_and_channel_clear_write_local_responses(self):
        controller = self.controller()
        self.assertTrue(controller.handle_version(object(), {"command": "version"}))
        self.assertIn("ciel-runtime 1.2.3", self.writes[-1][2])
        self.assertTrue(controller.handle_channel_clear(object(), {"command": "clear"}))
        self.assertIn("backlog discarded", self.writes[-1][2])

    def test_live_changes_publish_typed_events(self):
        controller = self.controller()
        self.assertTrue(
            controller.handle_live_llm_options(
                object(), {"command": "llm", "value": "balanced"}
            )
        )
        self.assertEqual("config.llm", self.events[-1]["category"])
        self.assertTrue(
            controller.handle_live_api_keys(
                object(), {"command": "keys", "value": "replace"}
            )
        )
        self.assertEqual("config.api_key", self.events[-1]["category"])
        self.assertEqual(2, self.events[-1]["data"]["key_count"])

    def test_unmatched_request_is_not_handled(self):
        controller = self.controller()
        self.assertFalse(controller.handle_version(object(), {"command": "other"}))
        self.assertEqual([], self.writes)


if __name__ == "__main__":
    unittest.main()
