"""MCP proxy stdio framing and process transport adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from typing import Any, Callable

from .mcp_proxy_codec import _mcp_proxy_error_response


_MCP_PROXY_IO_LOCK = threading.Lock()


def _mcp_proxy_observe_stdout_line(
    server_name: str,
    line: bytes,
    observe_json_message: Callable[..., Any],
) -> None:
    try:
        text = line.decode("utf-8", errors="replace").strip()
        if not text or not text.startswith("{"):
            return
        payload = json.loads(text)
    except Exception:
        return
    observe_json_message(server_name, payload)


def _mcp_proxy_header_end(buffer: bytes) -> tuple[int, int] | None:
    crlf = buffer.find(b"\r\n\r\n")
    lf = buffer.find(b"\n\n")
    candidates: list[tuple[int, int]] = []
    if crlf >= 0:
        candidates.append((crlf, 4))
    if lf >= 0:
        candidates.append((lf, 2))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def _mcp_proxy_frame_header(buffer: bytes) -> tuple[int, int, int] | None:
    header = _mcp_proxy_header_end(buffer)
    if not header:
        return None
    header_end, delimiter_len = header
    length = _mcp_proxy_content_length(buffer[:header_end])
    if length is None:
        return None
    return header_end, delimiter_len, length


def _mcp_proxy_content_length(header_bytes: bytes) -> int | None:
    try:
        header_text = header_bytes.decode("ascii", errors="replace")
    except Exception:
        return None
    for line in re.split(r"\r?\n", header_text):
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except Exception:
                return None
            return length if length >= 0 else None
    return None


class _McpStdoutObserver:
    def __init__(self, server_name: str, observe_json_message: Callable[..., Any]) -> None:
        self.server_name = server_name
        self._observe_json_message = observe_json_message
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.buffer.extend(chunk)
        self._drain()

    def _drop_until_candidate(self) -> bool:
        data = bytes(self.buffer)
        if not data:
            return False
        stripped = data.lstrip()
        if len(stripped) != len(data):
            del self.buffer[: len(data) - len(stripped)]
            data = stripped
        if _mcp_proxy_frame_header(data) or data.startswith(b"{"):
            return True
        lowered = data.lower()
        content_idx = lowered.find(b"content-length:")
        json_idx = data.find(b"{")
        candidates = [idx for idx in (content_idx, json_idx) if idx >= 0]
        newline_idx = data.find(b"\n")
        if candidates:
            keep_from = min(candidates)
            if newline_idx >= 0 and newline_idx < keep_from:
                del self.buffer[: newline_idx + 1]
            elif keep_from > 0:
                del self.buffer[:keep_from]
            return True
        if newline_idx >= 0:
            del self.buffer[: newline_idx + 1]
            return True
        if len(self.buffer) > 1024 * 1024:
            del self.buffer[:-4096]
        return False

    def _drain(self) -> None:
        while self.buffer:
            if not self._drop_until_candidate():
                return
            data = bytes(self.buffer)
            frame = _mcp_proxy_frame_header(data)
            if frame:
                header_end, delimiter_len, length = frame
                body_start = header_end + delimiter_len
                body_end = body_start + length
                if len(data) < body_end:
                    return
                body = data[body_start:body_end]
                del self.buffer[:body_end]
                try:
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                except Exception:
                    continue
                self._observe_json_message(self.server_name, payload)
                continue
            if data.startswith(b"{"):
                newline_idx = data.find(b"\n")
                if newline_idx < 0:
                    return
                line = data[:newline_idx]
                del self.buffer[: newline_idx + 1]
                _mcp_proxy_observe_stdout_line(self.server_name, line, self._observe_json_message)
                continue
            return


def _mcp_proxy_forward_stdin(proc: subprocess.Popen[bytes], *, log: Callable[..., Any]) -> None:
    try:
        stdin_fd = sys.stdin.fileno()
        while True:
            chunk = os.read(stdin_fd, 65536)
            if not chunk:
                break
            if proc.stdin:
                proc.stdin.write(chunk)
                proc.stdin.flush()
    except Exception as exc:
        log("WARN", f"mcp_proxy_stdin_forward_failed error={type(exc).__name__}: {exc}")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception as exc:
            log("WARN", f"mcp_proxy_stdin_close_failed error={type(exc).__name__}: {exc}")


def _mcp_proxy_stdio_mode(server: dict[str, Any]) -> str:
    mode = str(server.get("ciel_runtime_stdio") or server.get("stdio_mode") or "").strip().lower()
    if mode in ("jsonl", "json-lines", "json_lines", "newline-json", "line-json"):
        return "jsonl"
    return "framed"


def _mcp_proxy_write_proc_jsonl(proc: subprocess.Popen[bytes], body: bytes) -> None:
    line = body.strip()
    if not line or not proc.stdin:
        return
    proc.stdin.write(line + b"\n")
    proc.stdin.flush()


def _mcp_proxy_drain_stdin_jsonl_buffer(proc: subprocess.Popen[bytes], buffer: bytearray, *, final: bool = False) -> None:
    while buffer:
        data = bytes(buffer)
        stripped = data.lstrip()
        if len(stripped) != len(data):
            del buffer[: len(data) - len(stripped)]
            data = stripped
        frame = _mcp_proxy_frame_header(data)
        if frame:
            header_end, delimiter_len, length = frame
            body_start = header_end + delimiter_len
            body_end = body_start + length
            if len(data) < body_end:
                return
            body = data[body_start:body_end]
            del buffer[:body_end]
            _mcp_proxy_write_proc_jsonl(proc, body)
            continue
        if data.startswith(b"{"):
            newline_idx = data.find(b"\n")
            if newline_idx >= 0:
                line = data[:newline_idx]
                del buffer[: newline_idx + 1]
                _mcp_proxy_write_proc_jsonl(proc, line)
                continue
            if final:
                del buffer[:]
                _mcp_proxy_write_proc_jsonl(proc, data)
            return
        lowered = data.lower()
        content_idx = lowered.find(b"content-length:")
        json_idx = data.find(b"{")
        candidates = [idx for idx in (content_idx, json_idx) if idx >= 0]
        newline_idx = data.find(b"\n")
        if candidates:
            keep_from = min(candidates)
            if keep_from > 0:
                del buffer[:keep_from]
            continue
        if newline_idx >= 0:
            del buffer[: newline_idx + 1]
            continue
        if len(buffer) > 1024 * 1024:
            del buffer[:-4096]
        return


def _mcp_proxy_forward_stdin_jsonl(proc: subprocess.Popen[bytes], *, log: Callable[..., Any]) -> None:
    buffer = bytearray()
    try:
        stdin_fd = sys.stdin.fileno()
        while True:
            chunk = os.read(stdin_fd, 65536)
            if not chunk:
                break
            buffer.extend(chunk)
            _mcp_proxy_drain_stdin_jsonl_buffer(proc, buffer)
        _mcp_proxy_drain_stdin_jsonl_buffer(proc, buffer, final=True)
    except Exception as exc:
        log("WARN", f"mcp_proxy_jsonl_stdin_forward_failed error={type(exc).__name__}: {exc}")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception as exc:
            log("WARN", f"mcp_proxy_jsonl_stdin_close_failed error={type(exc).__name__}: {exc}")


def _mcp_proxy_write_stdout_frame(body: bytes) -> None:
    with _MCP_PROXY_IO_LOCK:
        sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
        sys.stdout.buffer.flush()


# Framing the Streamable HTTP proxy uses when replying to the MCP client on
# stdout. Claude Code's stdio MCP client speaks newline-delimited JSON (JSONL);
# replying with LSP-style Content-Length frames makes Claude Code fail to
# connect ("Failed to connect"). The stdio proxy paths are unaffected -- they
# keep using _mcp_proxy_write_stdout_frame directly. Default JSONL (Claude
# Code's format); switched to "framed" if the client actually sends frames.
_MCP_PROXY_HTTP_CLIENT_FRAMING = "jsonl"


def _mcp_proxy_set_http_client_framing(mode: str) -> None:
    global _MCP_PROXY_HTTP_CLIENT_FRAMING
    if mode in ("jsonl", "framed"):
        _MCP_PROXY_HTTP_CLIENT_FRAMING = mode


def _mcp_proxy_write_client_message(body: bytes) -> None:
    with _MCP_PROXY_IO_LOCK:
        if _MCP_PROXY_HTTP_CLIENT_FRAMING == "framed":
            sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body)
        else:
            sys.stdout.buffer.write(body.strip() + b"\n")
        sys.stdout.buffer.flush()


def _mcp_proxy_write_json_response(payload: dict[str, Any]) -> None:
    _mcp_proxy_write_client_message(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _mcp_proxy_drain_input_messages(buffer: bytearray, *, final: bool = False) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while buffer:
        data = bytes(buffer)
        stripped = data.lstrip()
        if len(stripped) != len(data):
            del buffer[: len(data) - len(stripped)]
            data = stripped
        frame = _mcp_proxy_frame_header(data)
        if frame:
            # Client sent an LSP-style Content-Length frame: reply in kind.
            _mcp_proxy_set_http_client_framing("framed")
            header_end, delimiter_len, length = frame
            body_start = header_end + delimiter_len
            body_end = body_start + length
            if len(data) < body_end:
                return messages
            body = data[body_start:body_end]
            del buffer[:body_end]
        elif data.startswith(b"{"):
            # Client sent newline-delimited JSON (Claude Code): reply in kind.
            _mcp_proxy_set_http_client_framing("jsonl")
            newline_idx = data.find(b"\n")
            if newline_idx < 0:
                if not final:
                    return messages
                body = bytes(data)
                del buffer[:]
            else:
                body = data[:newline_idx]
                del buffer[: newline_idx + 1]
        else:
            content_idx = data.lower().find(b"content-length:")
            json_idx = data.find(b"{")
            candidates = [idx for idx in (content_idx, json_idx) if idx >= 0]
            if candidates:
                keep_from = min(candidates)
                if keep_from > 0:
                    del buffer[:keep_from]
                continue
            newline_idx = data.find(b"\n")
            if newline_idx >= 0:
                del buffer[: newline_idx + 1]
                continue
            if len(buffer) > 1024 * 1024:
                del buffer[:-4096]
            return messages
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception as exc:
            _mcp_proxy_write_json_response(_mcp_proxy_error_response(None, f"invalid JSON-RPC payload: {type(exc).__name__}", -32700))
            continue
        if isinstance(payload, dict):
            messages.append(payload)
    return messages


def _mcp_proxy_emit_jsonl_stdout_line(
    server_name: str,
    line: bytes,
    *,
    observe_json_message: Callable[..., Any],
    log: Callable[..., Any],
) -> None:
    body = line.strip()
    if not body:
        return
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        try:
            sys.stderr.buffer.write(body + b"\n")
            sys.stderr.buffer.flush()
        except Exception as write_exc:
            log(
                "WARN",
                f"mcp_proxy_invalid_stdout_log_failed server={server_name} "
                f"error={type(write_exc).__name__}: {write_exc}",
            )
        return
    observe_json_message(server_name, payload)
    _mcp_proxy_write_stdout_frame(body)


def _mcp_proxy_forward_stdout_jsonl(
    server_name: str,
    proc: subprocess.Popen[bytes],
    *,
    observe_json_message: Callable[..., Any],
    log: Callable[..., Any],
) -> None:
    if not proc.stdout:
        return
    buffer = bytearray()
    while True:
        chunk = proc.stdout.read(65536)
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            newline_idx = buffer.find(b"\n")
            if newline_idx < 0:
                break
            line = bytes(buffer[:newline_idx])
            del buffer[: newline_idx + 1]
            _mcp_proxy_emit_jsonl_stdout_line(server_name, line, observe_json_message=observe_json_message, log=log)
    if buffer.strip():
        _mcp_proxy_emit_jsonl_stdout_line(server_name, bytes(buffer), observe_json_message=observe_json_message, log=log)


def _mcp_proxy_forward_stderr(proc: subprocess.Popen[bytes], *, log: Callable[..., Any]) -> None:
    try:
        if not proc.stderr:
            return
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
    except Exception as exc:
        log("WARN", f"mcp_proxy_stderr_forward_failed error={type(exc).__name__}: {exc}")


def _mcp_proxy_streamable_http_request(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
    protocol_version: str,
    session_id: str | None,
    *,
    post_json: Callable[..., Any],
) -> tuple[Any, str | None]:
    return post_json(endpoint, headers, payload, timeout, protocol_version, session_id)
