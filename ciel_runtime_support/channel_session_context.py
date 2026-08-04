"""Durable streamable-HTTP channel session bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .channel_session_lifecycle import (
    ChannelSessionLifecycleServices,
    cleanup_stale_channel_sessions,
    delete_channel_session,
)
from .channel_session_repository import ChannelSessionRepository


@dataclass(frozen=True, slots=True)
class ChannelSessionConfigPorts:
    config_dir: Callable[[], Path]
    protocol_version: str
    log: Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ChannelSessionHttpPorts:
    streamable_headers: Callable[..., dict[str, str]]
    error_body: Callable[..., str]
    session_not_found: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChannelSessionStatePorts:
    connections: dict[str, dict[str, Any]]
    lock: Any


@dataclass(frozen=True, slots=True)
class ChannelSessionContext:
    config: ChannelSessionConfigPorts
    http: ChannelSessionHttpPorts
    state: ChannelSessionStatePorts

    def path(self) -> Path:
        return self.config.config_dir() / "channel-streamable-sessions.json"

    def repository(self) -> ChannelSessionRepository:
        return ChannelSessionRepository(
            path=self.path(),
            default_protocol_version=self.config.protocol_version,
            log=self.config.log,
        )

    def services(self) -> ChannelSessionLifecycleServices:
        return ChannelSessionLifecycleServices(
            streamable_headers=self.http.streamable_headers,
            http_error_body=self.http.error_body,
            session_not_found=self.http.session_not_found,
            records=self.records,
            forget=self.forget,
            log=self.config.log,
        )

    def records(self) -> list[dict[str, Any]]:
        return self.repository().records()

    def write(self, records: list[dict[str, Any]]) -> None:
        self.repository().write(records)

    def record(
        self,
        name: str,
        url: str,
        session_id: str | None,
        protocol_version: str,
    ) -> None:
        self.repository().record(name, url, session_id, protocol_version)

    def forget(self, name: str, url: str, session_id: str | None) -> None:
        del name, url
        self.repository().forget(session_id)

    def delete(
        self,
        name: str,
        endpoint: str,
        headers: dict[str, str],
        protocol_version: str,
        session_id: str | None,
        reason: str,
        *,
        timeout: float = 5.0,
    ) -> bool:
        return delete_channel_session(
            name,
            endpoint,
            headers,
            protocol_version,
            session_id,
            reason,
            self.services(),
            default_protocol_version=self.config.protocol_version,
            timeout=timeout,
        )

    def close_state(self, state: dict[str, Any], reason: str) -> bool:
        if str(state.get("transport") or "").strip().lower() not in {
            "http",
            "streamable-http",
        }:
            return True
        name = str(state.get("name") or "")
        endpoint = str(state.get("mcp_endpoint") or state.get("url") or "")
        session_id = str(state.get("mcp_session_id") or "").strip() or None
        headers = dict(state.get("headers") or {})
        protocol_version = str(
            state.get("mcp_protocol_version") or self.config.protocol_version
        )
        ok = self.delete(
            name,
            endpoint,
            headers,
            protocol_version,
            session_id,
            reason,
        )
        if ok:
            with self.state.lock:
                current = self.state.connections.get(name)
                same_session = current and str(
                    current.get("mcp_session_id") or ""
                ) == str(session_id or "")
                if current is state or same_session:
                    current["mcp_session_id"] = None
                    current["mcp_initialized"] = False
        return ok

    def cleanup_stale(
        self,
        name: str,
        url: str,
        headers: dict[str, str],
        protocol_version: str,
        *,
        keep_session_id: str | None = None,
    ) -> None:
        cleanup_stale_channel_sessions(
            name,
            url,
            headers,
            protocol_version,
            self.services(),
            default_protocol_version=self.config.protocol_version,
            keep_session_id=keep_session_id,
        )


@dataclass(frozen=True, slots=True)
class ChannelSessionCompatibilityApi:
    context: Callable[[], ChannelSessionContext]

    def path(self) -> Path:
        return self.context().path()

    def repository(self) -> ChannelSessionRepository:
        return self.context().repository()

    def services(self) -> ChannelSessionLifecycleServices:
        return self.context().services()

    def records(self) -> list[dict[str, Any]]:
        return self.context().records()

    def write(self, records: list[dict[str, Any]]) -> None:
        self.context().write(records)

    def record(
        self,
        name: str,
        url: str,
        session_id: str | None,
        protocol_version: str,
    ) -> None:
        self.context().record(name, url, session_id, protocol_version)

    def forget(self, name: str, url: str, session_id: str | None) -> None:
        self.context().forget(name, url, session_id)

    def delete(
        self,
        name: str,
        endpoint: str,
        headers: dict[str, str],
        protocol_version: str,
        session_id: str | None,
        reason: str,
        *,
        timeout: float = 5.0,
    ) -> bool:
        return self.context().delete(
            name,
            endpoint,
            headers,
            protocol_version,
            session_id,
            reason,
            timeout=timeout,
        )

    def close_state(self, state: dict[str, Any], reason: str) -> bool:
        return self.context().close_state(state, reason)

    def cleanup_stale(
        self,
        name: str,
        url: str,
        headers: dict[str, str],
        protocol_version: str,
        *,
        keep_session_id: str | None = None,
    ) -> None:
        self.context().cleanup_stale(
            name,
            url,
            headers,
            protocol_version,
            keep_session_id=keep_session_id,
        )


__all__ = [
    "ChannelSessionCompatibilityApi",
    "ChannelSessionConfigPorts",
    "ChannelSessionContext",
    "ChannelSessionHttpPorts",
    "ChannelSessionStatePorts",
]
