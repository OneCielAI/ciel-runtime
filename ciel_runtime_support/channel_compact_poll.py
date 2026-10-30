"""Polling policy for injecting queued session compaction requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .channel_message_repository import ChannelFileLockBusy
from .channel_compact_injection import CompactSubmissionUncertain


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
    # The CLI the proxy wraps (selects /clear or /new for new_session) and
    # the queued actions it types; None means every action.
    runtime: str = ""
    actions: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class ChannelCompactPollPolicy:
    poll_interval_seconds: float = 0.5
    defer_log_interval_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ChannelCompactPollServices:
    inject_pending: Callable[..., str]
    # Recovery compaction may bypass an old unconfirmed channel wake, but the
    # injector still requires a safe native turn/tool boundary.
    recovery_pending: Callable[[], bool] = lambda: False


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
    recovery_protected: bool = False,
) -> ChannelCompactPollState:
    if not input_ready or now - state.last_poll_at < policy.poll_interval_seconds:
        return state
    try:
        if inflight_message_id is not None and not (services.recovery_pending() or recovery_protected):
            return ChannelCompactPollState(now, state.defer_logged_at)

        # A compact can retire the transcript that confirmed an earlier wake.
        # Keep that wake's ownership; only session commands may cross its hold,
        # still subject to the injector's tool/turn/draft guards.
        log_defer = now - state.defer_logged_at >= policy.defer_log_interval_seconds
        status = services.inject_pending(
            writer,
            enter_bytes,
            log_defer=log_defer,
            submit_retry_count=options.submit_retry_count,
            confirm_submit=options.confirm_submit,
            bracketed_paste=options.bracketed_paste,
            submit_delay_seconds=options.submit_delay_seconds,
            runtime=options.runtime,
            actions=options.actions,
        )
    except CompactSubmissionUncertain:
        # The injector persisted the failed-open result. Do not kill the SSE
        # poller or replay this command; the controller rejects its old id.
        return ChannelCompactPollState(now, state.defer_logged_at)
    except ChannelFileLockBusy:
        # The injector masks this type once submission may have started.
        # Leave the request and defer-log timestamp intact for a bounded poll.
        return ChannelCompactPollState(now, state.defer_logged_at)
    defer_logged_at = state.defer_logged_at
    if status == "deferred" and log_defer:
        defer_logged_at = now
    elif status == "injected":
        defer_logged_at = 0.0
    return ChannelCompactPollState(now, defer_logged_at)
