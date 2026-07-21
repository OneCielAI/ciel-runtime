import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_cursor_repository import (
    ChannelCursorRepository,
    ChannelCursorStatePolicy,
)


class ChannelCursorRepositoryTests(unittest.TestCase):
    def test_state_policy_merges_file_cache_and_scan_monotonically(self):
        policy = ChannelCursorStatePolicy()

        self.assertEqual(8, policy.resolve_read(8, 5, lambda: 99).value)
        self.assertEqual(8, policy.resolve_read(5, 8, lambda: 99).value)
        self.assertEqual(7, policy.resolve_read(None, 7, lambda: 99).value)
        initialized = policy.resolve_read(None, None, lambda: 12)
        self.assertEqual(12, initialized.value)
        self.assertTrue(initialized.persist)

    def test_state_policy_rejects_non_monotonic_commits(self):
        policy = ChannelCursorStatePolicy()

        self.assertIsNone(policy.newer(None, 7))
        self.assertIsNone(policy.newer(7, 7))
        self.assertEqual(8, policy.newer(8, 7))

    def test_round_trip_clamps_negative_cursor_and_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cursor.json"
            repository = ChannelCursorRepository(path=path, log=lambda _level, _message: None)

            self.assertTrue(repository.write(-2, metadata={"updated_at": 10.0}))
            self.assertEqual(0, repository.read())
            self.assertEqual(10.0, json.loads(path.read_text(encoding="utf-8"))["updated_at"])

    def test_missing_cursor_is_normal_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            repository = ChannelCursorRepository(
                path=Path(tmp) / "missing.json",
                log=lambda level, message: events.append((level, message)),
            )
            self.assertIsNone(repository.read())
            self.assertEqual([], events)

    def test_corrupt_cursor_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cursor.json"
            path.write_text("{broken", encoding="utf-8")
            events = []
            repository = ChannelCursorRepository(
                path=path,
                log=lambda level, message: events.append((level, message)),
            )
            self.assertIsNone(repository.read())
            self.assertEqual("WARN", events[0][0])
            self.assertIn("channel_cursor_read_failed", events[0][1])


if __name__ == "__main__":
    unittest.main()
