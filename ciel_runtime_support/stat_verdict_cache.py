"""Stat-verified per-file verdicts for scans over many small, mostly unchanged record files.

A scan that must decide something about every file in a directory (for example
"is a reply still owed for this receipt") reads each file only when its stat
identity ``(st_dev, st_ino, st_size, st_mtime_ns)`` changed since the last scan.
Records are replaced atomically (a new file), so a rewrite always changes the
identity. A file modified within the quiet window is decided but not
remembered, so a same-tick in-place edit cannot be served stale (the same rule
as ``work_parallel_primitives.StatJsonCache``). Each scan keeps only the files
it saw, so memory follows the directory, not its history.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .work_parallel_primitives import StatJsonCache, _CACHE_QUIET_NS


class StatVerdictCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scans: dict[str, dict[str, tuple[tuple[int, int, int, int], Any]]] = {}

    def scan(self, scope: str, paths: Iterable[Path], decide: Callable[[Path], Any]):
        """Yield ``(path, verdict)`` for every path, calling ``decide`` only for new or changed files."""
        with self._lock:
            previous = self._scans.get(scope, {})
        current: dict[str, tuple[tuple[int, int, int, int], Any]] = {}
        complete = False
        try:
            for path in paths:
                key = StatJsonCache.stat_key(path)
                if key is None:
                    continue
                norm = os.path.normcase(str(path))
                hit = previous.get(norm)
                if hit is not None and hit[0] == key:
                    verdict = hit[1]
                else:
                    verdict = decide(path)
                    if time.time_ns() - key[3] <= _CACHE_QUIET_NS:
                        yield path, verdict
                        continue
                current[norm] = (key, verdict)
                yield path, verdict
            complete = True
        finally:
            # A scan stopped early saw only part of the directory: keep what it
            # learned without forgetting the files it did not reach.
            with self._lock:
                self._scans[scope] = current if complete else {**previous, **current}
