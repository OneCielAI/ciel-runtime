import json
import unittest

from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices,
    active_tool_call_from_text,
    active_turn_from_text,
    content_text,
    record_timestamp_seconds,
    queued_age_seconds_from_text,
    queued_command_ids_from_text,
    user_text,
    wake_state_from_text,
)


class ChannelTranscriptTests(unittest.TestCase):
    def wake_services(self):
        return ChannelWakeTranscriptServices(
            claim_prompt=lambda _message_id: "",
            prompt_references_message_id=lambda text, message_id, _prompts: f"#{message_id}" in text,
            prompt_message_ids=lambda text: {
                int(token[1:]) for token in text.split() if token.startswith("#") and token[1:].isdigit()
            },
            now=lambda: 100.0,
        )

    def test_content_and_user_records_are_protocol_neutral(self):
        record = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hello"}],
            },
        }
        self.assertEqual("hello", user_text(record))
        self.assertEqual("a\nb", content_text([{"text": "a"}, {"output_text": "b"}]))

    def test_tool_activity_tracks_calls_and_outputs(self):
        started = json.dumps(
            {"type": "response_item", "payload": {"type": "function_call", "call_id": "call-1"}}
        )
        completed = json.dumps(
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1"}}
        )
        self.assertTrue(active_tool_call_from_text(started))
        self.assertFalse(active_tool_call_from_text("\n".join((started, completed))))

    def test_turn_activity_and_timestamp_projection(self):
        started = json.dumps({"type": "event_msg", "payload": {"type": "turn_started"}})
        completed = json.dumps({"type": "event_msg", "payload": {"type": "turn_complete"}})
        self.assertTrue(active_turn_from_text(started))
        self.assertFalse(active_turn_from_text("\n".join((started, completed))))
        self.assertEqual(0.0, record_timestamp_seconds({"timestamp": "1970-01-01T00:00:00Z"}))

    def test_wake_state_and_queue_age_share_transcript_port(self):
        queued = json.dumps(
            {
                "type": "queue-operation",
                "operation": "enqueue",
                "content": "wake #7",
                "timestamp": 90,
            }
        )
        user = json.dumps({"type": "user", "message": {"role": "user", "content": "wake #7"}})
        assistant = json.dumps({"type": "assistant", "message": {"role": "assistant"}})
        services = self.wake_services()
        self.assertEqual("queued", wake_state_from_text(7, queued, None, services))
        self.assertEqual(10.0, queued_age_seconds_from_text(7, queued, None, services))
        self.assertEqual("pending", wake_state_from_text(7, "\n".join((queued, user)), None, services))
        self.assertEqual(
            "completed", wake_state_from_text(7, "\n".join((queued, user, assistant)), None, services)
        )
        self.assertEqual({7}, queued_command_ids_from_text(queued, services))


if __name__ == "__main__":
    unittest.main()
