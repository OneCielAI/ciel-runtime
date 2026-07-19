"""Persistent repository for provider rate usage and API-key cooldown state."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock, RLock
from typing import Any


class RateLimitRepository:
    def __init__(
        self,
        config_dir: Path,
        state_path: Path,
        lock: Lock | RLock,
        log: Callable[[str, str], None],
    ) -> None:
        self.config_dir = config_dir
        self.state_path = state_path
        self.lock = lock
        self.log = log

    def entry(self, key: str, legacy_key: str) -> dict[str, Any]:
        state = self._read()
        entry = state.get(key)
        if not isinstance(entry, dict):
            entry = state.get(legacy_key)
        return entry if isinstance(entry, dict) else {}

    def effective_rpm(self, key: str, legacy_key: str, configured: int | None) -> int | None:
        if configured == 0:
            return 0
        entry = self.entry(key, legacy_key)
        try:
            server_rpm = int(entry.get("server_rpm") or 0)
            updated_at = float(entry.get("server_rpm_updated_at") or 0.0)
            if server_rpm > 0 and 0.0 <= time.time() - updated_at < 3600.0:
                return server_rpm
        except Exception:
            pass
        return configured

    def usage(
        self,
        key: str,
        legacy_key: str,
        rpm: int | None,
        recent: Callable[..., list[float]],
    ) -> tuple[int, int | None]:
        if rpm is None:
            return 0, None
        if rpm == 0:
            return 0, 0
        state = self._read()
        entry = state.get(key)
        if not isinstance(entry, dict):
            entry = state.get(legacy_key)
        timestamps = (
            entry.get("timestamps")
            if isinstance(entry, dict)
            else [float(entry)]
            if isinstance(entry, (int, float))
            else []
        )
        used = len(recent(timestamps, time.time(), 60.0, include_future=False))
        return used, rpm

    def record_usage(
        self,
        key: str,
        legacy_key: str,
        rpm: int | None,
        recent: Callable[..., list[float]],
    ) -> tuple[int, int | None]:
        if rpm is None:
            return 0, None
        with self.lock:
            state = self._read()
            now = time.time()
            entry = state.get(key)
            if not isinstance(entry, dict):
                entry = state.get(legacy_key)
            timestamps = (
                entry.get("timestamps")
                if isinstance(entry, dict)
                else [float(entry)]
                if isinstance(entry, (int, float))
                else []
            )
            values = recent(timestamps, now, 60.0, include_future=True)
            values.append(now)
            new_entry: dict[str, Any] = {
                "timestamps": values[-max(int(rpm or 0), 240) :],
                "rpm": int(rpm or 0),
                "updated_at": now,
                "last_wait": 0.0,
            }
            existing_penalty = (
                float(entry.get("penalty_until") or 0.0)
                if isinstance(entry, dict)
                else 0.0
            )
            if existing_penalty > now:
                new_entry["penalty_until"] = existing_penalty
            state[key] = new_entry
            self._write(state)
            return len(values), rpm

    def register_cooldown(self, state_key: str, seconds: float) -> None:
        with self.lock:
            state = self._read()
            now = time.time()
            state[state_key] = {"cooldown_until": now + seconds, "last_429_at": now}
            self._write(state)

    def cooldown_until(self, state_key: str) -> float:
        with self.lock:
            entry = self._read().get(state_key)
        if not isinstance(entry, dict):
            return 0.0
        try:
            until = float(entry.get("cooldown_until") or 0.0)
        except Exception:
            return 0.0
        return until if until > time.time() else 0.0

    def reset_key_cooldowns(self) -> int:
        with self.lock:
            state = self._read()
            kept = {key: value for key, value in state.items() if ":__key__:" not in str(key)}
            removed = len(state) - len(kept)
            if removed > 0:
                self._write(kept)
        return removed

    def _read(self) -> dict[str, Any]:
        try:
            value = (
                json.loads(self.state_path.read_text(encoding="utf-8"))
                if self.state_path.exists()
                else {}
            )
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _write(self, state: dict[str, Any]) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8"
        )
