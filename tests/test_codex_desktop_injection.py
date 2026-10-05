from __future__ import annotations

import queue
import unittest
from typing import Any

from ciel_runtime_support.codex_app_server import CodexAppServerError, CodexAppServerState
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
    def __init__(self) -> None:
        self.records: list[tuple[int, str, str]] = []

    def transition(self, request_id: int, status: str, *, reason: str = "", data: Any = None):
        self.records.append((request_id, status, reason))
        return {}


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
            raise CodexAppServerError("turn/start failed")
        self.calls.append(("start", thread_id, text))
        return {"turn": {"id": f"turn-{len(self.calls)}", "status": "inProgress"}}

    def turn_steer(self, thread_id, expected_turn_id, text, **_kw):
        if self.fail_steer:
            raise CodexAppServerError("no active turn")
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
        self.assertIn((2, "failed", "upstream 500"), self.status.records)

    def test_router_transport_messages_are_left_for_the_router_while_a_turn_runs(self):
        self.client.state = CodexAppServerState(thread_id="thread-a", active_turn_id="turn-live")
        self.messages = [_message(3, transport="router"), _message(4)]
        self.injector.poll_once()
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)

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
        self.assertEqual([(9, "failed", "submit_failed")], self.status.records)

    def test_turns_started_in_another_chat_move_the_target(self):
        self.client.notifications.put({"method": "turn/started", "params": {"threadId": "thread-b", "turn": {"id": "x"}}})
        self.client.state = CodexAppServerState(thread_id="thread-b")
        self.messages = [_message(11)]
        self.injector.poll_once()
        self.assertEqual("thread-b", self.injector.thread_id)
        # The turn that moved the target is still running, so the message steers into it.
        self.assertEqual(("steer", "thread-b", "x"), self.client.calls[0])



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


if __name__ == "__main__":
    unittest.main()
