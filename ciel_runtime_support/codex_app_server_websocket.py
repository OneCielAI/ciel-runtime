"""Standard-library WebSocket transport for `codex app-server --listen ws://`.

`CodexAppServerClient` speaks newline-delimited JSON over a process's pipes.
This module presents one WebSocket connection through that same process shape
(stdin/stdout/poll/terminate/wait/kill), so the client is reused unchanged.

The handshake deliberately sends no Origin header: app-server answers a
handshake that carries one with 403 Forbidden (measured 2026-09-23 against
codex app-server 0.155 bundled with the Codex desktop app).
"""

from __future__ import annotations

import base64
import os
import socket
import struct
import threading
from typing import Callable, Iterator
from urllib.parse import urlsplit

_OP_CONTINUATION = 0x0
_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


class CodexWebSocketError(OSError):
    """Raised when the app-server WebSocket handshake or framing fails."""


def ws_endpoint(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    if parts.scheme != "ws":
        raise CodexWebSocketError(f"unsupported app-server URL (ws:// only): {url}")
    host = parts.hostname or "127.0.0.1"
    port = parts.port or 80
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return host, port, path


def encode_client_frame(opcode: int, payload: bytes, mask: bytes) -> bytes:
    """One final, masked client frame (RFC 6455 5.2)."""

    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return bytes(header) + mask + masked


class CodexWebSocketConnection:
    def __init__(
        self,
        sock: socket.socket,
        *,
        random_bytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._sock = sock
        self._random = random_bytes
        self._send_lock = threading.Lock()
        self._buffer = b""
        self.closed = False

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0) -> "CodexWebSocketConnection":
        host, port, path = ws_endpoint(url)
        sock = socket.create_connection((host, port), timeout=timeout)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            connection = cls(sock)
            head = connection._read_until(b"\r\n\r\n")
            status = head.split(b"\r\n", 1)[0].decode("latin-1")
            if " 101 " not in f"{status} ":
                raise CodexWebSocketError(f"app-server WebSocket handshake refused: {status}")
            sock.settimeout(None)
            return connection
        except BaseException:
            sock.close()
            raise

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._sock.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise EOFError("app-server WebSocket closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise CodexWebSocketError("app-server closed the connection during the handshake")
            self._buffer += chunk
        head, self._buffer = self._buffer.split(marker, 1)
        return head

    def send_frame(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            self._sock.sendall(encode_client_frame(opcode, payload, self._random(4)))

    def send_text(self, text: str) -> None:
        self.send_frame(_OP_TEXT, text.encode("utf-8"))

    def receive_text(self) -> str | None:
        """Next complete text message, or None once the server closes."""

        fragments: list[bytes] = []
        while True:
            try:
                first, second = self._read_exact(2)
            except (EOFError, OSError):
                self.closed = True
                return None
            final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else b""
            payload = self._read_exact(length) if length else b""
            if mask:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == _OP_PING:
                self.send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                self.closed = True
                return None
            if opcode in (_OP_TEXT, _OP_CONTINUATION):
                fragments.append(payload)
                if final:
                    return b"".join(fragments).decode("utf-8", errors="replace")

    def close(self) -> None:
        if not self.closed:
            try:
                self.send_frame(_OP_CLOSE, struct.pack("!H", 1000))
            except OSError:
                pass
        self.closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class _LineWriter:
    """Collects the client's newline-terminated JSON and sends one frame per line."""

    def __init__(self, connection: CodexWebSocketConnection) -> None:
        self._connection = connection
        self._pending = ""

    def write(self, data: str) -> int:
        self._pending += data
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._connection.send_text(line)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._connection.close()


class _LineReader:
    def __init__(self, connection: CodexWebSocketConnection) -> None:
        self._connection = connection

    def __iter__(self) -> Iterator[str]:
        while True:
            text = self._connection.receive_text()
            if text is None:
                return
            yield text + "\n"


class CodexAppServerWebSocketProcess:
    """Process-shaped view of one WebSocket connection for CodexAppServerClient."""

    def __init__(self, connection: CodexWebSocketConnection) -> None:
        self.connection = connection
        self.stdin = _LineWriter(connection)
        self.stdout = _LineReader(connection)

    @classmethod
    def connect(cls, url: str, *, timeout: float = 10.0) -> "CodexAppServerWebSocketProcess":
        return cls(CodexWebSocketConnection.connect(url, timeout=timeout))

    def poll(self) -> int | None:
        return 0 if self.connection.closed else None

    def terminate(self) -> None:
        self.connection.close()

    def kill(self) -> None:
        self.connection.close()

    def wait(self, timeout: float | None = None) -> int:
        return 0


__all__ = [
    "CodexAppServerWebSocketProcess",
    "CodexWebSocketConnection",
    "CodexWebSocketError",
    "encode_client_frame",
    "ws_endpoint",
]
