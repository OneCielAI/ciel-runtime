"""Lifecycle service for creating and stopping channel transport connections."""

from __future__ import annotations

import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelConnectionLifecycleEffects:
    safe_segment: Callable[[str, str], str]
    close_session: Callable[[dict[str, Any], str], None]
    cleanup_stale_sessions: Callable[[str, str, dict[str, str], str], None]
    public_status: Callable[[str, dict[str, Any]], dict[str, Any]]
    all_statuses: Callable[[], dict[str, Any]]
    sse_worker: Callable[[str, str | None], None]
    streamable_http_worker: Callable[[str, str | None], None]


@dataclass(frozen=True, slots=True)
class ChannelConnectionLifecyclePolicy:
    streamable_protocol_version: str
    legacy_sse_protocol_version: str
    parse_bool: Callable[[Any, bool], bool]


@dataclass(frozen=True, slots=True)
class ChannelConnectionLifecycleStore:
    states: dict[str, dict[str, Any]]
    lock: Lock

    def replace(self, name: str, state: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            prior = self.states.get(name)
            if prior:
                prior["running"] = False
            self.states[name] = state
            return dict(prior) if prior else None

    def stop(self, name: str | None) -> tuple[list[str], list[dict[str, Any]]]:
        stopped: list[str] = []
        states_to_close: list[dict[str, Any]] = []
        with self.lock:
            targets = [name] if name else list(self.states)
            for target in targets:
                if not target:
                    continue
                state = self.states.get(target)
                if state:
                    state["running"] = False
                    states_to_close.append(dict(state))
                    stopped.append(target)
        return stopped, states_to_close


@dataclass(frozen=True, slots=True)
class ChannelConnectionLifecycle:
    store: ChannelConnectionLifecycleStore
    effects: ChannelConnectionLifecycleEffects
    policy: ChannelConnectionLifecyclePolicy

    @staticmethod
    def _headers(config: dict[str, Any]) -> dict[str, str]:
        raw_headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
        headers = {str(key): str(value) for key, value in raw_headers.items() if str(key).strip()}
        token = str(config.get("bearer_token") or config.get("token") or "").strip()
        if token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _event_filter(config: dict[str, Any]) -> list[str]:
        event_filter = config.get("event_filter")
        if isinstance(event_filter, str):
            return [item.strip() for item in event_filter.split(",") if item.strip()]
        if isinstance(event_filter, list):
            return [str(item).strip() for item in event_filter if str(item).strip()]
        return []

    def _new_state(
        self,
        config: dict[str, Any],
        name: str,
        url: str,
        transport: str,
        headers: dict[str, str],
        connection_id: str,
    ) -> dict[str, Any]:
        protocol_version = (
            self.policy.streamable_protocol_version
            if transport == "streamable-http"
            else self.policy.legacy_sse_protocol_version
        )
        return {
            "name": name,
            "connection_id": connection_id,
            "url": url,
            "headers": headers,
            "channel": str(config.get("channel") or "default"),
            "sender_id": str(config.get("sender_id") or config.get("sender") or name),
            "recipient": str(config.get("recipient") or config.get("recipient_id") or config.get("to") or "all"),
            "running": True,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_event_at": None,
            "messages_received": 0,
            "last_error": None,
            "event_filter": self._event_filter(config),
            "last_sse_event_id": str(
                config.get("last_sse_event_id")
                or config.get("last_event_id")
                or config.get("lastEventId")
                or ""
            ).strip(),
            "sse_reconnects": 0,
            "read_timeout_seconds": float(config.get("read_timeout_seconds") or config.get("timeout") or 300.0),
            "retry_seconds": float(config.get("retry_seconds") or 5.0),
            "mcp_enabled": bool(config.get("mcp", config.get("mcp_enabled", True))),
            "mcp_endpoint": None,
            "mcp_initialized": False,
            "mcp_session_id": None,
            "mcp_last_error": None,
            "mcp_rpc_results": {},
            "transport": transport,
            "streamable_requires_session": self.policy.parse_bool(
                config.get(
                    "streamable_requires_session",
                    config.get("require_session", config.get("mcp_session_required", True)),
                ),
                True,
            ),
            "mcp_protocol_version": str(config.get("mcp_protocol_version") or protocol_version),
            "mcp_timeout_seconds": float(config.get("mcp_timeout_seconds") or 20.0),
        }

    def start(self, config: dict[str, Any]) -> dict[str, Any]:
        url = str(config.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("SSE url must start with http:// or https://")
        name = self.effects.safe_segment(
            str(config.get("name") or urllib.parse.urlparse(url).netloc or "sse"), "sse"
        )
        declared_transport = str(config.get("transport") or config.get("type") or "").strip().lower()
        transport = "streamable-http" if declared_transport in {"http", "streamable-http"} else "sse"
        headers = self._headers(config)
        connection_id = uuid.uuid4().hex
        state = self._new_state(config, name, url, transport, headers, connection_id)
        prior_state = self.store.replace(name, state)
        if prior_state:
            self.effects.close_session(prior_state, "replace_connection")
        if transport == "streamable-http":
            self.effects.cleanup_stale_sessions(
                name,
                url,
                headers,
                str(state.get("mcp_protocol_version") or self.policy.streamable_protocol_version),
            )
        worker = (
            self.effects.streamable_http_worker
            if transport == "streamable-http"
            else self.effects.sse_worker
        )
        thread = threading.Thread(
            target=worker,
            args=(name, connection_id),
            daemon=True,
            name=f"ciel-runtime-channel-{transport}-{name}",
        )
        thread.start()
        return self.effects.public_status(name, state)

    def stop(self, name: str | None = None) -> dict[str, Any]:
        stopped, states_to_close = self.store.stop(name)
        for state in states_to_close:
            self.effects.close_session(state, "stop_connection")
        return {"stopped": stopped, "connections": self.effects.all_statuses()}
