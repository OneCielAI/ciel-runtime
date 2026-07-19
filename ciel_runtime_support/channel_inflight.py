"""State machine for channel wake prompts awaiting turn completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


InflightAction = Literal["none", "completed", "unseen_retry", "stale", "waiting"]


@dataclass(frozen=True, slots=True)
class ChannelInflightSnapshot:
    message_id: int
    cursor: int | None
    wake_state: str
    started_at: float
    logged_at: float
    now: float
    last_id: int


@dataclass(frozen=True, slots=True)
class ChannelInflightPolicy:
    unseen_retry_seconds: float
    waiting_log_interval: float
    is_stale: Callable[[str, float, float], bool]
    commit_cursor_on_stale: bool
    log_namespace: str
    stale_event: str


@dataclass(frozen=True, slots=True)
class ChannelInflightEffects:
    commit_cursor: Callable[[int], Any]
    complete_wake: Callable[[int], Any]
    release_wake: Callable[[int], Any]
    ensure_cursor: Callable[[], int]
    log: Callable[[str, str], Any]


@dataclass(frozen=True, slots=True)
class ChannelInflightUpdate:
    action: InflightAction
    message_id: int | None
    cursor: int | None
    started_at: float
    logged_at: float
    pending_recheck: bool
    last_id: int


def advance_channel_inflight(
    snapshot: ChannelInflightSnapshot,
    policy: ChannelInflightPolicy,
    effects: ChannelInflightEffects,
) -> ChannelInflightUpdate:
    message_id = snapshot.message_id
    state = snapshot.wake_state
    age = snapshot.now - snapshot.started_at
    if state == "completed":
        if snapshot.cursor is not None:
            effects.commit_cursor(snapshot.cursor)
        effects.complete_wake(message_id)
        effects.log(
            "INFO",
            f"{policy.log_namespace}_confirmed message_id={message_id} cursor={snapshot.cursor or '-'}",
        )
        return _reset("completed", snapshot)
    if state == "missing" and snapshot.started_at > 0 and age >= policy.unseen_retry_seconds:
        effects.release_wake(message_id)
        effects.log(
            "WARN",
            f"{policy.log_namespace}_unseen_retry message_id={message_id} age={age:.1f}s",
        )
        return _reset("unseen_retry", snapshot, last_id=effects.ensure_cursor(), logged_at=snapshot.now)
    if policy.is_stale(state, snapshot.started_at, snapshot.now):
        if policy.commit_cursor_on_stale and snapshot.cursor is not None:
            effects.commit_cursor(snapshot.cursor)
        effects.release_wake(message_id)
        effects.log(
            "WARN",
            f"{policy.log_namespace}_{policy.stale_event} message_id={message_id} state={state} "
            f"age={age:.1f}s cursor={snapshot.cursor or '-'}",
        )
        return _reset("stale", snapshot, last_id=effects.ensure_cursor(), logged_at=snapshot.now)
    if snapshot.now - snapshot.logged_at >= policy.waiting_log_interval:
        effects.log(
            "INFO",
            f"{policy.log_namespace}_waiting_for_turn_completion message_id={message_id} state={state}",
        )
        return ChannelInflightUpdate(
            action="waiting",
            message_id=message_id,
            cursor=snapshot.cursor,
            started_at=snapshot.started_at,
            logged_at=snapshot.now,
            pending_recheck=False,
            last_id=snapshot.last_id,
        )
    return ChannelInflightUpdate(
        action="none",
        message_id=message_id,
        cursor=snapshot.cursor,
        started_at=snapshot.started_at,
        logged_at=snapshot.logged_at,
        pending_recheck=False,
        last_id=snapshot.last_id,
    )


def _reset(
    action: InflightAction,
    snapshot: ChannelInflightSnapshot,
    *,
    last_id: int | None = None,
    logged_at: float | None = None,
) -> ChannelInflightUpdate:
    return ChannelInflightUpdate(
        action=action,
        message_id=None,
        cursor=None,
        started_at=0.0,
        logged_at=snapshot.logged_at if logged_at is None else logged_at,
        pending_recheck=True,
        last_id=snapshot.last_id if last_id is None else last_id,
    )
