"""Claude Code cross-session socket transport for admitted runtime inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import posixpath
import secrets
import socket
import threading
import time
import uuid
from typing import Any, Callable


from .claude_socket_paths import canonical_socket_path, session_key_hash


SESSION_KEY_LIMIT = 4096
TOKEN_HEX_LENGTH = 32
# After a failed connect the socket counts as unavailable for this long, so the
# terminal path delivers instead of every message waiting on a dead socket.
CONNECT_FAILURE_COOLDOWN_SECONDS = 30.0


class SocketConnectFailed(OSError):
    """Connection failed before any payload bytes were written."""


class SessionHandoffPending(RuntimeError):
    """No transport may fall back to the parked original while it hands off."""


def generated_socket_path(platform_name: str | None = None) -> str:
    platform_name = platform_name or os.name
    nonce = secrets.token_hex(16)
    if platform_name == "nt":
        return rf"\\.\pipe\LOCAL\cc-msg-{nonce}"
    uid = os.getuid() if hasattr(os, "getuid") else 0
    temp_root = os.environ.get("TMPDIR") or "/tmp"
    if not temp_root.startswith("/"):
        temp_root = "/tmp"
    return posixpath.join(temp_root, f"cc-socks-{uid}", f"ciel-{nonce}.sock")


def prepared_socket_path(config: dict[str, Any], passthrough: list[str]) -> str:
    for index, value in enumerate(passthrough):
        if value == "--messaging-socket-path" and index + 1 < len(passthrough):
            return str(passthrough[index + 1])
        text = str(value)
        if text.startswith("--messaging-socket-path="):
            return text.partition("=")[2]
    claude_config = config.get("claude_code") if isinstance(config, dict) else {}
    if isinstance(claude_config, dict) and claude_config.get("session_socket_input") is False:
        return ""
    return generated_socket_path()


class ClaudeSessionSocketClient:
    """Delivers through Claude Code's ``--messaging-socket-path``.

    The socket is usable only while Claude Code has published a session key
    for it (``~/.claude/sessions/<pid>.<hash>.key``) and accepts connections.
    Emily (2026-10-04): after a relaunch no key existed for 30 hours and every
    message waited silently, so ``available()`` now requires the key and a
    recent successful connect, letting the terminal path take over, and the
    change of state is logged at ERROR.
    """

    def __init__(
        self,
        home: Path,
        log: Callable[[str, str], None],
        *,
        platform_name: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._home = home
        self._log = log
        self._platform_name = platform_name or os.name
        self._clock = clock
        self._target = ""
        self._published_target = False
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        self._unavailable_reason = ""

    @property
    def target(self) -> str:
        from .claude_session_handoff import socket_handoff
        with self._lock:
            configured = self._target
            published = self._published_target
        if not configured:
            return ""
        directory = Path(os.environ.get("CLAUDE_CONFIG_DIR") or self._home / ".claude") / "sessions"
        handoff = socket_handoff(directory, configured)
        if handoff.pending or (published and not handoff.record):
            raise SessionHandoffPending("native background handoff is not yet verified")
        with self._lock:
            if self._target != configured:
                raise SessionHandoffPending("native socket configuration changed during resolution")
            self._published_target = published or bool(handoff.record)
        return str(handoff.record.get("messagingSocketPath") or configured)

    def configure(self, path: str | None) -> None:
        target = str(path or "").strip()
        with self._lock:
            self._target = target
            self._published_target = False
            self._blocked_until = 0.0
            self._unavailable_reason = ""
        self._log(
            "INFO",
            "claude_session_socket_%s%s"
            % ("configured" if target else "cleared", f" target={target}" if target else ""),
        )

    def available(self) -> bool:
        try:
            target = self.target
        except SessionHandoffPending:
            # Reserve the whole-message transport; unavailable would type into
            # the parked interactive session instead of waiting for its peer.
            return True
        if not target:
            return False
        with self._lock:
            continued = canonical_socket_path(target, self._platform_name) != canonical_socket_path(self._target, self._platform_name)
        if self._clock() < self._blocked_until:
            return continued
        if not self._read_peer_token(target):
            self._mark_unavailable("auth_key_unavailable", target)
            return continued
        self._mark_available(target)
        return True

    def _mark_unavailable(self, reason: str, target: str) -> None:
        with self._lock:
            changed = self._unavailable_reason != reason
            self._unavailable_reason = reason
        if changed:
            self._log(
                "ERROR",
                f"claude_session_socket_unavailable reason={reason} target={target} "
                "fallback=terminal",
            )

    def _mark_available(self, target: str) -> None:
        with self._lock:
            was = self._unavailable_reason
            self._unavailable_reason = ""
        if was:
            self._log("INFO", f"claude_session_socket_available target={target} after={was}")

    def send(self, prompt: str, _messages: list[dict[str, Any]] | None = None) -> bool:
        target = self.target
        if not target:
            self._log("INFO", "claude_session_socket_deferred reason=target_unconfigured")
            return False
        with self._lock:
            continued = canonical_socket_path(target, self._platform_name) != canonical_socket_path(self._target, self._platform_name)
        token = self._read_peer_token(target)
        if not token:
            if continued:
                raise SessionHandoffPending("background peer key is not yet available")
            self._log("INFO", "claude_session_socket_deferred reason=auth_key_unavailable")
            self._mark_unavailable("auth_key_unavailable", target)
            return False
        frames = (
            json.dumps({"type": "auth", "token": token}, separators=(",", ":"))
            + "\n"
            + json.dumps(
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "message": {"role": "user", "content": prompt},
                    "priority": "next",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            self._write(target, frames)
        except OSError as exc:
            self._log(
                "INFO",
                f"claude_session_socket_deferred reason=connect_failed error={type(exc).__name__}: {exc}",
            )
            with self._lock:
                self._blocked_until = self._clock() + CONNECT_FAILURE_COOLDOWN_SECONDS
            self._mark_unavailable("connect_failed", target)
            if continued and isinstance(exc, SocketConnectFailed):
                raise SessionHandoffPending("background peer connection is not yet available") from exc
            if continued:
                # A write may have reached the peer. Never replay into the
                # parked TTY or claim that this was definitely unsubmitted.
                raise RuntimeError("background peer socket write uncertain") from exc
            return False
        self._log(
            "INFO",
            f"claude_session_socket_injected bytes={len(frames)} target={target}",
        )
        self._mark_available(target)
        return True

    def _read_peer_token(self, target: str) -> str:
        digest = session_key_hash(target, self._platform_name)
        directory = Path(os.environ.get("CLAUDE_CONFIG_DIR") or self._home / ".claude") / "sessions"
        try:
            candidates = sorted(
                directory.glob(f"*.{digest}.key"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return ""
        for path in candidates:
            try:
                if path.stat().st_size > SESSION_KEY_LIMIT:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            token = str(payload.get("peerToken") or "") if isinstance(payload, dict) else ""
            if len(token) == TOKEN_HEX_LENGTH and all(char in "0123456789abcdefABCDEF" for char in token):
                return token
        return ""

    def _write(self, target: str, frames: bytes) -> None:
        if self._platform_name == "nt":
            try:
                stream = open(target, "wb", buffering=0)
            except OSError as exc:
                raise SocketConnectFailed(str(exc)) from exc
            with stream:
                stream.write(frames)
            return
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(2.0)
            try:
                client.connect(target)
            except OSError as exc:
                raise SocketConnectFailed(str(exc)) from exc
            client.sendall(frames)
            client.shutdown(socket.SHUT_WR)
        finally:
            client.close()


__all__ = [
    "ClaudeSessionSocketClient",
    "canonical_socket_path",
    "generated_socket_path",
    "prepared_socket_path",
    "session_key_hash",
]
