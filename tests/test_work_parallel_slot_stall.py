"""Fork slots held by leases with no running fork (Walkie 2026-11-16).

All three P1 slots of the Walkie conversation were held while 0 forks ran, so
no queued item could start:
- n1856: a dead owner's rework reservation that never bound a worker
  (orphan_release refused it: "unbound worker");
- n1438: a submitted result still flagged as holding a slot
  (orphan_release refused it: "unexpected artifact");
- n1861: a waiting lease whose owner died after the launch-time sweep ran.
Two owner-less reservations from 2026-11-05 in a live conversation also held
the only P3 slot and one of two P2 slots.
"""
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_orphan_sweep as sweep_module
from ciel_runtime_support.work_parallel import WorkParallelStore, active_by_priority
from ciel_runtime_support.work_parallel_mcp import _orphan_preview

DEAD = {"owner_state": "dead", "observed_identity": ""}


def candidate(name, priority="P1"):
    return dict(item_id=name, priority=priority, created_at=10, approved=True, focused=True,
                dependencies_done=True, plan_version=7, usage=.05, role="worker", mode="fork")


class SlotStallTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WorkParallelStore(self.root / "work-parallel.json")
        self.store.configure({"paused": False}, capability_ready=True, expected_version=0)
        self.store.configure({"priority_limits": {"P1": 3, "P2": 2, "P3": 1, "P4": 1, "P5": 1}},
                             capability_ready=True, expected_version=self.store.read()["version"])
        self.now = 100

    def reserve(self, name, scope="claude:a", owner=None, bind=True, priority="P1", dispatch=True):
        self.now += 61
        d, ident = self.store.reserve([candidate(name, priority)], scope=scope, now=self.now,
                                      dispatch_name="work-" + name if dispatch else None,
                                      expected_version=self.store.read()["version"],
                                      **({"owner": owner} if owner else {}))
        if bind:
            self.store.bind(ident, "host-" + name, agent_name="work-" + name,
                            expected_version=self.store.read()["version"])
        return ident

    def force(self, ident, **fields):
        def apply(d):
            d["leases"][ident].update(fields)
        self.store._change(apply, self.store.read()["version"])

    def test_dead_owners_unbound_reservation_is_released(self):
        ident = self.reserve("n1856", owner={"session_pid": 11, "identity": "birth-11"}, bind=False)
        self.store.orphan_release(ident, proof="owner gone", observation=DEAD,
                                  expected_version=self.store.read()["version"])
        self.assertNotIn(ident, self.store.read()["leases"])

    def test_submitted_result_holding_a_slot_frees_it_and_stays_reviewable(self):
        ident = self.reserve("n1438", owner={"session_pid": 11, "identity": "birth-11"})
        artifact = hashlib.sha256(b"result").hexdigest()
        self.store.report_completion(ident, plan_version=7, artifact=artifact, evidence="done",
                                     expected_version=self.store.read()["version"])
        self.force(ident, slot=True, idle_proof=None)  # the recorded anomaly
        self.assertEqual(1, active_by_priority(self.store.read()["leases"])["P1"])
        self.store.orphan_release(ident, proof="owner gone", observation=DEAD,
                                  expected_version=self.store.read()["version"])
        lease = self.store.read()["leases"][ident]
        self.assertEqual(("submitted", False, artifact), (lease["state"], lease["slot"], lease["artifact"]))
        self.assertEqual(0, active_by_priority(self.store.read()["leases"])["P1"])

    def test_sweep_frees_every_walkie_slot_and_capacity_returns(self):
        owner = {"session_pid": 11, "identity": "birth-11"}
        unbound = self.reserve("n1856", owner=owner, bind=False)
        submitted = self.reserve("n1438", owner=owner)
        artifact = hashlib.sha256(b"r").hexdigest()
        self.store.report_completion(submitted, plan_version=7, artifact=artifact, evidence="done",
                                     expected_version=self.store.read()["version"])
        self.force(submitted, slot=True, idle_proof=None)
        waiting = self.reserve("n1861", owner=owner)
        self.store.report_question(waiting, plan_version=7, evidence="asks",
                                   expected_version=self.store.read()["version"])
        self.force(waiting, slot=True, idle_proof=None)  # reacquired, then its owner died
        # Written by a build before owners and dispatch names: no launch can bind them.
        old_p3 = self.reserve("n176", scope="claude:live", bind=False, priority="P3", dispatch=False)
        old_p2 = self.reserve("n212", scope="claude:live", bind=False, priority="P2", dispatch=False)
        # A named owner-less reservation in a live conversation may still be launching.
        fresh = self.reserve("fresh", scope="claude:live", bind=False, priority="P2")
        self.assertEqual({"P1": 3, "P2": 2, "P3": 1, "P4": 0, "P5": 0},
                         active_by_priority(self.store.read()["leases"]))
        with patch("ciel_runtime_support.work_parallel_mcp.process_identity", {11: ""}.get):
            result = sweep_module.sweep(self.store, {"claude:live"}, _orphan_preview)
        self.assertEqual({unbound, submitted, waiting, old_p3, old_p2}, {r["lease_id"] for r in result["released"]})
        self.assertEqual([], result["kept"])
        leases = self.store.read()["leases"]
        self.assertTrue(leases[fresh]["slot"])
        self.assertEqual("orphaned", leases[waiting]["state"])
        self.assertEqual("submitted", leases[submitted]["state"])
        self.assertEqual({"P1": 0, "P2": 1, "P3": 0, "P4": 0, "P5": 0}, active_by_priority(leases))

    def test_running_router_sweeps_at_most_once_a_minute(self):
        calls = []
        with patch.object(sweep_module, "sweep_at_launch", lambda log: calls.append(1) or {"released": 0}), \
                patch.dict(sweep_module._last_sweep, {"at": 0.0}):
            self.assertEqual({"released": 0}, sweep_module.sweep_while_running(print, now=1000.0))
            self.assertIsNone(sweep_module.sweep_while_running(print, now=1059.0))
            self.assertEqual({"released": 0}, sweep_module.sweep_while_running(print, now=1060.0))
        self.assertEqual(2, len(calls))

    def test_the_host_status_read_runs_the_sweep(self):
        from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
        config = {"work_mode": True, "work_parallel_host": {"token": "x" * 40}}
        seen = []
        with patch.object(sweep_module, "sweep_while_running", lambda log: seen.append("swept")), \
                patch("ciel_runtime_support.work_parallel_mcp.ledger", lambda: self.store):
            for host in ({"token": "x" * 40}, None):
                args = {"session_id": "claude:a", **({"_host": host} if host else {})}
                result = dispatch_work_tool(1, "work_parallel_status", args, load_config=lambda: config,
                                            state_dir=self.root,
                                            respond=lambda _id, text, error=False: {"text": text, "error": error})
                self.assertFalse(result["error"], result)
        self.assertEqual(["swept"], seen)  # the model's own read never sweeps


if __name__ == "__main__":
    unittest.main()
