"""A router restart resumes transcripts where it stopped (Walkie 48926 F3).

A long-lived transcript (a resumed session) is written to after every launch,
so "modified since the launch" read it from byte 0 again and re-reported every
turn it ever held.  Turn ends now keep a stored cursor; a transcript seen for
the first time starts at its end unless it was created after the launch.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from ciel_runtime_support.transcript_delta_delivery import (
    ToolCallEventSettings,
    TranscriptDeliveryPorts,
    TranscriptDeltaDeliveryService,
)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _turn(turn_id, at):
    return (
        json.dumps({"type": "event_msg", "timestamp": _iso(at), "payload": {"type": "task_started", "turn_id": turn_id}}) + "\n"
        + json.dumps({"type": "event_msg", "timestamp": _iso(at), "payload": {"type": "task_complete", "turn_id": turn_id}}) + "\n"
    )


class _Router:
    """One router process: a service over a shared cursor file."""

    def __init__(self, root: Path, path: Path, started_at: float):
        self.posted: list[dict] = []
        self.service = TranscriptDeltaDeliveryService(
            root / "cursors.json",
            "workspace-1",
            TranscriptDeliveryPorts(
                load_config=lambda: {},
                latest_transcript=lambda: path,
                scope=lambda: {"runtime": "codex", "session_id": "s", "started_at": started_at, "turn_scan_path": None},
                log=lambda *_args: None,
                agent_turn_post=self.posted.append,
            ),
        )

    def ids(self):
        return [event["turn_id"] for event in self.posted]


class RestartOffsetTests(unittest.TestCase):
    def long_lived(self, root: Path, turns: int) -> tuple[Path, float]:
        """A transcript created a day before the launch, written to after it."""

        created = time.time() - 86_400
        path = root / "resumed.jsonl"
        path.write_text("".join(_turn(f"old-{i}", created + i) for i in range(turns)), encoding="utf-8")
        started_at = time.time() + 5
        os.utime(path, (started_at + 1, started_at + 1))
        return path, started_at

    def test_a_resumed_long_transcript_is_not_reread_from_the_beginning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path, started_at = self.long_lived(root, 300)
            router = _Router(root, path, started_at)
            self.assertEqual(0, router.service.poll_agent_turns())
            with path.open("a", encoding="utf-8") as stream:
                stream.write(_turn("new-1", started_at + 2))
            self.assertEqual(1, router.service.poll_agent_turns())
            self.assertEqual(["new-1"], router.ids())

    def test_a_restart_resumes_turn_ends_from_the_stored_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path, started_at = self.long_lived(root, 3)
            first = _Router(root, path, started_at)
            first.service.poll_agent_turns()
            with path.open("a", encoding="utf-8") as stream:
                stream.write(_turn("t-1", started_at + 2))
            first.service.poll_agent_turns()
            self.assertEqual(["t-1"], first.ids())
            # The router stops; a turn ends while it is down; a new router starts.
            with path.open("a", encoding="utf-8") as stream:
                stream.write(_turn("t-2", started_at + 3))
            second = _Router(root, path, started_at + 10)
            os.utime(path, (started_at + 11, started_at + 11))
            second.service.poll_agent_turns()
            self.assertEqual(["t-2"], second.ids())
            stored = json.loads((root / "cursors.json").read_text(encoding="utf-8"))["agent_turn_sources"]
            self.assertEqual([path.stat().st_size], [c["offset"] for c in stored.values()])

    def test_a_transcript_created_after_the_launch_is_read_from_its_beginning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            started_at = time.time() - 5
            path = root / "new-session.jsonl"
            path.write_text(_turn("first", time.time()), encoding="utf-8")
            router = _Router(root, path, started_at)
            self.assertEqual(1, router.service.poll_agent_turns())
            self.assertEqual(["first"], router.ids())

    def test_tool_call_start_follows_the_same_rule(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path, started_at = self.long_lived(root, 3)
            tail = ToolCallEventSettings(True, 1.0, 1_048_576, "tail", False)
            scope = {"started_at": started_at, "turn_scan_path": None}
            size = path.stat().st_size
            self.assertEqual(size, TranscriptDeltaDeliveryService._initial_tool_call_offset(tail, scope, path, size))


if __name__ == "__main__":
    unittest.main()
