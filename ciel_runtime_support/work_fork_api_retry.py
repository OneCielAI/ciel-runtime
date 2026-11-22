"""Bounded transcript-based transient API error detection for running Work forks.

Reuses the exact typed classifiers main uses (``runtime_input_retry.classify_claude_api_error``
and ``claude_typed_turn_resend.is_auto_compact_failed``) plus ``fork_turn_watch.is_empty_stream_error``
so Work forks paused by a transient upstream API error or auto-compaction failure can be retried
by the Work host instead of being salvaged after two nudges.

Read-only and stat-cached: never returns transcript text or secrets.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

from .claude_typed_turn_resend import auto_compact_failed
from .fork_turn_watch import _parse_time, is_empty_stream_error, subagents_dir
from .runtime_input_retry import classify_claude_api_error

TAIL_BYTES = 131_072
MAX_RECORD_BYTES = 4 * 1024 * 1024
_CACHE_MAX = 256
_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}\Z")
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}\Z")

_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[tuple[int, int], dict[str, Any] | None]] = OrderedDict()


def _active_claude_scope() -> dict[str, Any]:
    from . import fork_turn_watch

    watch = getattr(fork_turn_watch, "_ROUTER_WATCH", None)
    if watch is None:
        return {}
    try:
        scope = watch.ports.scope()
    except Exception:  # noqa: BLE001 - unreadable scope degrades cleanly
        return {}
    return scope if isinstance(scope, dict) else {}


def _classify_error_record(record: dict[str, Any]) -> tuple[str, bool] | None:
    """Return ``(kind_label, is_compaction)`` if ``record`` is a retryable fork API error."""
    if record.get("type") != "assistant" or record.get("isApiErrorMessage") is not True:
        return None
    if auto_compact_failed(record):
        return "auto_compact_failed", True
    if is_empty_stream_error(record):
        return "empty_stream", False
    if record.get("resendCannotCure") is True or record.get("apiErrorIsTransient") is False:
        return None
    failure = classify_claude_api_error(record)
    if record.get("truncatedAfterOutput") is True:
        # Forks have no dedicated stream-recovery loop; a mid-stream cut is retryable.
        return failure.label, False
    if failure.transient:
        return failure.label, False
    return None


def _tail_lines(path: Path) -> list[bytes]:
    with path.open("rb") as stream:
        end = stream.seek(0, 2)
        if end <= 0:
            return []
        offset = max(0, end - TAIL_BYTES)
        stream.seek(offset)
        raw = stream.read(end - offset)
    # Ignore any trailing incomplete line without a newline terminator.
    last_nl = raw.rfind(b"\n")
    if last_nl < 0:
        return []
    raw = raw[:last_nl]
    lines = raw.split(b"\n")
    if offset > 0 and lines:
        lines = lines[1:]
    return lines


def _error_uuid(record: dict[str, Any], kind: str) -> str:
    raw = record.get("uuid")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:64]
    seed = f"{record.get('timestamp') or ''}:{kind}:{record.get('error') or ''}:{record.get('apiErrorStatus') or ''}"
    return "syn-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def classify_fork_transcript(path: Path) -> dict[str, Any] | None:
    """Inspect one fork transcript tail; return retryable error metadata or None."""
    try:
        if path.is_symlink():
            return None
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    stamp = (int(st.st_mtime_ns), int(st.st_size))
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == stamp:
            _CACHE.move_to_end(key)
            return dict(cached[1]) if cached[1] is not None else None
    result: dict[str, Any] | None = None
    try:
        lines = _tail_lines(path)
    except OSError:
        return None
    last_relevant: dict[str, Any] | None = None
    last_progress_uuid = ""
    for line in lines:
        if not line.strip() or len(line) > MAX_RECORD_BYTES:
            continue
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("type") not in ("assistant", "user"):
            continue
        last_relevant = row
        if row.get("type") == "assistant" and row.get("isApiErrorMessage") is not True:
            uid = row.get("uuid")
            if isinstance(uid, str) and uid.strip():
                last_progress_uuid = uid.strip()[:64]
    if last_relevant is not None:
        classified = _classify_error_record(last_relevant)
        if classified is not None:
            kind, compaction = classified
            result = {
                "retryable": True,
                "error_uuid": _error_uuid(last_relevant, kind),
                "last_progress_uuid": last_progress_uuid,
                "kind": kind,
                "compaction": compaction,
                "record_at": _parse_time(last_relevant.get("timestamp")),
            }
    with _CACHE_LOCK:
        _CACHE[key] = (stamp, result)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return dict(result) if result is not None else None


def _subagents_for(session_id: str, leases: dict[str, Any], *,
                   claude_config_dir: str | Path | None = None,
                   cwd: str | Path | None = None) -> Path | None:
    if claude_config_dir and cwd:
        return subagents_dir(claude_config_dir, cwd, session_id)
    scope = _active_claude_scope()
    if (scope.get("runtime") == "claude" and scope.get("session_id") == session_id
            and scope.get("claude_config_dir") and scope.get("cwd")):
        return subagents_dir(scope["claude_config_dir"], scope["cwd"], session_id)
    cfg = claude_config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    repo = cwd
    if not repo:
        for lease in leases.values():
            ws = lease.get("workspace") if isinstance(lease, dict) else None
            if isinstance(ws, dict) and isinstance(ws.get("repo"), str) and ws["repo"]:
                repo = ws["repo"]
                break
    if not repo:
        return None
    return subagents_dir(cfg, repo, session_id)


def inspect_running_forks(leases: dict[str, Any], scope: str, *,
                          claude_config_dir: str | Path | None = None,
                          cwd: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Map running Work lease_id -> payload-free retryable API error info."""
    if not isinstance(leases, dict) or not isinstance(scope, str) or not scope.startswith("claude:"):
        return {}
    session_id = scope.split(":", 1)[1]
    if not _SESSION_ID.fullmatch(session_id):
        return {}
    running = {
        lease_id: lease for lease_id, lease in leases.items()
        if isinstance(lease, dict)
        and lease.get("scope") == scope
        and lease.get("state") == "running"
        and isinstance(lease.get("host_id"), str)
        and _AGENT_ID.fullmatch(lease["host_id"])
    }
    if not running:
        return {}
    directory = _subagents_for(session_id, running, claude_config_dir=claude_config_dir, cwd=cwd)
    if directory is None or not directory.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for lease_id, lease in running.items():
        agent_id = lease["host_id"]
        info = classify_fork_transcript(directory / f"agent-{agent_id}.jsonl")
        if info is not None:
            out[lease_id] = {"agent_id": agent_id, **info}
    return out
