"""``ciel-runtimectl session-api-key``: memory-only keys for one running session.

    session-api-key list
    session-api-key inspect --session claude:<id> [--provider P]
    session-api-key inject  --session claude:<id> --provider P [--ttl-seconds N] [--stdin | --fd N]
    session-api-key replace --session claude:<id> --provider P --expected-version N [--stdin | --fd N]
    session-api-key clear   --session claude:<id> --provider P --expected-version N

The key is read from a hidden prompt (a terminal), standard input (``--stdin``)
or an inherited file descriptor (``--fd N``); there is no option that takes the
key itself, so it never appears in a process listing.  The command talks only
to this workspace's loopback router (session_api_key_client) and prints
metadata: session, provider, source, version, times and fingerprint.  Keys set
here are not stored; ``api-key``, ``set-api-key`` and ``set-api-keys`` remain
the persistent commands.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from .session_api_key_client import SessionApiKeyClient, SessionApiKeyClientError
from .session_api_key_http import PERSISTENT_NOTE
from .session_api_key_vault import MAX_KEY_LENGTH

ACTIONS = ("list", "inspect", "inject", "replace", "clear", "reset-state")


def stdin_hidden_prompt_available(stream: Any = None) -> bool:
    """True only when stdin is a real terminal suitable for ``getpass``.

    On Windows ``sys.stdin.isatty()`` is also true for the ``NUL`` device.
    ``getpass.getpass`` then waits forever.  A Console input handle is the
    positive proof: ``GetConsoleMode(GetStdHandle(STD_INPUT_HANDLE))`` must
    succeed.  On other platforms, ``isatty`` is the platform's terminal proof.
    """

    stream = sys.stdin if stream is None else stream
    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError):
        return False
    if os.name != "nt":
        return True
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        handle = kernel32.GetStdHandle(-10 & 0xFFFFFFFF)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        return bool(handle and handle != wintypes.HANDLE(-1).value
                    and kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (AttributeError, OSError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class SessionApiKeyCliIO:
    write_line: Callable[[str], None]
    stdin_isatty: Callable[[], bool]
    prompt_secret: Callable[[str], str]
    read_stdin: Callable[[int], str]
    read_fd: Callable[[int, int], bytes] = lambda fd, limit: os.read(fd, limit)


def _when(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
    except (TypeError, ValueError):
        return "-"


def metadata_line(row: dict[str, Any]) -> str:
    line = (f"session={row.get('session')} provider={row.get('provider')} source={row.get('source')} "
            f"version={row.get('version')} state={row.get('state', 'active')} "
            f"fallback={row.get('fallback', 'fail_closed')} injected_at={_when(row.get('injected_at'))} "
            f"expires_at={_when(row.get('expires_at'))} fingerprint={row.get('fingerprint') or '-'}")
    if row.get("notice"):
        line += f" [{row['notice']}; age {int(row.get('age_seconds') or 0)}s]"
    return line


class _Refused(Exception):
    """A refusal before anything was sent: text mode exits with the message, --json prints one error object."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Row fields a JSON answer may carry: metadata only, never a key.
_ROW_FIELDS = ("session", "provider", "source", "version", "state", "fallback", "cause", "injected_at",
               "expires_at", "lost_at", "fingerprint", "notice", "age_seconds", "end_proof", "runtime",
               "last_activity", "launcher_pid", "staged_at", "abandoned_at")


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [{name: row[name] for name in _ROW_FIELDS if name in row} for row in rows or [] if isinstance(row, dict)]


class SessionApiKeyCli:
    def __init__(self, client: Callable[[], SessionApiKeyClient], io: SessionApiKeyCliIO) -> None:
        self.client, self.io = client, io
        self._json = False

    def run(self, args: Any) -> int:
        action = str(getattr(args, "action", "") or "list")
        if action not in ACTIONS:
            raise SystemExit(f"session-api-key: unknown action {action!r}; use one of {', '.join(ACTIONS)}")
        self._json = bool(getattr(args, "json", False))
        try:
            if action == "list":
                return self._list()
            if action == "inspect":
                return self._inspect(args)
            if action == "clear":
                return self._clear(args)
            if action == "reset-state":
                return self._reset_state(args)
            return self._put(args, action)
        except _Refused as exc:
            if self._json:
                self._emit({"ok": False, "command": action, "error": exc.code, "message": str(exc)})
                return 2
            raise SystemExit(str(exc)) from None
        except SessionApiKeyClientError as exc:
            if self._json:
                self._emit({"ok": False, "command": action, "error": exc.code, "message": str(exc)})
            else:
                self.io.write_line(f"session-api-key {action}: {exc.code}: {exc}")
            return 1

    def _emit(self, payload: dict[str, Any]) -> None:
        self.io.write_line(json.dumps(payload, sort_keys=True))

    # -- reading ---------------------------------------------------------------
    def _list(self) -> int:
        state = self.client().state()
        health = state.get("health") or {}
        if self._json:
            self._emit({"ok": True, "command": "list", "notices": list(state.get("notices") or []),
                        "health": {k: health.get(k) for k in ("rows", "limit", "state_unreadable") if k in health},
                        "sessions": [{**_rows([row])[0], "keys": _rows(row.get("keys"))}
                                     for row in state.get("sessions") or [] if isinstance(row, dict)],
                        "losses": _rows(state.get("losses")), "staged": _rows(state.get("staged")),
                        "abandoned": _rows(state.get("abandoned")),
                        "persistent_note": str(state.get("persistent_note") or PERSISTENT_NOTE)})
            return 0
        for notice in state.get("notices") or []:
            self.io.write_line(f"NOTICE: {notice}")
        if health:
            self.io.write_line(f"records: {health.get('rows')} of {health.get('limit')}")
        sessions = state.get("sessions") or []
        if not sessions:
            self.io.write_line("No sessions have used this router recently.")
        for row in sessions:
            self.io.write_line(f"{row.get('session')}  runtime={row.get('runtime')} provider={row.get('provider') or '-'} "
                               f"last_activity={_when(row.get('last_activity'))}")
            for key in row.get("keys") or []:
                self.io.write_line(f"  session key: {metadata_line(key)}")
        for row in state.get("staged") or []:
            self.io.write_line(f"staged, awaiting first request: provider={row.get('provider')} source={row.get('source')} "
                               f"launcher_pid={row.get('launcher_pid')} expires_at={_when(row.get('expires_at'))}")
        for row in state.get("abandoned") or []:
            self.io.write_line(f"abandoned: scope rebound by another launch: provider={row.get('provider')} "
                               f"source={row.get('source')} launcher_pid={row.get('launcher_pid')} "
                               f"at={_when(row.get('abandoned_at'))}")
        self.io.write_line(str(state.get("persistent_note") or PERSISTENT_NOTE))
        return 0

    def _reset_state(self, args: Any) -> int:
        """Router-wide, so it needs the exact phrase: typed at a terminal, or on stdin with --stdin."""

        reset = (self.client().state().get("reset") or {})
        phrase, warning = str(reset.get("confirm") or ""), str(reset.get("warning") or "")
        if not self._json:
            self.io.write_line(warning)
        if bool(getattr(args, "stdin", False)):
            typed = self.io.read_stdin(len(phrase) + 2)
        elif self.io.stdin_isatty():
            # With --json the warning goes into the prompt (the terminal), so stdout stays one JSON object.
            typed = self.io.prompt_secret(f"{warning}\nType {phrase} to reset: " if self._json
                                          else f"Type {phrase} to reset: ")
        else:
            raise _Refused("no_confirmation_source", "session-api-key reset-state: confirm at a terminal, or pass "
                                                     "the phrase on stdin with --stdin.")
        if typed.strip() != phrase:
            if self._json:
                self._emit({"ok": False, "command": "reset-state", "error": "not_confirmed",
                            "message": "Not confirmed; nothing changed."})
            else:
                self.io.write_line("Not confirmed; nothing changed.")
            return 1
        result = self.client().action({"action": "reset_state", "source": "ctl", "confirm": phrase},
                                      str(getattr(args, "idempotency_key", "") or ""))
        if self._json:
            self._emit({"ok": True, "command": "reset-state", "was_unreadable": bool(result.get("was_unreadable"))})
        else:
            self.io.write_line(f"reset_state: was_unreadable={result.get('was_unreadable')}")
        return 0

    def _inspect(self, args: Any) -> int:
        session = self._session(args)
        provider = str(getattr(args, "provider", "") or "").strip().lower()
        rows = [row for row in self.client().state().get("keys") or []
                if row.get("session") == session and (not provider or row.get("provider") == provider)]
        if self._json:
            self._emit({"ok": bool(rows), "command": "inspect", "keys": _rows(rows),
                        **({} if rows else {"error": "not_found"})})
            return 0 if rows else 1
        if not rows:
            self.io.write_line(f"No session key for {session}{' and ' + provider if provider else ''}.")
            return 1
        for row in rows:
            self.io.write_line(metadata_line(row))
        return 0

    # -- changes -----------------------------------------------------------------
    @staticmethod
    def _session(args: Any) -> str:
        session = str(getattr(args, "session", "") or "").strip()
        if not session:
            raise _Refused("session_required", "session-api-key: --session claude:<id> is required "
                                               "(see `session-api-key list`).")
        return session

    @staticmethod
    def _provider(args: Any) -> str:
        provider = str(getattr(args, "provider", "") or "").strip()
        if not provider:
            raise _Refused("provider_required", "session-api-key: --provider is required.")
        return provider

    def _secret(self, args: Any) -> str:
        use_stdin = bool(getattr(args, "stdin", False))
        fd = getattr(args, "fd", None)
        if use_stdin and fd is not None:
            raise _Refused("one_key_source", "session-api-key: use either --stdin or --fd, not both.")
        if fd is not None:
            chunks, total = [], 0
            while total <= MAX_KEY_LENGTH:
                chunk = self.io.read_fd(int(fd), MAX_KEY_LENGTH + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            value = b"".join(chunks).decode("utf-8", errors="strict")
        elif use_stdin:
            value = self.io.read_stdin(MAX_KEY_LENGTH + 2)
        elif self.io.stdin_isatty():
            value = self.io.prompt_secret("Session API key (input hidden): ")
        else:
            raise _Refused("no_key_source", "session-api-key: no terminal for a hidden prompt; pass the key with "
                                            "--stdin or --fd N.")
        value = value.strip()
        if not value:
            raise _Refused("no_key", "session-api-key: no key given; nothing changed.")
        return value

    def _put(self, args: Any, action: str) -> int:
        session, provider = self._session(args), self._provider(args)
        expected = getattr(args, "expected_version", None)
        if action == "replace" and expected in (None, ""):
            raise _Refused("expected_version_required",
                           "session-api-key replace: --expected-version is required (see `inspect`).")
        body: dict[str, Any] = {"action": action, "session": session, "provider": provider, "source": "ctl",
                                "ttl_seconds": getattr(args, "ttl_seconds", None),
                                "expected_version": 0 if action == "inject" else int(expected),
                                "api_key": self._secret(args)}
        try:
            result = self.client().action(body, str(getattr(args, "idempotency_key", "") or ""))
        finally:
            body["api_key"] = ""
        if self._json:
            self._emit({"ok": True, "command": action, "replayed": bool(result.get("replayed")),
                        "key": _rows([result.get("key") or {}])[0], "memory_only": True})
            return 0
        self.io.write_line(f"{result.get('action')}{' (replayed)' if result.get('replayed') else ''}: "
                           f"{metadata_line(result.get('key') or {})}")
        self.io.write_line("Memory only, for this session and provider; the stored keys are unchanged.")
        return 0

    def _clear(self, args: Any) -> int:
        session, provider = self._session(args), self._provider(args)
        expected = getattr(args, "expected_version", None)
        if expected in (None, ""):
            raise _Refused("expected_version_required",
                           "session-api-key clear: --expected-version is required (see `inspect`).")
        result = self.client().action({"action": "clear", "session": session, "provider": provider,
                                       "source": "ctl", "expected_version": int(expected)},
                                      str(getattr(args, "idempotency_key", "") or ""))
        if self._json:
            self._emit({"ok": True, "command": "clear", "replayed": bool(result.get("replayed")),
                        "key": _rows([result.get("key") or {}])[0]})
            return 0
        self.io.write_line(f"cleared{' (replayed)' if result.get('replayed') else ''}: "
                           f"{metadata_line(result.get('key') or {})}")
        return 0


__all__ = ["ACTIONS", "SessionApiKeyCli", "SessionApiKeyCliIO", "metadata_line",
           "stdin_hidden_prompt_available"]
