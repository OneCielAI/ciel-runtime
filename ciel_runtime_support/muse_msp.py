"""Muse Session Protocol (MSP) client over a ``muse serve`` stdio host.

Muse Code exposes a stable, versioned session protocol: ``muse serve`` hosts it
over the client's own stdin/stdout as newline-delimited JSON-RPC 2.0, and
``muse schema generate-json-schema`` exports the wire contract embedded in the
binary. That protocol - not terminal keystrokes - is the injection surface an
external program should use, because it carries the parameters a TUI paste
cannot express: an explicit busy disposition (queue/steer/replace), display-only
text, idempotency handles, approval and user-input answers, and a typed event
stream.

This module is deliberately dependency-free and import-free so it can be
executed on either side of the Windows/WSL boundary that Muse Code lives on.
"""

from __future__ import annotations

import json
import os
import queue
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

MSP_SCHEMA_VERSION = 1
MSP_CLIENT_NAME = "ciel_runtime"
# Busy disposition for `turn/start`; the wire default is `queue`.
MSP_IF_BUSY = ("queue", "steer", "replace")
# The wire spells approval modes differently from the CLI flags (`muse
# --approval-mode untrusted|on-request|never`): the host rejected "never" with
# "unknown variant `never`, expected one of allowAll, promptUnmatched,
# onRequest, denyUnmatched" (live 2026-09-19).
MSP_APPROVAL_MODES = ("allowAll", "promptUnmatched", "onRequest", "denyUnmatched")
MSP_APPROVAL_MODE_ALIASES = {
    "never": "allowAll",
    "allowall": "allowAll",
    "on-request": "onRequest",
    "onrequest": "onRequest",
    "untrusted": "promptUnmatched",
    "promptunmatched": "promptUnmatched",
    "deny": "denyUnmatched",
    "denyunmatched": "denyUnmatched",
}
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0


def muse_approval_mode(value: str) -> str:
    """Normalize a CLI spelling onto the wire's approval-mode enum."""

    text = str(value or "").strip()
    if text in MSP_APPROVAL_MODES:
        return text
    resolved = MSP_APPROVAL_MODE_ALIASES.get(text.lower())
    if resolved is None:
        raise ValueError(
            f"approval mode must be one of {', '.join(MSP_APPROVAL_MODES)} "
            f"(or {', '.join(sorted(MSP_APPROVAL_MODE_ALIASES))})"
        )
    return resolved


class MuseMspError(RuntimeError):
    """A JSON-RPC error returned by the Muse host."""

    def __init__(self, code: int | None, message: str, *, kind: str = "") -> None:
        super().__init__(f"MSP error {code}: {message}" if code is not None else message)
        self.code = code
        self.kind = kind
        self.message = message


class MuseMspProtocolError(MuseMspError):
    """The host spoke something this client cannot parse."""


def muse_command_id(*, seed: str | None = None, now_ms: int | None = None) -> str:
    """Mint the SS3.1.1 idempotency handle.

    The wire requires UUIDv7 (time-ordered). ``seed`` only influences the random
    tail, so the same seed produces different ids at different times - callers
    that retry a command must reuse the id they already minted, which is what
    :class:`MuseCommandIds` exists for.
    """

    milliseconds = int(time.time() * 1000) if now_ms is None else int(now_ms)
    rng = random.Random(seed) if seed is not None else random
    tail = rng.getrandbits(74)
    raw = bytearray(16)
    raw[0:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | ((tail >> 68) & 0x0F)
    raw[7] = (tail >> 60) & 0xFF
    raw[8] = 0x80 | ((tail >> 54) & 0x3F)
    for index in range(9, 16):
        raw[index] = (tail >> (8 * (15 - index))) & 0xFF
    hexed = raw.hex()
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"


class MuseCommandIds:
    """Reuse one command id per logical command so retries cannot double-submit.

    The id is derived from the key, so the same message delivered again by a
    restarted process still carries the id the host already saw - the host
    deduplicates the command instead of running it twice.
    """

    def __init__(self) -> None:
        self._ids: dict[str, str] = {}
        self._generations: dict[str, int] = {}
        self._lock = threading.Lock()

    def for_key(self, key: str, *, fresh: bool = False) -> str:
        with self._lock:
            generation = self._generations.get(key, 0)
            if fresh or key not in self._ids:
                if fresh and key in self._ids:
                    generation += 1
                self._generations[key] = generation
                self._ids[key] = muse_command_id(seed=f"{key}#{generation}")
            return self._ids[key]

    def forget(self, key: str) -> None:
        with self._lock:
            self._ids.pop(key, None)
            self._generations.pop(key, None)


@dataclass(frozen=True, slots=True)
class MuseNotification:
    """One server-to-client notification frame."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    emitted_at_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_frame(cls, frame: Mapping[str, Any]) -> "MuseNotification":
        params = frame.get("params")
        emitted = frame.get("emittedAtMs")
        return cls(
            method=str(frame.get("method") or ""),
            params=dict(params) if isinstance(params, Mapping) else {},
            emitted_at_ms=int(emitted) if isinstance(emitted, (int, float)) else None,
            raw=dict(frame),
        )


class MuseMspConnection:
    """One MSP connection: a ``muse serve`` child plus its NDJSON channel."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        log: Callable[[str, str], Any] = lambda _level, _message: None,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._process = process
        self._log = log
        self._timeout = float(request_timeout_seconds)
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._notifications: queue.Queue[MuseNotification] = queue.Queue(maxsize=10_000)
        self._closed = threading.Event()
        self._reader = threading.Thread(
            target=self._read_loop, name="muse-msp-reader", daemon=True
        )
        self._reader.start()
        self.initialize_result: dict[str, Any] | None = None
        self._initialized = False

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
        log: Callable[[str, str], Any] = lambda _level, _message: None,
        request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> "MuseMspConnection":
        """Spawn ``muse serve`` and attach the connection to its stdio."""

        child_env = dict(os.environ if env is None else env)
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
            env=child_env,
        )
        connection = cls(process, log=log, request_timeout_seconds=request_timeout_seconds)
        connection._drain_stderr()
        return connection

    @classmethod
    def for_host_command(
        cls,
        host_command: Sequence[str],
        **kwargs: Any,
    ) -> "MuseMspConnection":
        """Spawn a host built from the explicit command (``muse serve …``)."""

        return cls.start(host_command, **kwargs)

    def _drain_stderr(self) -> None:
        stderr = self._process.stderr

        def pump() -> None:
            if stderr is None:
                return
            for raw in iter(stderr.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip()
                if text:
                    self._log("DEBUG", f"muse_msp_host_stderr {text[:400]}")

        threading.Thread(target=pump, name="muse-msp-stderr", daemon=True).start()

    def close(self, *, timeout: float = 5.0) -> int | None:
        """Close stdin, reap the host, then release the pipes.

        Closing stdout while the reader thread is parked in ``readline`` makes
        the buffered reader raise ``ValueError: PyMemoryView_FromBuffer`` (seen
        live 2026-09-19), so the streams are released only after the process is
        gone and the reader has been let go of its handle.
        """

        self._closed.set()
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return None
        finally:
            self._reader.join(timeout=timeout)
            for stream in (self._process.stdout, self._process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
        return self._process.returncode

    def __enter__(self) -> "MuseMspConnection":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- wire --------------------------------------------------------------

    def _read_loop(self) -> None:
        stream = self._process.stdout
        try:
            if stream is None:
                return
            for raw in iter(stream.readline, b""):
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except (TypeError, ValueError):
                    self._log("WARN", f"muse_msp_unparsed {text[:200]}")
                    continue
                if not isinstance(frame, Mapping):
                    continue
                if "id" in frame and ("result" in frame or "error" in frame):
                    self._deliver_reply(frame)
                    continue
                self._deliver_notification(frame)
        except (OSError, ValueError) as exc:
            self._log("DEBUG", f"muse_msp_reader_stopped {type(exc).__name__}")
        finally:
            self._closed.set()
            with self._pending_lock:
                waiters = list(self._pending.values())
            for waiter in waiters:
                waiter.put({"_closed": True})

    def _deliver_reply(self, frame: Mapping[str, Any]) -> None:
        try:
            reply_id = int(frame.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        with self._pending_lock:
            waiter = self._pending.pop(reply_id, None)
        if waiter is not None:
            waiter.put(dict(frame))

    def _deliver_notification(self, frame: Mapping[str, Any]) -> None:
        notification = MuseNotification.from_frame(frame)
        if not notification.method:
            return
        try:
            self._notifications.put_nowait(notification)
        except queue.Full:
            self._log("WARN", "muse_msp_notification_dropped queue_full")

    def _write(self, frame: Mapping[str, Any]) -> None:
        stdin = self._process.stdin
        if stdin is None or self._closed.is_set():
            raise MuseMspError(None, "MSP connection is closed")
        line = json.dumps(dict(frame), ensure_ascii=False, separators=(",", ":"))
        try:
            stdin.write(line.encode("utf-8") + b"\n")
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MuseMspError(None, f"MSP host is not writable: {exc}") from exc

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._id_lock:
            self._next_id += 1
            request_id = self._next_id
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = waiter
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            frame["params"] = dict(params)
        self._write(frame)
        deadline = self._timeout if timeout is None else float(timeout)
        try:
            reply = waiter.get(timeout=deadline)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise MuseMspError(None, f"MSP request timed out: {method}") from None
        if reply.get("_closed"):
            raise MuseMspError(None, f"MSP host closed during {method}")
        error = reply.get("error")
        if isinstance(error, Mapping):
            data = error.get("data")
            kind = str(data.get("kind") or "") if isinstance(data, Mapping) else ""
            code = error.get("code")
            raise MuseMspError(
                int(code) if isinstance(code, (int, float)) else None,
                str(error.get("message") or "MSP request failed"),
                kind=kind,
            )
        result = reply.get("result")
        return dict(result) if isinstance(result, Mapping) else {}

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            frame["params"] = dict(params)
        self._write(frame)

    # -- handshake ---------------------------------------------------------

    def initialize(
        self,
        *,
        client_name: str = MSP_CLIENT_NAME,
        client_version: str = "1.0",
        client_title: str | None = None,
        requested_capabilities: Iterable[str] = (),
        opt_out_notifications: Iterable[str] = (),
        experimental: bool = False,
        user_input_dialogs: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Open the connection and close the handshake with ``initialized``.

        The host enforces ``clientInfo.name`` matching ``^[a-z0-9_]+$`` and
        refuses every other request until the ``initialized`` notification
        arrives (SS1.4).
        """

        client_info: dict[str, Any] = {"name": client_name, "version": client_version}
        if client_title:
            client_info["title"] = client_title
        capabilities: dict[str, Any] = {}
        requested = sorted({str(value) for value in requested_capabilities if str(value)})
        if requested:
            capabilities["requestedCapabilities"] = requested
        opted_out = [str(value) for value in opt_out_notifications if str(value)]
        if opted_out:
            capabilities["optOutNotificationMethods"] = opted_out
        if experimental:
            capabilities["experimentalApi"] = True
        if user_input_dialogs:
            capabilities["userInputDialogs"] = True
        params: dict[str, Any] = {"clientInfo": client_info}
        if capabilities:
            params["capabilities"] = capabilities
        result = self.request("initialize", params, timeout=timeout)
        self.initialize_result = result
        self.notify("initialized")
        self._initialized = True
        return result

    def require_initialized(self) -> None:
        if not self._initialized:
            raise MuseMspError(None, "initialize() must be called before session work")

    # -- session commands --------------------------------------------------

    def session_start(
        self,
        *,
        command_id: str,
        workspace_root: str | os.PathLike[str] | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        approval_mode: str | None = None,
        session_id: str | None = None,
        config: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"commandId": command_id}
        if workspace_root is not None:
            params["workspaceRoot"] = str(workspace_root)
        if provider_id:
            params["providerId"] = provider_id
        if model_id:
            params["modelId"] = model_id
        if approval_mode:
            params["approvalMode"] = muse_approval_mode(approval_mode)
        if session_id:
            params["sessionId"] = session_id
        if config:
            params["config"] = dict(config)
        return self.request("session/start", params, timeout=timeout)

    def session_resume(
        self,
        *,
        command_id: str,
        session_id: str,
        cursor: str | None = None,
        exclude_items: bool = False,
        history: str | None = None,
        config: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"commandId": command_id, "sessionId": session_id}
        if cursor is not None:
            params["cursor"] = cursor
        if exclude_items:
            params["excludeItems"] = True
        if history:
            params["history"] = history
        if config:
            params["config"] = dict(config)
        return self.request("session/resume", params, timeout=timeout)

    def turn_start(
        self,
        *,
        command_id: str,
        session_id: str,
        text: str | None = None,
        parts: Sequence[Mapping[str, Any]] | None = None,
        display_text: str | None = None,
        if_busy: str | None = None,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Submit user input; ``if_busy`` selects queue, steer, or replace."""

        if if_busy is not None and if_busy not in MSP_IF_BUSY:
            raise ValueError(f"if_busy must be one of {', '.join(MSP_IF_BUSY)}")
        payload: list[dict[str, Any]] = []
        if parts is not None:
            payload.extend(dict(part) for part in parts)
        if text is not None:
            payload.append({"type": "text", "text": text})
        if not payload:
            raise ValueError("turn/start requires non-empty input")
        params: dict[str, Any] = {
            "commandId": command_id,
            "sessionId": session_id,
            "input": payload,
        }
        if display_text:
            params["displayText"] = display_text
        if if_busy is not None:
            params["ifBusy"] = if_busy
        if reasoning_effort:
            params["reasoningEffort"] = reasoning_effort
        return self.request("turn/start", params, timeout=timeout)

    def turn_steer(
        self,
        *,
        command_id: str,
        session_id: str,
        expected_turn_id: str,
        text: str | None = None,
        parts: Sequence[Mapping[str, Any]] | None = None,
        reasoning_effort: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        payload: list[dict[str, Any]] = []
        if parts is not None:
            payload.extend(dict(part) for part in parts)
        if text is not None:
            payload.append({"type": "text", "text": text})
        if not payload:
            raise ValueError("turn/steer requires non-empty input")
        params: dict[str, Any] = {
            "commandId": command_id,
            "sessionId": session_id,
            "expectedTurnId": expected_turn_id,
            "input": payload,
        }
        if reasoning_effort:
            params["reasoningEffort"] = reasoning_effort
        return self.request("turn/steer", params, timeout=timeout)

    def turn_interrupt(
        self,
        *,
        command_id: str,
        session_id: str,
        turn_id: str | None = None,
        retract: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"commandId": command_id, "sessionId": session_id}
        if turn_id:
            params["turnId"] = turn_id
        if retract:
            params["retract"] = True
        return self.request("turn/interrupt", params, timeout=timeout)

    def approval_decide(
        self,
        *,
        command_id: str,
        session_id: str,
        approval_id: str,
        choice_id: str,
        requirement_id: str | None = None,
        feedback: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "commandId": command_id,
            "sessionId": session_id,
            "approvalId": approval_id,
            "choiceId": choice_id,
        }
        if requirement_id:
            params["requirementId"] = requirement_id
        if feedback is not None:
            params["feedback"] = feedback
        return self.request("approval/decide", params, timeout=timeout)

    def user_input_answer(
        self,
        *,
        command_id: str,
        session_id: str,
        user_input_id: str,
        answers: Sequence[Mapping[str, Any]],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "commandId": command_id,
            "sessionId": session_id,
            "userInputId": user_input_id,
            "answers": [dict(answer) for answer in answers],
        }
        return self.request("userInput/answer", params, timeout=timeout)

    # -- notifications -----------------------------------------------------

    def next_notification(self, *, timeout: float | None = None) -> MuseNotification | None:
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def notifications(
        self,
        *,
        until: Callable[[MuseNotification], bool] | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> Iterator[MuseNotification]:
        """Yield notifications until ``until`` matches (or a deadline passes)."""

        end = None
        if deadline is not None:
            end = float(deadline)
        elif timeout is not None:
            end = time.monotonic() + float(timeout)
        while not self._closed.is_set():
            remaining = None if end is None else max(0.0, end - time.monotonic())
            if remaining is not None and remaining <= 0:
                return
            notification = self.next_notification(timeout=remaining)
            if notification is None:
                if remaining is not None:
                    return
                continue
            yield notification
            if until is not None and until(notification):
                return

    def wait_for(
        self,
        method: str,
        *,
        predicate: Callable[[MuseNotification], bool] | None = None,
        timeout: float = 60.0,
    ) -> MuseNotification | None:
        for notification in self.notifications(timeout=timeout):
            if notification.method != method:
                continue
            if predicate is None or predicate(notification):
                return notification
        return None

    def drain_notifications(self) -> list[MuseNotification]:
        drained: list[MuseNotification] = []
        while True:
            try:
                drained.append(self._notifications.get_nowait())
            except queue.Empty:
                return drained


__all__ = [
    "MSP_APPROVAL_MODES",
    "MSP_CLIENT_NAME",
    "MSP_IF_BUSY",
    "MSP_APPROVAL_MODE_ALIASES",
    "MSP_SCHEMA_VERSION",
    "MuseCommandIds",
    "MuseMspConnection",
    "MuseMspError",
    "MuseMspProtocolError",
    "MuseNotification",
    "muse_approval_mode",
    "muse_command_id",
]
