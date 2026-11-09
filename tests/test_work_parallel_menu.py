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
                apply(config, "work_parallel_p1_limit", "2", lambda c: saved.append(dict(c)))
                apply(config, "work_parallel_launches_per_minute", "6", lambda c: saved.append(dict(c)))
                apply(config, "work_discovery_enabled", "on", lambda c: saved.append(dict(c)))
                self.assertEqual(store.read()["policy"]["priority_limits"], dict(P1=2, P2=3, P3=1, P4=1, P5=1))
                self.assertEqual(store.read()["policy"]["launches_per_minute"], 6)
                self.assertTrue(config["work_discovery"]["enabled"])
                self.assertEqual(len(saved), 3)
                with self.assertRaises(ValueError):
                    apply(config, "work_parallel_P2", "70%", lambda c: self.fail("threshold menu row removed"))

    def test_all_languages_localize_labels_and_save_without_changing_instruction(self):
        from ciel_runtime_support.recent_menu_text import LANGUAGES, menu_text
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "parallel.json")
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=store):
                for language in LANGUAGES:
                    config = {"language": language}
                    message = apply(config, "work_discovery_instruction", "on", lambda _: None)
                    self.assertEqual("on", config["work_discovery"]["instruction"])
                    self.assertIn(menu_text("work_parallel_saved", language), message)
                    projected = {key: (label, value) for label, key, value in rows(config)}
                    self.assertEqual("on", projected["work_discovery_instruction"][1])
                    self.assertEqual(menu_text("work_parallel_enabled", language),
                                     projected["work_parallel_enabled"][0])

    def test_orphan_cleanup_row_previews_and_releases_only_proven_dead_or_confirmed_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "parallel.json")
            def row(name, owner):
                version = store.read()["version"]
                d, ident = store.reserve([dict(item_id=name, priority="P1", created_at=1, approved=True, focused=True,
                                               dependencies_done=True, plan_version=1, usage=.05, role="worker", mode="fork")],
                                         scope="claude:old", expected_version=version, owner=owner)
                store.bind(ident, "host-" + name, expected_version=d["version"])
                return ident
            dead = row("dead", {"session_pid": 11, "identity": "birth-11"})
            live = row("live", {"session_pid": 22, "identity": "birth-22"})
            legacy = row("legacy", None)
            identities = {11: "", 22: "birth-22"}
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=store), \
                 patch("ciel_runtime_support.work_parallel_mcp.process_identity", side_effect=identities.get):
                before = store.read()
                shown = {key: value for _, key, value in rows({})}["work_parallel_orphans"]
                self.assertEqual(before, store.read())
                self.assertIn("dead=1", shown)
                self.assertIn("legacy_no_owner=1", shown)
                message = apply({}, "work_parallel_orphans", "apply", lambda _: None)
                self.assertIn("  released " + dead, message)
                leases = store.read()["leases"]
                self.assertEqual("orphaned", leases[dead]["state"])
                self.assertTrue(leases[live]["slot"] and leases[legacy]["slot"])
                with self.assertRaises(ValueError):
                    apply({}, "work_parallel_orphans", "legacy " + live[:8], lambda _: None)
                apply({}, "work_parallel_orphans", "legacy " + legacy[:8], lambda _: None)
                self.assertEqual("orphaned", store.read()["leases"][legacy]["state"])
                self.assertTrue(store.read()["leases"][live]["slot"])

    def test_invalid_value_does_not_save(self):
        with tempfile.TemporaryDirectory() as root:
            store = WorkParallelStore(Path(root) / "parallel.json")
            with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=store):
                with self.assertRaises(ValueError):
                    apply({}, "work_parallel_p3_limit", "0", lambda c: self.fail("saved"))


if __name__ == "__main__":
    unittest.main()
