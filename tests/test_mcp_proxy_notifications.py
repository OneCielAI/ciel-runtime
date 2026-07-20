import json
import threading
import unittest

from ciel_runtime_support.mcp_proxy_notifications import (
    McpNotificationDedupeState,
    McpNotificationEffects,
    McpNotificationProjectionPorts,
    McpProxyNotificationService,
)


class McpProxyNotificationServiceTests(unittest.TestCase):
    def service(self, recent):
        return McpProxyNotificationService(
            projection=McpNotificationProjectionPorts(
                json_safe_metadata=lambda value: value,
                event_meta=lambda *_sources: {},
                event_text=lambda value: str(value.get("content") or "")
                if isinstance(value, dict)
                else "",
                pretty_json=lambda value: json.dumps(value),
                semantic_text=lambda _value: "hello",
            ),
            effects=McpNotificationEffects(
                append_chat_message=lambda payload: {**payload, "id": 1},
                log=lambda _level, _message: None,
            ),
            dedupe=McpNotificationDedupeState(
                lock=threading.Lock(),
                recent=recent,
                ttl_seconds=3.0,
                native_method="notifications/claude/channel",
                clock=lambda: 100.0,
            ),
        )

    def test_generic_and_native_writers_share_semantic_dedupe_key(self):
        service = self.service({})
        generic = {
            "channel": "room",
            "sender_id": "agent",
            "message": "hello",
            "meta": {"mcp_method": "notifications/message", "room_id": "room"},
        }
        native = {
            **generic,
            "meta": {
                "mcp_method": "notifications/claude/channel",
                "room_id": "room",
            },
        }

        self.assertEqual((False, None), service.should_skip_duplicate("stdio", generic))
        self.assertEqual(
            (True, "notifications/message"),
            service.should_skip_duplicate("stdio", native),
        )

    def test_stable_event_identity_deduplicates_across_transports(self):
        service = self.service({})
        payload = {
            "channel": "room",
            "message": "hello",
            "meta": {
                "mcp_method": "notifications/message",
                "room_id": "room",
                "stream_id": "100-0",
            },
        }

        self.assertEqual((False, None), service.should_skip_duplicate("stdio", payload))
        self.assertEqual(
            (True, "notifications/message"),
            service.should_skip_duplicate("http", payload),
        )


if __name__ == "__main__":
    unittest.main()
