import unittest
import dataclasses
import errno
from unittest.mock import Mock

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.channel_compact_injection import (
    ChannelCompactInjectionService, ChannelCompactRequestPorts, ChannelCompactRuntimePorts,
)

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

    def test_recovery_compact_is_not_starved_by_unconfirmed_channel_wake(self):
        calls = []
        poll_pending_compaction(
            11.0, 1, b"cr", 42, ChannelCompactPollState(), self.options(),
            ChannelCompactPollServices(lambda *_args, **_kw: calls.append("compact"), recovery_pending=lambda: True),
        )
        self.assertEqual(["compact"], calls)

    def _busy_service(self, stage):
        request = {"id": "compact-lock", "action": "compact", "automatic": True}
        busy = ChannelFileLockBusy(errno.EACCES, "private lock payload")
        read, clear, write, control = Mock(return_value=request), Mock(), Mock(), Mock()
        control.command_ready.return_value = True
        if stage == "read":
            read.side_effect = busy
        elif stage == "ready":
            control.command_ready.side_effect = busy
        elif stage == "write":
            write.side_effect = busy
        elif stage == "submitted":
            control.command_submitted.side_effect = busy
        elif stage == "clear":
            clear.side_effect = busy
        service = ChannelCompactInjectionService(
            ChannelCompactRequestPorts(read, clear),
            ChannelCompactRuntimePorts(lambda: False, lambda: False, lambda value: value, write, repr),
            Mock(),
        )
        return service, request, clear, write, control

    def test_pre_submit_lock_busy_defers_without_consuming_or_marking_uncertain(self):
        for stage in ("facade_read", "read", "ready", "recovery_pending"):
            with self.subTest(stage=stage):
                service, request, clear, write, control = self._busy_service(stage)
                inject = Mock(side_effect=lambda *args, **kwargs: service.inject(*args, control=control, **kwargs))
                recovery = Mock(return_value=True)
                if stage == "facade_read":
                    inject.side_effect = ChannelFileLockBusy(errno.EACCES, "private facade payload")
                if stage == "recovery_pending":
                    recovery.side_effect = ChannelFileLockBusy(errno.EACCES, "private recovery payload")
                before = ChannelCompactPollState(10.0, 3.0)
                services = ChannelCompactPollServices(inject, recovery)
                inflight = 42 if stage == "recovery_pending" else None
                state = poll_pending_compaction(40.0, 1, b"\r", inflight, before, self.options(), services)
                self.assertEqual(ChannelCompactPollState(40.0, 3.0), state)
                clear.assert_not_called()
                write.assert_not_called()
                control.command_failed.assert_not_called()
                control.command_submitted.assert_not_called()
                # No hot-loop retry; the next eligible poll still owns the request.
                self.assertIs(state, poll_pending_compaction(40.2, 1, b"\r", inflight, state, self.options(), services))
                inject.reset_mock(side_effect=True)
                recovery.reset_mock(side_effect=True)
                recovery.return_value = True
                service = dataclasses.replace(service, request=dataclasses.replace(service.request, read=lambda: request))
                control.command_ready.side_effect = None
                inject.side_effect = lambda *args, **kwargs: service.inject(*args, control=control, **kwargs)
                state = poll_pending_compaction(40.6, 1, b"\r", inflight, state, self.options(), services)
                self.assertEqual(ChannelCompactPollState(40.6, 0.0), state)
                write.assert_called_once()
                clear.assert_called_once_with(request["id"])

    def test_submission_or_bookkeeping_lock_busy_is_not_a_retryable_poll(self):
        for stage in ("write", "submitted", "clear"):
            with self.subTest(stage=stage):
                service, request, clear, write, control = self._busy_service(stage)
                services = ChannelCompactPollServices(
                    lambda *args, **kwargs: service.inject(*args, control=control, **kwargs)
                )
                state = poll_pending_compaction(40.0, 1, bytes([13]), None,
                    ChannelCompactPollState(), self.options(), services)
                self.assertEqual(40.0, state.last_poll_at)
                write.assert_called_once()
                if stage == "write":
                    control.command_failed.assert_called_once_with(request, "command_submission_uncertain", uncertain=True)
                    clear.assert_not_called()
                else:
                    control.command_submitted.assert_called_once_with(request)
                    control.command_failed.assert_called_once_with(request, "command_submission_uncertain", uncertain=True)

    def test_other_errors_are_not_hidden_as_contention(self):
        services = ChannelCompactPollServices(Mock(side_effect=OSError(errno.EIO, "fixture I/O")))
        with self.assertRaises(OSError):
            poll_pending_compaction(40.0, 1, b"\r", None, ChannelCompactPollState(), self.options(), services)


if __name__ == "__main__":
    unittest.main()
