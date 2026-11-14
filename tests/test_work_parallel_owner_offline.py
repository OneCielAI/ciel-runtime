"""A rework is reserved only for the exact live owner of its conversation.

ciel-runtimectl and REST can decide a rework for a conversation the router is
not bound to (continue-ctl C1-C4). That once reserved a replacement lease with
no owner, which the orphan preview then classed legacy_no_owner. Now the
decision stays durable and waits as ``owner_offline`` ("rework queued: waiting
for the conversation to resume") until a roll-forward proves the owner: the
host's live session process, or the router's bound scope naming exactly this
conversation with a live launcher. Exactly one replacement lease exists even
when two hosts roll forward at once. Stranded recovery follows the same rule.
"""
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore

import test_work_parallel_finalization as fin

SCOPE = fin.SCOPE

PHRASE = "rework queued: waiting for the conversation to resume"


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class _Fixture(unittest.TestCase):
    """The finalization suite's helpers only; none of its test methods (no inheritance)."""
    _base_setup = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle
    pending_version = fin.MainReviewTests.pending_version
    review = fin.MainReviewTests.review

    def setUp(self):
        self._base_setup()
        self.router = self.tmp / "router-instance"
        self.router.mkdir(exist_ok=True)  # the base fixture binds it; each test sets its own scope
        instance = patch("ciel_runtime_support.runtime_paths.ROUTER_INSTANCE_DIR", self.router)
        instance.start()
        self.addCleanup(instance.stop)
        self.unbind()

    # Router scope -----------------------------------------------------------

    def bind(self, conversation=SCOPE, pid=None):
        runtime, session = conversation.split(":", 1)
        (self.router / "compaction-scope.json").write_text(json.dumps({
            "runtime": runtime, "session_id": session, "launcher_pid": os.getpid() if pid is None else pid,
            "epoch": "e1"}), encoding="utf-8")

    def unbind(self):
        (self.router / "compaction-scope.json").unlink(missing_ok=True)

    def replacement_leases(self, predecessor):
        item = self.store.read()["leases"][predecessor]["item_id"]
        return [row for row in self.store.read()["leases"].values()
                if row["item_id"] == item and row["role"] == "resume"]

    def decided(self):
        """A completed result and main's durable rework decision, decided from another conversation's router."""
        reserve, _, _ = self.completed()
        self.idle(reserve["lease_id"])
        self.bind("claude:another-conversation")
        result = self.review(reserve["lease_id"], "rework", decision_id="ctl")
        return reserve["lease_id"], result

    def roll_forward(self, lease_id, **host):
        return self.call("event", {"internal": True, **host}, event="review_roll_forward", lease_id=lease_id)

    def row(self, lease_id):
        return WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json").read()["reviews"][lease_id]



class OwnerOfflineTests(_Fixture):
    # The defect ----------------------------------------------------------------

    def test_rework_for_a_conversation_the_router_is_not_bound_to_reserves_nothing(self):
        lease_id, result = self.decided()
        self.assertEqual(("awaiting_capacity", PHRASE), (result["state"], result["reason"]))
        self.assertEqual({"code": "owner_offline", "message": PHRASE},
                         {k: self.row(lease_id)["blocked"][k] for k in ("code", "message")})
        self.assertEqual([], self.replacement_leases(lease_id))
        self.assertEqual([], [row for row in m._orphan_preview(self.store)["leases"]
                              if row["owner_state"] == "legacy_no_owner"])
        pending = self.call("status", {"internal": True})["review"]["pending"]
        self.assertEqual([("awaiting_capacity", "owner_offline")],
                         [(row["state"], row["blocked"]["code"]) for row in pending if row["lease_id"] == lease_id])

    def test_unbound_router_and_dead_launcher_prove_nothing(self):
        lease_id, _ = self.decided()
        self.unbind()
        self.assertEqual("awaiting_capacity", self.roll_forward(lease_id)["state"])
        self.bind(SCOPE, pid=dead_pid())
        self.assertEqual("awaiting_capacity", self.roll_forward(lease_id)["state"])
        self.assertEqual([], self.replacement_leases(lease_id))

    def test_host_session_process_must_be_alive_and_match_its_stated_identity(self):
        lease_id, _ = self.decided()
        self.assertEqual("awaiting_capacity", self.roll_forward(
            lease_id, session_pid=os.getpid(), session_process_identity="not-this-process")["state"])
        self.assertEqual([], self.replacement_leases(lease_id))
        result = self.roll_forward(lease_id, session_pid=os.getpid())
        self.assertNotEqual("awaiting_capacity", result["state"])
        [lease] = self.replacement_leases(lease_id)
        self.assertEqual(os.getpid(), lease["owner"]["session_pid"])
        self.assertTrue(lease["owner"]["identity"])

    def test_the_bound_conversation_rolls_it_forward_with_its_live_launcher_as_owner(self):
        lease_id, _ = self.decided()
        self.bind(SCOPE)
        result = self.roll_forward(lease_id)
        self.assertNotEqual("awaiting_capacity", result["state"])
        [lease] = self.replacement_leases(lease_id)
        self.assertEqual(os.getpid(), lease["owner"]["session_pid"])
        self.assertIsNone(self.row(lease_id)["blocked"])
        self.assertEqual([], [row for row in m._orphan_preview(self.store)["leases"]
                              if row["owner_state"] == "legacy_no_owner"])

    def test_two_concurrent_roll_forwards_reserve_exactly_one_lease(self):
        lease_id, _ = self.decided()
        self.bind(SCOPE)
        from ciel_runtime_support import work_parallel
        real = work_parallel.rework_admission
        barrier, errors, results = threading.Barrier(2), [], []

        def admission(d, ident, *, now):
            # Both hosts have read the same ledger version before either reserves.
            barrier.wait(10)
            return real(d, ident, now=now)

        def host():
            try:
                results.append(self.roll_forward(lease_id))
            except Exception as error:  # a CAS loss is a valid outcome for one host
                errors.append(error)
        with patch.object(work_parallel, "rework_admission", admission):
            threads = [threading.Thread(target=host) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
        self.assertEqual(2, len(results) + len(errors))
        self.roll_forward(lease_id)  # whichever stage is left
        leases = self.replacement_leases(lease_id)
        self.assertEqual(1, len(leases), leases)
        self.assertEqual(os.getpid(), leases[0]["owner"]["session_pid"])
        self.assertTrue(self.row(lease_id)["stages"]["accepted"])

    def test_57dbfb3_reads_the_owner_offline_row(self):
        import test_work_parallel_rework_worktree as rw
        lease_id, _ = self.decided()
        beta = rw.beta_module("work_parallel_finalization")
        raw = json.loads((self.tmp / "work-parallel-finalization.json").read_text(encoding="utf-8"))
        beta.validate(raw)
        self.assertEqual("owner_offline", raw["reviews"][lease_id]["blocked"]["code"])


class StrandedOwnerOfflineTests(_Fixture):
    """The stranded-v1 recovery waits the same way (no worktree, no report)."""

    def stranded_decided(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        lease_id = reserve["lease_id"]
        self.call("event", {"host_id": "worker-1"}, event="bind", lease_id=lease_id)
        import shutil
        shutil.rmtree(reserve["workspace"]["path"])
        self.call("event", {}, event="host_salvage", lease_id=lease_id, reason="native completed without report")
        self.call("event", {"proof": "durable salvage receipt"}, event="unreported", lease_id=lease_id)
        self.bind("claude:another-conversation")
        status = self.call("status", {"internal": True})
        self.call("item_review", {}, lease_id=lease_id, decision="rework", decision_id="ctl",
                  evidence="recover it", expected_finalization_version=status["review"]["version"])
        return lease_id

    def test_stranded_recovery_waits_owner_offline_then_recovers_for_the_owner(self):
        lease_id = self.stranded_decided()
        side = json.loads((self.tmp / "work-parallel-stranded-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(("deciding", "owner_offline", PHRASE),
                         (side["rows"][lease_id]["state"], side["rows"][lease_id]["blocked"]["code"],
                          side["rows"][lease_id]["blocked"]["message"]))
        self.assertEqual([], self.replacement_leases(lease_id))
        pending = self.call("status", {"internal": True})["review"]["pending"]
        self.assertEqual(["awaiting_capacity"], [row["state"] for row in pending if row["lease_id"] == lease_id])
        self.bind(SCOPE)
        self.roll_forward(lease_id)
        [lease] = self.replacement_leases(lease_id)
        self.assertEqual(os.getpid(), lease["owner"]["session_pid"])


if __name__ == "__main__":
    unittest.main()
