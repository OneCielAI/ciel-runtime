"""Recover a channel delivery cursor from queued-only transcript entries."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelCursorRecoveryPolicy:
    cache_ttl_seconds: float = 5.0
    transcript_max_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ChannelCursorRecoveryPorts:
    latest_transcript: Callable[[], Path | None]
    read_tail: Callable[..., str]
    queued_command_ids: Callable[[str], set[int]]
    wake_state: Callable[[int, str], str]
    clamp_to_clear_floor: Callable[[int], int]
    now: Callable[[], float]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelCursorRecoveryService:
    cache: dict[str, Any]
    policy: ChannelCursorRecoveryPolicy
    ports: ChannelCursorRecoveryPorts

    def recover(self, last_id: int) -> int:
        if last_id <= 0:
            return last_id
        path = self.ports.latest_transcript()
        if path is None:
            return last_id
        marker = self._file_marker(path)
        if marker is None:
            return last_id
        now = self.ports.now()
        if self._cache_is_fresh(last_id, marker, now):
            cached = self.cache.get("recovered_last_id")
            recovered = int(cached) if isinstance(cached, int) else last_id
            return self.ports.clamp_to_clear_floor(recovered)
        text = self.ports.read_tail(path, max_bytes=self.policy.transcript_max_bytes)
        recovered = self._recover_from_text(last_id, text)
        self.cache.update(
            {
                "checked_at": now,
                "last_id": last_id,
                "marker": marker,
                "recovered_last_id": recovered,
            }
        )
        return self.ports.clamp_to_clear_floor(recovered)

    @staticmethod
    def _file_marker(path: Path) -> tuple[str, int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return str(path), int(stat.st_mtime_ns), int(stat.st_size)

    def _cache_is_fresh(self, last_id: int, marker: tuple[str, int, int], now: float) -> bool:
        return bool(
            self.cache.get("last_id") == last_id
            and self.cache.get("marker") == marker
            and now - float(self.cache.get("checked_at") or 0.0) < self.policy.cache_ttl_seconds
        )

    def _recover_from_text(self, last_id: int, text: str) -> int:
        if not text:
            return last_id
        for message_id in sorted(self.ports.queued_command_ids(text)):
            if message_id <= last_id and self.ports.wake_state(message_id, text) == "missing":
                recovered = max(0, message_id - 1)
                self.ports.log(
                    "WARN",
                    "channel_stdin_proxy_recover_queued_only "
                    f"message_id={message_id} cursor={last_id} recovered_cursor={recovered}",
                )
                return recovered
        return last_id


__all__ = [
    "ChannelCursorRecoveryPolicy",
    "ChannelCursorRecoveryPorts",
    "ChannelCursorRecoveryService",
]
