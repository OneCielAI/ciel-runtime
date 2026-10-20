"""Synthetic child-epoch ownership regression tests (no live runtime)."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import concurrent.futures
import errno

from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository, WakeAttemptConflict
from ciel_runtime_support.channel_wake_delivery_repository import ChannelWakeDeliveryRepository, DeliveryAttempts
from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices, wake_state_evidence_from_text, wake_states_from_text,
)
from ciel_runtime_support.channel_wake_claim_repository import prompt_message_ids, prompt_references_message_id


class InterruptedAttemptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.status = RuntimeInputStatusRepository(
            Path(self.directory.name) / "status.jsonl", lambda **kw: None,
            lambda *args: None, threading.RLock(),
        )

    def test_verified_exit_quarantines_batch_and_fences_late_completion(self):
        attempt = self.status.begin_attempt([3801, 3802], "old-child")
        self.assertEqual([], self.status.reconcile_child_exit("old-child", verified_exit=False))
        self.assertEqual([], self.status.reconcile_child_exit("new-child", verified_exit=True))
        self.assertEqual([3801, 3802], self.status.reconcile_child_exit("old-child", verified_exit=True))
        for message_id in (3801, 3802):
            row = self.status.transition(message_id, "replied", expected_attempt=attempt)
            self.assertEqual("interrupted_uncertain", row["status"])
            self.assertEqual([3801, 3802], row["data"]["batch_ids"])
        self.status.begin_attempt([3803], "new-child")
        self.assertEqual("submitted", self.status.get(3803)["status"])
        self.assertEqual(2, len(self.status.list_latest(status="interrupted_uncertain")))

    def test_batch_conflict_writes_no_partial_ownership(self):
        self.status.transition(40, "queued")
        old = self.status.begin_attempt([41], "old")
        self.status.transition(41, "replied", expected_attempt=old)
        before = self.status.path.read_bytes()
        with self.assertRaises(WakeAttemptConflict) as caught:
            self.status.begin_attempt([40, 41], "new")
        self.assertEqual(41, caught.exception.request_id)
        self.assertEqual(before, self.status.path.read_bytes())
        self.assertEqual("queued", self.status.get(40)["status"])

    def test_existing_ownership_is_never_replaced(self):
        old = self.status.begin_attempt([40], "old")
        for epoch in ("old", "new"):
            with self.subTest(epoch=epoch), self.assertRaises(WakeAttemptConflict):
                self.status.begin_attempt([40], epoch)
            self.assertEqual(old, self.status.get(40)["data"]["attempt_id"])

    def test_corrupt_history_blocks_admission_without_a_write(self):
        self.status.transition(40, "queued")
        with self.status.path.open("a") as stream:
            stream.write("{incomplete")
        before = self.status.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.status.begin_attempt([40], "child")
        self.assertEqual(before, self.status.path.read_bytes())

    def test_two_repository_instances_admit_exactly_one_owner(self):
        other = RuntimeInputStatusRepository(
            self.status.path, lambda **_: None, lambda *_: None, threading.RLock())
        self.status.transition(40, "queued")
        barrier = threading.Barrier(2)

        def attempt(repository):
            barrier.wait(timeout=5)
            try:
                return repository.begin_attempt([40], "child")
            except WakeAttemptConflict:
                return None

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, (self.status, other)))
        winners = [value for value in results if value is not None]
        self.assertEqual(1, len(winners))
        self.assertEqual(winners[0], self.status.get(40)["data"]["attempt_id"])

    def test_lock_error_after_first_write_is_not_safe_to_retry(self):
        from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
        for message_id in (40, 41):
            self.status.transition(message_id, "queued")
        record = self.status._record
        calls = 0

        def fail_late(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ChannelFileLockBusy(errno.EACCES, "busy")
            return record(*args)

        with mock.patch.object(RuntimeInputStatusRepository, "_record", side_effect=fail_late):
            with self.assertRaises(RuntimeError) as caught:
                self.status.begin_attempt([40, 41], "child")
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)

    def test_fence_rejects_other_attempt(self):
        self.status.begin_attempt([9], "child")
        row = self.status.transition(9, "failed", expected_attempt="foreign")
        self.assertEqual("submitted", row["status"])

    def test_incomplete_history_does_not_release_claims_or_prompts(self):
        cleared = []
        delivery = ChannelWakeDeliveryRepository(threading.RLock(), {9}, {9: "prompt"},
            {9: frozenset({9})}, cleared.append, lambda value: None,
            status=self.status, attempts=DeliveryAttempts(child_epoch="child"))
        delivery.begin_attempt([{"id": 9}])
        with self.status.path.open("a") as stream:
            stream.write('{"request_id":')
        with self.assertRaises(RuntimeError):
            delivery.child_exited("child", verified_exit=True)
        self.assertEqual([], cleared)
        self.assertEqual("prompt", delivery.prompt(9))
        self.assertTrue(delivery.is_delivered(9))

    def test_dequeue_requires_attribution_and_is_not_completion(self):
        services = ChannelWakeTranscriptServices(lambda value: "", prompt_references_message_id,
                                                prompt_message_ids, lambda: 0)
        enqueue = {"type": "queue-operation", "operation": "enqueue", "content": "[ciel-wake] ids=3801,3802"}
        for content, exit_record in ((None, None), ("[ciel-wake] ids=3801,3802", 2)):
            records = [enqueue, {"type": "queue-operation", "operation": "dequeue", "content": content},
                       {"type": "assistant", "message": {"role": "assistant", "content": "unrelated"}}]
            text = chr(10).join(json.dumps(row) for row in records)
            for message_id in (3801, 3802):
                evidence = wake_state_evidence_from_text(message_id, text, None, services)
                self.assertEqual("queued", evidence.state)
                self.assertEqual(exit_record, evidence.queue_exit_record)
            self.assertEqual({3801: "queued", 3802: "queued"}, wake_states_from_text([3801, 3802], text, services))


if __name__ == "__main__":
    unittest.main()
