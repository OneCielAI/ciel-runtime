"""Cross-platform advisory lock primitive for durable local repositories."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


class ChannelFileLockBusy(OSError):
    """A Windows advisory lock acquisition exhausted its bounded wait."""


# ``LK_LOCK`` is opaque: on Windows it can report EDEADLK/EACCES even though
# its native wait was not the durable wait callers need.  Retry a bounded
# number of fresh acquisition attempts so a release racing a refused request
# can still win.  The bounded wait is now total time, not a fixed trickle:
# Andy 2026-11-21 showed a workspace manifest held through several git
# verification/snapshot commands outlasted a 3x50ms retry budget and a main
# review decision was still refused with ChannelFileLockBusy even after the
# bounded retry landed.  Keep the total wait under one second so callers that
# need a fast final refusal still get one, while ordinary hold windows settle.
_LOCK_ATTEMPTS = 11
_LOCK_WAIT_SECONDS = 0.05


@contextmanager
def exclusive_file_lock(path: Path):
    """Hold a cross-platform advisory lock beside a repository artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as lock_file:
        if os.name == 'nt':
            import msvcrt

            # Windows permits locking beyond EOF; no initialization write.
            # Retry only the contention errors. Other OS errors are final.
            last_error = None
            for attempt in range(_LOCK_ATTEMPTS):
                lock_file.seek(0)
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    last_error = None
                    break
                except OSError as error:
                    last_error = error
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if attempt + 1 < _LOCK_ATTEMPTS:
                        time.sleep(_LOCK_WAIT_SECONDS)
            if last_error is not None:
                raise ChannelFileLockBusy(
                    last_error.errno,
                    f'channel file lock acquisition busy: {lock_path}',
                ) from last_error
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
