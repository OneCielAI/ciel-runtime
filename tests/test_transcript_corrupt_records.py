"""Corrupt transcript regions never stall delivery (Walkie 48926 F3).

A Claude Code transcript on an agent PC held a 28,532,571-byte NUL run (no
newline) followed by valid JSON.  Every reader of the transcript (delivery,
tool-call events, agent turn ends) stopped at the NUL run's offset and logged
``record exceeds max_record_bytes`` every 30 s for days.  A NUL run or a line
over ``max_record_bytes`` is now skipped once, with one WARN, and the cursor
moves past it.
"""

import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.transcript_delta_delivery import (
    TranscriptDeliveryPorts,
    TranscriptDeltaDeliveryService,
)

RECORD_LIMIT = 4096


def _config(**extra):
    return {
        "transcript_events": {
            "enabled": True,
            "url": "http://fixture.invalid/",
            "start_mode": "beginning",
            "content_filter": "raw",
            "max_batch_bytes": 1024,
            "max_record_bytes": RECORD_LIMIT,
        },
        "tool_call_events": {"enabled": True, "start_mode": "beginning"},
        **extra,
    }


def _turn(turn_id):
    return (
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn_id}}) + "\n"
        + json.dumps({"type": "event_msg", "timestamp": "2026-10-12T19:00:00Z", "payload": {"type": "task_complete", "turn_id": turn_id}}) + "\n"
    ).encode()


class _Fixture:
    def __init__(self, root: Path, body: bytes):
        self.root = root
        self.path = root / "session.jsonl"
        self.path.write_bytes(body)
        self.logs: list[tuple[str, str]] = []
        self.events: list[dict] = []
        self.posted: list[dict] = []
        self.published: list[dict] = []
        self.service = self.make()

    def make(self):
        service = TranscriptDeltaDeliveryService(
            self.root / "cursors.json",
            "workspace-1",
            TranscriptDeliveryPorts(
                load_config=_config,
                latest_transcript=lambda: self.path,
                scope=lambda: {"runtime": "codex", "session_id": "session-1", "turn_scan_path": self.path, "turn_scan_offset": 0},
                log=lambda level, message: self.logs.append((level, message)),
                event_publish=lambda **event: self.published.append(event),
                agent_turn_post=self.posted.append,
            ),
        )
        service._post = lambda _settings, event: self.events.append(event) or True
        return service

    def cursors(self):
        return json.loads((self.root / "cursors.json").read_text(encoding="utf-8"))

    def warnings(self):
        return [message for level, message in self.logs if level == "WARN"]


class NulRunTests(unittest.TestCase):
    NUL = 3 * RECORD_LIMIT

    def body(self):
        return b'{"before":1}\n' + b"\x00" * self.NUL + b'{"after":2}\n'

    def test_delivery_skips_the_nul_run_and_delivers_the_record_after_it(self):
        with tempfile.TemporaryDirectory() as td:
            f = _Fixture(Path(td), self.body())
            for _ in range(4):
                f.service.poll_once()
            contents = "".join(event["data"]["content"] for event in f.events)
            self.assertIn('{"before":1}', contents)
            self.assertIn('{"after":2}', contents)
            self.assertNotIn("\x00", contents)
            end = f.path.stat().st_size
            self.assertEqual(end, f.events[-1]["data"]["end_offset"])
            self.assertEqual([end], [c["offset"] for c in f.cursors()["destinations"].values()])
            skipped = [w for w in f.warnings() if "transcript_record_skipped" in w]
            self.assertEqual(1, len(skipped), f.warnings())
            self.assertIn("offset=13", skipped[0])
            self.assertIn(f"length={self.NUL}", skipped[0])
            self.assertIn("reason=nul_run", skipped[0])
            self.assertIn("path=session.jsonl", skipped[0])
            self.assertFalse(any("max_record_bytes" in w for w in f.warnings()))

    def test_tool_call_events_and_agent_turns_pass_the_nul_run(self):
        with tempfile.TemporaryDirectory() as td:
            f = _Fixture(Path(td), _turn("t-1") + b"\x00" * self.NUL + _turn("t-2"))
            for _ in range(4):
                f.service.poll_tool_call_events()
                f.service.poll_agent_turns()
            self.assertEqual(["t-1", "t-2"], [event["turn_id"] for event in f.posted])
            end = f.path.stat().st_size
            self.assertEqual([end], [c["offset"] for c in f.cursors()["tool_call_sources"].values()])

    def test_a_nul_run_still_at_the_end_of_the_file_is_not_skipped_yet(self):
        with tempfile.TemporaryDirectory() as td:
            f = _Fixture(Path(td), b'{"before":1}\n' + b"\x00" * self.NUL)
            for _ in range(3):
                f.service.poll_once()
            self.assertEqual([13], [c["offset"] for c in f.cursors()["destinations"].values()])
            self.assertEqual([], f.warnings())
            with f.path.open("ab") as stream:
                stream.write(b'{"after":2}\n')
            f.service.poll_once()
            self.assertIn('{"after":2}', f.events[-1]["data"]["content"])

    def test_a_short_nul_prefix_inside_a_batch_does_not_hide_the_record(self):
        with tempfile.TemporaryDirectory() as td:
            f = _Fixture(Path(td), _turn("t-1")[:-1] + b"\n" + b"\x00" * 16 + _turn("t-2"))
            f.service.poll_agent_turns()
            self.assertEqual(["t-1", "t-2"], [event["turn_id"] for event in f.posted])


class OversizedLineTests(unittest.TestCase):
    def body(self):
        big = json.dumps({"data": "x" * (RECORD_LIMIT * 3)}).encode() + b"\n"
        return b'{"before":1}\n' + big + b'{"after":2}\n', len(big)

    def test_a_line_over_max_record_bytes_is_skipped_once_and_the_cursor_moves_on(self):
        with tempfile.TemporaryDirectory() as td:
            body, big = self.body()
            f = _Fixture(Path(td), body)
            for _ in range(4):
                f.service.poll_once()
            contents = "".join(event["data"]["content"] for event in f.events)
            self.assertIn('{"before":1}', contents)
            self.assertIn('{"after":2}', contents)
            self.assertNotIn("xxxx", contents)
            self.assertEqual([f.path.stat().st_size], [c["offset"] for c in f.cursors()["destinations"].values()])
            skipped = [w for w in f.warnings() if "transcript_record_skipped" in w]
            self.assertEqual(1, len(skipped), f.warnings())
            self.assertIn("offset=13", skipped[0])
            self.assertIn(f"length={big}", skipped[0])
            self.assertIn(f"reason=record_exceeds_max_record_bytes={RECORD_LIMIT}", skipped[0])

    def test_an_oversized_line_still_being_written_waits_for_its_newline(self):
        with tempfile.TemporaryDirectory() as td:
            f = _Fixture(Path(td), b'{"before":1}\n{"data":"' + b"x" * (RECORD_LIMIT * 3))
            for _ in range(3):
                f.service.poll_once()
            self.assertEqual([13], [c["offset"] for c in f.cursors()["destinations"].values()])
            with f.path.open("ab") as stream:
                stream.write(b'"}\n{"after":2}\n')
            for _ in range(3):
                f.service.poll_once()
            self.assertIn('{"after":2}', f.events[-1]["data"]["content"])
            self.assertEqual(1, len([w for w in f.warnings() if "transcript_record_skipped" in w]))

    def test_agent_turns_pass_an_oversized_line(self):
        # Turn ends read 1 MiB batches, so their record bound is at least 1 MiB.
        with tempfile.TemporaryDirectory() as td:
            big = json.dumps({"data": "x" * 1_100_000}).encode() + b"\n"
            f = _Fixture(Path(td), _turn("t-1") + big + _turn("t-2"))
            for _ in range(4):
                f.service.poll_agent_turns()
            self.assertEqual(["t-1", "t-2"], [event["turn_id"] for event in f.posted])


if __name__ == "__main__":
    unittest.main()
