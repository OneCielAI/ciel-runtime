"""What actually happened to each effective-P0 goal (user requirement 2026-11-18).

Celly: "Side P0 queued <item> · fork starting" appeared for 9 of 10 goals before any
fork existed. The router now knows, per goal, the forks running or why there are none,
and each outcome of one activation is claimed for main once.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_parallel import MAX_LEASES, WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_p0 import p0_store

SECRET = "p" * 40
SCOPE = "claude:p0-outcomes"
FOREIGN = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1", "GIT_CONFIG_NOSYSTEM": "1"}


class Board:
    def __init__(self):
        self.doc = tree.empty_document()

    def read(self):
        return self.doc


class P0OutcomeBase(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.board = Board()
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        self.clock = 1

    def goal(self, title, leaves, *, p0=True, priority="P1", executing=False):
        doc = self.board.doc
        root = tree.add_node(doc, None, title, priority=priority, now=self.tick())
        ids = {name: tree.add_node(doc, root, name, now=self.tick()) for name in leaves}
        if executing:
            tree.set_phase(doc, tree.PHASE_EXECUTING, root)
        if p0:
            side = p0_store(self.store)
            created = next(node for node in doc["nodes"] if node["id"] == root)["created_at"]
            side.activate_root(SCOPE, root, created, set_by="user", expected_version=side.read()["version"], now=5.0)
        return root, ids

    def tick(self):
        self.clock += 1
        return self.clock

    def call(self, event, host=None, **args):
        args.setdefault("expected_version", self.store.read()["version"])
        args["event"] = event
        args["_host"] = {"token": SECRET, **(host or {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, self.board, SCOPE, self.config, None))

    def launch(self, event="p0_reserve_next", name="agent"):
        reserved = self.call(event, {"fork": True, "dispatch_name": "work-auto-" + str(self.tick())}, role="worker")
        if "intent_id" in reserved:
            keys = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
            self.call("p0_launch_assignment", {"internal": True}, **keys)
            self.call("p0_launch_spawning", {"internal": True}, **keys)
            agent_name = reserved["dispatch_name"]
        else:
            agent_name = self.store.read()["leases"][reserved["lease_id"]]["dispatch_name"]
        self.call("bind", {"host_id": f"{name}-{reserved['lease_id'][:6]}", "agent_name": agent_name},
                  lease_id=reserved["lease_id"])
        return reserved

    def host_view(self):
        with patch.object(m, "ledger", return_value=self.store):
            return m.project(self.store, SCOPE, self.board.doc, host_view=True)

    def goals(self):
        return {row["root_id"]: row for row in self.host_view()["p0_goals"]}


class P0LaneTests(P0OutcomeBase):
    def test_one_running_p0_fork_does_not_stop_the_other_p0_leaves(self):
        # Before: the newest P0 leaf stayed first while its fork ran, so the next
        # reserve failed with "coordination persistence failed".
        self.goal("first", ["a", "b"])
        self.goal("second", ["c"])
        items = [self.launch()["item_id"] for _ in range(3)]
        self.assertEqual(3, len(set(items)))
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            self.call("p0_reserve_next", {"fork": True}, role="worker")

    def test_a_running_p0_fork_does_not_stop_ordinary_dispatch(self):
        self.goal("urgent", ["a"])
        _, ordinary = self.goal("ordinary", ["b"], p0=False, priority="P2", executing=True)
        self.launch()
        reserved = self.launch("reserve_next")
        self.assertEqual(ordinary["b"], reserved["item_id"])

    def test_an_unbound_p0_launch_is_still_replayed(self):
        self.goal("urgent", ["a"])
        first = self.call("p0_reserve_next", {"fork": True}, role="worker")
        replay = self.call("p0_reserve_next", {"fork": True}, role="worker")
        self.assertEqual(first["lease_id"], replay["lease_id"])

    def test_a_refused_goal_goes_behind_the_others(self):
        from ciel_runtime_support.work_parallel_p0_outcomes import P0Outcomes
        _, late = self.goal("refused", ["x"])
        healthy, ids = self.goal("healthy", ["y"])
        self.assertEqual(ids["y"], self.call("p0_reserve_next", {"fork": True}, role="worker")["item_id"])
        current = self.store.read()
        lease = next(iter(current["leases"]))
        self.assertTrue(lease)
        found = m._p0_outcome_key(self.store, self.board.doc, SCOPE, ids["y"])
        P0Outcomes(self.store.path).note_refusal(found[0], ids["y"], "workspace_prepare_failed", "boom")
        rows = m._p0_next_rows(self.store, self.board.doc, SCOPE)
        self.assertEqual([late["x"], ids["y"]], [row["item_id"] for row in rows])
        self.assertTrue(healthy)


class P0GoalRowTests(P0OutcomeBase):
    def test_each_goal_says_what_it_has_or_why_it_has_none(self):
        pending, _ = self.goal("pending", ["b"])
        run_root, run = self.goal("runs", ["a"])
        waits, wait = self.goal("waits", ["c"])
        tree.update_node(self.board.doc, wait["c"], waiting_on="vendor answer")
        empty = tree.add_node(self.board.doc, None, "empty", priority="P1", now=self.tick())
        node = next(row for row in self.board.doc["nodes"] if row["id"] == empty)
        side = p0_store(self.store)
        side.activate_root(SCOPE, empty, node["created_at"], set_by="user", expected_version=side.read()["version"], now=6.0)
        # Newest first: the empty goal is itself a leaf, "c" waits, then "a", then "b".
        self.launch()
        self.launch()
        rows = self.goals()
        self.assertEqual(("running", 1), (rows[run_root]["state"], rows[run_root]["forks"]))
        self.assertEqual(run["a"], rows[run_root]["fork_agents"][0]["item_id"])
        self.assertEqual(("pending", ""), (rows[pending]["state"], rows[pending]["code"]))
        self.assertEqual(("blocked", "waiting_on"), (rows[waits]["state"], rows[waits]["code"]))
        self.assertEqual("vendor answer", rows[waits]["waits"][0]["waiting_on"])
        self.assertEqual("running", rows[empty]["state"])
        self.assertEqual("planning", rows[run_root]["phase"])

    def test_open_lease_and_finished_fork_reasons(self):
        root, ids = self.goal("asked", ["a"])
        reserved = self.launch()
        ledger = self.store.read()
        ledger["leases"][reserved["lease_id"]].update(state="waiting_input", slot=False, usage=0, idle_proof="asked")
        self.store.path.write_text(json.dumps(ledger), encoding="utf-8")
        row = self.goals()[root]
        self.assertEqual(("blocked", "open_lease", {"waiting_input": 1}), (row["state"], row["code"], row["fenced_states"]))
        self.assertIn("open leases: waiting_input 1", row["reason"])
        self.assertTrue(ids)

    def test_a_lease_of_another_process_is_an_owner_failure(self):
        root, _ = self.goal("foreign", ["a"])
        reserved = self.launch()
        ledger = self.store.read()
        ledger["leases"][reserved["lease_id"]].update(state="submitted", slot=False, usage=0, artifact="a" * 64, idle_proof="x")
        self.store.path.write_text(json.dumps(ledger), encoding="utf-8")
        with patch.object(m, "_owner_classes", return_value={reserved["lease_id"]: "other_live"}):
            self.assertEqual("owner_failure", self.goals()[root]["code"])

    def test_the_lease_bound_is_named_when_a_leaf_could_run(self):
        root, _ = self.goal("bound", ["a"])
        with patch("ciel_runtime_support.work_parallel.lease_bound_reached", return_value=True):
            self.assertEqual("lease_bound", self.goals()[root]["code"])
        self.assertGreater(MAX_LEASES, 0)

    def test_workspace_ownership_failure_is_reported_with_git_text(self):
        repo = self.tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        for args in (["config", "user.name", "t"], ["config", "user.email", "t@example.invalid"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        (repo / "f.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True)
        root, ids = self.goal("owned elsewhere", ["a"])
        empty = self.tmp / "gitconfig"
        empty.write_text("", encoding="utf-8")
        with patch.dict(os.environ, {**FOREIGN, "GIT_CONFIG_GLOBAL": str(empty), "CIEL_RUNTIME_LAUNCH_CWD": str(self.tmp)}):
            with self.assertRaisesRegex(WorkParallelError, "workspace_git_untrusted"):
                self.call("p0_reserve_next", {"fork": True, "workspace_repo": str(repo)}, role="worker")
        row = self.goals()[root]
        self.assertEqual(("blocked", "workspace_git_untrusted"), (row["state"], row["code"]))
        self.assertEqual(ids["a"], row["refusal"]["item_id"])
        self.assertIn("dubious ownership", row["refusal"]["detail"])
        # The next successful reservation ends the refusal.
        self.launch()
        self.assertEqual("running", self.goals()[root]["state"])
        self.assertIsNone(self.goals()[root]["refusal"])

    def test_a_denied_native_launch_is_the_exact_reason(self):
        root, _ = self.goal("denied", ["a"])
        reserved = self.call("p0_reserve_next", {"fork": True}, role="worker")
        keys = {key: reserved[key] for key in ("intent_id", "lease_id", "dispatch_name")}
        self.call("p0_launch_assignment", {"internal": True}, **keys)
        self.call("p0_launch_spawning", {"internal": True}, **keys)
        self.call("p0_launch_denied", {"internal": True, "proof": "native spawn denied before launch: no"}, **keys)
        row = self.goals()[root]
        self.assertEqual(("blocked", "spawn_denied"), (row["state"], row["code"]))
        self.assertIn("native spawn denied", row["refusal"]["detail"])

    def test_the_diagnosis_lists_goals_without_titles_or_refusal_text(self):
        root, wait = self.goal("Secret goal title", ["a"])
        tree.update_node(self.board.doc, wait["a"], waiting_on="Daniel answer")
        view = self.host_view()
        row = next(item for item in view["priority_diagnosis"]["rows"] if item["priority"] == "P0")
        self.assertEqual("goal_blocked", row["state"])
        self.assertEqual([root], [goal["root_id"] for goal in row["goals"]])
        self.assertIn(root + ":", row["reason"])
        self.assertIn("P0", view["priority_diagnosis"]["attention"])
        public = json.dumps(m.public_projection(view, SCOPE)["priority_diagnosis"])
        self.assertNotIn("Secret goal title", public)
        self.assertNotIn("Daniel", public)
        with patch.object(m, "ledger", return_value=self.store):
            self.assertNotIn("p0_goals", m.project(self.store, SCOPE, self.board.doc))


class P0OutcomeClaimTests(P0OutcomeBase):
    def claim(self, root, outcome, **args):
        return self.call("p0_outcome_claim", {"internal": True}, root_id=root, outcome=outcome, **args)["claimed"]

    def test_each_outcome_is_claimed_once_and_a_failed_delivery_releases_it(self):
        root, _ = self.goal("claimed", ["a"])
        self.assertTrue(self.claim(root, "blocked:waiting_on"))
        self.assertFalse(self.claim(root, "blocked:waiting_on"))
        self.assertTrue(self.claim(root, "started"))
        self.assertIn("started", self.goals()[root]["notified"])
        self.claim(root, "started", release=True)
        self.assertTrue(self.claim(root, "started"))

    def test_the_model_cannot_claim_and_a_plain_goal_has_nothing_to_claim(self):
        root, _ = self.goal("guarded", ["a"])
        with self.assertRaisesRegex(WorkParallelError, "mod-internal"):
            self.call("p0_outcome_claim", None, root_id=root, outcome="started")
        plain, _ = self.goal("plain", ["b"], p0=False)
        self.assertFalse(self.claim(plain, "started"))


if __name__ == "__main__":
    unittest.main()
