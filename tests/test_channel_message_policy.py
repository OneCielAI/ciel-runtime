import unittest

from ciel_runtime_support.channel_message_policy import (
    message_coalesce_key,
    message_has_unique_reference,
    message_order_value,
    string_list,
    superseded_message_ids,
)


class ChannelMessagePolicyTests(unittest.TestCase):
    def test_string_list_flattens_json_and_collection_values(self):
        self.assertEqual(
            ["Robert", "Sarah", "all"],
            string_list(['["Robert"]', ("Sarah", "*")]),
        )

    def test_string_list_ignores_empty_values(self):
        self.assertEqual([], string_list(None))
        self.assertEqual([], string_list("  "))

    def test_external_event_identity_projects_source_channel_and_topic(self):
        message = {
            "id": 7,
            "channel": "ops",
            "kind": "notification",
            "meta": {
                "sse_source": "deploy",
                "sse_event": "status",
                "cursor": "12-3",
                "topic": "api",
            },
        }
        self.assertEqual((2, 12, 3), message_order_value(message))
        self.assertEqual(
            ("deploy", "ops", "status", "notification", "api"),
            message_coalesce_key(message),
        )

    def test_nested_message_reference_prevents_coalescing(self):
        message = {
            "id": 7,
            "kind": "notification",
            "meta": {
                "mcp_server": "mail",
                "mcp_method": "changed",
                "cursor": "2",
                "mcp_json": {"params": {"meta": {"message_id": "mail-1"}}},
            },
        }
        self.assertTrue(message_has_unique_reference(message))
        self.assertIsNone(message_coalesce_key(message))

    def test_latest_event_supersedes_older_event_with_same_identity(self):
        messages = [
            {
                "id": 10,
                "kind": "notification",
                "meta": {"sse_source": "jobs", "sse_event": "progress", "cursor": "9"},
            },
            {
                "id": 11,
                "kind": "notification",
                "meta": {"sse_source": "jobs", "sse_event": "progress", "cursor": "10"},
            },
        ]
        self.assertEqual({10}, superseded_message_ids(messages))

    def test_web_chat_and_explicit_local_delivery_are_not_coalesced(self):
        web_chat = {"id": 1, "kind": "web_chat", "meta": {"source": "ciel-runtime-web-chat"}}
        local = {"id": 2, "kind": "notice", "delivery": ["llm"], "meta": {"source": "local"}}
        self.assertIsNone(message_coalesce_key(web_chat))
        self.assertIsNone(message_coalesce_key(local))


if __name__ == "__main__":
    unittest.main()
