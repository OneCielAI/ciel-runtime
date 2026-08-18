import json
import unittest

from ciel_runtime_support.channel_transcript import (
    ChannelWakeStateReader,
    ChannelWakeStateReaderPorts,
    ChannelWakeTranscriptServices,
    WakeStateEvidence,
    active_tool_call_from_text,
    active_turn_from_text,
    content_text,
    record_timestamp_seconds,
    queued_age_seconds_from_text,
    queued_command_ids_from_text,
    user_text,
    wake_state_from_text,
    wake_state_evidence_from_text,
)


class ChannelTranscriptTests(unittest.TestCase):
    def test_wake_state_reader_projects_message_prompts_and_staleness(self):
        calls = []
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: "transcript.jsonl",
                read_tail_text=lambda _path: "tail",
                wake_state_evidence_from_text=lambda message_id, text, prompts=(): (
                    calls.append((message_id, text, prompts))
                    or WakeStateEvidence("queued")
                ),
                queued_age_from_text=lambda message_id, text, prompts: (
                    calls.append((message_id, text, prompts)) or 31.0
                ),
                stale_seconds=lambda: 30.0,
                log=lambda *_args: None,
            )
        )
        message = {"id": "7", "message": "body"}

        self.assertEqual("queued", reader.state_for_message(message, "rendered"))
        self.assertTrue(reader.queued_is_stale(message, "rendered"))
        self.assertEqual(("rendered", "body"), calls[0][2])

    def test_wake_state_reader_handles_invalid_ids_and_missing_transcript(self):
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: None,
                read_tail_text=lambda _path: self.fail("missing transcript must not be read"),
                wake_state_evidence_from_text=lambda *_args: self.fail("missing transcript must not be parsed"),
                queued_age_from_text=lambda *_args: self.fail("missing transcript must not be parsed"),
                stale_seconds=lambda: 30.0,
                log=lambda *_args: None,
            )
        )

        self.assertEqual("completed", reader.state_for_message({"id": "invalid"}))
        self.assertEqual("unknown", reader.state(7))
        self.assertFalse(reader.queued_is_stale({"id": 7}))

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

    def test_claude_turn_stays_active_across_tool_results_until_end_turn(self):
        records = (
            json.dumps({"type": "user", "message": {"role": "user", "content": "work"}}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": "tool-1"}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "tool-1"}],
                    },
                }
            ),
        )
        self.assertTrue(active_turn_from_text("\n".join(records)))
        end_turn = json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "stop_reason": "end_turn", "content": []},
            }
        )
        self.assertFalse(active_turn_from_text("\n".join((*records, end_turn))))

    def test_claude_turn_duration_closes_turn_without_end_turn_record(self):
        user = json.dumps({"type": "user", "message": {"role": "user", "content": "work"}})
        duration = json.dumps({"type": "system", "subtype": "turn_duration"})
        self.assertFalse(active_turn_from_text("\n".join((user, duration))))

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

    def test_completed_state_logs_matching_transcript_records(self):
        logs = []
        transcript = "\n".join(
            (
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "supervisor-session",
                        "timestamp": "2026-08-18T04:58:35Z",
                        "message": {"role": "user", "content": "wake #70"},
                    }
                ),
                json.dumps(
                    {"type": "assistant", "message": {"role": "assistant"}}
                ),
            )
        )
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: "supervisor.jsonl",
                read_tail_text=lambda _path: transcript,
                wake_state_evidence_from_text=lambda message_id, text, prompts=(): wake_state_evidence_from_text(
                    message_id, text, prompts, self.wake_services()
                ),
                queued_age_from_text=lambda *_args: None,
                stale_seconds=lambda: 30.0,
                log=lambda level, message: logs.append((level, message)),
            )
        )

        self.assertEqual("completed", reader.state(70))
        self.assertEqual(1, len(logs))
        self.assertIn("transcript=supervisor.jsonl", logs[0][1])
        self.assertIn("prompt_record=1", logs[0][1])
        self.assertIn("completion_record=2", logs[0][1])
        self.assertIn("session_id=supervisor-session", logs[0][1])

    def test_prompt_candidates_prevent_incidental_id_from_completing_wake(self):
        transcript = "\n".join(
            (
                json.dumps(
                    {
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": "source code mentions id=70 but is unrelated",
                        },
                    }
                ),
                json.dumps(
                    {"type": "assistant", "message": {"role": "assistant"}}
                ),
            )
        )

        evidence = wake_state_evidence_from_text(
            70,
            transcript,
            ["[ciel-runtime external channel message] id=70 text=actual"],
            self.wake_services(),
        )

        self.assertEqual("missing", evidence.state)


if __name__ == "__main__":
    unittest.main()
