"""A superseded stranded result stays visible after its superseding lease ends.

Reproduced from the store a real 57dbfb3 console left on 2026-11-13
(run-text-unpaused-no_report-20261112-213147): beta redispatched the item
(lease 454ab672), the new build recorded the stranded row superseded, and every
beta fork then ended unreported. The ended list must still show the row,
bounded, with the superseding lease's current state; main's pending review
queue (wake, Stop gate) never contains it.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.http_response import HttpResponseAdapter
from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import WorkPlanStore

STORE = (Path(__file__).resolve().parents[1] / "docs/journal/2026/11/12/features/work-parallel-open-items/evidence"
         / "stores/no-report-superseded-213147")
SCOPE_FILE = "work-sessions"
STRANDED = "457849a137ec4dd39680bbb1779b2e87"
WINNER = "454ab67283c04dd680b650f9a0d68441"


class SupersededVisibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config_dir = Path(temporary.name) / "ciel"
        shutil.copytree(STORE, self.config_dir)
        runtime_patch = patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.config_dir)
        runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        self.store = WorkParallelStore(self.config_dir / "work-parallel.json")
        side = json.loads((self.config_dir / "work-parallel-stranded-v1.json").read_text(encoding="utf-8"))
        self.scope = side["rows"][STRANDED]["scope"]
        plan = next((self.config_dir / SCOPE_FILE).glob("*/work-plan.json"))
        self.board = WorkPlanStore(plan).read()

    def view(self):
        controller = WorkHttpController(WorkHttpServices(
            self.config_dir / "state", lambda: {"work_mode": True}, lambda: self.scope,
            lambda identity: {"session_id": identity, "last_observed": None},
            HttpResponseAdapter.write_json, "test-instance",
            parallel_status=lambda identity, board: m.project(self.store, identity, board, host_view=True),
            parallel_store=lambda: self.store))
        return controller._public_parallel(self.scope, self.board)

    def test_saved_store_is_the_measured_end_state(self):
        side = json.loads((self.config_dir / "work-parallel-stranded-v1.json").read_text(encoding="utf-8"))
        row = side["rows"][STRANDED]
        self.assertEqual(("superseded", WINNER), (row["state"], row["superseded"]["lease_id"]))
        self.assertEqual("unreported", self.store.read()["leases"][WINNER]["state"])

    def test_ended_list_shows_the_row_after_the_superseding_lease_ended(self):
        view = self.view()
        self.assertEqual([], [row["lease_id"] for row in view["parallel_reviews"]])
        ended = view["parallel_reviews_ended"]
        self.assertEqual([STRANDED], [row["lease_id"] for row in ended])
        row = ended[0]
        self.assertEqual(("no_report", "superseded", WINNER, "unreported"),
                         (row["kind"], row["phase"], row["superseding_lease_id"], row["superseding_state"]))
        self.assertIsInstance(row["observed_at"], float)

    def test_host_projection_lists_it_as_ended_and_model_projection_has_no_ended_key(self):
        host = m.project(self.store, self.scope, self.board, host_view=True)["review"]
        self.assertEqual([], host["pending"])
        self.assertEqual([(STRANDED, "superseded", "unreported")],
                         [(row["lease_id"], row["state"], row["stranded"]["superseding_state"])
                          for row in host["ended"]])
        model = m.project(self.store, self.scope, self.board, host_view=False)["review"]
        self.assertEqual([], model["pending"])
        self.assertNotIn("ended", model)
        # Main learns only how many ended rows exist, never which (lead ruling).
        self.assertEqual((1, 1), (model["ended_count"], host["ended_count"]))

    def test_ended_rows_are_bounded_to_the_newest_twenty(self):
        side_path = self.config_dir / "work-parallel-stranded-v1.json"
        side = json.loads(side_path.read_text(encoding="utf-8"))
        template = side["rows"][STRANDED]
        for index in range(25):
            ident = f"{index:032x}"
            side["rows"][ident] = {**template, "lease_id": ident, "updated_at": 1_800_000_000.0 + index,
                                   "superseded": {**template["superseded"], "observed_at": 1_800_000_000.0 + index}}
        side_path.write_text(json.dumps(side), encoding="utf-8")
        # Synthetic rows reuse one real row's facts; only the bound is under test.
        with patch("ciel_runtime_support.work_parallel_stranded.validate", lambda state: None):
            review = m.project(self.store, self.scope, self.board, host_view=True)["review"]
        self.assertEqual(20, len(review["ended"]))
        self.assertEqual(f"{24:032x}", review["ended"][0]["lease_id"])
        # The count is the total, not the bounded list's length.
        self.assertEqual(26, review["ended_count"])
        with patch("ciel_runtime_support.work_parallel_stranded.validate", lambda state: None):
            model = m.project(self.store, self.scope, self.board, host_view=False)["review"]
        self.assertEqual((26, False), (model["ended_count"], "ended" in model))


if __name__ == "__main__":
    unittest.main()
