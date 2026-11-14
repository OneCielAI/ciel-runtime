"""Errors and value checks shared by the parallel ledger and its sidecar stores.

They live apart from ``work_parallel`` so a sidecar such as ``work_parallel_p0``
can use them while ``work_parallel`` reads that sidecar, without an import cycle.
``work_parallel`` re-exports every name, so existing imports keep working.
"""
from __future__ import annotations

import math


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
