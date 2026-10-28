import copy
import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support import work_plan_tree as t
from ciel_runtime_support.work_mcp_tools import _run
from ciel_runtime_support.work_plan_store import WorkPlanStore, WorkPlanConflict


class WorkFocusTests(unittest.TestCase):
    def setUp(self):
        self.doc = t.empty_document()
        for name in ("Other", "AAAA first", "AAAA second"):
            t.set_plan(self.doc, {"title": name, "children": [{"title": "Phase", "children": [
                {"title": name + " one"}, {"title": name + " two"}]}]})
        self.ids = {n["title"]: n["id"] for n in self.doc["nodes"]}
        t.set_phase(self.doc, "executing")

    def focus(self, *names):
        t.set_focus(self.doc, [self.ids[n] for n in names], user_requested=True)

    def test_ordered_subtrees_override_unrelated_in_progress(self):
        t.update_node(self.doc, self.ids["Other one"], status="in_progress")
        before = {n["id"]: copy.deepcopy(n) for n in self.doc["nodes"]}
        self.focus("AAAA second two", "AAAA first")
        self.assertEqual(t.continuation(self.doc)["next"]["id"], self.ids["AAAA second two"])
        self.assertEqual(t.continuation(self.doc)["actionable"], 3)
        self.assertEqual(t.continuation(self.doc)["held"], 1)
        self.assertEqual({n["id"]: n for n in self.doc["nodes"]}, before)
        self.assertEqual(t.root_phase(self.doc, self.ids["Other"]), "executing")
        self.assertEqual(self.doc["phase"], "planning")

    def test_exhaustion_holds_until_explicit_clear_and_normalize_preserves(self):
        self.focus("AAAA first")
        for name in ("AAAA first one", "AAAA first two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        self.doc = t.normalize_document(json.loads(json.dumps(self.doc)))
        self.assertIsNone(t.continuation(self.doc)["next"])
        self.assertTrue(t.snapshot(self.doc)["focus"]["exhausted"])
        self.assertEqual(self.doc["active_root"], self.ids["AAAA first"])
        self.focus()
        self.assertEqual(self.doc["active_root"], self.ids["Other"])
        self.assertIsNotNone(t.continuation(self.doc)["next"])

    def test_waiting_selected_yields_to_next_selected_not_other(self):
        self.focus("AAAA first", "AAAA second")
        t.update_node(self.doc, self.ids["AAAA first"], waiting_on="approval")
        c = t.continuation(self.doc)
        self.assertEqual(c["next"]["root_id"], self.ids["AAAA second"])
        t.select_root(self.doc, c["next"]["root_id"])
        for name in ("AAAA second one", "AAAA second two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        self.assertIsNone(t.continuation(self.doc)["next"])
        self.assertFalse(t.snapshot(self.doc)["focus"]["exhausted"])

    def test_leaf_selection_respects_ancestor_block(self):
        self.focus("AAAA first two")
        self.assertEqual(t.continuation(self.doc)["next"]["id"], self.ids["AAAA first two"])
        self.assertEqual(t.snapshot(self.doc)["next"]["id"], self.ids["AAAA first two"])
        t.update_node(self.doc, self.ids["AAAA first"], status="blocked")
        self.assertIsNone(t.continuation(self.doc)["next"])

    def test_invalid_input_and_user_flag_are_atomic(self):
        for ids, flag in (([], False), (["missing"], True), ([1], True), ("n1", True), ([self.ids["Other"]]*2, True)):
            before = copy.deepcopy(self.doc)
            with self.assertRaises(t.WorkPlanError):
                t.set_focus(self.doc, ids, user_requested=flag)
            self.assertEqual(self.doc, before)
        self.doc["focus_ids"] = ["missing"]
        with self.assertRaises(t.WorkPlanError):
            t.normalize_document(self.doc)

    def test_deletion_replacement_and_unrelated_selection_fail_closed(self):
        self.focus("AAAA first one")
        for change in (lambda: t.remove_node(self.doc, self.ids["AAAA first"]),
                       lambda: t.set_plan(self.doc, {"title": "replacement"}, self.ids["AAAA first"]),
                       lambda: t.select_root(self.doc, self.ids["Other"], user_requested=True)):
            before = copy.deepcopy(self.doc)
            with self.assertRaises(t.WorkPlanError):
                change()
            self.assertEqual(self.doc, before)

    def test_urgent_cannot_be_released_by_focus(self):
        urgent = t.add_urgent(self.doc, {"title": "Urgent", "children": [{"title": "Phase", "children": [{"title": "Step"}]}]}, user_requested=True)
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            self.focus("AAAA first")
        self.assertEqual(self.doc, before)
        t.set_focus(self.doc, [urgent], user_requested=True)
        self.assertEqual(t.continuation(self.doc)["next"]["root_id"], urgent)

    def test_mcp_cas_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkPlanStore(Path(tmp)/"plan.json")
            store.transaction(lambda d: d.update(self.doc))
            result = _run("work_focus", {"node_ids": [self.ids["AAAA first"]], "user_requested": True, "expected_version": 1}, store, False)
            self.assertIn("focus updated", result)
            self.assertEqual(store.read()["focus_ids"], [self.ids["AAAA first"]])
            with self.assertRaises(WorkPlanConflict):
                _run("work_focus", {"node_ids": [], "user_requested": True, "expected_version": 1}, store, False)
            _run("work_focus", {"node_ids": [], "user_requested": True, "expected_version": 2}, store, False)
            self.assertEqual(store.read()["focus_ids"], [])

    def test_exhausted_focus_holds_new_goal_and_root_reorder(self):
        self.focus("AAAA first")
        for name in ("AAAA first one", "AAAA first two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        t.add_subtree(self.doc, None, {"title": "New unrelated", "children": [{"title": "Step"}]})
        self.assertEqual(self.doc["active_root"], self.ids["AAAA first"])
        with self.assertRaises(t.WorkPlanError):
            t.reorder_root(self.doc, self.ids["Other"], 1, user_requested=True)
        self.assertIsNone(t.continuation(self.doc)["next"])
