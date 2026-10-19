import threading
import unittest
from types import SimpleNamespace

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.channel_wake_delivery_repository import (
    ChannelWakeDeliveryRepository,
)


class ChannelWakeDeliveryRepositoryTests(unittest.TestCase):
    def repository(self, *, retained_limit=1000, prune_count=500):
        cleared = []
        committed = []
        repository = ChannelWakeDeliveryRepository(
            lock=threading.Lock(),
            delivered=set(),
            prompts={},
            batches={},
            clear_claim=cleared.append,
            commit_cursor=committed.append,
            retained_limit=retained_limit,
            prune_count=prune_count,
        )
        return repository, cleared, committed

    def test_delivery_and_prompt_state_are_projected_consistently(self):
        repository, _cleared, _committed = self.repository()

        self.assertTrue(repository.mark_delivered(7))
        self.assertFalse(repository.mark_delivered(7))
        repository.record_prompts([{"id": "7"}, {"id": "invalid"}], "wake")

        self.assertTrue(repository.is_delivered(7))
        self.assertEqual("wake", repository.prompt(7))

    def test_release_and_rollback_clear_claims_and_optional_cursor(self):
        repository, cleared, committed = self.repository()
        repository.mark_delivered(7)
        repository.record_prompts([{"id": 7}], "wake")

        repository.release_stale(7, True)
        repository.rollback([{"id": 8}, {"id": "invalid"}], [9])

        self.assertFalse(repository.is_delivered(7))
        self.assertEqual("", repository.prompt(7))
        self.assertEqual([7, 8, 9], cleared)
        self.assertEqual([7], committed)

    def test_oldest_state_is_pruned_after_retained_limit(self):
        repository, _cleared, _committed = self.repository(
            retained_limit=3, prune_count=2
        )

        for message_id in range(1, 5):
            repository.mark_delivered(message_id)
            repository.record_prompts([{"id": message_id}], str(message_id))

        self.assertEqual({3, 4}, repository.delivered)
        self.assertEqual({3: "3", 4: "4"}, repository.prompts)

    def test_completing_last_message_clears_every_claim_in_the_same_batch(self):
        repository, cleared, _committed = self.repository()
        messages = [{"id": 20}, {"id": 21}]
        for message in messages:
            repository.mark_delivered(message["id"])
        repository.record_prompts(messages, "one atomic batch")

        repository.complete(21)

        self.assertEqual({}, repository.prompts)
        self.assertEqual([20, 21], cleared)

    def test_completion_retry_preserves_entire_batch_after_lock_failure(self):
        for fail_on in (20, 21):
            with self.subTest(fail_on=fail_on):
                repository, cleared, _committed = self.repository()
                states = {}
                repository.status = SimpleNamespace(
                    transition=lambda message_id, state: states.__setitem__(message_id, state),
                )
                repository.record_prompts([{"id": 20}, {"id": 21}], "atomic batch")
                failed = False
                def clear_claim(message_id):
                    nonlocal failed
                    if message_id == fail_on and not failed:
                        failed = True
                        raise ChannelFileLockBusy(36, "fixture lock contention")
                    cleared.append(message_id)
                repository.clear_claim = clear_claim

                with self.assertRaises(ChannelFileLockBusy):
                    repository.complete(21)
                self.assertEqual({20, 21}, set(repository.batches))
                self.assertEqual("atomic batch", repository.prompt(20))
                self.assertEqual("atomic batch", repository.prompt(21))

                repository.complete(21)

                self.assertEqual({20: "replied", 21: "replied"}, states)
                self.assertEqual({}, repository.prompts)
                self.assertEqual({}, repository.batches)
                self.assertEqual({20, 21}, set(cleared))

    def test_completion_retry_preserves_batch_when_status_transition_fails(self):
        repository, _cleared, _committed = self.repository()
        states = {}
        failed = False
        def transition(message_id, state):
            nonlocal failed
            if message_id == 21 and state == "replied" and not failed:
                failed = True
                raise ChannelFileLockBusy(36, "fixture status lock contention")
            states[message_id] = state
        repository.status = SimpleNamespace(transition=transition)
        repository.record_prompts([{"id": 20}, {"id": 21}], "atomic batch")

        with self.assertRaises(ChannelFileLockBusy):
            repository.complete(21)
        self.assertEqual({20, 21}, set(repository.batches))

        repository.complete(21)

        self.assertEqual({20: "replied", 21: "replied"}, states)
        self.assertEqual({}, repository.batches)

    def test_releasing_stale_batch_commits_its_highest_message(self):
        repository, cleared, committed = self.repository()
        messages = [{"id": 20}, {"id": 21}]
        for message in messages:
            repository.mark_delivered(message["id"])
        repository.record_prompts(messages, "one atomic batch")

        repository.release_stale(20, True)

        self.assertEqual(set(), repository.delivered)
        self.assertEqual([20, 21], cleared)
        self.assertEqual([21], committed)

    def test_stale_release_retry_retains_full_batch_and_highest_cursor(self):
        for anchor in (20, 21):
            for stage in ("clear_claim", "transition", "commit_cursor"):
                with self.subTest(anchor=anchor, stage=stage):
                    repository, cleared, committed = self.repository()
                    states = {}
                    failed = False
                    def maybe_fail(selected):
                        nonlocal failed
                        if stage == selected and not failed:
                            failed = True
                            raise ChannelFileLockBusy(36, "fixture lock contention")
                    def clear_claim(message_id):
                        maybe_fail("clear_claim")
                        cleared.append(message_id)
                    def transition(message_id, state, **kwargs):
                        if state == "failed":
                            maybe_fail("transition")
                        states[message_id] = state
                    def commit_cursor(message_id):
                        maybe_fail("commit_cursor")
                        committed.append(message_id)
                    repository.clear_claim = clear_claim
                    repository.commit_cursor = commit_cursor
                    repository.status = SimpleNamespace(transition=transition)
                    for message_id in (20, 21):
                        repository.mark_delivered(message_id)
                    repository.record_prompts([{"id": 20}, {"id": 21}], "atomic batch")

                    with self.assertRaises(ChannelFileLockBusy):
                        repository.release_stale(anchor, True)
                    self.assertEqual({20, 21}, set(repository.batches))

                    repository.release_stale(anchor, True)

                    self.assertEqual({20: "failed", 21: "failed"}, states)
                    self.assertEqual({20, 21}, set(cleared))
                    self.assertEqual([21], committed)
                    self.assertEqual(set(), repository.delivered)
                    self.assertEqual({}, repository.batches)

    def test_equal_prompts_in_separate_batches_do_not_share_cleanup(self):
        repository, cleared, _committed = self.repository()
        repository.record_prompts([{"id": 20}, {"id": 21}], "same prompt")
        repository.record_prompts([{"id": 30}], "same prompt")

        repository.complete(21)

        self.assertEqual([20, 21], cleared)
        self.assertEqual("same prompt", repository.prompt(30))

    def test_failed_submission_is_sticky_and_emits_terminal_status(self):
        transitions = []
        repository, cleared, _committed = self.repository()
        repository.status = SimpleNamespace(
            transition=lambda request_id, status, **kwargs: transitions.append(
                (request_id, status, kwargs)
            ),
            get=lambda _request_id: None,
        )

        repository.mark_delivered(41)
        repository.fail([{"id": 41}], [41], "prompt_not_submitted")

        self.assertEqual("prompt_not_submitted", repository.failure_reason(41))
        self.assertFalse(repository.is_delivered(41))
        self.assertEqual((41, "failed"), transitions[-1][:2])
        self.assertIn(41, cleared)


if __name__ == "__main__":
    unittest.main()
