"""Loopback callback receiver for the Z.AI authorization-code flow."""

from __future__ import annotations

import hmac
import socket
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ZAI_OAUTH_CALLBACK_HOST = "127.0.0.1"
ZAI_OAUTH_CALLBACK_PUBLIC_HOST = "localhost"
ZAI_OAUTH_CALLBACK_PORT = 9899
ZAI_OAUTH_CALLBACK_PATH = "/callback"
ZAI_OAUTH_CALLBACK_REDIRECT_URI = "http://localhost:9899/callback"
_MAX_REQUEST_TARGET_BYTES = 8_192


class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


@dataclass(slots=True)
class ZaiOAuthLocalCallbackReceiver:
    """Receive one state-bound OAuth callback on a loopback-only HTTP server."""

    expected_state: str
    timeout_seconds: float
    host: str = ZAI_OAUTH_CALLBACK_HOST
    port: int = ZAI_OAUTH_CALLBACK_PORT
    public_host: str = ZAI_OAUTH_CALLBACK_PUBLIC_HOST
    path: str = ZAI_OAUTH_CALLBACK_PATH
    _server: _ExclusiveThreadingHTTPServer | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _callback_url: str = field(default="", init=False)
    _callback_ready: threading.Event = field(default_factory=threading.Event, init=False)
    _callback_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    @property
    def redirect_uri(self) -> str:
        port = self._server.server_port if self._server is not None else self.port
        return f"http://{self.public_host}:{port}{self.path}"

    def __enter__(self) -> ZaiOAuthLocalCallbackReceiver:
        receiver = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
                receiver._handle_get(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        try:
            self._server = _ExclusiveThreadingHTTPServer((self.host, self.port), CallbackHandler)
        except OSError as exc:
            raise RuntimeError(
                f"Z.AI OAuth callback listener could not bind to {self.host}:{self.port}."
            ) from exc
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="ciel-zai-oauth-callback",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        server, thread = self._server, self._thread
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        self._server = None
        self._thread = None

    def wait(self) -> str:
        if self._server is None:
            raise RuntimeError("Z.AI OAuth callback listener was not started.")
        if not self._callback_ready.wait(max(0.0, self.timeout_seconds)):
            raise RuntimeError(
                f"Z.AI OAuth localhost callback timed out after {int(self.timeout_seconds)} seconds."
            )
        with self._callback_lock:
            callback_url = self._callback_url
        if not callback_url:
            raise RuntimeError("Z.AI OAuth localhost callback was empty.")
        return callback_url

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if len(handler.path.encode("utf-8", errors="ignore")) > _MAX_REQUEST_TARGET_BYTES:
            self._respond(handler, 414, "OAuth callback request was too large.")
            return
        try:
            parsed = urllib.parse.urlsplit(handler.path)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        except ValueError:
            self._respond(handler, 400, "Invalid OAuth callback request.")
            return
        if parsed.path != self.path:
            self._respond(handler, 404, "OAuth callback path was not found.")
            return
        state = str((query.get("state") or [""])[0])
        if not state or not hmac.compare_digest(state, self.expected_state):
            self._respond(handler, 400, "OAuth callback state did not match.")
            return
        with self._callback_lock:
            if self._callback_url:
                self._respond(handler, 409, "OAuth callback was already received.")
                return
            self._callback_url = (
                f"{self.redirect_uri}?{parsed.query}" if parsed.query else self.redirect_uri
            )
        self._respond(
            handler,
            200,
            "Z.AI authorization received. You may close this tab and return to Ciel Runtime.",
        )
        self._callback_ready.set()

    @staticmethod
    def _respond(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
        body = (
            "<!doctype html><meta charset=\"utf-8\"><title>Ciel Runtime OAuth</title>"
            f"<p>{message}</p>"
        ).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


__all__ = [
    "ZAI_OAUTH_CALLBACK_HOST",
    "ZAI_OAUTH_CALLBACK_PATH",
    "ZAI_OAUTH_CALLBACK_PORT",
    "ZAI_OAUTH_CALLBACK_PUBLIC_HOST",
    "ZAI_OAUTH_CALLBACK_REDIRECT_URI",
    "ZaiOAuthLocalCallbackReceiver",
]
