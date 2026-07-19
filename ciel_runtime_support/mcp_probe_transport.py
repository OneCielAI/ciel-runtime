"""SSE and Streamable HTTP MCP capability probe transports."""

from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True, slots=True)
class McpProbeCodec:
    initialize_bytes: Callable[..., bytes]
    initialize_dict: Callable[..., dict[str, Any]]
    decode_sse_events: Callable[..., tuple[list[tuple[str, str]], bytearray]]
    capability_present: Callable[..., bool]
    decode_preview: Callable[..., str]


@dataclass(frozen=True, slots=True)
class McpProbeHttp:
    runtime_headers: Callable[..., dict[str, str]]
    urlopen: Callable[..., Any]
    streamable_post_json: Callable[..., Any]
    delete_streamable_session: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class McpProbePolicy:
    default_timeout: Callable[[], float]
    stderr_preview_chars: int
    stdout_preview_bytes: int
    sse_open_timeout_seconds: float
    sse_init_post_timeout_seconds: float
    streamable_protocol_version: str


@dataclass(frozen=True, slots=True)
class McpProbeServices:
    codec: McpProbeCodec
    http: McpProbeHttp
    policy: McpProbePolicy
    log: Callable[[str, str], Any]


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "capable": False,
        "reason": reason,
        "response_bytes": 0,
        "response_received": False,
        "elapsed_ms": 0,
        "exit_code": None,
        "stderr_bytes": 0,
        "stderr_preview": "",
        "stdout_preview": "",
    }


def probe_sse_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
    *,
    services: McpProbeServices,
) -> dict[str, Any]:
    started = time.time()
    url = str(server.get("url") or "").strip()
    if not url:
        return _empty_result("no_url")
    codec = services.codec
    http = services.http
    policy = services.policy
    effective_timeout = timeout if timeout is not None else policy.default_timeout()
    open_timeout = min(policy.sse_open_timeout_seconds, effective_timeout)
    request_headers = {
        **http.runtime_headers(server),
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    try:
        get_request = urllib.request.Request(url, headers=request_headers, method="GET")
        sse_response = http.urlopen(get_request, timeout=open_timeout)
    except Exception as exc:
        result = _empty_result(f"sse_open_failed:{type(exc).__name__}")
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        result["stderr_preview"] = str(exc)[:policy.stderr_preview_chars]
        return result

    chunks: queue.Queue[bytes | None] = queue.Queue()

    def read_sse() -> None:
        try:
            while True:
                line = sse_response.readline()
                if not line:
                    break
                chunks.put(line)
        except Exception as exc:
            services.log(
                "DEBUG",
                f"channel_probe_sse_reader_failed server={server_name} error={type(exc).__name__}: {exc}",
            )
        finally:
            chunks.put(None)

    threading.Thread(target=read_sse, daemon=True, name=f"channel-probe-sse-{server_name}").start()
    deadline = time.time() + effective_timeout
    sse_buffer = bytearray()
    bytes_seen = 0
    endpoint_url: str | None = None
    init_posted = False
    init_post_error: str | None = None
    capable = False
    response_received = False
    response_data_preview = ""
    post_headers = {"Content-Type": "application/json"}
    for key, value in request_headers.items():
        if key.lower() not in ("accept", "cache-control"):
            post_headers[key] = value
    try:
        while time.time() < deadline:
            wait = min(0.2, max(0.001, deadline - time.time()))
            try:
                chunk = chunks.get(timeout=wait)
            except queue.Empty:
                continue
            if chunk is None:
                break
            sse_buffer.extend(chunk)
            bytes_seen += len(chunk)
            events, sse_buffer = codec.decode_sse_events(sse_buffer)
            for event_name, data_text in events:
                if not init_posted and event_name == "endpoint":
                    target = data_text.strip()
                    if not target:
                        continue
                    endpoint_url = urllib.parse.urljoin(url, target)
                    try:
                        post_request = urllib.request.Request(
                            endpoint_url,
                            data=codec.initialize_bytes(),
                            headers=post_headers,
                            method="POST",
                        )
                        with http.urlopen(
                            post_request,
                            timeout=min(policy.sse_init_post_timeout_seconds, max(1.0, deadline - time.time())),
                        ) as post_response:
                            post_response.read()
                        init_posted = True
                    except Exception as exc:
                        init_post_error = f"{type(exc).__name__}: {exc}"
                        break
                    continue
                if not init_posted or not data_text:
                    continue
                try:
                    message = json.loads(data_text)
                except (TypeError, ValueError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("id") == 1 and "result" in message:
                    response_received = True
                    capable = codec.capability_present(message)
                    response_data_preview = codec.decode_preview(
                        data_text.encode("utf-8"), policy.stdout_preview_bytes
                    )
                    break
            if response_received or init_post_error:
                break
    finally:
        try:
            sse_response.close()
        except Exception as exc:
            services.log(
                "DEBUG",
                f"channel_probe_sse_close_failed server={server_name} error={type(exc).__name__}: {exc}",
            )
    elapsed_ms = int((time.time() - started) * 1000)
    if capable:
        reason = "capable"
    elif response_received:
        reason = "no_experimental_claude_channel"
    elif init_post_error:
        reason = f"sse_init_post_failed:{init_post_error.split(':', 1)[0]}"
    elif init_posted:
        reason = "timeout_waiting_for_initialize_response"
    elif endpoint_url:
        reason = "timeout_after_endpoint_event"
    else:
        reason = "timeout_no_endpoint_event"
    stderr_preview = init_post_error[:policy.stderr_preview_chars] if init_post_error else ""
    stdout_preview = response_data_preview if response_data_preview and not capable else ""
    services.log(
        "INFO",
        "channel_probe_result server=%s channel_capable=%s reason=%s transport=sse url=%s bytes=%d elapsed_ms=%d timeout_s=%.1f"
        % (server_name, capable, reason, url, bytes_seen, elapsed_ms, effective_timeout),
    )
    if stderr_preview:
        services.log("INFO", f"channel_probe_sse_error server={server_name} preview={stderr_preview!r}")
    return {
        "capable": capable,
        "reason": reason,
        "response_bytes": bytes_seen,
        "response_received": response_received,
        "elapsed_ms": elapsed_ms,
        "exit_code": None,
        "stderr_bytes": len(stderr_preview),
        "stderr_preview": stderr_preview,
        "stdout_preview": stdout_preview,
    }


def probe_streamable_http_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
    *,
    services: McpProbeServices,
) -> dict[str, Any]:
    started = time.time()
    url = str(server.get("url") or server.get("endpoint") or "").strip()
    if not url:
        return _empty_result("no_url")
    codec = services.codec
    http = services.http
    policy = services.policy
    effective_timeout = timeout if timeout is not None else policy.default_timeout()
    protocol_version = str(
        server.get("protocolVersion")
        or server.get("protocol_version")
        or policy.streamable_protocol_version
    )
    headers = http.runtime_headers(server)
    bytes_seen = 0
    response_received = False
    capable = False
    reason = ""
    stderr_preview = ""
    stdout_preview = ""
    initialized_failed = False
    session_id: str | None = None
    try:
        response, session_id = http.streamable_post_json(
            url,
            headers,
            codec.initialize_dict(protocol_version),
            max(1.0, min(120.0, effective_timeout)),
            protocol_version,
        )
        preview_source = json.dumps(response, ensure_ascii=False) if isinstance(response, (dict, list)) else str(response or "")
        bytes_seen = len(preview_source.encode("utf-8", errors="replace"))
        if isinstance(response, dict) and response.get("id") == 1 and "result" in response:
            response_received = True
            capable = codec.capability_present(response)
            if not capable:
                stdout_preview = codec.decode_preview(preview_source.encode("utf-8"), policy.stdout_preview_bytes)
        else:
            stdout_preview = codec.decode_preview(preview_source.encode("utf-8"), policy.stdout_preview_bytes)
        if response_received:
            try:
                http.streamable_post_json(
                    url,
                    headers,
                    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                    max(1.0, min(120.0, effective_timeout)),
                    protocol_version,
                    session_id,
                )
            except Exception as exc:
                initialized_failed = True
                capable = False
                stderr_preview = f"{type(exc).__name__}: {exc}"[:policy.stderr_preview_chars]
        reason = "streamable_http_initialized_post_failed" if initialized_failed else (
            "capable" if capable else ("no_experimental_claude_channel" if response_received else "no_initialize_response")
        )
    except urllib.error.HTTPError as exc:
        reason = "streamable_http_405_fallback_sse" if exc.code == 405 else f"streamable_http_open_failed:HTTPError:{exc.code}"
        try:
            data = exc.read()
        except Exception as read_exc:
            services.log(
                "DEBUG",
                f"channel_probe_http_error_read_failed server={server_name} error={type(read_exc).__name__}: {read_exc}",
            )
            data = b""
        bytes_seen = len(data)
        stderr_preview = (data.decode("utf-8", errors="replace") or str(exc))[:policy.stderr_preview_chars]
    except Exception as exc:
        reason = f"streamable_http_open_failed:{type(exc).__name__}"
        stderr_preview = str(exc)[:policy.stderr_preview_chars]
    finally:
        if session_id:
            http.delete_streamable_session(
                f"probe-{server_name}",
                url,
                headers,
                protocol_version,
                session_id,
                "channel_probe_cleanup",
                timeout=min(10.0, max(1.0, effective_timeout)),
            )
    elapsed_ms = int((time.time() - started) * 1000)
    services.log(
        "INFO",
        "channel_probe_result server=%s channel_capable=%s reason=%s transport=streamable-http url=%s bytes=%d elapsed_ms=%d timeout_s=%.1f"
        % (server_name, capable, reason, url, bytes_seen, elapsed_ms, effective_timeout),
    )
    if stderr_preview:
        services.log("INFO", f"channel_probe_streamable_http_error server={server_name} preview={stderr_preview!r}")
    return {
        "capable": capable,
        "reason": reason,
        "response_bytes": bytes_seen,
        "response_received": response_received,
        "elapsed_ms": elapsed_ms,
        "exit_code": None,
        "stderr_bytes": len(stderr_preview),
        "stderr_preview": stderr_preview,
        "stdout_preview": stdout_preview,
    }
