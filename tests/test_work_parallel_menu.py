import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_menu import apply, rows


class ParallelMenuTests(unittest.TestCase):
    def test_menu_and_policy_share_ledger(self):
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "parallel.json")
            config = {}
            saved = []
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=store):
                self.assertEqual(len(rows(config)), 13)
                apply(config, "work_parallel_max_active", "2", lambda c: saved.append(dict(c)))
                apply(config, "work_parallel_P2", "70%", lambda c: saved.append(dict(c)))
                apply(config, "work_discovery_enabled", "on", lambda c: saved.append(dict(c)))
                self.assertEqual(store.read()["policy"]["max_active"], 2)
                self.assertEqual(store.read()["policy"]["thresholds"]["P2"], .7)
                self.assertTrue(config["work_discovery"]["enabled"])
                self.assertEqual(len(saved), 3)

    def test_invalid_value_does_not_save(self):
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "parallel.json")
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=store):
                with self.assertRaises(ValueError):
                    apply({}, "work_parallel_max_active", "0", lambda c: self.fail("saved"))


if __name__ == "__main__":
    unittest.main()
