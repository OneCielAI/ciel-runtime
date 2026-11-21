"""Windows advisory-lock acquisition is bounded but resilient when a request
timeout/refusal races a lock being released (Andy n2472, 2026-11-21)."""
import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ciel_runtime_support import durable_file_lock as lock_module
from ciel_runtime_support.durable_file_lock import exclusive_file_lock


class DurableFileLockTests(unittest.TestCase):
    def test_windows_retries_contention_behind_opaque_lk_lock_attempt(self):
        windows = Mock(LK_LOCK=1, LK_UNLCK=0)
        windows.locking.side_effect = [
            OSError(errno.EDEADLK, "Resource deadlock avoided"), None, None]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "work-parallel-workspaces" / "manifest.json"
            with (
                patch.object(lock_module.os, "name", "nt"),
                patch.object(lock_module, "_LOCK_ATTEMPTS", 3),
                patch.object(lock_module.time, "sleep") as sleep,
                patch.dict("sys.modules", msvcrt=windows),
            ):
                with exclusive_file_lock(path):
                    self.assertTrue(path.with_name(path.name + ".lock").exists())
        # One contention retry, one successful acquire, and the final unlock.
        self.assertEqual(3, windows.locking.call_count)
        self.assertEqual([1, 1, 0], [item.args[1] for item in windows.locking.call_args_list])
        sleep.assert_called_once_with(lock_module._LOCK_WAIT_SECONDS)


if __name__ == "__main__":
    unittest.main()
