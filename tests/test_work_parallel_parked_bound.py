"""Parked leases count toward no slot and not toward the 100-lease bound.

User decision 2026-11-17 (Stage 3): a worker waiting for an answer
(waiting_input, slot given back) and a result waiting for main's review
(submitted) must not block new forks. Andy's board held 42 waiting_input and
33 submitted leases. Only the storage bound limits parked leases; each still
fences its own Work item against a second execution.
"""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ciel_runtime_support import work_parallel as wp
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore


def candidate(name, **changes):
    row = dict(item_id=name, priority="P2", created_at=10, approved=True, focused=True,
               dependencies_done=True, plan_version=7, usage=.05, role="worker", mode="fork")
    row.update(changes)
    return row


class ParkedLeaseBoundTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = WorkParallelStore(Path(temporary.name) / "parallel.json")
        self.store.configure(dict(paused=False), capability_ready=True, expected_version=0)

    def reserve(self, name, **changes):
        return self.store.reserve([candidate(name, **changes)], scope="scope",
                                  expected_version=self.store.read()["version"], now=100)[1]

    def seed(self, count, state, slot, prefix):
        """Write `count` open leases in `state` straight into the ledger."""
        template_id = self.reserve(prefix + "-template")
        document = self.store.read()
        template = document["leases"].pop(template_id)
        for index in range(count):
            row = dict(template, id=f"{prefix}-{index}", item_id=f"{prefix}-item-{index}", state=state,
                       slot=slot, usage=0, host_id=f"host-{prefix}-{index}", idle_proof="observed")
            if state in ("submitted", "verifying"):
                row["artifact"] = "a" * 64
            document["leases"][row["id"]] = row
        self.store.path.write_text(json.dumps(document), encoding="utf-8")

    def test_one_hundred_parked_leases_leave_admission_open(self):
        self.seed(60, "waiting_input", False, "asked")
        self.seed(45, "submitted", False, "review")
        leases = self.store.read()["leases"]
        self.assertEqual(105, sum(row["state"] in wp.ACTIVE_STATES for row in leases.values()))
        self.assertEqual(0, wp.bounded_open_leases(leases))
        self.assertFalse(wp.lease_bound_reached(leases))
        admitted = self.reserve("fresh")
        self.assertEqual("reserved", self.store.read()["leases"][admitted]["state"])

    def test_reserve_admits_a_fork_past_one_hundred_parked_leases(self):
        # Public behavior only (no new names): HEAD refused this with "lease bound".
        self.seed(70, "waiting_input", False, "asked")
        self.seed(40, "submitted", False, "review")
        admitted = self.reserve("fresh")
        self.assertEqual("reserved", self.store.read()["leases"][admitted]["state"])

    def test_counted_leases_still_stop_at_the_bound(self):
        self.seed(wp.MAX_LEASES, "verifying", False, "verify")
        self.assertTrue(wp.lease_bound_reached(self.store.read()["leases"]))
        with self.assertRaisesRegex(WorkParallelError, "lease bound"):
            self.reserve("blocked")

    def test_a_parked_lease_holding_its_slot_is_counted(self):
        # A waiting worker that has not yet given its slot back still runs.
        self.seed(wp.MAX_LEASES, "waiting_input", True, "holding")
        leases = self.store.read()["leases"]
        self.assertEqual(wp.MAX_LEASES, wp.bounded_open_leases(leases))
        self.assertTrue(wp.lease_bound_reached(leases))

    def test_a_parked_lease_still_fences_its_own_item(self):
        self.seed(1, "waiting_input", False, "asked")
        with self.assertRaisesRegex(WorkParallelError, "no eligible candidate"):
            self.reserve("asked-item-0")

    def test_rework_admission_ignores_parked_leases_for_the_bound(self):
        self.seed(120, "submitted", False, "review")
        document = self.store.read()
        self.assertEqual("", wp.rework_admission(document, "review-0", now=100)[0])


if __name__ == "__main__":
    unittest.main()
