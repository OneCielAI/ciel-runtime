import unittest
from unittest import mock

from ciel_runtime_support.initial_stream_retry import InitialStreamRetry
from ciel_runtime_support.remote_bridge import REMOTE_BRIDGE_CONFIG_MARKER
from ciel_runtime_support.upstream_error_policy import (
    initial_stream_retries,
    retryable_exception,
)


class ScriptedResponse:
    def __init__(self, events):
        self.events = list(events)
        self.closed = False

    def __iter__(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event

    def close(self):
        self.closed = True


class InitialStreamRetryTests(unittest.TestCase):
    def wrapper(self, response, reopen, retries=2):
        notices = []
        sleeps = []
        return (
            InitialStreamRetry(
                response,
                reopen,
                retries,
                retryable_exception,
                lambda attempt: float(attempt),
                sleeps.append,
                lambda attempt, error: notices.append((attempt, type(error).__name__)),
            ),
            notices,
            sleeps,
        )

    def test_reset_before_first_byte_reopens_and_recovers(self):
        first = ScriptedResponse([ConnectionResetError(104, "Connection reset by peer")])
        second = ScriptedResponse([b"data: ok\n", b"data: [DONE]\n"])
        reopen = mock.Mock(return_value=second)
        wrapper, notices, sleeps = self.wrapper(first, reopen)

        self.assertEqual([b"data: ok\n", b"data: [DONE]\n"], list(wrapper))
        reopen.assert_called_once_with(1)
        self.assertTrue(first.closed)
        self.assertEqual([(1, "ConnectionResetError")], notices)
        self.assertEqual([1.0], sleeps)

    def test_reset_after_first_byte_is_never_replayed(self):
        first = ScriptedResponse(
            [b"data: partial\n", ConnectionResetError(104, "Connection reset by peer")]
        )
        reopen = mock.Mock()
        wrapper, _notices, _sleeps = self.wrapper(first, reopen)

        iterator = iter(wrapper)
        self.assertEqual(b"data: partial\n", next(iterator))
        with self.assertRaises(ConnectionResetError):
            next(iterator)
        reopen.assert_not_called()

    def test_empty_response_uses_the_same_bounded_reconnect(self):
        first = ScriptedResponse([])
        second = ScriptedResponse([b"data: ok\n"])
        wrapper, notices, _sleeps = self.wrapper(first, mock.Mock(return_value=second))

        self.assertEqual([b"data: ok\n"], list(wrapper))
        self.assertEqual([(1, "EOFError")], notices)

    def test_configured_reconnects_are_bounded(self):
        self.assertEqual(2, initial_stream_retries({}))
        self.assertEqual(0, initial_stream_retries({"stream_initial_retries": -1}))
        self.assertEqual(5, initial_stream_retries({"stream_initial_retries": 99}))
        self.assertEqual(2, initial_stream_retries({"stream_initial_retries": "bad"}))

    def test_remote_bridge_disables_initial_stream_reconnects(self):
        self.assertEqual(
            0,
            initial_stream_retries(
                {
                    "stream_initial_retries": 5,
                    REMOTE_BRIDGE_CONFIG_MARKER: True,
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
