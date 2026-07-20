"""Bound and deduplicate long-running MCP notification wait tool calls."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, Callable


WAIT_TOOL_NAMES = frozenset(
    {
        "wait_for_notification",
        "wait_for_notifications",
        "wait_for_message",
        "wait_for_messages",
        "wait_for_event",
        "wait_for_events",
        "wait_for_response",
        "wait_for_responses",
    }
)


@dataclass(frozen=True, slots=True)
class McpNotificationWaitPolicy:
    env: Callable[[str], str | None]

    def timeout_cap_ms(self) -> int:
        return self._bounded_int(
            "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_TIMEOUT_MS", 1000, 100, 10_000
        )

    def duplicate_cap_ms(self) -> int:
        return self._bounded_int(
            "CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_TIMEOUT_MS", 100, 50, 5000
        )

    def duplicate_window_seconds(self) -> float:
        raw = self.env("CIEL_RUNTIME_MCP_NOTIFICATION_WAIT_DUPLICATE_WINDOW_SECONDS")
        if raw is None:
            return 90.0
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError):
            return 90.0
        return max(0.0, min(600.0, value))

    def _bounded_int(self, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = self.env(name)
        if raw is None:
            return default
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return default
        return 0 if value <= 0 else max(minimum, min(maximum, value))


class McpNotificationWaitRepository:
    def __init__(self, recent: dict[str, float], lock: Lock | RLock) -> None:
        self.recent = recent
        self._lock = lock

    def mark_and_is_duplicate(self, key: str, now: float, window: float) -> bool:
        with self._lock:
            stale = [name for name, seen_at in self.recent.items() if now - seen_at > window]
            for name in stale:
                self.recent.pop(name, None)
            previous = self.recent.get(key)
            duplicate = previous is not None and now - previous <= window
            self.recent[key] = now
            return duplicate


@dataclass(frozen=True, slots=True)
class McpNotificationWaitPorts:
    lookup_schema: Callable[[str], dict[str, Any] | None]
    now: Callable[[], float]
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class McpNotificationWaitService:
    policy: McpNotificationWaitPolicy
    repository: McpNotificationWaitRepository
    ports: McpNotificationWaitPorts

    @staticmethod
    def tool_leaf_name(tool_name: str) -> str:
        text = str(tool_name or "").strip()
        return text.rsplit("__", 1)[-1].strip().lower() if "__" in text else text.lower()

    def is_wait_tool(self, tool_name: str) -> bool:
        text = str(tool_name or "").strip().lower()
        return text.startswith("mcp__") and self.tool_leaf_name(text) in WAIT_TOOL_NAMES

    def effective_cap_ms(self, tool_name: str) -> tuple[int, bool]:
        cap_ms = self.policy.timeout_cap_ms()
        if cap_ms <= 0:
            return 0, False
        duplicate_cap_ms = self.policy.duplicate_cap_ms()
        window = self.policy.duplicate_window_seconds()
        if duplicate_cap_ms <= 0 or window <= 0:
            return cap_ms, False
        key = str(tool_name or "").strip().lower()
        duplicate = self.repository.mark_and_is_duplicate(key, self.ports.now(), window)
        return (min(cap_ms, duplicate_cap_ms), True) if duplicate else (cap_ms, False)

    def cap_input(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if not self.is_wait_tool(tool_name):
            return tool_input
        cap_ms, duplicate = self.effective_cap_ms(tool_name)
        if cap_ms <= 0:
            return tool_input
        fixed = dict(tool_input) if isinstance(tool_input, dict) else {}
        schema = self.ports.lookup_schema(tool_name) or {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        changed: list[str] = []
        for key in list(fixed):
            key_lower = str(key).strip().lower()
            if key_lower in {"timeout_ms", "timeoutms", "wait_ms", "waitms", "max_wait_ms", "maxwaitms"}:
                self._set_if_lower(fixed, changed, key, cap_ms)
            elif key_lower in {"timeout", "wait_seconds", "wait_s", "max_wait_seconds"}:
                self._set_if_lower(fixed, changed, key, max(0.1, cap_ms / 1000.0))
        if not changed:
            if "timeout_ms" in properties or "timeout_ms" in fixed or not properties:
                self._set_if_lower(fixed, changed, "timeout_ms", cap_ms)
            elif "timeout" in properties:
                self._set_if_lower(fixed, changed, "timeout", max(0.1, cap_ms / 1000.0))
        if changed:
            duplicate_label = " duplicate=true" if duplicate else ""
            self.ports.log(
                "INFO",
                f"mcp_notification_wait_timeout_capped tool={tool_name}{duplicate_label} {' '.join(changed)}",
            )
        return fixed

    @staticmethod
    def _set_if_lower(
        target: dict[str, Any], changed: list[str], key: str, value: int | float
    ) -> None:
        old = target.get(key)
        try:
            numeric = float(old)
        except Exception:
            target[key] = int(value) if float(value).is_integer() else value
            changed.append(f"{key}=missing->{value:g}")
            return
        if numeric > float(value):
            target[key] = int(value) if float(value).is_integer() else value
            changed.append(f"{key}={numeric:g}->{value:g}")


__all__ = [
    "McpNotificationWaitPolicy",
    "McpNotificationWaitPorts",
    "McpNotificationWaitRepository",
    "McpNotificationWaitService",
    "WAIT_TOOL_NAMES",
]
