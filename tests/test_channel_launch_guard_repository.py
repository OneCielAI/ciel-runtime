from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_launch_guard_repository import ChannelLaunchGuardRepository


class ChannelLaunchGuardRepositoryTests(unittest.TestCase):
    def test_round_trip_and_expiration(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            repository = ChannelLaunchGuardRepository(
                path=Path(tmp) / "guard.json",
                now=lambda: now[0],
                log=lambda _level, _message: None,
            )
            repository.write(42, 10.0)
            self.assertEqual({"max_existing_id": 42, "expires_at": 110.0}, repository.read())
            now[0] = 111.0
            self.assertIsNone(repository.read())

    def test_corrupt_guard_is_observable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guard.json"
            path.write_text("{broken", encoding="utf-8")
            events: list[tuple[str, str]] = []
            repository = ChannelLaunchGuardRepository(
                path=path,
                now=lambda: 100.0,
                log=lambda level, message: events.append((level, message)),
            )
            self.assertIsNone(repository.read())
            self.assertEqual("WARN", events[0][0])
            self.assertIn("channel_llm_launch_guard_read_failed", events[0][1])


if __name__ == "__main__":
    unittest.main()
