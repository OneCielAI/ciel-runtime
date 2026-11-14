"""Secret-free markers that let a restarted router fail closed for a lost session key.

The vault lives in router memory, so a restart loses every key.  Without a
trace of what was lost, the next request of that session would silently use
the stored key.  This file, in the router instance directory (never the
configuration), records one row per session and provider:

    {"session": "claude:<id>", "provider": "...", "state": "active" | "blocked_<cause>"
     | "fell_back_to_stored", "fallback": "fail_closed" | "stored", "cause": "...",
     "version": N, "source": "...", "expires_at": T, "lost_at": T, "launcher_pid": P}

No key, no fingerprint, no launch binding: writes keep only these fields, and
a read that finds any other field, a wrong type or more than MAX_FILE_BYTES
reports the file unreadable, which the vault treats as "blocked: state
unreadable" for every session without a live key (never as "no marker").
Writes go through a temporary file and an atomic replace under the file's
lock, like the other instance-dir state (secure_json_repository,
durable_file_lock).

A row ends only with an operator clear, a new inject, or when the launcher
that bound that exact session has exited; time never ends it.  The vault
refuses a new key instead of pruning a row when MAX_ROWS is reached.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .durable_file_lock import exclusive_file_lock
from .secure_json_repository import SecureJsonEffects, SecureJsonRepository

MARKER_FILE = "session-api-key-markers.json"
MAX_ROWS = 512
MAX_FILE_BYTES = 512 * 1024
_TEXT = (str,)
_NUMBER = (int, float)
_FIELDS: dict[str, tuple[type, ...]] = {
    "session": _TEXT, "provider": _TEXT, "state": _TEXT, "fallback": _TEXT, "cause": _TEXT,
    "version": (int,), "source": _TEXT, "expires_at": _NUMBER, "lost_at": _NUMBER, "launcher_pid": (int,),
    # The owner's creation token (not secret): pid + token prove a session's end.
    "launcher_identity": _TEXT,
    "end_proof": _TEXT,
    "injected_at": _NUMBER,
}
_REQUIRED = ("session", "provider", "state", "fallback", "version")
OK, MISSING, UNREADABLE = "ok", "missing", "unreadable"


def secret_free(row: dict[str, Any]) -> dict[str, Any]:
    """Only the marker fields; anything else (a key, a fingerprint, a binding) never reaches the file."""

    return {name: row[name] for name in _FIELDS if name in row}


def _valid(row: Any) -> bool:
    if not isinstance(row, dict) or not set(row) <= set(_FIELDS) or not all(name in row for name in _REQUIRED):
        return False
    return all(isinstance(value, _FIELDS[name]) and not isinstance(value, bool) for name, value in row.items())


@dataclass(frozen=True, slots=True)
class SessionKeyMarkerStore:
    path: Path
    log: Callable[[str, str], None] = lambda _level, _message: None

    def _repository(self) -> SecureJsonRepository:
        return SecureJsonRepository(self.path, SecureJsonEffects(log=self.log))

    def load(self) -> tuple[str, list[dict[str, Any]]]:
        """``(OK, rows)``, ``(MISSING, [])`` or ``(UNREADABLE, [])``."""

        with exclusive_file_lock(self.path):
            try:
                size = self.path.stat().st_size
            except FileNotFoundError:
                return MISSING, []
            except OSError:
                return UNREADABLE, []
            if size > MAX_FILE_BYTES:
                self.log("WARN", f"session_api_key_markers_unreadable reason=oversize bytes={size}")
                return UNREADABLE, []
            payload = self._repository().load("session_api_key_markers")
        rows = payload.get("markers") if isinstance(payload, dict) else None
        if (payload is None or payload.get("version") != 1 or not isinstance(rows, list)
                or len(rows) > MAX_ROWS or not all(_valid(row) for row in rows)):
            self.log("WARN", "session_api_key_markers_unreadable reason=format")
            return UNREADABLE, []
        return OK, [dict(row) for row in rows]

    def save(self, rows: list[dict[str, Any]]) -> None:
        clean = [secret_free(row) for row in rows]
        if len(clean) > MAX_ROWS or not all(_valid(row) for row in clean):
            raise ValueError("session key markers out of bounds")
        with exclusive_file_lock(self.path):
            self._repository().save({"version": 1, "markers": clean}, "session_api_key_markers")

    def quarantine(self) -> Path | None:
        """Move an unreadable file aside (bytes kept for diagnosis) so a reset can start clean."""

        with exclusive_file_lock(self.path):
            return self._repository().quarantine("session_api_key_markers")


__all__ = ["MARKER_FILE", "MAX_FILE_BYTES", "MAX_ROWS", "MISSING", "OK", "SessionKeyMarkerStore", "UNREADABLE",
           "secret_free"]
