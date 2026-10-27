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
        t.set_phase(self.doc, "executing")
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
        other = t.add_node(self.doc, None, "unplanned other goal")
        # The new goal queues; the planned active goal stays ready.
        self.assertTrue(t.readiness(self.doc)[0])
        t.select_root(self.doc, other, user_requested=True)
        self.assertFalse(t.readiness(self.doc)[0])

    def test_new_goals_queue_behind_the_unfinished_active_goal(self):
        root, phase, one, two = self.plan()
        t.set_phase(self.doc, "executing")
        second = t.set_plan(self.doc, {"title": "2. second", "children": [{"title": "p", "children": [{"title": "s"}]}]})
        third = t.add_node(self.doc, None, "3. third")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (root, "executing"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [second, third])
        snap = t.snapshot(self.doc)
        self.assertEqual([(q["id"], q["done"], q["total"]) for q in snap["queue"]], [(second, 0, 1), (third, 0, 0)])
        rendered = t.render(self.doc)
        self.assertIn(f"Queued goals (next first): {second} 2. second (pending); {third} 3. third (pending)", rendered)
        # A dependent request under a queued goal does not touch the active goal either.
        t.add_node(self.doc, third, "child of three")
        t.set_plan(self.doc, {"title": "2. second revised", "children": [{"title": "p2"}]}, second)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (root, "executing"))
        # Finishing the active goal activates the oldest queued goal in planning.
        t.update_node(self.doc, one, status="done", evidence="verified")
        t.update_node(self.doc, two, status="done", evidence="verified")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (second, "planning"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [third])
        self.assertIn(f"Finished goals: {root} goal", t.render(self.doc))

    def test_hybrid_scope_refinement_queue_and_approval_phases(self):
        root, phase, one, two = self.plan()
        t.set_phase(self.doc, "executing")
        refined = t.add_subtree(self.doc, one, {"title": "refinement", "children": [{"title": "verify"}]},
                                within_scope=True)
        self.assertEqual(self.doc["phase"], "executing")
        queued = t.set_plan(self.doc, {"title": "queued", "children": [nested(2)]})
        t.add_subtree(self.doc, queued, {"title": "more queue planning", "children": [nested(2)]})
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (root, "executing"))
        before = copy.deepcopy(self.doc)
        with self.assertRaisesRegex(t.WorkPlanError, "explicit user request"):
            t.select_root(self.doc, queued)
        self.assertEqual(self.doc, before)
        t.select_root(self.doc, queued, user_requested=True)
        self.assertEqual(self.doc["phase"], "planning")
        t.select_root(self.doc, root, user_requested=True)
        self.assertEqual(self.doc["phase"], "executing")
        t.add_node(self.doc, phase, "new scope")
        self.assertEqual(self.doc["phase"], "planning")
        t.set_phase(self.doc, "executing")
        t.select_root(self.doc, queued, user_requested=True)
        # Editing a paused goal without an explicit scope assertion revokes only that goal.
        t.add_node(self.doc, refined, "new paused scope")
        self.assertEqual(self.doc["phase"], "planning")
        t.select_root(self.doc, root, user_requested=True)
        self.assertEqual(self.doc["phase"], "planning")
        self.assertEqual(t.normalize_document(self.doc), self.doc)
        t.set_phase(self.doc, "executing")
        t.update_node(self.doc, one, note="changed scope")
        self.assertEqual(self.doc["phase"], "planning")

    def test_waiting_active_yields_only_to_first_runnable_queue_goal(self):
        root, _, one, two = self.plan()
        t.set_phase(self.doc, "executing")
        first = t.set_plan(self.doc, {"title": "first queue", "children": [nested(2)]})
        second = t.set_plan(self.doc, {"title": "second queue", "children": [nested(2)]})
        t.update_node(self.doc, one, waiting_on="external review")
        t.update_node(self.doc, two, status="blocked")
        self.assertEqual(t.continuation(self.doc)["next"]["root_id"], first)
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.select_root(self.doc, second)
        self.assertEqual(self.doc, before)
        t.select_root(self.doc, first)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (first, "planning"))
        self.assertEqual(t.root_phase(self.doc, root), "executing")
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [root, second])

    def test_yield_skips_blocked_queue_without_reordering_or_approving_it(self):
        root, _, one, two = self.plan()
        t.set_phase(self.doc, "executing")
        blocked = t.set_plan(self.doc, {"title": "blocked queue", "children": [nested(2)]})
        blocked_leaf = self.doc["nodes"][-1]["id"]
        runnable = t.set_plan(self.doc, {"title": "runnable queue", "children": [nested(2)]})
        t.update_node(self.doc, one, waiting_on="review")
        t.update_node(self.doc, two, status="blocked")
        t.update_node(self.doc, blocked_leaf, status="blocked")
        t.select_root(self.doc, runnable)
        self.assertEqual(self.doc["active_root"], runnable)
        self.assertEqual(self.doc["phase"], "planning")
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [root, blocked])
        self.assertEqual(t.root_phase(self.doc, blocked), "planning")

    def test_select_cannot_yield_when_every_goal_waits(self):
        _, _, one, two = self.plan()
        queued = t.set_plan(self.doc, {"title": "queued", "children": [nested(2)]})
        queued_leaf = self.doc["nodes"][-1]["id"]
        for leaf in (one, two, queued_leaf):
            t.update_node(self.doc, leaf, waiting_on="review")
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.select_root(self.doc, queued)
        self.assertEqual(self.doc, before)

    def test_urgent_waiting_active_does_not_yield_automatically(self):
        root, _, one, two = self.plan()
        t.set_phase(self.doc, "executing")
        queued = t.set_plan(self.doc, {"title": "queued", "children": [nested(2)]})
        self.doc["nodes"][0]["urgent"] = True
        t.update_node(self.doc, one, waiting_on="review")
        t.update_node(self.doc, two, status="blocked")
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.select_root(self.doc, queued)
        self.assertEqual(self.doc, before)

    def test_legacy_document_migrates_only_active_approval(self):
        root, *_ = self.plan()
        queued = t.add_node(self.doc, None, "queue")
        for n in self.doc["nodes"]:
            n.pop("phase", None)
        self.doc["phase"] = "executing"
        normalized = t.normalize_document(self.doc)
        self.assertEqual(t.root_phase(normalized, root), "executing")
        self.assertEqual(t.root_phase(normalized, queued), "planning")
        t.select_root(normalized, queued, user_requested=True)
        t.select_root(normalized, root, user_requested=True)
        self.assertEqual(normalized["phase"], "executing")

    def test_first_goal_and_goal_after_finished_one_become_active(self):
        root, _, one, two = self.plan()
        for leaf in (one, two):
            t.update_node(self.doc, leaf, status="done", evidence="verified")
        self.assertEqual(self.doc["active_root"], root)
        nxt = t.add_node(self.doc, None, "next")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (nxt, "planning"))

    def test_reopened_finished_goal_queues_and_removing_active_child_advances(self):
        root, phase, one, two = self.plan()
        t.update_node(self.doc, one, status="done", evidence="verified")
        second = t.add_node(self.doc, None, "second")
        second_step = t.add_node(self.doc, t.add_node(self.doc, second, "phase"), "step")
        self.assertEqual(self.doc["active_root"], root)
        t.remove_node(self.doc, two)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (second, "planning"))
        t.add_node(self.doc, phase, "dependent follow-up")
        self.assertEqual(self.doc["active_root"], second)
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [root])
        t.update_node(self.doc, second_step, status="done", evidence="verified")
        self.assertEqual(self.doc["active_root"], root)

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

    def test_update_semantic_noop_preserves_entire_document(self):
        root, _, one, _ = self.plan()
        t.set_phase(self.doc, "executing")
        t.update_node(self.doc, one, status="done", evidence="verified", now=20)
        for node_id in (root, one):
            item = next(n for n in self.doc["nodes"] if n["id"] == node_id)
            before = copy.deepcopy(self.doc)
            result = t.update_node(self.doc, node_id, status=item["status"],
                title=" " + item["title"] + " ", note="", evidence=item["evidence"],
                waiting_on=" \n\t ", ref="", now=100)
            self.assertEqual(self.doc, before)
            self.assertIs(result, item)
            t.update_node(self.doc, node_id, now=200)
            self.assertEqual(self.doc, before)

    def test_invalid_combined_update_does_not_mutate_node(self):
        _, _, one, _ = self.plan()
        before = copy.deepcopy(self.doc)
        with self.assertRaises(t.WorkPlanError):
            t.update_node(self.doc, one, status="in_progress", title=" ", now=100)
        self.assertEqual(self.doc, before)

    def test_reorder_siblings_at_every_nonroot_depth_preserves_nodes(self):
        for depth in range(1, t.MAX_DEPTH + 1):
            with self.subTest(depth=depth):
                doc = t.empty_document()
                parent = t.add_node(doc, None, "goal", now=1)
                for level in range(1, depth):
                    parent = t.add_node(doc, parent, str(level), now=1)
                siblings = [t.add_node(doc, parent, title, now=1) for title in ("one", "two", "three")]
                if depth < t.MAX_DEPTH:
                    t.add_node(doc, siblings[1], "descendant", now=2)
                other = t.add_node(doc, None, "other", now=1)
                t.add_node(doc, other, "unrelated", now=1)
                t.set_phase(doc, "executing")
                before = copy.deepcopy(doc)
                index = {n["id"]: copy.deepcopy(n) for n in doc["nodes"]}
                t.reorder_node(doc, siblings[2], 1)
                self.assertEqual([n["id"] for n in doc["nodes"] if n["parent_id"] == parent],
                                 [siblings[2], siblings[0], siblings[1]])
                self.assertEqual({n["id"]: n for n in doc["nodes"]}, index)
                self.assertEqual({k: v for k, v in doc.items() if k != "nodes"},
                                 {k: v for k, v in before.items() if k != "nodes"})
                after = copy.deepcopy(doc)
                t.reorder_node(doc, siblings[2], 1, user_requested=True)
                self.assertEqual(doc, after)
                self.assertEqual(t.normalize_document(doc), doc)

    def test_reorder_child_includes_finished_siblings_and_rejects_bad_inputs(self):
        _, phase, one, two = self.plan()
        t.update_node(self.doc, two, status="done", evidence="verified", now=20)
        t.reorder_node(self.doc, two, 1)
        self.assertEqual([n["id"] for n in self.doc["nodes"] if n["parent_id"] == phase], [two, one])
        before = copy.deepcopy(self.doc)
        for position in (None, 0, -1, 3, True, False, 1.0, "1"):
            with self.subTest(position=position), self.assertRaises(t.WorkPlanError):
                t.reorder_node(self.doc, one, position)
            self.assertEqual(self.doc, before)
        with self.assertRaises(t.WorkPlanError):
            t.reorder_node(self.doc, "missing", 1)
        self.assertEqual(self.doc, before)

    def test_reorder_node_root_delegates_guards_and_keeps_current_slot_noop(self):
        root, *_ = self.plan()
        second = t.add_node(self.doc, None, "second", now=10)
        third = t.add_node(self.doc, None, "third", now=10)
        t.select_root(self.doc, third, user_requested=True)
        t.set_phase(self.doc, "executing")
        before = copy.deepcopy(self.doc)
        # The active root is logically first even when its storage slot is last.
        t.reorder_node(self.doc, third, 1)
        self.assertEqual(self.doc, before)
        t.reorder_node(self.doc, root, 2)
        self.assertEqual(self.doc, before)
        with self.assertRaisesRegex(t.WorkPlanError, "explicit user request"):
            t.reorder_node(self.doc, second, 1)
        self.assertEqual(self.doc, before)
        t.reorder_node(self.doc, second, 1, user_requested=True)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (second, "planning"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [third, root])

    def test_root_reorder_from_finished_active_is_not_noop(self):
        finished = t.add_node(self.doc, None, "finished", now=1)
        t.update_node(self.doc, finished, status="done", evidence="verified", now=2)
        unfinished = t.add_node(self.doc, None, "unfinished", now=3)
        t.select_root(self.doc, finished, user_requested=True)
        t.reorder_node(self.doc, unfinished, 1, user_requested=True)
        self.assertEqual(self.doc["active_root"], unfinished)

    def test_falsey_non_list_children_rejected(self):
        for children in ({}, "", 0, False):
            with self.subTest(children=children), self.assertRaises(t.WorkPlanError):
                t.set_plan(t.empty_document(), {"title": "goal", "children": children})

class WorkContinuationTreeTests(unittest.TestCase):
    def setUp(self):
        self.doc = t.empty_document()

    def plan(self, title="goal", **fields):
        return t.set_plan(self.doc, {"title": title, **fields, "children": [
            {"title": title + " phase", "children": [{"title": title + " one"}, {"title": title + " two"}]}]}, now=10)

    def index(self):
        return {n["id"]: n for n in self.doc["nodes"]}

    def test_ref_wait_normalization_legacy_defaults_and_root_only_urgent(self):
        raw = dict(t.empty_document(), active_root="n1", nodes=[
            dict(node("n1"), ref=" \t연결\n  42\r\n", waiting_on="  review\tby\n peer ", urgent=True),
            dict(node("n2", "n1"), ref="r" * 100, waiting_on="w" * 200, urgent=True),
            node("n3", "n1"), dict(node("n4"), urgent="true"), dict(node("n5"), urgent=1)])
        doc = t.normalize_document(raw)
        root, child, legacy, text_flag, int_flag = doc["nodes"]
        self.assertEqual((root["ref"], root["waiting_on"], root["urgent"]), ("연결 42", "review by peer", True))
        self.assertEqual((child["ref"], child["waiting_on"], child["urgent"]),
                         ("r" * t.MAX_REF_CHARS, "w" * t.MAX_WAIT_CHARS, False))
        self.assertEqual((legacy["ref"], legacy["waiting_on"], legacy["urgent"]), ("", "", False))
        self.assertIs(text_flag["urgent"], False)
        self.assertIs(int_flag["urgent"], False)
        self.assertEqual(t.normalize_document(doc), doc)
        self.assertEqual(raw["nodes"][0]["ref"], " \t연결\n  42\r\n")  # no input mutation

    def test_ref_wait_add_update_and_replace_empty_clears(self):
        root = self.plan(ref=" id\n 7 ", waiting_on=" review\t pending ")
        child = t.add_node(self.doc, root, "child", ref=" issue\n8 ", waiting_on=" build\n9 ", now=11)
        self.assertEqual((self.index()[child]["ref"], self.index()[child]["waiting_on"]), ("issue 8", "build 9"))
        t.update_node(self.doc, root, note="keep labels", now=12)
        self.assertEqual((self.index()[root]["ref"], self.index()[root]["waiting_on"]), ("id 7", "review pending"))
        t.update_node(self.doc, child, ref="", waiting_on=" \n\t ", now=13)
        self.assertEqual((self.index()[child]["ref"], self.index()[child]["waiting_on"]), ("", ""))
        t.set_plan(self.doc, {"title": "revised", "children": [
            {"title": "phase", "ref": " p\t2 ", "waiting_on": " peer\n2 ", "children": [{"title": "step"}]}]}, root, now=14)
        self.assertEqual((self.index()[root]["ref"], self.index()[root]["waiting_on"]), ("id 7", "review pending"))
        self.assertEqual((self.doc["nodes"][1]["ref"], self.doc["nodes"][1]["waiting_on"]), ("p 2", "peer 2"))
        t.set_plan(self.doc, {"title": "clear", "ref": "", "waiting_on": "", "children": []}, root, now=15)
        self.assertEqual((self.index()[root]["ref"], self.index()[root]["waiting_on"]), ("", ""))
        self.assertEqual(t.normalize_document(self.doc), self.doc)

    def test_ref_wait_clipping_boundary_is_stable_on_roundtrip(self):
        # A clip at an inter-word space must not change on the next store read.
        for field, limit in (("ref", t.MAX_REF_CHARS), ("waiting_on", t.MAX_WAIT_CHARS)):
            with self.subTest(field=field):
                doc = t.empty_document()
                t.add_node(doc, None, "goal", **{field: "a" * (limit - 1) + " b"}, now=10)
                saved = doc["nodes"][0][field]
                loaded = t.normalize_document(doc)["nodes"][0][field]
                self.assertEqual(loaded, saved, f"{field}: saved={saved!r}, loaded={loaded!r}")

    def test_continuation_excludes_ancestor_waits_blocks_done_and_leaf_waits(self):
        root = t.set_plan(self.doc, {"title": "goal", "children": [
            {"title": "waiting branch", "waiting_on": "review", "children": [{"title": "w1"}, {"title": "w2"}]},
            {"title": "blocked branch", "children": [{"title": "b1"}, {"title": "b2"}]},
            {"title": "open", "children": [{"title": "leaf wait", "waiting_on": "build"},
                {"title": "blocked leaf"}, {"title": "done leaf"}, {"title": "ready"}]}]})
        ids = {n["title"]: n["id"] for n in self.doc["nodes"]}
        t.update_node(self.doc, ids["blocked branch"], status="blocked")
        t.update_node(self.doc, ids["blocked leaf"], status="blocked")
        t.update_node(self.doc, ids["done leaf"], status="done", evidence="verified")
        before = copy.deepcopy(self.doc)
        state = t.continuation(self.doc)
        self.assertEqual((state["actionable"], state["blocked"], state["held"]), (1, 3, 0))
        self.assertEqual(state["next"]["path"], ["goal", "open", "ready"])
        self.assertEqual([(r["id"], r["waiting_on"]) for r in state["waiting"]],
                         [(ids["waiting branch"], "review"), (ids["leaf wait"], "build")])
        self.assertEqual(self.doc, before)
        t.update_node(self.doc, root, waiting_on="root review")
        state = t.continuation(self.doc)
        self.assertEqual((state["actionable"], state["next"]), (0, None))
        self.assertEqual(state["waiting"], [{"id": root, "title": "goal", "waiting_on": "root review"}])
        t.update_node(self.doc, root, waiting_on="", status="blocked")
        self.assertEqual(t.continuation(self.doc)["actionable"], 0)
        t.update_node(self.doc, root, status="pending")
        self.assertEqual(t.continuation(self.doc)["next"]["id"], ids["ready"])

    def test_continuation_falls_back_to_queue_without_changing_active_or_phase(self):
        root = self.plan("active", waiting_on="peer")
        queued = self.plan("queued")
        later = self.plan("later")
        qleaves = [n for n in self.doc["nodes"] if n["title"] in ("queued one", "queued two")]
        t.update_node(self.doc, qleaves[1]["id"], status="in_progress")
        t.set_phase(self.doc, "executing")
        before = copy.deepcopy(self.doc)
        state = t.continuation(self.doc)
        self.assertEqual((state["actionable"], state["next"]["id"]), (2, qleaves[1]["id"]))
        self.assertEqual(state["next"]["path"], ["queued", "queued phase", "queued two"])
        self.assertEqual(self.doc, before)
        t.update_node(self.doc, queued, waiting_on="build")
        self.assertEqual(t.continuation(self.doc)["next"]["path"][0], "later")
        t.update_node(self.doc, later, status="blocked")
        self.assertIsNone(t.continuation(self.doc)["next"])
        t.update_node(self.doc, root, waiting_on="")
        self.assertEqual(t.continuation(self.doc)["next"]["path"][0], "active")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (root, "executing"))

    def test_continuation_waiting_rows_are_bounded_but_all_waits_exclude_leaves(self):
        t.set_plan(self.doc, {"title": "goal", "children": [
            {"title": str(i), "waiting_on": "peer " + str(i)} for i in range(t.MAX_WAITING_IN_CONTINUATION + 3)]})
        state = t.continuation(self.doc)
        self.assertEqual((state["actionable"], state["next"]), (0, None))
        self.assertEqual([r["title"] for r in state["waiting"]], [str(i) for i in range(t.MAX_WAITING_IN_CONTINUATION)])
        self.assertEqual(t.continuation(t.empty_document()),
                         {"actionable": 0, "next": None, "waiting": [], "blocked": 0, "held": 0})

    def test_nested_urgent_holds_queue_and_resumes_former_active_in_order(self):
        older = self.plan("older")
        former = self.plan("former")
        later = self.plan("later")
        t.select_root(self.doc, former, user_requested=True)  # local pure fixture, not the shared Work plan
        t.set_phase(self.doc, "executing")
        first = t.add_urgent(self.doc, {"title": "urgent one", "ref": "u1", "children": [
            {"title": "phase", "children": [{"title": "urgent step"}]}]}, user_requested=True)
        second = t.add_urgent(self.doc, {"title": "urgent two", "children": [
            {"title": "phase", "children": [{"title": "nested step"}]}]}, user_requested=True)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (second, "planning"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [first, former, older, later])
        self.assertEqual(t.normalize_document(self.doc), self.doc)
        state = t.snapshot(self.doc)
        self.assertIs(state["active_root"]["urgent"], True)
        self.assertTrue(all(row["held"] for row in state["queue"]))
        self.assertEqual((state["continuation"]["held"], state["continuation"]["next"]["title"]), (4, "nested step"))
        t.update_node(self.doc, second, waiting_on="external")
        self.assertEqual((t.continuation(self.doc)["actionable"], t.continuation(self.doc)["next"]), (0, None))
        t.update_node(self.doc, second, waiting_on="")
        t.update_node(self.doc, self.doc["nodes"][-1]["id"], status="done", evidence="nested urgent verified")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (first, "planning"))
        self.assertEqual(t.continuation(self.doc)["held"], 3)
        leaf = next(n["id"] for n in self.doc["nodes"] if n["title"] == "urgent step")
        t.update_node(self.doc, leaf, status="done", evidence="urgent verified")
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (former, "executing"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [older, later])
        self.assertEqual(t.continuation(self.doc)["held"], 0)
        self.assertTrue(all(not row["held"] for row in t.snapshot(self.doc)["queue"]))

    def test_urgent_requires_user_request_and_invalid_spec_is_atomic(self):
        self.plan()
        before = copy.deepcopy(self.doc)
        with self.assertRaisesRegex(t.WorkPlanError, "explicit user request"):
            t.add_urgent(self.doc, {"title": "unrequested"})
        for spec in (None, {"title": ""}, {"title": "partial", "children": [{"title": "ok"}, None]}):
            with self.subTest(spec=spec), self.assertRaises(t.WorkPlanError):
                t.add_urgent(self.doc, spec, user_requested=True)
            self.assertEqual(self.doc, before)

    def test_reorder_queue_default_and_explicit_active_change_preserve_subtrees(self):
        first = self.plan("first")
        second = self.plan("second")
        third = self.plan("third")
        children = {n["id"]: copy.deepcopy(n) for n in self.doc["nodes"] if n["parent_id"] is not None}
        t.set_phase(self.doc, "executing")
        t.reorder_root(self.doc, third, 2)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (first, "executing"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [third, second])
        before = copy.deepcopy(self.doc)
        for root, position in ((second, 1), (first, 3)):
            with self.assertRaisesRegex(t.WorkPlanError, "explicit user request"):
                t.reorder_root(self.doc, root, position)
            self.assertEqual(self.doc, before)
        t.reorder_root(self.doc, second, 1, user_requested=True)
        self.assertEqual((self.doc["active_root"], self.doc["phase"]), (second, "planning"))
        self.assertEqual([n["id"] for n in t.queued_roots(self.doc)], [first, third])
        self.assertEqual({n["id"]: n for n in self.doc["nodes"] if n["parent_id"] is not None}, children)
        self.assertEqual(t.normalize_document(self.doc), self.doc)

    def test_reorder_rejects_bad_positions_finished_roots_and_children_atomically(self):
        root = self.plan()
        finished = t.add_node(self.doc, None, "finished")
        t.update_node(self.doc, finished, status="done", evidence="verified")
        child = self.doc["nodes"][1]["id"]
        before = copy.deepcopy(self.doc)
        for position in (None, 0, -1, 2, True, False, 1.0, "1"):
            with self.subTest(position=position), self.assertRaises(t.WorkPlanError):
                t.reorder_root(self.doc, root, position, user_requested=True)
            self.assertEqual(self.doc, before)
        for invalid in ("missing", child, finished):
            with self.subTest(root=invalid), self.assertRaises(t.WorkPlanError):
                t.reorder_root(self.doc, invalid, 1, user_requested=True)
            self.assertEqual(self.doc, before)

    def test_snapshot_three_levels_metadata_counts_and_child_overflow(self):
        cap = t.MAX_BRANCH_CHILDREN_IN_SNAPSHOT
        root = t.set_plan(self.doc, {"title": "goal", "ref": "G", "children": [
            {"title": "branch", "ref": "B", "waiting_on": "review", "children": [
                {"title": str(i), "ref": "T" + str(i), "waiting_on": "build" if i == 1 else "",
                 "children": [{"title": "fourth level"}]} for i in range(cap + 2)]},
            {"title": "direct leaf"}]})
        fourth = next(n for n in self.doc["nodes"] if n["title"] == "fourth level")
        t.update_node(self.doc, fourth["id"], status="done", evidence="verified")
        before = copy.deepcopy(self.doc)
        state = t.snapshot(self.doc)
        branch, direct = state["breakdown"]
        self.assertEqual((state["active_root"]["id"], state["active_root"]["ref"]), (root, "G"))
        self.assertEqual((branch["ref"], branch["waiting_on"], branch["done"], branch["total"], branch["more"]),
                         ("B", "review", 1, cap + 2, 2))
        self.assertEqual([row["title"] for row in branch["children"]], [str(i) for i in range(cap)])
        self.assertEqual((branch["children"][0]["status"], branch["children"][1]["ref"],
                          branch["children"][1]["waiting_on"]), ("done", "T1", "build"))
        self.assertTrue(all("children" not in row for row in branch["children"]))
        self.assertEqual((direct["children"], direct["more"], direct["done"], direct["total"]), ([], 0, 0, 1))
        self.assertIn("[ G ] goal", t.render(self.doc))
        self.assertIn("[ B ] branch  ◷ review", t.render(self.doc))
        self.assertEqual(self.doc, before)

    def test_snapshot_counts_waiting_and_blocked_below_displayed_depth(self):
        cap = t.MAX_BRANCH_CHILDREN_IN_SNAPSHOT
        t.set_plan(self.doc, {"title": "goal", "waiting_on": "root review", "children": [
            {"title": "branch", "children": [
                {"title": "task", "children": [{"title": "deep wait", "waiting_on": "build"},
                                               {"title": "deep blocked"}, {"title": "deep done", "waiting_on": "old"}]},
                *[{"title": "filler " + str(i)} for i in range(cap - 1)],
                {"title": "cut blocked", "waiting_on": "approval"},
                {"title": "cut task", "children": [{"title": "cut wait", "waiting_on": "peer"}]}]}]})
        ids = {n["title"]: n["id"] for n in self.doc["nodes"]}
        t.update_node(self.doc, ids["deep blocked"], status="blocked")
        t.update_node(self.doc, ids["deep done"], status="done", evidence="verified")
        t.update_node(self.doc, ids["cut blocked"], status="blocked")
        before = copy.deepcopy(self.doc)
        state = t.snapshot(self.doc)
        branch = state["breakdown"][0]
        # The goal's own wait is shown on its title, not counted; a blocked node that also waits counts as blocked.
        self.assertEqual(state["attention"], {"waiting": 2, "blocked": 2})
        self.assertEqual(branch["children"][0]["hidden"], {"waiting": 1, "blocked": 1})
        self.assertEqual(branch["children"][1]["hidden"], {"waiting": 0, "blocked": 0})
        self.assertEqual((branch["more"], branch["more_attention"]), (2, {"waiting": 1, "blocked": 1}))
        self.assertEqual(state["next"]["waiting_on"], "build")
        self.assertEqual(self.doc, before)


class WorkPanelSnapshotTests(unittest.TestCase):
    def test_complete_root_index_exceeds_queue_preview_without_private_subtrees(self):
        doc = t.empty_document()
        active = t.add_node(doc, None, "active")
        roots = [t.add_node(doc, None, "queued " + str(i), note="PRIVATE")
                 for i in range(t.MAX_QUEUE_IN_SNAPSHOT + 5)]
        for root in roots:
            t.add_node(doc, root, "QUEUED CHILD", note="PRIVATE", within_scope=True)
        child = next(n for n in doc["nodes"] if n["parent_id"] == roots[-1])
        t.update_node(doc, child["id"], status="done", evidence="PRIVATE PROOF")
        before = copy.deepcopy(doc)
        state = t.snapshot(doc)
        self.assertEqual(len(state["queue"]), t.MAX_QUEUE_IN_SNAPSHOT)
        self.assertEqual([n["id"] for n in state["root_index"]], [active] + roots)
        self.assertEqual(state["root_index"][-1]["status"], "done")
        self.assertNotIn("PRIVATE", repr(state["root_index"]))
        self.assertNotIn("QUEUED CHILD", repr(state["root_index"]))
        self.assertEqual(doc, before)

    def test_active_completed_rows_include_deep_descendants_and_classify_by_status(self):
        doc = t.empty_document()
        t.set_plan(doc, {"title": "active", "children": [
            {"title": "phase", "children": [{"title": "task", "children": [
                {"title": "verified"}, {"title": "open"}]}]}]})
        leaf = next(n for n in doc["nodes"] if n["title"] == "verified")
        t.update_node(doc, leaf["id"], status="done", evidence="PRIVATE PROOF")
        state = t.snapshot(doc)
        self.assertEqual([n["id"] for n in state["completed_active"]], [leaf["id"]])
        self.assertNotIn("PRIVATE", repr(state["completed_active"]))
        # A root with all leaf counters done but its own state still open remains
        # open in the projection (including the empty 0/0 case).
        empty = t.empty_document()
        t.add_node(empty, None, "empty")
        state = t.snapshot(empty)
        self.assertEqual((state["done"], state["total"]), (0, 0))
        self.assertEqual(state["root_index"][0]["status"], "pending")
        self.assertEqual(state["completed_active"], [])
        self.assertEqual(t.snapshot(t.empty_document())["root_index"], [])

    def test_display_indexes_are_bounded_by_document_node_limit_and_preserve_held_metadata(self):
        doc = t.empty_document()
        t.add_node(doc, None, "active")
        doc["nodes"][0]["urgent"] = True
        for i in range(t.MAX_NODES - 1):
            t.add_node(doc, None, "queued " + str(i))
        state = t.snapshot(doc)
        self.assertEqual(len(state["root_index"]), t.MAX_NODES)
        self.assertFalse(state["root_index"][0]["held"])
        self.assertTrue(all(n["held"] for n in state["root_index"][1:]))


if __name__ == "__main__":
    unittest.main()
