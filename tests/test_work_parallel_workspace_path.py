"""G3: the host status view names each open lease's worktree; the model view never does.

The Work fork gate (ciel-work mod) confines a bound fork's file writes to its
lease worktree. It reads the confinement from the authenticated host status
view: every projected open lease row carries ``workspace_path``, the absolute
worktree path, or ``None`` when the lease has no workspace (the router admits
such a reservation; the gate then treats the lease as unconfined). A recorded
binding that is not an absolute path leaves the key out, which the gate denies.
The model's own work_parallel_status never carries the key. The host view lists
every open lease of the conversation, so a fork beyond the model's 24-row cap
is never reported as unbound.
"""
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import MAX_LEASES

import test_work_parallel_finalization as fin

SCOPE = fin.SCOPE


class _Fixture(unittest.TestCase):
    """The finalization suite's helpers only; none of its test methods (no inheritance)."""
    setUp = fin.MainReviewTests.setUp
    call = fin.MainReviewTests.call

    def model_status(self):
        """The model's own tool call: no host token."""
        with patch.object(m, "ledger", return_value=self.store):
            return json.loads(m.run("work_parallel_status", {}, self.board, SCOPE, self.config, None))

    def host_status(self):
        """The authenticated mod's own read (no worker identity)."""
        return self.call("status", {})

    def seed_open_leases(self, count, workspace=lambda index: None):
        """``count`` reserved P2 leases of this conversation, written as one ledger change."""
        idents = []

        def apply(d):
            for index in range(count):
                ident = uuid.uuid4().hex
                row = dict(id=ident, scope=SCOPE, item_id="seed-%02d" % index, priority="P2",
                           created_at=1_000 + index, role="worker", mode="fork", plan_version=1,
                           state="reserved", host_id=None, slot=True, usage=.05, artifact=None,
                           rework=0, evidence=None, idle_proof=None, cleanup_proof=None,
                           dispatch_name=None, owner=None, report_hash=None, terminal=None)
                binding = workspace(index)
                if binding is not None:
                    row["workspace"] = binding
                d["leases"][ident] = row
                idents.append(ident)
        self.store._change(apply, self.store.read()["version"])
        return idents


class WorkspacePathTests(_Fixture):
    def test_host_view_names_the_absolute_worktree_of_a_reservation(self):
        reserve = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                            event="reserve", item_id=self.board.item, priority="P1")
        row = self.host_status()["leases"][reserve["lease_id"]]
        self.assertEqual(reserve["workspace"]["path"], row["workspace_path"])
        self.assertTrue(Path(row["workspace_path"]).is_absolute())
        self.assertTrue(Path(row["workspace_path"]).is_dir())

    def test_a_reservation_without_a_workspace_is_admitted_and_shown_unconfined(self):
        # Measured: the router admits a parallel reservation with no workspace_repo.
        reserve = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item, priority="P1")
        self.assertIsNone(reserve["workspace"])
        lease = self.store.read()["leases"][reserve["lease_id"]]
        self.assertEqual(("reserved", True), (lease["state"], lease["slot"]))
        self.assertNotIn("workspace", lease)
        row = self.host_status()["leases"][reserve["lease_id"]]
        self.assertIn("workspace_path", row)
        self.assertIsNone(row["workspace_path"])

    def test_the_model_view_and_the_browser_view_never_carry_the_key(self):
        confined = self.call("event", {"fork": True, "workspace_repo": str(self.repo)},
                             event="reserve", item_id=self.board.item, priority="P1")
        unconfined = self.call("event", {"fork": True}, event="reserve", item_id=self.board.item2, priority="P1")
        model = self.model_status()
        self.assertEqual({confined["lease_id"], unconfined["lease_id"]}, set(model["leases"]))
        for row in model["leases"].values():
            self.assertNotIn("workspace_path", row)
        self.assertNotIn("workspace_path", json.dumps(model))
        browser = m.public_projection(m.project(self.store, SCOPE, self.board, host_view=True), SCOPE)
        self.assertNotIn("workspace_path", json.dumps(browser))

    def test_a_binding_that_is_not_an_absolute_path_leaves_the_key_out(self):
        [relative, absolute] = self.seed_open_leases(2, workspace=lambda index: {
            "path": ["relative/worktree", str(self.tmp / "abs-worktree")][index], "repo": str(self.repo)})
        leases = self.host_status()["leases"]
        self.assertNotIn("workspace_path", leases[relative])
        self.assertEqual(str(self.tmp / "abs-worktree"), leases[absolute]["workspace_path"])

    def test_the_host_view_lists_every_open_lease_beyond_the_model_cap(self):
        count = m.MODEL_LEASE_LIMIT + 6
        self.assertLessEqual(count, MAX_LEASES)
        idents = self.seed_open_leases(count, workspace=lambda index: None if index % 2 else {
            "path": str(self.tmp / ("wt-%02d" % index)), "repo": str(self.repo)})
        host = self.host_status()
        self.assertEqual(set(idents), set(host["leases"]))
        self.assertFalse(host["leases_truncated"])
        for index, ident in enumerate(idents):
            expected = None if index % 2 else str(self.tmp / ("wt-%02d" % index))
            self.assertEqual(expected, host["leases"][ident]["workspace_path"])
        model = self.model_status()
        self.assertEqual(m.MODEL_LEASE_LIMIT, len(model["leases"]))
        self.assertTrue(model["leases_truncated"])


if __name__ == "__main__":
    unittest.main()
