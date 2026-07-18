"""Configuration persistence port with atomic JSON file storage."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class JsonConfigRepository:
    """Own configuration caching, persistence, migration, and normalization."""

    def __init__(
        self,
        *,
        path: Path,
        defaults: dict[str, Any],
        merge: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        migrate: Callable[[dict[str, Any]], None],
        normalize: Callable[[dict[str, Any]], None],
    ) -> None:
        self._path = path
        self._defaults = defaults
        self._merge = merge
        self._migrate = migrate
        self._normalize = normalize
        self._cache: dict[str, Any] | None = None
        self._cache_mtime = 0.0

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._cache is not None and mtime == self._cache_mtime:
            return copy.deepcopy(self._cache)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        except (OSError, ValueError, TypeError):
            data = {}
        config = self._merge(self._defaults, data if isinstance(data, dict) else {})
        self._migrate(config)
        self._normalize(config)
        self._cache = config
        self._cache_mtime = mtime
        return copy.deepcopy(config)

    def save(self, config: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        temporary.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self._path)
        self._cache = copy.deepcopy(config)
        try:
            self._cache_mtime = self._path.stat().st_mtime
        except OSError:
            self._cache_mtime = 0.0

    def invalidate(self) -> None:
        self._cache = None
        self._cache_mtime = 0.0


__all__ = ["JsonConfigRepository"]
