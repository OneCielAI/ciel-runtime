import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.channel_cursor_recovery import (
    ChannelCursorRecoveryPolicy,
    ChannelCursorRecoveryPorts,
    ChannelCursorRecoveryService,
)


class ChannelCursorRecoveryServiceTest(unittest.TestCase):
    def test_recovers_before_oldest_missing_queued_command_and_caches_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text("queued", encoding="utf-8")
            cache = {}
            reads = []
            logs = []
            service = ChannelCursorRecoveryService(
                cache=cache,
                policy=ChannelCursorRecoveryPolicy(cache_ttl_seconds=5, transcript_max_bytes=123),
                ports=ChannelCursorRecoveryPorts(
                    latest_transcript=lambda: transcript,
                    read_tail=lambda path, max_bytes: reads.append((path, max_bytes)) or "queued",
                    queued_command_ids=lambda text: {8, 5},
                    wake_state=lambda message_id, text: "missing" if message_id == 5 else "queued",
                    clamp_to_clear_floor=lambda value: max(3, value),
                    now=lambda: 10.0,
                    log=lambda level, message: logs.append((level, message)),
                ),
            )

            self.assertEqual(4, service.recover(10))
            self.assertEqual(4, service.recover(10))

        self.assertEqual([(transcript, 123)], reads)
        self.assertEqual(4, cache["recovered_last_id"])
        self.assertTrue(any("message_id=5" in message for _level, message in logs))

    def test_non_positive_cursor_does_not_touch_transcript(self):
        service = ChannelCursorRecoveryService(
            cache={},
            policy=ChannelCursorRecoveryPolicy(),
            ports=ChannelCursorRecoveryPorts(
                latest_transcript=lambda: self.fail("transcript lookup should not run"),
                read_tail=lambda *args, **kwargs: "",
                queued_command_ids=lambda text: set(),
                wake_state=lambda message_id, text: "missing",
                clamp_to_clear_floor=lambda value: value,
                now=lambda: 0.0,
                log=lambda level, message: None,
            ),
        )

        self.assertEqual(0, service.recover(0))


if __name__ == "__main__":
    unittest.main()
