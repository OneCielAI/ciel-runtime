"""A full priority whose goal is back in planning must draw attention.

Andy 2026-11-21 (n1445/n2453): a title/scope edit returns the goal phase to
planning, resumes are refused, and nothing surfaces it until main reads state.
With every slot busy the diagnosis row was state=full with reason "all slots
in use", so the host's changed-diagnosis notice never fired.
"""
import unittest

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel_diagnosis import diagnose

SCOPE = "claude:planning-attention"
LIMITS = dict(P1=3, P2=2, P3=1, P4=1, P5=1)


def board():
    doc = tree.empty_document()
    p2 = tree.add_node(doc, None, "P2 goal", priority="P2", now=1)
    tree.add_node(doc, p2, "p2 leaf", now=2)  # goal stays in planning
    p1 = tree.add_node(doc, None, "P1 goal", priority="P1", now=3)
    tree.add_node(doc, p1, "p1 leaf", now=4)
    tree.set_phase(doc, tree.PHASE_EXECUTING, p1)
    return doc, p2


def lease(ident, item, priority):
    return {"id": ident, "scope": SCOPE, "item_id": item, "priority": priority,
            "role": "worker", "state": "running", "slot": True}


def ledger(leases):
    return {"policy": {"enabled": True, "paused": False, "priority_limits": dict(LIMITS),
                       "launches_per_minute": 4},
            "leases": {row["id"]: row for row in leases}, "launches": []}


class PlanningAttentionTests(unittest.TestCase):
    def test_full_with_planning_goal_draws_attention_and_names_it(self):
        doc, _p2 = board()
        busy = [lease("a", "x1", "P2"), lease("b", "x2", "P2")]
        result = diagnose(ledger(busy), doc, SCOPE, now=0, max_leases=100, bounded_open=2)
        row = next(r for r in result["rows"] if r["priority"] == "P2")
        self.assertEqual("full", row["state"])
        self.assertIn("goal in planning 1", row["reason"])
        self.assertIn("restore execution approval", row["reason"])
        self.assertIn("P2", result["attention"])
        self.assertIn("goal in planning 1", result["summary"])

    def test_plain_full_stays_quiet(self):
        doc, p2 = board()
        tree.set_phase(doc, tree.PHASE_EXECUTING, p2)
        busy = [lease("a", "x1", "P2"), lease("b", "x2", "P2")]
        result = diagnose(ledger(busy), doc, SCOPE, now=0, max_leases=100, bounded_open=2)
        row = next(r for r in result["rows"] if r["priority"] == "P2")
        self.assertEqual("full", row["state"])
        self.assertEqual("all slots in use", row["reason"])
        self.assertNotIn("P2", result["attention"])


if __name__ == "__main__":
    unittest.main()
