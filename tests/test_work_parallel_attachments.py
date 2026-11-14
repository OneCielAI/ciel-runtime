"""R2 B2: the Work host attachment sidecar and its owner classes (module only).

Liveness is proven by the launcher's pid plus its creation identity, observed
now. A record outlives its process, so a record alone proves nothing.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.work_parallel import WorkParallelError
from ciel_runtime_support.work_parallel_attachments import (
    EARLIER_CHILD, FILE_NAME, GONE, MAX_ATTACHMENTS, OTHER_LIVE, OWN, UNKNOWN, WorkParallelAttachments)
from ciel_runtime_support.workspace_router_selection import _process_identity

CONVERSATION = "claude:attachments-test"
A, B, C = "a" * 32, "b" * 32, "c" * 32


def dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ledger = Path(temporary.name) / "work-parallel.json"
        self.store = WorkParallelAttachments.beside(self.ledger)
        self.me = os.getpid()
        self.my_identity = _process_identity(self.me)
        self.assertTrue(self.my_identity)

    def other_live(self):
        """A live process that is not this launcher: a sleeping child."""
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        return child.pid

    def test_attach_records_the_launcher_identity_the_router_observed(self):
        result = self.store.attach(CONVERSATION, self.me, A)
        self.assertEqual({"state": "live", "launcher_pid": self.me, "launch_nonce": A},
                         {k: result["attachment"][k] for k in ("state", "launcher_pid", "launch_nonce")})
        self.assertNotIn("creation_identity", json.dumps(result))
        raw = json.loads((self.ledger.parent / FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(self.my_identity, raw["attachments"][A]["creation_identity"])

    def test_attach_refuses_a_dead_launcher_and_malformed_input(self):
        with self.assertRaisesRegex(WorkParallelError, "not running"):
            self.store.attach(CONVERSATION, dead_pid(), A)
        for args in ((CONVERSATION, 0, A), (CONVERSATION, "1", A), (CONVERSATION, self.me, "a b"),
                     (CONVERSATION, self.me, "x" * 201), (CONVERSATION, self.me, ""),
                     ("not-a-scope", self.me, A)):
            with self.subTest(args=args), self.assertRaises(WorkParallelError):
                self.store.attach(*args)
        self.assertFalse((self.ledger.parent / FILE_NAME).exists())

    def test_a_nonce_belongs_to_one_host_process(self):
        self.store.attach(CONVERSATION, self.me, A)
        self.store.attach(CONVERSATION, self.me, A)  # idempotent
        with self.assertRaisesRegex(WorkParallelError, "already attached"):
            self.store.attach(CONVERSATION, self.other_live(), A)
        with self.assertRaisesRegex(WorkParallelError, "already attached"):
            self.store.attach("claude:another", self.me, A)

    def test_owner_classes(self):
        other = self.other_live()
        self.store.attach(CONVERSATION, self.me, A)          # the caller
        self.store.attach(CONVERSATION, self.me, B)          # earlier claude.exe of the same launcher
        self.store.attach(CONVERSATION, other, C)            # another live launcher
        self.assertEqual(OWN, self.store.owner_class(A, A))
        self.assertEqual(EARLIER_CHILD, self.store.owner_class(B, A))
        self.assertEqual(OTHER_LIVE, self.store.owner_class(C, A))
        self.assertEqual(UNKNOWN, self.store.owner_class("d" * 32, A))   # no record (57dbfb3, pre-deploy)
        self.assertEqual(UNKNOWN, self.store.owner_class(None, A))       # pre-nonce intent
        # Without an attached caller a live owner is not proven other or own.
        self.assertEqual(UNKNOWN, self.store.owner_class(B, "e" * 32))
        self.assertEqual(UNKNOWN, self.store.owner_class(C, None))

    def test_a_gone_or_reused_launcher_is_gone_and_unreadable_is_unknown(self):
        self.store.attach(CONVERSATION, self.me, A)
        self.store.attach(CONVERSATION, self.me, B)
        def exited(pid):
            return ""
        def reused(pid):
            return "1"
        def unreadable(pid):
            return None
        self.assertEqual(GONE, self.store.owner_class(B, "e" * 32, identity_now=exited))
        self.assertEqual(GONE, self.store.owner_class(B, "e" * 32, identity_now=reused))
        self.assertEqual(UNKNOWN, self.store.owner_class(B, "e" * 32, identity_now=unreadable))

    def test_lease_owner_classes(self):
        self.store.attach(CONVERSATION, self.me, A)
        mine = {"session_pid": self.me, "identity": self.my_identity}
        other = self.other_live()
        self.assertEqual(EARLIER_CHILD, self.store.lease_owner_class(mine, A))
        self.assertEqual(OTHER_LIVE, self.store.lease_owner_class(
            {"session_pid": other, "identity": _process_identity(other)}, A))
        self.assertEqual(GONE, self.store.lease_owner_class({"session_pid": dead_pid(), "identity": "123"}, A))
        for owner in (None, {"session_pid": self.me, "identity": None}, {"session_pid": 0, "identity": "1"}):
            self.assertEqual(UNKNOWN, self.store.lease_owner_class(owner, A))
        self.assertEqual(mine, self.store.caller_owner(CONVERSATION, A))
        self.assertIsNone(self.store.caller_owner("claude:another", A))
        self.assertIsNone(self.store.caller_owner(CONVERSATION, "e" * 32))

    def test_summary_counts_and_never_identities(self):
        other = self.other_live()
        self.store.attach(CONVERSATION, self.me, A)
        self.store.attach(CONVERSATION, other, B)
        self.store.attach("claude:another", self.me, C)
        summary = self.store.summary(CONVERSATION)
        self.assertEqual((True, 2, 0, 0), (summary["available"], summary["live"], summary["gone"], summary["unknown"]))
        self.assertNotIn(self.my_identity, json.dumps(summary))

    def test_unreadable_record_is_left_unchanged_and_reads_unavailable(self):
        path = self.ledger.parent / FILE_NAME
        path.write_text("{not json", encoding="utf-8")
        self.assertFalse(self.store.summary(CONVERSATION)["available"])
        with self.assertRaises(WorkParallelError):
            self.store.attach(CONVERSATION, self.me, A)
        self.assertEqual("{not json", path.read_text(encoding="utf-8"))

    def test_bound_evicts_gone_launchers_before_live_ones(self):
        dead = dead_pid()
        def fake(pid):
            return "" if pid == dead else self.my_identity if pid == self.me else None
        raw = {"producer": "ciel-runtime/work_parallel_attachments", "schema": 1, "version": 1, "attachments": {}}
        for index in range(MAX_ATTACHMENTS):
            raw["attachments"][f"{index:032x}"] = {"conversation": CONVERSATION, "launcher_pid": dead,
                                                    "creation_identity": "9", "attached_at": 10.0 + index,
                                                    "seen_at": 10.0 + index}
        raw["attachments"]["f" * 32] = {"conversation": CONVERSATION, "launcher_pid": self.me,
                                        "creation_identity": self.my_identity, "attached_at": 1.0, "seen_at": 1.0}
        del raw["attachments"][f"{0:032x}"]
        (self.ledger.parent / FILE_NAME).write_text(json.dumps(raw), encoding="utf-8")
        self.store.attach(CONVERSATION, self.me, A, identity_now=fake)
        rows = json.loads((self.ledger.parent / FILE_NAME).read_text(encoding="utf-8"))["attachments"]
        self.assertEqual(MAX_ATTACHMENTS, len(rows))
        self.assertIn("f" * 32, rows)   # the oldest row is live, so it stays
        self.assertIn(A, rows)
        self.assertNotIn(f"{1:032x}", rows)  # the oldest gone row was evicted


if __name__ == "__main__":
    unittest.main()
