"""Channel delivery into a Codex desktop session through its app-server.

The desktop app runs against an app-server Ciel Runtime owns (see
codex_desktop_runtime), so channel messages enter as a second JSON-RPC client:
`turn/start` when the thread is idle, `turn/steer` while a turn is running.
Delivery is confirmed from the protocol's own turn notifications instead of a
transcript scan - the transcript path is what mis-reports delivered
session-socket messages as unseen (journal 2026-09-23, walkie SSE stall).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable

from ciel_runtime_support.channel_message_policy import (
    message_input_transport,
    message_is_web_chat_request,
    superseded_message_ids,
)
from ciel_runtime_support.channel_message_prompt import (
    format_wake_batch_prompt,
    format_web_chat_wake_batch_prompt,
    llm_message_skip_reason,
)
from ciel_runtime_support.codex_app_server import CodexAppServerClient, CodexAppServerError

CLIENT_NAME = "ciel-runtime"
CLIENT_TITLE = "Ciel Runtime channel"
DEFAULT_SCAN_LIMIT = 50
# A message that keeps failing to submit is recorded failed and passed, so it
# cannot hold every later message at the cursor forever.
MAX_SUBMIT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class CodexDesktopChannelPorts:
    read_messages: Callable[[int, int], list[dict[str, Any]]]
    read_cursor: Callable[[], int]
    commit_cursor: Callable[[int], Any]
    status: Any
    log: Callable[[str, str], Any]


@dataclass(slots=True)
class _Delivery:
    attempts: dict[int, int] = field(default_factory=dict)
    by_turn: dict[str, list[int]] = field(default_factory=dict)


def delivery_prompt(message: dict[str, Any]) -> str:
    if message_is_web_chat_request(message):
        return format_web_chat_wake_batch_prompt([message])
    return format_wake_batch_prompt([message])


def _turn_id(result: dict[str, Any]) -> str:
    turn = result.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str):
        return turn["id"]
    value = result.get("turnId")
    return value if isinstance(value, str) else ""


def _turn_error(turn: dict[str, Any]) -> str:
    error = turn.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "turn_failed")[:300]
    return "turn_failed"


class CodexDesktopChannelInjector:
    def __init__(
        self,
        connect: Callable[[], CodexAppServerClient],
        ports: CodexDesktopChannelPorts,
        *,
        cwd: str,
        version: str,
        poll_interval: float = 1.0,
        scan_limit: int = DEFAULT_SCAN_LIMIT,
        sleep: Callable[[float], Any] = time.sleep,
    ) -> None:
        self._connect = connect
        self._ports = ports
        self._cwd = cwd
        self._version = version
        self._poll_interval = poll_interval
        self._scan_limit = scan_limit
        self._sleep = sleep
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._delivery = _Delivery()
        self.client: CodexAppServerClient | None = None
        self.thread_id = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="codex-desktop-channel", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        client = self.client
        if client is not None:
            try:
                client.close(timeout=1.0)
            except CodexAppServerError as exc:
                self._ports.log("WARN", f"codex_desktop_channel_close_failed error={exc}")
        if self._thread is not None:
            self._thread.join(timeout)

    def open(self) -> None:
        client = self._connect()
        client.initialize(client_name=CLIENT_NAME, client_title=CLIENT_TITLE, client_version=self._version)
        client.start_thread(cwd=self._cwd)
        self.client = client
        self.thread_id = client.state.thread_id or ""
        self._ports.log("INFO", f"codex_desktop_channel_ready thread={self.thread_id or '-'} cwd={self._cwd}")

    def _run(self) -> None:
        try:
            self.open()
        except (CodexAppServerError, OSError) as exc:
            self._ports.log("ERROR", f"codex_desktop_channel_connect_failed error={exc}")
            return
        while not self._stop.is_set():
            try:
                self.poll_once()
            except (CodexAppServerError, OSError) as exc:
                if self._stop.is_set():
                    return
                self._ports.log("WARN", f"codex_desktop_channel_poll_failed error={exc}")
            self._sleep(self._poll_interval)

    def poll_once(self) -> None:
        self.drain_notifications()
        self.deliver_pending()

    def drain_notifications(self) -> None:
        client = self.client
        if client is None:
            return
        while True:
            message = client.next_notification(timeout=0.0)
            if message is None:
                return
            method = message.get("method")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "turn/started":
                # Follow the thread the person is actually working in: a turn
                # the desktop UI starts in another chat moves the target there.
                thread_id = params.get("threadId")
                if isinstance(thread_id, str) and thread_id:
                    self.thread_id = thread_id
            elif method == "turn/completed":
                self._complete_turn(params)

    def _complete_turn(self, params: dict[str, Any]) -> None:
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = turn.get("id") if isinstance(turn.get("id"), str) else ""
        message_ids = self._delivery.by_turn.pop(turn_id, [])
        status = str(turn.get("status") or "")
        for message_id in message_ids:
            if status == "completed":
                self._ports.status.transition(message_id, "replied")
            else:
                self._ports.status.transition(message_id, "failed", reason=_turn_error(turn))
            self._ports.log(
                "INFO",
                f"codex_desktop_channel_turn_completed message_id={message_id} turn={turn_id} status={status or '-'}",
            )

    def deliver_pending(self) -> None:
        cursor = int(self._ports.read_cursor() or 0)
        candidates = self._ports.read_messages(cursor, self._scan_limit)
        superseded = superseded_message_ids(candidates)
        for message in candidates:
            try:
                message_id = int(message.get("id") or 0)
            except (TypeError, ValueError):
                continue
            if message_id <= cursor:
                continue
            if message_input_transport(message) == "router":
                # The router appends these to the next model request itself;
                # leave the cursor here so neither path skips them.
                self._ports.log("INFO", f"codex_desktop_channel_deferred message_id={message_id} reason=router_transport")
                return
            skip = llm_message_skip_reason(message) or ("superseded_channel_notice" if message_id in superseded else "")
            if skip:
                self._ports.log("INFO", f"codex_desktop_channel_skipped message_id={message_id} reason={skip}")
                if skip == "superseded_channel_notice":
                    self._ports.status.transition(message_id, "skipped", reason=skip)
                self._ports.commit_cursor(message_id)
                cursor = message_id
                continue
            if not self._submit(message_id, delivery_prompt(message)):
                return
            self._ports.commit_cursor(message_id)
            cursor = message_id

    def _submit(self, message_id: int, prompt: str) -> bool:
        client = self.client
        if client is None or not self.thread_id:
            return False
        state = client.state
        active_turn = state.active_turn_id if state.thread_id == self.thread_id else None
        try:
            if active_turn:
                try:
                    client.turn_steer(self.thread_id, active_turn, prompt)
                    turn_id, method = active_turn, "turn/steer"
                except CodexAppServerError:
                    # The turn finished between the state read and the steer.
                    result = client.turn_start(self.thread_id, prompt, cwd=self._cwd)
                    turn_id, method = _turn_id(result), "turn/start"
            else:
                result = client.turn_start(self.thread_id, prompt, cwd=self._cwd)
                turn_id, method = _turn_id(result), "turn/start"
        except CodexAppServerError as exc:
            attempts = self._delivery.attempts.get(message_id, 0) + 1
            self._delivery.attempts[message_id] = attempts
            self._ports.log(
                "WARN",
                f"codex_desktop_channel_submit_failed message_id={message_id} attempt={attempts} error={exc}",
            )
            if attempts < MAX_SUBMIT_ATTEMPTS:
                return False
            self._ports.status.transition(message_id, "failed", reason="submit_failed")
            return True
        self._delivery.attempts.pop(message_id, None)
        self._ports.status.transition(message_id, "submitted")
        if turn_id:
            self._delivery.by_turn.setdefault(turn_id, []).append(message_id)
        self._ports.log(
            "INFO",
            f"codex_desktop_channel_injected message_id={message_id} thread={self.thread_id} turn={turn_id or '-'} via={method}",
        )
        return True


__all__ = [
    "CodexDesktopChannelInjector",
    "CodexDesktopChannelPorts",
    "delivery_prompt",
]
