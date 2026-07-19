"""Channel MCP SSE and Streamable HTTP transport orchestration."""

from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ChannelMcpTransportConfig:
    version: str
    legacy_protocol: str
    streamable_protocol: str
    native_names: frozenset[str] | set[str] | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChannelMcpTransportState:
    connections: dict[str, dict[str, Any]]
    lock: Any


@dataclass(frozen=True, slots=True)
class ChannelMcpHttpPorts:
    legacy_post: Callable[..., Any]
    streamable_post: Callable[..., Any]
    error_body: Callable[[BaseException], str]
    session_not_found: Callable[[BaseException, str], bool]
    parse_bool: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class ChannelMcpEffects:
    set_state: Callable[..., Any]
    take_response: Callable[..., Any]
    mark_session_lost: Callable[..., Any]
    absolute_endpoint: Callable[..., str]
    record_session: Callable[..., Any]
    store_response: Callable[..., Any]
    project_payload: Callable[..., Any]
    append_message: Callable[..., Any]
    log: Callable[[str, str], None]


class ChannelMcpTransport:
    def __init__(
        self,
        config: ChannelMcpTransportConfig,
        state: ChannelMcpTransportState,
        http: ChannelMcpHttpPorts,
        effects: ChannelMcpEffects,
    ) -> None:
        self.config = config
        self.state = state
        self.http = http
        self.effects = effects

    def rpc_request(
        self,
        name: str,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        request_id = int(time.time_ns() % 9_000_000_000_000_000)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        for attempt in range(2):
            with self.state.lock:
                state = self.state.connections.get(name)
                if not state:
                    raise RuntimeError(f"SSE channel {name} is not connected")
                transport = str(state.get("transport") or "sse").strip().lower()
                if transport in {"http", "streamable-http"} and not state.get(
                    "mcp_initialized"
                ):
                    needs_http_initialize = True
                else:
                    needs_http_initialize = False
            if needs_http_initialize:
                self.initialize_streamable(name)
            with self.state.lock:
                state = self.state.connections.get(name)
                if not state:
                    raise RuntimeError(f"SSE channel {name} is not connected")
                if not state.get("mcp_initialized"):
                    raise RuntimeError(f"SSE channel {name} is not MCP initialized")
                endpoint = str(state.get("mcp_endpoint") or "")
                headers = dict(state.get("headers") or {})
                transport = str(state.get("transport") or "sse").strip().lower()
                protocol_version = str(
                    state.get("mcp_protocol_version") or self.config.legacy_protocol
                )
                session_id = str(state.get("mcp_session_id") or "").strip() or None
                requires_session = self.http.parse_bool(
                    state.get("streamable_requires_session"), True
                )
                effective_timeout = float(
                    timeout
                    if timeout is not None
                    else state.get("mcp_timeout_seconds") or 20.0
                )
            if not endpoint:
                raise RuntimeError(f"SSE channel {name} has no MCP endpoint")
            if transport in {"http", "streamable-http"}:
                if requires_session and not session_id:
                    raise RuntimeError(
                        f"Streamable HTTP MCP channel {name} has no Mcp-Session-Id"
                    )
                try:
                    posted, returned_session = self.http.streamable_post(
                        endpoint,
                        headers,
                        payload,
                        max(1.0, min(120.0, effective_timeout)),
                        protocol_version,
                        session_id,
                    )
                except urllib.error.HTTPError as exc:
                    body_text = self.http.error_body(exc)
                    if attempt == 0 and self.http.session_not_found(exc, body_text):
                        reason = (
                            f"streamable_http_session_not_found:HTTPError:{exc.code}"
                        )
                        self.effects.mark_session_lost(name, reason)
                        self.effects.log(
                            "WARN",
                            f"channel_http_mcp_session_lost name={name} method={method} error=HTTPError:{exc.code}:{exc.reason}",
                        )
                        continue
                    raise
                if returned_session:
                    self.effects.set_state(name, mcp_session_id=returned_session)
            else:
                posted = self.http.legacy_post(
                    endpoint, headers, payload, max(1.0, min(120.0, effective_timeout))
                )
            break
        else:
            raise RuntimeError(f"SSE channel {name} could not send MCP request")
        if (
            isinstance(posted, dict)
            and posted.get("id") == request_id
            and ("result" in posted or "error" in posted)
        ):
            return posted
        response = self.effects.take_response(
            name, request_id, max(1.0, min(120.0, effective_timeout))
        )
        if response is None:
            raise TimeoutError(
                f"timed out waiting for MCP SSE response id={request_id} method={method} channel={name}"
            )
        return response

    def maybe_initialize(self, name: str, endpoint_text: str) -> None:
        with self.state.lock:
            state = self.state.connections.get(name)
            if not state:
                return
            if not bool(state.get("mcp_enabled", True)):
                return
            stream_url = str(state.get("url") or "")
            endpoint = self.effects.absolute_endpoint(stream_url, endpoint_text)
            current_endpoint = str(state.get("mcp_endpoint") or "")
            was_initialized = bool(state.get("mcp_initialized"))
            if was_initialized and current_endpoint == endpoint:
                return
            headers = dict(state.get("headers") or {})
            timeout = max(
                5.0, min(120.0, float(state.get("mcp_timeout_seconds") or 20.0))
            )
            protocol_version = str(state.get("mcp_protocol_version") or "2024-11-05")
        try:
            if was_initialized and current_endpoint:
                self.effects.log(
                    "INFO",
                    f"channel_sse_mcp_reinitializing name={name} old_endpoint={current_endpoint} new_endpoint={endpoint}",
                )
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ciel-runtime-channel-bridge",
                        "version": self.config.version,
                    },
                },
            }
            self.http.legacy_post(endpoint, headers, initialize, timeout)
            initialized = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            self.http.legacy_post(endpoint, headers, initialized, timeout)
            self.effects.set_state(
                name,
                mcp_endpoint=endpoint,
                mcp_initialized=True,
                mcp_last_error=None,
                mcp_rpc_results={},
            )
            self.effects.log(
                "INFO", f"channel_sse_mcp_initialized name={name} endpoint={endpoint}"
            )
        except Exception as exc:
            self.effects.set_state(
                name,
                mcp_endpoint=endpoint,
                mcp_initialized=False,
                mcp_last_error=f"{type(exc).__name__}: {exc}",
            )
            self.effects.log(
                "WARN",
                f"channel_sse_mcp_initialize_failed name={name} endpoint={endpoint} error={type(exc).__name__}: {exc}",
            )

    def initialize_streamable(self, name: str) -> None:
        with self.state.lock:
            state = self.state.connections.get(name)
            if not state:
                return
            if not bool(state.get("mcp_enabled", True)):
                return
            endpoint = str(state.get("url") or "")
            if (
                bool(state.get("mcp_initialized"))
                and str(state.get("mcp_endpoint") or "") == endpoint
            ):
                return
            headers = dict(state.get("headers") or {})
            timeout = max(
                5.0, min(120.0, float(state.get("mcp_timeout_seconds") or 20.0))
            )
            protocol_version = str(
                state.get("mcp_protocol_version") or self.config.streamable_protocol
            )
            requires_session = self.http.parse_bool(
                state.get("streamable_requires_session"), True
            )
        try:
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ciel-runtime-channel-bridge",
                        "version": self.config.version,
                    },
                },
            }
            _result, session_id = self.http.streamable_post(
                endpoint, headers, initialize, timeout, protocol_version
            )
            if requires_session and not session_id:
                self.effects.set_state(
                    name,
                    mcp_endpoint=endpoint,
                    mcp_initialized=False,
                    mcp_session_id=None,
                    mcp_last_error="streamable_http_missing_session_id",
                )
                self.effects.log(
                    "WARN",
                    f"channel_http_mcp_initialize_failed name={name} endpoint={endpoint} error=missing_mcp_session_id",
                )
                return
            initialized = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            self.http.streamable_post(
                endpoint, headers, initialized, timeout, protocol_version, session_id
            )
            self.effects.set_state(
                name,
                mcp_endpoint=endpoint,
                mcp_initialized=True,
                mcp_session_id=session_id,
                mcp_last_error=None,
                mcp_rpc_results={},
            )
            self.effects.record_session(name, endpoint, session_id, protocol_version)
            visible_session = session_id or "-"
            self.effects.log(
                "INFO",
                f"channel_http_mcp_initialized name={name} endpoint={endpoint} session={visible_session}",
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 405:
                self.effects.set_state(
                    name,
                    transport="sse",
                    mcp_endpoint="",
                    mcp_initialized=False,
                    mcp_session_id=None,
                    mcp_protocol_version=self.config.legacy_protocol,
                    mcp_last_error="streamable_http_405_fallback_sse",
                )
                self.effects.log(
                    "WARN",
                    f"channel_http_fallback_sse name={name} endpoint={endpoint} reason=HTTPError:405",
                )
                return
            self.effects.set_state(
                name,
                mcp_endpoint=endpoint,
                mcp_initialized=False,
                mcp_last_error=f"HTTPError: {exc.code} {exc.reason}",
            )
            self.effects.log(
                "WARN",
                f"channel_http_mcp_initialize_failed name={name} endpoint={endpoint} error=HTTPError:{exc.code}: {exc.reason}",
            )
        except Exception as exc:
            self.effects.set_state(
                name,
                mcp_endpoint=endpoint,
                mcp_initialized=False,
                mcp_last_error=f"{type(exc).__name__}: {exc}",
            )
            self.effects.log(
                "WARN",
                f"channel_http_mcp_initialize_failed name={name} endpoint={endpoint} error={type(exc).__name__}: {exc}",
            )

    def dispatch(
        self,
        name: str,
        event_name: str,
        data_lines: list[str],
        event_id: str | None = None,
    ) -> None:
        data_text = "\n".join(data_lines)
        if event_id is not None:
            with self.state.lock:
                state = self.state.connections.get(name)
                if state:
                    state["last_sse_event_id"] = str(event_id)
        if (event_name or "").strip().lower() == "endpoint":
            self.maybe_initialize(name, data_text)
            return
        if self.effects.store_response(name, data_text):
            return
        if str(name or "").strip().lower() in self.config.native_names:
            self.effects.log(
                "INFO",
                f"channel_sse_message_ignored name={name} event={event_name or 'message'} reason=native_router_self_echo",
            )
            return
        with self.state.lock:
            state = self.state.connections.get(name)
            if not state:
                return
            defaults = dict(state)
        payload = self.effects.project_payload(
            data_text, event_name, defaults, event_id=event_id
        )
        if not payload:
            return
        saved = self.effects.append_message(payload)
        if saved.get("_ciel_runtime_duplicate"):
            self.effects.log(
                "INFO",
                f"channel_sse_message_skipped_duplicate name={name} event={event_name or 'message'} existing_id={saved.get('id')} channel={saved.get('channel')}",
            )
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self.state.lock:
            state = self.state.connections.get(name)
            if state:
                state["last_event_at"] = now
                state["messages_received"] = (
                    int(state.get("messages_received") or 0) + 1
                )
                state["last_error"] = None
        self.effects.log(
            "INFO",
            f"channel_sse_message_received name={name} event={event_name or 'message'} message_id={saved.get('id')} channel={saved.get('channel')}",
        )
