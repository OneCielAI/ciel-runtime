"""Atomic durable JSON replacement shared by queue and mailbox persistence."""
from contextlib import contextmanager
import json
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Any


_LOCKS = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _io_lock(path: Path):
    if os.name != 'nt':
        yield
        return
    from .durable_file_lock import exclusive_file_lock
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
    # Avoid the Windows byte-lock API's one-second sleep between competing
    # threads in this process; the file lock still protects other processes.
    with lock, exclusive_file_lock(path.with_name(path.name + '.io')):
        yield


@contextmanager
def reader(path: Path):
    """Keep Windows snapshot reads separate from the local replacement commit."""
    if not path.is_file():
        with path.open('rb'):
            pass
    with _io_lock(path):
        with path.open('rb') as stream:
            yield stream


def save(path: Path, row: dict[str, Any]) -> None:
    raw = json.dumps(row, ensure_ascii=False).encode('utf8')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the name independent of (and shorter than) the target's: with the
    # 64-hex stem, priority-marker temps reached 262 characters on AAP hosts
    # and failed under the legacy 260-character Windows path limit.
    temporary = path.with_name(f'.{os.getpid()}.{time.time_ns()}.{os.urandom(4).hex()}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        with _io_lock(path):
            for attempt in range(20):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as error:
                    # Non-cooperating Windows readers may briefly deny replace.
                    # Retry the fsynced local commit only, never an external send.
                    if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32) or attempt == 19:
                        raise
                    time.sleep(0.002)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)
