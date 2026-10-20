import dataclasses
import errno
import unittest
from unittest.mock import Mock, patch

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.channel_pending_poll import (
    ChannelPendingInjectionOptions,
    ChannelPendingPollPolicy,
    ChannelPendingPollServices,
    ChannelPendingPollState,
    poll_pending_channel_messages,
)
from ciel_runtime_support.channel_terminal_proxy import (
    ChannelTerminalPolling,
    _runtime_interaction_bytes,
)


class ChannelPendingPollTests(unittest.TestCase):
    def test_runtime_interaction_notice_clears_stale_child_tui_columns(self):
        rendered = _runtime_interaction_bytes("line one\nhttps://example.test/full")

        self.assertTrue(rendered.startswith(b"\r\n\x1b[2K\r"))
        self.assertTrue(rendered.endswith(b"\r\n\x1b[2K\r"))
        self.assertIn(b"line one\x1b[K\r\n\x1b[2K\rhttps://example.test/full\x1b[K", rendered)

    def options(self, *, enabled=True, confirm_submit=True):
        return ChannelPendingInjectionOptions(
            enabled, False, True, True, 2, confirm_submit, False, 0.1
        )

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

    def terminal_polling(self, *, active_tool_call=False, active_turn=False):
        return ChannelTerminalPolling(
            inject_compact=lambda *args, **kwargs: None,
            file_marker=lambda: (0.0, 0),
            should_check=lambda *args: False,
            active_tool_call=lambda: active_tool_call,
            active_turn=lambda: active_turn,
            inject_pending=lambda *args, **kwargs: args[1],
            wake_state=lambda _message_id: "missing",
            inflight_effects=lambda: None,
        )

    def test_terminal_input_busy_covers_active_turn_between_tool_calls(self):
        self.assertTrue(self.terminal_polling(active_tool_call=True).input_busy())
        self.assertTrue(self.terminal_polling(active_turn=True).input_busy())
        self.assertFalse(self.terminal_polling().input_busy())

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
        self.assertTrue(observed[0]["display_llm_delivery_body"])

    def test_terminal_availability_defaults_true_and_false_is_forwarded(self):
        self.assertTrue(self.options().terminal_available)
        observed = []
        state = ChannelPendingPollState(last_id=10)
        poll_pending_channel_messages(
            1.0, None, b"\r", state,
            dataclasses.replace(self.options(), terminal_available=False),
            self.policy(),
            self.services(inject=lambda *args, **kwargs: observed.append(kwargs) or args[1]),
        )
        self.assertFalse(observed[0]["terminal_available"])

    def test_pre_submit_contention_preserves_cursor_marker_and_inflight_until_next_poll(self):
        for busy in (False, True):
            for stage in ("ensure_cursor", "inject_pending"):
                with self.subTest(busy=busy, stage=stage):
                    lock_busy = ChannelFileLockBusy(errno.EACCES, "secret must not appear in logs")
                    ensure = Mock(side_effect=[lock_busy, 12] if stage == "ensure_cursor" else None, return_value=12)
                    inject = Mock(side_effect=[lock_busy, 20] if stage == "inject_pending" else None, return_value=20)
                    logs = []
                    base = self.services(active=busy, logs=logs)
                    services = dataclasses.replace(
                        base,
                        ensure_cursor=ensure,
                        inject_pending=inject,
                        session_socket_ready=lambda: True,
                        should_check=lambda marker, old, recheck, _inflight: recheck or marker != old,
                    )
                    state = ChannelPendingPollState(last_id=10, last_marker=(1.0, 50), last_scan_at=1.0)
                    state.inflight_message_id = 9
                    state.inflight_cursor = 9
                    state.inflight.backlog = [8]
                    before = dataclasses.asdict(state.inflight)
                    options = dataclasses.replace(self.options(), terminal_available=False)
                    poll_pending_channel_messages(2.0, None, b"\r", state, options, self.policy(), services)
                    self.assertEqual(10, state.last_id)
                    self.assertEqual((1.0, 50), state.last_marker)
                    self.assertEqual(1.0, state.last_scan_at)
                    self.assertEqual(2.0, state.last_poll_at)
                    self.assertTrue(state.pending_recheck)
                    self.assertEqual(before, dataclasses.asdict(state.inflight))
                    self.assertIn("stage=pre_submit errno=13", logs[0][1])
                    self.assertNotIn("secret", logs[0][1])
                    counts = (ensure.call_count, inject.call_count)
                    poll_pending_channel_messages(2.1, None, b"\r", state, options, self.policy(), services)
                    self.assertEqual(counts, (ensure.call_count, inject.call_count))
                    poll_pending_channel_messages(2.6, None, b"\r", state, options, self.policy(), services)
                    self.assertEqual(20, state.last_id)
                    self.assertEqual((2.0, 100), state.last_marker)
                    self.assertEqual(2.6, state.last_scan_at)

    def test_generic_oserror_is_not_a_retryable_lock_failure(self):
        failure = OSError(errno.EACCES, "could be a write failure")
        state = ChannelPendingPollState(last_id=10, last_marker=(1.0, 50), last_scan_at=1.0)
        with self.assertRaises(OSError) as caught:
            poll_pending_channel_messages(
                2.0, None, b"\r", state, self.options(), self.policy(),
                self.services(inject=Mock(side_effect=failure)),
            )
        self.assertIs(failure, caught.exception)
        self.assertFalse(state.pending_recheck)
        self.assertEqual(10, state.last_id)
        self.assertEqual((1.0, 50), state.last_marker)

    def test_socket_batch_pre_submit_lock_retries_without_cursor_skip_or_child_cleanup(self):
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages

        messages = [_message(40), _message(41)]
        for message in messages:
            message["meta"] = {"input_transport": "session_socket"}
        harness = _InjectionHarness(messages)
        base = harness.services()
        send = Mock(return_value=True)
        injection_services = dataclasses.replace(
            base,
            io=dataclasses.replace(base.io, write_session_socket=send, session_socket_available=lambda: True),
            policy=dataclasses.replace(base.policy, wake_batch_limit=lambda: 10),
        )
        services = dataclasses.replace(
            self.services(),
            ensure_cursor=lambda: 39,
            inject_pending=lambda *args, **kwargs: inject_pending_channel_messages(
                *args, **kwargs, services=injection_services,
            ),
        )
        state = ChannelPendingPollState(last_id=39)
        options = dataclasses.replace(self.options(confirm_submit=False), terminal_available=False)
        child_terminate = Mock()
        with patch.object(harness.delivery, "failure_reason", side_effect=["", ChannelFileLockBusy(errno.EACCES, "busy")]):
            try:
                poll_pending_channel_messages(1.0, None, b"\r", state, options, self.policy(), services)
            except Exception:
                child_terminate()
                raise
        child_terminate.assert_not_called()
        self.assertEqual(39, state.last_id)
        self.assertEqual((0.0, -1), state.last_marker)
        self.assertEqual(set(), harness.delivery.delivered)
        send.assert_not_called()
        poll_pending_channel_messages(1.6, None, b"\r", state, options, self.policy(), services)
        self.assertEqual(41, state.last_id)
        send.assert_called_once()
        self.assertEqual([40, 41], [message["id"] for message in send.call_args.args[1]])
        self.assertEqual([41], harness.delivery.commits)
        self.assertEqual([], harness.written)

    def test_post_send_status_lock_is_not_swallowed_as_unsubmitted(self):
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages

        message = _message(40)
        message["meta"] = {"input_transport": "session_socket"}
        harness = _InjectionHarness([message])
        base = harness.services()
        send = Mock(return_value=True)
        injection_services = dataclasses.replace(
            base, io=dataclasses.replace(base.io, write_session_socket=send, session_socket_available=lambda: True),
        )
        services = dataclasses.replace(
            self.services(), ensure_cursor=lambda: 39,
            inject_pending=lambda *args, **kwargs: inject_pending_channel_messages(
                *args, **kwargs, services=injection_services,
            ),
        )
        state = ChannelPendingPollState(last_id=39)
        options = dataclasses.replace(self.options(), terminal_available=False)
        with patch.object(harness.delivery, "record_prompts", side_effect=ChannelFileLockBusy(errno.EACCES, "busy")):
            with self.assertRaises(RuntimeError):
                poll_pending_channel_messages(1.0, None, b"\r", state, options, self.policy(), services)
        send.assert_called_once()
        self.assertFalse(state.pending_recheck)
        self.assertEqual("submission_exception", harness.delivery.failure_reason(40))
        self.assertIsNone(state.inflight_message_id)

    def test_confirmation_does_not_cross_earlier_deferred_socket_message(self):
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
        from ciel_runtime_support.channel_inflight import ChannelInflightEffects, ChannelInflightPolicy, ChannelInflightSnapshot, advance_channel_inflight
        messages = [_message(40), _message(41)]
        for message in messages:
            message["meta"] = {"input_transport": "session_socket"}
        harness = _InjectionHarness(messages, claim_ids={40})
        base = harness.services()
        send = Mock(return_value=True)
        injection_services = dataclasses.replace(
            base, io=dataclasses.replace(base.io, write_session_socket=send, session_socket_available=lambda: True),
        )
        services = dataclasses.replace(
            self.services(), ensure_cursor=lambda: 39,
            inject_pending=lambda *args, **kwargs: inject_pending_channel_messages(*args, **kwargs, services=injection_services),
        )
        state = ChannelPendingPollState(last_id=39)
        state.inflight_message_id = 38
        options = dataclasses.replace(self.options(), terminal_available=False)

        poll_pending_channel_messages(1.0, None, bytes([13]), state, options, self.policy(), services)

        self.assertEqual(39, state.last_id)
        self.assertEqual(41, state.inflight_message_id)
        self.assertEqual(39, state.inflight_cursor)
        self.assertEqual([41], [row["id"] for row in send.call_args.args[1]])
        advance_channel_inflight(
            ChannelInflightSnapshot(41, state.inflight_cursor, "completed", 1.0, 1.0, 2.0, state.last_id),
            ChannelInflightPolicy(30, 30, lambda *args: False, False, "fixture", "stale"),
            ChannelInflightEffects(harness.delivery.commit, harness.delivery.complete, Mock(), lambda: 39, Mock()),
        )
        self.assertEqual([39], harness.delivery.commits)
        self.assertFalse(harness.delivery.is_delivered(40))

    def test_confirmation_stops_before_excluded_pending_or_queued_candidate(self):
        from test_channel_pending_injection import _InjectionHarness, _message
        from ciel_runtime_support.channel_pending_injection import inject_pending_channel_messages
        for wake_state in ("pending", "queued"):
            with self.subTest(wake_state=wake_state):
                messages = [_message(40), _message(41)]
                for message in messages:
                    message["meta"] = {"input_transport": "session_socket"}
                harness = _InjectionHarness(messages)
                harness.delivery.mark_delivered(41)
                base = harness.services()
                send = Mock(return_value=True)
                injection_services = dataclasses.replace(
                    base,
                    state=dataclasses.replace(base.state, wake_state_for_message=lambda row, prompt: wake_state if row["id"] == 41 else "missing"),
                    io=dataclasses.replace(base.io, write_session_socket=send, session_socket_available=lambda: True),
                    policy=dataclasses.replace(base.policy, wake_batch_limit=lambda: 10),
                )
                services = dataclasses.replace(
                    self.services(), ensure_cursor=lambda: 39,
                    inject_pending=lambda *args, **kwargs: inject_pending_channel_messages(*args, **kwargs, services=injection_services),
                )
                state = ChannelPendingPollState(last_id=39)

                poll_pending_channel_messages(1.0, None, bytes([13]), state, self.options(), self.policy(), services)

                self.assertEqual(40, state.last_id)
                self.assertEqual(40, state.inflight_message_id)
                self.assertEqual(40, state.inflight_cursor)
                self.assertEqual([40], [row["id"] for row in send.call_args.args[1]])

    def test_batch_commit_cursor_uses_highest_injected_id_not_deferred_return_cursor(self):
        def inject(*args, **kwargs):
            kwargs["injected_message_ids"].extend([20, 21])
            return 10

        state = ChannelPendingPollState(last_id=10)
        poll_pending_channel_messages(
            1.0, 1, b"\r", state, self.options(), self.policy(), self.services(inject=inject)
        )

        self.assertEqual(10, state.last_id)
        self.assertEqual(21, state.inflight_message_id)
        self.assertEqual(21, state.inflight_cursor)

    def test_unconfirmed_single_write_commits_without_inflight_replay(self):
        observed = []

        def inject(*args, **kwargs):
            observed.append(kwargs)
            kwargs["injected_message_ids"].append(20)
            return 20

        state = ChannelPendingPollState(last_id=10)
        poll_pending_channel_messages(
            1.0,
            1,
            b"\r",
            state,
            self.options(confirm_submit=False),
            self.policy(),
            self.services(inject=inject),
        )

        self.assertEqual(20, state.last_id)
        self.assertIsNone(state.inflight_message_id)
        self.assertTrue(observed[0]["commit_cursor"])

    def test_periodic_safety_rescan_recovers_when_file_marker_was_missed(self):
        calls = []
        services = self.services(inject=lambda *args, **kwargs: calls.append(args[1]) or args[1])
        services = ChannelPendingPollServices(
            file_marker=services.file_marker,
            should_check=lambda *_args: False,
            active=services.active,
            ensure_cursor=services.ensure_cursor,
            inject_pending=services.inject_pending,
            log=services.log,
        )
        state = ChannelPendingPollState(last_id=10, last_scan_at=1.0)

        poll_pending_channel_messages(
            4.0, 1, b"\r", state, self.options(), self.policy(), services
        )
        self.assertEqual([], calls)
        poll_pending_channel_messages(
            6.1, 1, b"\r", state, self.options(), self.policy(), services
        )

        self.assertEqual([12], calls)
        self.assertEqual(6.1, state.last_scan_at)

    def test_active_turn_scans_new_input_when_the_session_socket_is_ready(self):
        # Walkie 2026-09-23: the gate held session-socket input until the turn
        # ended, although Claude Code queues a socket frame sent mid-turn.
        calls = []
        base = self.services(active=True, inject=lambda *args, **kwargs: calls.append(args[1]) or args[1])
        services = ChannelPendingPollServices(
            file_marker=base.file_marker,
            should_check=base.should_check,
            active=base.active,
            ensure_cursor=base.ensure_cursor,
            inject_pending=base.inject_pending,
            log=base.log,
            session_socket_ready=lambda: True,
        )
        state = ChannelPendingPollState(last_id=10, last_scan_at=1.0)

        poll_pending_channel_messages(2.0, 1, b"\r", state, self.options(), self.policy(), services)
        self.assertEqual([12], calls)
        self.assertTrue(state.pending_recheck)
        self.assertEqual((2.0, 100), state.last_marker)

        # Same marker, safety rescan not due: no rescan on every poll mid-turn.
        poll_pending_channel_messages(3.0, 1, b"\r", state, self.options(), self.policy(), services)
        self.assertEqual([12], calls)
        # The safety rescan still runs.
        poll_pending_channel_messages(7.5, 1, b"\r", state, self.options(), self.policy(), services)
        self.assertEqual([12, 12], calls)

    def test_active_turn_without_a_session_socket_still_waits(self):
        calls = []
        state = ChannelPendingPollState(last_id=10, last_scan_at=1.0)
        poll_pending_channel_messages(
            2.0, 1, b"\r", state, self.options(), self.policy(),
            self.services(active=True, inject=lambda *args, **kwargs: calls.append(args[1]) or args[1]),
        )
        self.assertEqual([], calls)
        self.assertTrue(state.pending_recheck)

    def test_injection_during_an_unconfirmed_one_keeps_both_awaiting_confirmation(self):
        # E2E 2026-09-23: ids 4, 5, 6 went in one by one during a turn; only 6
        # was tracked, so 4 and 5 never left "submitted".
        next_ids = [[4], [5]]

        def inject(*args, **kwargs):
            ids = next_ids.pop(0)
            kwargs["injected_message_ids"].extend(ids)
            return ids[-1]

        state = ChannelPendingPollState(last_id=3)
        services = self.services(inject=inject)
        poll_pending_channel_messages(1.0, 1, b"\r", state, self.options(), self.policy(), services)
        poll_pending_channel_messages(2.0, 1, b"\r", state, self.options(), self.policy(), services)
        self.assertEqual(5, state.inflight_message_id)
        self.assertEqual([4], state.inflight.backlog)

        state.inflight_message_id = None  # 5 confirmed by the inflight loop
        poll_pending_channel_messages(2.1, 1, b"\r", state, self.options(), self.policy(), services)
        self.assertEqual(4, state.inflight_message_id)
        self.assertEqual(2.1, state.inflight_started_at)
        self.assertIsNone(state.inflight_cursor)
        self.assertEqual([], state.inflight.backlog)

    def test_context_recovery_hard_gate_precedes_busy_socket_scan_and_cursor(self):
        inject, marker, cursor = Mock(), Mock(), Mock()
        services = dataclasses.replace(
            self.services(active=True), session_socket_ready=lambda: True,
            external_admission_paused=lambda: True,
            inject_pending=inject, file_marker=marker, ensure_cursor=cursor,
        )
        state = ChannelPendingPollState(last_id=10, last_marker=(1.0, 20))
        result = poll_pending_channel_messages(
            31.0, 1, b"cr", state, self.options(), self.policy(), services,
        )
        self.assertIs(state, result)
        self.assertTrue(state.pending_recheck)
        self.assertEqual(10, state.last_id)
        self.assertEqual((1.0, 20), state.last_marker)
        self.assertEqual(0.0, state.last_scan_at)
        inject.assert_not_called()
        marker.assert_not_called()
        cursor.assert_not_called()

    def test_compaction_pause_does_not_age_an_unconfirmed_wake(self):
        state = ChannelPendingPollState(last_id=10)
        state.inflight_message_id = 11
        state.inflight_started_at = 20.0
        self.assertTrue(state.inflight.hold_for_recovery(25.0, True))
        self.assertTrue(state.inflight.hold_for_recovery(500.0, True))
        self.assertEqual(20.0, state.inflight_started_at)
        self.assertFalse(state.inflight.hold_for_recovery(525.0, False))
        self.assertEqual(520.0, state.inflight_started_at)
        self.assertEqual(11, state.inflight_message_id)


if __name__ == "__main__":
    unittest.main()
