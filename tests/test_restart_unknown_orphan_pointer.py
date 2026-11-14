"""A restart of a submitted lease whose owner is unknown resolves, and ctl can decide it.

History: under B2 the router refused this restart ("owner unknown ... Use the
existing operator orphan release"), and that pointer led nowhere. The lease is
`submitted` with `slot: false`, the orphan preview skips slot-false leases, and
release_previewed never releases unknown owners (measured 2026-11-13, journal
ctl-work-sse-parity step 39). Lead ruling D restored the pre-B2 behaviour for
unknown owners and kept refusing other_live.

This reuses backend's fixture (helpers only, no inherited tests). After the
restart, the review is published as pending, `ciel-runtimectl work reviews`
lists it through the in-process controller, and a ctl review decision is
accepted on that path. A live other owner is still refused with the owner_live
text alone.
"""
import argparse
import io
import json
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelError

import test_work_parallel_cross_process_ownership as owner_tests

LIVE_TEXT = "Another live Work host owns this launch attempt; waiting for that host."


def ns(action, key=None, value=None, **extra):
    base = dict(action=action, key=key, value=value, json=True, session=None, confirm=False,
                expected_version=None, expected_finalization_version=None, preview_digest=None, attempt_ref=None,
                expected_settings_fingerprint=None, expected_review_pin=None,
                revision=None, clear_wait=[], activate=False, operation_id=None)
    base.update(extra)
    return argparse.Namespace(**base)


class RestartUnknownResolves(owner_tests._Fixture):
    completed_rework = owner_tests.RestartAndCompleteTests.completed_rework
    restart = owner_tests.RestartAndCompleteTests.restart

    def ctl(self, args):
        """The real ctl entry, bound to this fixture's board, ledger and state."""
        from ciel_runtime_support.work_settings_cli import command
        output = []
        state = self.board.store.path.parent
        with patch("ciel_runtime_support.work_ctl.state_dir", return_value=state), \
                patch.object(m, "ledger", return_value=self.store), \
                patch("ciel_runtime_support.work_ctl.WorkCtl.require_session", side_effect=lambda session: session), \
                patch("ciel_runtime_support.work_http_controller.session_work_plan_store",
                      side_effect=lambda _root, _identity: self.board.store), redirect_stderr(io.StringIO()):
            try:
                command(lambda: {"work_mode": True}, lambda _config: None, output.append)(args)
                code = 0
            except SystemExit as stop:
                code = stop.code
        return code, json.loads(output[-1])

    def test_unknown_owner_restart_publishes_a_review_ctl_lists_and_decides(self):
        self.attach(owner_tests.CALLER)
        _, replacement, _, receipt = self.completed_rework(owner=owner_tests.UNKNOWN)
        result = self.restart(replacement, receipt)
        self.assertEqual((True, True), (result["observed_terminal"], result["review_pending"]), result)
        self.assertEqual([], [row for row in self.errors() if row["code"] in ("owner_unknown", "owner_live")])
        review = m.finalization(self.store).read()["reviews"][replacement]
        self.assertIsNone(review["decision"])                          # pending, decidable
        code, listed = self.ctl(ns("reviews", session=owner_tests.SCOPE))
        self.assertEqual(0, code, listed)
        row = next(row for row in listed["parallel_reviews"] if row["lease_id"] == replacement)
        self.assertTrue(row.get("review_pin", "").startswith("rp1:"), row)
        code, decided = self.ctl(ns("review-rework", replacement, "fix the regression", session=owner_tests.SCOPE))
        self.assertEqual(0, code, decided)
        self.assertEqual(("rework", replacement), (decided["result"]["decision"], decided["result"]["lease_id"]))
        self.assertEqual(row["review_pin"], decided["request"]["expected_review_pin"])
        self.assertEqual("rework", m.finalization(self.store).read()["reviews"][replacement]["decision"])

    def test_other_live_owner_is_still_refused_with_its_own_text_only(self):
        self.attach(owner_tests.OWNER)          # a different live host owns the attempt
        _, replacement, _, receipt = self.completed_rework(owner=owner_tests.OWNER)
        with patch.object(m, "_restart_owner_class", return_value="other_live"):
            with self.assertRaises(WorkParallelError) as refused:
                self.restart(replacement, receipt)
        self.assertEqual(LIVE_TEXT, str(refused.exception))
        self.assertNotIn("orphan", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
