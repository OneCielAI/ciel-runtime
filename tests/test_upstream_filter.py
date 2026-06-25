import ciel_runtime
import unittest

from ciel_runtime_support.transcript_filter import CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES


def converted_texts(messages):
    body = {"messages": messages}
    out = ciel_runtime.anthropic_messages_to_ollama(body)
    return [str(message.get("content", "")) for message in out]


def converted_openai_texts(messages):
    body = {"messages": messages}
    out = ciel_runtime.anthropic_messages_to_openai(body)
    return [str(message.get("content", "")) for message in out]


class UpstreamFilterTests(unittest.TestCase):
    def test_all_known_transcript_event_types_are_filtered(self):
        for event_type in CLAUDE_CODE_TRANSCRIPT_EVENT_TYPES:
            with self.subTest(event_type=event_type):
                messages = [
                    {"role": "user", "content": "real user prompt"},
                    {"type": event_type, "content": f"internal event text for {event_type}"},
                ]

                texts = converted_texts(messages)

                self.assertIn("real user prompt", texts)
                self.assertFalse(any("internal event text" in text for text in texts))

    def test_queue_operation_is_not_converted_to_user_prompt(self):
        messages = [
            {"role": "user", "content": "real user prompt"},
            {"type": "queue-operation", "operation": "enqueue", "content": "start Phase 2"},
        ]

        texts = converted_texts(messages)

        self.assertIn("real user prompt", texts)
        self.assertFalse(any("start Phase 2" in text for text in texts))

    def test_internal_event_with_stray_role_is_still_filtered(self):
        messages = [
            {"role": "user", "content": "real user prompt"},
            {"role": "user", "type": "queue-operation", "operation": "enqueue", "content": "queued continuation"},
        ]

        texts = converted_texts(messages)

        self.assertIn("real user prompt", texts)
        self.assertFalse(any("queued continuation" in text for text in texts))

    def test_role_bearing_queue_operation_is_filtered_on_openai_path(self):
        messages = [
            {"role": "user", "content": "real user prompt"},
            {"role": "assistant", "type": "queue-operation", "operation": "enqueue", "content": "queued continuation"},
        ]

        texts = converted_openai_texts(messages)

        self.assertIn("real user prompt", texts)
        self.assertFalse(any("queued continuation" in text for text in texts))

    def test_roleless_transcript_wrapper_is_filtered(self):
        messages = [
            {"type": "user", "message": {"role": "user", "content": "wrapped transcript prompt"}},
            {"role": "user", "content": [{"type": "text", "text": "real prompt"}]},
        ]

        texts = converted_texts(messages)

        self.assertTrue(any("real prompt" in text for text in texts))
        self.assertFalse(any("wrapped transcript prompt" in text for text in texts))

    def test_valid_anthropic_messages_are_preserved(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "README.md"}},
                ],
            },
        ]

        out = ciel_runtime.anthropic_messages_to_ollama({"messages": messages})

        self.assertTrue(any(message.get("role") == "user" and "hello" in str(message.get("content")) for message in out))
        tool_call_messages = [message for message in out if message.get("role") == "assistant" and message.get("tool_calls")]
        self.assertEqual(1, len(tool_call_messages))
        self.assertEqual("Read", tool_call_messages[0]["tool_calls"][0]["function"]["name"])

    def test_persisted_tool_output_is_not_rewritten_on_ollama_path(self):
        persisted = (
            "<persisted-output>\n"
            "Output too large (55.5KB). Full output saved to: /tmp/tool-results/toolu_1.json\n\n"
            "Preview (first 2KB):\n"
            "{\"ok\": true}\n"
            "</persisted-output>"
        )
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "checkin", "input": {"ack_previous": True}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": persisted},
                ],
            },
        ]

        out = ciel_runtime.anthropic_messages_to_ollama({"messages": messages})

        tool_messages = [message for message in out if message.get("role") == "tool"]
        self.assertEqual(1, len(tool_messages))
        self.assertEqual(persisted, tool_messages[0]["content"])
        self.assertFalse(any("Tool `checkin` completed successfully" in str(message.get("content", "")) for message in out))

    def test_persisted_tool_output_is_not_rewritten_on_openai_path(self):
        persisted = (
            "<persisted-output>\n"
            "Output too large (55.5KB). Full output saved to: /tmp/tool-results/toolu_1.json\n\n"
            "Preview (first 2KB):\n"
            "{\"ok\": true}\n"
            "</persisted-output>"
        )
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "checkin", "input": {"ack_previous": True}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": persisted},
                ],
            },
        ]

        out = ciel_runtime.anthropic_messages_to_openai({"messages": messages})

        tool_messages = [message for message in out if message.get("role") == "tool"]
        self.assertEqual(1, len(tool_messages))
        self.assertEqual(persisted, tool_messages[0]["content"])
        self.assertFalse(any("Tool `checkin` completed successfully" in str(message.get("content", "")) for message in out))


if __name__ == "__main__":
    unittest.main()
