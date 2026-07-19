import unittest

from ciel_runtime_support.channel_event_identity import (
    fallback_dedupe_key,
    message_event_identity_key,
    stable_dedupe_key,
)


class ChannelEventIdentityTests(unittest.TestCase):
    def test_nested_event_identity_includes_room_and_stable_id(self):
        message = {
            "message": "updated",
            "meta": {
                "mcp_json": {
                    "method": "notifications/rooms",
                    "params": {"content": "updated", "meta": {"room_id": "ops", "stream_id": "7-1"}},
                }
            },
        }
        identity = message_event_identity_key(message)
        self.assertIsNotNone(identity)
        self.assertEqual(("event", "notifications/rooms", "ops", "", "stream_id", "7-1"), identity[:-1])

    def test_non_notification_rpc_has_no_event_identity(self):
        message = {"meta": {"mcp_method": "tools/call", "message_id": "7"}, "message": "result"}
        self.assertIsNone(message_event_identity_key(message))

    def test_stable_key_normalizes_equivalent_notification_json(self):
        left = {
            "message": "updated",
            "meta": {"mcp_method": "notifications/rooms", "mcp_json": {"b": 2, "a": 1}},
        }
        right = {
            "message": "updated",
            "meta": {"mcp_method": "notifications/rooms", "mcp_json": {"a": 1, "b": 2}},
        }
        self.assertEqual(stable_dedupe_key(left), stable_dedupe_key(right))

    def test_fallback_key_only_accepts_nonempty_notifications(self):
        notification = {
            "channel": "ops",
            "sender_id": "agent",
            "message": "  task\nchanged ",
            "meta": {"mcp_server": "tasks", "mcp_method": "notifications/tasks"},
        }
        self.assertIsNotNone(fallback_dedupe_key(notification))
        self.assertIsNone(fallback_dedupe_key({"message": "plain", "meta": {"mcp_method": "tools/call"}}))


if __name__ == "__main__":
    unittest.main()
