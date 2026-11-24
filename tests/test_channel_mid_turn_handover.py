"""Mid-turn session-socket input as Claude Code 2.1.331 records it.

Betty (2026-11-23/24, beta): a session-socket input that arrives during a
running turn is recorded as

1. ``queue-operation enqueue`` with no ``content`` at the moment it arrives,
2. later, when the turn absorbs it, a ``queued_command`` attachment for the
   input (stamped with the enqueue time) and only then
3. ``queue-operation remove`` with the content and ``reason=absorbed_mid_turn``.

All 31 inputs the router marked ``unseen_failed`` in two Betty router logs had
steps 2 and 3 in the transcript; 47 of 53 mid-turn hand-overs since 11-23 wrote
the attachment before the remove. The record shapes below are copied from that
transcript with the external text replaced.
"""

import json
import unittest
from datetime import datetime, timezone

from ciel_runtime_support.channel_inflight import (
    ChannelInflightEffects,
    ChannelInflightPolicy,
    ChannelInflightSnapshot,
    advance_channel_inflight,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeStateReader,
    ChannelWakeStateReaderPorts,
    ChannelWakeTranscriptServices,
    queue_holds_unattributed_input,
    queued_age_seconds_from_text,
    queued_dropped_from_text,
    wake_state_evidence_from_text,
    wake_state_from_text,
    wake_states_from_text,
)
from ciel_runtime_support.channel_wake_claim_repository import (
    prompt_message_ids,
    prompt_references_message_id,
)

SESSION = "00000000-0000-4000-8000-000000000001"


def epoch(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def prompt(message_id: int) -> str:
    return (
        "[ciel-runtime external channel message] channel=external:default room=external:default "
        f"from=https://walkie.invalid/conversations/c1 id={message_id} thread=c1:1 "
        'metadata={"event_type":"com.ciel.walkie.support.message"}\nsupport body'
    )


def enqueue(at: str) -> dict:
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": at, "sessionId": SESSION}


def dequeue(at: str) -> dict:
    return {"type": "queue-operation", "operation": "dequeue", "timestamp": at, "sessionId": SESSION}


def handed_over(message_id: int, enqueued_at: str) -> dict:
    return {
        "parentUuid": "00000000-0000-4000-8000-0000000000aa",
        "isSidechain": False,
        "attachment": {
            "type": "queued_command",
            "prompt": prompt(message_id),
            "source_uuid": f"00000000-0000-4000-8000-{message_id:012d}",
            "delivery_id": f"00000000-0000-4000-9000-{message_id:012d}",
            "commandMode": "prompt",
            "origin": {"kind": "peer", "from": "unknown"},
            "timestamp": enqueued_at,
            "isMeta": True,
        },
        "type": "attachment",
        "uuid": f"00000000-0000-4000-a000-{message_id:012d}",
        "timestamp": enqueued_at,
        "sessionId": SESSION,
        "version": "2.1.331",
    }


def absorbed(message_id: int, at: str) -> dict:
    return {
        "type": "queue-operation",
        "operation": "remove",
        "timestamp": at,
        "sessionId": SESSION,
        "content": prompt(message_id),
        "reason": "absorbed_mid_turn",
        "commandUuid": f"00000000-0000-4000-8000-{message_id:012d}",
        "deliveryId": f"00000000-0000-4000-9000-{message_id:012d}",
    }


def dropped(message_id: int, at: str) -> dict:
    return {"type": "queue-operation", "operation": "remove", "timestamp": at, "sessionId": SESSION,
            "content": prompt(message_id)}


def tool_result(at: str) -> dict:
    return {"type": "user", "timestamp": at, "sessionId": SESSION,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def assistant(at: str) -> dict:
    return {"type": "assistant", "timestamp": at, "sessionId": SESSION,
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}}


def task_notification(operation: str, at: str) -> dict:
    return {"type": "queue-operation", "operation": operation, "timestamp": at, "sessionId": SESSION,
            "content": "<task-notification>\n<task-id>b1</task-id>\n</task-notification>"}


def text(*records) -> str:
    return "\n".join(json.dumps(record) for record in records)


def services(now: float = 0.0) -> ChannelWakeTranscriptServices:
    return ChannelWakeTranscriptServices(
        claim_prompt=lambda _message_id: "",
        prompt_references_message_id=prompt_references_message_id,
        prompt_message_ids=prompt_message_ids,
        now=lambda: now,
    )


# Betty message 7866, times as recorded.
SENT = "2026-11-24T06:12:43.700Z"
QUEUED = "2026-11-24T06:12:43.812Z"
ABSORBED = "2026-11-24T06:13:36.278Z"
REPLIED = "2026-11-24T06:14:19.105Z"
WAITING = (enqueue(QUEUED), tool_result("2026-11-24T06:13:16.506Z"), assistant("2026-11-24T06:13:18.133Z"))
HANDED = (*WAITING, handed_over(7866, QUEUED), absorbed(7866, ABSORBED))
ANSWERED = (*HANDED, assistant(REPLIED))


class MidTurnHandoverEvidenceTests(unittest.TestCase):
    def test_attachment_before_absorbing_remove_is_delivery(self):
        self.assertEqual("pending", wake_state_from_text(7866, text(*HANDED), None, services()))
        evidence = wake_state_evidence_from_text(7866, text(*ANSWERED), None, services())
        self.assertEqual("completed", evidence.state)
        self.assertEqual(4, evidence.prompt_record)
        self.assertEqual(6, evidence.completion_record)

    def test_attachment_before_remove_respects_the_evidence_horizon(self):
        after = epoch(QUEUED) + 60.0
        self.assertNotEqual(
            "completed", wake_state_from_text(7866, text(*ANSWERED), None, services(), not_before=after)
        )
        before = epoch(SENT) - 5.0
        self.assertEqual(
            "completed", wake_state_from_text(7866, text(*ANSWERED), None, services(), not_before=before)
        )

    def test_batch_states_read_the_same_hand_over(self):
        records = (*HANDED, handed_over(7867, QUEUED), absorbed(7867, ABSORBED), assistant(REPLIED))
        batch = wake_states_from_text([7866, 7867], text(*records), services())
        self.assertEqual({7866: "completed", 7867: "completed"}, batch)
        self.assertEqual(
            batch, {message_id: wake_state_from_text(message_id, text(*records), None, services()) for message_id in batch}
        )

    def test_absorbed_input_is_not_a_dropped_input(self):
        self.assertFalse(queued_dropped_from_text(7866, text(*ANSWERED), None, services()))

    def test_a_remove_without_hand_over_is_still_a_drop(self):
        records = (enqueue(QUEUED), dropped(7866, ABSORBED))
        self.assertEqual("queued", wake_state_from_text(7866, text(*records), None, services()))
        self.assertTrue(queued_dropped_from_text(7866, text(*records), None, services()))
        self.assertEqual({7866: "queued"}, wake_states_from_text([7866], text(*records), services()))

    def test_a_dropped_contentless_entry_is_aged_from_its_remove(self):
        # The fix keeps such an input in flight instead of failing it at 20 s,
        # so the drop must still be ageable for the stale-queue release.
        records = (enqueue(QUEUED), dropped(7866, ABSORBED))
        age = queued_age_seconds_from_text(7866, text(*records), None, services(epoch(ABSORBED) + 200.0))
        self.assertAlmostEqual(200.0, age, places=3)
        older = (enqueue(QUEUED), dict(enqueue(QUEUED), content=prompt(7866)), dropped(7866, ABSORBED))
        age = queued_age_seconds_from_text(7866, text(*older), None, services(epoch(ABSORBED) + 200.0))
        self.assertAlmostEqual(200.0 + epoch(ABSORBED) - epoch(QUEUED), age, places=3)

    def test_a_hand_over_without_any_remove_is_not_delivery(self):
        records = (enqueue(QUEUED), handed_over(7866, QUEUED), assistant(REPLIED))
        self.assertEqual("missing", wake_state_from_text(7866, text(*records), None, services()))
        self.assertEqual({7866: "missing"}, wake_states_from_text([7866], text(*records), services()))


class UnattributedQueueEntryTests(unittest.TestCase):
    def test_contentless_enqueue_after_the_send_is_held(self):
        self.assertTrue(queue_holds_unattributed_input(text(*WAITING), epoch(SENT)))

    def test_entry_older_than_the_send_does_not_count(self):
        self.assertFalse(queue_holds_unattributed_input(text(*WAITING), epoch(QUEUED) + 10.0))

    def test_idle_dequeue_takes_the_entry(self):
        records = (enqueue(QUEUED), dequeue("2026-11-24T06:12:43.830Z"))
        self.assertFalse(queue_holds_unattributed_input(text(*records), epoch(SENT)))

    def test_absorbing_remove_takes_the_entry(self):
        self.assertFalse(queue_holds_unattributed_input(text(*HANDED), epoch(SENT)))

    def test_content_bearing_entries_never_count_or_take(self):
        only_notification = (task_notification("enqueue", QUEUED),)
        self.assertFalse(queue_holds_unattributed_input(text(*only_notification), epoch(SENT)))
        records = (*WAITING, task_notification("enqueue", "2026-11-24T06:13:00.000Z"),
                   task_notification("remove", "2026-11-24T06:13:05.000Z"))
        self.assertTrue(queue_holds_unattributed_input(text(*records), epoch(SENT)))

    def test_two_sends_need_two_exits(self):
        records = (enqueue(QUEUED), enqueue("2026-11-24T06:12:44.987Z"), handed_over(7866, QUEUED),
                   absorbed(7866, ABSORBED))
        self.assertTrue(queue_holds_unattributed_input(text(*records), epoch(SENT)))

    def test_pop_all_empties_the_queue(self):
        records = (*WAITING, {"type": "queue-operation", "operation": "popAll", "timestamp": ABSORBED})
        self.assertFalse(queue_holds_unattributed_input(text(*records), epoch(SENT)))


class InflightUnseenRuleTests(unittest.TestCase):
    def setUp(self):
        self.released = []
        self.completed = []
        self.logs = []

    def effects(self, holds=None):
        return ChannelInflightEffects(
            commit_cursor=lambda _cursor: None,
            complete_wake=self.completed.append,
            release_wake=self.released.append,
            ensure_cursor=lambda: 7865,
            log=lambda level, message: self.logs.append((level, message)),
            queue_holds_input=holds,
        )

    def policy(self):
        return ChannelInflightPolicy(
            unseen_retry_seconds=20.0, waiting_log_interval=30.0, is_stale=lambda *_args: False,
            commit_cursor_on_stale=False, log_namespace="channel_test", stale_event="stale_inflight",
        )

    def snapshot(self, state, now):
        sent = epoch(SENT)
        return ChannelInflightSnapshot(7866, 7866, state, sent, sent, sent + now, 7865)

    def test_held_queue_entry_keeps_the_input_in_flight(self):
        seen = []
        update = advance_channel_inflight(
            self.snapshot("missing", 25.0), self.policy(), self.effects(lambda since: seen.append(since) or True)
        )
        self.assertEqual(7866, update.message_id)
        self.assertEqual([], self.released)
        self.assertEqual([epoch(SENT)], seen)
        self.assertFalse(any("unseen_failed" in message for _level, message in self.logs))

    def test_nothing_held_still_fails_unseen(self):
        for holds in (None, lambda _since: False):
            with self.subTest(port=holds is not None):
                self.released.clear()
                update = advance_channel_inflight(self.snapshot("missing", 25.0), self.policy(), self.effects(holds))
                self.assertEqual("failed", update.action)
                self.assertEqual([7866], self.released)

    def test_reader_follows_betty_7866_to_completion(self):
        stages = {"text": ""}
        reader = ChannelWakeStateReader(
            ChannelWakeStateReaderPorts(
                latest_transcript=lambda: "transcript.jsonl",
                read_tail_text=lambda _path: stages["text"],
                wake_state_evidence_from_text=lambda message_id, body, prompts=(), **kwargs: wake_state_evidence_from_text(
                    message_id, body, prompts, services(), **kwargs
                ),
                queued_age_from_text=lambda *_args, **_kwargs: None,
                queued_dropped_from_text=lambda *_args, **_kwargs: False,
                stale_seconds=lambda: 180.0,
                log=lambda *_args: None,
            )
        )
        effects = self.effects(reader.queue_holds_input_since)
        timeline = ((WAITING, 25.0, 7866), (HANDED, 53.0, 7866), (ANSWERED, 96.0, None))
        for records, age, expected in timeline:
            with self.subTest(age=age):
                stages["text"] = text(*records)
                update = advance_channel_inflight(
                    self.snapshot(reader.state(7866), age), self.policy(), effects
                )
                self.assertEqual(expected, update.message_id)
        self.assertEqual([], self.released)
        self.assertEqual([7866], self.completed)


if __name__ == "__main__":
    unittest.main()
