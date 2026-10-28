from __future__ import annotations

import queue
import dataclasses
from unittest.mock import Mock
import unittest
from typing import Any

from ciel_runtime_support.codex_app_server import (
    CodexAppServerError, CodexAppServerState, CodexAppServerNotSent, CodexAppServerRejected)
from ciel_runtime_support.codex_app_server_resume import ResumeModel
from ciel_runtime_support.codex_desktop_injection import (
    FULL_ACCESS,
    MAX_SUBMIT_ATTEMPTS,
    SUBSCRIBE_RETRY_SECONDS,
    CodexAppServerChannelInjector,
    CodexDesktopChannelInjector,
    CodexDesktopChannelPorts,
    CodexSessionCommandPorts,
    is_user_thread,
)


def _message(message_id: int, text: str = "hello", *, transport: str = "session_socket", **extra: Any) -> dict[str, Any]:
    message = {
        "id": message_id,
        "channel": "external:default",
        "sender_id": "walkie",
        "recipients": ["all"],
        "kind": "external_event",
        "message": text,
        "meta": {"input_transport": transport, "source": "ciel-runtime-external-event"},
        "delivery": ["llm"],
        "visibility": "private_runtime",
    }
    message.update(extra)
    return message


class _Status:
    """In-memory lifecycle double; durable semantics are covered separately."""
    def __init__(self) -> None:
        self.records: list[tuple[int, str, str]] = []
        self.rows: dict[int, dict] = {}
        self.count = 0

    def get(self, request_id):
        return self.rows.get(request_id)

    def list_latest(self, *, after_request_id=0, status="", limit=500):
        return [row for key, row in sorted(self.rows.items())
                if key > after_request_id and (not status or row["status"] == status)][:limit]

    def begin_attempt(self, ids, child_epoch):
        from ciel_runtime_support.runtime_input_status import WakeAttemptConflict
        for key in ids:
            state = self.rows.get(key, {}).get("status", "")
            if state not in {"", "queued"}:
                raise WakeAttemptConflict(key, state)
        self.count += 1
        attempt = str(self.count)
        for key in ids:
            self.rows[key] = {"request_id": key, "status": "submitted",
                              "data": {"attempt_id": attempt, "child_epoch": child_epoch}}
        return attempt

    def defer_unsubmitted(self, key, attempt):
        row = self.rows[key]
        if row["data"]["attempt_id"] != attempt:
            return False
        self.rows.pop(key)
        return True

    def transition(self, request_id: int, status: str, *, reason: str = "", data: Any = None,
                   expected_attempt=None):
        previous = self.rows.get(request_id, {})
        if expected_attempt is not None and previous.get("data", {}).get("attempt_id") != expected_attempt:
            return previous
        merged = {**previous.get("data", {}), **(data or {})}
        self.rows[request_id] = {"request_id": request_id, "status": status, "data": merged}
        if previous.get("status") != status or status == "submitted" and (data or {}).get("native_turn_id"):
            self.records.append((request_id, status, reason))
        return self.rows[request_id]


class _Client:
    def __init__(self) -> None:
        self.state = CodexAppServerState(thread_id="thread-a")
        self.calls: list[tuple[str, str, str]] = []
        self.notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self.fail_start = 0
        self.fail_steer = False
        self.fail_resume = 0
        self.resumed: list[str] = []
        self.requests: list[tuple[str, Any]] = []
        self.turns_list: dict[str, Any] = {"data": []}
        self.compacted: list[str] = []
        self.started_threads = 0

    def resume_thread(self, thread_id, *, exclude_turns=True, **_kw):
        if self.fail_resume:
            self.fail_resume -= 1
            raise CodexAppServerError("thread/resume failed: no rollout found")
        self.resumed.append(thread_id)
        return {}

    def start_thread(self, *, cwd=None, **_kw):
        self.started_threads += 1
        return {"thread": {"id": f"own-{self.started_threads}"}}

    def compact_thread(self, thread_id):
        self.compacted.append(thread_id)
        return {}

    def request(self, method, params=None, **_kw):
        self.requests.append((method, params))
        return self.turns_list

    def initialize(self, **_kw):
        return {}

    def turn_start(self, thread_id, text, *, cwd=None, **_kw):
        if self.fail_start:
            self.fail_start -= 1
            raise CodexAppServerNotSent("turn/start did not enter write")
        self.calls.append(("start", thread_id, text))
        return {"turn": {"id": f"turn-{len(self.calls)}", "status": "inProgress"}}

    def turn_steer(self, thread_id, expected_turn_id, text, **_kw):
        if self.fail_steer:
            raise CodexAppServerRejected("turn/steer", {"code": -32600, "message": "no active turn to steer"})
        self.calls.append(("steer", thread_id, expected_turn_id))
        return {}

    def next_notification(self, *, timeout=0.0):
        try:
            return self.notifications.get_nowait()
        except queue.Empty:
            return None


class CodexDesktopInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.cursor = 0
        self.status = _Status()
        self.logs: list[str] = []
        self.client = _Client()
        self.injector = CodexDesktopChannelInjector(
            lambda: self.client,  # type: ignore[arg-type,return-value]
            CodexDesktopChannelPorts(
                read_messages=lambda last_id, limit: [m for m in self.messages if m["id"] > last_id][:limit],
                read_cursor=lambda: self.cursor,
                commit_cursor=self._commit,
                status=self.status,
                log=lambda level, message: self.logs.append(f"{level} {message}"),
            ),
            cwd="C:/work",
            version="test",
        )
        self.injector.client = self.client  # type: ignore[assignment]
        self.injector.thread_id = "thread-a"

    def _commit(self, message_id: int) -> None:
        self.cursor = max(self.cursor, message_id)

    def test_terminal_startup_gate_holds_queue_commands_and_direct_submit(self):
        ready = [False]
        self.injector._delivery_ready = lambda: ready[0]
        self.messages = [_message(7)]
        commands = Mock()
        commands.read.return_value = {"id": "compact-1", "action": "compact"}
        self.injector._ports = dataclasses.replace(self.injector._ports, session_commands=commands)
        self.client.notifications.put({"method": "thread/status/changed", "params": {
            "threadId": "thread-a", "status": {"type": "idle"}}})
        self.injector.poll_once()
        self.injector.deliver_pending()
        self.injector.run_session_command()
        self.assertFalse(self.injector._submit(7, "continuation"))
        self.assertEqual([], self.client.calls)
        self.assertEqual([], self.client.compacted)
        self.assertEqual([], self.status.records)
        self.assertEqual({}, self.injector._delivery.attempts)
        self.assertEqual(0, self.cursor)
        commands.read.assert_not_called()
        commands.clear.assert_not_called()
        self.assertTrue(self.client.notifications.empty())
        ready[0] = True
        self.injector.poll_once()
        self.assertEqual(["thread-a"], self.client.compacted)
        self.assertEqual("start", self.client.calls[0][0])
        self.assertEqual(7, self.cursor)
        commands.clear.assert_called_once_with("compact-1")

    def test_context_pressure_stops_steer_without_cursor_or_status_transition(self):
        self.messages = [_message(7)]
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-live")
        control = Mock()
        control.external_admission_paused.return_value = True
        self.injector._ports = dataclasses.replace(self.injector._ports, compaction=control)
        self.injector.poll_once()
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)
        self.assertEqual([], self.status.records)
        control.external_admission_paused.return_value = False
        self.injector.poll_once()
        self.assertEqual([("steer", "thread-a", "turn-live")], self.client.calls)
        self.assertEqual(7, self.cursor)

    def test_compact_rpc_acceptance_reports_only_submission_to_shared_control(self):
        request = {"id": "automatic-compact", "action": "compact", "automatic": True}
        control = Mock()
        control.command_ready.return_value = True
        control.external_admission_paused.return_value = True
        cleared = []
        self.messages = [_message(7)]
        self.injector._ports = dataclasses.replace(
            self.injector._ports, compaction=control,
            session_commands=CodexSessionCommandPorts(lambda: request, cleared.append),
        )
        self.injector.poll_once()
        self.assertEqual(["thread-a"], self.client.compacted)
        control.command_submitted.assert_called_once_with(request)
        self.assertEqual(["automatic-compact"], cleared)
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)
        self.assertEqual([], self.status.records)
        self.assertTrue(any("session_command_submitted" in line for line in self.logs))
        self.assertFalse(any("session_command_done" in line for line in self.logs))

    def test_idle_thread_gets_a_new_turn_and_the_cursor_moves(self):
        self.messages = [_message(5, "first")]
        self.injector.poll_once()
        self.assertEqual("start", self.client.calls[0][0])
        self.assertIn("first", self.client.calls[0][2])
        self.assertEqual(5, self.cursor)
        self.assertEqual([(5, "submitted", "")], self.status.records)

    def test_running_turn_is_steered_instead_of_queued(self):
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-live")
        self.messages = [_message(7)]
        self.injector.poll_once()
        self.assertEqual([("steer", "thread-a", "turn-live")], self.client.calls)
        self.assertEqual(7, self.cursor)

    def test_steer_race_falls_back_to_a_new_turn(self):
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-gone")
        self.client.fail_steer = True
        self.messages = [_message(8)]
        self.injector.poll_once()
        self.assertEqual("start", self.client.calls[0][0])
        self.assertEqual(8, self.cursor)

    def test_turn_completion_marks_the_message_replied_or_failed(self):
        self.messages = [_message(1), _message(2, "second", thread_id="t2")]
        self.injector.poll_once()
        self.client.notifications.put({"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {"id": "turn-1", "status": "completed"}}})
        self.client.notifications.put(
            {"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {"id": "turn-2", "status": "failed", "error": {"message": "upstream 500"}}}}
        )
        self.injector.poll_once()
        self.assertIn((1, "replied", ""), self.status.records)
        # Unclassified errors are not resent; the reason keeps the type and text.
        self.assertIn((2, "failed", "turn_failed upstream 500"), self.status.records)

    def test_router_transport_messages_are_left_for_the_router_while_a_turn_runs(self):
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-live")
        self.messages = [_message(3, transport="router"), _message(4)]
        self.injector.poll_once()
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)

    def test_direct_gateway_router_transport_is_delivered_by_rpc_while_busy(self):
        from unittest import mock
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-live")
        self.messages = [_message(3, "whole body", transport="router")]
        with mock.patch("ciel_runtime_support.launch_data_path.model_http_bypassed", return_value=True):
            self.injector.poll_once()
        self.assertEqual([("steer", "thread-a", "turn-live")], self.client.calls)
        self.assertEqual(3, self.cursor)

    def test_router_transport_messages_wake_an_idle_thread(self):
        # An idle thread makes no model request the router could add them to.
        self.messages = [_message(3, "routed", transport="router")]
        self.injector.poll_once()
        self.assertEqual("start", self.client.calls[0][0])
        self.assertIn("routed", self.client.calls[0][2])
        self.assertEqual(3, self.cursor)

    def test_turns_carry_the_session_permissions(self):
        seen: list[dict[str, Any]] = []
        original = self.client.turn_start

        def turn_start(thread_id, text, **kw):
            seen.append(kw)
            return original(thread_id, text, **kw)

        self.client.turn_start = turn_start  # type: ignore[method-assign]
        self.injector._permissions = FULL_ACCESS
        self.messages = [_message(5)]
        self.injector.poll_once()
        self.assertEqual("never", seen[0]["approval_policy"])
        self.assertEqual({"type": "dangerFullAccess"}, seen[0]["sandbox_policy"])

    def test_initial_thread_is_resumed_with_permissions_before_anything_else(self):
        resumed: list[tuple[str, dict[str, Any]]] = []
        self.client.resume_thread = lambda thread_id, **kw: resumed.append((thread_id, kw)) or {}  # type: ignore[method-assign]
        injector = CodexAppServerChannelInjector(
            lambda: self.client,  # type: ignore[arg-type,return-value]
            self.injector._ports,
            cwd="C:/work",
            version="test",
            start_own_thread=False,
            initial_thread_id="saved-1",
            permissions=FULL_ACCESS,
        )
        injector.open()
        self.assertEqual("saved-1", injector.thread_id)
        self.assertEqual(
            [
                (
                    "saved-1",
                    {
                        "exclude_turns": True,
                        "model": None,
                        "model_provider": None,
                        "approval_policy": "never",
                        "sandbox": "danger-full-access",
                    },
                )
            ],
            resumed,
        )
        self.assertEqual(0, self.client.started_threads)
        injector.subscribe_target()
        self.assertEqual(1, len(resumed))

    def test_resumes_carry_the_server_model_and_provider(self):
        # Edward 2026-10-05: a thread saved under `ciel-runtime` failed to open
        # on a `ciel-runtime-codex` server when resume sent no provider.
        resumed: list[tuple[str, dict[str, Any]]] = []
        self.client.resume_thread = lambda thread_id, **kw: resumed.append((thread_id, kw)) or {}  # type: ignore[method-assign]
        injector = CodexAppServerChannelInjector(
            lambda: self.client,  # type: ignore[arg-type,return-value]
            self.injector._ports,
            cwd="C:/work",
            version="test",
            start_own_thread=False,
            initial_thread_id="saved-1",
            resume_model=ResumeModel("ciel-sol", "ciel-runtime-codex"),
        )
        injector.open()
        injector.thread_id = "other-2"
        injector.subscribe_target()
        self.assertEqual(["saved-1", "other-2"], [thread_id for thread_id, _kw in resumed])
        for _thread_id, kw in resumed:
            self.assertEqual("ciel-sol", kw["model"])
            self.assertEqual("ciel-runtime-codex", kw["model_provider"])

    def test_hidden_messages_are_passed_without_a_turn(self):
        self.messages = [_message(3, visibility="hidden"), _message(4, "real")]
        self.injector.poll_once()
        self.assertEqual(1, len(self.client.calls))
        self.assertIn("real", self.client.calls[0][2])
        self.assertEqual(4, self.cursor)

    def test_submit_failures_hold_the_cursor_then_record_failure(self):
        self.client.fail_start = MAX_SUBMIT_ATTEMPTS
        self.messages = [_message(9)]
        for _ in range(MAX_SUBMIT_ATTEMPTS - 1):
            self.injector.poll_once()
            self.assertEqual(0, self.cursor)
        self.injector.poll_once()
        self.assertEqual(9, self.cursor)
        self.assertEqual([(9, "failed", "submit_not_sent")], self.status.records)

    def test_turns_started_in_another_chat_move_the_target(self):
        self.client.notifications.put({"method": "turn/started", "params": {"threadId": "thread-b", "turn": {"id": "x"}}})
        self.client.state = CodexAppServerState(thread_id="thread-b")
        self.messages = [_message(11)]
        self.injector.poll_once()
        self.assertEqual("thread-b", self.injector.thread_id)
        # The turn that moved the target is still running, so the message steers into it.
        self.assertEqual(("steer", "thread-b", "x"), self.client.calls[0])



class CodexDurableDeliveryTests(unittest.TestCase):
    # Reuse setup, not inherited test cases (loaded explicitly by the runner).
    def setUp(self):
        CodexDesktopInjectionTests.setUp(self)
        import tempfile
        import threading
        from pathlib import Path
        from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.status = RuntimeInputStatusRepository(Path(self.temp.name) / "status.jsonl",
                                                  lambda **kw: None, lambda *args: None, threading.RLock())
        self.injector._ports = dataclasses.replace(self.injector._ports, status=self.status)
        self.messages = [_message(1)]
        self.status.transition(1, "queued")

    _commit = CodexDesktopInjectionTests._commit

    def test_ownership_is_durable_before_the_first_write(self):
        original = self.client.turn_start
        def check(*args, **kw):
            row = self.status.get(1)
            self.assertEqual("submitted", row["status"])
            self.assertEqual(self.injector._child_epoch, row["data"]["child_epoch"])
            self.assertTrue(row["data"]["attempt_id"])
            return original(*args, **kw)
        self.client.turn_start = check
        self.injector.deliver_pending()
        self.assertEqual(1, self.cursor)

    def test_unknown_start_acceptance_never_replays_or_advances_cursor_after_restart(self):
        from ciel_runtime_support.codex_app_server import CodexAppServerUncertain
        calls = []
        def accept_then_lose(*args, **kw):
            calls.append(args)
            raise CodexAppServerUncertain("ack lost")
        self.client.turn_start = accept_then_lose
        self.injector.deliver_pending()
        self.assertEqual("interrupted_uncertain", self.status.get(1)["status"])
        for _ in range(3):
            self.injector.deliver_pending()
        restarted = CodexAppServerChannelInjector(lambda: self.client, self.injector._ports,
                                                  cwd="C:/work", version="test")
        restarted.client, restarted.thread_id = self.client, "thread-a"
        restarted._adopt_attempts()
        restarted.deliver_pending()
        self.assertEqual((1, 0), (len(calls), self.cursor))

    def test_crash_after_wire_before_cursor_is_never_replayed(self):
        calls = []
        def crash(*args, **kw):
            calls.append(args)
            raise SystemExit("injector crash after write")
        self.client.turn_start = crash
        with self.assertRaises(SystemExit):
            self.injector.deliver_pending()
        self.assertEqual("submitted", self.status.get(1)["status"])
        restarted = CodexAppServerChannelInjector(lambda: self.client, self.injector._ports,
                                                  cwd="C:/work", version="test")
        restarted.client, restarted.thread_id = self.client, "thread-a"
        restarted._adopt_attempts()
        restarted.deliver_pending()
        self.assertEqual((1, 0), (len(calls), self.cursor))

    def test_unknown_steer_does_not_fall_back_to_start(self):
        from ciel_runtime_support.codex_app_server import CodexAppServerUncertain
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-x")
        self.client.turn_steer = Mock(side_effect=CodexAppServerUncertain("ack lost"))
        self.injector.deliver_pending()
        self.injector.deliver_pending()
        self.assertEqual([], self.client.calls)
        self.assertEqual(1, self.client.turn_steer.call_count)
        self.assertEqual(("interrupted_uncertain", 0), (self.status.get(1)["status"], self.cursor))

    def test_explicit_non_mismatch_rejection_is_terminal_not_a_start_fallback(self):
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-x")
        self.client.turn_steer = Mock(side_effect=CodexAppServerRejected(
            "turn/steer", {"code": -32603, "message": "internal error"}))
        self.injector.deliver_pending()
        self.injector.deliver_pending()
        self.assertEqual([], self.client.calls)
        self.assertEqual(("failed", 1), (self.status.get(1)["status"], self.cursor))

    def test_persistence_failure_prevents_the_wire(self):
        from unittest.mock import patch
        with patch.object(type(self.status), "begin_attempt", side_effect=OSError("fsync failed")):
            with self.assertRaises(OSError):
                self.injector.deliver_pending()
        self.assertEqual(([], 0), (self.client.calls, self.cursor))

    def test_unknown_retry_acceptance_keeps_claim_and_never_replays(self):
        from ciel_runtime_support.codex_app_server import CodexAppServerUncertain
        from ciel_runtime_support.runtime_input_retry import RetrySettings
        clock = [1000.0]
        self.injector._wall_clock = lambda: clock[0]
        self.injector._ports = dataclasses.replace(self.injector._ports,
                                                   retry_settings=lambda: RetrySettings(interval_seconds=1))
        self.injector.deliver_pending()
        self.injector._complete_turn({"threadId": "thread-a", "turn": {
            "id": "turn-1", "status": "failed", "error": {"codexErrorInfo": "serverOverloaded"}}})
        clock[0] += 2
        calls = []
        def lost(*args, **kwargs):
            calls.append(args)
            raise CodexAppServerUncertain("retry accepted; ack lost")
        self.client.turn_start = lost
        self.injector.deliver_pending()
        self.assertEqual("interrupted_uncertain", self.status.get(1)["status"])
        self.assertTrue(self.status.get(1)["data"]["retry"]["claim"])
        clock[0] += 200
        for _ in range(3):
            self.injector.deliver_pending()
        restarted = CodexAppServerChannelInjector(lambda: self.client, self.injector._ports,
                                                  cwd="C:/work", version="test")
        restarted.client, restarted.thread_id = self.client, "thread-a"
        restarted._adopt_attempts()
        restarted._adopt_retries()
        restarted.deliver_pending()
        self.assertEqual(1, len(calls))

    def test_proven_not_sent_releases_attempt_then_retries_once(self):
        self.client.fail_start = 1
        self.injector.deliver_pending()
        self.assertEqual(("queued", 0), (self.status.get(1)["status"], self.cursor))
        self.injector.deliver_pending()
        self.assertEqual(("submitted", 1, 1), (self.status.get(1)["status"], self.cursor, len(self.client.calls)))

    def test_restart_reconciles_acknowledged_completed_turn_without_sending(self):
        self.injector.deliver_pending()
        self.cursor = 0
        self.client.turns_list = {"data": [{"id": "turn-1", "status": "completed"}]}
        restarted = CodexAppServerChannelInjector(lambda: self.client, self.injector._ports,
                                                  cwd="C:/work", version="test")
        restarted.client, restarted.thread_id = self.client, "thread-a"
        restarted._adopt_attempts()
        restarted.deliver_pending()
        self.assertEqual(("replied", 1, 1), (self.status.get(1)["status"], self.cursor, len(self.client.calls)))

    def test_another_injector_cannot_steal_a_durable_initial_attempt(self):
        self.injector.deliver_pending()
        self.cursor = 0
        other_client = _Client()
        other = CodexAppServerChannelInjector(lambda: other_client, self.injector._ports,
                                              cwd="C:/work", version="other")
        other.client, other.thread_id = other_client, "thread-a"
        self.assertFalse(other._submit(1, "same input"))
        other.deliver_pending()
        self.assertEqual(([], 0, 1), (other_client.calls, self.cursor, len(self.client.calls)))

    def test_wrong_thread_and_old_attempt_cannot_complete_the_new_owner(self):
        self.injector.deliver_pending()
        self.injector._complete_turn({"threadId": "foreign", "turn": {"id": "turn-1", "status": "completed"}})
        self.assertEqual("submitted", self.status.get(1)["status"])
        old = self.status.get(1)["data"]["attempt_id"]
        self.assertTrue(self.status.defer_unsubmitted(1, old))
        newer = self.status.begin_attempt([1], "new-owner")
        self.injector._complete_turn({"threadId": "thread-a", "turn": {"id": "turn-1", "status": "completed"}})
        self.assertEqual(("submitted", newer), (self.status.get(1)["status"], self.status.get(1)["data"]["attempt_id"]))


class _Commands:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None
        self.cleared: list[str | None] = []

    def read(self):
        return self.request

    def clear(self, request_id):
        self.cleared.append(request_id)
        self.request = None


class CodexAppServerSessionTests(unittest.TestCase):
    """Thread following, subscription and queued session commands (codex 0.159.3 protocol)."""

    def setUp(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.cursor = 0
        self.status = _Status()
        self.logs: list[str] = []
        self.client = _Client()
        self.client.state = CodexAppServerState()
        self.commands = _Commands()
        self.clock = 100.0
        self.ports = CodexDesktopChannelPorts(
            read_messages=lambda last_id, limit: [m for m in self.messages if m["id"] > last_id][:limit],
            read_cursor=lambda: self.cursor,
            commit_cursor=lambda message_id: setattr(self, "cursor", max(self.cursor, message_id)),
            status=self.status,
            log=lambda level, message: self.logs.append(f"{level} {message}"),
            session_commands=CodexSessionCommandPorts(self.commands.read, self.commands.clear),
        )

    def injector(self, **kwargs: Any) -> CodexAppServerChannelInjector:
        injector = CodexAppServerChannelInjector(
            lambda: self.client,  # type: ignore[arg-type,return-value]
            self.ports,
            cwd="C:/work",
            version="test",
            now=lambda: self.clock,
            **kwargs,
        )
        injector.open()
        return injector

    def _thread_started(self, thread_id: str, **thread: Any) -> None:
        self.client.notifications.put({"method": "thread/started", "params": {"thread": {"id": thread_id, "ephemeral": False, "threadSource": "user", "parentThreadId": None, **thread}}})

    def test_desktop_mode_starts_its_own_thread(self):
        injector = self.injector()
        self.assertEqual("own-1", injector.thread_id)

    def test_tui_mode_waits_for_the_tui_thread_and_ignores_title_threads(self):
        injector = self.injector(start_own_thread=False, session_actions=frozenset({"compact"}))
        self.assertEqual("", injector.thread_id)
        self.messages = [_message(1)]
        injector.poll_once()
        self.assertEqual([], self.client.calls)
        self._thread_started("tui-main")
        self._thread_started("title", ephemeral=True, threadSource="thread_title")
        self._thread_started("child", parentThreadId="tui-main")
        self.client.fail_resume = 1
        injector.poll_once()
        self.assertEqual("tui-main", injector.thread_id)
        self.assertEqual([("start", "tui-main")], [c[:2] for c in self.client.calls])
        self.assertEqual([], self.client.resumed)
        self.clock += SUBSCRIBE_RETRY_SECONDS
        injector.poll_once()
        self.assertEqual(["tui-main"], self.client.resumed)

    def test_tui_new_moves_the_target(self):
        injector = self.injector(start_own_thread=False)
        self._thread_started("first")
        injector.poll_once()
        self._thread_started("after-new")
        injector.poll_once()
        self.assertEqual("after-new", injector.thread_id)

    def test_busy_unsubscribed_thread_holds_messages_until_idle(self):
        injector = self.injector(start_own_thread=False)
        self._thread_started("tui-main")
        self.client.fail_resume = 5
        self.client.notifications.put({"method": "thread/status/changed", "params": {"threadId": "tui-main", "status": {"type": "active", "activeFlags": []}}})
        self.messages = [_message(4)]
        injector.poll_once()
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)
        self.client.notifications.put({"method": "thread/status/changed", "params": {"threadId": "tui-main", "status": {"type": "idle"}}})
        injector.poll_once()
        self.assertEqual([("start", "tui-main")], [c[:2] for c in self.client.calls])
        self.assertEqual(4, self.cursor)

    def test_compact_waits_for_idle_then_uses_thread_compact_start(self):
        injector = self.injector()
        self.commands.request = {"id": "c1", "action": "compact", "command": "/compact"}
        self.client.notifications.put({"method": "turn/started", "params": {"threadId": "own-1", "turn": {"id": "t1"}}})
        injector.poll_once()
        self.assertEqual([], self.client.compacted)
        self.assertIsNotNone(self.commands.request)
        self.client.notifications.put({"method": "turn/completed", "params": {"threadId": "own-1", "turn": {"id": "t1", "status": "completed"}}})
        injector.poll_once()
        self.assertEqual(["own-1"], self.client.compacted)
        self.assertEqual(["c1"], self.commands.cleared)

    def test_goal_clear_uses_thread_goal_clear_even_while_a_turn_runs(self):
        injector = self.injector()
        self.client.turns_list = {"cleared": True}
        self.commands.request = {"id": "g1", "action": "goal_clear", "command": "/goal clear"}
        self.client.notifications.put({"method": "turn/started", "params": {"threadId": "own-1", "turn": {"id": "t1"}}})
        injector.poll_once()
        self.assertIn(("thread/goal/clear", {"threadId": "own-1"}), self.client.requests)
        self.assertEqual(["g1"], self.commands.cleared)
        self.assertTrue(any("action=goal_clear" in line and "cleared=true" in line for line in self.logs))

    def test_request_without_action_is_compact(self):
        injector = self.injector()
        self.commands.request = {"id": "old", "command": "/compact"}
        injector.poll_once()
        self.assertEqual(["own-1"], self.client.compacted)

    def test_new_session_starts_a_thread_and_retargets(self):
        injector = self.injector()
        self.commands.request = {"id": "n1", "action": "new_session"}
        injector.poll_once()
        self.assertEqual("own-2", injector.thread_id)
        self.assertEqual(["n1"], self.commands.cleared)
        self.messages = [_message(2)]
        injector.poll_once()
        self.assertEqual(("start", "own-2"), self.client.calls[-1][:2])

    def test_actions_left_to_another_consumer_stay_queued(self):
        injector = self.injector(start_own_thread=False, session_actions=frozenset({"compact"}))
        self._thread_started("tui-main")
        self.commands.request = {"id": "n1", "action": "new_session"}
        injector.poll_once()
        self.assertEqual(0, self.client.started_threads)
        self.assertEqual([], self.commands.cleared)

    def test_turns_finished_before_subscribing_are_settled_from_the_turn_list(self):
        injector = self.injector(start_own_thread=False)
        self._thread_started("tui-main")
        self.client.fail_resume = 1
        self.messages = [_message(1)]
        injector.poll_once()  # subscribe deferred (no rollout yet), message delivered via turn/start
        self.assertEqual([(1, "submitted", "")], self.status.records)
        self.client.turns_list = {"data": [{"id": "turn-1", "status": "completed"}]}
        injector.poll_once()  # turn/start reset the retry clock, so subscribing runs at once
        self.assertEqual(["tui-main"], self.client.resumed)
        self.assertIn((1, "replied", ""), self.status.records)
        self.assertEqual("thread/turns/list", self.client.requests[0][0])
        self.assertEqual(1, sum("subscribe_deferred" in line for line in self.logs))

    def test_user_thread_classification(self):
        self.assertTrue(is_user_thread({"id": "a", "threadSource": "user", "ephemeral": False}))
        self.assertTrue(is_user_thread({"id": "a"}))
        self.assertFalse(is_user_thread({"id": "a", "threadSource": "thread_title", "ephemeral": True}))
        self.assertFalse(is_user_thread({"id": "a", "parentThreadId": "p"}))
        self.assertFalse(is_user_thread(None))

    def test_desktop_alias_is_the_generic_injector(self):
        self.assertIs(CodexDesktopChannelInjector, CodexAppServerChannelInjector)


class AgentTurnEndReportTests(unittest.TestCase):
    def test_each_completed_turn_is_reported_with_who_started_it(self):
        client = _Client()
        reported: list[dict[str, Any]] = []
        messages = [_message(1, "from walkie")]
        injector = CodexAppServerChannelInjector(
            lambda: client,  # type: ignore[arg-type,return-value]
            CodexDesktopChannelPorts(
                read_messages=lambda last_id, limit: [m for m in messages if m["id"] > last_id][:limit],
                read_cursor=lambda: 0,
                commit_cursor=lambda _id: None,
                status=_Status(),
                log=lambda *_args: None,
                report_turn_end=reported.append,
            ),
            cwd="C:/work",
            version="test",
            runtime_label="codex-desktop",
        )
        injector.client = client  # type: ignore[assignment]
        injector.thread_id = "thread-a"
        injector.poll_once()  # starts turn-1 for the channel message
        client.notifications.put({"method": "item/started", "params": {"threadId": "thread-a", "turnId": "typed-1", "item": {"type": "userMessage", "content": [{"type": "text", "text": "check the logs"}]}}})
        client.notifications.put({"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {"id": "turn-1", "status": "completed"}}})
        client.notifications.put({"method": "turn/completed", "params": {"threadId": "thread-a", "turn": {"id": "typed-1", "status": "interrupted"}}})
        injector.drain_notifications()
        self.assertEqual(
            [("turn-1", "end_turn", False, "codex-desktop"), ("typed-1", "interrupted", True, "codex-desktop")],
            [(e["turn_id"], e["reason"], e["by_user_input"], e["runtime"]) for e in reported],
        )

    def test_turns_settled_after_subscribing_are_reported_and_unlabelled_sessions_stay_quiet(self):
        client = _Client()
        client.turns_list = {"data": [{"id": "turn-1", "status": "completed"}]}
        reported: list[dict[str, Any]] = []
        for label, expected in (("codex-app-server", ["turn-1"]), ("", [])):
            reported.clear()
            injector = CodexAppServerChannelInjector(
                lambda: client,  # type: ignore[arg-type,return-value]
                CodexDesktopChannelPorts(
                    read_messages=lambda last_id, limit: [_message(1)][:limit] if last_id < 1 else [],
                    read_cursor=lambda: 0,
                    commit_cursor=lambda _id: None,
                    status=_Status(),
                    log=lambda *_args: None,
                    report_turn_end=reported.append,
                ),
                cwd="C:/work",
                version="test",
                runtime_label=label,
            )
            injector.client = client  # type: ignore[assignment]
            injector.thread_id = "thread-a"
            client.calls.clear()
            injector.deliver_pending()  # turn-1 starts before the thread is subscribed
            injector.subscribe_target()
            with self.subTest(label=label):
                self.assertEqual(expected, [e["turn_id"] for e in reported])


if __name__ == "__main__":
    unittest.main()
