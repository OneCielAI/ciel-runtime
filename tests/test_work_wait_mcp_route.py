"""The real work_parallel_event entry point routes wait-boost host events."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ciel_runtime_support import work_parallel_mcp as mcp
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_wait_product_adapter import configure_wait_factory

TOKEN = "t" * 40


class Orchestrator:
    def __init__(self):
        self.calls = []

    def cleanup_to_done(self, **kwargs):
        self.calls.append(kwargs)
        return {"state": "done", "lease_id": "secret-lease", "pin": "secret"}


class WaitMcpRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkParallelStore(Path(self.tmp.name) / "work-parallel.json")
        patcher = mock.patch.object(mcp, "ledger", return_value=self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.orchestrator = Orchestrator()
        configure_wait_factory(
            lambda store, board, identity, host: (self.orchestrator, lambda row, reason: None)
        )
        self.addCleanup(configure_wait_factory, None)
        self.config = {"work_parallel_host": {"token": TOKEN, "capability_ready": True}}

    def call(self, host, event="wait_boost_cleanup"):
        args = {"event": event, "wait_args": {"cleanup_key": "c", "now": 1.0}, "_host": host}
        return mcp.run("work_parallel_event", args, None, "claude:scope", self.config, None)

    def test_authenticated_internal_host_routes_and_redacts(self):
        raw = self.call({"token": TOKEN, "internal": True})
        payload = json.loads(raw)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"], {"state": "done"})
        self.assertNotIn("secret", raw)
        self.assertEqual(self.orchestrator.calls, [{"cleanup_key": "c", "now": 1.0}])

    def test_unauthenticated_worker_or_unknown_event_refused(self):
        with self.assertRaises(WorkParallelError):
            self.call({"token": "wrong" * 10, "internal": True})
        with self.assertRaises(WorkParallelError):
            self.call({"token": TOKEN, "internal": True, "agent_id": "worker"})
        with self.assertRaises(WorkParallelError):
            self.call({"token": TOKEN})
        with self.assertRaises(WorkParallelError):
            self.call({"token": TOKEN, "internal": True}, event="wait_boost_unknown")
        self.assertEqual(self.orchestrator.calls, [])

    def test_status_projections_share_wait_boost_block_without_secrets(self):
        from ciel_runtime_support.work_parallel_wait_boost import WaitBoostStore, boost_key
        sys_path = Path(__file__).resolve().parent
        import sys
        if str(sys_path) not in sys.path:
            sys.path.insert(0, str(sys_path))
        from test_wait_boost_sidecar import consuming
        row = consuming()
        row["scope"] = "claude:scope"
        row["lease_id"] = "lease-secret-7f3a"
        WaitBoostStore(self.store.path.with_name("work-parallel-wait-boost-v1.json")).change(
            0, lambda document: document["rows"].update(
                {boost_key("claude:scope", row["item_id"]): row}), now=2.0,
        )
        model = mcp.project(self.store, "claude:scope")
        public = mcp.public_projection(model, "claude:scope")
        self.assertEqual(model["wait_boost"], public["wait_boost"])
        self.assertTrue(model["wait_boost"]["eligible"])
        self.assertEqual(model["wait_boost"]["behavior"], "consuming")
        for secret in (row["attempt_id"], row["lease_id"], row["release_receipt"]):
            self.assertNotIn(secret, json.dumps(public))
        other = mcp.project(self.store, "claude:other")
        self.assertFalse(other["wait_boost"]["eligible"])


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
