from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_wake_claim_repository import (
    ChannelWakeClaimRepository,
    prompt_message_ids,
    prompt_references_message_id,
)


class ChannelWakeClaimRepositoryTests(unittest.TestCase):
    def repository(self, path: Path, now: list[float]) -> ChannelWakeClaimRepository:
        return ChannelWakeClaimRepository(
            path=path,
            file_lock=nullcontext,
            now=lambda: now[0],
            ttl_seconds=lambda: 30.0,
            log=lambda _level, _message: None,
        )

    def test_prompt_reference_supports_ids_and_normalized_claim_text(self):
        self.assertEqual({7, 8}, prompt_message_ids("pending_ids=7, 8"))
        self.assertTrue(prompt_references_message_id("wake id=9", 9))
        self.assertTrue(prompt_references_message_id("hello   world", 4, ["hello world"]))

    def test_claim_is_exclusive_and_clear_allows_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = self.repository(Path(tmp) / "claims.json", [100.0])
            self.assertTrue(repository.claim(7, "wake id=7"))
            self.assertFalse(repository.claim(7, "wake id=7"))
            self.assertEqual("wake id=7", repository.prompt(7))
            repository.clear(7)
            self.assertTrue(repository.claim(7, "wake id=7"))

    def test_expired_claim_is_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            repository = self.repository(Path(tmp) / "claims.json", now)
            self.assertTrue(repository.claim(8, "old"))
            now[0] = 131.0
            self.assertEqual("", repository.prompt(8))
            self.assertTrue(repository.claim(8, "new"))


if __name__ == "__main__":
    unittest.main()
