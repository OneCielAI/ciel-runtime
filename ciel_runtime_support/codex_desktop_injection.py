"""Channel delivery into a Codex session through an app-server Ciel Runtime owns.

The desktop app, a `codex --remote` TUI and bare app-server clients all run
against an app-server Ciel Runtime starts (see codex_desktop_runtime and
codex_app_server_session), so channel messages enter as a second JSON-RPC
client: `turn/start` when the thread is idle, `turn/steer` while a turn is
running. Delivery is confirmed from the protocol's own turn notifications
instead of a transcript scan - the transcript path is what mis-reports
delivered session-socket messages as unseen (journal 2026-09-23, walkie SSE
stall).

Queued session commands (the ``compact_session``/``new_session`` MCP tools)
go in the same way: ``thread/compact/start`` and ``thread/start``.

Measured on codex 0.159.3 (journal 2026-10-01 runtime-control): every client
receives ``thread/started`` for threads any client starts, but turn
notifications only for threads it started or resumed; ``thread/resume`` of a
thread without turns fails ("no rollout found"), and ``turn/start`` into a
thread this client never resumed still works.

Measured on codex 0.160.0 (journal 2026-10-03 research/codex-app-server): a
resumed thread keeps the approval/sandbox it was saved with, and a ``--remote``
TUI may not override them; overrides take effect when the first client to
resume the thread passes them, or per ``turn/start`` ("this turn and
subsequent turns", typed turns included).
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
from ciel_runtime_support.agent_turn_events import AppServerTurnTracker, user_input_text
from ciel_runtime_support.codex_app_server_resume import ResumeModel
from ciel_runtime_support.runtime_compaction_control import RuntimeCompactionControl
from ciel_runtime_support import runtime_input_retry
from ciel_runtime_support.runtime_input_retry import RetrySettings

CLIENT_NAME = "ciel-runtime"
CLIENT_TITLE = "Ciel Runtime channel"
DEFAULT_SCAN_LIMIT = 50
# A message that keeps failing to submit is recorded failed and passed, so it
# cannot hold every later message at the cursor forever.
MAX_SUBMIT_ATTEMPTS = 3
SESSION_ACTIONS = frozenset({"compact", "new_session", "goal_clear"})
# A thread without turns cannot be resumed yet; subscribing is retried.
SUBSCRIBE_RETRY_SECONDS = 3.0
# A resend claimed this long ago without a recorded send may have reached
# the app-server before its owner stopped; it is failed, never sent again.
RETRY_CLAIM_STALE_SECONDS = 120.0
# Scheduled resends older than this are not adopted by a new injector.
RETRY_ADOPT_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class AppServerPermissions:
    """Approval and sandbox for the threads a session drives."""

    approval_policy: str
    sandbox: str  # thread/resume (SandboxMode)
    sandbox_policy: dict[str, Any]  # turn/start (SandboxPolicy)


# What `codex --yolo` means; Ciel's Codex TUI launch adds --yolo.
FULL_ACCESS = AppServerPermissions("never", "danger-full-access", {"type": "dangerFullAccess"})


@dataclass(frozen=True, slots=True)
class CodexSessionCommandPorts:
    """The single-slot compact/new-session request the MCP tools queue."""

    read: Callable[[], dict[str, Any] | None]
    clear: Callable[[str | None], Any]


@dataclass(frozen=True, slots=True)
class CodexDesktopChannelPorts:
    read_messages: Callable[[int, int], list[dict[str, Any]]]
    read_cursor: Callable[[], int]
    commit_cursor: Callable[[int], Any]
    status: Any
    log: Callable[[str, str], Any]
    session_commands: CodexSessionCommandPorts | None = None
    # Receives one agent.turn_ended event per finished turn (posted to the router).
    report_turn_end: Callable[[dict[str, Any]], Any] | None = None
    compaction: RuntimeCompactionControl | None = None
    # Resend policy for transient turn failures (menu: External event inputs).
    retry_settings: Callable[[], RetrySettings] = RetrySettings


@dataclass(slots=True)
class _Delivery:
    attempts: dict[int, int] = field(default_factory=dict)
    by_turn: dict[str, list[int]] = field(default_factory=dict)
    # The original input of each message still awaiting its turn's outcome.
    prompts: dict[int, dict[str, Any]] = field(default_factory=dict)
    tool_turns: set[str] = field(default_factory=set)
    # error notifications (willRetry=false) by turn, for a turn/completed
    # whose own error lacks the typed codexErrorInfo.
    turn_errors: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Scheduled resends by message id; the lowest id goes first.
    retries: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class _Threads:
    active_turn: dict[str, str] = field(default_factory=dict)
    busy: set[str] = field(default_factory=set)
    subscribed: set[str] = field(default_factory=set)
    subscribe_attempt_at: dict[str, float] = field(default_factory=dict)
    deferral_logged: set[str] = field(default_factory=set)


def session_command_action(request: dict[str, Any]) -> str:
    action = str(request.get("action") or "").strip().lower()
    return action if action in SESSION_ACTIONS else "compact"


def is_user_thread(thread: Any) -> bool:
    """A conversation a person works in, not a title or sub-agent thread."""

    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
        return False
    if thread.get("ephemeral") is True or thread.get("parentThreadId"):
        return False
    return thread.get("threadSource") in (None, "user")


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


class CodexAppServerChannelInjector:
    """Second app-server client that delivers channel input and session commands.

    ``start_own_thread`` gives the injector a thread of its own at connect
    time (desktop and bare app-server sessions); a ``--remote`` TUI starts its
    own thread, which the injector picks up from ``thread/started``.
    ``session_actions`` are the queued session commands this client carries
    out; a TUI only changes conversation through its own ``/new``, so that
    mode leaves new_session to the terminal proxy.  ``initial_thread_id`` is a
    saved conversation to resume at connect time (before a TUI attaches to
    it), with ``permissions`` when given.  ``resume_model`` is the server's
    launch model and provider, sent with every ``thread/resume``.
    """

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
        start_own_thread: bool = True,
        session_actions: frozenset[str] = SESSION_ACTIONS,
        now: Callable[[], float] = time.monotonic,
        wait_ready: Callable[[], bool] | None = None,
        initial_thread_id: str = "",
        permissions: AppServerPermissions | None = None,
        resume_model: ResumeModel | None = None,
        runtime_label: str = "codex-app-server",
        delivery_ready: Callable[[], bool] = lambda: True,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._connect = connect
        self._wall_clock = wall_clock
        self._delivery_ready = delivery_ready
        self._turn_ends = AppServerTurnTracker(runtime_label)
        self._ports = ports
        self._cwd = cwd
        self._version = version
        self._poll_interval = poll_interval
        self._scan_limit = scan_limit
        self._sleep = sleep
        self._start_own_thread = start_own_thread
        self._session_actions = frozenset(session_actions)
        self._now = now
        self._wait_ready = wait_ready
        self._initial_thread_id = initial_thread_id
        self._permissions = permissions
        self._resume_model = resume_model or ResumeModel()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._delivery = _Delivery()
        self._threads = _Threads()
        self._deferred_command = ""
        self.client: CodexAppServerClient | None = None
        self.thread_id = ""
        self._bound_thread = ""

    def start(self, *, opened: bool = False) -> None:
        """Run the poll loop; ``opened`` when open() already connected."""

        self._thread = threading.Thread(target=self._run, args=(opened,), name="codex-desktop-channel", daemon=True)
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
        self.client = client
        if self._initial_thread_id:
            self._resume_initial_thread(client)
        elif self._start_own_thread:
            self._start_thread(client)
        self._adopt_retries()
        self._ports.log("INFO", f"codex_desktop_channel_ready thread={self.thread_id or '-'} cwd={self._cwd}")

    def _adopt_retries(self) -> None:
        """Take over resends an earlier Codex injector scheduled but never sent.

        Older than the adoption window, or claimed by a sender that never
        recorded the send, they are failed with that reason - never left
        queued and never sent twice.
        """

        list_latest = getattr(self._ports.status, "list_latest", None)
        if not callable(list_latest):
            return
        try:
            rows = list_latest(status="queued", limit=500)
        except Exception as exc:  # the status history may be busy; adoption is best effort
            self._ports.log("WARN", f"codex_desktop_channel_retry_adopt_deferred error={type(exc).__name__}")
            return
        now = self._wall_clock()
        for row in rows:
            retry = runtime_input_retry.pending_retry(row)
            if retry is None or retry.get("runtime") != "codex":
                continue
            message_id = int(row["request_id"])
            if retry.get("claim"):
                if now - float(retry.get("claimed_at_epoch") or 0) >= RETRY_CLAIM_STALE_SECONDS:
                    self._fail_retry(message_id, "retry_send_uncertain")
                continue
            if now - float(row.get("updated_at_epoch") or 0) >= RETRY_ADOPT_SECONDS:
                self._fail_retry(message_id, "retry_abandoned:" + str(retry.get("last_failure") or "-"))
                continue
            messages = self._ports.read_messages(message_id - 1, 1)
            message = messages[0] if messages and int(messages[0].get("id") or 0) == message_id else None
            if message is None:
                self._fail_retry(message_id, "retry_message_unavailable")
                continue
            self._delivery.prompts[message_id] = message
            self._delivery.retries[message_id] = dict(retry)
            self._ports.log("INFO", f"codex_desktop_channel_retry_adopted message_id={message_id} "
                            f"attempt={retry.get('attempt')}/{retry.get('limit')}")

    def _fail_retry(self, message_id: int, reason: str) -> None:
        """Terminal failure of a queued resend; delivery moves to the next input."""

        self._delivery.retries.pop(message_id, None)
        self._delivery.prompts.pop(message_id, None)
        self._ports.status.transition(message_id, "failed", reason=reason)
        self._ports.log("WARN", f"codex_desktop_channel_retry_failed message_id={message_id} reason={reason}")

    def _bind_target(self) -> None:
        """Give compaction admission the exact thread this client delivers to."""

        bind = getattr(self._ports.compaction, "bind_native_session", None)
        # Retried each poll until the launch that owns this client has bound.
        if callable(bind) and self.thread_id and self.thread_id != self._bound_thread and bind("codex", self.thread_id):
            self._bound_thread = self.thread_id

    def _resume_initial_thread(self, client: CodexAppServerClient) -> None:
        permissions = self._permissions
        client.resume_thread(
            self._initial_thread_id,
            exclude_turns=True,
            model=self._resume_model.model,
            model_provider=self._resume_model.model_provider,
            approval_policy=permissions.approval_policy if permissions else None,
            sandbox=permissions.sandbox if permissions else None,
        )
        self.thread_id = self._initial_thread_id
        self._threads.subscribed.add(self.thread_id)
        self._bind_target()

    def _start_thread(self, client: CodexAppServerClient) -> str:
        result = client.start_thread(cwd=self._cwd)
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = thread.get("id") if isinstance(thread.get("id"), str) else ""
        self.thread_id = thread_id or client.state.thread_id or ""
        if self.thread_id:
            self._threads.subscribed.add(self.thread_id)
        self._bind_target()
        return self.thread_id

    def _run(self, opened: bool = False) -> None:
        if not opened:
            if self._wait_ready is not None and not self._wait_ready():
                self._ports.log("ERROR", "codex_desktop_channel_connect_failed error=app-server not ready")
                return
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
        self._bind_target()
        self.subscribe_target()
        self.run_session_command()
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
            thread_id = params.get("threadId") if isinstance(params.get("threadId"), str) else ""
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            if method == "thread/started":
                thread = params.get("thread")
                if is_user_thread(thread) and thread["id"] != self.thread_id:
                    # Every client hears about new threads: a TUI /new or a
                    # chat opened in the desktop app moves the target there.
                    self.thread_id = thread["id"]
                    self._ports.log("INFO", f"codex_app_server_channel_target thread={self.thread_id} via=thread/started")
            elif method == "thread/status/changed":
                status = params.get("status") if isinstance(params.get("status"), dict) else {}
                if thread_id and status.get("type") == "active":
                    self._threads.busy.add(thread_id)
                elif thread_id:
                    self._threads.busy.discard(thread_id)
                    self._threads.active_turn.pop(thread_id, None)
            elif method == "turn/started":
                # Follow the thread the person is actually working in: a turn
                # the desktop UI starts in another chat moves the target there.
                if thread_id:
                    self.thread_id = thread_id
                    if isinstance(turn.get("id"), str):
                        self._threads.active_turn[thread_id] = turn["id"]
            elif method == "turn/completed":
                if thread_id:
                    self._threads.active_turn.pop(thread_id, None)
                self._report_turn_end(turn)
                self._complete_turn(params)
            elif method == "error":
                self._note_error(params)
            elif method in ("item/started", "item/completed"):
                self._turn_ends.note_input(*user_input_text(params))
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                turn_id = params.get("turnId")
                if isinstance(turn_id, str) and item.get("type") in runtime_input_retry.CODEX_TOOL_ITEM_TYPES:
                    self._delivery.tool_turns.add(turn_id)

    def _note_error(self, params: dict[str, Any]) -> None:
        """ErrorNotification: willRetry=true is Codex retrying on its own and
        never ends the turn; a final error is kept for the turn's completion."""

        turn_id = params.get("turnId") if isinstance(params.get("turnId"), str) else ""
        error = params.get("error") if isinstance(params.get("error"), dict) else {}
        if turn_id not in self._delivery.by_turn:
            return
        failure = runtime_input_retry.classify_codex_turn({"error": error})
        if params.get("willRetry") is True:
            self._ports.log("INFO", f"codex_desktop_channel_turn_error_retrying turn={turn_id} error={failure.label}")
            return
        self._delivery.turn_errors[turn_id] = error

    def subscribe_target(self) -> None:
        """Resume the target thread so its turn notifications reach this client."""

        client, thread_id = self.client, self.thread_id
        if client is None or not thread_id or thread_id in self._threads.subscribed:
            return
        now = self._now()
        last = self._threads.subscribe_attempt_at.get(thread_id)
        if last is not None and now - last < SUBSCRIBE_RETRY_SECONDS:
            return
        self._threads.subscribe_attempt_at[thread_id] = now
        try:
            client.resume_thread(
                thread_id,
                exclude_turns=True,
                model=self._resume_model.model,
                model_provider=self._resume_model.model_provider,
            )
        except CodexAppServerError as exc:
            # A thread without turns has no rollout yet; turn/start still works.
            if thread_id not in self._threads.deferral_logged:
                self._threads.deferral_logged.add(thread_id)
                self._ports.log("INFO", f"codex_app_server_channel_subscribe_deferred thread={thread_id} error={str(exc)[:200]}")
            return
        self._threads.subscribed.add(thread_id)
        self._ports.log("INFO", f"codex_app_server_channel_subscribed thread={thread_id}")
        self._reconcile_turns(client, thread_id)

    def _reconcile_turns(self, client: CodexAppServerClient, thread_id: str) -> None:
        """Settle turns that started (and may have ended) before this client subscribed."""

        if not self._delivery.by_turn:
            return
        try:
            result = client.request("thread/turns/list", {"threadId": thread_id, "limit": 20})
        except CodexAppServerError as exc:
            self._ports.log("WARN", f"codex_app_server_channel_turns_list_failed thread={thread_id} error={str(exc)[:200]}")
            return
        turns = result.get("data") if isinstance(result.get("data"), list) else []
        for turn in turns:
            if not isinstance(turn, dict) or turn.get("id") not in self._delivery.by_turn:
                continue
            if turn.get("status") in (None, "inProgress"):
                continue
            self._report_turn_end(turn)
            self._complete_turn({"threadId": thread_id, "turn": turn})

    def _active_turn(self, thread_id: str) -> str | None:
        turn_id = self._threads.active_turn.get(thread_id)
        if turn_id:
            return turn_id
        client = self.client
        state = client.state if client is not None else None
        if state is not None and state.thread_id == thread_id:
            return state.active_turn_id
        return None

    def turn_running(self) -> bool:
        """Whether a turn is in progress on the followed thread (a goal between turns is not)."""

        return bool(self.thread_id and self._active_turn(self.thread_id))

    def _busy(self, thread_id: str) -> bool:
        return thread_id in self._threads.busy or bool(self._active_turn(thread_id))

    def run_session_command(self) -> None:
        if not self._delivery_ready():
            return
        commands = self._ports.session_commands
        client = self.client
        if commands is None or client is None:
            return
        request = commands.read()
        if not request:
            return
        action = session_command_action(request)
        if action not in self._session_actions:
            return
        request_id = str(request.get("id") or "")
        if action == "compact":
            self._compact(client, commands, request)
        elif action == "goal_clear":
            self._goal_clear(client, commands, request_id)
        else:
            self._new_session(client, commands, request_id)

    def _compact(self, client: CodexAppServerClient, commands: CodexSessionCommandPorts, request: dict[str, Any]) -> None:
        request_id = str(request.get("id") or "")
        thread_id = self.thread_id
        if not thread_id:
            return
        if self._busy(thread_id):
            if self._deferred_command != request_id:
                self._deferred_command = request_id
                self._ports.log("INFO", f"codex_app_server_session_command_deferred id={request_id or '-'} action=compact reason=active_turn")
            return
        control = self._ports.compaction
        if control and not control.command_ready(request):
            return
        try:
            client.compact_thread(thread_id)
        except CodexAppServerError as exc:
            # A failed RPC can still have reached the native process. Never
            # reopen admission or replay automatic compact based on its ack.
            if control:
                control.command_failed(request, "compact_rpc_unconfirmed", uncertain=True)
            commands.clear(request_id or None)
            self._ports.log(
                "WARN",
                f"codex_app_server_session_command_failed id={request_id or '-'} action=compact "
                f"thread={thread_id} error={str(exc)[:300]}",
            )
            return
        if control:
            control.command_submitted(request)
        commands.clear(request_id or None)
        self._ports.log(
            "INFO",
            f"codex_app_server_session_command_submitted id={request_id or '-'} action=compact "
            f"thread={thread_id} via=thread/compact/start",
        )

    def _goal_clear(self, client: CodexAppServerClient, commands: CodexSessionCommandPorts, request_id: str) -> None:
        # Not deferred while a turn runs: an active goal keeps the thread busy.
        thread_id = self.thread_id
        if not thread_id:
            return
        try:
            result = client.request("thread/goal/clear", {"threadId": thread_id})
        except CodexAppServerError as exc:
            commands.clear(request_id or None)
            self._ports.log(
                "WARN",
                f"codex_app_server_session_command_failed id={request_id or '-'} action=goal_clear "
                f"thread={thread_id} error={str(exc)[:300]}",
            )
            return
        commands.clear(request_id or None)
        cleared = bool(result.get("cleared")) if isinstance(result, dict) else False
        self._ports.log(
            "INFO",
            f"codex_app_server_session_command_done id={request_id or '-'} action=goal_clear "
            f"thread={thread_id} cleared={str(cleared).lower()} via=thread/goal/clear",
        )

    def _new_session(self, client: CodexAppServerClient, commands: CodexSessionCommandPorts, request_id: str) -> None:
        previous = self.thread_id
        try:
            thread_id = self._start_thread(client)
        except CodexAppServerError as exc:
            commands.clear(request_id or None)
            self._ports.log(
                "WARN",
                f"codex_app_server_session_command_failed id={request_id or '-'} action=new_session error={str(exc)[:300]}",
            )
            return
        commands.clear(request_id or None)
        self._ports.log(
            "INFO",
            f"codex_app_server_session_command_done id={request_id or '-'} action=new_session "
            f"thread={thread_id or '-'} previous={previous or '-'} via=thread/start",
        )

    def _report_turn_end(self, turn: dict[str, Any]) -> None:
        report = self._ports.report_turn_end
        if report is None or not self._turn_ends.runtime:
            # No label: the session's transcript watcher reports its turns.
            return
        event = self._turn_ends.completed(turn, started_by_ciel=str(turn.get("id") or "") in self._delivery.by_turn)
        if event is None:
            return
        try:
            report(event)
        except Exception as exc:  # the router may be restarting; never stop delivery
            self._ports.log("WARN", f"agent_turn_event_report_failed turn={event['turn_id']} error={exc}")

    def _complete_turn(self, params: dict[str, Any]) -> None:
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = turn.get("id") if isinstance(turn.get("id"), str) else ""
        message_ids = self._delivery.by_turn.pop(turn_id, [])
        status = str(turn.get("status") or "")
        notified = self._delivery.turn_errors.pop(turn_id, None)
        tool_activity = turn_id in self._delivery.tool_turns
        self._delivery.tool_turns.discard(turn_id)
        for message_id in message_ids:
            if status == "completed":
                self._delivery.prompts.pop(message_id, None)
                self._ports.status.transition(message_id, "replied")
            else:
                self._turn_failed(message_id, runtime_input_retry.classify_codex_turn(turn, notified),
                                  detail=_turn_error(turn), tool_activity=tool_activity)
            self._ports.log(
                "INFO",
                f"codex_desktop_channel_turn_completed message_id={message_id} turn={turn_id} status={status or '-'}",
            )

    def _turn_failed(self, message_id: int, failure: runtime_input_retry.TurnFailure, *,
                     detail: str, tool_activity: bool) -> None:
        """Resend a transient failure in order, or fail it and move on."""

        settings = self._ports.retry_settings()
        get = getattr(self._ports.status, "get", None)
        row = get(message_id) if callable(get) else None
        data = row.get("data") if isinstance(row, dict) and isinstance(row.get("data"), dict) else {}
        previous = data.get("retry") if isinstance(data.get("retry"), dict) else None
        retry = runtime_input_retry.next_retry(
            previous, failure, settings, self._wall_clock(), runtime="codex", tool_activity=tool_activity
        ) if message_id in self._delivery.prompts else None
        if retry is not None:
            schedule = getattr(self._ports.status, "schedule_retry", None)
            record = schedule(message_id, retry, reason=f"retry_scheduled:{failure.label}") if callable(schedule) else {}
            if record.get("status") == "queued":
                self._delivery.retries[message_id] = retry
                self._ports.log(
                    "WARN",
                    f"codex_desktop_channel_retry_scheduled message_id={message_id} attempt={retry['attempt']}/"
                    f"{retry['limit']} error={failure.label} tool_activity={str(tool_activity).lower()} "
                    f"due_in={settings.interval_seconds:g}s",
                )
                return
        self._delivery.prompts.pop(message_id, None)
        reason = runtime_input_retry.terminal_reason(previous, failure, settings)
        if reason.startswith("turn_failed") and detail not in {"turn_failed", failure.label}:
            reason = f"{reason} {detail}"[:300]
        self._ports.status.transition(message_id, "failed", reason=reason,
                                      data={"tool_activity": tool_activity} if tool_activity else None)

    def deliver_pending(self) -> None:
        if not self._delivery_ready():
            return
        if self._ports.compaction and self._ports.compaction.external_admission_paused():
            # A paused gate defers a due resend without spending its attempt.
            return
        if self._delivery.retries and not self._deliver_retry():
            # A scheduled resend stays ahead of every later input.
            return
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
            from .launch_data_path import model_http_bypassed
            if (not model_http_bypassed() and message_input_transport(message) == "router"
                    and (not self.thread_id or self._busy(self.thread_id))):
                # A running turn takes these in its next model request (the
                # router appends them); leave the cursor here so neither path
                # skips them.  An idle thread makes no request, so they go in
                # as a turn below.
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
            if not self._submit(message_id, delivery_prompt(message), message):
                return
            self._ports.commit_cursor(message_id)
            cursor = message_id

    def _deliver_retry(self) -> bool:
        """Send the oldest due resend; True once no resend is waiting.

        A resend is a fresh ``turn/start`` on an idle thread - never a steer
        into a turn someone else is running.
        """

        message_id = min(self._delivery.retries)
        retry = self._delivery.retries[message_id]
        if self._wall_clock() < float(retry.get("due_epoch") or 0):
            return False
        client = self.client
        if client is None or not self.thread_id or self._busy(self.thread_id) or not self._delivery_ready():
            return False
        claim = self._ports.status.claim_retry(message_id, int(retry["attempt"]), now=self._wall_clock())
        if not claim:
            # Another owner moved the row: stop tracking, never send it here.
            self._delivery.retries.pop(message_id, None)
            self._ports.log("INFO", f"codex_desktop_channel_retry_released message_id={message_id} reason=not_owner")
            return not self._delivery.retries
        original = self._delivery.prompts.get(message_id)
        if original is None:
            self._fail_retry(message_id, "retry_message_unavailable")
            return not self._delivery.retries
        prompt = delivery_prompt(runtime_input_retry.resend_message(original, retry))
        try:
            turn_id = _turn_id(self._turn_start(client, prompt))
        except CodexAppServerError as exc:
            failures = int(retry.get("submit_failures") or 0) + 1
            if failures >= MAX_SUBMIT_ATTEMPTS:
                self._fail_retry(message_id, "retry_submit_failed")
                return not self._delivery.retries
            due = self._wall_clock() + float(retry.get("interval_seconds") or 0)
            retry.update(submit_failures=failures, due_epoch=due)
            self._ports.status.release_retry_claim(message_id, claim, due)
            self._ports.log("WARN", f"codex_desktop_channel_retry_submit_failed message_id={message_id} "
                            f"failure={failures} error={str(exc)[:200]}")
            return False
        del self._delivery.retries[message_id]
        self._ports.status.transition(message_id, "submitted", data={"retry": {**retry, "claim": ""}})
        if turn_id:
            self._delivery.by_turn.setdefault(turn_id, []).append(message_id)
        self._ports.log(
            "WARN",
            f"codex_desktop_channel_retry_sent message_id={message_id} attempt={retry['attempt']}/{retry['limit']} "
            f"thread={self.thread_id} turn={turn_id or '-'} previous_error={retry.get('last_failure')}",
        )
        return not self._delivery.retries

    def _turn_start(self, client: CodexAppServerClient, prompt: str) -> dict[str, Any]:
        permissions = self._permissions
        return client.turn_start(
            self.thread_id,
            prompt,
            cwd=self._cwd,
            approval_policy=permissions.approval_policy if permissions else None,
            sandbox_policy=permissions.sandbox_policy if permissions else None,
        )

    def _submit(self, message_id: int, prompt: str, message: dict[str, Any] | None = None) -> bool:
        if not self._delivery_ready():
            return False
        if self._ports.compaction and self._ports.compaction.external_admission_paused():
            return False
        client = self.client
        if client is None or not self.thread_id:
            return False
        active_turn = self._active_turn(self.thread_id)
        if not active_turn and self.thread_id in self._threads.busy:
            # A turn runs in a thread this client is not subscribed to, so the
            # id turn/steer needs is unknown; deliver once the thread is idle.
            return False
        try:
            if active_turn:
                try:
                    client.turn_steer(self.thread_id, active_turn, prompt)
                    turn_id, method = active_turn, "turn/steer"
                except CodexAppServerError:
                    # The turn finished between the state read and the steer.
                    result = self._turn_start(client, prompt)
                    turn_id, method = _turn_id(result), "turn/start"
            else:
                result = self._turn_start(client, prompt)
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
        if self.thread_id not in self._threads.subscribed:
            # The turn just created the rollout, so subscribing can work now.
            self._threads.subscribe_attempt_at.pop(self.thread_id, None)
        self._ports.status.transition(message_id, "submitted")
        if turn_id:
            self._delivery.by_turn.setdefault(turn_id, []).append(message_id)
            if message is not None:
                self._delivery.prompts[message_id] = message
        self._ports.log(
            "INFO",
            f"codex_desktop_channel_injected message_id={message_id} thread={self.thread_id} turn={turn_id or '-'} via={method}",
        )
        return True


CodexDesktopChannelInjector = CodexAppServerChannelInjector


__all__ = [
    "AppServerPermissions",
    "CodexAppServerChannelInjector",
    "CodexDesktopChannelInjector",
    "CodexDesktopChannelPorts",
    "CodexSessionCommandPorts",
    "FULL_ACCESS",
    "SESSION_ACTIONS",
    "delivery_prompt",
    "is_user_thread",
    "session_command_action",
]
