import threading
import unittest

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

    def test_equal_prompts_in_separate_batches_do_not_share_cleanup(self):
        repository, cleared, _committed = self.repository()
        repository.record_prompts([{"id": 20}, {"id": 21}], "same prompt")
        repository.record_prompts([{"id": 30}], "same prompt")

        repository.complete(21)

        self.assertEqual([20, 21], cleared)
        self.assertEqual("same prompt", repository.prompt(30))


if __name__ == "__main__":
    unittest.main()
