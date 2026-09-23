from __future__ import annotations

import queue
import unittest
from typing import Any

from ciel_runtime_support.codex_app_server import CodexAppServerError, CodexAppServerState
from ciel_runtime_support.codex_desktop_injection import (
    MAX_SUBMIT_ATTEMPTS,
    CodexDesktopChannelInjector,
    CodexDesktopChannelPorts,
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

    def test_router_transport_messages_are_left_for_the_router(self):
        self.messages = [_message(3, transport="router"), _message(4)]
        self.injector.poll_once()
        self.assertEqual([], self.client.calls)
        self.assertEqual(0, self.cursor)

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
        self.assertEqual(("start", "thread-b"), self.client.calls[0][:2])


if __name__ == "__main__":
    unittest.main()
