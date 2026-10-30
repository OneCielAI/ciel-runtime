"""Only exact native parked handoff shares Work, never latest or arbitrary resume."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ciel_runtime_support.work_plan_store import WorkPlanStore, session_work_plan_store
from ciel_runtime_support.work_plan_tree import add_node, WorkPlanError
from ciel_runtime_support.claude_socket_paths import session_key_hash
from ciel_runtime_support.work_session_lineage import _path


class WorkLineageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="owned-work-lineage-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / "native" / "sessions"
        self.directory.mkdir(parents=True)
        env = patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.directory.parent)})
        env.start()
        self.addCleanup(env.stop)
        identity = patch("ciel_runtime_support.claude_session_handoff._process_identity", side_effect=lambda pid: "start-" + str(pid))
        identity.start()
        self.addCleanup(identity.stop)
        self.parent, self.child = "claude:parent", "claude:12345678-child"
        self.source = session_work_plan_store(self.root, self.parent)
        self.source.transaction(lambda d: add_node(d, None, "owned preserved task"))
        self.record(101, "parent", parkedJobId="12345678")
        self.record(102, "12345678-child", kind="bg", jobId="12345678")

    def record(self, pid, sid, **extra):
        socket = "owned-socket-" + str(pid)
        row = dict(pid=pid, sessionId=sid, cwd=str(self.root), procStart="start-" + str(pid),
                   startedAt=1000 + pid, pidDomain="owned", messagingSocketPath=socket, kind="interactive")
        row.update(extra)
        (self.directory / f"{pid}.json").write_text(json.dumps(row), encoding="utf-8")
        key = dict(peerToken="ab" * 16, procStartFt=row["procStart"], pidDomain=row["pidDomain"])
        (self.directory / f"{pid}.{session_key_hash(socket)}.key").write_text(json.dumps(key), encoding="utf-8")

    def test_exact_handoff_keeps_path_version_and_mutations(self):
        child = session_work_plan_store(self.root, self.child)
        self.assertEqual(self.source.path, child.path)
        self.assertEqual(self.source.read_snapshot(), child.read_snapshot())
        child.transaction(lambda d: add_node(d, None, "continued"), expected_version=1)
        self.assertEqual(2, self.source.read_snapshot()["version"])
        self.assertEqual(2, len(self.source.read_snapshot()["nodes"]))

    def test_mapping_survives_restart_without_native_records(self):
        expected = session_work_plan_store(self.root, self.child).read_snapshot()
        for path in self.directory.iterdir():
            path.unlink()
        from ciel_runtime_support.work_session_lineage import WorkSessionStore
        resumed = WorkSessionStore(self.root, self.child, store_factory=WorkPlanStore, notify=lambda *_: None)
        self.assertEqual(expected, resumed.read_snapshot())

    def test_observers_can_read_and_mutate_same_lineage_without_deadlock(self):
        from ciel_runtime_support.work_plan_store import subscribe_work_plan_changes
        child = session_work_plan_store(self.root, self.child)
        child.read_snapshot()
        observed = []
        def observe(path, before, after):
            observed.append(child.read_snapshot()["version"])
            if after["version"] == 2:
                child.transaction(lambda doc: add_node(doc, None, "observer continuation"))
        unsubscribe = subscribe_work_plan_changes(observe)
        try:
            child.transaction(lambda doc: add_node(doc, None, "second"))
        finally:
            unsubscribe()
        self.assertEqual([2, 3], observed)
        self.assertEqual(3, self.source.read_snapshot()["version"])

    def test_alias_cycle_and_invalid_target_fail_closed(self):
        child = session_work_plan_store(self.root, self.child)
        child.read_snapshot()
        marker = _path(self.root, self.child).with_name("work-lineage.json")
        original = json.loads(marker.read_text())
        marker.write_text(json.dumps(dict(original, canonical="claude:../../escape")))
        with self.assertRaises(WorkPlanError):
            child.read_snapshot()
        marker.write_text(json.dumps(original))
        parent_marker = _path(self.root, self.parent).with_name("work-lineage.json")
        parent_marker.write_text(json.dumps(dict(session=self.parent, canonical=self.child,
                                                authority="native-parked-handoff")))
        with self.assertRaisesRegex(WorkPlanError, "cycle"):
            child.read_snapshot()

    def test_unrelated_missing_store_snapshot_creates_no_files(self):
        root = self.root / "other-workspace"
        store = session_work_plan_store(root, "claude:unrelated")
        self.assertEqual([], store.read_snapshot()["nodes"])
        self.assertFalse(root.exists())

    def test_old_handle_follows_mapping_after_native_records_disappear(self):
        child = session_work_plan_store(self.root, self.child)
        child.read_snapshot()
        for path in self.directory.iterdir():
            path.unlink()
        child.transaction(lambda doc: add_node(doc, None, "after native exit"), expected_version=1)
        self.assertEqual(2, self.source.read_snapshot()["version"])
        self.assertFalse(_path(self.root, self.child).exists())

    def test_cross_process_resume_uses_persisted_canonical_mapping(self):
        import subprocess
        import sys
        child = session_work_plan_store(self.root, self.child)
        expected = child.read_snapshot()
        for path in self.directory.iterdir():
            path.unlink()
        code = ("import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
                "from ciel_runtime_support.work_plan_store import session_work_plan_store;"
                "print(json.dumps(session_work_plan_store(Path(sys.argv[2]),sys.argv[3]).read_snapshot()))")
        result = subprocess.run([sys.executable, "-I", "-c", code,
            str(Path(__import__("ciel_runtime_support.work_plan_store", fromlist=["__file__"]).__file__).resolve().parents[1]),
            str(self.root), self.child],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(expected, json.loads(result.stdout))

    def test_unrelated_session_is_isolated(self):
        self.assertEqual([], session_work_plan_store(self.root, "claude:unrelated").read_snapshot()["nodes"])
        self.assertEqual([], session_work_plan_store(self.root, "codex:12345678-child").read_snapshot()["nodes"])

    def test_mismatched_cwd_domain_job_and_dead_identity_never_link(self):
        for changes in [dict(cwd="other"), dict(pidDomain="other"), dict(jobId="wrong"), dict(procStart="reused")]:
            with self.subTest(changes=changes):
                record_changes = dict(kind="bg", jobId="12345678")
                record_changes.update(changes)
                self.record(102, "12345678-child", **record_changes)
                self.assertEqual([], session_work_plan_store(self.root, self.child).read_snapshot()["nodes"])
        self.record(102, "12345678-child", kind="bg", jobId="12345678")
        with patch("ciel_runtime_support.claude_session_handoff._process_identity", return_value=""):
            self.assertEqual([], session_work_plan_store(self.root, self.child).read_snapshot()["nodes"])

    def test_preexisting_split_board_is_not_overwritten(self):
        path = _path(self.root, self.child)
        WorkPlanStore(path).transaction(lambda d: add_node(d, None, "separate new work"))
        before = path.read_bytes()
        original = self.source.path.read_bytes()
        with self.assertRaisesRegex(WorkPlanError, "two saved boards"):
            session_work_plan_store(self.root, self.child).read_snapshot()
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(original, self.source.path.read_bytes())

    def test_ambiguous_parent_is_error(self):
        self.record(103, "other-parent", parkedJobId="12345678", startedAt=900)
        with self.assertRaisesRegex(WorkPlanError, "multiple native parents"):
            session_work_plan_store(self.root, self.child).read_snapshot()

    def test_corrupt_alias_and_missing_source_fail_closed(self):
        child = session_work_plan_store(self.root, self.child)
        child.read_snapshot()
        marker = _path(self.root, self.child).with_name("work-lineage.json")
        original = marker.read_bytes()
        marker.write_text("[]")
        with self.assertRaises(WorkPlanError):
            child.read_snapshot()
        marker.write_bytes(original)
        self.source.path.unlink()
        with self.assertRaisesRegex(WorkPlanError, "source is missing"):
            child.read_snapshot()

    def test_ordinary_and_linked_snapshot_reads_do_not_take_store_lock(self):
        child = session_work_plan_store(self.root, self.child)
        child.read_snapshot()
        with patch("ciel_runtime_support.work_session_lineage.exclusive_file_lock", side_effect=AssertionError("snapshot locked")):
            child.read_snapshot()
            session_work_plan_store(self.root, "claude:unrelated").read_snapshot()
