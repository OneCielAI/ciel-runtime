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

    def test_focused_subtrees_run_first_without_holding_other_runnable_work(self):
        t.update_node(self.doc, self.ids["Other one"], status="in_progress")
        self.focus("AAAA second two", "AAAA first")
        c = t.continuation(self.doc)
        self.assertEqual(c["next"]["id"], self.ids["AAAA second two"])
        # Focused leaves (3) and every other runnable leaf (3) remain actionable.
        self.assertEqual(c["actionable"], 6)
        self.assertEqual(c["held"], 0)
        self.assertEqual(self.doc["active_root"], self.ids["AAAA second"])
        self.assertEqual(t.root_phase(self.doc, self.ids["Other"]), "executing")
        self.assertEqual(self.doc["phase"], "planning")

    def test_finished_focus_releases_automatically(self):
        self.focus("AAAA first")
        for name in ("AAAA first one", "AAAA first two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        self.assertEqual(self.doc["focus_ids"], [])
        self.assertFalse(t.snapshot(self.doc)["focus"]["exhausted"])
        self.assertEqual(self.doc["active_root"], self.ids["Other"])
        self.assertEqual(t.continuation(self.doc)["next"]["id"], self.ids["Other one"])
        self.doc = t.normalize_document(json.loads(json.dumps(self.doc)))
        self.assertEqual(self.doc["focus_ids"], [])

    def test_waiting_focused_item_yields_to_other_runnable_work(self):
        self.focus("AAAA first", "AAAA second")
        t.update_node(self.doc, self.ids["AAAA first"], waiting_on="approval")
        c = t.continuation(self.doc)
        self.assertEqual(c["next"]["root_id"], self.ids["AAAA second"])
        t.select_root(self.doc, c["next"]["root_id"])
        for name in ("AAAA second one", "AAAA second two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        # Every focused step now waits; unrelated approved work keeps running and focus stays.
        c = t.continuation(self.doc)
        self.assertEqual(c["next"]["root_id"], self.ids["Other"])
        self.assertEqual(c["actionable"], 2)
        self.assertEqual(self.doc["focus_ids"], [self.ids["AAAA first"], self.ids["AAAA second"]])
        self.focus()

    def test_blocked_focused_leaf_does_not_block_other_work(self):
        self.focus("AAAA first two")
        self.assertEqual(t.continuation(self.doc)["next"]["id"], self.ids["AAAA first two"])
        t.update_node(self.doc, self.ids["AAAA first"], status="blocked")
        c = t.continuation(self.doc)
        self.assertEqual(c["next"]["root_id"], self.ids["Other"])
        self.assertEqual(t.snapshot(self.doc)["next"]["id"], self.ids["Other one"])

    def test_invalid_input_and_user_flag_are_atomic(self):
        for ids, flag in (([], False), (["missing"], True), ([1], True), ("n1", True), ([self.ids["Other"]]*2, True)):
            before = copy.deepcopy(self.doc)
            with self.assertRaises(t.WorkPlanError):
                t.set_focus(self.doc, ids, user_requested=flag)
            self.assertEqual(self.doc, before)
        self.doc["focus_ids"] = ["missing"]
        with self.assertRaises(t.WorkPlanError):
            t.normalize_document(self.doc)

    def test_deletion_and_replacement_stay_fail_closed_but_selection_is_priority_only(self):
        self.focus("AAAA first one")
        for change in (lambda: t.remove_node(self.doc, self.ids["AAAA first"]),
                       lambda: t.set_plan(self.doc, {"title": "replacement"}, self.ids["AAAA first"])):
            before = copy.deepcopy(self.doc)
            with self.assertRaises(t.WorkPlanError):
                change()
            self.assertEqual(self.doc, before)
        t.select_root(self.doc, self.ids["Other"], user_requested=True)
        self.assertEqual(self.doc["active_root"], self.ids["Other"])
        self.assertEqual(self.doc["focus_ids"], [self.ids["AAAA first one"]])

    def test_urgent_outranks_focus_but_focus_protects_the_urgent_hold(self):
        self.focus("AAAA first")
        urgent = t.add_urgent(self.doc, {"title": "Urgent", "children": [{"title": "Phase", "children": [{"title": "Step"}]}]}, user_requested=True)
        self.assertEqual(self.doc["active_root"], urgent)
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

    def test_finished_focus_does_not_block_new_goals_or_root_reorder(self):
        self.focus("AAAA first")
        for name in ("AAAA first one", "AAAA first two"):
            t.update_node(self.doc, self.ids[name], status="done", evidence="verified")
        self.assertEqual(self.doc["focus_ids"], [])
        added = t.add_subtree(self.doc, None, {"title": "New unrelated", "children": [{"title": "Step"}]})
        self.assertEqual(self.doc["active_root"], self.ids["Other"])
        t.reorder_root(self.doc, added, 1, user_requested=True)
        self.assertEqual(self.doc["active_root"], added)


if __name__ == "__main__":
    unittest.main()
