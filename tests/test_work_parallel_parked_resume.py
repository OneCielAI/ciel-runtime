"""A parked worker whose wait is cleared is flagged for automatic resume (Stage 3).

User requirement 2026-11-17: a worker that asked a question and gave its slot
back resumes on the same lease, fork and worktree once its wait is cleared.
"Cleared" means the item and its ancestors have no waiting_on and are not
blocked. The router computes the verdict on the host view (the mod's board
snapshot has no nodes); the model's own status never carries it. A worker
question parks with no waiting_on recorded, so the pump's wait_cleared also
needs a wait the host saw recorded on the chain while the lease was parked.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelStore

SCOPE = "claude:parked-resume"


def board():
    doc = tree.empty_document()
    goal = tree.add_node(doc, None, "goal", priority="P2", now=1)
    branch = tree.add_node(doc, goal, "branch", now=2)
    ids = {"goal": goal, "branch": branch}
    for index, name in enumerate(("cleared", "waits", "blocked", "done", "deep")):
        ids[name] = tree.add_node(doc, goal if name != "deep" else branch, name, now=3 + index)
    tree.update_node(doc, ids["waits"], waiting_on="Daniel answer")
    tree.update_node(doc, ids["blocked"], status="blocked")
    tree.update_node(doc, ids["done"], status="done", evidence="verified")
    tree.set_phase(doc, tree.PHASE_EXECUTING, goal)
    return doc, ids


class ParkedWaitClearedTests(unittest.TestCase):
    def test_item_and_ancestors_must_be_free_of_waits_and_blocks(self):
        doc, ids = board()
        self.assertTrue(m.parked_wait_cleared(doc, ids["cleared"]))
        self.assertTrue(m.parked_wait_cleared(doc, ids["deep"]))
        for name in ("waits", "blocked", "done"):
            self.assertFalse(m.parked_wait_cleared(doc, ids[name]), name)
        self.assertFalse(m.parked_wait_cleared(doc, "missing"))
        tree.update_node(doc, ids["branch"], waiting_on="vendor reply")
        self.assertFalse(m.parked_wait_cleared(doc, ids["deep"]))

    def test_a_goal_back_in_planning_is_not_cleared(self):
        doc, ids = board()
        tree.set_phase(doc, tree.PHASE_PLANNING, ids["goal"])
        self.assertFalse(m.parked_wait_cleared(doc, ids["cleared"]))


class HostViewTests(unittest.TestCase):
    def test_host_view_flags_parked_leases_and_the_model_view_does_not(self):
        doc, ids = board()
        with TemporaryDirectory() as td:
            store = WorkParallelStore(Path(td) / "work-parallel.json")
            store.configure(dict(paused=False), capability_ready=True, expected_version=0)
            template_id = store.reserve([dict(item_id="template", priority="P2", created_at=10, approved=True,
                                              focused=True, dependencies_done=True, plan_version=1, usage=.05,
                                              role="worker", mode="fork")],
                                        scope=SCOPE, expected_version=store.read()["version"], now=100)[1]
            document = store.read()
            template = document["leases"].pop(template_id)
            for ident, item, slot in (("La", ids["cleared"], False), ("Lb", ids["waits"], False),
                                      ("Lc", ids["deep"], True)):
                document["leases"][ident] = dict(template, id=ident, item_id=item, state="waiting_input", slot=slot,
                                                 usage=0, host_id="agent-" + ident, idle_proof="asked")
            store.path.write_text(json.dumps(document), encoding="utf-8")
            with patch.object(m, "ledger", return_value=store):
                host = m.project(store, SCOPE, doc, host_view=True)
                model = m.project(store, SCOPE, doc)
                # Lb's recorded wait is released: seen while parked, now gone.
                tree.update_node(doc, ids["waits"], waiting_on="")
                self.after_release = m.project(store, SCOPE, doc, host_view=True)
                # A wait recorded on La's goal after it parked, then cleared.
                tree.update_node(doc, ids["goal"], waiting_on="vendor reply")
                m.project(store, SCOPE, doc, host_view=True)
                tree.update_node(doc, ids["goal"], waiting_on="")
                self.after_late_wait = m.project(store, SCOPE, doc, host_view=True)
        def flags(view, ident):
            return tuple(view["leases"][ident][k] for k in ("wait_clear_now", "wait_seen", "wait_cleared"))
        # La asked a question: nothing recorded, so clear now but never resumed by the pump.
        self.assertEqual((True, False, False), flags(host, "La"))
        self.assertEqual((False, True, False), flags(host, "Lb"))
        # Still holding its slot: not parked, nothing to resume.
        self.assertNotIn("wait_cleared", host["leases"]["Lc"])
        self.assertTrue(all("wait_cleared" not in row and "wait_seen" not in row for row in model["leases"].values()))
        self.assertEqual((True, True, True), flags(self.after_release, "Lb"))
        self.assertEqual((True, True, True), flags(self.after_late_wait, "La"))


class ParkedNoticeClaimTests(unittest.TestCase):
    def test_main_is_told_once_per_marked_lease_and_a_failed_delivery_can_retry(self):
        with TemporaryDirectory() as td:
            store = WorkParallelStore(Path(td) / "work-parallel.json")
            self.assertFalse(m._parked_notice_claim(store, "L1"))  # no recorded wait, nothing to tell
            Path(td, m.PARKED_WAITS_NAME).write_text(json.dumps({"leases": {"L1": {"node": "n1"}}}), encoding="utf-8")
            self.assertTrue(m._parked_notice_claim(store, "L1"))
            self.assertFalse(m._parked_notice_claim(store, "L1"))
            self.assertTrue(m._parked_notice_claim(store, "L1", release=True))
            self.assertTrue(m._parked_notice_claim(store, "L1"))


if __name__ == "__main__":
    unittest.main()
