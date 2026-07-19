import unittest

from ciel_runtime_support.channel_compact_poll import (
    ChannelCompactInjectionOptions,
    ChannelCompactPollServices,
    ChannelCompactPollState,
    poll_pending_compaction,
)


class ChannelCompactPollTests(unittest.TestCase):
    def options(self):
        return ChannelCompactInjectionOptions(2, True, False, 0.1)

    def test_skips_before_interval_and_when_input_is_not_ready(self):
        calls = []
        services = ChannelCompactPollServices(lambda *args, **kwargs: calls.append((args, kwargs)))
        state = ChannelCompactPollState(last_poll_at=10.0)

        self.assertIs(state, poll_pending_compaction(10.2, 1, b"\r", None, state, self.options(), services))
        self.assertIs(
            state,
            poll_pending_compaction(11.0, 1, b"\r", None, state, self.options(), services, input_ready=False),
        )
        self.assertEqual([], calls)

    def test_inflight_advances_poll_without_injecting(self):
        calls = []
        state = poll_pending_compaction(
            11.0,
            1,
            b"\r",
            42,
            ChannelCompactPollState(10.0, 3.0),
            self.options(),
            ChannelCompactPollServices(lambda *args, **kwargs: calls.append((args, kwargs))),
        )

        self.assertEqual(ChannelCompactPollState(11.0, 3.0), state)
        self.assertEqual([], calls)

    def test_deferred_and_injected_status_update_log_state(self):
        observed = []

        def inject(*args, **kwargs):
            observed.append(kwargs)
            return "deferred"

        state = poll_pending_compaction(
            40.0,
            1,
            b"\r",
            None,
            ChannelCompactPollState(),
            self.options(),
            ChannelCompactPollServices(inject),
        )
        self.assertEqual(40.0, state.defer_logged_at)
        self.assertTrue(observed[0]["log_defer"])

        injected = poll_pending_compaction(
            41.0,
            1,
            b"\r",
            None,
            state,
            self.options(),
            ChannelCompactPollServices(lambda *args, **kwargs: "injected"),
        )
        self.assertEqual(ChannelCompactPollState(41.0, 0.0), injected)


if __name__ == "__main__":
    unittest.main()
