"""Channel connection registry, worker, and lifecycle bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .channel_connection_lifecycle import (
    ChannelConnectionLifecycle,
    ChannelConnectionLifecycleEffects,
    ChannelConnectionLifecyclePolicy,
    ChannelConnectionLifecycleStore,
)
from .channel_connection_registry import ChannelConnectionRegistry
from .channel_connection_worker import (
    ChannelConnectionWorker,
    ChannelWorkerEffects,
    ChannelWorkerPolicy,
    ChannelWorkerStateStore,
)


@dataclass(frozen=True, slots=True)
class ChannelConnectionStatePorts:
    connections: dict[str, dict[str, Any]]
    lock: Any
    rpc_condition: Any


@dataclass(frozen=True, slots=True)
class ChannelConnectionWorkerPorts:
    log: Callable[[str, str], None]
    dispatch: Callable[..., Any]
    initialize_streamable: Callable[[str], None]
    close_state_session: Callable[[dict[str, Any], str], bool]
    streamable_headers: Callable[..., dict[str, str]]
    session_not_found: Callable[..., bool]
    http_error_body: Callable[..., str]


@dataclass(frozen=True, slots=True)
class ChannelConnectionLifecyclePorts:
    safe_segment: Callable[[Any], str]
    close_session: Callable[[dict[str, Any], str], bool]
    cleanup_stale_sessions: Callable[..., None]
    parse_bool: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChannelConnectionProtocol:
    streamable_version: str
    legacy_sse_version: str


@dataclass(frozen=True, slots=True)
class ChannelConnectionContext:
    state: ChannelConnectionStatePorts
    worker_ports: ChannelConnectionWorkerPorts
    lifecycle_ports: ChannelConnectionLifecyclePorts
    protocol: ChannelConnectionProtocol

    def registry(self) -> ChannelConnectionRegistry:
        return ChannelConnectionRegistry(
            states=self.state.connections,
            lock=self.state.lock,
            rpc_condition=self.state.rpc_condition,
            log=self.worker_ports.log,
        )

    def statuses(self) -> dict[str, Any]:
        return self.registry().statuses()

    def update(self, name: str, **updates: Any) -> None:
        self.registry().update(name, **updates)

    def mark_session_lost(self, name: str, reason: str) -> None:
        self.registry().mark_session_lost(name, reason)

    def store_rpc_response(self, name: str, data_text: str) -> bool:
        return self.registry().store_rpc_response(name, data_text)

    def take_rpc_response(
        self, name: str, rpc_id: Any, timeout: float
    ) -> dict[str, Any] | None:
        return self.registry().take_rpc_response(name, rpc_id, timeout)

    def state_name_for_mcp_server(self, server_name: str) -> str | None:
        return self.registry().state_name_for_mcp_server(server_name)

    def connection_matches(
        self, state: dict[str, Any], connection_id: str | None
    ) -> bool:
        if not connection_id:
            return True
        return str(state.get("connection_id") or "") == str(connection_id)

    def worker_running(self, name: str, connection_id: str | None) -> bool:
        with self.state.lock:
            state = self.state.connections.get(name)
            return bool(
                state
                and state.get("running")
                and self.connection_matches(state, connection_id)
            )

    def worker(self) -> ChannelConnectionWorker:
        ports = self.worker_ports
        return ChannelConnectionWorker(
            state_store=ChannelWorkerStateStore(
                self.state.connections, self.state.lock
            ),
            effects=ChannelWorkerEffects(
                log=ports.log,
                dispatch=ports.dispatch,
                set_state=self.update,
                initialize_streamable=ports.initialize_streamable,
                close_state_session=ports.close_state_session,
                streamable_headers=ports.streamable_headers,
                session_not_found=ports.session_not_found,
                http_error_body=ports.http_error_body,
            ),
            policy=ChannelWorkerPolicy(
                streamable_protocol_version=self.protocol.streamable_version,
                legacy_sse_protocol_version=self.protocol.legacy_sse_version,
                parse_bool=self.lifecycle_ports.parse_bool,
            ),
        )

    def run_sse_worker(
        self, name: str, connection_id: str | None = None
    ) -> None:
        self.worker().run_sse(name, connection_id)

    def run_streamable_http_worker(
        self, name: str, connection_id: str | None = None
    ) -> None:
        self.worker().run_streamable_http(name, connection_id)

    def lifecycle(self) -> ChannelConnectionLifecycle:
        ports = self.lifecycle_ports
        return ChannelConnectionLifecycle(
            store=ChannelConnectionLifecycleStore(
                self.state.connections, self.state.lock
            ),
            effects=ChannelConnectionLifecycleEffects(
                safe_segment=ports.safe_segment,
                close_session=ports.close_session,
                cleanup_stale_sessions=ports.cleanup_stale_sessions,
                public_status=ChannelConnectionRegistry.public_status,
                all_statuses=self.statuses,
                sse_worker=self.run_sse_worker,
                streamable_http_worker=self.run_streamable_http_worker,
            ),
            policy=ChannelConnectionLifecyclePolicy(
                streamable_protocol_version=self.protocol.streamable_version,
                legacy_sse_protocol_version=self.protocol.legacy_sse_version,
                parse_bool=ports.parse_bool,
            ),
        )

    def start(self, config: dict[str, Any]) -> dict[str, Any]:
        return self.lifecycle().start(config)

    def stop(self, name: str | None = None) -> dict[str, Any]:
        return self.lifecycle().stop(name)


@dataclass(frozen=True, slots=True)
class ChannelConnectionCompatibilityApi:
    context: Callable[[], ChannelConnectionContext]

    def registry(self) -> ChannelConnectionRegistry:
        return self.context().registry()

    def statuses(self) -> dict[str, Any]:
        return self.context().statuses()

    def update(self, name: str, **updates: Any) -> None:
        self.context().update(name, **updates)

    def mark_session_lost(self, name: str, reason: str) -> None:
        self.context().mark_session_lost(name, reason)

    def store_rpc_response(self, name: str, data_text: str) -> bool:
        return self.context().store_rpc_response(name, data_text)

    def take_rpc_response(
        self, name: str, rpc_id: Any, timeout: float
    ) -> dict[str, Any] | None:
        return self.context().take_rpc_response(name, rpc_id, timeout)

    def state_name_for_mcp_server(self, server_name: str) -> str | None:
        return self.context().state_name_for_mcp_server(server_name)

    def connection_matches(
        self, state: dict[str, Any], connection_id: str | None
    ) -> bool:
        return self.context().connection_matches(state, connection_id)

    def worker_running(self, name: str, connection_id: str | None) -> bool:
        return self.context().worker_running(name, connection_id)

    def worker(self) -> ChannelConnectionWorker:
        return self.context().worker()

    def run_sse_worker(
        self, name: str, connection_id: str | None = None
    ) -> None:
        self.context().run_sse_worker(name, connection_id)

    def run_streamable_http_worker(
        self, name: str, connection_id: str | None = None
    ) -> None:
        self.context().run_streamable_http_worker(name, connection_id)

    def lifecycle(self) -> ChannelConnectionLifecycle:
        return self.context().lifecycle()

    def start(self, config: dict[str, Any]) -> dict[str, Any]:
        return self.context().start(config)

    def stop(self, name: str | None = None) -> dict[str, Any]:
        return self.context().stop(name)


__all__ = [
    "ChannelConnectionCompatibilityApi",
    "ChannelConnectionContext",
    "ChannelConnectionLifecyclePorts",
    "ChannelConnectionProtocol",
    "ChannelConnectionStatePorts",
    "ChannelConnectionWorkerPorts",
]
