"""Legacy reviewer rows in the fork projection: labelled, never current work.

A reviewer lease minted by 4eebfdc/5c91ea8 is read-only and draining. It is
shown in its own labelled group (never "validating", which reads as current
work), with its state and slot. Its agent id never reaches a model-facing
Work payload: main has no use for it. The authenticated host read keeps it (the
revival gate attributes sends by it), and the operator web view shows it.
"""
import json
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool

from test_work_parallel_legacy_reviewer import SCOPE, LegacyReviewerFixture

LABEL = "Legacy reviewer (57dbfb3, read-only, draining)"


class LegacyReviewerForkGroupTests(LegacyReviewerFixture):
    def seeded(self):
        lease, _ = self.reviewing_worker()
        self.legacy_reviewer("legacy-reviewer", host_id="LEGACY-REVIEWER-AGENT")
        return lease

    def model_status(self):
        """What main reads: the router's work_parallel_status with no host token."""
        config = {"work_mode": True, **self.config}
        with patch.object(m, "ledger", return_value=self.store):
            response = dispatch_work_tool(1, "work_parallel_status", {"session_id": SCOPE},
                                          load_config=lambda: dict(config), state_dir=self.state,
                                          respond=lambda rid, text, error=False: {"text": text, "error": error},
                                          save_config=lambda value: None)
        self.assertFalse(response["error"], response)
        return json.loads(response["text"])

    def test_legacy_reviewer_is_its_own_labelled_group_with_state_and_slot(self):
        lease = self.seeded()
        forks = self.call("status", {"internal": True})["forks"]
        self.assertEqual(LABEL, forks["group_labels"]["legacy_reviewer"])
        self.assertEqual([("legacy-reviewer", "submitted", False)],
                         [(row["lease_id"], row["state"], row["slot"]) for row in forks["legacy_reviewer"]])
        self.assertEqual(1, forks["counts"]["legacy_reviewer"])
        self.assertNotIn("legacy-reviewer", [row["lease_id"] for group in m.EVIDENCE_GROUPS
                                             if group != "legacy_reviewer" for row in forks[group]])
        # The worker it reviewed keeps its ordinary evidence group.
        self.assertIn(lease, [row["lease_id"] for group in m.EVIDENCE_GROUPS if group != "legacy_reviewer"
                              for row in forks[group]])

    def test_a_consumed_legacy_reviewer_is_ended_not_draining(self):
        lease = self.seeded()
        self.accept(lease)
        forks = self.call("status", {"internal": True})["forks"]
        self.assertEqual([], forks["legacy_reviewer"])
        self.assertIn("legacy-reviewer", [row["lease_id"] for row in forks["ended"]])

    def test_model_facing_status_never_carries_the_legacy_reviewer_agent_id(self):
        self.seeded()
        status = self.model_status()
        self.assertNotIn("LEGACY-REVIEWER-AGENT", json.dumps(status))
        # The row itself is not hidden: main still sees it labelled.
        self.assertEqual(["legacy-reviewer"], [row["lease_id"] for row in status["forks"]["legacy_reviewer"]])
        self.assertIn("legacy-reviewer", status["leases"])

    def test_host_read_keeps_the_legacy_reviewer_agent_id_for_attribution(self):
        self.seeded()
        status = self.call("status", {"internal": True})
        self.assertEqual("LEGACY-REVIEWER-AGENT", status["leases"]["legacy-reviewer"]["host_id"])
        self.assertEqual("LEGACY-REVIEWER-AGENT", status["forks"]["legacy_reviewer"][0]["agent_id"])

    def test_model_facing_event_and_configure_replies_never_carry_it(self):
        lease = self.seeded()
        configured = self.call("configure", {}, changes={"launches_per_minute": 5})
        self.assertNotIn("LEGACY-REVIEWER-AGENT", json.dumps(configured))
        cleaned = self.call("event", {}, event="reconcile", lease_id=lease)
        self.assertNotIn("LEGACY-REVIEWER-AGENT", json.dumps(cleaned))


class LegacyReviewerWebGroupTests(LegacyReviewerFixture):
    """The operator view keeps the row, labelled, and the live refresh sees it."""

    def controller(self):
        from ciel_runtime_support.http_response import HttpResponseAdapter
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        return WorkHttpController(WorkHttpServices(
            self.state, lambda: {"work_mode": True, **self.config}, lambda: SCOPE,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board),
            parallel_store=lambda: self.store))

    def test_operator_view_lists_the_group_and_live_refresh_sees_it(self):
        from test_work_http_parallel_gaps import serve
        controller = self.controller()
        request = serve(self, controller)
        self.reviewing_worker()
        before, _ = controller._parallel_fingerprint(SCOPE, self.board.read())
        self.legacy_reviewer("legacy-reviewer", host_id="LEGACY-REVIEWER-AGENT")
        status, body = request("/ca/work/api/v1/parallel")
        self.assertEqual(200, status, body)
        self.assertEqual(LABEL, body["forks"]["group_labels"]["legacy_reviewer"])
        self.assertEqual([("legacy-reviewer", "submitted", False)],
                         [(row["lease_id"], row["state"], row["slot"]) for row in body["forks"]["legacy_reviewer"]])
        after, _ = controller._parallel_fingerprint(SCOPE, self.board.read())
        self.assertNotEqual(before, after)
