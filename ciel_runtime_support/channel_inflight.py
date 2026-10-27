"""State machine for channel wake prompts awaiting turn completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from .runtime_input_retry import is_failed_state


InflightAction = Literal["none", "completed", "failed", "retrying", "waiting", "interrupted_uncertain"]


@dataclass(frozen=True, slots=True)
class ChannelInflightSnapshot:
    message_id: int
    cursor: int | None
    wake_state: str
    started_at: float
    logged_at: float
    now: float
    last_id: int
    recovery_protected: bool = False
    # Admission holds suppress timeout-derived transitions, not exact evidence.
    admission_paused: bool = False


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
    # (message_id, failed wake state) -> "retry" | "failed" | "stale".  Unset
    # keeps the old reading of an API error record as completion.
    turn_failed: Callable[[int, str, float], str] | None = None


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
    if state == "interrupted_uncertain":
        # This state is emitted only after durable exit reconciliation. It is
        # not a completion, failure, or invitation to replay the input.
        if snapshot.cursor is not None:
            effects.commit_cursor(snapshot.cursor)
        effects.log("WARN", f"{policy.log_namespace}_interrupted_uncertain message_id={message_id} action=no_replay")
        return _reset("interrupted_uncertain", snapshot)
    if is_failed_state(state):
        # The prompt was answered by an API error. A transient one is resent
        # (the input is queued again with its resend record); any other ends
        # it failed with its typed reason and delivery moves on.
        outcome = effects.turn_failed(message_id, state, snapshot.started_at) if effects.turn_failed else "completed"
        if outcome == "retry":
            effects.log("WARN", f"{policy.log_namespace}_turn_failed message_id={message_id} action=retry_scheduled")
            return _reset("retrying", snapshot, last_id=effects.ensure_cursor())
        if outcome == "failed":
            if snapshot.cursor is not None:
                effects.commit_cursor(snapshot.cursor)
            effects.log("WARN", f"{policy.log_namespace}_turn_failed message_id={message_id} action=failed")
            return _reset("failed", snapshot, last_id=effects.ensure_cursor())
        # "stale": the error belongs to an earlier attempt; this attempt has
        # left no evidence yet, which is exactly "missing".
        state = "completed" if outcome == "completed" else "missing"
    if state == "completed":
        if snapshot.cursor is not None:
            effects.commit_cursor(snapshot.cursor)
        effects.complete_wake(message_id)
        effects.log(
            "INFO",
            f"{policy.log_namespace}_confirmed message_id={message_id} cursor={snapshot.cursor or '-'}",
        )
        return _reset("completed", snapshot)
    # Recovery can replace/truncate transcript evidence. Absence after that
    # boundary proves neither failure nor completion, regardless of elapsed
    # time. Keep ownership until evidence resolves it; never release to replay.
    age_failure_allowed = not snapshot.admission_paused and not (
        snapshot.recovery_protected and state in {"unknown", "missing"}
    )
    if age_failure_allowed and state == "missing" and snapshot.started_at > 0 and age >= policy.unseen_retry_seconds:
        effects.release_wake(message_id)
        effects.log(
            "WARN",
            f"{policy.log_namespace}_unseen_failed message_id={message_id} age={age:.1f}s",
        )
        return _reset("failed", snapshot, last_id=effects.ensure_cursor(), logged_at=snapshot.now)
    if age_failure_allowed and policy.is_stale(state, snapshot.started_at, snapshot.now):
        if policy.commit_cursor_on_stale and snapshot.cursor is not None:
            effects.commit_cursor(snapshot.cursor)
        effects.release_wake(message_id)
        effects.log(
            "WARN",
            f"{policy.log_namespace}_{policy.stale_event} message_id={message_id} state={state} "
            f"age={age:.1f}s cursor={snapshot.cursor or '-'}",
        )
        return _reset("failed", snapshot, last_id=effects.ensure_cursor(), logged_at=snapshot.now)
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
