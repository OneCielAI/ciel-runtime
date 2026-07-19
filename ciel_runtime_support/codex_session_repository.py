"""Read-only repository for the Codex local resume index."""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ciel_runtime_support.codex_config import (
    codex_config_paths_for_launch,
    toml_scalar_without_comment,
    unquote_toml_string,
)


def codex_sqlite_home(
    passthrough: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    launch_env = env or os.environ
    launch_cwd = (cwd or Path.cwd()).resolve()
    configured: str | None = None
    for path in codex_config_paths_for_launch(
        passthrough or [], env=launch_env, cwd=launch_cwd
    ):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        table = ""
        for line in text.splitlines():
            stripped = toml_scalar_without_comment(line)
            if not stripped:
                continue
            table_match = re.fullmatch(r"\[(.+)\]", stripped)
            if table_match:
                table = table_match.group(1).strip()
                continue
            if table:
                continue
            match = re.match(r"sqlite_home\s*=\s*(.+)$", stripped)
            if match:
                configured = unquote_toml_string(match.group(1))
    raw = configured or str(launch_env.get("CODEX_SQLITE_HOME") or "").strip()
    if raw:
        path = Path(os.path.expandvars(raw)).expanduser()
        return path if path.is_absolute() else launch_cwd / path
    return Path(launch_env.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


class CodexSessionRepository:
    def __init__(self, database: Path, log: Callable[[str, str], None]) -> None:
        self.database = database
        self.log = log

    def resumable(self, limit: int = 200, *, include_non_interactive: bool = False) -> list[dict[str, Any]]:
        if not self.database.is_file():
            return []
        sources = ["cli", "vscode"]
        if include_non_interactive:
            sources.extend(["exec", "app-server"])
        placeholders = ", ".join("?" for _ in sources)
        uri = self.database.resolve().as_uri() + "?mode=ro"
        try:
            with contextlib.closing(
                sqlite3.connect(uri, uri=True, timeout=1.0)
            ) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    f"""
                    SELECT id, title, first_user_message, cwd, model_provider,
                           COALESCE(NULLIF(updated_at_ms, 0), updated_at * 1000) AS activity_ms
                    FROM threads
                    WHERE archived = 0 AND source IN ({placeholders})
                    ORDER BY COALESCE(NULLIF(recency_at_ms, 0),
                                      NULLIF(updated_at_ms, 0), updated_at * 1000) DESC
                    LIMIT ?
                    """,
                    (*sources, max(1, min(1000, int(limit)))),
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            self.log(
                "WARN",
                f"codex_resume_index_read_failed error={type(exc).__name__}: {exc}",
            )
            return []
        return [dict(row) for row in rows]
