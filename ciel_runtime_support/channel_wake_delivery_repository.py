"""Thread-safe in-memory state for channel wake delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock, RLock
from typing import Any, Callable


@dataclass(slots=True)
class DeliveryAttempts:
    """The child epoch this delivery loop owns and the attempts it persisted."""

    child_epoch: str = ""
    by_message: dict[int, str] = field(default_factory=dict)


def _message_id(message: dict[str, Any]) -> int:
    try:
        return int(message.get("id") or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class ChannelWakeDeliveryRepository:
    lock: Lock | RLock
    delivered: set[int]
    prompts: dict[int, str]
    batches: dict[int, frozenset[int]]
    clear_claim: Callable[[int], None]
    commit_cursor: Callable[[int], None]
    failed: dict[int, str] | None = None
    status: Any = None
    # (retained_limit, prune_count) and the launch-scoped attempt bookkeeping
    # (child epoch -> attempt ids) share one field: the frozen port shape is
    # capped by tests/test_architecture_contracts.py.
    retention: tuple[int, int] = (1000, 500)
    attempts: DeliveryAttempts | None = None

    def begin_attempt(self, messages: list[dict[str, Any]]) -> str:
        state = self.attempts
        if self.status is None or state is None or not state.child_epoch:
            return ""
        ids = [_message_id(message) for message in messages if _message_id(message) > 0]
        attempt_id = self.status.begin_attempt(ids, state.child_epoch)
        with self.lock:
            state.by_message.update(dict.fromkeys(ids, attempt_id))
        return attempt_id

    def bind_child_epoch(self, child_epoch: str) -> DeliveryAttempts:
        state = self.attempts
        if state is None:
            state = self.attempts = DeliveryAttempts()
        state.child_epoch = str(child_epoch or "")
        return state

    def interrupted(self, message_id: int) -> bool:
        get = getattr(self.status, "get", None)
        row = get(message_id) if callable(get) else None
        return isinstance(row, dict) and row.get("status") == "interrupted_uncertain"

    def child_exited(self, child_epoch: str, *, verified_exit: bool) -> list[int]:
        if self.status is None:
            return []
        ids = self.status.reconcile_child_exit(child_epoch, verified_exit=verified_exit)
        # Persistence above must succeed for the entire epoch before releasing
        # claims. Keep delivered proofs: uncertain does not mean not delivered.
        for message_id in ids:
            self.clear_claim(message_id)
        with self.lock:
            for message_id in ids:
                self.prompts.pop(message_id, None)
                self.batches.pop(message_id, None)
        return ids

    def prompt(self, message_id: int) -> str:
        with self.lock:
            return self.prompts.get(message_id, "")

    def is_delivered(self, message_id: int) -> bool:
        with self.lock:
            return message_id in self.delivered

    def failure_reason(self, message_id: int) -> str:
        with self.lock:
            memory_reason = str((self.failed or {}).get(message_id) or "")
        if memory_reason:
            return memory_reason
        status = self.status.get(message_id) if self.status is not None else None
        if isinstance(status, dict) and status.get("status") == "failed":
            return str(status.get("reason") or "submission_failed")
        return ""

    def release_stale(self, message_id: int, commit_cursor: bool) -> None:
        if self.interrupted(message_id):
            return
        with self.lock:
            message_ids = self._prompt_group(message_id)
            if self.failed is None:
                self.failed = {}
            for grouped_id in sorted(message_ids):
                self.failed[grouped_id] = "stale_unconfirmed"
            self._prune(self.failed)
        for grouped_id in sorted(message_ids):
            self.clear_claim(grouped_id)
            self._transition(grouped_id, "failed", reason="stale_unconfirmed")
        if commit_cursor:
            self.commit_cursor(max(message_ids))
        with self.lock:
            for grouped_id in sorted(message_ids):
                self.delivered.discard(grouped_id)
                self.prompts.pop(grouped_id, None)
                self.batches.pop(grouped_id, None)

    def complete(self, message_id: int) -> None:
        if self.interrupted(message_id):
            return
        with self.lock:
            message_ids = self._prompt_group(message_id)
        # Keep the full batch available if clearing a claim or persisting its
        # status must be retried after a transient lock-acquisition failure.
        for grouped_id in sorted(message_ids):
            self.clear_claim(grouped_id)
            self._transition(grouped_id, "replied")
        with self.lock:
            for grouped_id in sorted(message_ids):
                self.prompts.pop(grouped_id, None)
                self.batches.pop(grouped_id, None)

    def mark_delivered(self, message_id: int) -> bool:
        with self.lock:
            if message_id in self.delivered:
                return False
            self.delivered.add(message_id)
            self._prune(self.delivered)
        return True

    def record_prompts(self, messages: list[dict[str, Any]], prompt: str) -> None:
        with self.lock:
            message_ids = frozenset(
                message_id
                for message in messages
                if (message_id := _message_id(message)) > 0
            )
            for message_id in message_ids:
                self.prompts[message_id] = prompt
                self.batches[message_id] = message_ids
            self._prune(self.prompts)
            self._prune(self.batches)
        for message_id in sorted(message_ids):
            self._transition(message_id, "submitted")

    def fail(
        self,
        messages: list[dict[str, Any]],
        claimed_ids: list[int],
        reason: str,
    ) -> None:
        messages = [message for message in messages if not self.interrupted(_message_id(message))]
        claimed_ids = [value for value in claimed_ids if not self.interrupted(value)]
        message_ids: list[int] = []
        with self.lock:
            if self.failed is None:
                self.failed = {}
            for message in messages:
                message_id = _message_id(message)
                if message_id <= 0:
                    continue
                message_ids.append(message_id)
                self.delivered.discard(message_id)
                self.prompts.pop(message_id, None)
                self.batches.pop(message_id, None)
                self.failed[message_id] = str(reason)
                self.clear_claim(message_id)
            self._prune(self.failed)
        for message_id in claimed_ids:
            self.clear_claim(message_id)
        for message_id in sorted(set(message_ids)):
            self._transition(message_id, "failed", reason=reason)

    def skip(self, message_id: int, reason: str) -> None:
        if message_id > 0:
            self._transition(message_id, "skipped", reason=reason)

    def _transition(self, message_id: int, state: str, **kwargs: Any) -> None:
        if self.status is not None:
            attempts = self.attempts
            attempt_id = attempts.by_message.get(message_id) if attempts is not None else None
            if attempt_id:
                kwargs["expected_attempt"] = attempt_id
            self.status.transition(message_id, state, **kwargs)

    def rollback(
        self, messages: list[dict[str, Any]], claimed_ids: list[int]
    ) -> None:
        messages = [message for message in messages if not self.interrupted(_message_id(message))]
        claimed_ids = [value for value in claimed_ids if not self.interrupted(value)]
        with self.lock:
            for message in messages:
                message_id = _message_id(message)
                if message_id <= 0:
                    continue
                self.delivered.discard(message_id)
                self.prompts.pop(message_id, None)
                self.batches.pop(message_id, None)
                self.clear_claim(message_id)
        for message_id in claimed_ids:
            self.clear_claim(message_id)

    def _prune(self, collection: set[int] | dict[int, Any]) -> None:
        retained_limit, prune_count = self.retention
        if len(collection) <= retained_limit:
            return
        for old_id in sorted(collection)[:prune_count]:
            if isinstance(collection, set):
                collection.discard(old_id)
            else:
                collection.pop(old_id, None)

    def _prompt_group(self, message_id: int) -> set[int]:
        return set(self.batches.get(message_id) or {message_id})


__all__ = ["ChannelWakeDeliveryRepository", "DeliveryAttempts"]
