"""Walkie delivery regressions (2026-09-23, router instance 9469).

Distinct Walkie messages were dropped as superseded notices, each busy period
injected one message, and session-socket deliveries were recorded as
stale_unconfirmed although the session answered them.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import ciel_runtime
from ciel_runtime_support.channel_message_policy import message_coalesce_key, superseded_message_ids
from ciel_runtime_support.channel_transcript import is_non_turn_user_record, is_typed_user_prompt, queued_dropped_from_text
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository


def _walkie_event(message_id: int, room: str, seq: int, text: str) -> dict:
    """Shape copied from runtime-inputs.jsonl of instance 9469 (message 707)."""

    return {
        "id": message_id,
        "channel": "external:default",
        "sender_id": f"http://walkie/conversations/{room}",
        "recipients": ["all"],
        "thread_id": f"{room}:{seq}",
        "kind": "external_event",
        "message": text,
        "visibility": "private_runtime",
        "delivery": ["llm"],
        "meta": {
            "source": "ciel-runtime-external-event",
            "source_kind": "external_event",
            "receiver_id": "default",
            "transport": "sse",
            "event_id": f"{room}:{seq}",
            "event_type": "com.ciel.walkie.support.message",
            "event_source": f"http://walkie/conversations/{room}",
            "transport_event_id": str(seq),
            "input_transport": "session_socket",
            "raw_preserved": True,
        },
    }


class WalkieSupersedeTests(unittest.TestCase):
    def test_distinct_external_events_are_never_superseded(self):
        messages = [
            _walkie_event(707, "room-a", 6859, "thread depth 10 limit"),
            _walkie_event(708, "room-a", 6862, "thread depth 30"),
            _walkie_event(709, "room-b", 6863, "github notice"),
        ]
        self.assertIsNone(message_coalesce_key(messages[0]))
        self.assertEqual(set(), superseded_message_ids(messages))

    def test_progress_notices_without_an_event_id_still_coalesce(self):
        messages = [
            {"id": 10, "kind": "notification", "meta": {"sse_source": "jobs", "sse_event": "progress", "cursor": "9"}},
            {"id": 11, "kind": "notification", "meta": {"sse_source": "jobs", "sse_event": "progress", "cursor": "10"}},
        ]
        self.assertEqual({10}, superseded_message_ids(messages))


class SessionSocketConfirmationTests(unittest.TestCase):
    def test_peer_origin_meta_record_is_a_typed_turn(self):
        # Record shape Claude Code wrote for session-socket message 669.
        record = {
            "type": "user",
            "isMeta": True,
            "origin": {"kind": "peer"},
            "message": {
                "role": "user",
                "content": "Another Claude session sent a message:\n[ciel-runtime external channel message] id=669",
            },
        }
        self.assertFalse(is_non_turn_user_record(record))
        self.assertTrue(is_typed_user_prompt(record))

    def test_queued_command_handed_to_a_running_turn_is_delivered_not_dropped(self):
        # Record order Claude Code wrote for message 714 (records 38460-38472).
        prompt = "[ciel-runtime external channel message] channel=external:default id=714 text=\"tag push\""
        transcript = "\n".join(
            json.dumps(record)
            for record in (
                {"type": "queue-operation", "operation": "enqueue", "content": prompt, "timestamp": "2026-09-23T16:20:10.341Z"},
                {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use", "content": []}},
                {"type": "queue-operation", "operation": "remove", "content": prompt, "timestamp": "2026-09-23T16:20:14.830Z"},
                {"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt}, "timestamp": "2026-09-23T16:20:10.340Z"},
                {"type": "assistant", "message": {"role": "assistant", "stop_reason": "tool_use", "content": []}},
            )
        ) + "\n"
        context = ciel_runtime.channel_wake_context()
        self.assertEqual("completed", context.wake_state_from_text(714, transcript))
        self.assertFalse(queued_dropped_from_text(714, transcript, [], context.transcript_services()))

    def test_plain_meta_records_remain_bookkeeping(self):
        record = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "<local-command-caveat>"}}
        self.assertTrue(is_non_turn_user_record(record))


class SkippedStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.events: list[dict] = []
        self.repo = RuntimeInputStatusRepository(
            Path(self._tmp.name) / "status.jsonl",
            lambda **kwargs: self.events.append(kwargs),
            lambda _level, _message: None,
            threading.RLock(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_queued_input_can_end_as_skipped_with_a_reason(self):
        self.repo.transition(5, "queued")
        record = self.repo.transition(5, "skipped", reason="superseded_channel_notice")
        self.assertEqual(("skipped", "superseded_channel_notice"), (record["status"], record["reason"]))
        self.assertEqual("skipped", self.repo.transition(5, "submitted").get("status"))

    def test_skipping_an_untracked_message_writes_nothing(self):
        self.assertEqual({}, self.repo.transition(9, "skipped", reason="superseded_channel_notice"))
        self.assertIsNone(self.repo.get(9))


class SessionSocketBatchTests(unittest.TestCase):
    def tearDown(self) -> None:
        for message_id in (707, 708, 709):
            ciel_runtime._CHANNEL_STDIN_WAKE_DELIVERED.discard(message_id)
            ciel_runtime._CHANNEL_STDIN_WAKE_PROMPTS.pop(message_id, None)
            ciel_runtime._CHANNEL_STDIN_WAKE_BATCHES.pop(message_id, None)

    def test_messages_held_behind_a_turn_go_in_as_one_submission(self):
        messages = [
            _walkie_event(707, "room-a", 6859, "first request"),
            _walkie_event(708, "room-a", 6862, "second request"),
            _walkie_event(709, "room-b", 6863, "third request"),
        ]
        with (
            mock.patch.object(ciel_runtime, "read_chat_messages", return_value=messages),
            mock.patch.object(ciel_runtime, "_channel_stdin_recover_cursor_from_queued_only", return_value=706),
            mock.patch.object(ciel_runtime, "_channel_stdin_active_turn", return_value=False),
            mock.patch.object(ciel_runtime, "_channel_stdin_active_tool_call", return_value=False),
            mock.patch.object(ciel_runtime._CLAUDE_SESSION_SOCKET, "send", return_value=True) as send,
            mock.patch.object(ciel_runtime, "_write_fd_all") as write_all,
            mock.patch.object(ciel_runtime, "router_log") as router_log,
        ):
            last_id = ciel_runtime._inject_pending_channel_messages(99, 706, commit_cursor=False)

        self.assertEqual(709, last_id)
        send.assert_called_once()
        prompt = send.call_args.args[0]
        for text in ("first request", "second request", "third request"):
            self.assertIn(text, prompt)
        write_all.assert_not_called()
        logged = [str(call.args[1]) for call in router_log.call_args_list if len(call.args) > 1]
        self.assertTrue(any("channel_stdin_proxy_injected count=3 message_ids=707,708,709" in line for line in logged))
        self.assertFalse(any("superseded" in line for line in logged))


if __name__ == "__main__":
    unittest.main()
