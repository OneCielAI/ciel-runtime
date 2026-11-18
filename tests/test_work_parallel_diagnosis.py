"""Per-priority dispatch diagnosis (Stage 3, user requirement 2026-11-17).

Each priority fills to its own limit (P1 3, P2 2, P3 1, P4 1, P5 1 -> 8 at
once) and every surface says, per priority, the limit, the slots in use, the
runnable candidates and why the rest cannot run.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_diagnosis import diagnose

SCOPE = "claude:diagnosis"
LIMITS = dict(P1=3, P2=2, P3=1, P4=1, P5=1)


def board_with_priorities():
    """P1 executing: 2 runnable, 1 waiting, 2 with open leases; P2 planning; P3 executing, 1 runnable."""
    doc = tree.empty_document()
    p1 = tree.add_node(doc, None, "P1 goal", priority="P1", now=1)
    leaves = {name: tree.add_node(doc, p1, name, now=2 + index)
              for index, name in enumerate(("a", "b", "waits", "running", "asked"))}
    tree.update_node(doc, leaves["waits"], waiting_on="Daniel approval")
    tree.set_phase(doc, tree.PHASE_EXECUTING, p1)
    p2 = tree.add_node(doc, None, "P2 goal", priority="P2", now=20)
    tree.add_node(doc, p2, "p2 leaf", now=21)
    p3 = tree.add_node(doc, None, "P3 goal", priority="P3", now=30)
    leaves["p3"] = tree.add_node(doc, p3, "p3 leaf", now=31)
    tree.set_phase(doc, tree.PHASE_EXECUTING, p3)
    return doc, leaves


def lease(ident, item, priority, state, slot):
    return {"id": ident, "scope": SCOPE, "item_id": item, "priority": priority, "role": "worker",
            "state": state, "slot": slot}


def ledger(leases, **policy):
    base = {"enabled": True, "paused": False, "priority_limits": dict(LIMITS), "launches_per_minute": 4}
    base.update(policy)
    return {"policy": base, "leases": {row["id"]: row for row in leases}, "launches": []}


class DiagnoseTests(unittest.TestCase):
    def rows(self, result):
        return {row["priority"]: row for row in result["rows"]}

    def test_each_priority_reports_limit_use_candidates_and_blockers(self):
        doc, leaves = board_with_priorities()
        result = diagnose(ledger([lease("l1", leaves["running"], "P1", "running", True),
                                  lease("l2", leaves["asked"], "P1", "waiting_input", False)]),
                          doc, SCOPE, now=1000, max_leases=100, bounded_open=1)
        rows = self.rows(result)
        p1 = rows["P1"]
        self.assertEqual((3, 1, 2, 2), (p1["limit"], p1["active"], p1["free"], p1["candidates"]))
        self.assertEqual({"waiting": 1, "planning": 0, "not_ready": 0, "fenced": 2, "stranded": 0}, p1["blocked"])
        self.assertEqual({"running": 1, "waiting_input": 1}, p1["fenced_states"])
        self.assertEqual("can_fill", p1["state"])
        self.assertEqual(("no_candidates", 1), (rows["P2"]["state"], rows["P2"]["blocked"]["planning"]))
        self.assertIn("goal in planning 1", rows["P2"]["reason"])
        self.assertEqual(("can_fill", 1), (rows["P3"]["state"], rows["P3"]["candidates"]))
        self.assertEqual("no_work", rows["P4"]["state"])
        self.assertEqual(["P2"], result["attention"])
        self.assertIn("P2 0/2 no candidates (free slot but no runnable item (goal in planning 1))", result["summary"])

    def test_full_disabled_rate_bound_and_parked_hold(self):
        doc, leaves = board_with_priorities()
        busy = [lease(f"r{i}", f"other-{i}", "P1", "running", True) for i in range(3)]
        rows = self.rows(diagnose(ledger(busy), doc, SCOPE, now=0, max_leases=100, bounded_open=3))
        self.assertEqual(("full", 0), (rows["P1"]["state"], rows["P1"]["free"]))
        rows = self.rows(diagnose(ledger([], paused=True), doc, SCOPE, now=0, max_leases=100, bounded_open=0))
        self.assertEqual("disabled", rows["P1"]["state"])
        rate = ledger([])
        rate["launches"] = [995, 996, 997, 998]
        rows = self.rows(diagnose(rate, doc, SCOPE, now=1000, max_leases=100, bounded_open=0))
        self.assertEqual("rate_limited", rows["P1"]["state"])
        rows = self.rows(diagnose(ledger([]), doc, SCOPE, now=0, max_leases=100, bounded_open=100))
        self.assertEqual("lease_bound", rows["P3"]["state"])
        rows = self.rows(diagnose(ledger([]), doc, SCOPE, holds={"P3": 1}, now=0, max_leases=100,
                                  bounded_open=0))
        self.assertEqual(("full", 1), (rows["P3"]["state"], rows["P3"]["held_for_parked"]))
        self.assertIn("kept for a released parked worker", rows["P3"]["reason"])

    def test_p0_rows_bypass_policy_and_count_their_own_slots(self):
        doc, leaves = board_with_priorities()
        p1_root = next(n["id"] for n in doc["nodes"] if n["title"] == "P1 goal")
        result = diagnose(ledger([lease("l9", leaves["running"], "P1", "running", True)], paused=True),
                          doc, SCOPE, p0_roots={p1_root}, p0_runnable={leaves["a"], leaves["b"]},
                          p0_active_ids={"l9"}, now=0, max_leases=100, bounded_open=1)
        rows = self.rows(result)
        self.assertEqual((None, 1, 2, "can_fill"), (rows["P0"]["limit"], rows["P0"]["active"],
                                                    rows["P0"]["candidates"], rows["P0"]["state"]))
        self.assertEqual((0, 0), (rows["P1"]["active"], rows["P1"]["unfinished"]))

    def test_a_blocked_p0_goal_draws_attention_and_is_named(self):
        doc, leaves = board_with_priorities()
        goal = {"root_id": "n9", "state": "blocked", "code": "waiting_on", "reason": "every remaining leaf waits or is blocked"}
        result = diagnose(ledger([]), doc, SCOPE, p0_goals=[goal], now=0, max_leases=100, bounded_open=0)
        row = self.rows(result)["P0"]
        self.assertEqual(("goal_blocked", [goal]), (row["state"], row["goals"]))
        self.assertEqual("n9: every remaining leaf waits or is blocked", row["reason"])
        self.assertIn("P0", result["attention"])
        running = diagnose(ledger([]), doc, SCOPE, p0_goals=[dict(goal, state="running", code="")], now=0,
                           max_leases=100, bounded_open=0)
        self.assertNotEqual("goal_blocked", self.rows(running)["P0"]["state"])
        self.assertNotIn("P0", running["attention"])
        taken = diagnose(ledger([lease("l", leaves["a"], "P1", "running", True)]), doc, SCOPE, p0_roots={doc["nodes"][0]["id"]},
                         p0_goals=[dict(goal, state="running", code="")], now=0, max_leases=100, bounded_open=1)
        self.assertNotIn("P0", taken["attention"])

    def test_attention_digest_changes_only_with_actionable_state(self):
        doc, leaves = board_with_priorities()
        one = diagnose(ledger([]), doc, SCOPE, now=0, max_leases=100, bounded_open=0)
        two = diagnose(ledger([lease("x", "other", "P1", "running", True)]), doc, SCOPE, now=0,
                       max_leases=100, bounded_open=1)
        self.assertEqual(one["attention_digest"], two["attention_digest"])
        tree.set_phase(doc, tree.PHASE_EXECUTING, next(n["id"] for n in doc["nodes"] if n["title"] == "P2 goal"))
        three = diagnose(ledger([]), doc, SCOPE, now=0, max_leases=100, bounded_open=0)
        self.assertNotEqual(one["attention_digest"], three["attention_digest"])
        self.assertEqual([], three["attention"])


class StatusProjectionTests(unittest.TestCase):
    def test_status_and_public_projection_carry_the_diagnosis(self):
        with TemporaryDirectory() as td:
            store = WorkParallelStore(Path(td) / "work-parallel.json")
            store.configure(dict(priority_limits=dict(LIMITS)), capability_ready=True, expected_version=0)
            doc, _ = board_with_priorities()
            with patch.object(m, "ledger", return_value=store):
                host = m.project(store, SCOPE, doc, host_view=True)
                model = m.project(store, SCOPE, doc)
            public = m.public_projection(host, SCOPE)
            for view in (host, model, public):
                rows = {row["priority"]: row for row in view["priority_diagnosis"]["rows"]}
                # No lease yet: all four ungated P1 leaves are runnable.
                self.assertEqual((3, 4), (rows["P1"]["limit"], rows["P1"]["candidates"]))
            self.assertNotIn("Daniel", json.dumps(public["priority_diagnosis"]))


if __name__ == "__main__":
    unittest.main()
