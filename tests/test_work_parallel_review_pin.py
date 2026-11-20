"""Wake bookkeeping never makes main's review decision stale (lead ruling R1).

The host shows main a pending review with ``expected_finalization_version``
and, since R1, a read-time ``review_pin``: "rp1:" plus the canonical-JSON
digest of the stored row minus wake, launch and updated_at. The wake's own
prepare/record writes, or a write to another review, move the journal version
but not the pin, so a decision carrying the pin lands on its first attempt.
Every semantic fact and cross-row invariant is still checked fresh, and a
decision without a pin keeps the version rule. Measured defect (pump, real
console run-p0-row1-absent-20261112-220809; review-pin-probe-*.json, both
builds): wake 2 -> 3 -> 4, then main's decision with 2 refused "reread".
"""
import json
import shutil
import unittest
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelConflict, WorkParallelError
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore

import test_work_parallel_finalization as fin

REVIEW_PIN = r"\Arp1:[0-9a-f]{64}\Z"


class ReviewPinHelpers:
    """The finalization suite's helpers only; this mixin defines no test methods."""
    _base_setup = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call
    completed = fin.MainReviewTests.completed
    idle = fin.MainReviewTests.idle
    pending_version = fin.MainReviewTests.pending_version
    review_row = fin.MainReviewTests.review_row

    def setUp(self):
        self._base_setup()

    def pending(self):
        return {row["lease_id"]: row for row in self.call("status", {})["review"]["pending"]}

    def published(self, item_id=None, worker_id="worker-1"):
        reserve, workspace, _ = self.completed(item_id=item_id, worker_id=worker_id)
        self.idle(reserve["lease_id"])
        return reserve["lease_id"], workspace

    def wake(self, lease_id):
        prepared = self.call("event", {"internal": True}, event="review_wake", operation="prepare",
                             lease_id=lease_id)
        self.call("event", {"internal": True}, event="review_wake", operation="record", lease_id=lease_id,
                  attempt_id=prepared["attempt_id"], state="appended")

    def decide(self, lease_id, decision, version, pin=None, decision_id=None):
        args = {"lease_id": lease_id, "decision": decision, "decision_id": decision_id or decision + "-1",
                "evidence": "main verified", "expected_finalization_version": version}
        if pin is not None:
            args["expected_review_pin"] = pin
        return self.call("item_review", {}, **args)

    def journal(self):
        return WorkParallelFinalizationStore(self.tmp / "work-parallel-finalization.json")

    def resume_leases(self, item_id):
        return [row for row in self.store.read()["leases"].values()
                if row["item_id"] == item_id and row["role"] == "resume"]


class ReviewPinTests(ReviewPinHelpers, unittest.TestCase):
    # The defect and the pin -------------------------------------------------

    def test_wake_writes_then_decision_with_the_shown_pin_lands_first_time(self):
        lease_id, _ = self.published()
        shown = self.pending()[lease_id]
        version = self.pending_version()
        self.assertRegex(shown["review_pin"], REVIEW_PIN)
        self.wake(lease_id)
        self.assertEqual(version + 2, self.journal().read()["version"])
        self.assertEqual(shown["review_pin"], self.pending()[lease_id]["review_pin"])
        result = self.decide(lease_id, "approve", version, pin=shown["review_pin"])
        self.assertEqual(("approve", "finalized"), (result["decision"], result["state"]))

    def test_without_a_pin_the_version_rule_is_unchanged(self):
        # A host that restarted lost the (lease, version) -> pin mapping it showed
        # and sends no pin: a stale version is still refused.
        lease_id, _ = self.published()
        version = self.pending_version()
        self.wake(lease_id)
        with self.assertRaisesRegex(WorkParallelConflict, "Work finalization changed; reread"):
            self.decide(lease_id, "approve", version)
        self.assertIsNone(self.review_row(lease_id)["decision"])
        self.assertEqual("approve", self.decide(lease_id, "approve", self.pending_version())["decision"])

    def test_a_decision_on_another_row_does_not_refuse_this_one(self):
        first, _ = self.published()
        second, _ = self.published(item_id=self.board.item2, worker_id="worker-2")
        shown = self.pending()
        version = self.pending_version()
        self.decide(second, "approve", version, pin=shown[second]["review_pin"])
        self.assertGreater(self.journal().read()["version"], version)
        self.assertEqual("finalized",
                         self.decide(first, "approve", version, pin=shown[first]["review_pin"])["state"])

    def test_a_malformed_pin_is_refused_and_nothing_is_decided(self):
        lease_id, _ = self.published()
        for pin in ("rp1:" + "A" * 64, "rp2:" + "a" * 64, "a" * 64):
            with self.subTest(pin=pin), self.assertRaisesRegex(WorkParallelError, "rp1:"):
                self.decide(lease_id, "approve", self.pending_version(), pin=pin)
        self.assertIsNone(self.review_row(lease_id)["decision"])

    # Fresh facts still refuse ----------------------------------------------

    def test_a_changed_workspace_head_is_refused_even_with_a_matching_pin(self):
        lease_id, workspace = self.published()
        pin = self.pending()[lease_id]["review_pin"]
        (workspace / "late.txt").write_text("changed after review")
        fin.git(workspace, "add", "late.txt")
        fin.git(workspace, "commit", "-m", "late change")
        with self.assertRaisesRegex(WorkParallelError, "stale workspace"):
            self.decide(lease_id, "approve", self.pending_version(), pin=pin)
        self.assertIsNone(self.review_row(lease_id)["decision"])

    def test_a_different_decision_already_durable_is_refused(self):
        lease_id, _ = self.published()
        pin, version = self.pending()[lease_id]["review_pin"], self.pending_version()
        self.decide(lease_id, "approve", version, pin=pin, decision_id="first")
        with self.assertRaisesRegex(WorkParallelError, "already decided differently"):
            self.decide(lease_id, "rework", version, pin=pin, decision_id="second")

    def test_a_pin_of_another_row_never_decides_this_one(self):
        first, _ = self.published()
        second, _ = self.published(item_id=self.board.item2, worker_id="worker-2")
        shown = self.pending()
        with self.assertRaisesRegex(WorkParallelConflict, "review changed; reread"):
            self.decide(first, "approve", self.pending_version(), pin=shown[second]["review_pin"])

    def test_row_two_takes_the_last_capacity_then_row_one_waits_and_is_never_double_reserved(self):
        first, _ = self.published()
        second, _ = self.published(item_id=self.board.item2, worker_id="worker-2")
        shown, version = self.pending(), self.pending_version()
        state = self.store.read()
        state["policy"]["priority_limits"]["P1"] = 1
        self.store.path.write_text(json.dumps(state), encoding="utf-8")
        self.decide(second, "rework", version, pin=shown[second]["review_pin"], decision_id="r2")
        self.assertEqual(1, len(self.resume_leases(self.board.item2)))
        result = self.decide(first, "rework", version, pin=shown[first]["review_pin"], decision_id="r1")
        self.assertEqual("awaiting_capacity", result["state"])
        self.assertEqual("capacity", self.review_row(first)["blocked"]["code"])
        self.assertEqual([], self.resume_leases(self.board.item))
        self.call("event", {"internal": True}, event="review_roll_forward", lease_id=first)
        self.assertEqual([], self.resume_leases(self.board.item))

    # Mixed versions: a real 57dbfb3 writer in the race ----------------------

    def beta_store(self):
        import test_work_parallel_rework_worktree as rw
        return rw.beta_module("work_parallel_finalization").WorkParallelFinalizationStore(
            self.tmp / "work-parallel-finalization.json")

    def test_a_57dbfb3_write_in_the_race_is_kept_and_the_decision_commits(self):
        first, _ = self.published()
        second, _ = self.published(item_id=self.board.item2, worker_id="worker-2")
        pin, version = self.pending()[first]["review_pin"], self.pending_version()
        real, injected = m._revalidate_review, []

        def beta_writes_between_pin_check_and_decide(store, board_store, row, decision=None):
            result = real(store, board_store, row, decision)
            if not injected:
                beta = self.beta_store()
                injected.append(beta.wake_prepare(second, expected_version=beta.read()["version"]))
            return result
        with patch.object(m, "_revalidate_review", beta_writes_between_pin_check_and_decide):
            result = self.decide(first, "approve", version, pin=pin)
        self.assertEqual(("approve", "finalized"), (result["decision"], result["state"]))
        self.assertEqual("submitting", self.review_row(second)["wake"]["state"])  # beta's update kept
        self.beta_store().read()  # 57dbfb3 still validates the file

    def test_a_57dbfb3_decision_of_the_same_row_in_the_race_wins(self):
        lease_id, _ = self.published()
        pin, version = self.pending()[lease_id]["review_pin"], self.pending_version()
        real, injected = m._revalidate_review, []

        def beta_decides_first(store, board_store, row, decision=None):
            result = real(store, board_store, row, decision)
            if not injected:
                beta = self.beta_store()
                injected.append(beta.decide(lease_id, "approve", "beta-decision", "beta evidence",
                                            decision_body_digest="e" * 64,
                                            expected_version=beta.read()["version"]))
            return result
        with patch.object(m, "_revalidate_review", beta_decides_first):
            with self.assertRaisesRegex(WorkParallelError, "already decided differently"):
                self.decide(lease_id, "rework", version, pin=pin, decision_id="ours")
        row = self.review_row(lease_id)
        self.assertEqual(("approve", "beta-decision"), (row["decision"], row["decision_id"]))
        self.assertEqual([], self.resume_leases(self.board.item))

    def test_the_pin_is_never_persisted(self):
        lease_id, _ = self.published()
        self.assertRegex(self.pending()[lease_id]["review_pin"], REVIEW_PIN)
        self.assertNotIn("review_pin", (self.tmp / "work-parallel-finalization.json").read_text())
        self.beta_store().read()


class TombstoneDecisionTests(ReviewPinHelpers, unittest.TestCase):
    """A decision made on the healthy projection survives the broken-worktree move (pump, cp4 batch)."""

    def broken_after_publication(self):
        lease_id, workspace = self.published()
        shown, version = self.pending()[lease_id], self.pending_version()
        shutil.rmtree(workspace)
        # .get: the pre-R1 projection has no pin; the version-only paths still run.
        return lease_id, shown.get("review_pin"), version

    def test_rework_on_the_earlier_projection_is_accepted_when_the_decision_moves_the_row(self):
        lease_id, _, version = self.broken_after_publication()
        result = self.decide(lease_id, "rework", version)
        self.assertEqual("rework", result["decision"])
        side = json.loads((self.tmp / "work-parallel-stranded-v1.json").read_text(encoding="utf-8"))["rows"][lease_id]
        self.assertIsNotNone(side["decision_id"])
        self.assertIsNone(self.review_row(lease_id))

    def moved_then_rework(self, use_pin):
        lease_id, pin, version = self.broken_after_publication()
        # The approve observes the broken worktree and moves the row (refused).
        with self.assertRaisesRegex(WorkParallelError,
                                    "workspace unavailable: .*; approve refused; rework creates a fresh workspace"):
            self.decide(lease_id, "approve", version, pin=pin if use_pin else None)
        self.assertIsNone(self.review_row(lease_id))
        result = self.decide(lease_id, "rework", version, pin=pin if use_pin else None, decision_id="rework-2")
        # The frozen-schema tombstone carries the old version, never a pin.
        side_text = (self.tmp / "work-parallel-stranded-v1.json").read_text(encoding="utf-8")
        self.assertNotIn("review_pin", side_text)
        self.assertNotIn("review_pin", (self.tmp / "work-parallel-finalization.json").read_text(encoding="utf-8"))
        return result

    def test_rework_after_the_row_already_moved_is_accepted_by_the_projection_version(self):
        self.assertEqual("rework", self.moved_then_rework(False)["decision"])

    def test_rework_after_the_row_already_moved_is_accepted_by_the_projection_pin(self):
        self.assertEqual("rework", self.moved_then_rework(True)["decision"])

    def test_approve_stays_refused_for_a_stranded_row(self):
        lease_id, pin, version = self.broken_after_publication()
        with self.assertRaisesRegex(WorkParallelError, "approve refused"):
            self.decide(lease_id, "approve", version, pin=pin)
        with self.assertRaisesRegex(WorkParallelError, "approve refused"):
            self.decide(lease_id, "approve", self.pending_version(), decision_id="approve-2")


if __name__ == "__main__":
    unittest.main()
