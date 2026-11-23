"""Side-released waits: exact leaf release, board receipt, sidecar row and
the scheduling lane over the real reserve/reacquire paths."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.side_work_decision import apply, wait_digest
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_wait_boost import boost_key
from ciel_runtime_support.work_plan_store import WorkPlanStore
from ciel_runtime_support.work_wait_release import record_released, sidecar_for

SECRET = "s" * 40
SCOPE = "claude:lane"
WAIT = "approval from Andy via Walkie"


def receipt(name):
    return hashlib.sha256(name.encode()).hexdigest()


class ExactReleaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = WorkPlanStore(self.root / "board.json")
        self.ledger = self.root / "work-parallel.json"
        _, self.goal = self.store.transaction(lambda d: tree.add_node(d, None, "goal", priority="P2"))
        _, self.group = self.store.transaction(lambda d: tree.add_node(d, self.goal, "group"))
        _, self.leaf = self.store.transaction(lambda d: tree.add_node(d, self.group, "deploy", waiting_on=WAIT))
        self.base = {"action": "update", "kind": "request", "summary": "Andy approved", "reply": "ok",
                     "note": "approved"}

    def block(self, node_id):
        def mutate(doc):
            for node in doc["nodes"]:
                if node["id"] == node_id:
                    node["status"] = "blocked"
        self.store.transaction(mutate)

    def node(self, node_id):
        return next(n for n in self.store.read()["nodes"] if n["id"] == node_id)

    def release(self, key, node_id, waiting_on=WAIT, parked=None):
        doc = self.store.read()
        return apply(self.store, key, {**self.base, "item_id": node_id,
                                       "release_waits": [{"id": node_id, "waiting_on": waiting_on}]},
                     doc["version"], parked_projection=parked)

    def test_blocked_exact_leaf_becomes_pending_and_only_that_leaf_changes(self):
        self.block(self.leaf)
        self.block(self.group)
        before = {n["id"]: dict(n) for n in self.store.read()["nodes"]}
        result = self.release(receipt("e1"), self.leaf)
        after = {n["id"]: n for n in self.store.read()["nodes"]}
        self.assertEqual("pending", after[self.leaf]["status"])
        self.assertEqual("", after[self.leaf]["waiting_on"])
        self.assertEqual([self.leaf], result["unblocked_waits"])
        # The blocked ancestor is untouched and still gates selection.
        self.assertEqual(before[self.group], after[self.group])
        self.assertEqual(before[self.goal], after[self.goal])
        self.assertEqual("blocked", after[self.group]["status"])
        self.assertEqual([], [row for row in tree.runnable_leaves(self.store.read()) if row["item_id"] == self.leaf])
        boost, = result["wait_boost"]
        self.assertEqual({"item_id": self.leaf, "root_id": self.goal,
                          "root_created_at": before[self.goal]["created_at"],
                          "release_receipt": receipt("e1"), "wait_digest": wait_digest(WAIT),
                          "parked_lease_id": None}, {k: v for k, v in boost.items() if k != "release_at"})
        self.assertNotIn(WAIT, json.dumps(boost))

    def test_waiting_only_leaf_keeps_its_status(self):
        result = self.release(receipt("e2"), self.leaf)
        self.assertEqual("pending", self.node(self.leaf)["status"])
        self.assertNotIn("unblocked_waits", result)
        self.assertEqual(1, len(result["wait_boost"]))

    def test_blocked_non_leaf_release_is_refused_without_reopening_descendants(self):
        def gate(doc):
            for node in doc["nodes"]:
                if node["id"] == self.group:
                    node.update(status="blocked", waiting_on="group approval")
        self.store.transaction(gate)
        before = {n["id"]: dict(n) for n in self.store.read()["nodes"]}
        result = self.release(receipt("e3"), self.group, waiting_on="group approval")
        self.assertEqual([{"id": self.group, "reason": "non_leaf_wait"}], result["unreleased_waits"])
        self.assertNotIn("wait_boost", result)
        after = {n["id"]: n for n in self.store.read()["nodes"]}
        # The update note is the decision's own; the wait, status and descendants stay.
        self.assertEqual(("blocked", "group approval"), (after[self.group]["status"], after[self.group]["waiting_on"]))
        self.assertEqual(before[self.leaf], after[self.leaf])

    def test_stale_text_creates_no_receipt_and_no_sidecar_row(self):
        result = self.release(receipt("e4"), self.leaf, waiting_on="different text")
        self.assertNotIn("wait_boost", result)
        self.assertEqual(WAIT, self.node(self.leaf)["waiting_on"])
        self.assertEqual({"created": [], "kept": [], "consuming": [], "invalid": 0},
                         record_released(self.ledger, SCOPE, receipt("e4"), result))
        self.assertFalse(sidecar_for(self.ledger).path.exists())

    def test_receipt_creates_one_boosted_row_and_replay_is_idempotent(self):
        result = self.release(receipt("e5"), self.leaf,
                              parked={"rows": [{"item_id": self.leaf, "root_id": self.goal, "lease_id": "L1",
                                                "state": "waiting_input", "slot": False}]})
        first = record_released(self.ledger, SCOPE, receipt("e5"), result)
        self.assertEqual([self.leaf], first["created"])
        row = sidecar_for(self.ledger).read()["rows"][boost_key(SCOPE, self.leaf)]
        self.assertEqual(("boosted", "L1"), (row["state"], row["parked_lease_id"]))
        replay = self.release(receipt("e5"), self.leaf)
        self.assertTrue(replay["duplicate"])
        again = record_released(self.ledger, SCOPE, receipt("e5"), replay)
        self.assertEqual(([], True), (again["created"], again["replay"]))
        self.assertEqual(row, sidecar_for(self.ledger).read()["rows"][boost_key(SCOPE, self.leaf)])

    def test_invalid_receipt_shape_creates_nothing(self):
        result = self.release(receipt("e6"), self.leaf)
        broken = {**result, "wait_boost": [{**result["wait_boost"][0], "extra": 1}]}
        self.assertEqual(1, record_released(self.ledger, SCOPE, receipt("e6"), broken)["invalid"])
        self.assertFalse(sidecar_for(self.ledger).path.exists())


def board_with(*leaves):
    nodes = [{"id": "g1", "parent_id": None, "title": "Goal", "phase": "executing", "status": "pending",
              "updated_at": 1, "created_at": 1.0, "priority": "P1", "note": "approved goal"}]
    for index, leaf in enumerate(leaves):
        nodes.append({"id": leaf, "parent_id": "g1", "title": "leaf " + leaf, "status": "pending",
                      "updated_at": 10 + index, "created_at": 10.0 + index})
    return {"version": 7, "phase": "executing", "active_root": "g1", "focus_ids": [], "nodes": nodes}


class LaneTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = WorkParallelStore(self.root / "work-parallel.json")
        self.board = board_with("a", "b", "c")
        board = self.board

        class Board:
            def read(self):
                return board

        self.board_store = Board()
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}

    def run_event(self, host=None, **args):
        args.setdefault("expected_version", self.ledger.read()["version"])
        if host is not None:
            args["_host"] = {"token": SECRET, **host}
        with patch.object(m, "ledger", return_value=self.ledger):
            return json.loads(m.run("work_parallel_event", args, self.board_store, SCOPE, self.config, None))

    def configure(self, limit):
        args = {"changes": {"enabled": True, "launches_per_minute": 30,
                            "priority_limits": {"P1": limit, "P2": 2, "P3": 2, "P4": 1, "P5": 1}},
                "expected_version": self.ledger.read()["version"], "_host": {"token": SECRET}}
        with patch.object(m, "ledger", return_value=self.ledger):
            m.run("work_parallel_configure", args, self.board_store, SCOPE, self.config, None)

    def boost(self, item_id, name, release_at, parked=None):
        result = {"wait_boost": [{"item_id": item_id, "root_id": "g1", "root_created_at": 1.0,
                                  "release_receipt": receipt(name), "release_at": release_at,
                                  "wait_digest": wait_digest(name), "parked_lease_id": parked}],
                  "queue": {"version": 8}}
        self.assertEqual([item_id], record_released(self.ledger.path, SCOPE, receipt(name), result)["created"])

    def row(self, item_id):
        return sidecar_for(self.ledger.path).read()["rows"].get(boost_key(SCOPE, item_id))

    def reserve_next(self):
        return self.run_event({"fork": True}, event="reserve_next")

    def test_boosts_go_first_newest_release_first_then_ordinary_queue_order(self):
        self.configure(3)
        self.boost("a", "older", 100.0)
        self.boost("b", "newer", 200.0)
        first = self.reserve_next()
        self.assertEqual("b", first["item_id"])            # newest release first
        self.assertIsNone(self.row("b"))                   # consumed by the reservation
        second = self.reserve_next()
        self.assertEqual("a", second["item_id"])           # the other boost before ordinary c
        third = self.reserve_next()
        self.assertEqual("c", third["item_id"])
        # Consumed exactly once: nothing is left to boost again.
        self.assertIsNone(self.row("a"))
        self.assertEqual("P1", next(n for n in self.board["nodes"] if n["id"] == "g1")["priority"])

    def test_capacity_refusal_keeps_the_boost(self):
        self.configure(1)
        # Queue order (user 2026-11-23): the first queued leaf takes the only P1 slot.
        self.assertEqual("a", self.reserve_next()["item_id"])
        self.boost("b", "later", 300.0)
        with self.assertRaises(WorkParallelError):
            self.reserve_next()
        self.assertEqual("boosted", self.row("b")["state"])

    def park(self, item_id):
        reserved = self.run_event({"fork": True}, event="reserve", item_id=item_id, priority="P1")
        ident = reserved["lease_id"]
        self.run_event({"host_id": "agent-" + item_id}, event="bind", lease_id=ident)
        self.run_event({"agent_id": "agent-" + item_id}, event="question", lease_id=ident,
                       plan_version=7, evidence="need approval")
        self.run_event({"proof": "native host status: waiting"}, event="wait", lease_id=ident)
        return ident

    def test_parked_worker_keeps_its_slot_free_and_reacquire_consumes(self):
        self.configure(1)
        ident = self.park("a")
        self.boost("a", "approval", 400.0, parked=ident)
        status = m.project(self.ledger, SCOPE)["wait_boost"]
        self.assertEqual(("parked_first_eligible", "a", False),
                         (status["behavior"], status["item_id"], status["ordinary_allowed"]))
        with self.assertRaisesRegex(WorkParallelError, "kept for a released parked worker"):
            self.reserve_next()
        self.assertEqual(1, len(self.ledger.read()["leases"]))
        reacquired = self.run_event({}, event="reacquire", lease_id=ident)
        self.assertEqual("reacquired", reacquired["wait_boost"])
        row = self.row("a")
        self.assertEqual(("consumed", "reacquired", ident),
                         (row["state"], row["outcome"], row["completed_lease_id"]))
        self.assertEqual("none", m.project(self.ledger, SCOPE)["wait_boost"]["reason"])

    def test_reblock_removes_the_row_before_selection_and_a_new_release_replaces_it(self):
        self.configure(3)
        self.boost("a", "first", 100.0)
        next(n for n in self.board["nodes"] if n["id"] == "a")["waiting_on"] = "a new question"
        reserved = self.reserve_next()
        self.assertNotEqual("a", reserved["item_id"])
        self.assertIsNone(self.row("a"))
        next(n for n in self.board["nodes"] if n["id"] == "a")["waiting_on"] = ""
        self.boost("a", "second", 500.0)
        self.assertEqual(receipt("second"), self.row("a")["release_receipt"])

    def test_done_item_row_is_removed(self):
        self.configure(3)
        self.boost("a", "gone", 100.0)
        node = next(n for n in self.board["nodes"] if n["id"] == "a")
        node.update(status="done", evidence="verified")
        self.reserve_next()
        self.assertIsNone(self.row("a"))

    def test_unreadable_sidecar_never_blocks_ordinary_scheduling(self):
        self.configure(3)
        sidecar_for(self.ledger.path).path.write_text("{not json", encoding="utf-8")
        self.assertEqual("a", self.reserve_next()["item_id"])          # ordinary queue order
        self.assertEqual("unavailable", m.project(self.ledger, SCOPE)["wait_boost"]["reason"])


if __name__ == "__main__":
    unittest.main()
