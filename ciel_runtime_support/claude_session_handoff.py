"""Resolve only Claude's explicit parked-session continuation, never newest peers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .workspace_router_selection import _process_identity


@dataclass(frozen=True, slots=True)
class SessionHandoff:
    record: dict[str, Any]
    pending: bool = False


def session_records(directory: Path) -> list[dict[str, Any]]:
    records = []
    try:
        paths = list(directory.glob("*.json"))
    except OSError:
        return records
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > 64 * 1024:
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(record, dict) and type(record.get("pid")) is int
                    and record["pid"] > 0 and path.stem == str(record["pid"])
                    and record.get("procStart") and record.get("sessionId")):
                records.append(record)
        except (OSError, ValueError, UnicodeError):
            continue
    return records


def _cwd(value: Any) -> str:
    return str(value or "").replace(chr(92), "/").rstrip("/").casefold()


def _live_key(directory: Path, record: dict[str, Any]) -> bool:
    from .claude_socket_paths import session_key_hash
    target = str(record.get("messagingSocketPath") or "")
    if not target:
        return False
    try:
        path = directory / f"{record['pid']}.{session_key_hash(target)}.key"
        if path.is_symlink() or path.stat().st_size > 4096:
            return False
        key = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(key, dict):
            return False
        token = key.get("peerToken")
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdefABCDEF" for c in token):
            return False
        if str(key.get("procStartFt") or "") != str(record["procStart"]):
            return False
        if key.get("pidDomain") != record.get("pidDomain"):
            return False
        identity = _process_identity(record["pid"])
        # Windows publishes the FILETIME creation token. Linux publishes the
        # /proc start token. Unknown/dead/reused pids cannot own a handoff.
        return bool(identity and str(identity) == str(record["procStart"]))
    except (OSError, ValueError, UnicodeError):
        return False


def follow_parked_session(directory: Path, record: dict[str, Any],
                          records: list[dict[str, Any]] | None = None) -> SessionHandoff:
    """An explicit parkedJobId -> unique live bg jobId edge, with bounded chains.

    An incomplete/ambiguous handoff holds input; it must not fall back to the
    parked original's accepting socket (Edward .14, 2026-10-26).
    """
    records = session_records(directory) if records is None else records
    current = record
    seen = set()
    for _ in range(8):
        job = current.get("parkedJobId")
        if not job:
            return SessionHandoff(current)
        if not isinstance(job, str) or not job.strip() or current.get("sessionId") in seen:
            return SessionHandoff({}, True)
        seen.add(current.get("sessionId"))
        candidates = [r for r in records if r.get("kind") == "bg" and r.get("jobId") == job]
        if len(candidates) != 1:
            return SessionHandoff({}, True)
        child = candidates[0]
        sid = str(child.get("sessionId") or "")
        try:
            valid = (job in (sid, sid[:8]) and sid not in seen
                     and _cwd(current.get("cwd")) and _cwd(child.get("cwd")) == _cwd(current.get("cwd"))
                     and current.get("pidDomain") and child.get("pidDomain") == current.get("pidDomain")
                     and float(child.get("startedAt") or 0) >= float(current.get("startedAt") or 0)
                     and _live_key(directory, child))
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            return SessionHandoff({}, True)
        current = child
    return SessionHandoff({}, True)


def socket_handoff(directory: Path, target: str) -> SessionHandoff:
    from .claude_socket_paths import canonical_socket_path
    records = session_records(directory)
    candidates = [r for r in records if r.get("messagingSocketPath")
                  and canonical_socket_path(str(r["messagingSocketPath"])) == canonical_socket_path(target)]
    if not candidates:
        # A key with its own published record is not a legacy key-only client,
        # even while that record cannot be parsed during native publication.
        from .claude_socket_paths import session_key_hash
        try:
            digest = session_key_hash(target)
            if any((directory / (key.name.split(".", 1)[0] + ".json")).exists()
                   for key in directory.glob(f"*.{digest}.key")):
                return SessionHandoff({}, True)
        except OSError:
            return SessionHandoff({}, True)
        return SessionHandoff({})  # older clients publish only a key
    if len(candidates) != 1:
        return SessionHandoff({}, True)
    return follow_parked_session(directory, candidates[0], records)
