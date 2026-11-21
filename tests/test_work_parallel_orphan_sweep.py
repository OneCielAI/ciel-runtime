import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ciel_runtime_support import work_parallel_orphan_sweep as sweep_module
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_parallel_mcp import _orphan_preview


def candidate(name, priority="P1"):
    return dict(item_id=name, priority=priority, created_at=10, approved=True, focused=True,
                dependencies_done=True, plan_version=7, usage=.05, role="worker", mode="fork")


class OrphanSweepTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = WorkParallelStore(self.root / "work-parallel.json")
        self.store.configure({"paused": False}, capability_ready=True, expected_version=0)
        self.store.configure({"priority_limits": {"P1": 8, "P2": 8, "P3": 8, "P4": 8, "P5": 8}},
                             capability_ready=True, expected_version=self.store.read()["version"])

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self, name, scope, owner=None, bind=True):
        self.now = getattr(self, "now", 100) + 61  # under the launch rate limit
        d, ident = self.store.reserve([candidate(name)], scope=scope, now=self.now, dispatch_name="work-" + name,
                                      expected_version=self.store.read()["version"],
                                      **({"owner": owner} if owner else {}))
        if bind:
            self.store.bind(ident, "host-" + name, agent_name="work-" + name,
                            expected_version=self.store.read()["version"])
        return ident

    def router(self, name, runtime, session, pid):
        instance = self.root / "router-instances" / name
        instance.mkdir(parents=True)
        (instance / "compaction-scope.json").write_text(json.dumps({"runtime": runtime, "session_id": session}),
                                                         encoding="utf-8")
        (instance / "router.pid").write_text(str(pid), encoding="utf-8")

    def test_releases_dead_owners_and_legacy_leases_of_conversations_no_router_serves(self):
        identities = {11: "", 12: "birth-12", 13: None, 21: "router-live", 22: ""}
        identity = identities.get
        dead = self.reserve("dead", "claude:a", owner={"session_pid": 11, "identity": "birth-11"})
        live = self.reserve("live", "claude:a", owner={"session_pid": 12, "identity": "birth-12"})
        unknown = self.reserve("unknown", "claude:a", owner={"session_pid": 13, "identity": "birth-13"})
        legacy_gone = self.reserve("legacy-gone", "claude:ended")
        legacy_unbound = self.reserve("legacy-unbound", "claude:ended", bind=False)
        legacy_served = self.reserve("legacy-served", "claude:running")
        legacy_dead_router = self.reserve("legacy-dead-router", "claude:stopped")
        self.router("9505-a", "claude", "running", 21)
        self.router("9506-b", "claude", "stopped", 22)

        scopes = sweep_module.live_router_scopes(self.root, identity)
        self.assertEqual({"claude:running"}, scopes)
        with patch("ciel_runtime_support.work_parallel_mcp.process_identity", identity):
            result = sweep_module.sweep(self.store, scopes, _orphan_preview)

        released = {row["lease_id"] for row in result["released"]}
        self.assertEqual({dead, legacy_gone, legacy_unbound, legacy_dead_router}, released)
        leases = self.store.read()["leases"]
        for ident in (dead, legacy_gone, legacy_dead_router):
            self.assertEqual(("orphaned", False), (leases[ident]["state"], leases[ident]["slot"]))
        self.assertNotIn(legacy_unbound, leases)  # a never-bound legacy reservation is removed outright
        for ident in (live, unknown, legacy_served):
            self.assertTrue(leases[ident]["slot"], ident)
        self.assertEqual([], result["kept"])

    def test_dead_parked_worker_is_retired_without_completing_the_item(self):
        leases = {}
        for name, pid in (("dead", 11), ("live", 12), ("unknown", 13), ("legacy", None)):
            owner = {"session_pid": pid, "identity": f"birth-{pid}"} if pid else None
            ident = leases[name] = self.reserve(name, "claude:a", owner=owner)
            self.store.report_question(ident, plan_version=7, evidence="Waiting for main answer",
                                       expected_version=self.store.read()["version"])
        with patch("ciel_runtime_support.work_parallel_mcp.process_identity",
                   {11: "", 12: "birth-12", 13: None}.get):
            result = sweep_module.sweep(self.store, {"claude:a"},
                                        lambda store: _orphan_preview(store, include_dead_parked=True))
        self.assertEqual([leases["dead"]], [row["lease_id"] for row in result["released"]])
        rows = self.store.read()["leases"]
        self.assertEqual(("orphaned", False, None), tuple(rows[leases["dead"]][k]
                         for k in ("state", "slot", "artifact")))
        for name in ("live", "unknown", "legacy"):
            self.assertEqual("waiting_input", rows[leases[name]]["state"])

    def test_a_lease_the_ledger_refuses_is_kept_and_the_sweep_continues(self):
        refused = self.reserve("refused", "claude:a", owner={"session_pid": 11, "identity": "birth-11"})
        after = self.reserve("after", "claude:a", owner={"session_pid": 11, "identity": "birth-11"})
        original = self.store.orphan_release

        def orphan_release(ident, **kwargs):
            if ident == refused:
                from ciel_runtime_support.work_parallel import WorkParallelError
                raise WorkParallelError("unexpected artifact")
            return original(ident, **kwargs)

        with patch("ciel_runtime_support.work_parallel_mcp.process_identity", {11: ""}.get), \
                patch.object(self.store, "orphan_release", orphan_release):
            result = sweep_module.sweep(self.store, set(), _orphan_preview)
        self.assertEqual([after], [row["lease_id"] for row in result["released"]])
        self.assertEqual([(refused, "unexpected artifact")], [(row["lease_id"], row["reason"]) for row in result["kept"]])

    def test_launch_phase_never_raises_and_logs_each_release(self):
        lines = []
        with patch.object(sweep_module, "sweep", side_effect=RuntimeError("boom")):
            self.assertEqual({"released": 0, "kept": 0, "failed": 1},
                             sweep_module.sweep_at_launch(lambda level, message: lines.append((level, message))))
        self.assertEqual([("WARN", "work_orphan_sweep_failed error=RuntimeError")], lines)
        lines.clear()
        dead = self.reserve("dead", "claude:a", owner={"session_pid": 11, "identity": "birth-11"})
        with patch("ciel_runtime_support.work_parallel_mcp.ledger", lambda: self.store), \
                patch("ciel_runtime_support.work_parallel_mcp.process_identity", {11: ""}.get), \
                patch("ciel_runtime_support.runtime_paths.CONFIG_DIR", self.root):
            outcome = sweep_module.sweep_at_launch(lambda level, message: lines.append((level, message)))
        self.assertEqual({"released": 1, "kept": 0, "failed": 0}, outcome)
        self.assertEqual([("INFO", f"work_orphan_released lease={dead[:12]} priority=P1 state=running owner=dead")],
                         lines)


if __name__ == "__main__":
    unittest.main()
