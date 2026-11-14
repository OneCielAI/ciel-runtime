"""The attachment record never evicts a live Work host (review F1, lead ruling 2026-11-13).

continue-ctl's probe (ctl-work-sse-parity/evidence/review/probe-r3.out) attached
255 newer live hosts; eviction removed the live owner's row, the owner then read
``unknown``, and under ruling D another host's restart observed the owner's live
fork as terminal. Only a gone or unreadable launcher's row may be evicted,
oldest first; when every recorded launcher is live the new attach is refused
with a verbatim error naming the bound, and nothing is written.
"""
import json
import unittest
from unittest.mock import patch

from ciel_runtime_support.work_parallel import WorkParallelError
from ciel_runtime_support import work_parallel_attachments as attachments_module
from ciel_runtime_support.work_parallel_attachments import MAX_ATTACHMENTS, WorkParallelAttachments

import test_work_parallel_cross_process_ownership as own
import test_work_parallel_rework_worktree as rw

SCOPE = own.SCOPE
# The verbatim refusal (lead ruling F1: an explicit error naming the bound).
ATTACHMENTS_FULL_TEXT = ("Work host attachment record is full: all 256 recorded Work hosts are live; "
                         "this host is not attached")


class LiveOwnerNeverEvictedTests(unittest.TestCase):
    """Borrowed fixture helpers only (no inherited test methods)."""
    _base_setup = rw.ReworkFixture.setUp
    call = rw.ReworkFixture.call
    review = rw.ReworkFixture.review
    submitted = rw.ReworkFixture.submitted
    rework = rw.ReworkFixture.rework
    launch = rw.ReworkFixture.launch
    bind = rw.ReworkFixture.bind
    report = rw.ReworkFixture.report
    idle = rw.ReworkFixture.idle
    lease = rw.ReworkFixture.lease
    reviews = rw.ReworkFixture.reviews
    absent_after_restart = rw.ReworkFixture.absent_after_restart
    setUp = own._Fixture.setUp
    attach = own._Fixture.attach
    errors = own._Fixture.errors
    completed_rework = own.RestartAndCompleteTests.completed_rework
    restart = own.RestartAndCompleteTests.restart

    def test_reviewer_probe_the_live_owner_stays_and_restart_is_refused(self):
        other_pid = self.pid + 1000
        self.attach(own.OWNER)
        doc = json.loads(self.side.path.read_text(encoding="utf-8"))
        doc["attachments"][own.OWNER].update(launcher_pid=other_pid, creation_identity="other-live")
        self.side.path.write_text(json.dumps(doc), encoding="utf-8")
        self.attach(own.CALLER)
        _, replacement, _, receipt = self.completed_rework()
        identity_now = lambda pid: "other-live" if pid == other_pid else self.identity  # noqa: E731
        with patch("ciel_runtime_support.work_parallel_attachments._identity_now", side_effect=identity_now):
            self.assertEqual("other_live", self.side.owner_class(own.OWNER, own.CALLER))
            initial = len(self.side.read()["attachments"])
            added, refused = 0, None
            while added < 400:
                try:
                    self.side.attach(SCOPE, self.pid, f"{added:032x}")
                except WorkParallelError as error:
                    refused = str(error)
                    break
                added += 1
            rows = self.side.read()["attachments"]
            self.assertEqual(ATTACHMENTS_FULL_TEXT, refused)
            self.assertEqual((MAX_ATTACHMENTS - initial, MAX_ATTACHMENTS), (added, len(rows)))
            self.assertIn(own.OWNER, rows)
            self.assertEqual("other_live", self.side.owner_class(own.OWNER, own.CALLER))
            with self.assertRaises(WorkParallelError) as raised:
                self.restart(replacement, receipt)
        self.assertEqual(own.LIVE_TEXT, str(raised.exception))
        self.assertNotIn(replacement, self.reviews())


class BoundTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.side = WorkParallelAttachments(Path(temporary.name) / "work-parallel-attachments-v1.json")
        self.identities = {}

    def identity_now(self, pid):
        return self.identities.get(pid, "")

    def seed(self, count, *, gone=()):
        """``count`` rows, launcher pid 1000+i, seen_at 1000+i; indexes in ``gone`` have exited."""
        rows = {}
        for index in range(count):
            pid = 1000 + index
            self.identities[pid] = "id-%d" % pid
            rows["%032x" % index] = {"conversation": SCOPE, "launcher_pid": pid, "creation_identity": "id-%d" % pid,
                                     "attached_at": 1000.0 + index, "seen_at": 1000.0 + index}
        for index in gone:
            self.identities[1000 + index] = ""  # exited
        self.side.path.write_text(json.dumps({"producer": "ciel-runtime/work_parallel_attachments", "schema": 1,
                                              "version": 1, "attachments": rows}), encoding="utf-8")
        self.side.read()  # the seed itself validates

    def new_attach(self, nonce="f" * 32, pid=9000):
        self.identities[pid] = "id-new"
        return self.side.attach(SCOPE, pid, nonce, identity_now=self.identity_now)

    def test_all_live_at_the_bound_refuses_the_new_attach_and_writes_nothing(self):
        self.seed(MAX_ATTACHMENTS)
        before = self.side.path.read_bytes()
        with self.assertRaises(WorkParallelError) as raised:
            self.new_attach()
        self.assertEqual(ATTACHMENTS_FULL_TEXT, str(raised.exception))
        self.assertIn(str(MAX_ATTACHMENTS), str(raised.exception))
        self.assertEqual(before, self.side.path.read_bytes())

    def test_gone_rows_are_evicted_oldest_first_before_any_refusal(self):
        self.seed(MAX_ATTACHMENTS, gone=(7, 3))
        self.new_attach()
        rows = self.side.read()["attachments"]
        self.assertEqual(MAX_ATTACHMENTS, len(rows))
        self.assertNotIn("%032x" % 3, rows)   # the oldest gone row
        self.assertIn("%032x" % 7, rows)      # the newer gone row was not needed
        self.assertIn("f" * 32, rows)
        self.new_attach(nonce="e" * 32, pid=9001)
        self.assertNotIn("%032x" % 7, self.side.read()["attachments"])
        with self.assertRaises(WorkParallelError):
            self.new_attach(nonce="d" * 32, pid=9002)  # now every row is live

    def test_an_unreadable_launcher_row_is_evictable(self):
        self.seed(MAX_ATTACHMENTS)
        self.identities[1000 + 5] = None  # identity unreadable now
        self.new_attach()
        self.assertNotIn("%032x" % 5, self.side.read()["attachments"])

    def test_the_module_text_is_the_verbatim_refusal(self):
        self.assertEqual(256, MAX_ATTACHMENTS)
        self.assertEqual(ATTACHMENTS_FULL_TEXT, getattr(attachments_module, "ATTACHMENTS_FULL_TEXT", None))

    def test_an_existing_nonce_keeps_its_update_path_at_the_bound(self):
        self.seed(MAX_ATTACHMENTS)
        row = self.side.read()["attachments"]["%032x" % 0]
        result = self.side.attach(SCOPE, row["launcher_pid"], "%032x" % 0, identity_now=self.identity_now)
        self.assertEqual("%032x" % 0, result["attachment"]["launch_nonce"])
        self.assertEqual(MAX_ATTACHMENTS, len(self.side.read()["attachments"]))


if __name__ == "__main__":
    unittest.main()
