"""Durable host-only Work parallel failure-log contract."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelError, WorkParallelStore
from ciel_runtime_support.work_parallel_failures import CODES, WorkParallelFailureLog

SECRET = "failure-log-host-secret-" * 2
SCOPE = "claude:failure-log"


class Board:
    def read(self):
        return {"version": 0, "nodes": [], "phase": "planning", "active_root": None, "focus_ids": []}


class WorkParallelFailureLogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.store = WorkParallelStore(self.tmp / "work-parallel.json")
        self.log = WorkParallelFailureLog.beside(self.store.path)
        self.config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}

    def test_each_fixed_code_is_durable_bounded_and_public_without_scope_or_text(self):
        for index, code in enumerate(CODES):
            result = self.log.record(SCOPE, code, lease_id=f"lease-{index}", dedup_key=f"event-{index}",
                                     now=1000 + index)
            self.assertEqual((code, 1, index + 1),
                             (result["code"], result["count"], result["failures_version"]))
        public = self.log.public(SCOPE)
        self.assertEqual(len(CODES), public["failures_version"])
        self.assertEqual(list(reversed(CODES)), [row["code"] for row in public["failures"]])
        raw = json.dumps(public)
        self.assertNotIn(SCOPE, raw)
        for forbidden in ("path", "text", "reason", "agent_id", "dispatch_name", "token"):
            self.assertNotIn(forbidden, raw)

    def test_dedup_replay_counts_one_row_and_never_exceeds_limits(self):
        first = self.log.record(SCOPE, "revival_refused", lease_id="L1", dedup_key="same", now=1000)
        second = self.log.record(SCOPE, "revival_refused", lease_id="L1", dedup_key="same", now=1001)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(2, second["count"])
        self.assertEqual(1, len(self.log.read()["events"]))
        with self.assertRaisesRegex(WorkParallelError, "different event"):
            self.log.record(SCOPE, "revival_refused", lease_id="L2", dedup_key="same", now=1002)
        for index in range(250):
            self.log.record(SCOPE, "decision_refused_stop", dedup_key=f"row-{index}", now=2000 + index)
        self.assertEqual(200, len(self.log.read()["events"]))
        public = self.log.public(SCOPE)
        self.assertEqual(50, len(public["failures"]))
        self.assertTrue(public["failures_truncated"])

    def test_invalid_fields_and_corrupt_or_other_schema_never_write(self):
        bad = (("invented", "L1", "key"), ("revival_refused", "path/to/lease", "key"),
               ("revival_refused", "L1", "contains space"), ("revival_refused", "L1", "x" * 65))
        for code, lease, key in bad:
            with self.subTest(code=code, lease=lease, key=key):
                before = self.log.path.read_bytes() if self.log.path.exists() else None
                with self.assertRaises(WorkParallelError):
                    self.log.record(SCOPE, code, lease_id=lease, dedup_key=key)
                self.assertEqual(before, self.log.path.read_bytes() if self.log.path.exists() else None)
        self.log.path.write_text('{"producer":"other","schema":1,"version":0,"events":[]}', encoding="utf-8")
        before = self.log.path.read_bytes()
        with self.assertRaisesRegex(WorkParallelError, "another producer"):
            self.log.record(SCOPE, "revival_refused", dedup_key="key")
        self.assertEqual(before, self.log.path.read_bytes())

    def test_only_mod_internal_event_can_write(self):
        def call(host):
            args = {"event": "failure_record", "code": "fork_attribution_stop", "lease_id": "L1",
                    "dedup_key": "stop-1", "expected_version": self.store.read()["version"],
                    "_host": {"token": SECRET, **host}}
            with patch.object(m, "ledger", return_value=self.store):
                return json.loads(m.run("work_parallel_event", args, Board(), SCOPE, self.config, None))
        for host in ({}, {"internal": False}, {"internal": True, "agent_id": "worker"}):
            before = self.log.path.read_bytes() if self.log.path.exists() else None
            with self.subTest(host=host), self.assertRaisesRegex(WorkParallelError, "verified mod-internal"):
                call(host)
            self.assertEqual(before, self.log.path.read_bytes() if self.log.path.exists() else None)
        result = call({"internal": True})
        self.assertEqual(("fork_attribution_stop", 1), (result["code"], result["count"]))

    def test_old_reader_ignores_the_new_sidecar(self):
        self.log.record(SCOPE, "decision_refused_stop", lease_id="L1", dedup_key="refused", now=1000)
        # It is not referenced from any frozen ledger document.
        before = self.store.path.read_bytes() if self.store.path.exists() else None
        self.store.configure({"paused": True}, capability_ready=True,
                             expected_version=self.store.read()["version"])
        self.assertNotEqual(before, self.store.path.read_bytes())
        self.assertEqual(1, len(self.log.read()["events"]))


if __name__ == "__main__":
    unittest.main()
