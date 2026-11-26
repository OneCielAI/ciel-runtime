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

    def test_already_terminal_queued_command_does_not_rewind_cursor(self):
        # Celly 2026-11-26 (12:57-13:23Z): message 16799 stayed in the 8 MB
        # transcript tail as a queued_command after its reply rolled off, while
        # durable lifecycle status was already "replied". Every 5 s recovery
        # rewound 16851 -> 16798 and re-scanned 50+ messages (214 cycles, 5581
        # log lines).
        from threading import Lock
        from ciel_runtime_support.channel_wake_delivery_repository import (
            ChannelWakeDeliveryRepository,
        )

        class FakeStatus:
            def __init__(self, rows):
                self._rows = rows

            def get(self, message_id):
                return self._rows.get(message_id)

        repo = ChannelWakeDeliveryRepository(
            lock=Lock(),
            delivered=set(),
            prompts={},
            batches={},
            clear_claim=lambda _mid: None,
            commit_cursor=lambda _mid: None,
            failed={16801: "stale_unconfirmed"},
            status=FakeStatus({
                16799: {"status": "replied"},
                16800: {"status": "skipped"},
                16802: {"status": "interrupted_uncertain"},
                16803: {"status": "failed", "reason": "stale_unconfirmed"},
                16810: {"status": "queued"},
            }),
        )
        logs = []
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text("queued", encoding="utf-8")
            service = ChannelCursorRecoveryService(
                cache={},
                policy=ChannelCursorRecoveryPolicy(),
                ports=ChannelCursorRecoveryPorts(
                    latest_transcript=lambda: transcript,
                    read_tail=lambda *args, **kwargs: "queued",
                    queued_command_ids=lambda text: {16799, 16800, 16801, 16802, 16803, 16810},
                    wake_states=lambda ids, text: {mid: "missing" for mid in ids},
                    clamp_to_clear_floor=lambda value: value,
                    now=lambda: 10.0,
                    log=lambda level, message: logs.append((level, message)),
                    is_terminal=repo.is_terminal,
                ),
            )
            # Skips terminal 16799..16803 and rewinds only before non-terminal 16810.
            self.assertEqual(16809, service.recover(16851))
            self.assertEqual(1, len(logs))
            self.assertIn("message_id=16810", logs[0][1])

            # When all missing queued_command ids are terminal, cursor is unchanged.
            logs.clear()
            service2 = ChannelCursorRecoveryService(
                cache={},
                policy=ChannelCursorRecoveryPolicy(),
                ports=ChannelCursorRecoveryPorts(
                    latest_transcript=lambda: transcript,
                    read_tail=lambda *args, **kwargs: "queued",
                    queued_command_ids=lambda text: {16799, 16800, 16801, 16802, 16803},
                    wake_states=lambda ids, text: {mid: "missing" for mid in ids},
                    clamp_to_clear_floor=lambda value: value,
                    now=lambda: 10.0,
                    log=lambda level, message: logs.append((level, message)),
                    is_terminal=repo.is_terminal,
                ),
            )
            self.assertEqual(16851, service2.recover(16851))
            self.assertEqual([], logs)

    def test_wake_context_wires_lifecycle_is_terminal_into_cursor_recovery(self):
        from types import SimpleNamespace
        from threading import Lock
        from ciel_runtime_support.channel_wake_context import ChannelWakeContext
        from ciel_runtime_support.channel_wake_delivery_repository import (
            ChannelWakeDeliveryRepository,
        )

        class FakeStatus:
            def get(self, message_id):
                return {"status": "replied"} if message_id == 16799 else None

        repo = ChannelWakeDeliveryRepository(
            lock=Lock(),
            delivered=set(),
            prompts={},
            batches={},
            clear_claim=lambda _mid: None,
            commit_cursor=lambda _mid: None,
            status=FakeStatus(),
        )
        ctx = SimpleNamespace(
            transcript=SimpleNamespace(recovery_cache={}, read_tail=lambda *_a, **_k: "queued", now=lambda: 10.0),
            transcript_policy=SimpleNamespace(latest_transcript=lambda: Path(__file__), log=lambda *_a: None),
            queued_command_ids_from_text=lambda _text: {16799},
            wake_states_from_text=lambda ids, _text: {mid: "missing" for mid in ids},
            cursor=SimpleNamespace(clamp_to_clear_floor=lambda v: v),
            pending_delivery=SimpleNamespace(lifecycle=repo),
        )
        service = ChannelWakeContext.cursor_recovery_service(ctx)
        self.assertIs(service.ports.is_terminal.__self__, repo)
        self.assertEqual(16851, service.recover(16851))


if __name__ == "__main__":
    unittest.main()
