import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelStore


class QueueDispatchOrderTests(unittest.TestCase):
    """Dispatch order inside one priority lane: queue order, oldest first.

    User order 2026-11-23: a priority's forks run in queue order up to the
    configured parallel count. ``queued_roots`` keeps the queue order in
    ``doc["nodes"]``; a scheduler that re-sorts derived candidates by newest
    ``created_at`` hands every free slot to the goal added last and starves
    the goal that was queued first.
    """

    def board(self):
        doc = tree.empty_document()
        self.first_queued = tree.add_node(doc, None, "first queued", now=1_000, priority="P1")
        self.first_leaf = tree.add_node(doc, self.first_queued, "first leaf", now=1_010)
        self.second_queued = tree.add_node(doc, None, "second queued", now=2_000, priority="P1")
        self.second_leaf = tree.add_node(doc, self.second_queued, "second leaf", now=2_010)
        for root in (self.first_queued, self.second_queued):
            tree.set_phase(doc, tree.PHASE_EXECUTING, root)
        return doc

    def test_ordinary_lane_dispatches_the_first_queued_goal_first(self):
        rows = [row for row in tree.runnable_leaves(self.board()) if row["priority"] == "P1"]
        self.assertEqual([self.first_leaf, self.second_leaf],
                         [row["item_id"] for row in rows])

    def test_p0_lane_dispatches_the_first_queued_goal_first(self):
        from ciel_runtime_support.work_parallel_mcp import _p0_runnable_leaves, p0_state
        doc = self.board()
        with TemporaryDirectory() as temp:
            store = WorkParallelStore(Path(temp) / "work-parallel.json")
            p0 = p0_state(store)
            nodes = {node["id"]: node for node in doc["nodes"]}
            for root in (self.first_queued, self.second_queued):
                p0.activate_root("claude:s", root, nodes[root]["created_at"], set_by="user",
                                 expected_version=p0.read()["version"])
            rows = _p0_runnable_leaves(store, doc, "claude:s")
        self.assertEqual([self.first_leaf, self.second_leaf], [row["item_id"] for row in rows])


class QueuedGoalApprovalTests(unittest.TestCase):
    """A queued goal can be approved without taking the active slot.

    Andy 2026-11-23: work_execute approved only the active goal and
    work_select refused to switch while the active goal still had a runnable
    step, so every queued goal stayed in planning and the ordinary lane never
    dispatched it (n2584, n2578, n1936, n2442, n2624: approval_generation 0).
    """

    def setUp(self):
        import tempfile
        from ciel_runtime_support.work_plan_store import WorkPlanStore
        temp = tempfile.TemporaryDirectory(prefix="work-queue-approval-")
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = WorkPlanStore(self.directory / "work-plan.json")

    def call(self, name, **args):
        from ciel_runtime_support import work_mcp_tools as m
        def respond(request_id, text, is_error=False):
            return {"text": text, "error": is_error}
        args.setdefault("session_id", "claude:test-session")
        return m.dispatch_work_tool(7, name, args, load_config=lambda: {}, state_dir=self.directory,
                                   respond=respond, store=self.store)

    def test_work_execute_approves_an_exact_queued_goal(self):
        for goal in ("active goal", "queued goal"):
            self.assertFalse(self.call("work_plan_set", goal=goal, children=[
                {"title": "phase", "children": [{"title": goal + " step"}]}])["error"])
        self.assertFalse(self.call("work_execute")["error"])
        doc = self.store.read()
        active = doc["active_root"]
        queued = next(n["id"] for n in doc["nodes"] if n["parent_id"] is None and n["id"] != active)
        self.assertEqual("planning", tree.root_phase(doc, queued))

        result = self.call("work_execute", root_id=queued)

        self.assertFalse(result["error"], result)
        doc = self.store.read()
        self.assertEqual("executing", tree.root_phase(doc, queued))
        self.assertEqual(active, doc["active_root"])
        self.assertEqual("executing", tree.root_phase(doc, active))
        self.assertIn(queued, [row["root_id"] for row in tree.runnable_leaves(doc)])

    def test_unknown_or_detail_root_is_refused_without_change(self):
        self.assertFalse(self.call("work_plan_set", goal="goal", children=[
            {"title": "phase", "children": [{"title": "step"}]}])["error"])
        before = self.store.read()
        for root in ("n99", "n2"):
            self.assertTrue(self.call("work_execute", root_id=root)["error"], root)
        self.assertEqual(before["version"], self.store.read()["version"])


if __name__ == "__main__":
    unittest.main()
