import tempfile
import threading
import unittest
from pathlib import Path

from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository


class RuntimeInputStatusRepositoryTests(unittest.TestCase):
    def repository(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        events = []
        repository = RuntimeInputStatusRepository(
            Path(temp_dir.name) / "runtime-input-status.jsonl",
            lambda **event: events.append(event),
            lambda _level, _message: None,
            threading.RLock(),
        )
        return repository, events

    def test_queued_submitted_replied_is_queryable_and_streamed(self):
        repository, events = self.repository()

        repository.transition(12, "queued")
        repository.transition(12, "submitted")
        repository.transition(12, "replied")

        self.assertEqual("replied", repository.get(12)["status"])
        self.assertEqual(["queued", "submitted", "replied"], [e["data"]["status"] for e in events])
        self.assertTrue(all(e["category"] == "runtime_input.status" for e in events))

    def test_failed_state_does_not_regress_to_queued(self):
        repository, events = self.repository()

        repository.transition(13, "queued")
        repository.transition(13, "failed", reason="prompt_not_submitted")
        repository.transition(13, "queued")

        self.assertEqual("failed", repository.get(13)["status"])
        self.assertEqual(2, len(events))
        self.assertEqual("prompt_not_submitted", repository.get(13)["reason"])


if __name__ == "__main__":
    unittest.main()
