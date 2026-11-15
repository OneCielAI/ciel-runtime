"""Python stacks of every thread when a Ciel process dies on a fatal fault.

The G:\\ciel-Walkie router died twice on 2026-11-15 inside CPython
(python314.dll access violation in FOR_ITER_LIST, a NULL iterator); the
minidump held no heap, so the Python function was unknown. ``faulthandler``
writes the Python traceback of each thread straight from the fault handler,
without the heap, so the next crash names its code.

The file sits beside the process's pid file. Each start appends one header
line; a fault after the last header belongs to the previous run, and the next
start reports it in one line. A Windows SEH exception that native code later
handles (for example inside a ctypes call) is written too, so a record is a
lead, not proof that the process died.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path
from typing import Any

FILE_NAME = "router-fault.log"
MAX_BYTES = 4 * 1024 * 1024
_TAIL_BYTES = 256 * 1024
_HEADER = "=== "
_FAULT_MARKERS = ("Windows fatal exception", "Fatal Python error")
# Kept open for the life of the process: faulthandler writes to its descriptor.
_handle: Any = None


def previous_fault(path: Path) -> str:
    """One line describing a fault the previous run recorded, else empty."""

    try:
        with path.open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - _TAIL_BYTES))
            text = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    start = text.rfind("\n" + _HEADER)
    header_end = text.find("\n", start + 1) if start >= 0 else -1
    body = text[header_end + 1:] if header_end >= 0 else text
    header = text[start + 1:header_end] if start >= 0 and header_end >= 0 else ""
    fault = next((line.strip() for line in body.splitlines() if line.strip().startswith(_FAULT_MARKERS)), "")
    if not fault:
        return ""
    lines = body.splitlines()
    frames: list[str] = []
    for index, line in enumerate(lines):
        if line.startswith("Current thread"):
            for row in lines[index + 1:]:
                if not row.strip().startswith("File "):
                    break
                frames.append(row.strip())
            break
    started = header.split(" started=", 1)[1].split(" ", 1)[0] if " started=" in header else "-"
    # The innermost frames, innermost first: native callers sit above stdlib.
    return f"{fault} (run started {started}); innermost: {' <- '.join(frames[:3]) or '-'}"[:800]


def enable(path: Path, label: str) -> str:
    """Start recording faults to ``path``; returns the previous run's fault, if any."""

    global _handle
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        earlier = previous_fault(path)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
        handle = open(path, "a", encoding="utf-8")
        handle.write(f"\n{_HEADER}{label} pid={os.getpid()} started={time.strftime('%Y-%m-%dT%H:%M:%S')} "
                     f"python={sys.version.split()[0]}\n")
        handle.flush()
        faulthandler.enable(file=handle, all_threads=True)
        _handle = handle
        return earlier
    except (OSError, ValueError, RuntimeError):
        return ""


__all__ = ["FILE_NAME", "enable", "previous_fault"]
