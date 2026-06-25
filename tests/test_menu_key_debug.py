import tempfile
import unittest
from pathlib import Path

import ciel_runtime


class MenuKeyDebugTests(unittest.TestCase):
    def test_menu_key_debug_log_uses_config_dir_not_global_tmp(self):
        self.assertEqual(ciel_runtime.CONFIG_DIR, ciel_runtime.MENU_KEY_DEBUG_PATH.parent)
        self.assertNotEqual(Path("/tmp/ca-key-debug.log"), ciel_runtime.MENU_KEY_DEBUG_PATH)

    def test_menu_key_debug_log_is_best_effort(self):
        original = ciel_runtime.MENU_KEY_DEBUG_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "nested" / "ca-key-debug.log"
                ciel_runtime.MENU_KEY_DEBUG_PATH = target
                ciel_runtime.append_menu_key_debug_log("hello\n")
                self.assertEqual("hello\n", target.read_text(encoding="utf-8"))

                ciel_runtime.MENU_KEY_DEBUG_PATH = Path(tmp)
                ciel_runtime.append_menu_key_debug_log("ignored\n")
        finally:
            ciel_runtime.MENU_KEY_DEBUG_PATH = original


if __name__ == "__main__":
    unittest.main()
