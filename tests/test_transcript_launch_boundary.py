"""First poll after launch must not replay a huge resumed transcript.

Andy 2026-11-21: after restart the launch boundary scan missed its deadline,
the turn-scan offset stayed 0, and the router replayed a 1.5GB transcript in
4MB batches, saturating CPU and freezing the console for minutes.
"""
import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.channel_transcript_repository import (
    ChannelTranscriptRepository,
)


def repo(home, scope):
    return ChannelTranscriptRepository(home=home, cache={}, scope=scope, now=lambda: 300.0)


def record(kind):
    return json.dumps({"type": "event_msg", "payload": {"type": kind}}) + "\n"


class FirstPollBoundaryTests(unittest.TestCase):
    def test_missing_boundary_establishes_end_of_prelaunch_history(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "resumed.jsonl"
            path.write_text(record("task_started") + record("old_output"), encoding="utf-8")
            scope = {"turn_active": False}  # boundary scan missed its deadline
            repository = repo(Path(raw_dir), scope)

            # The first poll sees only pre-launch history: it establishes the
            # boundary at EOF instead of replaying the history.
            self.assertEqual("", repository.read_turn_updates(path))
            self.assertEqual(path, Path(scope["turn_scan_path"]))
            self.assertEqual(path.stat().st_size, scope["turn_scan_offset"])

            # Records appended after the boundary are delivered normally.
            with path.open("a", encoding="utf-8") as stream:
                stream.write(record("task_complete"))
            self.assertIn('"task_complete"', repository.read_turn_updates(path))

    def test_stale_path_switch_keeps_normal_replay(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            old = Path(raw_dir) / "old.jsonl"
            new = Path(raw_dir) / "new.jsonl"
            new.write_text(record("task_started"), encoding="utf-8")
            scope = {"turn_scan_path": old, "turn_scan_offset": 100, "turn_active": True}
            repository = repo(Path(raw_dir), scope)

            self.assertIn('"task_started"', repository.read_turn_updates(new))


if __name__ == "__main__":
    unittest.main()
