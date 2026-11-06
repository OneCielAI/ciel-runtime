"""Cross-platform advisory lock primitive for durable local repositories."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path


class ChannelFileLockBusy(OSError):
    """A Windows advisory lock acquisition exhausted its bounded wait."""


@contextmanager
def exclusive_file_lock(path: Path):
    """Hold a cross-platform advisory lock beside a repository artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as lock_file:
        if os.name == 'nt':
            import msvcrt

            # Windows permits locking beyond EOF; no initialization write.
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise ChannelFileLockBusy(error.errno, f'channel file lock acquisition busy: {lock_path}') from error
                raise
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
