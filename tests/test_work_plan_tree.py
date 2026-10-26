import copy
import unittest
from ciel_runtime_support import work_plan_tree as t


def nested(depth):
    spec = {"title": "leaf"}
    for i in range(depth):
        spec = {"title": str(i), "children": [spec]}
    return spec


def node(i, parent=None):
    return {"id": i, "parent_id": parent, "title": i, "status": "pending"}


class WorkPlanTreeTests(unittest.TestCase):
    def setUp(self):
        self.doc = t.empty_document()

    def plan(self):
        t.set_plan(self.doc, {"title": "goal", "children": [
            {"title": "phase", "children": [{"title": "one"}, {"title": "two"}]}]}, now=10)
        return [n["id"] for n in self.doc["nodes"]]

    def test_empty(self):
        self.assertEqual(t.progress(self.doc), (0, 0))
        self.assertIsNone(t.next_leaf(self.doc))
        self.assertFalse(t.readiness(self.doc)[0])
        self.assertEqual(t.snapshot(self.doc)["phase"], "planning")
        self.assertIn("No active goal", t.render(self.doc))

    def test_depth_cap(self):
        t.set_plan(self.doc, nested(t.MAX_DEPTH))
        self.assertEqual(len(self.doc["nodes"]), t.MAX_DEPTH + 1)
        leaf = self.doc["nodes"][-1]["id"]
        self.assertEqual(len(t.path_titles(self.doc, leaf)), t.MAX_DEPTH + 1)
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.add_node(self.doc, leaf, "overflow")
        self.assertEqual(self.doc, before)
        with self.assertRaises(t.WorkPlanError):
            t.set_plan(t.empty_document(), nested(t.MAX_DEPTH + 1))

    def test_node_cap(self):
        root = t.set_plan(self.doc, {"title": "goal", "children": [
            {"title": str(i)} for i in range(t.MAX_NODES - 1)]})
        self.assertEqual(len(self.doc["nodes"]), t.MAX_NODES)
        with self.assertRaises(t.WorkPlanError):
            t.add_node(self.doc, root, "overflow")
        with self.assertRaises(t.WorkPlanError):
            t.set_plan(t.empty_document(), {"title": "goal", "children": [
                {"title": str(i)} for i in range(t.MAX_NODES)]})

    def test_normalize_caps(self):
        for nodes in ([node(f"n{i}") for i in range(t.MAX_NODES + 1)],
                      [node(f"n{i}", f"n{i-1}" if i else None) for i in range(t.MAX_DEPTH + 2)]):
            with self.subTest(count=len(nodes)), self.assertRaises(t.WorkPlanError):
                t.normalize_document(dict(t.empty_document(), nodes=nodes))

    def test_malformed_documents_and_cycles(self):
        for raw in (None, [], {"nodes": {}}, {"nodes": [None]},
                    {"nodes": [node("")]}, {"nodes": [dict(node("a"), title=" ")]},
                    {"nodes": [node("a"), node("a")]}, {"nodes": [node("a", "missing")]},
                    {"nodes": [node("a", "b"), node("b", "missing")]},
                    {"nodes": [node("a", "a")]},
                    {"nodes": [node("a", "b"), node("b", "a")]}):
            with self.subTest(raw=raw), self.assertRaises(t.WorkPlanError):
                if isinstance(raw, dict):
                    raw = dict(t.empty_document(), **raw)
                t.normalize_document(raw)

    def test_normalize_allocator_unique(self):
        raw = t.empty_document()
        raw["nodes"] = [node("n1"), node("n7")]
        doc = t.normalize_document(raw)
        t.add_node(doc, None, "new")
        ids = [n["id"] for n in doc["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_completion_aggregation_and_reopen(self):
        root, phase, one, two = self.plan()
        self.doc["phase"] = "executing"
        t.update_node(self.doc, one, status="done", evidence="verified")
        self.assertEqual(t.progress(self.doc), (1, 2))
        index = {n["id"]: n for n in self.doc["nodes"]}
        self.assertEqual(index[root]["status"], "in_progress")
        t.update_node(self.doc, two, status="done", evidence="verified")
        self.assertTrue(all(n["status"] == "done" for n in self.doc["nodes"]))
        self.assertEqual(self.doc["phase"], "planning")
        self.assertFalse(t.readiness(self.doc)[0])
        self.assertIsNone(t.next_leaf(self.doc))
        t.update_node(self.doc, one, status="pending")
        self.assertNotEqual(index[root]["status"], "done")
        self.assertNotEqual(index[phase]["status"], "done")
        t.update_node(self.doc, one, status="done", evidence="verified")
        t.add_node(self.doc, phase, "new")
        self.assertNotEqual(index[root]["status"], "done")
        self.assertEqual(t.progress(self.doc), (2, 3))

    def test_unfinished_parent_rejected(self):
        root, phase, *_ = self.plan()
        for item in (root, phase):
            with self.assertRaises(t.WorkPlanError):
                t.update_node(self.doc, item, status="done")

    def test_readiness(self):
        root = t.add_node(self.doc, None, "goal")
        self.assertFalse(t.readiness(self.doc)[0])
        phase = t.add_node(self.doc, root, "phase")
        self.assertFalse(t.readiness(self.doc)[0])
        t.add_node(self.doc, phase, "action")
        self.assertTrue(t.readiness(self.doc)[0])
        t.add_node(self.doc, None, "unplanned other goal")
        self.assertFalse(t.readiness(self.doc)[0])

    def test_next_priority(self):
        _, _, one, two = self.plan()
        t.update_node(self.doc, one, status="blocked")
        self.assertEqual(t.next_leaf(self.doc)["id"], two)
        t.update_node(self.doc, one, status="in_progress")
        self.assertEqual(t.next_leaf(self.doc)["id"], one)

    def test_replace_remove_select(self):
        root, phase, one, two = self.plan()
        other = t.add_node(self.doc, None, "other")
        t.set_plan(self.doc, nested(2), root)
        ids = {n["id"] for n in self.doc["nodes"]}
        self.assertTrue({root, other}.issubset(ids))
        self.assertFalse({phase, one, two} & ids)
        self.assertEqual(self.doc["active_root"], root)
        self.assertEqual(t.remove_node(self.doc, root), 3)
        self.assertEqual(self.doc["active_root"], other)
        self.assertEqual(t.remove_node(self.doc, other), 1)
        self.assertIsNone(self.doc["active_root"])

    def test_invalid_replacement_atomic(self):
        root, *_ = self.plan()
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.set_plan(self.doc, {"title": "bad", "children": [{"title": "ok"}, {"title": ""}]}, root)
        self.assertEqual(self.doc, before)

    def test_bad_specs(self):
        for spec in (None, [], {"title": ""}, {"title": "x", "children": "bad"},
                     {"title": "x", "children": [None]}):
            with self.subTest(spec=spec), self.assertRaises(t.WorkPlanError):
                t.set_plan(t.empty_document(), spec)


    def test_done_leaf_requires_verification_evidence(self):
        _, _, leaf, _ = self.plan()
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.update_node(self.doc, leaf, status="done")
        self.assertEqual(self.doc, before)
        t.update_node(self.doc, leaf, evidence="test output retained")
        t.update_node(self.doc, leaf, status="done")
        self.assertEqual(t.progress(self.doc), (1, 2))

    def test_remove_unfinished_sibling_reaggregates_completion(self):
        root, phase, one, two = self.plan()
        t.update_node(self.doc, one, status="done", evidence="verified")
        self.assertEqual(t.remove_node(self.doc, two), 1)
        index = {n["id"]: n for n in self.doc["nodes"]}
        self.assertEqual(t.progress(self.doc), (1, 1))
        self.assertEqual(index[phase]["status"], "done")
        self.assertEqual(index[root]["status"], "done")

    def test_falsey_non_list_children_rejected(self):
        for children in ({}, "", 0, False):
            with self.subTest(children=children), self.assertRaises(t.WorkPlanError):
                t.set_plan(t.empty_document(), {"title": "goal", "children": children})

if __name__ == "__main__":
    unittest.main()
