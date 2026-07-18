from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


class CodexAppServerError(RuntimeError):
    """Raised when the Codex app-server JSON-RPC transport fails."""


@dataclass(frozen=True)
class CodexAppServerState:
    thread_id: str | None = None
    active_turn_id: str | None = None
    last_turn_status: str | None = None


def codex_app_server_has_explicit_transport(args: Iterable[str]) -> bool:
    values = list(args)
    for idx, arg in enumerate(values):
        text = str(arg)
        if text == "--stdio" or text.startswith("--listen="):
            return True
        if text == "--listen" and idx + 1 < len(values):
            return True
    return False


def codex_app_server_has_subcommand(args: Iterable[str]) -> bool:
    values = list(args)
    i = 0
    while i < len(values):
        arg = str(values[i])
        if arg == "--":
            return bool(i + 1 < len(values))
        if arg in ("-c", "--config", "--listen", "--enable", "--disable", "--ws-auth", "--ws-token-file", "--ws-token-sha256", "--ws-shared-secret-file", "--ws-issuer", "--ws-audience", "--ws-max-clock-skew-seconds"):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return arg in {"daemon", "proxy", "generate-ts", "generate-json-schema", "help"}
    return False


def codex_app_server_launch_args(
    passthrough: Iterable[str],
    *,
    config_args: Iterable[str] = (),
    default_listen_url: str | None = None,
) -> list[str]:
    args = [str(arg) for arg in passthrough]
    out = ["app-server", *[str(arg) for arg in config_args]]
    if default_listen_url and not codex_app_server_has_explicit_transport(args) and not codex_app_server_has_subcommand(args):
        out.extend(["--listen", default_listen_url])
    out.extend(args)
    return out


def command_for_popen(executable: str, args: Iterable[str]) -> list[str]:
    argv = [str(executable), *[str(arg) for arg in args]]
    if os.name == "nt" and str(executable).lower().endswith((".cmd", ".bat")):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(argv)]
    return argv


def text_user_input(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def responses_user_message_item(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


def _omit_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class CodexAppServerClient:
    """Small JSON-RPC client for `codex app-server` stdio.

    Codex app-server intentionally omits the JSON-RPC `"jsonrpc": "2.0"`
    field on the wire. Requests are still matched by `id`.
    """

    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.process = process
        self._now = now
        self._next_id = 1
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader_error: BaseException | None = None
        self._state = CodexAppServerState()
        if process.stdout is None or process.stdin is None:
            raise CodexAppServerError("codex app-server process must have stdin/stdout pipes")
        self._reader = threading.Thread(target=self._read_loop, name="codex-app-server-reader", daemon=True)
        self._reader.start()

    @classmethod
    def spawn(
        cls,
        codex_executable: str,
        args: Iterable[str] = (),
        *,
        env: dict[str, str] | None = None,
        cwd: str | os.PathLike[str] | None = None,
    ) -> "CodexAppServerClient":
        cmd = command_for_popen(codex_executable, ["app-server", "--stdio", *[str(arg) for arg in args]])
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(cwd) if cwd is not None else None,
            bufsize=1,
        )
        return cls(proc)

    @property
    def state(self) -> CodexAppServerState:
        with self._lock:
            return self._state

    def close(self, timeout: float = 2.0) -> None:
        """Close pipes and reap the child, surfacing cleanup failures."""

        errors: list[str] = []
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except Exception as exc:
            errors.append(f"stdin close failed: {type(exc).__name__}: {exc}")
        if self.process.poll() is None:
            try:
                self.process.terminate()
            except Exception as exc:
                errors.append(f"terminate failed: {type(exc).__name__}: {exc}")
            try:
                self.process.wait(timeout=max(0.0, timeout))
            except subprocess.TimeoutExpired:
                try:
                    self.process.kill()
                    self.process.wait(timeout=max(0.0, timeout))
                except Exception as exc:
                    errors.append(f"kill failed: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.append(f"wait failed: {type(exc).__name__}: {exc}")
        if errors:
            raise CodexAppServerError("; ".join(errors))

    def initialize(
        self,
        *,
        client_name: str,
        client_title: str | None,
        client_version: str,
        experimental_api: bool = True,
        request_attestation: bool = False,
    ) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": client_name,
                    "title": client_title,
                    "version": client_version,
                },
                "capabilities": {
                    "experimentalApi": bool(experimental_api),
                    "requestAttestation": bool(request_attestation),
                },
            },
        )
        self.notify("initialized")
        return result

    def start_thread(
        self,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        permissions: str | None = None,
        approval_policy: str | None = None,
    ) -> dict[str, Any]:
        result = self.request(
            "thread/start",
            _omit_none(
                {
                    "cwd": str(cwd) if cwd is not None else None,
                    "model": model,
                    "modelProvider": model_provider,
                    "permissions": permissions,
                    "approvalPolicy": approval_policy,
                }
            ),
        )
        thread_id = _thread_id_from_response(result)
        if thread_id:
            with self._lock:
                self._state = CodexAppServerState(
                    thread_id=thread_id,
                    active_turn_id=self._state.active_turn_id,
                    last_turn_status=self._state.last_turn_status,
                )
        return result

    def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        model: str | None = None,
        model_provider: str | None = None,
        exclude_turns: bool = True,
    ) -> dict[str, Any]:
        result = self.request(
            "thread/resume",
            _omit_none(
                {
                    "threadId": thread_id,
                    "cwd": str(cwd) if cwd is not None else None,
                    "model": model,
                    "modelProvider": model_provider,
                    "excludeTurns": exclude_turns,
                }
            ),
        )
        with self._lock:
            self._state = CodexAppServerState(
                thread_id=thread_id,
                active_turn_id=self._state.active_turn_id,
                last_turn_status=self._state.last_turn_status,
            )
        return result

    def turn_start(
        self,
        thread_id: str,
        text: str,
        *,
        cwd: str | os.PathLike[str] | None = None,
        client_user_message_id: str | None = None,
        responsesapi_client_metadata: dict[str, str] | None = None,
        model: str | None = None,
        permissions: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "turn/start",
            _omit_none(
                {
                    "threadId": thread_id,
                    "clientUserMessageId": client_user_message_id,
                    "input": [text_user_input(text)],
                    "responsesapiClientMetadata": responsesapi_client_metadata,
                    "cwd": str(cwd) if cwd is not None else None,
                    "model": model,
                    "permissions": permissions,
                }
            ),
        )

    def turn_steer(
        self,
        thread_id: str,
        expected_turn_id: str,
        text: str,
        *,
        client_user_message_id: str | None = None,
        responsesapi_client_metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "turn/steer",
            _omit_none(
                {
                    "threadId": thread_id,
                    "clientUserMessageId": client_user_message_id,
                    "input": [text_user_input(text)],
                    "responsesapiClientMetadata": responsesapi_client_metadata,
                    "expectedTurnId": expected_turn_id,
                }
            ),
        )

    def compact_thread(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/compact/start", {"threadId": thread_id})

    def inject_user_message_item(self, thread_id: str, text: str) -> dict[str, Any]:
        return self.request("thread/inject_items", {"threadId": thread_id, "items": [responses_user_message_item(text)]})

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write_json(payload)
        deadline = self._now() + timeout
        with self._condition:
            while True:
                if request_id in self._responses:
                    response = self._responses.pop(request_id)
                    break
                if self._reader_error is not None:
                    raise CodexAppServerError(f"codex app-server reader failed: {self._reader_error}") from self._reader_error
                remaining = deadline - self._now()
                if remaining <= 0:
                    raise CodexAppServerError(f"timed out waiting for codex app-server response to {method}")
                self._condition.wait(timeout=remaining)
        if "error" in response:
            raise CodexAppServerError(f"codex app-server {method} failed: {response['error']}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._write_json(payload)

    def next_notification(self, *, timeout: float = 0.0) -> dict[str, Any] | None:
        try:
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def _write_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        stdin = self.process.stdin
        if stdin is None:
            raise CodexAppServerError("codex app-server stdin is closed")
        stdin.write(data)
        stdin.flush()

    def _read_loop(self) -> None:
        try:
            stdout = self.process.stdout
            if stdout is None:
                return
            for line in stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    self._reader_error = exc
                    with self._condition:
                        self._condition.notify_all()
                    return
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int):
                    with self._condition:
                        self._responses[request_id] = message
                        self._condition.notify_all()
                    continue
                self._track_notification(message)
                self._notifications.put(message)
        except BaseException as exc:
            self._reader_error = exc
            with self._condition:
                self._condition.notify_all()

    def _track_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(params, dict):
            return
        with self._lock:
            thread_id = self._state.thread_id
            active_turn_id = self._state.active_turn_id
            last_turn_status = self._state.last_turn_status
            if method in ("thread/started", "thread/status/changed", "turn/started", "turn/completed"):
                candidate_thread_id = params.get("threadId")
                if isinstance(candidate_thread_id, str) and candidate_thread_id:
                    thread_id = candidate_thread_id
            turn = params.get("turn")
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                status = turn.get("status")
                if isinstance(status, str):
                    last_turn_status = status
                if method == "turn/started" and isinstance(turn_id, str) and turn_id:
                    active_turn_id = turn_id
                elif method == "turn/completed":
                    active_turn_id = None
            self._state = CodexAppServerState(
                thread_id=thread_id,
                active_turn_id=active_turn_id,
                last_turn_status=last_turn_status,
            )


def _thread_id_from_response(result: dict[str, Any]) -> str | None:
    thread = result.get("thread")
    if isinstance(thread, dict):
        thread_id = thread.get("id")
        if isinstance(thread_id, str) and thread_id:
            return thread_id
    thread_id = result.get("threadId")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    path = result.get("path")
    if isinstance(path, (str, Path)) and str(path):
        return None
    return None


__all__ = [
    "CodexAppServerClient",
    "CodexAppServerError",
    "CodexAppServerState",
    "codex_app_server_has_explicit_transport",
    "codex_app_server_has_subcommand",
    "codex_app_server_launch_args",
    "command_for_popen",
    "responses_user_message_item",
    "text_user_input",
]
