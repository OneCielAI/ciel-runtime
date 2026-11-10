"""Native fork evidence: only an observed native fork counts as parallel work.

Temporary ledgers only; no runtime session or router is started.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_native import (FRESH_SECONDS, HEARTBEAT_SECONDS, NativeForkEvidence,
                                                       classify)

SECRET = "native-host-secret-" * 3
SCOPE = "claude:native"
OTHER = "claude:other"


def board():
    nodes = [{"id": f"n{i}", "parent_id": None, "title": f"Item {i}", "ref": f"R-{i}", "status": "pending",
              "updated_at": 1, "priority": "P2", "phase": "executing"} for i in range(1, 6)]
    return {"version": 3, "phase": "executing", "active_root": "n1", "focus_ids": [], "nodes": nodes}


class Env:
    def __init__(self, root: Path):
        self.store = WorkParallelStore(root / "work-parallel.json")
        self.board = board()
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}

    def run(self, name, host=None, scope=SCOPE, **args):
        args.setdefault("expected_version", self.store.read()["version"])
        if host is not None:
            args["_host"] = {"token": SECRET, **host}
        holder = self

        class Board:
            def read(self):
                return holder.board

        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_" + name, args, Board(), scope, self.config, None))

    def launch(self, item, host_id, scope=SCOPE):
        r = self.run("event", {"fork": True}, scope=scope, event="reserve", item_id=item, priority="P2")
        self.run("event", {"host_id": host_id, "agent_name": "work-" + item}, scope=scope,
                 event="bind", lease_id=r["lease_id"])
        return r["lease_id"]

    def observe(self, rows, scope=SCOPE, complete=True):
        return self.run("event", {}, scope=scope, event="native_observation", rows=rows, complete=complete,
                        expected_evidence_version=m.native_evidence(self.store).read()["version"])

    def project(self, scope=SCOPE):
        return m.project(self.store, scope, self.board)


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = Env(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_create_nothing_and_missing_evidence_is_not_observed(self):
        evidence = NativeForkEvidence(Path(self.tmp.name) / "work-parallel-native.json")
        self.assertEqual(0, evidence.read()["version"])
        self.assertFalse(evidence.path.exists())
        lease = self.env.launch("n1", "agent-1")
        forks = self.env.project()["forks"]
        # A bound running lease with no native observation is not a fork.
        self.assertEqual(0, forks["confirmed_count"])
        self.assertEqual([lease], [r["lease_id"] for r in forks["uncertain"]])
        self.assertIsNone(forks["observed_at"])
        self.assertFalse(evidence.path.exists())

    def test_two_observed_running_forks_are_confirmed_and_counted(self):
        a = self.env.launch("n1", "agent-a")
        b = self.env.launch("n2", "agent-b")
        self.env.observe([{"lease_id": a, "agent_id": "agent-a", "agent_name": "work-n1", "status": "running"},
                          {"lease_id": b, "agent_id": "agent-b", "agent_name": "work-n2", "status": "pending"}])
        forks = self.env.project()["forks"]
        self.assertEqual(2, forks["confirmed_count"])
        self.assertEqual({"n1", "n2"}, {r["item_id"] for r in forks["confirmed"]})
        row = next(r for r in forks["confirmed"] if r["item_id"] == "n1")
        self.assertEqual(("Item 1", "R-1", "running", "agent-a"),
                         (row["item_title"], row["item_ref"], row["native"]["status"], row["agent_id"]))
        self.assertIsNotNone(row["bound_at"])
        self.assertIsNotNone(row["reserved_at"])

    def test_reservation_waiting_and_slot_alone_are_never_confirmed(self):
        r = self.env.run("event", {"fork": True}, event="reserve", item_id="n3", priority="P2")
        forks = self.env.project()["forks"]
        self.assertEqual([r["lease_id"]], [x["lease_id"] for x in forks["reservation"]])
        self.assertEqual(0, forks["confirmed_count"])
        lease = {"id": "L", "state": "waiting_input", "host_id": "h", "slot": True}
        record = {"agent_id": "h", "status": "running", "last_seen_at": 100, "missing_since": None}
        self.assertEqual("waiting", classify(lease, record, 100, now=101))

    def test_rows_must_name_this_conversations_bound_fork(self):
        a = self.env.launch("n1", "agent-a")
        other = self.env.launch("n2", "agent-x", scope=OTHER)
        for rows in ([{"lease_id": a, "agent_id": "main", "status": "running"}],
                     [{"lease_id": other, "agent_id": "agent-x", "status": "running"}],
                     [{"lease_id": a, "agent_id": "agent-a", "status": "busy"}],
                     [{"lease_id": a, "agent_id": "agent-a", "status": "running", "transcript": "x"}]):
            with self.assertRaises(WorkParallelError):
                self.env.observe(rows)
        self.assertFalse((Path(self.tmp.name) / "work-parallel-native.json").exists())

    def test_unchanged_observation_keeps_version_and_heartbeat_is_throttled(self):
        a = self.env.launch("n1", "agent-a")
        evidence = m.native_evidence(self.env.store)
        rows = [{"lease_id": a, "agent_id": "agent-a", "status": "running"}]
        leases = {k: v for k, v in self.env.store.read()["leases"].items() if v["scope"] == SCOPE}
        state, _ = evidence.observe(SCOPE, leases, rows, complete=True, expected_version=0, now=1000)
        state2, changed = evidence.observe(SCOPE, leases, rows, complete=True,
                                           expected_version=state["version"], now=1000 + HEARTBEAT_SECONDS - 1)
        self.assertEqual((state["version"], []), (state2["version"], changed))
        state3, _ = evidence.observe(SCOPE, leases, rows, complete=True,
                                     expected_version=state2["version"], now=1000 + HEARTBEAT_SECONDS)
        self.assertEqual(state["version"] + 1, state3["version"])
        self.assertEqual(1000 + HEARTBEAT_SECONDS, state3["records"][a]["last_seen_at"])

    def test_absent_row_is_missing_not_dead_and_stale_is_uncertain(self):
        a = self.env.launch("n1", "agent-a")
        evidence = m.native_evidence(self.env.store)
        leases = {k: v for k, v in self.env.store.read()["leases"].items() if v["scope"] == SCOPE}
        state, _ = evidence.observe(SCOPE, leases, [{"lease_id": a, "agent_id": "agent-a", "status": "running"}],
                                    complete=True, expected_version=0, now=1000)
        lease = leases[a]
        self.assertEqual("confirmed", classify(lease, state["records"][a], 1000, now=1000 + FRESH_SECONDS))
        self.assertEqual("uncertain", classify(lease, state["records"][a], 1000, now=1001 + FRESH_SECONDS))
        state, changed = evidence.observe(SCOPE, leases, [], complete=True, expected_version=state["version"], now=1005)
        self.assertEqual([a], changed)
        self.assertEqual(1005, state["records"][a]["missing_since"])
        self.assertEqual("uncertain", classify(lease, state["records"][a], 1005, now=1006))
        # The slot is untouched: absence never releases capacity.
        self.assertTrue(self.env.store.read()["leases"][a]["slot"])

    def test_terminal_native_status_is_not_processing(self):
        a = self.env.launch("n1", "agent-a")
        self.env.observe([{"lease_id": a, "agent_id": "agent-a", "status": "killed"}])
        forks = self.env.project()["forks"]
        self.assertEqual(0, forks["confirmed_count"])
        self.assertEqual("killed", forks["uncertain"][0]["native"]["status"])

    def test_projection_is_session_scoped_and_device_is_aggregate_only(self):
        self.env.launch("n1", "agent-a")
        self.env.launch("n2", "agent-x", scope=OTHER)
        projected = self.env.project()
        public = m.public_projection(projected, SCOPE)
        text = json.dumps(public)
        self.assertNotIn("agent-x", text)
        self.assertNotIn(OTHER, text)
        self.assertNotIn("leases", public)
        device = public["device"]
        self.assertEqual((2, 1, 1), (device["active_by_priority"]["P2"], device["this_session_by_priority"]["P2"],
                                     device["other_sessions_by_priority"]["P2"]))
        for forbidden in ("owner", "session_pid", "report_hash", "worker_token", "workspace", "idle_proof",
                          "cleanup_proof", "artifact"):
            self.assertNotIn(forbidden, text)
        # `evidence` is only the classification word, never the lease's free-text proof.
        self.assertEqual({"uncertain"}, {row["evidence"] for row in public["forks"]["uncertain"]})

    def test_prune_removes_only_missing_leases_and_reports_changes(self):
        a = self.env.launch("n1", "agent-a")
        b = self.env.launch("n2", "agent-b")
        evidence = m.native_evidence(self.env.store)
        leases = {k: v for k, v in self.env.store.read()["leases"].items() if v["scope"] == SCOPE}
        state, _ = evidence.observe(SCOPE, leases, [
            {"lease_id": a, "agent_id": "agent-a", "status": "running"},
            {"lease_id": b, "agent_id": "agent-b", "status": "running"},
        ], complete=True, expected_version=0, now=1000)
        state, removed = evidence.prune({a}, expected_version=state["version"])
        self.assertEqual([b], removed)
        self.assertEqual({a}, set(state["records"]))
        unchanged, removed = evidence.prune({a}, expected_version=state["version"])
        self.assertEqual((state["version"], []), (unchanged["version"], removed))

    def test_resumed_lease_starts_a_new_native_generation(self):
        a = self.env.launch("n1", "agent-a")
        evidence = m.native_evidence(self.env.store)
        leases = {k: v for k, v in self.env.store.read()["leases"].items() if v["scope"] == SCOPE}
        state, _ = evidence.observe(SCOPE, leases, [{"lease_id": a, "agent_id": "agent-a", "status": "running"}],
                                    complete=True, expected_version=0, now=1000)
        leases[a] = {**leases[a], "host_id": "agent-a2"}
        state, _ = evidence.observe(SCOPE, leases, [{"lease_id": a, "agent_id": "agent-a2", "status": "running"}],
                                    complete=True, expected_version=state["version"], now=1100)
        self.assertEqual(("agent-a2", 1100), (state["records"][a]["agent_id"], state["records"][a]["first_seen_at"]))

    def test_native_observation_requires_evidence_version(self):
        a = self.env.launch("n1", "agent-a")
        with self.assertRaisesRegex(WorkParallelError, "expected_evidence_version required"):
            self.env.run("event", {}, event="native_observation",
                         rows=[{"lease_id": a, "agent_id": "agent-a", "status": "running"}], complete=True)

    def test_workers_cannot_report_native_observations(self):
        a = self.env.launch("n1", "agent-a")
        with self.assertRaises(WorkParallelError):
            self.env.run("event", {"agent_id": "agent-a"}, event="native_observation",
                         rows=[{"lease_id": a, "agent_id": "agent-a", "status": "running"}], complete=True,
                         expected_evidence_version=0)


if __name__ == "__main__":
    unittest.main()
