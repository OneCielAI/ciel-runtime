"""Streamable HTTP MCP proxy application service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request


@dataclass(frozen=True, slots=True)
class McpHttpProxyCodec:
    compact_tool_result_response: Callable[..., Any]
    drain_input_messages: Callable[..., Any]
    error_response: Callable[..., Any]
    notification_payload: Callable[..., Any]
    notification_wait_response: Callable[..., Any]
    observe_json_message: Callable[..., Any]
    tool_call_arguments: Callable[..., Any]
    tool_call_name: Callable[..., Any]
    tool_is_notification_wait: Callable[..., Any]
    wait_timeout_seconds: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class McpHttpProxyTransport:
    http_error_body_text: Callable[..., Any]
    session_not_found: Callable[..., Any]
    stream_read_timeout_error: Callable[..., Any]
    streamable_headers: Callable[..., Any]
    streamable_http_request: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class McpHttpProxyRuntime:
    default_protocol_version: str
    disable_notification_stream: Callable[..., Any]
    is_streamable_http: Callable[..., Any]
    json_safe_metadata: Callable[..., Any]
    log: Callable[..., Any]
    parse_bool: Callable[..., Any]
    server_runtime_headers: Callable[..., Any]
    write_json_response: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class McpHttpProxyServices:
    codec: McpHttpProxyCodec
    transport: McpHttpProxyTransport
    runtime: McpHttpProxyRuntime


def run_mcp_streamable_http_proxy(
    server_name: str,
    server_config_path: Path,
    *,
    services: McpHttpProxyServices,
) -> int:
    codec = services.codec
    transport = services.transport
    runtime = services.runtime
    MCP_STREAMABLE_HTTP_PROTOCOL_VERSION = runtime.default_protocol_version
    _http_error_body_text = transport.http_error_body_text
    _json_safe_metadata = runtime.json_safe_metadata
    _mcp_proxy_compact_tool_result_response = codec.compact_tool_result_response
    _mcp_proxy_drain_input_messages = codec.drain_input_messages
    _mcp_proxy_error_response = codec.error_response
    _mcp_proxy_notification_payload = codec.notification_payload
    _mcp_proxy_notification_wait_response = codec.notification_wait_response
    _mcp_proxy_observe_json_message = codec.observe_json_message
    _mcp_proxy_streamable_http_request = transport.streamable_http_request
    _mcp_proxy_tool_call_arguments = codec.tool_call_arguments
    _mcp_proxy_tool_call_name = codec.tool_call_name
    _mcp_proxy_tool_is_notification_wait = codec.tool_is_notification_wait
    _mcp_proxy_wait_timeout_seconds = codec.wait_timeout_seconds
    _mcp_proxy_write_json_response = runtime.write_json_response
    _mcp_server_disable_proxy_notification_stream = runtime.disable_notification_stream
    _mcp_server_is_streamable_http = runtime.is_streamable_http
    _mcp_stream_read_timeout_error = transport.stream_read_timeout_error
    _mcp_streamable_headers = transport.streamable_headers
    _streamable_http_session_not_found = transport.session_not_found
    mcp_server_runtime_headers = runtime.server_runtime_headers
    parse_bool = runtime.parse_bool
    router_log = runtime.log
    try:
        server = json.loads(server_config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        router_log("ERROR", f"mcp_http_proxy_config_read_failed server={server_name} error={type(exc).__name__}: {exc}")
        print(f"ciel-runtime mcp-proxy: cannot read server config: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    if not isinstance(server, dict) or not _mcp_server_is_streamable_http(server):
        router_log("ERROR", f"mcp_http_proxy_invalid_config server={server_name}")
        print("ciel-runtime mcp-proxy: server config is not a Streamable HTTP MCP server", file=sys.stderr, flush=True)
        return 2
    endpoint = str(server.get("url") or server.get("endpoint") or "").strip()
    headers = mcp_server_runtime_headers(server)
    protocol_version = str(server.get("mcp_protocol_version") or server.get("protocolVersion") or server.get("protocol_version") or MCP_STREAMABLE_HTTP_PROTOCOL_VERSION)
    timeout = max(5.0, min(120.0, float(server.get("mcp_timeout_seconds") or server.get("timeout") or 20.0)))
    requires_session = parse_bool(server.get("streamable_requires_session", server.get("require_session", server.get("mcp_session_required", True))), True)
    notification_stream_enabled = not _mcp_server_disable_proxy_notification_stream(server)
    session_id: str | None = None
    initialize_payload: dict[str, Any] | None = None
    initialized_payload: dict[str, Any] | None = None
    session_lock = threading.Lock()
    stream_stop = threading.Event()
    # Single-owner notification-stream lifecycle. ONE manager thread owns the
    # backend session and the notification GET stream. It initializes the
    # session, streams notifications, and on session loss re-initializes IN THE
    # SAME thread -- so a second stream can never exist (no leak) and the stream
    # never stays dead while Claude Code is idle (no zombie). The stdin loop
    # NEVER initializes a session or opens a stream; when a tool call needs a
    # session it asks the manager via session_cond and waits briefly.
    session_cond = threading.Condition(session_lock)
    session_requested = False
    stream_reopen_requested = False
    initialize_result: dict[str, Any] | None = None
    manager_thread: threading.Thread | None = None
    pending_notifications: list[dict[str, Any]] = []
    pending_wait_count = 0
    initialized_wait_seconds = max(
        0.0,
        min(5.0, float(server.get("initialized_wait_seconds") or server.get("mcp_initialized_wait_seconds") or 1.0)),
    )
    notification_condition = threading.Condition()
    router_log("INFO", f"mcp_http_proxy_started server={server_name} endpoint={endpoint}")

    def queue_proxy_notification(payload: dict[str, Any], saved: dict[str, Any] | None) -> None:
        queued = _json_safe_metadata(payload)
        if saved and saved.get("id") is not None:
            queued["ciel_runtime_message_id"] = saved.get("id")
        with notification_condition:
            pending_notifications.append(queued)
            if len(pending_notifications) > 200:
                del pending_notifications[:-200]
            notification_condition.notify_all()

    def has_pending_notification_wait() -> bool:
        with notification_condition:
            return pending_wait_count > 0

    def wait_for_proxy_notifications(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal pending_wait_count
        request_id = payload.get("id")
        timeout_seconds = _mcp_proxy_wait_timeout_seconds(_mcp_proxy_tool_call_arguments(payload))
        deadline = time.time() + timeout_seconds
        with notification_condition:
            pending_wait_count += 1
            try:
                while not pending_notifications:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    notification_condition.wait(timeout=min(1.0, remaining))
                notifications = list(pending_notifications)
                pending_notifications.clear()
            finally:
                pending_wait_count -= 1
        timed_out = not notifications
        router_log(
            "INFO",
            f"mcp_http_proxy_wait_resolved server={server_name} request_id={request_id} count={len(notifications)} timed_out={timed_out}",
        )
        return _mcp_proxy_notification_wait_response(request_id, server_name, notifications, timed_out=timed_out)

    def emit_streamable_sse_message(data_lines: list[str]) -> None:
        if not data_lines:
            return
        data_text = "\n".join(data_lines).strip()
        if not data_text:
            return
        try:
            payload = json.loads(data_text)
        except Exception as exc:
            router_log("WARN", f"mcp_http_proxy_stream_json_failed server={server_name} error={type(exc).__name__}")
            return
        if not isinstance(payload, dict):
            return
        if _mcp_proxy_notification_payload(server_name, payload):
            saved = _mcp_proxy_observe_json_message(server_name, payload, schedule_direct=False)
            if saved:
                queue_proxy_notification(payload, saved)
                pending_wait = has_pending_notification_wait()
                router_log(
                    "INFO",
                    f"mcp_http_proxy_notification_queued server={server_name} message_id={saved.get('id')} pending_wait={pending_wait}",
                )
            return
        _mcp_proxy_observe_json_message(server_name, payload)
        _mcp_proxy_write_json_response(payload)

    def current_session_id() -> str | None:
        with session_lock:
            return session_id

    def request_session_and_wait(timeout_s: float) -> str | None:
        """Ask the manager for a session and wait briefly. Never inits here.

        The stdin loop calls this when a tool call needs a session but none is
        active. It only signals the manager (which solely owns session creation)
        and waits; it never POSTs initialize or opens a stream itself, so two
        owners can never exist.
        """
        nonlocal session_requested
        with session_cond:
            if session_id is not None:
                return session_id
            session_requested = True
            session_cond.notify_all()
            deadline = time.time() + timeout_s
            while session_id is None and not stream_stop.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                session_cond.wait(timeout=min(1.0, remaining))
            return session_id

    def manager_initialize_locked() -> str | None:
        """Re-establish the backend session. Returns the new session id or None.

        Called ONLY by the manager thread, so session_id has a single writer.
        Publishes the initialize result (for the stdin initialize reply) and the
        new session id under session_cond, waking any waiter.
        """
        nonlocal session_id, initialize_result
        init_payload = initialize_payload
        if not init_payload:
            return None
        try:
            result, returned_session = _mcp_proxy_streamable_http_request(
                endpoint, headers, init_payload, timeout, protocol_version, None,
            )
        except Exception as exc:
            router_log("WARN", f"mcp_http_proxy_session_init_failed server={server_name} error={type(exc).__name__}: {exc}")
            return None
        if isinstance(result, dict):
            _mcp_proxy_observe_json_message(server_name, result)
        with session_cond:
            session_id = returned_session or session_id
            if isinstance(result, dict):
                initialize_result = result
            new_session = session_id
            session_cond.notify_all()
        if new_session and initialized_payload:
            try:
                _mcp_proxy_streamable_http_request(
                    endpoint, headers, initialized_payload, timeout, protocol_version, new_session,
                )
            except Exception as exc:
                router_log(
                    "WARN",
                    f"mcp_http_proxy_initialized_notification_failed server={server_name} "
                    f"error={type(exc).__name__}: {exc}",
                )
        if new_session:
            router_log("INFO", f"mcp_http_proxy_session_initialized server={server_name} session={new_session}")
        return new_session

    def stream_manager() -> None:
        """Single owner of the backend session and notification GET stream.

        One thread for the whole proxy lifetime. It (re)initializes the session
        when missing or requested, then runs the GET event-stream; on any
        session loss / reconnect it loops back and re-initializes here, so there
        is never a second stream worker and the stream never stays dead while
        Claude Code is idle.
        """
        nonlocal session_requested, session_id, stream_reopen_requested
        last_event_id: str | None = None
        retry_seconds = max(1.0, min(60.0, float(server.get("retry_seconds") or 5.0)))
        read_timeout = max(
            5.0,
            min(
                3600.0,
                float(
                    server.get("notification_read_timeout_seconds")
                    or server.get("stream_read_timeout_seconds")
                    or server.get("read_timeout_seconds")
                    or server.get("stream_timeout")
                    or 60.0
                ),
            ),
        )
        pre_initialized_read_timeout = max(
            0.2,
            min(5.0, float(server.get("pre_initialized_read_timeout_seconds") or 0.5)),
        )
        while not stream_stop.is_set():
            with session_cond:
                current_session = session_id
                requested = session_requested
                reopen_requested = stream_reopen_requested
                if reopen_requested:
                    stream_reopen_requested = False
            # (Re)initialize when we have no session, or a tool call requested one.
            if current_session is None or requested:
                with session_cond:
                    session_requested = False
                current_session = manager_initialize_locked()
                if current_session is None:
                    # Init failed; back off and retry. Wake early on shutdown.
                    with session_cond:
                        if not stream_stop.is_set():
                            session_cond.wait(timeout=retry_seconds)
                    continue
                last_event_id = None
            if not notification_stream_enabled:
                # No stream to own; just idle until shutdown or a session request.
                with session_cond:
                    if not stream_stop.is_set() and not session_requested:
                        session_cond.wait(timeout=read_timeout)
                continue
            if initialized_payload is None and initialized_wait_seconds > 0:
                # MCP clients send notifications/initialized immediately after
                # initialize. Opening the Streamable HTTP GET before that point
                # can leave some stateful servers with a live but unsubscribed
                # notification stream. Wait briefly for the standard handshake;
                # if an older/nonstandard client never sends it, keep the old
                # behavior after the grace window.
                with session_cond:
                    if initialized_payload is None and not stream_stop.is_set():
                        session_cond.wait(timeout=initialized_wait_seconds)
                    if session_requested:
                        continue
            worker_session = current_session
            event_name = "message"
            data_lines: list[str] = []
            try:
                request_headers = _mcp_streamable_headers(
                    headers, protocol_version, worker_session, accept="text/event-stream",
                )
                if last_event_id:
                    request_headers["Last-Event-ID"] = last_event_id
                req = urllib.request.Request(endpoint, headers=request_headers, method="GET")
                with session_cond:
                    stream_read_timeout = read_timeout if initialized_payload is not None else min(read_timeout, pre_initialized_read_timeout)
                with urllib.request.urlopen(req, timeout=stream_read_timeout) as response:
                    router_log("INFO", f"mcp_http_proxy_stream_connected server={server_name} session={worker_session} last_event_id={last_event_id or '-'}")
                    while not stream_stop.is_set():
                        with session_cond:
                            if session_requested or stream_reopen_requested or session_id != worker_session:
                                break
                        raw = response.readline()
                        if raw == b"":
                            raise ConnectionError("Streamable HTTP MCP notification stream ended")
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        if not line:
                            emit_streamable_sse_message(data_lines)
                            data_lines = []
                            event_name = "message"
                            continue
                        if line.startswith(":"):
                            continue
                        field, _, value = line.partition(":")
                        if value.startswith(" "):
                            value = value[1:]
                        if field == "event":
                            event_name = value or "message"
                        elif field == "data":
                            data_lines.append(value)
                        elif field == "id":
                            last_event_id = value
                        elif field == "retry":
                            try:
                                retry_seconds = max(1.0, min(60.0, int(value) / 1000.0))
                            except (TypeError, ValueError) as exc:
                                router_log(
                                    "WARN",
                                    f"mcp_http_proxy_invalid_retry server={server_name} value={value!r} "
                                    f"error={type(exc).__name__}: {exc}",
                                )
                continue
            except urllib.error.HTTPError as exc:
                body_text = _http_error_body_text(exc)
                if _streamable_http_session_not_found(exc, body_text):
                    with session_cond:
                        if session_id == worker_session:
                            session_id = None
                    last_event_id = None
                    router_log("WARN", f"mcp_http_proxy_stream_session_lost server={server_name} error=HTTPError:{exc.code}:{exc.reason}")
                    continue  # re-init at loop top
                router_log("WARN", f"mcp_http_proxy_stream_reconnect server={server_name} event={event_name} error=HTTPError:{exc.code}:{exc.reason}")
            except Exception as exc:
                if _mcp_stream_read_timeout_error(exc):
                    with session_cond:
                        initialized_seen = initialized_payload is not None
                        session_cond.notify_all()
                    router_log(
                        "WARN",
                        f"mcp_http_proxy_stream_timeout_reconnect server={server_name} event={event_name} "
                        f"initialized={initialized_seen} session={worker_session or '-'} "
                        f"last_event_id={last_event_id or '-'} error={type(exc).__name__}: {exc}",
                    )
                    continue
                router_log("WARN", f"mcp_http_proxy_stream_reconnect server={server_name} event={event_name} error={type(exc).__name__}: {exc}")
            # Reconnect backoff. A plain reset retries the same session; a stream
            # that ended because the backend dropped the session will fail the
            # GET with 404 next round and re-init above.
            with session_cond:
                if not stream_stop.is_set():
                    session_cond.wait(timeout=retry_seconds)

    def ensure_manager_started() -> None:
        nonlocal manager_thread
        if manager_thread is not None:
            return
        manager_thread = threading.Thread(
            target=stream_manager, daemon=True, name=f"ca-mcp-http-stream-{server_name}",
        )
        manager_thread.start()

    buffer = bytearray()
    try:
        stdin_fd = sys.stdin.fileno()
        while True:
            chunk = os.read(stdin_fd, 65536)
            if not chunk:
                break
            buffer.extend(chunk)
            for payload in _mcp_proxy_drain_input_messages(buffer):
                request_id = payload.get("id")
                method = str(payload.get("method") or "")
                if method == "notifications/initialized":
                    # Cache it (the manager re-sends it after each re-init) and,
                    # if a session is already up, forward it now so the initial
                    # handshake completes in order.
                    with session_cond:
                        initialized_payload = payload
                        active = session_id
                    if active:
                        try:
                            _mcp_proxy_streamable_http_request(endpoint, headers, payload, timeout, protocol_version, active)
                            router_log("INFO", f"mcp_http_proxy_initialized_forwarded server={server_name} session={active}")
                        except urllib.error.HTTPError as exc:
                            body_text = _http_error_body_text(exc)
                            with session_cond:
                                if _streamable_http_session_not_found(exc, body_text) and session_id == active:
                                    session_id = None
                                    session_requested = True
                            router_log(
                                "WARN",
                                f"mcp_http_proxy_initialized_forward_failed server={server_name} error=HTTPError:{exc.code}:{exc.reason}",
                            )
                        except Exception as exc:
                            router_log("WARN", f"mcp_http_proxy_initialized_forward_failed server={server_name} error={type(exc).__name__}: {exc}")
                    with session_cond:
                        if active and session_id == active:
                            stream_reopen_requested = True
                        session_cond.notify_all()
                    continue
                if method == "initialize":
                    # Hand the initialize off to the single session owner: cache
                    # the payload, wake the manager to (re)initialize, and reply
                    # with the result it produces. stdin never POSTs initialize
                    # itself, so session_id has exactly one writer (the manager).
                    with session_cond:
                        initialize_payload = payload
                        initialize_result = None
                        session_requested = True
                        session_cond.notify_all()
                    ensure_manager_started()
                    with session_cond:
                        deadline = time.time() + timeout
                        while initialize_result is None and not stream_stop.is_set():
                            remaining = deadline - time.time()
                            if remaining <= 0:
                                break
                            session_cond.wait(timeout=min(1.0, remaining))
                        resp = initialize_result
                    if payload.get("id") is not None:
                        if isinstance(resp, dict):
                            reply = dict(resp)
                            reply["id"] = request_id
                            _mcp_proxy_write_json_response(reply)
                        else:
                            _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, "initialize timeout"))
                    continue
                try:
                    if _mcp_proxy_tool_is_notification_wait(_mcp_proxy_tool_call_name(payload)):
                        _mcp_proxy_write_json_response(wait_for_proxy_notifications(payload))
                        continue
                    active_session = current_session_id()
                    if requires_session and not active_session:
                        active_session = request_session_and_wait(timeout)
                        if not active_session:
                            raise RuntimeError("Streamable HTTP MCP session is not initialized")
                    result, _returned = _mcp_proxy_streamable_http_request(
                        endpoint, headers, payload, timeout, protocol_version, active_session,
                    )
                    tool_name = _mcp_proxy_tool_call_name(payload)
                    if isinstance(result, dict):
                        _mcp_proxy_observe_json_message(server_name, result)
                        result = _mcp_proxy_compact_tool_result_response(server_name, tool_name, result)
                    if payload.get("id") is not None:
                        if isinstance(result, dict):
                            _mcp_proxy_write_json_response(result)
                        else:
                            _mcp_proxy_write_json_response(
                                {"jsonrpc": "2.0", "id": request_id, "result": result if result is not None else {}}
                            )
                except urllib.error.HTTPError as exc:
                    body_text = _http_error_body_text(exc)
                    if _streamable_http_session_not_found(exc, body_text) and initialize_payload:
                        # Backend dropped the session mid tool-call. Ask the
                        # manager to re-init (single owner) and retry once on the
                        # session it provides. The stream resumes on that same
                        # re-init -- no second owner.
                        router_log("WARN", f"mcp_http_proxy_session_lost server={server_name} method={method} error=HTTPError:{exc.code}:{exc.reason}")
                        with session_cond:
                            if session_id is not None:
                                session_id = None
                            session_requested = True
                            session_cond.notify_all()
                        active_session = request_session_and_wait(timeout)
                        if active_session:
                            try:
                                result, _r = _mcp_proxy_streamable_http_request(
                                    endpoint, headers, payload, timeout, protocol_version, active_session,
                                )
                                tool_name = _mcp_proxy_tool_call_name(payload)
                                if isinstance(result, dict):
                                    _mcp_proxy_observe_json_message(server_name, result)
                                    result = _mcp_proxy_compact_tool_result_response(server_name, tool_name, result)
                                if payload.get("id") is not None:
                                    _mcp_proxy_write_json_response(result if isinstance(result, dict) else {"jsonrpc": "2.0", "id": request_id, "result": result if result is not None else {}})
                                continue
                            except Exception as retry_exc:
                                _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, f"{type(retry_exc).__name__}: {retry_exc}"))
                                continue
                        _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, "Streamable HTTP MCP session is not initialized"))
                        continue
                    _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, f"HTTPError:{exc.code}: {exc.reason}; {body_text}".strip()))
                except Exception as exc:
                    _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, f"{type(exc).__name__}: {exc}"))
        for payload in _mcp_proxy_drain_input_messages(buffer, final=True):
            request_id = payload.get("id")
            try:
                if _mcp_proxy_tool_is_notification_wait(_mcp_proxy_tool_call_name(payload)):
                    _mcp_proxy_write_json_response(wait_for_proxy_notifications(payload))
                    continue
                result, _r = _mcp_proxy_streamable_http_request(endpoint, headers, payload, timeout, protocol_version, current_session_id())
                tool_name = _mcp_proxy_tool_call_name(payload)
                if isinstance(result, dict):
                    result = _mcp_proxy_compact_tool_result_response(server_name, tool_name, result)
                if payload.get("id") is not None:
                    _mcp_proxy_write_json_response(result if isinstance(result, dict) else {"jsonrpc": "2.0", "id": request_id, "result": result if result is not None else {}})
            except Exception as exc:
                _mcp_proxy_write_json_response(_mcp_proxy_error_response(request_id, f"{type(exc).__name__}: {exc}"))
        router_log("INFO", f"mcp_http_proxy_exited server={server_name}")
        return 0
    except Exception as exc:
        router_log("ERROR", f"mcp_http_proxy_failed server={server_name} error={type(exc).__name__}: {exc}")
        print(f"ciel-runtime mcp-proxy: Streamable HTTP bridge failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        stream_stop.set()
        with session_cond:
            session_cond.notify_all()
        if manager_thread is not None:
            manager_thread.join(timeout=2.0)
