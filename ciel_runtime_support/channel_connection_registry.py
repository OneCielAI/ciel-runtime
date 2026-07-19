"""Thread-safe repository for live channel transport connection state."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import Condition, Lock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelConnectionRegistry:
    states: dict[str, dict[str, Any]]
    lock: Lock
    rpc_condition: Condition
    log: Callable[[str, str], None]
    clock: Callable[[], float] = time.time

    @staticmethod
    def public_status(name: str, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "url": state.get("url"),
            "channel": state.get("channel"),
            "sender_id": state.get("sender_id"),
            "recipient": state.get("recipient"),
            "running": bool(state.get("running")),
            "started_at": state.get("started_at"),
            "last_event_at": state.get("last_event_at"),
            "messages_received": int(state.get("messages_received") or 0),
            "event_filter": state.get("event_filter") or [],
            "read_timeout_seconds": state.get("read_timeout_seconds"),
            "last_sse_event_id": state.get("last_sse_event_id"),
            "sse_reconnects": int(state.get("sse_reconnects") or 0),
            "transport": state.get("transport") or "sse",
            "mcp_endpoint": state.get("mcp_endpoint"),
            "mcp_initialized": bool(state.get("mcp_initialized")),
            "mcp_session_id": state.get("mcp_session_id"),
            "mcp_protocol_version": state.get("mcp_protocol_version"),
            "mcp_last_error": state.get("mcp_last_error"),
            "last_error": state.get("last_error"),
        }

    def statuses(self) -> dict[str, Any]:
        with self.lock:
            return {name: self.public_status(name, state) for name, state in self.states.items()}

    def update(self, name: str, **updates: Any) -> None:
        with self.lock:
            if state := self.states.get(name):
                state.update(updates)

    def mark_session_lost(self, name: str, reason: str) -> None:
        with self.lock:
            state = self.states.get(name)
            if not state:
                return
            state.update(
                mcp_initialized=False,
                mcp_session_id=None,
                mcp_last_error=reason,
                last_error=reason,
                sse_reconnects=int(state.get("sse_reconnects") or 0) + 1,
            )

    def store_rpc_response(self, name: str, data_text: str) -> bool:
        try:
            payload = json.loads(str(data_text or "").strip())
        except (json.JSONDecodeError, TypeError):
            return False
        if (
            not isinstance(payload, dict)
            or payload.get("id") is None
            or ("result" not in payload and "error" not in payload)
        ):
            return False
        rpc_id = str(payload["id"])
        with self.rpc_condition:
            with self.lock:
                state = self.states.get(name)
                if not state:
                    return True
                results = state.get("mcp_rpc_results")
                if not isinstance(results, dict):
                    results = {}
                    state["mcp_rpc_results"] = results
                results[rpc_id] = payload
                for old_id in list(results)[: max(0, len(results) - 200)]:
                    results.pop(old_id, None)
            self.rpc_condition.notify_all()
        self.log("INFO", f"channel_sse_mcp_rpc_response name={name} id={rpc_id}")
        return True

    def take_rpc_response(
        self, name: str, rpc_id: Any, timeout: float
    ) -> dict[str, Any] | None:
        key = str(rpc_id)
        deadline = self.clock() + max(0.1, timeout)
        with self.rpc_condition:
            while True:
                with self.lock:
                    state = self.states.get(name)
                    results = state.get("mcp_rpc_results") if isinstance(state, dict) else None
                    if isinstance(results, dict) and key in results:
                        found = results.pop(key)
                        return found if isinstance(found, dict) else None
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return None
                self.rpc_condition.wait(min(remaining, 1.0))

    @staticmethod
    def public_mcp_name(name: str) -> str:
        text = str(name or "").strip()
        return text[4:] if text.startswith("mcp-") else text

    def state_name_for_mcp_server(self, server_name: str) -> str | None:
        text = str(server_name or "").strip()
        candidates = [text, text[4:] if text.startswith("mcp-") else f"mcp-{text}"] if text else []
        with self.lock:
            for candidate in candidates:
                if candidate in self.states:
                    return candidate
            return next(
                (name for name in self.states if self.public_mcp_name(name) == text),
                None,
            )
