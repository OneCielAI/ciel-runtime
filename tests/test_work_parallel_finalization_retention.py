"""Finalized review rows are trimmed before the journal reaches its byte cap.

Andy 2026-11-25: work-parallel-finalization.json reached 2,097,049 of
2,097,152 bytes with 327 finalized rows and no retention, so every new review
row failed with "finalization byte limit" and seven completed results got no
review. Temp directory only; no live runtime.
"""
import copy
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_finalization as fin
from ciel_runtime_support.work_parallel import WorkParallelError
from ciel_runtime_support.work_parallel_finalization import STAGES, WorkParallelFinalizationStore

DAY = 24 * 60 * 60


def facts(lease_id, *, filler=0, item="n1", generation=1):
    """One review row's facts, the shape _review_facts publishes (no workspace)."""
    return {"lease_id": lease_id, "scope": "claude:retention", "item_id": item, "root_id": "n0",
            "priority": "P1", "plan_version": 1, "generation": generation,
            "artifact": "a" * 64, "completion_receipt": "b" * 64, "fingerprint": "c" * 64,
            "summary": ("s" * filler)[:2000] or "done", "coverage": [],
            "verification": ("v" * max(0, filler - 2000))[:2000] or "tests pass", "workspace": None}


def finalized(row, *, at):
    row = copy.deepcopy(row)
    row.update(state="finalized", decision="approve", decision_id="d-" + row["lease_id"],
               decision_digest=fin.decision_digest(row["lease_id"], "approve", "verified"),
               evidence="verified", stages={key: True for key in STAGES},
               wake={"state": "acknowledged", "attempt_id": None, "attempts": 0, "updated_at": at},
               updated_at=at)
    return row


def rework_ready(row, replacement, *, at):
    row = copy.deepcopy(row)
    row.update(state="rework_ready", decision="rework", decision_id="d-" + row["lease_id"],
               decision_digest=fin.decision_digest(row["lease_id"], "rework", "redo"),
               evidence="redo", stages={key: True for key in STAGES},
               replacement_lease_id=replacement, replacement_generation=row["generation"] + 1,
               wake={"state": "acknowledged", "attempt_id": None, "attempts": 0, "updated_at": at},
               updated_at=at)
    row["launch"] = {**row["launch"], "state": "bound", "dispatch_name": "work-rework-" + replacement,
                     "attempt": 1, "generation": row["generation"] + 1, "agent_id": "agent-" + replacement,
                     "proof": "bound", "launch_ref": None, "updated_at": at}
    return row


class FinalizationRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.path = self.dir / "work-parallel-finalization.json"
        self.now = time.time()

    def store(self):
        return fin.finalization_store(type("Ledger", (), {"path": self.dir / "work-parallel.json"})())

    def template(self, lease_id, **kwargs):
        """A pending row exactly as pending() writes it, read back from a scratch store."""
        scratch = WorkParallelFinalizationStore(self.dir / ("scratch-" + lease_id + ".json"))
        scratch.pending(facts(lease_id, **kwargs), expected_version=0)
        return scratch.read()["reviews"][lease_id]

    def seed(self, rows):
        state = {"schema": fin.SCHEMA, "version": 7, "reviews": {row["lease_id"]: row for row in rows}}
        fin.validate(state)
        self.path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                             encoding="utf-8")

    def near_cap(self, *, protect_recent=0, extra=()):
        """Old finalized rows that fill the journal to within one new row of MAX_BYTES."""
        base = self.template("t0", filler=3900)
        rows, size, index = list(extra), 0, 0
        while True:
            lease = "old%04d" % index
            row = finalized({**copy.deepcopy(base), "lease_id": lease}, at=self.now - 3 * DAY + index)
            candidate = rows + [row]
            raw = json.dumps({"schema": fin.SCHEMA, "version": 7,
                              "reviews": {r["lease_id"]: r for r in candidate}},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if len(raw) > fin.MAX_BYTES - 1500:
                break
            rows, size, index = candidate, len(raw), index + 1
        for offset in range(protect_recent):
            # Recent rows: younger than the protection window.
            rows[-1 - offset] = finalized(rows[-1 - offset], at=self.now - 60 - offset)
        self.seed(rows)
        return rows

    def test_new_review_trims_oldest_finalized_rows_instead_of_failing(self):
        rows = self.near_cap(protect_recent=2)
        store = self.store()
        version = store.read()["version"]
        store.pending(facts("new-lease", filler=3900), expected_version=version)
        state = store.read()
        self.assertIn("new-lease", state["reviews"])
        raw = self.path.read_bytes()
        self.assertLessEqual(len(raw), fin.HISTORY_TRIM_TARGET)
        # Oldest first: the first rows went, the protected recent ones stayed.
        self.assertNotIn(rows[0]["lease_id"], state["reviews"])
        self.assertIn(rows[-1]["lease_id"], state["reviews"])
        self.assertIn(rows[-2]["lease_id"], state["reviews"])
        kept = sorted(row["updated_at"] for row in state["reviews"].values() if row["state"] == "finalized")
        removed = [row for row in rows if row["lease_id"] not in state["reviews"]]
        self.assertTrue(removed)
        self.assertLessEqual(max(row["updated_at"] for row in removed), kept[0])

    def test_undecided_and_rework_rows_are_never_trimmed(self):
        pending = self.template("live-pending", filler=100)
        predecessor = rework_ready(self.template("pred", filler=100, item="n9"), "repl", at=self.now - 5 * DAY)
        replacement = finalized(self.template("repl", filler=100, item="n9", generation=2), at=self.now - 4 * DAY)
        rows = self.near_cap(extra=[pending, predecessor, replacement])
        store = self.store()
        store.pending(facts("new-lease", filler=3900), expected_version=store.read()["version"])
        state = store.read()
        self.assertIn("live-pending", state["reviews"])
        self.assertIn("pred", state["reviews"])
        # The finalized replacement of a rework_ready predecessor stays: without
        # it the projection would show the finished predecessor again.
        self.assertIn("repl", state["reviews"])
        self.assertNotIn("pred", [row["lease_id"] for row in store.projection("claude:retention")["pending"]])
        old = [row["lease_id"] for row in rows if row["lease_id"].startswith("old")]
        self.assertTrue([lease for lease in old if lease not in state["reviews"]])
        self.assertLessEqual(len(self.path.read_bytes()), fin.HISTORY_TRIM_TARGET)

    def test_a_stranded_recovered_rows_replacement_is_kept(self):
        rows = self.near_cap()
        oldest = rows[0]["lease_id"]
        (self.dir / "work-parallel-stranded-v1.json").write_text(json.dumps({"schema": 1, "version": 3, "rows": {
            "stranded-src": {"lease_id": "stranded-src", "state": "recovered", "replacement_lease_id": oldest}}}),
            encoding="utf-8")
        store = self.store()
        store.pending(facts("new-lease", filler=3900), expected_version=store.read()["version"])
        state = store.read()
        self.assertIn(oldest, state["reviews"])
        self.assertNotIn(rows[1]["lease_id"], state["reviews"])

    def test_unreadable_stranded_journal_keeps_the_old_refusal(self):
        self.near_cap()
        (self.dir / "work-parallel-stranded-v1.json").write_text("{not json", encoding="utf-8")
        store = self.store()
        with self.assertRaisesRegex(WorkParallelError, "finalization byte limit"):
            store.pending(facts("new-lease", filler=3900), expected_version=store.read()["version"])

    def test_nothing_trimmable_still_refuses(self):
        rows = self.near_cap()
        recent = [finalized(row, at=self.now - 30) for row in rows]
        self.seed(recent)
        store = self.store()
        with self.assertRaisesRegex(WorkParallelError, "finalization byte limit"):
            store.pending(facts("new-lease", filler=3900), expected_version=store.read()["version"])

    def test_a_write_under_the_cap_trims_nothing(self):
        rows = [finalized(self.template("a%d" % index), at=self.now - 5 * DAY) for index in range(3)]
        self.seed(rows)
        store = self.store()
        store.pending(facts("new-lease"), expected_version=store.read()["version"])
        self.assertEqual({"a0", "a1", "a2", "new-lease"}, set(store.read()["reviews"]))


if __name__ == "__main__":
    unittest.main()
