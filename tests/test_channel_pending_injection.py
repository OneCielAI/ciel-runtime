"""Root-cause guards for channel wake delivery.

Live 2026-09-22 (instance 9469, workspace g:\\ciel-Walkie): 32 messages sat at
``queued`` behind a durable cursor that had advanced past them, delivered
messages were recorded ``failed/stale_unconfirmed`` while the CLI still held
them, and the instance log went silent at its rotation cap.  These tests pin
the three repairs: the cursor may not move past a deferred message, a failed
record may not stall the scan, and a log line is never dropped.
"""

import dataclasses
import errno
import json
import pathlib
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.channel_pending_injection import (
    ChannelInjectionIO,
    ChannelInjectionPolicy,
    ChannelInjectionPrompts,
    ChannelInjectionServices,
    ChannelInjectionState,
    ChannelInjectionWakeStore,
    inject_pending_channel_messages,
)
from ciel_runtime_support.channel_runtime_environment import (
    ChannelRuntimeEnvironmentPolicy,
)
from ciel_runtime_support.channel_transcript import (
    ChannelWakeTranscriptServices,
    queued_dropped_from_text,
)
from ciel_runtime_support.channel_wake_delivery_repository import (
    ChannelWakeDeliveryRepository,
)
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository, WakeAttemptConflict, WakeHistoryUnavailable
from ciel_runtime_support.runtime_logging import LogLevelRepository, RouterFileLogger


def _message(message_id: int, *, channel: str = "external:default") -> dict:
    return {"id": message_id, "channel": channel, "message": f"body-{message_id}"}


class _RecordingRepository(ChannelWakeDeliveryRepository):
    """Delivery repository that also records the cursor commits."""

    def __init__(self) -> None:
        super().__init__(
            lock=threading.Lock(),
            delivered=set(),
            prompts={},
            batches={},
            clear_claim=lambda _message_id: None,
            commit_cursor=lambda _message_id: None,
        )
        self.commits: list[int] = []

    def commit(self, message_id: int) -> None:
        self.commits.append(message_id)


class _InjectionHarness:
    def __init__(self, messages: list[dict], *, claim_ids: set[int] | None = None) -> None:
        self.messages = {int(message["id"]): message for message in messages}
        self.claim_ids = set(claim_ids or set())
        self.delivery = _RecordingRepository()
        self.written: list[str] = []
        self.logs: list[tuple[str, str]] = []

    def services(self) -> ChannelInjectionServices:
        def read_messages(last_id: int, *_args, **_kwargs) -> list[dict]:
            return [
                self.messages[message_id]
                for message_id in sorted(self.messages)
                if message_id > last_id
            ]

        def claim_for_nonblocking_scan(message_id: int) -> bool:
            return message_id in self.claim_ids

        return ChannelInjectionServices(
            state=ChannelInjectionState(
                active_tool_call=lambda: False,
                active_turn=lambda: False,
                recover_cursor=lambda last_id: last_id,
                pending_scan_limit=lambda: 50,
                superseded_ids=lambda _candidates: set(),
                message_is_web_chat=lambda _message: False,
                message_skip_reason=lambda _message: "",
                event_identity_key=lambda _message: (),
                wake_state_for_message=lambda _message, _prompt=None: "missing",
                queued_wake_is_stale=lambda _message, _prompt=None: False,
            ),
            prompts=ChannelInjectionPrompts(
                llm_delivery=lambda messages: " ".join(str(m.get("message")) for m in messages),
                visible_llm_delivery=lambda messages: " ".join(str(m.get("message")) for m in messages),
                web_chat=lambda messages: " ".join(str(m.get("message")) for m in messages),
                standard=lambda messages: " ".join(str(m.get("message")) for m in messages),
                enter_bytes=lambda _value: b"\r",
                enter_label=lambda _value: "cr",
            ),
            wake_store=ChannelInjectionWakeStore(
                claim_for_nonblocking_scan=claim_for_nonblocking_scan,
                claim_prompt=lambda _message_id, _prompt: True,
                clear_claim=lambda _message_id: None,
                release_stale=lambda _message_id, _commit: None,
                lifecycle=self.delivery,
                commit_cursor=self.delivery.commit,
            ),
            io=ChannelInjectionIO(
                inject_lock=threading.Lock(),
                read_messages=read_messages,
                write_prompt=self._write_prompt,
                log=lambda level, message: self.logs.append((level, message)),
            ),
            policy=ChannelInjectionPolicy(
                wake_batch_limit=lambda: 1,
                replay_skip_reason=lambda _message: "",
            ),
        )

    def _write_prompt(self, _master_fd, prompt: str, *_args, **_kwargs) -> bool:
        self.written.append(prompt)
        return True


class DeferralFloorTests(unittest.TestCase):
    def test_cursor_stops_before_a_skipped_message(self) -> None:
        harness = _InjectionHarness([_message(10), _message(11)], claim_ids={10})

        returned = inject_pending_channel_messages(
            99,
            9,
            None,
            skip_blocking_wake_states=True,
            services=harness.services(),
        )

        self.assertEqual(harness.written, ["body-11"])
        # The scan reached id 11, but id 10 still has to be delivered, so the
        # durable cursor must stay below it - 9, not 11.
        self.assertEqual(harness.delivery.commits, [9])
        self.assertEqual(returned, 9)
        self.assertTrue(
            any("stdin_wake_claimed_continue" in message for _, message in harness.logs)
        )

    def test_cursor_advances_when_nothing_is_deferred(self) -> None:
        harness = _InjectionHarness([_message(20)])

        returned = inject_pending_channel_messages(
            99,
            19,
            None,
            skip_blocking_wake_states=True,
            services=harness.services(),
        )

        self.assertEqual(harness.delivery.commits, [20])
        self.assertEqual(returned, 20)


class FailedRecordTests(unittest.TestCase):
    def test_failed_message_does_not_stall_later_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = pathlib.Path(temp_dir) / "runtime-input-status.jsonl"
            status = RuntimeInputStatusRepository(
                status_path,
                publish_event=Mock(),
                log=lambda *_args: None,
                lock=threading.Lock(),
            )
            status.transition(30, "queued", data={"input_transport": "session_socket"})
            status.transition(30, "failed", reason="stale_unconfirmed")

            harness = _InjectionHarness([_message(30), _message(31)])
            harness.delivery.status = status

            inject_pending_channel_messages(99, 29, None, services=harness.services())

        self.assertEqual(harness.written, ["body-31"])
        self.assertEqual(harness.delivery.commits, [31])
        self.assertTrue(
            any("prior_submission_failed" in message for _, message in harness.logs)
        )


class SessionSocketFallbackTests(unittest.TestCase):
    """ara/edward 2026-10-03: Codex has no session socket, so these messages
    were typed in full into the Windows TUI and lost characters."""

    def _run(self, wake_for_llm_delivery: bool, *, socket_configured: bool | None = None, transport: str = "session_socket") -> _InjectionHarness:
        message = _message(40)
        message["meta"] = {"input_transport": transport}
        harness = _InjectionHarness([message])
        services = harness.services()
        if socket_configured is not None:
            # Codex: the Claude socket client is wired in but never configured.
            harness.socket_sends = []
            services = dataclasses.replace(
                services,
                io=dataclasses.replace(
                    services.io,
                    write_session_socket=lambda prompt, _messages: harness.socket_sends.append(prompt) or socket_configured,
                    session_socket_available=lambda: socket_configured,
                ),
            )
        services = dataclasses.replace(
            services,
            prompts=dataclasses.replace(
                services.prompts,
                llm_delivery=lambda messages: "[ciel-wake] pending_ids="
                + ",".join(str(m["id"]) for m in messages),
                standard=lambda messages: "TYPED " + " ".join(str(m["message"]) for m in messages),
            ),
        )
        inject_pending_channel_messages(
            99, 39, None, wake_for_llm_delivery=wake_for_llm_delivery, services=services
        )
        return harness

    def test_direct_gateway_without_socket_types_the_whole_body(self) -> None:
        from unittest import mock
        with mock.patch("ciel_runtime_support.launch_data_path.model_http_bypassed", return_value=True):
            harness = self._run(True, socket_configured=False)
        self.assertEqual(["TYPED body-40"], harness.written)

    def test_direct_gateway_with_socket_sends_the_whole_message(self) -> None:
        from unittest import mock
        with mock.patch("ciel_runtime_support.launch_data_path.model_http_bypassed", return_value=True):
            harness = self._run(True, socket_configured=True, transport="router")
        self.assertEqual(1, len(harness.socket_sends))
        self.assertEqual([], harness.written)

    def test_falls_back_to_the_router_block_when_router_delivery_is_on(self) -> None:
        harness = self._run(True)
        self.assertEqual(["[ciel-wake] pending_ids=40"], harness.written)
        self.assertTrue(any("fallback=router" in message for _, message in harness.logs))

    def test_unconfigured_socket_goes_to_the_router_block_without_trying_it(self) -> None:
        harness = self._run(True, socket_configured=False)
        self.assertEqual(["[ciel-wake] pending_ids=40"], harness.written)
        self.assertEqual([], harness.socket_sends)

    def test_configured_socket_is_still_used(self) -> None:
        harness = self._run(True, socket_configured=True)
        self.assertEqual(1, len(harness.socket_sends))
        self.assertEqual([], harness.written)

    def test_falls_back_to_typing_without_router_delivery(self) -> None:
        harness = self._run(False)
        self.assertEqual(["TYPED body-40"], harness.written)
        self.assertTrue(any("fallback=tty" in message for _, message in harness.logs))


class SocketOnlyInjectionTests(unittest.TestCase):
    def services(self, harness, *, available=True, send=None, busy=False):
        base = harness.services()
        return dataclasses.replace(
            base,
            state=dataclasses.replace(base.state, active_turn=lambda: busy),
            io=dataclasses.replace(
                base.io,
                write_session_socket=send or Mock(return_value=True),
                session_socket_available=lambda: available,
            ),
            policy=dataclasses.replace(base.policy, wake_batch_limit=lambda: 10),
        )

    def socket_message(self, message_id):
        message = _message(message_id)
        message["meta"] = {"input_transport": "session_socket"}
        return message

    def ownership(self, harness, root):
        status = RuntimeInputStatusRepository(
            pathlib.Path(root) / "status.jsonl", Mock(), lambda *_: None, threading.RLock())
        harness.delivery.status = status
        harness.delivery.bind_child_epoch("owned-child")
        return status

    def test_durable_terminal_rows_with_missing_transcript_are_not_resent(self):
        for terminal in ("replied", "skipped", "failed", "interrupted_uncertain"):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as root:
                harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
                status = self.ownership(harness, root)
                status.transition(40, "queued")
                if terminal in {"replied", "interrupted_uncertain"}:
                    status.begin_attempt([40], "old-child")
                status.transition(40, terminal)
                status.transition(41, "queued")
                services = self.services(harness)
                self.assertEqual(41, inject_pending_channel_messages(
                    None, 39, services=services, terminal_available=False))
                self.assertEqual(terminal, status.get(40)["status"])
                sent = services.io.write_session_socket.call_args.args
                self.assertEqual([41], [m["id"] for m in sent[1]])
                self.assertNotIn("body-40", sent[0])
                self.assertEqual("owned-child", status.get(41)["data"]["child_epoch"])

    def test_racing_completion_rebuilds_batch_without_partial_ownership(self):
        with tempfile.TemporaryDirectory() as root:
            harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
            status = self.ownership(harness, root)
            for message_id in (40, 41):
                status.transition(message_id, "queued")
            services = self.services(harness)
            raced = False

            def wake(message, prompt):
                nonlocal raced
                if message["id"] == 41 and not raced:
                    raced = True
                    status.begin_attempt([41], "other-child")
                    status.transition(41, "replied")
                return "missing"

            services = dataclasses.replace(services, state=dataclasses.replace(
                services.state, wake_state_for_message=wake))
            with self.assertRaises(WakeAttemptConflict) as caught:
                inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
            self.assertEqual(41, caught.exception.request_id)
            self.assertEqual("queued", status.get(40)["status"])
            self.assertEqual({}, harness.delivery.attempts.by_message)
            self.assertEqual(set(), harness.delivery.delivered)
            self.assertEqual([], harness.delivery.commits)
            services.io.write_session_socket.assert_not_called()
            self.assertEqual(41, inject_pending_channel_messages(
                None, 39, services=services, terminal_available=False))
            sent = services.io.write_session_socket.call_args.args
            self.assertEqual([40], [m["id"] for m in sent[1]])
            self.assertNotIn("body-41", sent[0])
            services.io.write_session_socket.assert_called_once()

    def test_submitted_owner_is_not_replayed_or_passed_by_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
            status = self.ownership(harness, root)
            old = status.begin_attempt([40], "old-child")
            status.transition(41, "queued")
            services = self.services(harness)
            self.assertEqual(39, inject_pending_channel_messages(
                None, 39, services=services, terminal_available=False))
            self.assertEqual([39], harness.delivery.commits)
            self.assertEqual(old, status.get(40)["data"]["attempt_id"])
            self.assertEqual([41], [m["id"] for m in services.io.write_session_socket.call_args.args[1]])

    def test_corrupt_history_holds_poll_without_crashing_or_sending(self):
        from ciel_runtime_support.channel_pending_poll import (
            ChannelPendingPollState, ChannelPendingPollPolicy,
            ChannelPendingPollServices, ChannelPendingInjectionOptions,
            poll_pending_channel_messages,
        )
        damages = [b"{incomplete", b"not-json" + bytes([10]), bytes([10]), bytes([255, 10])]
        for status_name in ("queued", "submitted", "replied"):
            row = {"request_id": 40, "status": status_name,
                   "updated_at": "2026-10-20T09:00:00+00:00", "updated_at_epoch": 1}
            damages.append(bytes(17) + json.dumps(row).encode() + bytes([10]))
        valid_row = json.dumps({"request_id": 40, "status": "queued"}).encode()
        damages.extend((valid_row, valid_row + bytes([0, 10]),
                        valid_row[:5] + bytes(1) + valid_row[5:] + bytes([10]),
                        b"{\"request_id\":40,\"status\":\"queued\",\"status\":\"replied\"}" + bytes([10]),
                        b"{\"request_id\":40,\"status\":\"queued\",\"data\":{\"x\":NaN}}" + bytes([10])))
        for damage in damages:
            with self.subTest(damage=repr(damage)), tempfile.TemporaryDirectory() as root:
                harness = _InjectionHarness([self.socket_message(40)])
                status = self.ownership(harness, root)
                status.transition(40, "queued")
                valid = status.path.read_bytes()
                with status.path.open("ab") as stream:
                    stream.write(damage)
                damaged = status.path.read_bytes()
                services = self.services(harness)
                logs = []
                polling = ChannelPendingPollServices(
                    file_marker=lambda: (1.0, 10), should_check=lambda *_: True,
                    active=lambda: False, ensure_cursor=lambda: 39,
                    inject_pending=lambda writer, cursor, enter, **options:
                        inject_pending_channel_messages(writer, cursor, enter, services=services, **options),
                    log=lambda level, message: logs.append((level, message)),
                )
                options = ChannelPendingInjectionOptions(True, False, False, False, 1, True, False, 0,
                                                         terminal_available=False)
                state = ChannelPendingPollState(last_id=39, last_marker=(0.0, 0))
                policy = ChannelPendingPollPolicy("test", "busy")
                for now in (31.0, 31.1, 32.0):
                    self.assertIs(state, poll_pending_channel_messages(
                        now, None, bytes([13]), state, options, policy, polling))
                self.assertEqual(39, state.last_id)
                self.assertEqual((0.0, 0), state.last_marker)
                self.assertIsNone(state.inflight_message_id)
                self.assertTrue(state.pending_recheck)
                self.assertEqual(damaged, status.path.read_bytes())
                self.assertEqual([], harness.delivery.commits)
                self.assertEqual({}, harness.delivery.attempts.by_message)
                services.io.write_session_socket.assert_not_called()
                self.assertEqual(1, len(logs))
                self.assertIn("reason=wake_history_incomplete", logs[0][1])
                self.assertIn("line=2", logs[0][1])
                # Only the isolated test repairs its own fixture. Production
                # never deletes or rewrites an ambiguous ownership record.
                status.path.write_bytes(valid)
                poll_pending_channel_messages(33.0, None, bytes([13]), state, options, policy, polling)
                services.io.write_session_socket.assert_called_once()

    def test_corrupt_history_claim_cleanup_does_not_become_safe_retry(self):
        harness = _InjectionHarness([_message(40)])
        services = harness.services()
        busy = WakeHistoryUnavailable(2)
        services = dataclasses.replace(services, wake_store=dataclasses.replace(
            services.wake_store, clear_claim=Mock(side_effect=busy)))
        with patch.object(harness.delivery, "begin_attempt", side_effect=busy):
            with self.assertRaises(RuntimeError) as caught:
                inject_pending_channel_messages(123, 39, services=services, wake_for_llm_delivery=True)
        self.assertNotIsInstance(caught.exception, WakeHistoryUnavailable)
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)

    def test_post_send_history_failure_is_not_a_pre_submit_deferral(self):
        harness = _InjectionHarness([self.socket_message(40)])
        services = self.services(harness, send=Mock(side_effect=WakeHistoryUnavailable(2)))
        with self.assertRaises(RuntimeError) as caught:
            inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
        self.assertNotIsInstance(caught.exception, WakeHistoryUnavailable)
        services.io.write_session_socket.assert_called_once()

    def test_post_send_conflict_is_not_safe_to_retry(self):
        harness = _InjectionHarness([self.socket_message(40)])
        services = self.services(harness, send=Mock(side_effect=WakeAttemptConflict(40, "replied")))
        with self.assertRaises(RuntimeError) as caught:
            inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
        self.assertNotIsInstance(caught.exception, WakeAttemptConflict)
        services.io.write_session_socket.assert_called_once()

    def test_unavailable_socket_defers_before_claim_or_delivery_mark(self):
        for busy in (False, True):
            for wake in (False, True):
                with self.subTest(busy=busy, wake=wake):
                    harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
                    services = self.services(harness, available=False, busy=busy)
                    claim = Mock(side_effect=AssertionError("must not claim"))
                    services = dataclasses.replace(
                        services,
                        wake_store=dataclasses.replace(
                            services.wake_store,
                            claim_prompt=claim,
                            claim_for_nonblocking_scan=claim,
                        ),
                    )
                    returned = inject_pending_channel_messages(
                        None, 39, services=services, terminal_available=False,
                        wake_for_llm_delivery=wake, skip_blocking_wake_states=True,
                    )
                    self.assertEqual(39, returned)
                    self.assertEqual([], harness.written)
                    self.assertEqual([], harness.delivery.commits)
                    self.assertEqual(set(), harness.delivery.delivered)
                    services.io.write_session_socket.assert_not_called()
                    claim.assert_not_called()

    def test_irrelevant_terminal_records_do_not_block_later_socket(self):
        for reason in ("noise", "superseded", "completed", "delivered"):
            with self.subTest(reason=reason):
                first = _message(40)
                first["meta"] = {"input_transport": "tty"}
                harness = _InjectionHarness([first, self.socket_message(41)])
                services = self.services(harness)
                if reason == "noise":
                    services = dataclasses.replace(services, state=dataclasses.replace(
                        services.state, message_skip_reason=lambda row: "heartbeat" if row["id"] == 40 else "",
                    ))
                elif reason == "superseded":
                    services = dataclasses.replace(services, state=dataclasses.replace(
                        services.state, superseded_ids=lambda rows: {40},
                    ))
                elif reason == "completed":
                    services = dataclasses.replace(services, state=dataclasses.replace(
                        services.state, wake_state_for_message=lambda row, prompt: "completed" if row["id"] == 40 else "missing",
                    ))
                else:
                    harness.delivery.mark_delivered(40)

                returned = inject_pending_channel_messages(None, 39, services=services, terminal_available=False)

                self.assertEqual(41, returned)
                services.io.write_session_socket.assert_called_once()
                self.assertEqual([41], [row["id"] for row in services.io.write_session_socket.call_args.args[1]])
                self.assertEqual([], harness.written)

    def test_different_socket_batch_does_not_advance_past_unsent_candidate(self):
        first = self.socket_message(40)
        second = self.socket_message(41)
        second["meta"]["raw_injection"] = True
        harness = _InjectionHarness([first, second])
        services = self.services(harness)

        self.assertEqual(40, inject_pending_channel_messages(None, 39, services=services, terminal_available=False))
        self.assertEqual([40], [row["id"] for row in services.io.write_session_socket.call_args.args[1]])
        self.assertEqual(41, inject_pending_channel_messages(None, 40, services=services, terminal_available=False))
        self.assertEqual([41], [row["id"] for row in services.io.write_session_socket.call_args.args[1]])
        self.assertEqual([40, 41], harness.delivery.commits)

    def test_non_socket_input_defers_without_leaping_to_later_socket(self):
        for transport in ("tty", "router"):
            with self.subTest(transport=transport):
                message = _message(40)
                message["meta"] = {"input_transport": transport}
                harness = _InjectionHarness([message, self.socket_message(41)])
                services = self.services(harness)
                claim = Mock(side_effect=AssertionError("must not claim"))
                services = dataclasses.replace(
                    services,
                    wake_store=dataclasses.replace(services.wake_store, claim_prompt=claim),
                )
                with patch("ciel_runtime_support.launch_data_path.model_http_bypassed", return_value=True):
                    returned = inject_pending_channel_messages(
                        None, 39, services=services, terminal_available=False,
                        wake_for_llm_delivery=True,
                    )
                self.assertEqual(39, returned)
                self.assertEqual([], harness.written)
                self.assertEqual(set(), harness.delivery.delivered)
                self.assertEqual([], harness.delivery.commits)
                services.io.write_session_socket.assert_not_called()
                claim.assert_not_called()

    def test_socket_batch_stops_and_commits_before_deferred_non_socket(self):
        message = _message(41)
        message["meta"] = {"input_transport": "tty"}
        harness = _InjectionHarness([self.socket_message(40), message, self.socket_message(42)])
        services = self.services(harness)
        injected = []
        returned = inject_pending_channel_messages(
            None, 39, services=services, terminal_available=False, injected_message_ids=injected,
        )
        self.assertEqual(40, returned)
        self.assertEqual([40], injected)
        self.assertEqual([40], harness.delivery.commits)
        self.assertEqual([40], [m["id"] for m in services.io.write_session_socket.call_args.args[1]])
        self.assertEqual([], harness.written)
        self.assertEqual({40}, harness.delivery.delivered)

    def test_socket_batch_stops_before_a_socket_that_becomes_unavailable(self):
        harness = _InjectionHarness([self.socket_message(40), self.socket_message(41), self.socket_message(42)])
        services = self.services(harness)
        services = dataclasses.replace(
            services, io=dataclasses.replace(
                services.io, session_socket_available=Mock(side_effect=[True, False]),
            ),
        )
        returned = inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
        self.assertEqual(40, returned)
        self.assertEqual([40], harness.delivery.commits)
        self.assertEqual({40}, harness.delivery.delivered)
        self.assertEqual([40], [m["id"] for m in services.io.write_session_socket.call_args.args[1]])
        self.assertEqual([], harness.written)

    def test_wrapper_forwards_socket_only_option(self):
        import ciel_runtime

        context = Mock()
        context.inject_pending.return_value = 39
        commit_cursors = []
        with patch.object(ciel_runtime, "channel_wake_context", return_value=context):
            returned = ciel_runtime._inject_pending_channel_messages(
                None, 39, terminal_available=False, injected_commit_cursors=commit_cursors,
            )
        self.assertEqual(39, returned)
        self.assertFalse(context.inject_pending.call_args.kwargs["terminal_available"])
        self.assertIs(commit_cursors, context.inject_pending.call_args.kwargs["injected_commit_cursors"])

    def test_ready_socket_can_send_during_an_active_turn(self):
        harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
        services = self.services(harness, busy=True)
        injected = []
        returned = inject_pending_channel_messages(
            None, 39, services=services, terminal_available=False,
            wake_for_llm_delivery=True, injected_message_ids=injected,
        )
        self.assertEqual(41, returned)
        self.assertEqual([40, 41], injected)
        services.io.write_session_socket.assert_called_once()
        self.assertEqual([], harness.written)
        self.assertEqual([41], harness.delivery.commits)

    def test_failed_send_has_no_tty_fallback_and_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = RuntimeInputStatusRepository(
                pathlib.Path(tmp) / "status.jsonl", publish_event=Mock(),
                log=lambda *_args: None, lock=threading.Lock(),
            )
            status.transition(40, "queued", data={"input_transport": "session_socket"})
            harness = _InjectionHarness([self.socket_message(40)])
            harness.delivery.status = status
            send = Mock(return_value=False)
            services = self.services(harness, send=send)
            injected = []
            returned = inject_pending_channel_messages(
                None, 39, services=services, terminal_available=False,
                injected_message_ids=injected,
            )
            self.assertEqual(39, returned)
            self.assertEqual("prompt_not_submitted", status.get(40)["reason"])
            self.assertEqual("failed", status.get(40)["status"])
            returned = inject_pending_channel_messages(
                None, returned, services=services, terminal_available=False,
            )
        send.assert_called_once()
        self.assertEqual(40, returned)
        self.assertEqual([], harness.written)
        self.assertEqual([], injected)
        self.assertEqual([], harness.delivery.commits)

    def test_status_failure_after_socket_send_remains_failed_without_resending(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = RuntimeInputStatusRepository(
                pathlib.Path(tmp) / "status.jsonl", publish_event=Mock(),
                log=lambda *_args: None, lock=threading.Lock(),
            )
            status.transition(40, "queued")
            status.publish_event = Mock(side_effect=[OSError(errno.EACCES, "status publisher denied"), None])
            harness = _InjectionHarness([self.socket_message(40)])
            harness.delivery.status = status
            send = Mock(return_value=True)
            services = self.services(harness, send=send)
            with self.assertRaises(OSError) as caught:
                inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
            self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)
            self.assertEqual("failed", status.get(40)["status"])
            self.assertEqual("submission_exception", status.get(40)["reason"])
            self.assertEqual(40, inject_pending_channel_messages(None, 39, services=services, terminal_available=False))
        send.assert_called_once()
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)

    def test_pre_submit_lock_on_later_candidate_leaves_earlier_input_unmarked(self):
        harness = _InjectionHarness([self.socket_message(40), self.socket_message(41)])
        services = self.services(harness)
        busy = ChannelFileLockBusy(errno.EACCES, "busy")
        with patch.object(harness.delivery, "failure_reason", side_effect=["", busy]):
            with self.assertRaises(ChannelFileLockBusy):
                inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
        self.assertEqual(set(), harness.delivery.delivered)
        self.assertEqual([], harness.delivery.commits)
        services.io.write_session_socket.assert_not_called()
        returned = inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
        self.assertEqual(41, returned)
        self.assertEqual([40, 41], [m["id"] for m in services.io.write_session_socket.call_args.args[1]])

    def test_post_send_lock_errors_are_not_safe_to_replay(self):
        for stage in ("send", "record_prompts", "commit_cursor", "failure_bookkeeping"):
            with self.subTest(stage=stage):
                harness = _InjectionHarness([self.socket_message(40)])
                busy = ChannelFileLockBusy(errno.EACCES, "private payload must not be logged")
                send = Mock(return_value=stage != "failure_bookkeeping")
                if stage == "send":
                    send.side_effect = busy
                services = self.services(harness, send=send)
                if stage == "commit_cursor":
                    services = dataclasses.replace(
                        services, wake_store=dataclasses.replace(
                            services.wake_store, commit_cursor=Mock(side_effect=busy),
                        ),
                    )
                with patch.object(harness.delivery, "record_prompts", wraps=harness.delivery.record_prompts) as record:
                    if stage == "record_prompts":
                        record.side_effect = busy
                    with patch.object(harness.delivery, "clear_claim", side_effect=busy if stage == "failure_bookkeeping" else None):
                        with self.assertRaises(RuntimeError) as caught:
                            inject_pending_channel_messages(None, 39, services=services, terminal_available=False)
                self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)
                send.assert_called_once()
                self.assertEqual([], harness.written)
                self.assertTrue(any("stage=post_submit" in entry for _, entry in harness.logs))
                self.assertFalse(any("private payload" in entry for _, entry in harness.logs))
                if stage != "commit_cursor":
                    self.assertTrue(harness.delivery.failure_reason(40))


class PartialClaimTests(unittest.TestCase):
    def services(self, harness, claim, clear):
        base = harness.services()
        return dataclasses.replace(
            base,
            policy=dataclasses.replace(base.policy, wake_batch_limit=lambda: 10),
            wake_store=dataclasses.replace(base.wake_store, claim_prompt=claim, clear_claim=clear),
        )

    def test_claim_refusal_releases_only_claims_created_by_this_attempt(self):
        harness = _InjectionHarness([_message(40), _message(41), _message(42)])
        claims = {41: "another scan", 77: "unrelated"}
        cleared = []

        def claim(message_id, prompt):
            if message_id in claims:
                return False
            claims[message_id] = prompt
            return True

        def clear(message_id):
            cleared.append(message_id)
            claims.pop(message_id, None)

        returned = inject_pending_channel_messages(
            99, 39, services=self.services(harness, claim, clear), wake_for_llm_delivery=True,
        )
        self.assertEqual(39, returned)
        self.assertEqual([40], cleared)
        self.assertEqual({41: "another scan", 77: "unrelated"}, claims)
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)
        self.assertFalse(harness.delivery.failed)

    def test_pre_submit_acquisition_failure_releases_previous_claims(self):
        harness = _InjectionHarness([_message(40), _message(41), _message(42)])
        busy = ChannelFileLockBusy(errno.EACCES, "lock busy")
        claim = Mock(side_effect=[True, True, busy])
        clear = Mock()
        with self.assertRaises(ChannelFileLockBusy) as caught:
            inject_pending_channel_messages(
                99, 39, services=self.services(harness, claim, clear), wake_for_llm_delivery=True,
            )
        self.assertIs(busy, caught.exception)
        self.assertEqual([40, 41], [call.args[0] for call in clear.call_args_list])
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)
        self.assertFalse(harness.delivery.failed)

    def test_ambiguous_claim_write_is_not_changed_to_acquisition_contention(self):
        harness = _InjectionHarness([_message(40), _message(41)])
        failure = OSError(errno.EACCES, "claim write denied")
        claim = Mock(side_effect=[True, failure])
        clear = Mock()
        with self.assertRaises(OSError) as caught:
            inject_pending_channel_messages(
                99, 39, services=self.services(harness, claim, clear), wake_for_llm_delivery=True,
            )
        self.assertIs(failure, caught.exception)
        self.assertNotIsInstance(caught.exception, ChannelFileLockBusy)
        clear.assert_called_once_with(40)
        self.assertEqual([], harness.written)

    def test_claim_cleanup_failure_is_not_safe_to_retry_and_attempts_all_owned_claims(self):
        harness = _InjectionHarness([_message(40), _message(41), _message(42)])
        claim = Mock(side_effect=[True, True, False])
        clear = Mock(side_effect=[ChannelFileLockBusy(errno.EACCES, "busy"), None])
        with self.assertRaises(RuntimeError):
            inject_pending_channel_messages(
                99, 39, services=self.services(harness, claim, clear), wake_for_llm_delivery=True,
            )
        self.assertEqual([40, 41], [call.args[0] for call in clear.call_args_list])
        self.assertEqual([], harness.written)
        self.assertFalse(harness.delivery.failed)


class QueuedStalePolicyTests(unittest.TestCase):
    def test_queued_is_not_stale_by_age(self) -> None:
        policy = ChannelRuntimeEnvironmentPolicy(environment={}, launch_recent_default=0.0)
        self.assertFalse(policy.inflight_is_stale("queued", 100.0, 100.0 + 3600.0, 180.0))
        self.assertTrue(policy.inflight_is_stale("unknown", 100.0, 100.0 + 181.0, 180.0))
        self.assertFalse(policy.inflight_is_stale("unknown", 100.0, 100.0 + 60.0, 180.0))


class QueuedDroppedEvidenceTests(unittest.TestCase):
    def _services(self) -> ChannelWakeTranscriptServices:
        return ChannelWakeTranscriptServices(
            claim_prompt=lambda _message_id: "",
            prompt_references_message_id=(
                lambda text, message_id, _prompts: f"id={message_id}" in str(text or "")
            ),
            prompt_message_ids=lambda _text: [],
            now=time.time,
        )

    def test_removed_queue_command_is_dropped(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
            ]
        )
        self.assertTrue(queued_dropped_from_text(7, text, None, self._services()))

    def test_queued_command_without_removal_is_in_flight(self) -> None:
        text = json.dumps(
            {"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))

    def test_re_enqueued_command_is_in_flight_again(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
            ]
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))

    def test_real_user_turn_clears_the_drop(self) -> None:
        text = "\n".join(
            [
                json.dumps({"type": "queue-operation", "operation": "enqueue", "content": "wake id=7"}),
                json.dumps({"type": "queue-operation", "operation": "remove", "content": "wake id=7"}),
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": [{"type": "text", "text": "wake id=7"}]},
                    }
                ),
            ]
        )
        self.assertFalse(queued_dropped_from_text(7, text, None, self._services()))


class LateCompletionStatusTests(unittest.TestCase):
    def _status(self, path: pathlib.Path) -> RuntimeInputStatusRepository:
        return RuntimeInputStatusRepository(
            path, publish_event=Mock(), log=lambda *_args: None, lock=threading.Lock()
        )

    def test_failed_record_is_corrected_by_a_late_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = self._status(pathlib.Path(temp_dir) / "status.jsonl")
            status.transition(41, "queued")
            status.transition(41, "submitted")
            status.transition(41, "failed", reason="stale_unconfirmed")

            record = status.transition(41, "replied")

            self.assertEqual(record["status"], "replied")
            self.assertEqual(status.get(41)["status"], "replied")

    def test_replied_record_stays_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status = self._status(pathlib.Path(temp_dir) / "status.jsonl")
            status.transition(42, "queued")
            status.transition(42, "submitted")
            status.transition(42, "replied")

            status.transition(42, "failed", reason="stale_unconfirmed")

            self.assertEqual(status.get(42)["status"], "replied")


class RouterLogRotationTests(unittest.TestCase):
    def _logger(self, path: pathlib.Path, max_bytes: int) -> RouterFileLogger:
        levels = LogLevelRepository(
            config_dir=path.parent,
            path=path.parent / "log-level",
            cache={"value": None, "checked_at": 0.0, "file_mtime": 0.0},
            default_level=3,
            environ={},
        )
        return RouterFileLogger(path.parent, path, max_bytes, levels)

    def test_line_survives_a_failed_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            path = root / "router.log"
            path.write_text("x" * 64, encoding="utf-8")
            # A directory at the rotation target makes the plain replace fail,
            # standing in for the Windows handle that froze instance 9469.
            (root / "router.log.1").mkdir()
            logger = self._logger(path, max_bytes=16)

            logger.write("INFO", "survives")

            self.assertIn("survives", path.read_text(encoding="utf-8"))




class CompactionAdmissionTests(unittest.TestCase):
    def test_pressure_holds_busy_whole_message_socket_without_delivery_or_cursor(self):
        message = _message(51)
        message["meta"] = {"input_transport": "session_socket"}
        harness = _InjectionHarness([message])
        services = harness.services()
        socket = Mock(return_value=True)
        services = dataclasses.replace(
            services,
            state=dataclasses.replace(services.state, active_turn=lambda: True),
            io=dataclasses.replace(services.io, write_session_socket=socket,
                                   session_socket_available=lambda: True,
                                   external_admission_paused=lambda: True),
        )
        injected, ceilings = [], []
        returned = inject_pending_channel_messages(
            99, 50, services=services, injected_message_ids=injected,
            injected_commit_cursors=ceilings,
        )
        self.assertEqual(50, returned)
        self.assertEqual([], injected)
        self.assertEqual([], ceilings)
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)
        self.assertFalse(harness.delivery.is_delivered(51))
        self.assertFalse(harness.delivery.failure_reason(51))
        socket.assert_not_called()

    def test_pressure_gate_rechecks_after_candidate_reads_before_claiming(self):
        message = _message(52)
        message["meta"] = {"input_transport": "router"}
        harness = _InjectionHarness([message])
        services = harness.services()
        paused = iter((False, True))
        claim = Mock(return_value=True)
        services = dataclasses.replace(
            services,
            io=dataclasses.replace(services.io, external_admission_paused=lambda: next(paused)),
            wake_store=dataclasses.replace(services.wake_store, claim_prompt=claim),
        )
        self.assertEqual(50, inject_pending_channel_messages(
            99, 50, services=services, wake_for_llm_delivery=True,
        ))
        claim.assert_not_called()
        self.assertEqual([], harness.written)
        self.assertEqual([], harness.delivery.commits)
        self.assertFalse(harness.delivery.failure_reason(52))

    def test_same_durable_backlog_releases_once_only_after_checkpoint_and_shrink(self):
        from test_runtime_compaction_control import _ControllerHarness, boundary

        controller_harness = _ControllerHarness()
        controller_harness.setUp()
        self.addCleanup(controller_harness.doCleanups)
        controller_harness.request()
        message = _message(61)
        message["meta"] = {"input_transport": "session_socket"}
        harness = _InjectionHarness([message])
        services = harness.services()
        sent = []
        services = dataclasses.replace(
            services,
            state=dataclasses.replace(services.state, active_turn=lambda: True),
            io=dataclasses.replace(
                services.io, write_session_socket=lambda prompt, pending: sent.append((prompt, pending)) or True,
                session_socket_available=lambda: True,
                external_admission_paused=controller_harness.control.external_admission_paused,
            ),
        )
        self.assertEqual(60, inject_pending_channel_messages(99, 60, services=services))
        self.assertEqual([], sent)
        request = controller_harness.repository.read()
        self.assertTrue(controller_harness.control.command_ready(request))
        controller_harness.control.command_submitted(request)
        controller_harness.repository.clear(request["id"])
        controller_harness.clock += 1
        controller_harness.append(boundary("session-a", controller_harness.clock))
        self.assertEqual(60, inject_pending_channel_messages(99, 60, services=services))
        controller_harness.request(120)
        self.assertEqual("verified", controller_harness.snapshot()["phase"])
        cursor = inject_pending_channel_messages(99, 60, services=services)
        self.assertEqual(61, cursor)
        self.assertEqual(1, len(sent))
        self.assertEqual([61], harness.delivery.commits)
        self.assertEqual(61, inject_pending_channel_messages(99, cursor, services=services))
        self.assertEqual(1, len(sent))
        self.assertFalse(harness.delivery.failure_reason(61))


if __name__ == "__main__":
    unittest.main()
