import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import WorkPlanConflict, WorkPlanStore, subscribe_work_plan_changes
from ciel_runtime_support.work_retention import WorkRetentionService, retention_status


class WorkRetentionTests(unittest.TestCase):
    def plan(self):
        doc = tree.empty_document()
        tree.set_plan(doc, {"title": "Goal", "children": [{"title": "Phase", "children": [
            {"title": "Old done"}, {"title": "Still open"}]}]}, now=1)
        tree.set_phase(doc, "executing")
        tree.update_node(doc, "n3", status="done", evidence="verified", now=100)
        return doc

    def test_boundary_and_unfinished_progress(self):
        doc = self.plan()
        self.assertEqual(tree.expire_completed(doc, now=86499), 0)
        before = copy.deepcopy(doc)
        self.assertEqual(tree.expire_completed(doc, now=86500), 1)
        self.assertNotIn("n3", {n["id"] for n in doc["nodes"]})
        self.assertEqual(tree.progress(doc), (1, 2))
        self.assertEqual(doc["phase"], "executing")
        self.assertEqual(tree.continuation(doc)["next"]["id"], "n4")
        for node in doc["nodes"]:
            old = next(n for n in before["nodes"] if n["id"] == node["id"])
            self.assertEqual(node["updated_at"], old["updated_at"])
        self.assertEqual(tree.expire_completed(doc, now=86501), 0)

    def test_note_edit_and_reopen_completion_clock(self):
        doc = self.plan()
        tree.update_node(doc, "n3", note="later note", now=200)
        self.assertEqual(doc["nodes"][2]["completed_at"], 100)
        tree.update_node(doc, "n3", status="in_progress", now=300)
        self.assertIsNone(doc["nodes"][2]["completed_at"])
        self.assertEqual(tree.expire_completed(doc, now=86500), 0)
        tree.update_node(doc, "n3", status="done", now=500)
        self.assertEqual(doc["nodes"][2]["completed_at"], 500)
        self.assertEqual(tree.expire_completed(doc, now=86500), 0)

    def test_ancestor_completion_and_reopen(self):
        doc = self.plan()
        tree.update_node(doc, "n4", status="done", evidence="verified", now=200)
        self.assertEqual(doc["nodes"][0]["completed_at"], 200)
        tree.update_node(doc, "n4", status="pending", now=300)
        self.assertIsNone(doc["nodes"][0]["completed_at"])
        self.assertIsNone(doc["nodes"][1]["completed_at"])

    def test_subtree_expiration_and_stable_ids(self):
        doc = self.plan()
        tree.update_node(doc, "n4", status="done", evidence="verified", now=200)
        self.assertEqual(tree.expire_completed(doc, now=86500), 1)
        self.assertEqual(tree.progress(doc), (2, 2))
        self.assertEqual(tree.expire_completed(doc, now=86600), 3)
        self.assertEqual(doc["nodes"], [])
        self.assertIsNone(doc["active_root"])
        self.assertEqual(doc["next_id"], 5)

    def test_focus_preserved(self):
        doc = self.plan()
        doc["focus_ids"] = ["n3"]
        self.assertEqual(tree.expire_completed(doc, now=90000), 0)
        self.assertEqual(doc["focus_ids"], ["n3"])
        doc["focus_ids"] = []
        self.assertEqual(tree.expire_completed(doc, now=90000), 1)

    def test_legacy_and_unknown_clock(self):
        doc = self.plan()
        del doc["nodes"][2]["completed_at"]
        self.assertEqual(tree.expire_completed(doc, now=86500), 1)
        doc = self.plan()
        del doc["nodes"][2]["completed_at"]
        doc["nodes"][2]["updated_at"] = 0
        self.assertEqual(tree.expire_completed(doc, now=90000), 0)
        self.assertEqual(doc["nodes"][2]["completed_at"], 90000)
        self.assertEqual(tree.expire_completed(doc, now=176400), 1)

    def test_future_and_invalid_clock(self):
        self.assertEqual(tree.expire_completed(self.plan(), now=50), 0)
        for value in (True, -1, "100", float("nan"), float("inf")):
            candidate = self.plan()
            candidate["nodes"][2]["completed_at"] = value
            with self.subTest(value=value), self.assertRaises(tree.WorkPlanError):
                tree.normalize_document(candidate)

    def test_atomic_version_and_events(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WorkPlanStore(Path(temp) / "work-plan.json")
            saved, _ = store.transaction(lambda d: d.update(self.plan()))
            events = []
            unsubscribe = subscribe_work_plan_changes(lambda _p, before, after: events.append((before["version"], after["version"])))
            try:
                self.assertEqual(store.expire_completed(now=86500), 1)
            finally:
                unsubscribe()
            self.assertEqual(events, [(saved["version"], saved["version"] + 1)])
            self.assertEqual(store.read_snapshot()["nodes"][1]["expired_done_steps"], 1)
            with self.assertRaises(WorkPlanConflict):
                store.transaction(lambda d: tree.update_node(d, "n4", note="stale"), expected_version=saved["version"])
            before = store.path.read_bytes()
            store.read_snapshot()
            self.assertEqual(store.path.read_bytes(), before)

    def test_service_off_corrupt_scope_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "work-sessions" / ("a" * 64) / "work-plan.json"
            store = WorkPlanStore(path)
            store.transaction(lambda d: d.update(self.plan()))
            other = root / "outside.json"
            other.write_text("owned external file", encoding="utf-8")
            corrupt = path.parent.with_name("b" * 64) / path.name
            corrupt.parent.mkdir()
            corrupt.write_text("{broken", encoding="utf-8")
            config = {"work_completed_auto_delete": False}
            service = WorkRetentionService(root, lambda: config, lambda *_: None, interval=1)
            self.assertEqual(service.sweep(), 0)
            config["work_completed_auto_delete"] = True
            with patch("ciel_runtime_support.work_plan_tree.time.time", return_value=86500):
                self.assertEqual(service.sweep(), 1)
            self.assertEqual(corrupt.read_text(), "{broken")
            self.assertEqual(list(corrupt.parent.glob("*.corrupt-*")), [])
            self.assertEqual(other.read_text(), "owned external file")
            service.start()
            service.stop()
            self.assertFalse(service._thread.is_alive())

    def test_policy(self):
        self.assertEqual(retention_status({})["completed_after_seconds"], 86400)
        self.assertTrue(retention_status({})["enabled"])


class WorkRetentionIntegrationTests(unittest.TestCase):
    def test_menu_option_and_persistence(self):
        import ciel_runtime
        from ciel_runtime_support.llm_option_config import set_work_mode_option
        cfg = copy.deepcopy(ciel_runtime.DEFAULT_CONFIG)
        provider = cfg["current_provider"]
        rows, keys = ciel_runtime.llm_option_panel_rows(provider, cfg["providers"][provider], "en")
        self.assertIn("work_completed_auto_delete", keys)
        self.assertTrue(any("24h" in row for row in rows))
        saved = []
        lines = set_work_mode_option(cfg, "work_completed_auto_delete", "off", ciel_runtime.parse_bool, lambda d: saved.append(copy.deepcopy(d)))
        self.assertFalse(saved[-1]["work_completed_auto_delete"])
        self.assertIn("off", lines[0])
        set_work_mode_option(cfg, "work_completed_auto_delete", "on", ciel_runtime.parse_bool, lambda _: None)
        self.assertTrue(cfg["work_completed_auto_delete"])

    def test_pruned_parent_can_finish_without_losing_old_progress(self):
        doc = WorkRetentionTests().plan()
        tree.expire_completed(doc, now=86500)
        tree.update_node(doc, "n4", status="done", evidence="verified", now=86600)
        self.assertEqual(tree.progress(doc), (2, 2))
        self.assertEqual(doc["nodes"][0]["status"], "done")
        normalized = tree.normalize_document(doc)
        self.assertEqual(tree.progress(normalized), (2, 2))
        self.assertEqual(tree.expire_completed(normalized, now=173000), 3)

    def test_corrupt_done_parent_never_deletes_unfinished_child(self):
        doc = WorkRetentionTests().plan()
        doc["nodes"][1].update(status="done", completed_at=1)
        self.assertEqual(tree.expire_completed(doc, now=90000), 1)
        self.assertIn("n4", {n["id"] for n in doc["nodes"]})

    def test_focus_parent_protects_all_completed_descendants(self):
        doc = WorkRetentionTests().plan()
        doc["focus_ids"] = ["n2"]
        self.assertEqual(tree.expire_completed(doc, now=90000), 0)
        self.assertEqual(doc["focus_ids"], ["n2"])

    def test_recent_done_parent_does_not_turn_into_unverified_leaf(self):
        doc = WorkRetentionTests().plan()
        tree.update_node(doc, "n4", status="done", evidence="verified", now=101)
        # A recently touched completed parent can outlive both children.
        doc["nodes"][1]["completed_at"] = 1000
        doc["nodes"][0]["completed_at"] = 1000
        self.assertEqual(tree.expire_completed(doc, now=86600), 2)
        self.assertEqual(tree.progress(doc), (2, 2))
        self.assertEqual(tree.snapshot(doc)["continuation"]["actionable"], 0)


class WorkRetentionSafetyTests(unittest.TestCase):
    def test_concurrent_cleanup_reopen_serializes_without_losing_pending_work(self):
        import concurrent.futures
        import threading
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "work-plan.json"
            first = WorkPlanStore(path)
            first.transaction(lambda d: d.update(WorkRetentionTests().plan()))
            barrier = threading.Barrier(2)
            def reopen():
                barrier.wait(timeout=5)
                return WorkPlanStore(path).transaction(lambda d: tree.update_node(d, "n4", status="in_progress"))
            def cleanup():
                barrier.wait(timeout=5)
                return WorkPlanStore(path).expire_completed(now=90000)
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                a, b = pool.submit(reopen), pool.submit(cleanup)
                a.result(timeout=10)
                b.result(timeout=10)
            doc = first.read_snapshot()
            self.assertEqual(next(n for n in doc["nodes"] if n["id"] == "n4")["status"], "in_progress")
            self.assertNotIn("n3", {n["id"] for n in doc["nodes"]})
            self.assertEqual(tree.progress(doc), (1, 2))
            self.assertEqual(tree.continuation(doc)["actionable"], 1)

    def test_cleanup_noop_does_not_change_version_or_file_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = WorkPlanStore(Path(temp) / "work-plan.json")
            doc, _ = store.transaction(lambda d: d.update(WorkRetentionTests().plan()))
            before = store.path.read_bytes()
            self.assertEqual(store.expire_completed(now=200), 0)
            self.assertEqual(before, store.path.read_bytes())
            self.assertEqual(doc["version"], store.read_snapshot()["version"])

    def test_symbolic_session_folders_are_never_followed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            external = root / "outside"
            external.mkdir()
            path = external / "work-plan.json"
            store = WorkPlanStore(path)
            store.transaction(lambda d: d.update(WorkRetentionTests().plan()))
            sessions = root / "state/work-sessions"
            sessions.mkdir(parents=True)
            try:
                (sessions / ("a" * 64)).symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            before = path.read_bytes()
            service = WorkRetentionService(root / "state", lambda: {}, lambda *_: None)
            with patch("ciel_runtime_support.work_plan_tree.time.time", return_value=90000):
                self.assertEqual(service.sweep(), 0)
            self.assertEqual(path.read_bytes(), before)

    def test_completed_root_expiry_never_changes_active_approval_or_order(self):
        doc = WorkRetentionTests().plan()
        old = tree.add_subtree(doc, None, {"title": "Old root", "children": [{"title": "Verified child"}]}, now=1)
        child = doc["nodes"][-1]["id"]
        tree.update_node(doc, child, status="done", evidence="verified", now=2)
        active, phase = doc["active_root"], doc["phase"]
        self.assertEqual(tree.expire_completed(doc, now=90000), 3)
        self.assertEqual(doc["active_root"], active)
        self.assertEqual(doc["phase"], phase)
        self.assertNotIn(old, {n["id"] for n in doc["nodes"]})


class WorkRetentionReplanTests(unittest.TestCase):
    def test_replacing_plan_clears_retired_counts_and_completion_clock(self):
        doc = WorkRetentionTests().plan()
        tree.expire_completed(doc, now=90000)
        doc["nodes"][0]["expired_done_steps"] = 5
        doc["nodes"][0]["completed_at"] = 1
        tree.set_plan(doc, {"title": "Replacement", "children": [{"title": "Phase", "children": [
            {"title": "New task"}]}]}, root_id="n1", now=90001)
        self.assertNotIn("completed_at", doc["nodes"][0])
        self.assertNotIn("expired_done_steps", doc["nodes"][0])
        self.assertEqual(tree.progress(doc), (0, 1))
        self.assertEqual(tree.expire_completed(doc, now=180000), 0)
