"""Network workers for SSE and MCP Streamable HTTP channel connections."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from ciel_runtime_support.sse_stream import SseRetryState, SseStreamServices, consume_sse_stream


@dataclass(frozen=True, slots=True)
class ChannelWorkerEffects:
    log: Callable[[str, str], None]
    dispatch: Callable[[str, str, list[str], str | None], None]
    set_state: Callable[..., None]
    initialize_streamable: Callable[[str], None]
    close_state_session: Callable[[dict[str, Any], str], None]
    streamable_headers: Callable[[dict[str, str], str, str | None, str], dict[str, str]]
    session_not_found: Callable[[urllib.error.HTTPError, str], bool]
    http_error_body: Callable[[urllib.error.HTTPError], str]


@dataclass(frozen=True, slots=True)
class ChannelWorkerPolicy:
    streamable_protocol_version: str
    legacy_sse_protocol_version: str
    parse_bool: Callable[[Any, bool], bool]


@dataclass(frozen=True, slots=True)
class ChannelWorkerStateStore:
    states: dict[str, dict[str, Any]]
    lock: Lock

    @staticmethod
    def _matches(state: dict[str, Any], connection_id: str | None) -> bool:
        return not connection_id or str(state.get("connection_id") or "") == str(connection_id)

    def snapshot(self, name: str, connection_id: str | None) -> dict[str, Any] | None:
        with self.lock:
            state = self.states.get(name)
            if not state or not state.get("running") or not self._matches(state, connection_id):
                return None
            return dict(state)

    def running(self, name: str, connection_id: str | None) -> bool:
        return self.snapshot(name, connection_id) is not None

    def record_reconnect(
        self,
        name: str,
        connection_id: str | None,
        error: str,
    ) -> str | None:
        with self.lock:
            state = self.states.get(name)
            if not state or not state.get("running") or not self._matches(state, connection_id):
                return None
            state["last_error"] = error
            state["sse_reconnects"] = int(state.get("sse_reconnects") or 0) + 1
            event_id = state.get("last_sse_event_id")
            return str(event_id) if event_id is not None and str(event_id) != "" else "-"

    def state_after_initialize(
        self,
        name: str,
        connection_id: str | None,
        parse_bool: Callable[[Any, bool], bool],
    ) -> tuple[str, str | None] | None:
        with self.lock:
            state = self.states.get(name)
            if not state or not state.get("running") or not self._matches(state, connection_id):
                return None
            if str(state.get("transport") or "").strip().lower() == "sse":
                return "sse", None
            if not state.get("mcp_initialized"):
                return "retry", None
            session_id = str(state.get("mcp_session_id") or "").strip() or None
            if parse_bool(state.get("streamable_requires_session"), True) and not session_id:
                state["mcp_initialized"] = False
                state["mcp_last_error"] = "streamable_http_missing_session_id"
                return "retry", None
            return "ready", session_id

    def record_http_error(
        self,
        name: str,
        connection_id: str | None,
        error: urllib.error.HTTPError,
        session_not_found: bool,
        legacy_protocol_version: str,
    ) -> tuple[str, str] | None:
        with self.lock:
            state = self.states.get(name)
            if not state or not state.get("running") or not self._matches(state, connection_id):
                return None
            if error.code == 405:
                state.update(
                    transport="sse",
                    mcp_protocol_version=legacy_protocol_version,
                    mcp_initialized=False,
                    mcp_session_id=None,
                    last_error="streamable_http_405_fallback_sse",
                )
                return "sse", "-"
            if session_not_found:
                state.update(
                    mcp_initialized=False,
                    mcp_session_id=None,
                    mcp_last_error=f"streamable_http_session_not_found:HTTPError:{error.code}",
                )
            state["last_error"] = f"HTTPError: {error.code} {error.reason}"
            state["sse_reconnects"] = int(state.get("sse_reconnects") or 0) + 1
            event_id = state.get("last_sse_event_id")
            resumed = str(event_id) if event_id is not None and str(event_id) != "" else "-"
            return "retry", resumed

    def state_for_close(self, name: str, connection_id: str | None) -> dict[str, Any] | None:
        with self.lock:
            state = self.states.get(name)
            return dict(state) if state and self._matches(state, connection_id) else None


@dataclass(frozen=True, slots=True)
class ChannelConnectionWorker:
    state_store: ChannelWorkerStateStore
    effects: ChannelWorkerEffects
    policy: ChannelWorkerPolicy

    @staticmethod
    def _event_id(value: Any) -> str:
        return str(value) if value is not None and str(value) != "" else "-"

    def run_sse(self, name: str, connection_id: str | None = None) -> None:
        while snapshot := self.state_store.snapshot(name, connection_id):
            url = str(snapshot.get("url") or "")
            headers = dict(snapshot.get("headers") or {})
            last_event_id = snapshot.get("last_sse_event_id")
            read_timeout = max(5.0, min(3600.0, float(snapshot.get("read_timeout_seconds") or 300.0)))
            retry = SseRetryState(max(1.0, min(60.0, float(snapshot.get("retry_seconds") or 5.0))))
            try:
                request_headers = {**headers, "Accept": "text/event-stream"}
                if last_event_id is not None and str(last_event_id) != "":
                    request_headers["Last-Event-ID"] = str(last_event_id)
                request = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(request, timeout=read_timeout) as response:
                    self.effects.set_state(name, last_error=None)
                    self.effects.log(
                        "INFO",
                        f"channel_sse_connected name={name} url={url} last_event_id={self._event_id(last_event_id)}",
                    )
                    consume_sse_stream(
                        response,
                        retry,
                        "SSE stream ended",
                        SseStreamServices(
                            should_continue=lambda: self.state_store.running(name, connection_id),
                            dispatch=lambda event, data, event_id: self.effects.dispatch(
                                name, event, data, event_id
                            ),
                            invalid_retry=lambda value: self.effects.log(
                                "WARN", f"channel_sse_invalid_retry name={name} value={value!r}"
                            ),
                        ),
                    )
            except Exception as error:
                resumed = self.state_store.record_reconnect(
                    name, connection_id, f"{type(error).__name__}: {error}"
                )
                if resumed is None:
                    return
                self.effects.log(
                    "WARN",
                    f"channel_sse_reconnect name={name} last_event_id={resumed} error={type(error).__name__}: {error}",
                )
                time.sleep(retry.seconds)

    def run_streamable_http(self, name: str, connection_id: str | None = None) -> None:
        switch_to_sse = False
        try:
            while snapshot := self.state_store.snapshot(name, connection_id):
                url = str(snapshot.get("url") or "")
                headers = dict(snapshot.get("headers") or {})
                protocol_version = str(
                    snapshot.get("mcp_protocol_version") or self.policy.streamable_protocol_version
                )
                session_id = str(snapshot.get("mcp_session_id") or "").strip() or None
                requires_session = self.policy.parse_bool(snapshot.get("streamable_requires_session"), True)
                needs_initialize = not snapshot.get("mcp_initialized") or (requires_session and not session_id)
                last_event_id = snapshot.get("last_sse_event_id")
                read_timeout = max(5.0, min(3600.0, float(snapshot.get("read_timeout_seconds") or 300.0)))
                retry = SseRetryState(max(1.0, min(60.0, float(snapshot.get("retry_seconds") or 5.0))))
                if needs_initialize:
                    self.effects.initialize_streamable(name)
                    initialized = self.state_store.state_after_initialize(
                        name, connection_id, self.policy.parse_bool
                    )
                    if initialized is None:
                        return
                    outcome, session_id = initialized
                    if outcome == "sse":
                        self.effects.log("INFO", f"channel_http_worker_switching_to_sse name={name}")
                        switch_to_sse = True
                        break
                    if outcome == "retry":
                        time.sleep(retry.seconds)
                        continue
                try:
                    request_headers = self.effects.streamable_headers(
                        headers, protocol_version, session_id, "text/event-stream"
                    )
                    if last_event_id is not None and str(last_event_id) != "":
                        request_headers["Last-Event-ID"] = str(last_event_id)
                    request = urllib.request.Request(url, headers=request_headers, method="GET")
                    with urllib.request.urlopen(request, timeout=read_timeout) as response:
                        self.effects.set_state(name, last_error=None)
                        self.effects.log(
                            "INFO",
                            f"channel_http_connected name={name} url={url} session={session_id or '-'} "
                            f"last_event_id={self._event_id(last_event_id)}",
                        )
                        consume_sse_stream(
                            response,
                            retry,
                            "Streamable HTTP SSE stream ended",
                            SseStreamServices(
                                should_continue=lambda: self.state_store.running(name, connection_id),
                                dispatch=lambda event, data, event_id: self.effects.dispatch(
                                    name, event, data, event_id
                                ),
                                invalid_retry=lambda value: self.effects.log(
                                    "WARN", f"channel_http_invalid_retry name={name} value={value!r}"
                                ),
                            ),
                        )
                except urllib.error.HTTPError as error:
                    body_text = self.effects.http_error_body(error)
                    session_lost = self.effects.session_not_found(error, body_text)
                    outcome = self.state_store.record_http_error(
                        name,
                        connection_id,
                        error,
                        session_lost,
                        self.policy.legacy_sse_protocol_version,
                    )
                    if outcome is None:
                        return
                    action, resumed = outcome
                    if action == "sse":
                        self.effects.log(
                            "WARN",
                            f"channel_http_fallback_sse name={name} url={url} reason=HTTPError:405",
                        )
                        switch_to_sse = True
                        break
                    event = "session_lost_reinitialize" if session_lost else "reconnect"
                    self.effects.log(
                        "WARN",
                        f"channel_http_{event} name={name} last_event_id={resumed} "
                        f"error=HTTPError:{error.code}:{error.reason}",
                    )
                    time.sleep(retry.seconds)
                except Exception as error:
                    resumed = self.state_store.record_reconnect(
                        name, connection_id, f"{type(error).__name__}: {error}"
                    )
                    if resumed is None:
                        return
                    self.effects.log(
                        "WARN",
                        f"channel_http_reconnect name={name} last_event_id={resumed} "
                        f"error={type(error).__name__}: {error}",
                    )
                    time.sleep(retry.seconds)
        finally:
            state = self.state_store.state_for_close(name, connection_id)
            if state and not switch_to_sse:
                self.effects.close_state_session(state, "worker_exit")
        if switch_to_sse:
            self.run_sse(name, connection_id)
