"""Application service for inspecting and discarding transient channel backlog."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelBacklogCursors:
    scan_tail: Callable[[], int]
    llm_lock: Any
    read_llm: Callable[[], int]
    write_llm: Callable[[int], None]
    cache_llm: Callable[[int], None]
    write_clear_floor: Callable[[int], None]
    mcp_lock: Any
    read_mcp: Callable[[], int]
    write_mcp: Callable[[int], None]
    cache_mcp: Callable[[int], None]


@dataclass(frozen=True, slots=True)
class ChannelBacklogRuntime:
    recovery_cache: dict[Any, Any]
    session_lock: Any
    sessions: dict[Any, dict[str, Any]]
    condition: Any
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelBacklogService:
    cursors: ChannelBacklogCursors
    runtime: ChannelBacklogRuntime

    def clear(self) -> dict[str, Any]:
        tail = max(0, self.cursors.scan_tail())
        with self.cursors.llm_lock:
            old_llm = self.cursors.read_llm()
            self.cursors.cache_llm(tail)
            self._write_cursor("channel_llm_cursor", self.cursors.write_llm, tail)
            self._write_cursor("channel_llm_clear_floor", self.cursors.write_clear_floor, tail)
        self.runtime.recovery_cache.clear()
        with self.cursors.mcp_lock:
            old_mcp = self.cursors.read_mcp()
            self.cursors.cache_mcp(tail)
            self._write_cursor("channel_mcp_cursor", self.cursors.write_mcp, tail)
        with self.runtime.session_lock:
            for state in self.runtime.sessions.values():
                try:
                    state["last_id"] = max(int(state.get("last_id") or 0), tail)
                except (TypeError, ValueError, OverflowError):
                    state["last_id"] = tail
            session_count = len(self.runtime.sessions)
        with self.runtime.condition:
            self.runtime.condition.notify_all()
        stats = {
            "chat_tail": tail,
            "discarded_llm": max(0, tail - int(old_llm or 0)),
            "discarded_mcp": max(0, tail - int(old_mcp or 0)),
            "mcp_sessions_updated": session_count,
        }
        self.runtime.log(
            "INFO",
            f"channel_backlog_cleared chat_tail={tail} discarded_llm={stats['discarded_llm']} "
            f"discarded_mcp={stats['discarded_mcp']} mcp_sessions_updated={session_count}",
        )
        return stats

    def status(self) -> dict[str, Any]:
        tail = max(0, self.cursors.scan_tail())
        with self.cursors.llm_lock:
            llm_cursor = self.cursors.read_llm()
        with self.cursors.mcp_lock:
            mcp_cursor = self.cursors.read_mcp()
        return {
            "chat_tail": tail,
            "pending_llm": max(0, tail - int(llm_cursor or 0)),
            "pending_mcp": max(0, tail - int(mcp_cursor or 0)),
            "mcp_sessions": len(self.runtime.sessions),
        }

    def _write_cursor(self, name: str, writer: Callable[[int], None], tail: int) -> None:
        try:
            writer(tail)
        except Exception as exc:
            self.runtime.log("WARN", f"{name}_write_failed error={type(exc).__name__}: {exc}")


__all__ = ["ChannelBacklogCursors", "ChannelBacklogRuntime", "ChannelBacklogService"]
