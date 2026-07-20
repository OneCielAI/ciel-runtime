"""Claude and Codex transcript discovery repository."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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
        self.cache.clear()
        self.cache.update({"checked_at": 0.0, "path": None})

    def roots(self) -> tuple[tuple[Path, str], ...]:
        runtime = str(self.scope.get("runtime") or "").strip().casefold()
        claude_root = (self.home / ".claude" / "projects", "*/*.jsonl")
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
                if mtime > latest_mtime:
                    latest = path
                    latest_mtime = mtime
        self.cache["checked_at"] = now
        self.cache["path"] = latest
        return latest

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
