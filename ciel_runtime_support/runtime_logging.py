"""Runtime log-level repository and rotating file logger."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOG_LEVELS = {"SILENT": 0, "ERROR": 1, "WARN": 2, "INFO": 3, "DEBUG": 4, "TRACE": 5}
LOG_LEVEL_NAMES = {value: name for name, value in LOG_LEVELS.items()}


def normalize_log_level(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("log level is empty")
    upper = {
        "OFF": "SILENT",
        "NONE": "SILENT",
        "QUIET": "SILENT",
        "WARNING": "WARN",
        "WARNINGS": "WARN",
    }.get(raw.upper(), raw.upper())
    if upper in ("DEFAULT", "RESET", "UNSET", "AUTO"):
        return None
    if upper in LOG_LEVELS:
        return upper
    if upper.isdigit():
        return LOG_LEVEL_NAMES[max(0, min(5, int(upper)))]
    raise ValueError(f"unknown log level: {value}")


@dataclass(frozen=True, slots=True)
class LogLevelRepository:
    config_dir: Path
    path: Path
    cache: dict[str, Any]
    default_level: int
    environ: dict[str, str]

    def current(self) -> int:
        now = time.time()
        if self.cache["value"] is not None and now - float(self.cache["checked_at"]) < 1.0:
            return int(self.cache["value"])
        level = self._file_level(now)
        if level is None:
            value = self.environ.get("CIEL_RUNTIME_LOG_LEVEL", "").strip().upper()
            if value in LOG_LEVELS:
                level = LOG_LEVELS[value]
            elif value.isdigit():
                level = max(0, min(5, int(value)))
        if level is None:
            level = self.default_level
        self.cache.update(value=level, checked_at=now)
        return level

    def _file_level(self, now: float) -> int | None:
        try:
            if not self.path.exists():
                return None
            modified = self.path.stat().st_mtime
            if self.cache["value"] is not None and modified == self.cache["file_mtime"]:
                self.cache["checked_at"] = now
                return int(self.cache["value"])
            text = self.path.read_text(encoding="utf-8").strip().upper()
            self.cache["file_mtime"] = modified
            if text in LOG_LEVELS:
                return LOG_LEVELS[text]
            if text.isdigit():
                return max(0, min(5, int(text)))
        except Exception:
            pass
        return None

    def reset_cache(self) -> None:
        self.cache.update(value=None, checked_at=0.0, file_mtime=0.0)

    def name(self, value: int | None = None) -> str:
        effective = self.current() if value is None else value
        return str(LOG_LEVEL_NAMES.get(int(effective), effective))

    def source(self) -> str:
        if self.path.exists():
            return "file"
        if self.environ.get("CIEL_RUNTIME_LOG_LEVEL", "").strip():
            return "env"
        return "default"

    def status(self) -> str:
        return f"{self.name()} ({self.source()})"

    def set(self, value: str) -> list[str]:
        try:
            level = normalize_log_level(value)
        except ValueError as error:
            return [f"{error}. Known levels: {', '.join(LOG_LEVELS)}, DEFAULT."]
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if level is None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.reset_cache()
            return [f"Log level reset to {self.status()}."]
        self.path.write_text(level + "\n", encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass
        self.reset_cache()
        return [f"Log level set to {level}."]


@dataclass(frozen=True, slots=True)
class RouterFileLogger:
    config_dir: Path
    path: Path
    max_bytes: int
    levels: LogLevelRepository

    def write(self, level: str, message: str) -> None:
        threshold = LOG_LEVELS.get(level, 0)
        if threshold <= 0 or threshold > self.levels.current():
            return
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self.path.replace(self.path.with_suffix(".log.1"))
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} [{level}] {message}\n")
        except Exception:
            pass
