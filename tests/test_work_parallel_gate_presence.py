"""G5: the Work fork gate presence the mod observed, shown on the host, REST and web views.

ciel-work probes the ciel-work-gate plugin live before every Work fork spawn
and refuses the spawn itself when the gate does not answer. It reports the
probe's result to the router once per process start and on every change of
(state, gate version). The router keeps that report in a new sidecar
(``work-parallel-gate-presence-v1.json``, which 57dbfb3 never reads) and shows
it as ``fork_gate``. Until B6 a tool child that steals the host token could
forge a report, so the projection is display only: it never authorizes or
refuses a reservation.
"""
import json
import unittest
from unittest.mock import Mock, patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelError

import test_work_parallel_finalization as fin

SCOPE = fin.SCOPE
OTHER = "claude:another-conversation"
NONCE = "0123456789abcdef" * 2
NOT_LOADED = "Work fork gate not loaded: parallel execution disabled"
NOT_REPORTED = "Work fork gate presence not reported by this conversation's Work host"
UNREADABLE = "Work fork gate presence record unreadable; left unchanged"
SIDECAR = "work-parallel-gate-presence-v1.json"
CLOCK = "ciel_runtime_support.work_parallel_gate_presence.time"


class _Fixture(unittest.TestCase):
    """The finalization suite's helpers only; none of its test methods (no inheritance)."""
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call

    def report(self, state, version="1", probe_ref=NONCE, identity=SCOPE, **host):
        args = {"event": "gate_presence", "state": state, "probe_ref": probe_ref,
                "_host": {"token": fin.SECRET, "internal": True, **host}}
        if version is not None:
            args["gate_version"] = version
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_event", args, self.board, identity, self.config, None))

    def host_gate(self, identity=SCOPE):
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", {"_host": {"token": fin.SECRET}}, self.board,
                                    identity, self.config, None))["fork_gate"]

    def model_status(self):
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", {}, self.board, SCOPE, self.config, None))

    def controller(self, *, host_view=True):
        return WorkHttpController(WorkHttpServices(
            self.tmp / "state", lambda: {"work_mode": True}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board, host_view=host_view),
            parallel_store=lambda: self.store))

    def rest(self, *, host_view=True):
        return self.controller(host_view=host_view)._public_parallel(SCOPE, self.board.read())

    def sidecar(self):
        return self.store.path.with_name(SIDECAR)


class GatePresenceTests(_Fixture):
    def test_no_report_is_unknown_with_its_own_text(self):
        expected = {"state": "unknown", "reason": "not_reported", "gate_version": None,
                    "observed_at": None, "reported_at": None, "message": NOT_REPORTED}
        self.assertEqual(expected, self.host_gate())
        self.assertEqual(expected, self.rest()["fork_gate"])
        self.assertFalse(self.sidecar().exists())

    def test_a_loaded_report_shows_loaded_without_a_message(self):
        with patch(CLOCK, Mock(time=Mock(return_value=1_000.5))):
            result = self.report("loaded")
        gate = {"state": "loaded", "reason": None, "gate_version": "1", "observed_at": 1_000.5,
                "reported_at": 1_000.5, "message": None}
        self.assertEqual(gate, result["fork_gate"])
        self.assertEqual(gate, self.host_gate())
        view = self.rest()
        self.assertEqual(gate, view["fork_gate"])
        self.assertEqual(result["gate_presence_version"], view["gate_presence_version"])
        self.assertNotIn(NONCE, json.dumps(view))
        self.assertNotIn(NONCE, json.dumps(self.host_gate()))

    def test_missing_and_mismatch_show_the_ruled_text(self):
        self.report("missing", version=None)
        self.assertEqual(("not_loaded", "missing", None, NOT_LOADED),
                         tuple(self.host_gate()[key] for key in ("state", "reason", "gate_version", "message")))
        self.report("mismatch", version="2")
        gate = self.rest()["fork_gate"]
        self.assertEqual(("not_loaded", "mismatch", "2", NOT_LOADED),
                         tuple(gate[key] for key in ("state", "reason", "gate_version", "message")))

    def test_observed_at_is_since_the_reported_state_began(self):
        for stamp, state, version in ((10.0, "loaded", "1"), (20.0, "loaded", "1"),
                                      (30.0, "mismatch", "2"), (40.0, "loaded", "1")):
            with patch(CLOCK, Mock(time=Mock(return_value=stamp))):
                self.report(state, version=version)
            gate = self.host_gate()
            self.assertEqual(stamp, gate["reported_at"])
        self.assertEqual(40.0, gate["observed_at"])
        with patch(CLOCK, Mock(time=Mock(return_value=50.0))):
            self.report("loaded")
        self.assertEqual((40.0, 50.0), (self.host_gate()["observed_at"], self.host_gate()["reported_at"]))

    def test_only_the_mod_internal_host_may_report(self):
        with self.assertRaisesRegex(WorkParallelError, "mod-internal"):
            with patch.object(m, "ledger", return_value=self.store):
                m.run("work_parallel_event", {"event": "gate_presence", "state": "loaded", "gate_version": "1",
                                              "probe_ref": NONCE, "_host": {"token": fin.SECRET}},
                      self.board, SCOPE, self.config, None)
        with self.assertRaisesRegex(WorkParallelError, "mod-internal"):
            self.report("loaded", agent_id="worker-1")
        with self.assertRaisesRegex(WorkParallelError, "authenticated parallel host required"):
            with patch.object(m, "ledger", return_value=self.store):
                m.run("work_parallel_event", {"event": "gate_presence", "state": "loaded", "gate_version": "1",
                                              "probe_ref": NONCE}, self.board, SCOPE, self.config, None)
        self.assertFalse(self.sidecar().exists())

    def test_malformed_reports_are_refused_and_write_nothing(self):
        cases = [dict(state="present"), dict(state="loaded", version=None), dict(state="mismatch", version=None),
                 dict(state="loaded", version="1 2"), dict(state="loaded", version="v" * 33),
                 dict(state="loaded", probe_ref=NONCE[:-1]), dict(state="loaded", probe_ref=NONCE.upper()),
                 dict(state="loaded", probe_ref=None), dict(state="missing", version=7)]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(WorkParallelError):
                self.report(**case)
        self.assertFalse(self.sidecar().exists())

    def test_reports_are_per_conversation(self):
        self.report("missing", version=None, identity=OTHER)
        self.assertEqual("unknown", self.host_gate()["state"])
        self.report("loaded")
        self.assertEqual(("loaded", "not_loaded"), (self.host_gate()["state"], self.host_gate(OTHER)["state"]))

    def test_the_model_view_has_no_fork_gate(self):
        self.report("missing", version=None)
        self.assertNotIn("fork_gate", self.model_status())

    def test_the_ctl_controller_shows_it_from_a_model_projection_too(self):
        # ciel-runtimectl builds its controller on project() without host_view.
        self.report("mismatch", version="2")
        self.assertEqual(("not_loaded", "mismatch"),
                         tuple(self.rest(host_view=False)["fork_gate"][key] for key in ("state", "reason")))

    def test_a_report_changes_the_parallel_refresh_fingerprint_and_never_the_ledger(self):
        controller = self.controller()
        self.assertIn(self.sidecar(), controller._parallel_paths())
        before, _ = controller._parallel_fingerprint(SCOPE, self.board.read())
        ledger_before = self.store.path.read_bytes() if self.store.path.exists() else None
        self.report("missing", version=None)
        after, _ = controller._parallel_fingerprint(SCOPE, self.board.read())
        self.assertNotEqual(before, after)
        self.assertEqual(ledger_before, self.store.path.read_bytes() if self.store.path.exists() else None)

    def test_an_unreadable_record_is_unknown_and_left_unchanged(self):
        self.sidecar().write_text("{not json", encoding="utf-8")
        expected = {"state": "unknown", "reason": "unreadable", "gate_version": None,
                    "observed_at": None, "reported_at": None, "message": UNREADABLE}
        self.assertEqual(expected, self.host_gate())
        self.assertEqual(expected, self.rest()["fork_gate"])
        with self.assertRaises(WorkParallelError):
            self.report("loaded")
        self.assertEqual("{not json", self.sidecar().read_text(encoding="utf-8"))

    def test_the_record_is_bounded_and_drops_the_oldest_conversation(self):
        for index in range(65):
            with patch(CLOCK, Mock(time=Mock(return_value=100.0 + index))):
                self.report("loaded", identity="claude:conversation-%02d" % index)
        doc = json.loads(self.sidecar().read_text(encoding="utf-8"))
        self.assertEqual(64, len(doc["sessions"]))
        self.assertNotIn("claude:conversation-00", doc["sessions"])
        self.assertEqual("unknown", self.host_gate("claude:conversation-00")["state"])
        self.assertEqual("loaded", self.host_gate("claude:conversation-64")["state"])


if __name__ == "__main__":
    unittest.main()
