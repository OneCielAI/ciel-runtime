"""Thread-safe in-memory state for channel wake delivery."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, Callable


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
    retained_limit: int = 1000
    prune_count: int = 500

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
        with self.lock:
            message_ids = self._prompt_group(message_id)
            if self.failed is None:
                self.failed = {}
            for grouped_id in sorted(message_ids):
                self.delivered.discard(grouped_id)
                self.prompts.pop(grouped_id, None)
                self.batches.pop(grouped_id, None)
                self.failed[grouped_id] = "stale_unconfirmed"
            self._prune(self.failed)
        for grouped_id in sorted(message_ids):
            self.clear_claim(grouped_id)
            self._transition(grouped_id, "failed", reason="stale_unconfirmed")
        if commit_cursor:
            self.commit_cursor(max(message_ids))

    def complete(self, message_id: int) -> None:
        with self.lock:
            message_ids = self._prompt_group(message_id)
            for grouped_id in sorted(message_ids):
                self.prompts.pop(grouped_id, None)
                self.batches.pop(grouped_id, None)
        for grouped_id in sorted(message_ids):
            self.clear_claim(grouped_id)
            self._transition(grouped_id, "replied")

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

    def _transition(self, message_id: int, state: str, **kwargs: Any) -> None:
        if self.status is not None:
            self.status.transition(message_id, state, **kwargs)

    def rollback(
        self, messages: list[dict[str, Any]], claimed_ids: list[int]
    ) -> None:
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
        if len(collection) <= self.retained_limit:
            return
        for old_id in sorted(collection)[: self.prune_count]:
            if isinstance(collection, set):
                collection.discard(old_id)
            else:
                collection.pop(old_id, None)

    def _prompt_group(self, message_id: int) -> set[int]:
        return set(self.batches.get(message_id) or {message_id})


__all__ = ["ChannelWakeDeliveryRepository"]
