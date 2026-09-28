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
    wake_states_from_text,
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
                queued_dropped_from_text=lambda message_id, text, prompts, **_kwargs: True,
                stale_seconds=lambda: 30.0,
                log=lambda *_args: None,
            )
        )
        message = {"id": "7", "message": "body"}

        self.assertEqual("queued", reader.state_for_message(message, "rendered"))
        self.assertTrue(reader.queued_is_stale(message, "rendered"))
        self.assertEqual(("rendered", "body"), calls[0][2])

    def test_queued_command_without_removal_evidence_is_not_stale(self):
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: "transcript.jsonl",
                read_tail_text=lambda _path: "tail",
                wake_state_evidence_from_text=lambda *_args, **_kwargs: WakeStateEvidence("queued"),
                queued_age_from_text=lambda *_args, **_kwargs: 3600.0,
                queued_dropped_from_text=lambda *_args, **_kwargs: False,
                stale_seconds=lambda: 30.0,
                log=lambda *_args: None,
            )
        )

        self.assertFalse(reader.queued_is_stale({"id": 7, "message": "body"}, "rendered"))

    def test_wake_state_reader_handles_invalid_ids_and_missing_transcript(self):
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: None,
                read_tail_text=lambda _path: self.fail("missing transcript must not be read"),
                wake_state_evidence_from_text=lambda *_args: self.fail("missing transcript must not be parsed"),
                queued_age_from_text=lambda *_args: self.fail("missing transcript must not be parsed"),
                queued_dropped_from_text=lambda *_args: self.fail("missing transcript must not be parsed"),
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

    def test_turn_from_a_dead_session_does_not_stay_active_after_relaunch(self):
        # The console was killed inside a tool call: the transcript keeps an
        # assistant tool_use with no result, and the resumed session appends to
        # the same file. That stale record must not defer wakes forever.
        killed = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T08:16:16Z",
                "message": {
                    "role": "assistant",
                    "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tool-9", "name": "Bash"}],
                },
            }
        )
        resumed_bookkeeping = json.dumps({"type": "last-prompt", "leafUuid": "abc"})
        text = "\n".join((killed, resumed_bookkeeping))
        launched_at = 1787128000.0  # after the killed record, before the relaunch turn
        self.assertTrue(active_tool_call_from_text(text))
        self.assertFalse(active_tool_call_from_text(text, not_before=launched_at))
        self.assertTrue(active_turn_from_text(text))
        self.assertFalse(active_turn_from_text(text, not_before=launched_at))
        # A turn opened after the relaunch is still reported as running.
        fresh = json.dumps(
            {
                "type": "user",
                "timestamp": "2026-08-19T09:00:00Z",
                "message": {"role": "user", "content": "work"},
            }
        )
        self.assertTrue(active_turn_from_text("\n".join((text, fresh)), not_before=launched_at))
        fresh_tool = json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T09:00:01Z",
                "message": {
                    "role": "assistant",
                    "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "id": "tool-10", "name": "Bash"}],
                },
            }
        )
        self.assertTrue(
            active_tool_call_from_text(
                "\n".join((text, fresh, fresh_tool)), not_before=launched_at
            )
        )

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
                queued_dropped_from_text=lambda *_args: False,
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

    def batch_transcript(self) -> str:
        def queue(operation, ids):
            return {"type": "queue-operation", "operation": operation, "content": f"[walkie] ids={ids} body"}

        def handed_over(ids):
            return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": f"[walkie] ids={ids} body"}}

        def typed(content, **extra):
            return {"type": "user", "message": {"role": "user", "content": content}, **extra}

        assistant = {"type": "assistant", "message": {"role": "assistant", "content": "ok"}}
        records = [
            assistant,
            queue("enqueue", "1"), queue("remove", "1"), handed_over("1"), assistant,  # completed
            queue("enqueue", "2"),  # queued, never taken
            handed_over("3"), assistant,  # handed over without a remove: missing
            typed("[walkie] id=5 hello"), assistant,  # completed
            queue("enqueue", "8,9"), queue("remove", "8,9"), handed_over("8,9"), assistant,  # completed
            {"type": "queue-operation", "operation": "popAll", "content": "[walkie] id=10"},  # not evidence
            typed([{"type": "tool_result", "tool_use_id": "t", "content": "log: id=6 id=12"}]), assistant,
            typed("summary mentions id=12", isCompactSummary=True), assistant,
            "not json",
            typed("raw tty prompt for eleven"), assistant,  # 11 completes only through its claimed prompt
            queue("enqueue", "13"), typed("[walkie] id=13 again"),  # pending: no answer yet
            queue("enqueue", "4"), queue("remove", "4"),  # queued (removed, never handed over)
        ]
        return "\n".join(item if isinstance(item, str) else json.dumps(item) for item in records)

    def test_batch_wake_states_equal_the_per_id_states(self):
        from ciel_runtime_support.channel_wake_claim_repository import (
            prompt_message_ids,
            prompt_references_message_id,
        )

        text = self.batch_transcript()
        services = ChannelWakeTranscriptServices(
            claim_prompt=lambda message_id: "raw tty prompt for eleven" if message_id == 11 else "",
            prompt_references_message_id=prompt_references_message_id,
            prompt_message_ids=prompt_message_ids,
            now=lambda: 100.0,
        )
        ids = [0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 99]

        batch = wake_states_from_text(ids, text, services)

        self.assertEqual({message_id: wake_state_from_text(message_id, text, None, services) for message_id in ids}, batch)
        self.assertEqual(
            {0: "completed", 1: "completed", 2: "queued", 3: "missing", 4: "queued", 5: "completed", 6: "missing",
             8: "completed", 9: "completed", 10: "missing", 11: "completed", 12: "missing", 13: "pending", 99: "missing"},
            batch,
        )

    def test_batch_wake_states_read_each_record_once(self):
        from ciel_runtime_support.channel_wake_claim_repository import prompt_message_ids

        calls = []
        services = ChannelWakeTranscriptServices(
            claim_prompt=lambda _message_id: "",
            prompt_references_message_id=lambda *_args: self.fail("per-id matching must not run"),
            prompt_message_ids=lambda text: calls.append(text) or prompt_message_ids(text),
            now=lambda: 100.0,
        )
        text = self.batch_transcript()

        wake_states_from_text(list(range(1, 300)), text, services)

        self.assertLessEqual(len(calls), text.count("\n") + 1)

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
