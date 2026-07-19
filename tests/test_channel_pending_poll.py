import unittest

from ciel_runtime_support.channel_pending_poll import (
    ChannelPendingInjectionOptions,
    ChannelPendingPollPolicy,
    ChannelPendingPollServices,
    ChannelPendingPollState,
    poll_pending_channel_messages,
)


class ChannelPendingPollTests(unittest.TestCase):
    def options(self, *, enabled=True):
        return ChannelPendingInjectionOptions(enabled, False, True, 2, True, False, 0.1)

    def services(self, *, active=False, inject=None, logs=None):
        return ChannelPendingPollServices(
            file_marker=lambda: (2.0, 100),
            should_check=lambda marker, previous, recheck, inflight: True,
            active=lambda: active,
            ensure_cursor=lambda: 12,
            inject_pending=inject or (lambda *args, **kwargs: args[1]),
            log=lambda level, message: (logs if logs is not None else []).append((level, message)),
        )

    def policy(self):
        return ChannelPendingPollPolicy("channel_test", "active_turn")

    def test_disabled_poll_still_advances_timestamp_and_reads_marker(self):
        state = ChannelPendingPollState(last_id=10)
        result = poll_pending_channel_messages(
            1.0, 1, b"\r", state, self.options(enabled=False), self.policy(), self.services()
        )
        self.assertIs(state, result)
        self.assertEqual(1.0, state.last_poll_at)
        self.assertEqual((0.0, -1), state.last_marker)

    def test_active_turn_defers_and_logs_on_interval(self):
        logs = []
        state = ChannelPendingPollState(last_id=10)
        poll_pending_channel_messages(
            31.0, 1, b"\r", state, self.options(), self.policy(), self.services(active=True, logs=logs)
        )

        self.assertTrue(state.pending_recheck)
        self.assertEqual(31.0, state.defer_logged_at)
        self.assertIn("channel_test_deferred cursor=10 reason=active_turn", logs[0][1])

    def test_injection_updates_cursor_and_inflight_state(self):
        observed = []

        def inject(*args, **kwargs):
            observed.append(kwargs)
            kwargs["injected_message_ids"].extend([20, 21])
            return 21

        state = ChannelPendingPollState(last_id=10)
        poll_pending_channel_messages(
            1.0, 1, b"\r", state, self.options(), self.policy(), self.services(inject=inject)
        )

        self.assertEqual(21, state.last_id)
        self.assertEqual(21, state.inflight_message_id)
        self.assertEqual(21, state.inflight_cursor)
        self.assertEqual(1.0, state.inflight_started_at)
        self.assertFalse(observed[0]["commit_cursor"])
        self.assertFalse(observed[0]["skip_blocking_wake_states"])


if __name__ == "__main__":
    unittest.main()
