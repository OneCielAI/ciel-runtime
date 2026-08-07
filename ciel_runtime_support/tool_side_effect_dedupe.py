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
    repeated_execution_suffixes: frozenset[str] = frozenset(
        {"shell_command", "bash", "exec", "execute", "run_command", "write", "edit", "apply_patch"}
    )
    # One completed call can legitimately be retried after a client/process
    # interruption because the resumed request carries no explicit new user
    # message.  Only stop the call after the same successful result has already
    # been observed twice consecutively.  This still bounds genuine model loops
    # without treating a single historical completion as a permanent lock.
    completed_repeat_limit: int = 2


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

    @staticmethod
    def _normalized_payload(value: Any) -> Any:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [ToolSideEffectDedupeService._normalized_payload(item) for item in value]
        if isinstance(value, dict):
            return {
                key: ToolSideEffectDedupeService._normalized_payload(item)
                for key, item in value.items()
            }
        return value

    @classmethod
    def _signature(cls, tool_name: str, tool_input: Any) -> str:
        payload = json.dumps(
            cls._normalized_payload(tool_input if isinstance(tool_input, dict) else {}),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return f"{tool_name}:{payload}"

    def completed_repeat_count(
        self, source_body: dict[str, Any] | None, tool_name: str, tool_input: dict[str, Any]
    ) -> int:
        if not isinstance(source_body, dict):
            return 0
        tool_leaf = tool_name.rsplit("__", 1)[-1].strip().lower()
        if tool_leaf not in self.policy.repeated_execution_suffixes:
            return 0
        target = self._signature(tool_name, tool_input)
        tool_uses: dict[str, str] = {}
        completed: list[str | None] = []
        for message in source_body.get("messages") or []:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            blocks = content if isinstance(content, list) else []
            if message.get("role") == "assistant":
                for block in blocks:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    tool_id = str(block.get("id") or "")
                    if tool_id:
                        tool_uses[tool_id] = self._signature(
                            str(block.get("name") or ""), block.get("input")
                        )
                continue
            if message.get("role") != "user":
                continue
            results = [
                block
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            has_new_intent = isinstance(content, str) and bool(content.strip())
            has_new_intent = has_new_intent or any(
                not isinstance(block, dict) or block.get("type") != "tool_result"
                for block in blocks
            )
            if has_new_intent:
                completed.clear()
            for block in results:
                signature = tool_uses.get(str(block.get("tool_use_id") or ""))
                completed.append(None if block.get("is_error") else signature)
        count = 0
        for signature in reversed(completed):
            if signature != target:
                break
            count += 1
        return count

    def should_drop(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        raw_name: str = "",
        source_body: dict[str, Any] | None = None,
    ) -> bool:
        key = self.key(tool_name, tool_input)
        if key:
            now = self.ports.now()
            previous = self.repository.previous_or_record(key, now, self.policy.ttl_seconds)
            if previous is not None:
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
        repeat_count = self.completed_repeat_count(source_body, tool_name, tool_input)
        if repeat_count < max(1, self.policy.completed_repeat_limit):
            return False
        self.ports.audit(
            "dropped_repeated_completed_tool_call",
            {
                "raw_name": raw_name or tool_name,
                "matched_name": tool_name,
                "emitted_input": tool_input,
                "completed_repeats": repeat_count,
            },
        )
        self.ports.log(
            "WARN",
            f"dropped repeated completed tool call raw_name={raw_name or tool_name!r} "
            f"matched_name={tool_name!r} completed_repeats={repeat_count}",
        )
        return True


__all__ = [
    "ToolSideEffectDedupePolicy",
    "ToolSideEffectDedupePorts",
    "ToolSideEffectDedupeRepository",
    "ToolSideEffectDedupeService",
]
