"""Discard every failed side receipt (user request 2026-11-18).

One operation behind every surface: /work-mode discard-failed (router tool
work_side_discard_failed), the launcher menu, the web console, REST
POST /ca/work/api/v1/side/discard-failed (= commands/side-discard-failed) and
ciel-runtimectl work side-discard-failed.
"""
import argparse
import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import runtime_paths
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, discard_failed, discard_failed_workspace
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool

A = "claude:aaaaaaaa-1111-4111-8111-111111111111"
HOST = {"token": "x" * 40}


class DiscardFailedTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.gettempdir()).resolve()
        self.assertTrue(str(Path(runtime_paths.CONFIG_DIR).resolve()).lower().startswith(str(root).lower()),
                        "not an isolated profile")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = SideIntakeInbox(self.root, A)
        self.now = time.time()
        self.config = {"work_mode": True, "work_side_intake": {"enabled": True},
                       "work_parallel_host": {"token": "x" * 40}}

    def row(self, key, *, failure=None, lease=None, stage=None):
        receipt = self.inbox.receive("event " + key, key)["receipt_id"]
        current = self.inbox._load(receipt)
        if failure is not None:
            current["side_failure"] = {"category": "source_context", "reason": "Source thread exceeds page limit",
                                       "attempt_count": 2, "failed_at": self.now - 60, "retry_not_before": 0,
                                       "quarantined": False, "retryable": True, **failure}
        if lease is not None:
            current["processing"] = {"owner": "actor", "generation": "g" * 32, "lease_until": lease,
                                     "started_at": self.now - 5}
        self.inbox._save_file(self.inbox._event_path("pending", receipt), current)
        return receipt

    def fixture(self):
        return {"failed": self.row("failed", failure={}),
                "other_category": self.row("other", failure={"category": "invalid_answer"}),
                "leased": self.row("leased", failure={}, lease=self.now + 120),
                "terminal": self.row("terminal", failure={"quarantined": True, "retryable": False}),
                "fresh": self.row("fresh")}

    def test_failed_rows_become_operator_discarded_and_leased_rows_are_untouched(self):
        ids = self.fixture()
        leased_before = self.inbox._load(ids["leased"])
        result = discard_failed(self.root, A, now=self.now)
        self.assertEqual((2, 1, 1), (result["discarded"], result["skipped_leased"], result["already_terminal"]))
        self.assertEqual(sorted([ids["failed"], ids["other_category"]]), sorted(result["receipt_ids"]))
        for key in (ids["failed"], ids["other_category"]):
            failure = self.inbox._load(key)["side_failure"]
            self.assertEqual((True, False, True, self.now), (failure["quarantined"], failure["retryable"],
                                                              failure["operator_discarded"], failure["operator_discarded_at"]))
            self.assertTrue(failure["reason"].endswith("discarded by operator"))
            # Not deleted: the receipt stays pending-stage for audit, quarantined.
            self.assertEqual("received", self.inbox._load(key)["stage"])
        self.assertEqual(leased_before, self.inbox._load(ids["leased"]))
        self.assertNotIn("side_failure", self.inbox._load(ids["fresh"]))
        # Nothing discarded is forwarded to main (the legacy backfill included).
        from ciel_runtime_support.side_forward import offered
        for _ in range(3):
            offered_ids = {row["receipt_id"] for row in offered(self.inbox, limit=50)}
            self.assertFalse(offered_ids & set(result["receipt_ids"]), offered_ids)
        self.assertNotIn("main_forward", self.inbox._load(ids["failed"]))
        # The receipt views say so: the host pane's receipt and the public (web) summary.
        summary = next(row for row in self.inbox.summaries(20) if row["receipt_id"] == ids["failed"])
        self.assertIs(True, summary["side_failure"]["operator_discarded"])
        public, _ = SideIntakeInbox._public_summary_parts(ids["failed"], self.inbox._load(ids["failed"]))
        self.assertIs(True, public["operator_discarded"])

    def test_a_later_claim_never_picks_a_discarded_row(self):
        ids = self.fixture()
        discard_failed(self.root, A, now=self.now)
        claimed = []
        while (row := claim(self.root, A, "actor", now=self.now + 1)) is not None:
            claimed.append(row["receipt_id"])
        self.assertEqual([ids["fresh"]], claimed)

    def test_a_category_limits_it(self):
        ids = self.fixture()
        result = discard_failed(self.root, A, category="invalid_answer", now=self.now)
        # The leased row is another category too: categories are checked first.
        self.assertEqual((1, 2), (result["discarded"], result["other_category"]))
        self.assertTrue(self.inbox._load(ids["other_category"])["side_failure"]["operator_discarded"])
        self.assertNotIn("operator_discarded", self.inbox._load(ids["failed"])["side_failure"])

    def test_the_router_tool_is_host_only_and_works_with_side_intake_off(self):
        self.fixture()
        self.config["work_side_intake"]["enabled"] = False

        def call(host):
            return dispatch_work_tool(1, "work_side_discard_failed", {"session_id": A, **({"_host": host} if host else {})},
                                      load_config=lambda: self.config, state_dir=self.root,
                                      respond=lambda _id, text, error=False: {"text": text, "error": error})
        self.assertTrue(call(None)["error"])
        self.assertTrue(call({"token": "y" * 40})["error"])
        done = call(HOST)
        self.assertFalse(done["error"], done)
        self.assertEqual(2, json.loads(done["text"])["discarded"])

    def test_rest_and_ctl_give_the_same_result(self):
        from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
        reply = {}

        def write_json(_handler, payload, status=200):
            reply.clear()
            reply.update(status=status, payload=payload)
        controller = WorkHttpController(WorkHttpServices(self.root, lambda: self.config, lambda: A,
                                                         lambda _identity: {}, write_json, "test"))
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        from ciel_runtime_support import work_plan_tree as tree
        store = session_work_plan_store(self.root, A)
        store.transaction(lambda doc: tree.add_subtree(doc, None, {"title": "fixture"}),
                          expected_version=store.read_snapshot()["version"])
        self.fixture()
        path = "/ca/work/api/v1/side/discard-failed"
        handler = type("Handler", (), {"headers": {}, "path": path})()
        controller.post(handler, path, {"session_id": A})
        self.assertEqual(422, reply["status"], reply)  # confirmation required, nothing changed
        self.assertEqual(0, discard_failed(self.root, A, category="none_such")["discarded"])
        controller.post(handler, path, {"session_id": A, "confirmed": True})
        self.assertEqual(200, reply["status"], reply)
        rest = reply["payload"]["result"]
        self.assertEqual((2, 1, 1), (rest["discarded"], rest["skipped_leased"], rest["already_terminal"]))
        # ctl on a second workspace with the same rows: the same counts.
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.root, self.inbox = Path(other.name), SideIntakeInbox(Path(other.name), A)
        store = session_work_plan_store(self.root, A)
        store.transaction(lambda doc: tree.add_subtree(doc, None, {"title": "fixture"}),
                          expected_version=store.read_snapshot()["version"])
        self.fixture()
        from ciel_runtime_support.work_settings_cli import command
        output, errors = [], io.StringIO()

        def run(confirm):
            args = argparse.Namespace(action="side-discard-failed", key=None, value=None, json=True, session=A,
                                      confirm=confirm, expected_version=None, expected_finalization_version=None,
                                      preview_digest=None, attempt_ref=None, expected_settings_fingerprint=None,
                                      revision=None, clear_wait=[], activate=False, operation_id=None)
            with redirect_stderr(errors), patch("ciel_runtime_support.work_ctl.state_dir", return_value=self.root):
                try:
                    command(lambda: self.config, lambda _config: None, output.append)(args)
                    return 0
                except SystemExit as stop:
                    return stop.code
        self.assertEqual(3, run(False))
        self.assertEqual(0, discard_failed(self.root, A, category="none_such")["discarded"])
        self.assertEqual(0, run(True), output)
        ctl = json.loads(output[-1])
        ctl = ctl.get("result", ctl)
        self.assertEqual((rest["discarded"], rest["skipped_leased"], rest["already_terminal"]),
                         (ctl["discarded"], ctl["skipped_leased"], ctl["already_terminal"]))

    def test_ctl_side_noun_runs_the_same_work_action(self):
        # ciel-runtimectl side discard-failed [CATEGORY] == work side-discard-failed.
        from unittest.mock import MagicMock
        from ciel_runtime_support.cli_parser import build_cli_parser
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.work_plan_store import session_work_plan_store
        from ciel_runtime_support.work_settings_cli import command
        store = session_work_plan_store(self.root, A)
        store.transaction(lambda doc: tree.add_subtree(doc, None, {"title": "fixture"}),
                          expected_version=store.read_snapshot()["version"])
        ids = self.fixture()
        output = []
        services = MagicMock()
        services.work = command(lambda: self.config, lambda _config: None, output.append)
        parser = build_cli_parser(services)
        args = parser.parse_args(["side", "discard-failed", "invalid_answer", "--session", A, "--confirm", "--json"])
        with patch("ciel_runtime_support.work_ctl.state_dir", return_value=self.root):
            args.func(args)
        result = json.loads(output[-1])
        result = result.get("result", result)
        self.assertEqual((1, "invalid_answer"), (result["discarded"], result["category"]))
        self.assertTrue(self.inbox._load(ids["other_category"])["side_failure"]["operator_discarded"])

    def test_the_menu_discards_across_the_workspace(self):
        self.fixture()
        other = SideIntakeInbox(self.root, "claude:bbbbbbbb-2222-4222-8222-222222222222")
        receipt = other.receive("event b", "b")["receipt_id"]
        row = other._load(receipt)
        row["side_failure"] = {"category": "turn_error", "attempt_count": 1, "failed_at": 0, "retry_not_before": 0,
                               "quarantined": False, "retryable": True}
        other._save_file(other._event_path("pending", receipt), row)
        from ciel_runtime_support.work_settings_menu import discard_failed_rows, panel_rows, run_discard_failed
        rows, values = panel_rows({"language": "ko"}, side=True)
        self.assertIn("__discard_failed__", values)
        self.assertEqual(["__confirm_discard__", "back"], discard_failed_rows({})[1])
        text, result = run_discard_failed({"language": "en"}, self.root)
        self.assertEqual((3, 2, 1), (result["discarded"], result["sessions"], result["skipped_leased"]))
        self.assertIn("Discarded 3 failed side receipt(s) in 2 session(s)", text)
        self.assertEqual(0, discard_failed_workspace(self.root)["discarded"])


if __name__ == "__main__":
    unittest.main()
