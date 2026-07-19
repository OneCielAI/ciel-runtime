"""Shared polling state machine for pending channel message injection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ChannelPendingPollState:
    last_id: int
    last_marker: tuple[float, int] = (0.0, -1)
    last_poll_at: float = 0.0
    pending_recheck: bool = False
    defer_logged_at: float = 0.0
    inflight_message_id: int | None = None
    inflight_cursor: int | None = None
    inflight_logged_at: float = 0.0
    inflight_started_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ChannelPendingInjectionOptions:
    enabled: bool
    web_chat_only: bool
    wake_for_llm_delivery: bool
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
    if not options.enabled or not services.should_check(
        marker,
        state.last_marker,
        state.pending_recheck,
        state.inflight_message_id,
    ):
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
    state.last_id = max(state.last_id, services.ensure_cursor())
    injected_ids: list[int] = []
    state.last_id = services.inject_pending(
        writer,
        state.last_id,
        enter_bytes,
        web_chat_only=options.web_chat_only,
        wake_for_llm_delivery=options.wake_for_llm_delivery,
        commit_cursor=False,
        injected_message_ids=injected_ids,
        submit_retry_count=options.submit_retry_count,
        confirm_submit=options.confirm_submit,
        bracketed_paste=options.bracketed_paste,
        submit_delay_seconds=options.submit_delay_seconds,
        skip_blocking_wake_states=state.inflight_message_id is not None,
    )
    if injected_ids:
        state.inflight_message_id = injected_ids[-1]
        state.inflight_cursor = state.last_id
        state.inflight_logged_at = now
        state.inflight_started_at = now
    return state
