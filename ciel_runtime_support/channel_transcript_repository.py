"""Claude and Codex transcript discovery repository."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


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
        self.scope["cwd"] = Path(cwd).expanduser() if cwd is not None else None
        self.scope["session_id"] = str(session_id or "").strip()
        self.scope["bound_path"] = None
        self.cache.clear()
        self.cache.update({"checked_at": 0.0, "path": None})

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
        if runtime == "codex":
            return (codex_root,)
        if runtime == "claude":
            scope_cwd = self.scope.get("cwd")
            if scope_cwd is not None:
                project_dir = claude_projects / self._claude_project_key(scope_cwd)
                if project_dir.is_dir():
                    return ((project_dir, "*.jsonl"),)
            return (claude_root,)
        return claude_root, codex_root

    def latest(self, ttl_seconds: float = 2.0) -> Path | None:
        now = self.now()
        cached_at = float(self.cache.get("checked_at") or 0.0)
        cached_path = self.cache.get("path")
        if now - cached_at < ttl_seconds:
            return cached_path if isinstance(cached_path, Path) else None
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
                paths = root.glob(pattern)
            except Exception:
                continue
            for path in paths:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if scope_started_at > 0 and mtime < scope_started_at - 1.0:
                    continue
                if runtime == "codex" and (scope_started_at > 0 or scope_cwd):
                    session_started_at, session_cwd = self._codex_session_identity(path)
                    if (
                        session_started_at is not None
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
    def _codex_session_identity(path: Path) -> tuple[float | None, str]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                record = json.loads(stream.readline(64 * 1024))
        except (OSError, UnicodeError, ValueError, TypeError):
            return None, ""
        if not isinstance(record, dict):
            return None, ""
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
        return started_at, str(metadata.get("cwd") or "")

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
