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
            state_requests = []
            service = ChannelCursorRecoveryService(
                cache=cache,
                policy=ChannelCursorRecoveryPolicy(cache_ttl_seconds=5, transcript_max_bytes=123),
                ports=ChannelCursorRecoveryPorts(
                    latest_transcript=lambda: transcript,
                    read_tail=lambda path, max_bytes: reads.append((path, max_bytes)) or "queued",
                    queued_command_ids=lambda text: {8, 5, 12},
                    wake_states=lambda ids, text: state_requests.append(list(ids))
                    or {message_id: "missing" if message_id == 5 else "queued" for message_id in ids},
                    clamp_to_clear_floor=lambda value: max(3, value),
                    now=lambda: 10.0,
                    log=lambda level, message: logs.append((level, message)),
                ),
            )

            self.assertEqual(4, service.recover(10))
            self.assertEqual(4, service.recover(10))

        self.assertEqual([(transcript, 123)], reads)
        # One batched state read per transcript read, only for ids at or below the cursor.
        self.assertEqual([[5, 8]], state_requests)
        self.assertEqual(4, cache["recovered_last_id"])
        self.assertTrue(any("message_id=5" in message for _level, message in logs))

    def test_no_queued_ids_at_or_below_the_cursor_skips_the_state_read(self):
        service = ChannelCursorRecoveryService(
            cache={},
            policy=ChannelCursorRecoveryPolicy(),
            ports=ChannelCursorRecoveryPorts(
                latest_transcript=lambda: Path(__file__),
                read_tail=lambda *args, **kwargs: "queued",
                queued_command_ids=lambda text: {20},
                wake_states=lambda ids, text: self.fail("no candidate to check"),
                clamp_to_clear_floor=lambda value: value,
                now=lambda: 0.0,
                log=lambda level, message: None,
            ),
        )

        self.assertEqual(10, service.recover(10))

    def test_non_positive_cursor_does_not_touch_transcript(self):
        service = ChannelCursorRecoveryService(
            cache={},
            policy=ChannelCursorRecoveryPolicy(),
            ports=ChannelCursorRecoveryPorts(
                latest_transcript=lambda: self.fail("transcript lookup should not run"),
                read_tail=lambda *args, **kwargs: "",
                queued_command_ids=lambda text: set(),
                wake_states=lambda ids, text: {},
                clamp_to_clear_floor=lambda value: value,
                now=lambda: 0.0,
                log=lambda level, message: None,
            ),
        )

        self.assertEqual(0, service.recover(0))


if __name__ == "__main__":
    unittest.main()
