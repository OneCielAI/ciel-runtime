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
                wake_state_evidence_from_text=lambda message_id, text, prompts=(), **_kwargs: (
                    calls.append((message_id, text, prompts))
                    or WakeStateEvidence("queued")
                ),
                queued_age_from_text=lambda message_id, text, prompts, **_kwargs: (
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

    def real_wake_services(self):
        # The production matcher: message-id references are authoritative,
        # prompt-text containment is the fallback for raw tty prompts.
        from ciel_runtime_support.channel_wake_claim_repository import (
            prompt_message_ids,
            prompt_references_message_id,
        )

        return ChannelWakeTranscriptServices(
            claim_prompt=lambda _message_id: "",
            prompt_references_message_id=prompt_references_message_id,
            prompt_message_ids=prompt_message_ids,
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
                wake_state_evidence_from_text=lambda message_id, text, prompts=(), **_kwargs: wake_state_evidence_from_text(
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

    def test_compact_continuation_and_local_commands_do_not_hold_a_turn_open(self):
        # After /compact, Claude Code's transcript ends on user-role records
        # (continuation summary, caveat, local-command echoes) with no
        # assistant response — the CLI is idle at the prompt. Counting them
        # as turn-opening input defers channel wakes forever.
        records = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "real work"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": []}}),
            # The raw slash command carries no distinguishing flag; the
            # following <command-name> echo is what proves it was local.
            json.dumps({"type": "user", "message": {"role": "user", "content": "/compact"}}),
            json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>Caveat</local-command-caveat>"}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "<command-name>/compact</command-name>"}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "<local-command-stdout>Compacted</local-command-stdout>"}}),
            json.dumps({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "This session is being continued…"}}),
        ]
        self.assertFalse(active_turn_from_text("\n".join(records)))
        typed = json.dumps({"type": "user", "message": {"role": "user", "content": "new question"}})
        self.assertTrue(active_turn_from_text("\n".join((*records, typed))))

    def test_compact_summary_echo_of_the_body_never_completes_a_wake(self):
        body = "[CIELARVIS voice recovery] Voice is unavailable"
        summary = json.dumps(
            {
                "type": "user",
                "isCompactSummary": True,
                "timestamp": "2026-08-19T05:10:00Z",
                "message": {"role": "user", "content": f"Summary quotes: {body}"},
            }
        )
        assistant = json.dumps({"type": "assistant", "message": {"role": "assistant"}})

        evidence = wake_state_evidence_from_text(
            109, "\n".join((summary, assistant)), [body], self.real_wake_services()
        )

        self.assertEqual("missing", evidence.state)

    def test_tool_result_echo_of_the_body_never_completes_a_wake(self):
        # An agent grepping logs prints past message bodies into tool results
        # (persisted as user-role records). Template messages repeat verbatim,
        # so counting those as delivery evidence silently drops re-sends.
        body = "[CIELARVIS voice recovery] Voice is unavailable"
        tool_echo = json.dumps(
            {
                "type": "user",
                "timestamp": "2026-08-18T04:25:55Z",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "=== id 104 " + body}
                    ],
                },
            }
        )
        assistant = json.dumps({"type": "assistant", "message": {"role": "assistant"}})

        evidence = wake_state_evidence_from_text(
            108, "\n".join((tool_echo, assistant)), [body], self.real_wake_services()
        )

        self.assertEqual("missing", evidence.state)

    def test_records_older_than_the_message_never_complete_its_wake(self):
        body = "[CIELARVIS voice recovery] Voice is unavailable"
        old_prompt = json.dumps(
            {
                "type": "user",
                "timestamp": "2026-08-18T02:33:00Z",
                "message": {"role": "user", "content": body},
            }
        )
        assistant = json.dumps({"type": "assistant", "message": {"role": "assistant"}})
        text = "\n".join((old_prompt, assistant))
        services = self.real_wake_services()
        from datetime import datetime, timezone

        created = datetime(2026, 8, 18, 4, 52, 44, tzinfo=timezone.utc).timestamp()
        stale = wake_state_evidence_from_text(
            108, text, [body], services, not_before=created - 5.0
        )
        self.assertEqual("missing", stale.state)
        # The identical prompt typed AFTER the message was created still counts.
        fresh_prompt = json.dumps(
            {
                "type": "user",
                "timestamp": "2026-08-18T04:53:10Z",
                "message": {"role": "user", "content": body},
            }
        )
        fresh = wake_state_evidence_from_text(
            108,
            "\n".join((fresh_prompt, assistant)),
            [body],
            services,
            not_before=created - 5.0,
        )
        self.assertEqual("completed", fresh.state)

    def test_reader_anchors_evidence_at_the_message_creation_time(self):
        self.assertEqual(
            995.0, ChannelWakeStateReader.message_not_before({"created_at_epoch": 1000.0})
        )
        self.assertIsNone(ChannelWakeStateReader.message_not_before({"id": 5}))

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
