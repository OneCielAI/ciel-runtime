"""Channel message duplicate-detection application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChannelMessageDedupePorts:
    stable_key: Callable[[dict[str, Any]], Any]
    fallback_key: Callable[[dict[str, Any]], Any]
    recent_rows: Callable[[], list[dict[str, Any]]]
    launch_guard: Callable[[], dict[str, Any] | None]
    timestamp_seconds: Callable[[Any], float]
    now: Callable[[], float]


@dataclass(frozen=True, slots=True)
class ChannelMessageDedupeService:
    ports: ChannelMessageDedupePorts
    fallback_ttl_seconds: float

    def duplicate(
        self,
        message: dict[str, Any],
    ) -> dict[str, Any] | None:
        stable_key = self.ports.stable_key(message)
        fallback_key = self.ports.fallback_key(message)
        if not stable_key and not fallback_key:
            return None
        now = self.ports.now()
        launch_guard = (
            self.ports.launch_guard() if fallback_key else None
        )
        guard_max_existing_id = (
            int(launch_guard.get("max_existing_id") or 0)
            if launch_guard
            else 0
        )
        for row in reversed(self.ports.recent_rows()):
            if stable_key and self.ports.stable_key(row) == stable_key:
                return row
            if (
                not fallback_key
                or self.ports.fallback_key(row) != fallback_key
            ):
                continue
            row_time = self.ports.timestamp_seconds(row.get("time"))
            if (
                row_time > 0
                and now - row_time <= self.fallback_ttl_seconds
            ):
                return row
            try:
                row_id = int(row.get("id") or 0)
            except Exception:
                row_id = 0
            if (
                guard_max_existing_id > 0
                and 0 < row_id <= guard_max_existing_id
            ):
                return row
        return None
