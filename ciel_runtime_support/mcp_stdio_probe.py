"""Stdio MCP capability probe process adapter."""

from __future__ import annotations

from dataclasses import dataclass
import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class StdioProbeCodec:
    initialize_payload: Callable[[], bytes]
    strategy_for: Callable[..., str]
    find_initialize_response: Callable[..., dict[str, Any] | None]
    capability_present: Callable[..., bool]
    decode_preview: Callable[..., str]


@dataclass(frozen=True, slots=True)
class StdioProbeProcess:
    is_stdio: Callable[..., bool]
    resolve_server_process: Callable[..., tuple[str, list[str]]]
    popen: Callable[..., Any]


@dataclass(frozen=True, slots=True)
class StdioProbePolicy:
    default_timeout: Callable[[], float]
    stderr_cap_bytes: int
    stderr_preview_chars: int
    stdout_preview_bytes: int


@dataclass(frozen=True, slots=True)
class StdioProbeServices:
    codec: StdioProbeCodec
    process: StdioProbeProcess
    policy: StdioProbePolicy
    log: Callable[[str, str], Any]


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "capable": False,
        "reason": reason,
        "response_bytes": 0,
        "response_received": False,
        "exit_code": None,
        "stderr_bytes": 0,
        "stderr_preview": "",
        "stdout_preview": "",
        "elapsed_ms": 0,
    }


def probe_stdio_mcp_for_channel_capability_detailed(
    server_name: str,
    server: dict[str, Any],
    timeout: float | None = None,
    *,
    services: StdioProbeServices,
) -> dict[str, Any]:
    started = time.time()
    codec = services.codec
    process = services.process
    policy = services.policy
    if not process.is_stdio(server):
        return _empty_result("not_stdio")
    command = str(server.get("command") or "").strip()
    args_raw = server.get("args", [])
    args = [str(item) for item in args_raw] if isinstance(args_raw, list) else []
    if not command:
        return _empty_result("no_command")
    command, args = process.resolve_server_process(command, args)
    environment = os.environ.copy()
    raw_environment = server.get("env")
    if isinstance(raw_environment, dict):
        environment.update({str(key): str(value) for key, value in raw_environment.items() if str(key)})
    cwd_value = server.get("cwd") or server.get("workingDirectory")
    cwd = str(cwd_value) if cwd_value else None
    framed = codec.strategy_for(server) == "framed"
    effective_timeout = timeout if timeout is not None else policy.default_timeout()
    try:
        child = process.popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=environment,
            bufsize=0,
            close_fds=True,
        )
    except Exception as exc:
        services.log("DEBUG", f"channel_probe_spawn_failed server={server_name} error={type(exc).__name__}: {exc}")
        result = _empty_result(f"spawn_failed:{type(exc).__name__}")
        result["stderr_preview"] = str(exc)[:policy.stderr_preview_chars]
        result["elapsed_ms"] = int((time.time() - started) * 1000)
        return result

    stdout_chunks: queue.Queue[bytes | None] = queue.Queue()
    stderr_buffer = bytearray()
    stderr_lock = threading.Lock()

    def read_stdout() -> None:
        try:
            if child.stdout is None:
                return
            while True:
                chunk = child.stdout.read(4096)
                if not chunk:
                    break
                stdout_chunks.put(chunk)
        except Exception as exc:
            services.log("DEBUG", f"channel_probe_stdout_reader_failed server={server_name} error={type(exc).__name__}: {exc}")
        finally:
            stdout_chunks.put(None)

    def read_stderr() -> None:
        try:
            if child.stderr is None:
                return
            while True:
                chunk = child.stderr.read(1024)
                if not chunk:
                    break
                with stderr_lock:
                    remaining = policy.stderr_cap_bytes - len(stderr_buffer)
                    if remaining > 0:
                        stderr_buffer.extend(chunk[:remaining])
        except Exception as exc:
            services.log("DEBUG", f"channel_probe_stderr_reader_failed server={server_name} error={type(exc).__name__}: {exc}")

    stdout_thread = threading.Thread(target=read_stdout, daemon=True, name=f"channel-probe-stdout-{server_name}")
    stderr_thread = threading.Thread(target=read_stderr, daemon=True, name=f"channel-probe-stderr-{server_name}")
    stdout_thread.start()
    stderr_thread.start()
    body = codec.initialize_payload()
    frame = b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body if framed else body + b"\n"
    try:
        if child.stdin:
            child.stdin.write(frame)
            child.stdin.flush()
    except Exception as exc:
        services.log("WARN", f"channel_probe_initialize_write_failed server={server_name} error={type(exc).__name__}: {exc}")

    deadline = time.time() + effective_timeout
    stdout_buffer = bytearray()
    capable = False
    response_received = False
    eof_seen = False
    try:
        while time.time() < deadline:
            wait = min(0.2, max(0.001, deadline - time.time()))
            try:
                chunk = stdout_chunks.get(timeout=wait)
            except queue.Empty:
                continue
            if chunk is None:
                eof_seen = True
                break
            stdout_buffer.extend(chunk)
            response = codec.find_initialize_response(bytes(stdout_buffer), framed)
            if response is not None:
                response_received = True
                capable = codec.capability_present(response)
                break
    finally:
        try:
            if child.stdin:
                child.stdin.close()
        except Exception as exc:
            services.log("DEBUG", f"channel_probe_stdin_close_failed server={server_name} error={type(exc).__name__}: {exc}")
        try:
            child.terminate()
            child.wait(timeout=1.0)
        except Exception as terminate_exc:
            services.log("DEBUG", f"channel_probe_terminate_failed server={server_name} error={type(terminate_exc).__name__}: {terminate_exc}")
            try:
                child.kill()
                child.wait(timeout=1.0)
            except Exception as kill_exc:
                services.log("WARN", f"channel_probe_kill_failed server={server_name} error={type(kill_exc).__name__}: {kill_exc}")
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
        for stream in (child.stdout, child.stderr):
            try:
                if stream:
                    stream.close()
            except Exception as exc:
                services.log("DEBUG", f"channel_probe_stream_close_failed server={server_name} error={type(exc).__name__}: {exc}")

    exit_code = child.returncode
    reason = "capable" if capable else (
        "no_experimental_claude_channel" if response_received else ("exited_without_response" if eof_seen else "timeout")
    )
    with stderr_lock:
        stderr_bytes = len(stderr_buffer)
        stderr_preview = codec.decode_preview(stderr_buffer, policy.stderr_preview_chars) if not capable else ""
    stdout_preview = ""
    if not capable and not response_received and stdout_buffer:
        stdout_preview = codec.decode_preview(
            bytes(stdout_buffer)[:policy.stdout_preview_bytes],
            policy.stdout_preview_bytes,
        )
    elapsed_ms = int((time.time() - started) * 1000)
    services.log(
        "INFO",
        "channel_probe_result server=%s channel_capable=%s reason=%s framed=%s bytes=%d stderr_bytes=%d exit_code=%s elapsed_ms=%d timeout_s=%.1f"
        % (
            server_name,
            capable,
            reason,
            framed,
            len(stdout_buffer),
            stderr_bytes,
            "None" if exit_code is None else str(exit_code),
            elapsed_ms,
            effective_timeout,
        ),
    )
    if stderr_preview:
        services.log("INFO", f"channel_probe_stderr server={server_name} preview={stderr_preview!r}")
    if stdout_preview:
        services.log("INFO", f"channel_probe_stdout server={server_name} preview={stdout_preview!r}")
    return {
        "capable": capable,
        "reason": reason,
        "response_bytes": len(stdout_buffer),
        "response_received": response_received,
        "exit_code": exit_code,
        "stderr_bytes": stderr_bytes,
        "stderr_preview": stderr_preview,
        "stdout_preview": stdout_preview,
        "elapsed_ms": elapsed_ms,
    }
