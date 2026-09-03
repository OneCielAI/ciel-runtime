"""Shared polling state machine for pending channel message injection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ChannelPendingInflightState:
    message_id: int | None = None
    cursor: int | None = None
    logged_at: float = 0.0
    started_at: float = 0.0
    attempts: int = 0


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

    if services.active():
        state.pending_recheck = True
        if now - state.defer_logged_at >= policy.defer_log_interval_seconds:
            state.defer_logged_at = now
            services.log(
                "INFO",
                f"{policy.log_namespace}_deferred cursor={state.last_id} reason={policy.active_reason}",
            )
        return state

    if marker != state.last_marker:
        state.last_marker = marker
    state.pending_recheck = False
    state.last_scan_at = now
    state.last_id = max(state.last_id, services.ensure_cursor())
    injected_ids: list[int] = []
    state.last_id = services.inject_pending(
        writer,
        state.last_id,
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
        submit_retry_count=options.submit_retry_count,
        confirm_submit=options.confirm_submit,
        bracketed_paste=options.bracketed_paste,
        submit_delay_seconds=options.submit_delay_seconds,
        skip_blocking_wake_states=state.inflight_message_id is not None,
    )
    if injected_ids and options.confirm_submit:
        state.inflight_message_id = injected_ids[-1]
        state.inflight.attempts += 1
        # The injector deliberately returns the cursor preceding an LLM-delivery
        # batch until the resulting turn is confirmed.  The durable commit point
        # is nevertheless the highest message in that atomic batch.
        state.inflight_cursor = max(injected_ids)
        state.inflight_logged_at = now
        state.inflight_started_at = now
    return state
