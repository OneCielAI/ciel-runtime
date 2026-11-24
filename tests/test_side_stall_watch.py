"""The side tells main once about a fork that is stuck (user 2026-11-24).

Andy: a fork parked on an external deploy condition and main both waited for
hours with no answer and no special effort, and only the person's complaint on
the thread showed it. The blocker check now lists a waiting fork whose recorded
wait has not moved for hours (or one parked on a question that long); the side
re-verifies the condition first. If it still holds, it files a stall notice
(stall_notices) and the router records that exact wait as told, so main hears of
it once per wait.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_gate_check as gate
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_work_decision import apply, validate
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store

SCOPE = "claude:parent"
HOST = {"token": "x" * 40}
HOUR = 3600.0
NOW = 400_000.0


def receipt(name):
    return hashlib.sha256(name.encode()).hexdigest()


def lease(lease_id, item_id, *, state="waiting_input", created_at=NOW - 20 * HOUR,
          evidence="question?", slot=False, scope=SCOPE):
    return {"id": lease_id, "scope": scope, "item_id": item_id, "state": state, "slot": slot,
            "host_id": "agent-" + lease_id, "role": "worker", "priority": "P1", "evidence": evidence,
            "created_at": created_at}


class StalledForkUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkPlanStore(Path(self.tmp.name) / "board.json")
        s = self.store
        _, self.goal = s.transaction(lambda d: tree.add_node(d, None, "deploy", priority="P1"))
        _, self.stuck = s.transaction(
            lambda d: tree.add_node(d, self.goal, "deploy the build",
                                    waiting_on="production release 0.4.509 deployed", now=NOW - 48 * HOUR))
        _, self.fresh = s.transaction(
            lambda d: tree.add_node(d, self.goal, "recent wait",
                                    waiting_on="a CI result", now=NOW - 2 * HOUR))
        _, self.question = s.transaction(lambda d: tree.add_node(d, self.goal, "question item", now=NOW - 30 * HOUR))
        _, self.done = s.transaction(lambda d: tree.add_node(d, self.goal, "done item", now=NOW - 30 * HOUR))
        s.transaction(lambda d: tree.update_node(d, self.done, status="done", evidence="ok"))

    def leases(self):
        return {
            "L1": lease("L1", self.stuck, created_at=NOW - 40 * HOUR, evidence="waiting for the production deploy"),
            "L2": lease("L2", self.fresh, created_at=NOW - 2 * HOUR),
            "L3": lease("L3", self.question, created_at=NOW - 26 * HOUR, evidence="which region?"),
            "L4": lease("L4", self.done),
            "L5": lease("L5", self.stuck, state="running", slot=True),
            "L6": lease("L6", self.stuck, scope="claude:other"),
        }

    def test_stalled_forks_lists_only_long_waiting_forks(self):
        rows = gate.stalled_forks(self.leases(), SCOPE, self.store.read(), now=NOW)
        # L2 is recent, L4's item is done, L5 runs, L6 is another conversation.
        self.assertEqual(["L1", "L3"], [row["lease_id"] for row in rows])
        stuck = rows[0]
        self.assertEqual((self.stuck, self.goal, "wait"), (stuck["item_id"], stuck["root_id"], stuck["kind"]))
        self.assertEqual("production release 0.4.509 deployed", stuck["waiting_on"])
        self.assertEqual(NOW - 48 * HOUR, stuck["since"])
        self.assertEqual(48.0, stuck["hours"])
        self.assertEqual("question", rows[1]["kind"])
        self.assertEqual("which region?", rows[1]["question"])

    def test_a_recorded_told_line_is_not_listed_until_the_wait_changes(self):
        self.store.transaction(lambda d: tree.update_node(
            d, self.stuck, note='progress\nSide stall notice (event ' + receipt("e9")[:12] + '): still unmet'))
        rows = gate.stalled_forks(self.leases(), SCOPE, self.store.read(), now=NOW)
        self.assertEqual(["L3"], [row["lease_id"] for row in rows])
        self.store.transaction(lambda d: tree.update_node(
            d, self.stuck, waiting_on="production release 0.4.510 deployed", now=NOW - 20 * HOUR))
        rows = gate.stalled_forks(self.leases(), SCOPE, self.store.read(), now=NOW)
        self.assertIn("L1", [row["lease_id"] for row in rows])

    def test_a_recently_changed_wait_is_not_stalled_even_on_an_old_item(self):
        self.store.transaction(lambda d: tree.update_node(
            d, self.stuck, waiting_on="production release 0.4.510 deployed", now=NOW - 1 * HOUR))
        rows = gate.stalled_forks(self.leases(), SCOPE, self.store.read(), now=NOW)
        self.assertEqual(["L3"], [row["lease_id"] for row in rows])

    def test_a_board_with_only_stalled_forks_is_checked_and_the_event_names_them(self):
        stalled = gate.stalled_forks(self.leases(), SCOPE, self.store.read(), now=NOW)
        tick = gate.tick_rows({}, self.store.read(), None, now=NOW, stalled=stalled)
        self.assertIsNotNone(tick)
        event = gate.tick_event({}, [], tick[1], NOW, stalled=stalled)
        self.assertIn("멈춘 포크", event["text"])
        self.assertIn("lease L1", event["text"])
        self.assertIn("stall_notices", event["text"])
        self.assertEqual(["L1", "L3"], event["stalled"])


class StallNoticeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.store = session_work_plan_store(self.state, SCOPE)
        s = self.store
        _, self.goal = s.transaction(lambda d: tree.add_node(d, None, "deploy", priority="P1"))
        _, self.stuck = s.transaction(
            lambda d: tree.add_node(d, self.goal, "deploy the build",
                                    waiting_on="production release 0.4.509 deployed"))

    def decision(self, **extra):
        return {"action": "update", "kind": "request", "item_id": self.stuck,
                "summary": "n1 유지: production release is still 0.4.508", "reply": "기록",
                "note": "checked: production release is still 0.4.508",
                "refs": ["162759"], **extra}

    def notice(self, item_id=None, **over):
        row = {"item_id": item_id or self.stuck, "lease_id": "L1", "kind": "wait",
               "waiting_on": "production release 0.4.509 deployed",
               "checked": "production release.json says 0.4.508 (#162759)",
               "needed": "배포 결정 또는 무인 배포 먼저 여부"}
        row.update(over)
        return row

    def apply(self, decision, key="stall-one", parked=None):
        return apply(self.store, receipt(key), decision, self.store.read()["version"],
                     gate_check=True, parked_projection=parked)

    def node(self, node_id):
        return next(n for n in self.store.read()["nodes"] if n["id"] == node_id)

    def test_a_verified_stall_is_recorded_and_reported_once(self):
        parked = {"rows": [{"item_id": self.stuck, "root_id": self.goal, "lease_id": "L1",
                            "state": "waiting_input", "slot": False}]}
        result = self.apply(self.decision(stall_notices=[self.notice()]), parked=parked)
        self.assertEqual([{"item_id": self.stuck, "lease_id": "L1", "kind": "wait",
                           "waiting_on": "production release 0.4.509 deployed",
                           "checked": "production release.json says 0.4.508 (#162759)",
                           "needed": "배포 결정 또는 무인 배포 먼저 여부"}], result["stall_notices"])
        self.assertIn("Side stall notice (event " + receipt("stall-one")[:12] + ")", self.node(self.stuck)["note"])
        self.assertNotIn("unnoticed_stalls", result)
        # The same wait is not told twice, whatever the side files next.
        again = self.apply(self.decision(stall_notices=[self.notice()]), key="stall-two", parked=parked)
        self.assertNotIn("stall_notices", again)
        self.assertEqual([{"item_id": self.stuck, "reason": "already_notified"}], again["unnoticed_stalls"])

    def test_a_stall_notice_is_refused_without_a_live_parked_lease(self):
        result = self.apply(self.decision(stall_notices=[self.notice()]))
        self.assertNotIn("stall_notices", result)
        self.assertEqual([{"item_id": self.stuck, "reason": "the Work ledger was unreadable"}],
                         result["unnoticed_stalls"])
        other = {"rows": [{"item_id": self.goal, "root_id": self.goal, "lease_id": "L1",
                           "state": "waiting_input", "slot": False}]}
        result = self.apply(self.decision(stall_notices=[self.notice()]), key="stall-three", parked=other)
        self.assertEqual([{"item_id": self.stuck, "reason": "not a parked worker of this item"}],
                         result["unnoticed_stalls"])

    def test_stall_notices_are_validated(self):
        with self.assertRaisesRegex(tree.WorkPlanError, "invalid side decision stall_notices"):
            validate(self.decision(stall_notices=[{**self.notice(), "kind": "wall"}]))
        with self.assertRaisesRegex(tree.WorkPlanError, "invalid side decision stall_notices"):
            validate(self.decision(stall_notices=[self.notice(checked="")]))


class StallWatchMcpTests(unittest.TestCase):
    """The router's gate check lists the stalled fork, and its resolve records the notice."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = {"work_mode": True, "work_side_intake": {"enabled": True},
                       "work_parallel_host": {"token": "x" * 40}}
        self.store = session_work_plan_store(self.root, SCOPE)
        _, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, "deploy", priority="P1"))
        old = __import__("time").time() - 30 * HOUR
        _, self.leaf = self.store.transaction(
            lambda d: tree.add_node(d, self.goal, "deploy the build",
                                    waiting_on="production release 0.4.509 deployed", now=old))
        from unittest.mock import Mock
        self.ledger = Mock()
        self.ledger.read.return_value = {"version": 3, "leases": {
            "L1": lease("L1", self.leaf, created_at=old, evidence="waiting for the production deploy")}}
        self.reviews = Mock()
        self.reviews.read.return_value = {"reviews": {}}

    def invoke(self, name, args, host=None):
        return dispatch_work_tool(1, name, {**args, "session_id": SCOPE, "_host": host or HOST},
                                  load_config=lambda: self.config, state_dir=self.root,
                                  respond=lambda _id, text, error=False: {"text": text, "error": error})

    def value(self, name, args, host=None):
        result = self.invoke(name, args, host)
        self.assertFalse(result["error"], result)
        value = json.loads(result["text"])
        if isinstance(value, dict) and "paged" in value:
            pages = [self.value("work_host_page", {"id": value["paged"]["id"], "part": part})["text"]
                     for part in range(value["paged"]["parts"])]
            value = json.loads("".join(pages))
        return value

    def test_a_stalled_fork_is_checked_and_main_is_told_once(self):
        from unittest.mock import patch
        with patch("ciel_runtime_support.work_parallel_mcp.ledger", return_value=self.ledger), \
                patch("ciel_runtime_support.work_parallel_mcp.finalization", return_value=self.reviews):
            queued = json.loads(self.invoke("work_side_gate_check", {"idle": True, "forks_running": 0})["text"])
            self.assertTrue(queued.get("queued"), queued)
            self.assertEqual(["L1"], queued["stalled"])
            claimed = self.value("work_side_claim", {"owner": "actor"})["claim"]
            self.assertIn("오래 멈춘 포크", claimed["text"])
            self.assertIn("stall_notices", claimed["decision_fields"])
            host = {**HOST, "receipt_id": claimed["receipt_id"], "generation": claimed["generation"],
                    "work_digest": claimed["work_digest"]}
            resolved = self.invoke("work_side_resolve", {"decision": {
                "action": "answer", "kind": "status", "summary": self.leaf + " 유지: release is still 0.4.508",
                "reply": "stalled",
                "stall_notices": [{"item_id": self.leaf, "lease_id": "L1", "kind": "wait",
                                   "waiting_on": "production release 0.4.509 deployed",
                                   "checked": "release.json says 0.4.508",
                                   "needed": "deploy decision"}]},
                "expected_version": claimed["workload"]["version"]}, host)
        self.assertFalse(resolved["error"], resolved)
        result = json.loads(resolved["text"])["result"]
        self.assertEqual(["L1"], [row["lease_id"] for row in result["stall_notices"]])
        node = next(n for n in self.store.read()["nodes"] if n["id"] == self.leaf)
        self.assertIn("Side stall notice (event ", node["note"])
        # Told: the next check no longer lists it.
        rows = gate.stalled_forks(self.ledger.read()["leases"], SCOPE, self.store.read())
        self.assertEqual([], rows)


if __name__ == "__main__":
    unittest.main()
