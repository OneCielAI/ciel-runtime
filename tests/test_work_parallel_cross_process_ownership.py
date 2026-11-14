"""R2 B2: automatic absence/restart/complete-list decisions are fenced by Work host attachments.

A launch attempt joins to its owning claude.exe through launch_nonce. A worker
lease joins through its durable launcher {pid, creation identity}. The owning
launcher is checked now through process_identity. Agent-list absence is never
proof that another live or unknown process's fork is gone.
"""
import json
import os
import unittest
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelError
from ciel_runtime_support.work_parallel_attachments import WorkParallelAttachments
from ciel_runtime_support.workspace_router_selection import _process_identity

import test_work_parallel_rework_worktree as rw

SCOPE = rw.SCOPE
OWNER, CALLER, EARLIER, UNKNOWN = "a" * 32, "b" * 32, "c" * 32, "d" * 32
UNKNOWN_TEXT = "Owner of this launch attempt is unknown; resolve it from the Work pane."
LIVE_TEXT = "Another live Work host owns this launch attempt; waiting for that host."


class _Fixture(unittest.TestCase):
    """Rework helpers only; no inherited test methods."""
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

    def setUp(self):
        self._base_setup()
        self.side = WorkParallelAttachments.beside(self.store.path)
        self.pid = os.getpid()
        self.identity = _process_identity(self.pid)
        self.assertTrue(self.identity)

    def attach(self, nonce, pid=None):
        return self.side.attach(SCOPE, self.pid if pid is None else pid, nonce)

    def status(self, nonce=None):
        host = {"token": rw.SECRET, **({"launch_nonce": nonce} if nonce else {})}
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", {"_host": host}, self.board, SCOPE,
                                    self.config, None))

    def errors(self):
        from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
        return WorkParallelFailureLog.beside(self.store.path).public(SCOPE)["failures"]


class LaunchAbsenceTests(_Fixture):
    def observed_attempt(self, owner=OWNER):
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-owner", nonce=owner)
        return first, replacement, name

    def absent(self, replacement, name, caller=CALLER, *, event="rework_launch_absent", launch_ref=None):
        host = {"internal": True, "launch_nonce": caller,
                "absent_dispatch_name": name, "absent_agent_id": "agent-owner"}
        args = {"event": event, "lease_id": replacement, "dispatch_name": name}
        if launch_ref is not None:
            args["launch_ref"] = launch_ref
        return self.call("event", host, **args)

    def test_own_process_is_not_resolved_by_the_automatic_path(self):
        self.attach(OWNER)
        _, replacement, name = self.observed_attempt()
        with self.assertRaisesRegex(WorkParallelError, "belongs to this host"):
            self.absent(replacement, name, caller=OWNER)
        self.assertEqual("spawn_observed", self.reviews()[next(iter(self.reviews()))]["launch"]["state"])

    def test_earlier_child_of_the_same_launcher_is_allowed_after_restart(self):
        self.attach(OWNER)
        self.attach(EARLIER)  # same launcher pid + creation identity; one child at a time
        _, replacement, name = self.observed_attempt(owner=OWNER)
        result = self.absent(replacement, name, caller=EARLIER)
        self.assertEqual("spawn_denied", result["state"])

    def test_gone_owner_is_allowed(self):
        self.attach(OWNER)
        self.attach(CALLER)
        _, replacement, name = self.observed_attempt()
        # OWNER's launcher exited: its recorded creation identity no longer matches.
        with patch("ciel_runtime_support.work_parallel_attachments._identity_now",
                   side_effect=lambda pid: "" if pid == self.pid + 1000 else self.identity):
            doc = json.loads(self.side.path.read_text(encoding="utf-8"))
            doc["attachments"][OWNER].update(launcher_pid=self.pid + 1000, creation_identity="gone-launcher")
            self.side.path.write_text(json.dumps(doc), encoding="utf-8")
            result = self.absent(replacement, name)
        self.assertEqual("spawn_denied", result["state"])

    def test_other_live_owner_is_refused_and_visible(self):
        # Use a distinct live pid: this test process's child is represented by
        # an explicit identity function so the sidecar record is deterministic.
        self.attach(CALLER)
        self.attach(OWNER)
        _, replacement, name = self.observed_attempt()
        # OWNER and CALLER share one launcher here, so rewrite OWNER's durable
        # launcher to a distinct live identity while retaining its nonce.
        path = self.side.path
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["attachments"][OWNER].update(launcher_pid=self.pid + 1000, creation_identity="other-live")
        path.write_text(json.dumps(doc), encoding="utf-8")
        with patch("ciel_runtime_support.work_parallel_attachments._identity_now",
                   side_effect=lambda pid: "other-live" if pid == self.pid + 1000 else self.identity):
            with self.assertRaisesRegex(WorkParallelError, "Another live Work host"):
                self.absent(replacement, name)
        self.assertEqual("owner_live", self.errors()[0]["code"])

    def test_unknown_owner_is_refused_with_the_ruled_text(self):
        self.attach(CALLER)
        _, replacement, name = self.observed_attempt(owner=UNKNOWN)  # no OWNER attachment
        with self.assertRaisesRegex(WorkParallelError, UNKNOWN_TEXT):
            self.absent(replacement, name)
        self.assertEqual("owner_unknown", self.errors()[0]["code"])

    def test_manual_resolution_is_allowed_for_unknown_but_refused_for_other_live(self):
        self.attach(CALLER)
        first, replacement, name = self.observed_attempt(owner=UNKNOWN)
        ref = self.reviews()[first]["launch"]["launch_ref"]
        self.call("event", {"internal": True, "user_confirmed": True}, event="rework_launch_manual_request",
                  lease_id=first, launch_ref=ref)
        ref = self.reviews()[first]["launch"]["launch_ref"]
        self.assertEqual("spawn_denied", self.absent(replacement, name, event="rework_launch_manual_resolve",
                                                      launch_ref=ref)["state"])

    def test_host_status_is_host_only_and_classifies_lease_owner(self):
        self.attach(CALLER)
        reserve = self.call("event", {"fork": True, "launch_nonce": CALLER}, event="reserve",
                            item_id=self.board.item, priority="P1")
        host = self.status(CALLER)
        self.assertEqual("own", host["leases"][reserve["lease_id"]]["owner_process"])
        # Every host attached in this test (the suite's own nonces plus CALLER)
        # runs under this live test launcher.
        attached = len(json.loads(self.side.path.read_text(encoding="utf-8"))["attachments"])
        self.assertEqual((attached, 0, 0), tuple(host["session_attachments"][key]
                                                 for key in ("live", "gone", "unknown")))
        # The model's own tool call carries no host token.
        with patch.object(m, "ledger", return_value=self.store):
            model = json.loads(m.run("work_parallel_status", {}, self.board, SCOPE, self.config, None))
        self.assertNotIn("session_attachments", model)
        self.assertNotIn("owner_process", json.dumps(model))


class RestartAndCompleteTests(_Fixture):
    def completed_rework(self, owner=OWNER):
        first, replacement, _ = self.rework()
        token, name = self.launch(replacement, 1, "agent-owner", nonce=owner)
        self.bind(replacement, "agent-owner", name)
        self.report(replacement, token, "c" * 64, "done")
        lease = self.lease(replacement)
        run = m.coordination(self.store).read()["runs"][replacement]
        return first, replacement, lease, run["exit"]["digest"]

    def restart(self, replacement, receipt, caller=CALLER):
        return self.call("event", {"internal": True, "launch_nonce": caller,
                                   "absent_agent_id": "agent-owner", "absent_from_complete_list": True},
                         event="restart_submitted_terminal", lease_id=replacement, generation=2,
                         receipt_digest=receipt)

    def test_restart_of_an_unknown_owner_keeps_the_pre_b2_behaviour(self):
        """Lead ruling (D): unknown = no attach record (legacy, pre-deploy, unattached).

        Refusing it left a submitted result with no resolving path (the orphan
        preview skips slot-false leases; continue-ctl's measurement), so the
        restart observation publishes the review exactly as before B2.
        """
        self.attach(CALLER)
        _, replacement, _, receipt = self.completed_rework(owner=UNKNOWN)
        result = self.restart(replacement, receipt)
        self.assertEqual((True, True), (result["observed_terminal"], result["review_pending"]))
        self.assertEqual([], self.errors())
        pending = self.call("status", {"internal": True, "launch_nonce": CALLER})["review"]["pending"]
        self.assertIn(replacement, [row["lease_id"] for row in pending])

    def test_restart_of_another_live_owner_is_refused_without_an_orphan_pointer(self):
        self.attach(CALLER)
        self.attach(OWNER)
        _, replacement, _, receipt = self.completed_rework()
        doc = json.loads(self.side.path.read_text(encoding="utf-8"))
        doc["attachments"][OWNER].update(launcher_pid=self.pid + 1000, creation_identity="other-live")
        self.side.path.write_text(json.dumps(doc), encoding="utf-8")
        with patch("ciel_runtime_support.work_parallel_attachments._identity_now",
                   side_effect=lambda pid: "other-live" if pid == self.pid + 1000 else self.identity):
            with self.assertRaises(WorkParallelError) as raised:
                self.restart(replacement, receipt)
        self.assertEqual(LIVE_TEXT, str(raised.exception))
        self.assertNotIn("orphan", str(raised.exception))
        self.assertEqual("owner_live", self.errors()[0]["code"])
        self.assertNotIn(replacement, self.reviews())

    def test_restart_earlier_child_is_allowed(self):
        self.attach(OWNER)
        self.attach(CALLER)
        _, replacement, _, receipt = self.completed_rework()
        result = self.restart(replacement, receipt)
        self.assertTrue(result["observed_terminal"])

    def test_complete_list_does_not_mark_an_unknown_owner_missing(self):
        # A bound ordinary worker with no attachment-backed owner: complete is
        # downgraded false, so a prior native record keeps missing_since None.
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        lease_id = reserve["lease_id"]
        self.call("event", {"host_id": "agent-u"}, event="bind", lease_id=lease_id)
        self.call("event", {}, event="native_observation", rows=[{
            "lease_id": lease_id, "agent_id": "agent-u", "status": "running"}], complete=False,
            expected_evidence_version=0)
        self.call("event", {"launch_nonce": CALLER}, event="native_observation", rows=[], complete=True,
                  expected_evidence_version=m.native_evidence(self.store).read()["version"])
        self.assertIsNone(m.native_evidence(self.store).read()["records"][lease_id]["missing_since"])


class MixedVersionTests(_Fixture):
    """A real 57dbfb3 process on the same files (git-archive extract, separate process)."""
    beta_process = rw.ReworkFixture.beta_process
    assert_beta_reads_every_store = rw.ReworkFixture.assert_beta_reads_every_store

    def test_57dbfb3_reads_every_store_this_build_wrote_with_attachments(self):
        self.attach(CALLER)
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-owner", nonce=CALLER)
        self.status(CALLER)
        self.assertTrue(self.side.path.exists())
        self.assert_beta_reads_every_store()

    def test_measure_57dbfb3_absence_rule_is_not_fenced_by_new_code(self):
        """Residual (lead ruling 4): a 57dbfb3 router runs its own old absence rule.

        This build's owner record says another live host owns the attempt; a
        57dbfb3 process on the same files still resolves it absent. Recorded as
        a mixed-version limitation, not guarded further.
        """
        self.attach(CALLER)
        self.attach(OWNER)
        first, replacement, name = None, None, None
        first, replacement, _ = self.rework()
        _, name = self.launch(replacement, 1, "agent-owner", nonce=OWNER)
        doc = json.loads(self.side.path.read_text(encoding="utf-8"))
        doc["attachments"][OWNER].update(launcher_pid=self.pid + 1000, creation_identity="other-live")
        self.side.path.write_text(json.dumps(doc), encoding="utf-8")
        with patch("ciel_runtime_support.work_parallel_attachments._identity_now",
                   side_effect=lambda pid: "other-live" if pid == self.pid + 1000 else self.identity):
            with self.assertRaisesRegex(WorkParallelError, "Another live Work host"):
                self.call("event", {"internal": True, "launch_nonce": CALLER, "absent_dispatch_name": name,
                                    "absent_agent_id": "agent-owner"},
                          event="rework_launch_absent", lease_id=replacement, dispatch_name=name)
        [beta] = self.beta_process(("work_parallel_event", {
            "event": "rework_launch_absent", "lease_id": replacement, "dispatch_name": name,
            "host": {"internal": True, "launch_nonce": "beta-process", "absent_dispatch_name": name,
                     "absent_agent_id": "agent-owner"}}))
        print("\n[R2-B2 mixed] 57dbfb3 rework_launch_absent on an other-live owner:", json.dumps(beta)[:300])
        self.assertEqual("spawn_denied", (beta.get("ok") or {}).get("state"), beta)


if __name__ == "__main__":
    unittest.main()
