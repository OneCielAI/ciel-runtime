"""Errors and value checks shared by the parallel ledger and its sidecar stores.

They live apart from ``work_parallel`` so a sidecar such as ``work_parallel_p0``
can use them while ``work_parallel`` reads that sidecar, without an import cycle.
``work_parallel`` re-exports every name, so existing imports keep working.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

REPLACE_ATTEMPTS = 20
REPLACE_WAIT_SECONDS = .002
_CACHE_QUIET_NS = 50_000_000
_CACHE_MAX_ENTRIES = 32


class StatJsonCache:
    """Thread-safe stat-verified cache for validated JSON state files.

    Stores already-validated, migrated JSON text keyed by normalized path and
    ``(st_dev, st_ino, st_size, st_mtime_ns)``. Returning ``json.loads(text)``
    in C avoids disk reads, Windows advisory locks, pure-Python
    ``object_pairs_hook`` checks, full schema re-validation, and deep-copy
    overhead while still returning an independent dict per caller.

    External writes within ``_CACHE_QUIET_NS`` of ``st_mtime_ns`` bypass the
    cache so same-tick in-place edits cannot serve stale data; commits recorded
    via ``put_committed`` are trusted immediately for their exact stat tuple.
    """

    def __init__(self, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[tuple[int, int, int, int], str, bool]] = OrderedDict()
        self._max_entries = max_entries

    @staticmethod
    def stat_key(path: Path) -> tuple[int, int, int, int] | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (int(st.st_dev), int(st.st_ino), int(st.st_size), int(st.st_mtime_ns))

    @staticmethod
    def _norm(path: Path) -> str:
        return os.path.normcase(str(path))

    def get(self, path: Path) -> tuple[Any, tuple[int, int, int, int] | None]:
        key = self.stat_key(path)
        if key is None:
            return None, None
        norm = self._norm(path)
        with self._lock:
            hit = self._entries.get(norm)
            if hit is not None and hit[0] == key:
                trusted = hit[2] or (time.time_ns() - key[3] > _CACHE_QUIET_NS)
                if trusted:
                    self._entries.move_to_end(norm)
                    raw_text = hit[1]
                else:
                    raw_text = None
            else:
                raw_text = None
        if raw_text is None:
            return None, key
        return json.loads(raw_text), key

    def put(self, path: Path, state: dict, *, stat_key: tuple[int, int, int, int] | None = None,
            committed: bool = False) -> None:
        key = stat_key if stat_key is not None else self.stat_key(path)
        if key is None:
            return
        if not committed and time.time_ns() - key[3] <= _CACHE_QUIET_NS:
            return
        raw_text = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        norm = self._norm(path)
        with self._lock:
            self._entries[norm] = (key, raw_text, committed)
            self._entries.move_to_end(norm)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def invalidate(self, path: Path) -> None:
        norm = self._norm(path)
        with self._lock:
            self._entries.pop(norm, None)


PARALLEL_JSON_CACHE = StatJsonCache()


def replace_with_retry(tmp, path):
    """os.replace, retried briefly while Windows refuses it (WinError 5).

    Any open handle on the target refuses a replace for as long as it is open
    (measured 2026-11-20). The ledger has retried this since 8eee6e8; a store
    that did not raised it to the router (Andy, stranded store, 11:38:32Z).
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_WAIT_SECONDS)


class WorkParallelError(ValueError):
    pass


class WorkParallelConflict(WorkParallelError):
    pass


class WorkParallelStateUnavailable(WorkParallelError):
    """A stored parallel file could not be read or is invalid; nothing was changed."""


def require(ok, message):
    if not ok:
        raise WorkParallelError(message)


def integer(v, low=0, high=2**53-1):
    return type(v) is int and low <= v <= high


def number(v, high=1):
    return type(v) in (int, float) and math.isfinite(v) and 0 <= v <= high


def text(v, limit=2000):
    return type(v) is str and 0 < len(v) <= limit and chr(0) not in v


def unique_object(pairs):
    d = {}
    for k, v in pairs:
        require(k not in d, "duplicate JSON key")
        d[k] = v
    return d
