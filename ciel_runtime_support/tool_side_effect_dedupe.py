"""Deduplicate exact repeated side-effect tool calls within a bounded window."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupePolicy:
    side_effect_suffixes: frozenset[str]
    ttl_seconds: float = 600.0


class ToolSideEffectDedupeRepository:
    def __init__(self, recent: dict[str, float], lock: Lock | RLock) -> None:
        self.recent = recent
        self._lock = lock

    def previous_or_record(self, key: str, now: float, ttl_seconds: float) -> float | None:
        with self._lock:
            expired = [name for name, seen_at in self.recent.items() if now - seen_at > ttl_seconds]
            for name in expired:
                self.recent.pop(name, None)
            previous = self.recent.get(key)
            if previous is None or now - previous > ttl_seconds:
                self.recent[key] = now
                return None
            return previous


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupePorts:
    now: Callable[[], float]
    audit: Callable[[str, dict[str, Any]], None]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ToolSideEffectDedupeService:
    policy: ToolSideEffectDedupePolicy
    repository: ToolSideEffectDedupeRepository
    ports: ToolSideEffectDedupePorts

    def key(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        if not isinstance(tool_name, str) or not tool_name:
            return None
        normalized_name = tool_name.strip()
        tool_leaf = normalized_name.rsplit("__", 1)[-1].strip().lower()
        if tool_leaf not in self.policy.side_effect_suffixes:
            return None
        try:
            payload = json.dumps(
                tool_input or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
            )
        except Exception:
            payload = repr(tool_input)
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
        return f"{normalized_name}:{digest}"

    def should_drop(self, tool_name: str, tool_input: dict[str, Any], raw_name: str = "") -> bool:
        key = self.key(tool_name, tool_input)
        if not key:
            return False
        now = self.ports.now()
        previous = self.repository.previous_or_record(key, now, self.policy.ttl_seconds)
        if previous is None:
            return False
        age = now - previous
        self.ports.audit(
            "dropped_duplicate_side_effect_tool_call",
            {
                "raw_name": raw_name or tool_name,
                "matched_name": tool_name,
                "emitted_input": tool_input,
                "age_seconds": round(age, 3),
                "ttl_seconds": self.policy.ttl_seconds,
            },
        )
        self.ports.log(
            "WARN",
            f"dropped duplicate side-effect tool call raw_name={raw_name or tool_name!r} "
            f"matched_name={tool_name!r} age={age:.1f}s",
        )
        return True


__all__ = [
    "ToolSideEffectDedupePolicy",
    "ToolSideEffectDedupePorts",
    "ToolSideEffectDedupeRepository",
    "ToolSideEffectDedupeService",
]
