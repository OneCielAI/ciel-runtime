"""Polling policy for injecting queued session compaction requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelCompactPollState:
    last_poll_at: float = 0.0
    defer_logged_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ChannelCompactInjectionOptions:
    submit_retry_count: int
    confirm_submit: bool
    bracketed_paste: bool
    submit_delay_seconds: float | None


@dataclass(frozen=True, slots=True)
class ChannelCompactPollPolicy:
    poll_interval_seconds: float = 0.5
    defer_log_interval_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ChannelCompactPollServices:
    inject_pending: Callable[..., str]


def poll_pending_compaction(
    now: float,
    writer: Any,
    enter_bytes: bytes,
    inflight_message_id: int | None,
    state: ChannelCompactPollState,
    options: ChannelCompactInjectionOptions,
    services: ChannelCompactPollServices,
    policy: ChannelCompactPollPolicy = ChannelCompactPollPolicy(),
    *,
    input_ready: bool = True,
) -> ChannelCompactPollState:
    if not input_ready or now - state.last_poll_at < policy.poll_interval_seconds:
        return state
    if inflight_message_id is not None:
        return ChannelCompactPollState(now, state.defer_logged_at)

    log_defer = now - state.defer_logged_at >= policy.defer_log_interval_seconds
    status = services.inject_pending(
        writer,
        enter_bytes,
        log_defer=log_defer,
        submit_retry_count=options.submit_retry_count,
        confirm_submit=options.confirm_submit,
        bracketed_paste=options.bracketed_paste,
        submit_delay_seconds=options.submit_delay_seconds,
    )
    defer_logged_at = state.defer_logged_at
    if status == "deferred" and log_defer:
        defer_logged_at = now
    elif status == "injected":
        defer_logged_at = 0.0
    return ChannelCompactPollState(now, defer_logged_at)
