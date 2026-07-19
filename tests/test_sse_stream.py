import unittest

from ciel_runtime_support.sse_stream import SseRetryState, SseStreamServices, consume_sse_stream


class _Response:
    def __init__(self, lines):
        self.lines = iter(lines)

    def readline(self):
        return next(self.lines, b"")


class SseStreamTests(unittest.TestCase):
    def test_dispatches_multiline_event_and_updates_retry(self):
        events = []
        invalid = []
        running = iter([True, True, True, True, True, True, False])
        retry = SseRetryState(5.0)
        consume_sse_stream(
            _Response([b"event: update\n", b"id: 42\n", b"retry: 2500\n", b"data: one\n", b"data: two\n", b"\n"]),
            retry,
            "ended",
            SseStreamServices(
                should_continue=lambda: next(running),
                dispatch=lambda event, data, event_id: events.append((event, data, event_id)),
                invalid_retry=invalid.append,
            ),
        )

        self.assertEqual(2.5, retry.seconds)
        self.assertEqual([("update", ["one", "two"], "42")], events)
        self.assertEqual([], invalid)

    def test_reports_invalid_retry_and_raises_on_eof(self):
        invalid = []
        with self.assertRaisesRegex(ConnectionError, "stream ended"):
            consume_sse_stream(
                _Response([b"retry: later\n"]),
                SseRetryState(5.0),
                "stream ended",
                SseStreamServices(
                    should_continue=lambda: True,
                    dispatch=lambda event, data, event_id: None,
                    invalid_retry=invalid.append,
                ),
            )

        self.assertEqual(["later"], invalid)


if __name__ == "__main__":
    unittest.main()
