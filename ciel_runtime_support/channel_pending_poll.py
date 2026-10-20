"""Shared polling state machine for pending channel message injection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ciel_runtime_support.channel_message_repository import ChannelFileLockBusy
from ciel_runtime_support.runtime_input_status import WakeAttemptConflict


@dataclass(slots=True)
class ChannelPendingInflightState:
    message_id: int | None = None
    cursor: int | None = None
    logged_at: float = 0.0
    started_at: float = 0.0
    attempts: int = 0
    # Earlier injections still awaiting confirmation.  A socket input can go
    # in during a turn and another one right after it; tracking only the
    # newest left the earlier ones at "submitted" forever.
    backlog: list[int] = field(default_factory=list)
    recovery_paused_at: float | None = None

    def hold_for_recovery(self, now: float, paused: bool) -> bool:
        if paused:
            if self.recovery_paused_at is None:
                self.recovery_paused_at = now
            return True
        if self.recovery_paused_at is not None:
            if self.started_at > 0:
                self.started_at += max(0.0, now - self.recovery_paused_at)
            self.recovery_paused_at = None
        return False


@dataclass(slots=True)
class ChannelPendingPollState:
    last_id: int
    last_marker: tuple[float, int] = (0.0, -1)
    last_poll_at: float = 0.0
    last_scan_at: float = 0.0
    pending_recheck: bool = False
    defer_logged_at: float = 0.0
    inflight: ChannelPendingInflightState = field(
        default_factory=ChannelPendingInflightState
    )

    @property
    def inflight_message_id(self) -> int | None:
        return self.inflight.message_id

    @inflight_message_id.setter
    def inflight_message_id(self, value: int | None) -> None:
        self.inflight.message_id = value

    @property
    def inflight_cursor(self) -> int | None:
        return self.inflight.cursor

    @inflight_cursor.setter
    def inflight_cursor(self, value: int | None) -> None:
        self.inflight.cursor = value

    @property
    def inflight_logged_at(self) -> float:
        return self.inflight.logged_at

    @inflight_logged_at.setter
    def inflight_logged_at(self, value: float) -> None:
        self.inflight.logged_at = value

    @property
    def inflight_started_at(self) -> float:
        return self.inflight.started_at

    @inflight_started_at.setter
    def inflight_started_at(self, value: float) -> None:
        self.inflight.started_at = value


@dataclass(frozen=True, slots=True)
class ChannelPendingInjectionOptions:
    enabled: bool
    web_chat_only: bool
    wake_for_llm_delivery: bool
    display_llm_delivery_body: bool
    submit_retry_count: int
    confirm_submit: bool
    bracketed_paste: bool
    submit_delay_seconds: float | None
    terminal_available: bool = True


@dataclass(frozen=True, slots=True)
class ChannelPendingPollPolicy:
    log_namespace: str
    active_reason: str
    poll_interval_seconds: float = 0.5
    defer_log_interval_seconds: float = 30.0
    safety_rescan_interval_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class ChannelPendingPollServices:
    file_marker: Callable[[], tuple[float, int]]
    should_check: Callable[[tuple[float, int], tuple[float, int], bool, int | None], bool]
    active: Callable[[], bool]
    ensure_cursor: Callable[[], int]
    inject_pending: Callable[..., int]
    log: Callable[[str, str], Any]
    session_socket_ready: Callable[[], bool] = lambda: False
    external_admission_paused: Callable[[], bool] = lambda: False


def poll_pending_channel_messages(
    now: float,
    writer: Any,
    enter_bytes: bytes,
    state: ChannelPendingPollState,
    options: ChannelPendingInjectionOptions,
    policy: ChannelPendingPollPolicy,
    services: ChannelPendingPollServices,
    *,
    input_ready: bool = True,
) -> ChannelPendingPollState:
    paused = state.inflight.hold_for_recovery(now, services.external_admission_paused())
    if paused:
        state.pending_recheck = True
        return state
    if state.inflight_message_id is None and state.inflight.backlog:
        state.inflight_message_id = state.inflight.backlog.pop(0)
        state.inflight_cursor = None
        state.inflight_started_at = now
        state.inflight_logged_at = now
    if not input_ready or now - state.last_poll_at < policy.poll_interval_seconds:
        return state
    state.last_poll_at = now
    marker = services.file_marker()
    marker_requires_scan = services.should_check(
        marker, state.last_marker, state.pending_recheck, state.inflight_message_id
    )
    safety_rescan_due = (
        state.last_scan_at <= 0.0
        or now - state.last_scan_at >= policy.safety_rescan_interval_seconds
    )
    if not options.enabled or not (marker_requires_scan or safety_rescan_due):
        return state

    busy = services.active()
    if busy:
        state.pending_recheck = True
        # Only terminal typing must wait for the turn boundary.  A
        # session-socket input goes into the CLI's own queue, which holds it
        # until the turn ends (measured with Claude Code 2.1.278: enqueue
        # during the turn, dequeue and delivery right after it).  This gate
        # predates the socket transport and used to hold every input until
        # the turn ended (Walkie 2026-09-23).  inject_pending still defers
        # TTY inputs itself, so scan only when new input or the safety
        # rescan calls for it rather than on every poll.
        socket_scan = services.session_socket_ready() and (
            marker != state.last_marker or safety_rescan_due
        )
        if not socket_scan:
            if now - state.defer_logged_at >= policy.defer_log_interval_seconds:
                state.defer_logged_at = now
                services.log(
                    "INFO",
                    f"{policy.log_namespace}_deferred cursor={state.last_id} reason={policy.active_reason}",
                )
            return state

    injected_ids: list[int] = []
    injected_commit_cursors: list[int] = []
    try:
        cursor = max(state.last_id, services.ensure_cursor())
        last_id = services.inject_pending(
            writer,
            cursor,
            enter_bytes,
            web_chat_only=options.web_chat_only,
            wake_for_llm_delivery=options.wake_for_llm_delivery,
            display_llm_delivery_body=options.display_llm_delivery_body,
            # A runtime without transcript confirmation (for example Muse Code)
            # uses a single fire-and-forget TTY write. Persist its cursor in that
            # same operation; otherwise the generic inflight verifier sees every
            # successful delivery as "missing" and replays it.
            commit_cursor=not options.confirm_submit,
            injected_message_ids=injected_ids,
            injected_commit_cursors=injected_commit_cursors,
            submit_retry_count=options.submit_retry_count,
            confirm_submit=options.confirm_submit,
            bracketed_paste=options.bracketed_paste,
            submit_delay_seconds=options.submit_delay_seconds,
            skip_blocking_wake_states=state.inflight_message_id is not None,
            terminal_available=options.terminal_available,
        )
    except WakeAttemptConflict as exc:
        # The injector releases only this scan's claims before exposing this
        # type. No send or ownership write occurred; rebuild on a bounded poll.
        state.pending_recheck = True
        if now - state.defer_logged_at >= policy.defer_log_interval_seconds:
            state.defer_logged_at = now
            services.log("WARN", f"{policy.log_namespace}_deferred stage=pre_submit "
                         f"message_id={exc.request_id} status={exc.status} reason=wake_ownership_conflict")
        return state
    except ChannelFileLockBusy as exc:
        # The injector never exposes this type after a submission attempt.
        # Keep the unconsumed marker/cursor and recheck on the next bounded poll.
        state.pending_recheck = True
        services.log(
            "WARN",
            f"{policy.log_namespace}_deferred stage=pre_submit errno={exc.errno} "
            "reason=channel_file_lock_busy",
        )
        return state
    state.last_marker = marker
    # A scan during a turn may have left TTY inputs behind; look again once
    # the turn ends.
    state.pending_recheck = busy
    state.last_scan_at = now
    state.last_id = last_id
    if injected_ids and options.confirm_submit:
        if state.inflight_message_id is not None:
            state.inflight.backlog.append(state.inflight_message_id)
        state.inflight_message_id = injected_ids[-1]
        state.inflight.attempts += 1
        # Router delivery may return its pre-batch cursor, while a mixed scan
        # may have deferred an earlier input. Use the injector's safe commit
        # ceiling rather than committing across that unresolved deferral.
        state.inflight_cursor = min(injected_commit_cursors) if injected_commit_cursors else max(injected_ids)
        state.inflight_logged_at = now
        state.inflight_started_at = now
    return state
