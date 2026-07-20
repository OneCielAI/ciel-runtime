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


if __name__ == "__main__":
    unittest.main()
