"""Codex resume-session query, presentation, and selection workflow."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CodexSessionRepositoryPorts:
    sqlite_home: Callable[..., Path]
    resumable: Callable[[Path, int, bool], list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CodexSessionPresentationPorts:
    select: Callable[..., int | None]
    compact_text: Callable[[str, int], str]
    output: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CodexSessionSelectionService:
    repository: CodexSessionRepositoryPorts
    presentation: CodexSessionPresentationPorts

    def sqlite_home_for_launch(
        self,
        passthrough: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> Path:
        return self.repository.sqlite_home(passthrough, env=env, cwd=cwd)

    def local_resume_sessions(
        self,
        env: dict[str, str] | None = None,
        limit: int = 200,
        include_non_interactive: bool = False,
        passthrough: list[str] | None = None,
        cwd: Path | None = None,
    ) -> list[dict[str, Any]]:
        database = (
            self.sqlite_home_for_launch(passthrough, env=env, cwd=cwd)
            / "state_5.sqlite"
        )
        return self.repository.resumable(database, limit, include_non_interactive)

    def resume_session_row(self, session: dict[str, Any]) -> str:
        title = str(
            session.get("title")
            or session.get("first_user_message")
            or "Untitled session"
        ).strip()
        title = re.sub(r"\s+", " ", title)
        cwd = str(session.get("cwd") or "").strip()
        folder = Path(cwd).name if cwd else "-"
        provider = str(session.get("model_provider") or "-").strip()
        try:
            activity = datetime.fromtimestamp(
                int(session.get("activity_ms") or 0) / 1000
            ).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, TypeError, ValueError):
            activity = "unknown time"
        title = self.presentation.compact_text(title, 66)
        return f"{title}  [{folder} | {provider} | {activity}]"

    def select_resume_session(
        self,
        env: dict[str, str] | None = None,
        include_non_interactive: bool = False,
        passthrough: list[str] | None = None,
    ) -> str | None:
        sessions = self.local_resume_sessions(
            env,
            include_non_interactive=include_non_interactive,
            passthrough=passthrough,
        )
        if not sessions:
            database = (
                self.sqlite_home_for_launch(passthrough, env=env) / "state_5.sqlite"
            )
            self.presentation.output(
                f"Ciel Runtime could not find resumable Codex sessions in: {database}"
            )
            return None
        selected = self.presentation.select(
            "Resume Codex session",
            [self.resume_session_row(session) for session in sessions],
            footer="Up/Down moves. Enter resumes. Esc/q cancels.",
        )
        if selected is None:
            return ""
        return str(sessions[selected].get("id") or "").strip()
