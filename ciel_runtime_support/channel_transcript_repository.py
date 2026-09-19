"""Claude and Codex transcript discovery repository."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import threading
from typing import Any


TURN_UPDATE_MAX_BYTES = 4 * 1024 * 1024
TURN_UPDATE_SKIP_SCAN_BYTES = 64 * 1024
# A transcript store can sit on a network share or a WSL UNC path whose relay
# stalls a stat call for minutes; no caller thread may own that wait (live:
# 2026-09-19 a `muse --continue` session froze at launch - the boundary scan -
# and again in the console-proxy poll loop, both stuck in
# `\\wsl.localhost\Ubuntu-26.04\home\...\muse\sessions` while the WSL 9p relay
# was wedged). Scans therefore run on one background worker under this
# deadline; callers that miss it serve their cached view instead.
BOUNDARY_SCAN_TIMEOUT_SECONDS = 2.5
TRANSCRIPT_SCAN_DEADLINE_SECONDS = 2.5


class _TranscriptScanWorker:
    """One daemon thread runs transcript filesystem scans.

    ``run`` executes its job on that thread and returns ``(result, completed)``.
    While a job is in flight (for example a stat stuck on a wedged relay) later
    submissions return ``(fallback, False)`` immediately instead of queueing
    behind it, so no caller thread ever blocks on the filesystem.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._thread: threading.Thread | None = None
        self._job: tuple[Callable[[], Any], list[Any], threading.Event] | None = None
        self._wake = threading.Event()

    def run(
        self, job: Callable[[], Any], deadline: float, fallback: Any
    ) -> tuple[Any, bool]:
        box: list[Any] = []
        done = threading.Event()
        with self._lock:
            if self._busy:
                return fallback, False
            self._busy = True
            self._job = (job, box, done)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="transcript-scan", daemon=True
                )
                self._thread.start()
        self._wake.set()
        if not done.wait(deadline):
            return fallback, False
        return (box[0] if box else fallback), True

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                pending = self._job
            if pending is None:
                continue
            job, box, done = pending
            try:
                box.append(job())
            except Exception:
                box.append(None)
            with self._lock:
                self._job = None
                self._busy = False
            done.set()


_TRANSCRIPT_SCAN_WORKER = _TranscriptScanWorker()


@dataclass(frozen=True, slots=True)
class ChannelTranscriptRepository:
    home: Path
    cache: dict[str, Any]
    scope: dict[str, Any]
    now: Callable[[], float]

    def set_scope(
        self,
        runtime: str,
        *,
        started_at: float | None = None,
        codex_home: Path | None = None,
        muse_home: Path | None = None,
        cwd: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self.scope["runtime"] = str(runtime or "").strip().casefold()
        self.scope["started_at"] = (
            self.now() if started_at is None else float(started_at)
        )
        self.scope["codex_home"] = (
            Path(codex_home).expanduser()
            if codex_home is not None
            else None
        )
        self.scope["muse_home"] = (
            Path(muse_home).expanduser()
            if muse_home is not None
            else None
        )
        self.scope["cwd"] = Path(cwd).expanduser() if cwd is not None else None
        self.scope["session_id"] = str(session_id or "").strip()
        self.scope["bound_path"] = None
        self.cache.clear()
        self.cache.update({"checked_at": 0.0, "path": None})
        self.scope["turn_active"] = False
        self.scope["turn_scan_path"] = None
        self.scope["turn_scan_offset"] = 0
        self.scope["turn_scan_skipping_record"] = False
        self.scope["turn_scan_skipped_bytes"] = 0
        self._capture_turn_scan_boundary()

    def _capture_turn_scan_boundary(self) -> None:
        """Remember where this console launch begins in an existing transcript.

        The scan is bounded by ``BOUNDARY_SCAN_TIMEOUT_SECONDS``: a transcript
        store on a stalled relay must not hold the launch hostage. A scan that
        misses the deadline leaves the boundary unset; the delivery loop
        establishes it on a later poll.
        """

        boundary = self._bounded_turn_scan_boundary()
        if boundary is not None:
            path, size = boundary
            self.scope["turn_scan_path"] = path
            self.scope["turn_scan_offset"] = size
        # Boundary discovery must not pin the ordinary latest-path TTL cache.
        self.cache.clear()
        self.cache.update({"checked_at": 0.0, "path": None})

    def _bounded_turn_scan_boundary(self) -> tuple[Path, int] | None:
        """Run the boundary scan on a worker thread with a hard deadline."""

        result: list[tuple[Path, int] | None] = []

        def scan() -> None:
            try:
                path = self.latest(ttl_seconds=0)
                result.append((path, path.stat().st_size) if path is not None else None)
            except OSError:
                result.append(None)

        worker = threading.Thread(
            target=scan, name="transcript-boundary-scan", daemon=True
        )
        worker.start()
        worker.join(BOUNDARY_SCAN_TIMEOUT_SECONDS)
        if not result:
            return None
        return result[0]

    def read_turn_updates(
        self,
        path: Path,
        max_bytes: int = TURN_UPDATE_MAX_BYTES,
        log: Callable[[str, str], None] | None = None,
    ) -> str:
        """Read complete JSONL records appended since this console launched.

        Active turns can emit more than the bounded diagnostic tail before they
        finish. Keeping an incremental offset preserves the opening lifecycle
        event without repeatedly reading a large resumed-session transcript.

        One poll is deliberately bounded. If a single JSONL record exceeds the
        bound, scan to its newline in small chunks and discard that record. Tool
        output can be arbitrarily large, while lifecycle records are small; the
        proxy must never materialize a multi-gigabyte transcript suffix merely
        to decide whether a turn is active.
        """

        try:
            limit = max(4096, min(16 * 1024 * 1024, int(max_bytes)))
        except (TypeError, ValueError):
            limit = TURN_UPDATE_MAX_BYTES
        scan_path = self.scope.get("turn_scan_path")
        if scan_path is None:
            self.scope["turn_scan_path"] = path
        elif Path(scan_path) != path:
            self.scope["turn_scan_path"] = path
            self.scope["turn_scan_offset"] = 0
            self.scope["turn_active"] = False
            self.scope["turn_scan_skipping_record"] = False
            self.scope["turn_scan_skipped_bytes"] = 0
        try:
            size = path.stat().st_size
            offset = max(0, int(self.scope.get("turn_scan_offset") or 0))
            remaining = limit
            if size < offset:
                offset = 0
                self.scope["turn_active"] = False
                self.scope["turn_scan_skipping_record"] = False
                self.scope["turn_scan_skipped_bytes"] = 0
            if size <= offset:
                return ""
            with path.open("rb") as stream:
                while offset < size and remaining > 0:
                    if bool(self.scope.get("turn_scan_skipping_record")):
                        stream.seek(offset)
                        chunk = stream.read(
                            min(
                                TURN_UPDATE_SKIP_SCAN_BYTES,
                                size - offset,
                                remaining,
                            )
                        )
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        newline = chunk.find(b"\n")
                        consumed = len(chunk) if newline < 0 else newline + 1
                        offset += consumed
                        self.scope["turn_scan_offset"] = offset
                        self.scope["turn_scan_skipped_bytes"] = (
                            max(0, int(self.scope.get("turn_scan_skipped_bytes") or 0))
                            + consumed
                        )
                        if newline < 0:
                            continue
                        self.scope["turn_scan_skipping_record"] = False
                        self.scope["turn_scan_skipped_bytes"] = 0
                        continue

                    stream.seek(offset)
                    data = stream.read(min(remaining, size - offset))
                    if not data:
                        break
                    remaining -= len(data)
                    complete_end = data.rfind(b"\n")
                    if complete_end >= 0:
                        consumed = complete_end + 1
                        self.scope["turn_scan_offset"] = offset + consumed
                        payload = data if consumed == len(data) else data[:consumed]
                        return payload.decode("utf-8", errors="replace")
                    if len(data) < limit:
                        # A normal record is still being appended. Keep its
                        # boundary so the next poll can parse it when complete.
                        return ""

                    # The first unread physical record alone exceeds the
                    # memory bound. Do not retain or repeatedly reread it.
                    self.scope["turn_scan_skipping_record"] = True
                    self.scope["turn_scan_skipped_bytes"] = len(data)
                    offset += len(data)
                    self.scope["turn_scan_offset"] = offset
                    if log is not None:
                        log(
                            "WARN",
                            "channel_turn_record_exceeds_memory_limit "
                            f"path={path.name} offset={offset - len(data)} "
                            f"limit_bytes={limit}",
                        )
        except (OSError, TypeError, ValueError):
            return ""
        return ""

    def roots(self) -> tuple[tuple[Path, str], ...]:
        runtime = str(self.scope.get("runtime") or "").strip().casefold()
        claude_projects = self.home / ".claude" / "projects"
        claude_root = (claude_projects, "*/*.jsonl")
        configured_codex_home = self.scope.get("codex_home")
        codex_home = (
            Path(configured_codex_home)
            if isinstance(configured_codex_home, Path)
            else self.home / ".codex"
        )
        codex_root = (codex_home / "sessions", "**/*.jsonl")
        configured_muse_home = self.scope.get("muse_home")
        muse_home = (
            Path(configured_muse_home)
            if isinstance(configured_muse_home, Path)
            else self.home / ".local" / "share" / "muse"
        )
        muse_root = (muse_home / "sessions", "*/*/*/*/session.jsonl")
        if runtime == "codex":
            return (codex_root,)
        if runtime == "claude":
            scope_cwd = self.scope.get("cwd")
            if scope_cwd is not None:
                project_dir = claude_projects / self._claude_project_key(scope_cwd)
                if project_dir.is_dir():
                    return ((project_dir, "*.jsonl"),)
            return (claude_root,)
        if runtime == "muse":
            return (muse_root,)
        return claude_root, codex_root, muse_root

    def latest(self, ttl_seconds: float = 2.0) -> Path | None:
        """Newest transcript for the current scope, never blocking the caller.

        The filesystem scan runs on the shared scan worker under a deadline;
        when a scan is already stuck (or this one misses its deadline) the
        cached view is served instead, so the console-proxy poll loop and the
        launch path stay responsive while a relay is wedged.
        """

        now = self.now()
        cached_at = float(self.cache.get("checked_at") or 0.0)
        cached_path = self.cache.get("path")
        cached = cached_path if isinstance(cached_path, Path) else None
        if now - cached_at < ttl_seconds:
            return cached
        result, completed = _TRANSCRIPT_SCAN_WORKER.run(
            lambda: self._scan_latest(now),
            TRANSCRIPT_SCAN_DEADLINE_SECONDS,
            None,
        )
        if not completed:
            # Retry no sooner than the TTL so a wedged scan is not queued anew
            # on every poll.
            self.cache["checked_at"] = now
            return cached
        return result if isinstance(result, Path) else None

    def _scan_latest(self, now: float) -> Path | None:
        latest: Path | None = None
        latest_mtime = -1.0
        scope_started_at = float(self.scope.get("started_at") or 0.0)
        runtime = str(self.scope.get("runtime") or "").strip().casefold()
        scope_cwd = self._normalized_cwd(self.scope.get("cwd"))
        scope_session_id = str(self.scope.get("session_id") or "").strip()
        bound_path = self.scope.get("bound_path")
        if runtime == "claude" and isinstance(bound_path, Path):
            try:
                if bound_path.is_file():
                    return bound_path
            except OSError:
                pass
        for root, pattern in self.roots():
            try:
                # ``Path.glob`` is lazy: stat errors during iteration (a wedged
                # WSL/UNC relay answers WinError 995) must be tolerated here,
                # so materialize the matches inside the guard.
                paths = list(root.glob(pattern))
            except Exception:
                continue
            for path in paths:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if (
                    scope_started_at > 0
                    and not scope_session_id
                    and mtime < scope_started_at - 1.0
                ):
                    continue
                if runtime == "codex" and (
                    scope_started_at > 0 or scope_cwd or scope_session_id
                ):
                    session_started_at, session_cwd, session_id = (
                        self._codex_session_identity(path)
                    )
                    if scope_session_id and session_id != scope_session_id:
                        continue
                    if (
                        not scope_session_id
                        and session_started_at is not None
                        and scope_started_at > 0
                        and session_started_at < scope_started_at - 1.0
                    ):
                        continue
                    normalized_session_cwd = self._normalized_cwd(session_cwd)
                    if scope_cwd and normalized_session_cwd and normalized_session_cwd != scope_cwd:
                        continue
                if runtime == "claude" and (scope_cwd or scope_session_id):
                    _session_started_at, session_cwd, session_id = (
                        self._claude_session_identity(path)
                    )
                    normalized_session_cwd = self._normalized_cwd(session_cwd)
                    if (
                        scope_cwd
                        and normalized_session_cwd
                        and normalized_session_cwd != scope_cwd
                    ):
                        continue
                    if scope_session_id and session_id != scope_session_id:
                        continue
                if mtime > latest_mtime:
                    latest = path
                    latest_mtime = mtime
        self.cache["checked_at"] = now
        self.cache["path"] = latest
        if runtime == "claude" and latest is not None:
            self.scope["bound_path"] = latest
        return latest

    @staticmethod
    def _claude_project_key(value: Any) -> str:
        raw = str(value or "").strip().rstrip("\\/")
        return re.sub(r"[^A-Za-z0-9._-]", "-", raw)

    @staticmethod
    def _normalized_cwd(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().replace("\\", "/").rstrip("/").casefold()

    @staticmethod
    def _codex_session_identity(path: Path) -> tuple[float | None, str, str]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                record = json.loads(stream.readline(64 * 1024))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None, "", ""
        if not isinstance(record, dict):
            return None, "", ""
        payload = record.get("payload")
        metadata = payload if isinstance(payload, dict) else {}
        raw_timestamp = metadata.get("timestamp") or record.get("timestamp")
        started_at: float | None = None
        if isinstance(raw_timestamp, (int, float)):
            started_at = float(raw_timestamp)
        elif isinstance(raw_timestamp, str) and raw_timestamp.strip():
            try:
                started_at = datetime.fromisoformat(
                    raw_timestamp.strip().replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                started_at = None
        session_id = str(metadata.get("session_id") or metadata.get("id") or "")
        return started_at, str(metadata.get("cwd") or ""), session_id

    @staticmethod
    def _claude_session_identity(path: Path) -> tuple[float | None, str, str]:
        started_at: float | None = None
        cwd = ""
        session_id = ""
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for _index in range(128):
                    raw = stream.readline(256 * 1024)
                    if not raw:
                        break
                    try:
                        record = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    if not cwd:
                        cwd = str(record.get("cwd") or "")
                    if not session_id:
                        session_id = str(record.get("sessionId") or "")
                    if started_at is None:
                        timestamp = record.get("timestamp")
                        if isinstance(timestamp, (int, float)):
                            started_at = float(timestamp)
                        elif isinstance(timestamp, str) and timestamp.strip():
                            try:
                                started_at = datetime.fromisoformat(
                                    timestamp.strip().replace("Z", "+00:00")
                                ).timestamp()
                            except ValueError:
                                pass
                    if cwd and session_id:
                        break
        except (OSError, UnicodeError):
            return None, "", ""
        return started_at, cwd, session_id

    @staticmethod
    def read_tail_text(
        path: Path,
        max_bytes: int = 512 * 1024,
    ) -> str:
        try:
            size = path.stat().st_size
            with path.open("rb") as stream:
                if size > max_bytes:
                    stream.seek(max(0, size - max_bytes))
                return stream.read(max_bytes).decode(
                    "utf-8",
                    errors="replace",
                )
        except Exception:
            return ""
