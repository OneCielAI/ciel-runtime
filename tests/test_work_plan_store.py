import concurrent.futures
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from ciel_runtime_support import work_plan_tree as t
from ciel_runtime_support.work_plan_store import (
    WorkPlanConflict, WorkPlanStore, session_work_plan_store, subscribe_work_plan_changes,
)


class WorkPlanStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="work-store-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "workspace" / "work-plan.json"
        self.store = WorkPlanStore(self.path)

    def test_missing_is_empty(self):
        self.assertEqual(self.store.read(), t.empty_document())
        self.assertFalse(self.path.exists())

    def test_transaction_persists_unicode_version_result(self):
        doc, result = self.store.transaction(lambda d: t.add_node(d, None, "검증 goal"), expected_version=0)
        self.assertEqual(doc["version"], 1)
        self.assertGreater(doc["updated_at"], 0)
        self.assertEqual(result, doc["active_root"])
        self.assertEqual(WorkPlanStore(self.path).read(), doc)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), doc)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_noop_and_detached_read(self):
        self.store.transaction(lambda d: t.add_node(d, None, "goal"))
        before = self.path.read_bytes()
        doc, result = self.store.transaction(lambda d: "unchanged", expected_version=1)
        self.assertEqual((doc["version"], result), (1, "unchanged"))
        self.assertEqual(self.path.read_bytes(), before)
        doc["nodes"][0]["title"] = "not persisted"
        self.assertEqual(self.store.read()["nodes"][0]["title"], "goal")

    def test_stale_cas_does_not_call_mutator(self):
        self.store.transaction(lambda d: t.add_node(d, None, "goal"), expected_version=0)
        before = self.path.read_bytes()
        called = []
        with self.assertRaises(WorkPlanConflict):
            self.store.transaction(lambda d: called.append(True), expected_version=0)
        self.assertEqual(called, [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_mutation_rolls_back_partial_tree(self):
        self.store.transaction(lambda d: t.add_node(d, None, "original"))
        before = self.path.read_bytes()
        def mutate(doc):
            t.add_node(doc, None, "must not persist")
            raise t.WorkPlanError("rejected")
        with self.assertRaises(t.WorkPlanError):
            self.store.transaction(mutate)
        self.assertEqual(self.path.read_bytes(), before)

    def test_concurrent_independent_stores_cas(self):
        barrier = threading.Barrier(6)
        def writer(index):
            store = WorkPlanStore(self.path)
            barrier.wait(timeout=10)
            try:
                store.transaction(lambda d: t.add_node(d, None, str(index)), expected_version=0)
                return "won"
            except WorkPlanConflict:
                return "conflict"
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            outcomes = list(pool.map(writer, range(6)))
        self.assertEqual(outcomes.count("won"), 1)
        self.assertEqual(outcomes.count("conflict"), 5)
        self.assertEqual(self.store.read()["version"], 1)
        self.assertEqual(len(self.store.read()["nodes"]), 1)

    def test_concurrent_writers_without_cas_preserve_all_writes(self):
        barrier = threading.Barrier(6)
        def writer(index):
            barrier.wait(timeout=10)
            WorkPlanStore(self.path).transaction(lambda d: t.add_node(d, None, str(index)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(writer, range(6)))
        doc = self.store.read()
        self.assertEqual(doc["version"], 6)
        self.assertEqual({n["title"] for n in doc["nodes"]}, {str(i) for i in range(6)})
        self.assertEqual(len({n["id"] for n in doc["nodes"]}), 6)

    def test_cross_process_cas(self):
        root = str(Path(__file__).resolve().parents[1])
        code = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ciel_runtime_support.work_plan_store import WorkPlanStore, WorkPlanConflict
from ciel_runtime_support.work_plan_tree import add_node
store = WorkPlanStore(Path(sys.argv[2]))
Path(sys.argv[3]).write_text('ready')
deadline = time.monotonic() + 15
while not Path(sys.argv[4]).exists():
    if time.monotonic() > deadline: raise RuntimeError('start barrier timeout')
    time.sleep(.01)
try:
    store.transaction(lambda d: add_node(d, None, sys.argv[5]), expected_version=0)
    print('won')
except WorkPlanConflict:
    print('conflict')
"""
        start = Path(self.temp.name) / "go"
        ready = [Path(self.temp.name) / f"ready-{i}" for i in range(3)]
        workers = [subprocess.Popen([sys.executable, "-I", "-c", code, root, str(self.path),
                                    str(ready[i]), str(start), str(i)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(3)]
        deadline = time.monotonic() + 10
        while not all(p.exists() for p in ready) and time.monotonic() < deadline:
            time.sleep(.01)
        start.write_text("go")
        outputs = [p.communicate(timeout=25) for p in workers]
        self.assertTrue(all(p.exists() for p in ready), outputs)
        for process, (_, stderr) in zip(workers, outputs):
            self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(sorted(out.strip() for out, _ in outputs), ["conflict", "conflict", "won"])
        self.assertEqual(self.store.read()["version"], 1)

    def test_expected_version_is_strict(self):
        for value in (True, False, 1.0, "1"):
            with self.subTest(value=value), self.assertRaises(t.WorkPlanError):
                self.store.transaction(lambda d: None, expected_version=value)

    def test_read_snapshot_is_read_only_and_corrupt_fails_without_quarantine(self):
        path = self.path
        original = b"{broken JSON"
        path.parent.mkdir(parents=True)
        path.write_bytes(original)
        with patch("ciel_runtime_support.work_plan_store.exclusive_file_lock") as lock:
            with self.assertRaises(t.WorkPlanError):
                self.store.read_snapshot()
            lock.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_read_snapshot_missing_creates_nothing(self):
        self.assertEqual(self.store.read_snapshot(), t.empty_document())
        self.assertFalse(self.path.parent.exists())

    def test_commit_observers_are_shared_noop_safe_and_errors_are_isolated(self):
        events = []
        unsubscribe = subscribe_work_plan_changes(lambda path, before, after: events.append(
            (path, before["version"], after["version"])))
        self.addCleanup(unsubscribe)
        callback_error = subscribe_work_plan_changes(lambda *_: (_ for _ in ()).throw(RuntimeError("ignored")))
        self.addCleanup(callback_error)
        other = WorkPlanStore(self.path)
        other.transaction(lambda d: t.add_node(d, None, "goal"))
        other.transaction(lambda d: "noop")
        self.assertEqual([(p, old, new) for p, old, new in events], [(other.path, 0, 1)])
        unsubscribe()
        other.transaction(lambda d: t.add_node(d, None, "second"))
        self.assertEqual(len(events), 1)

    def test_read_snapshot_is_detached_bounded_and_rejects_invalid_encodings(self):
        saved, _ = self.store.transaction(lambda d: t.add_node(d, None, "snapshot"))
        first = self.store.read_snapshot()
        first["nodes"][0]["title"] = "detached"
        self.assertEqual(self.store.read_snapshot(), saved)
        with patch("ciel_runtime_support.work_plan_store.MAX_WORK_PLAN_BYTES", 8):
            with self.assertRaisesRegex(t.WorkPlanError, "size limit"):
                self.store.read_snapshot()
        self.path.write_bytes(b"\xff")
        with self.assertRaisesRegex(t.WorkPlanError, "invalid Work plan snapshot"):
            self.store.read_snapshot()
        self.assertEqual(self.path.read_bytes(), b"\xff")
        self.assertEqual(list(self.path.parent.glob("*.corrupt-*")), [])

    def test_read_snapshot_observes_whole_atomic_documents_during_writes(self):
        self.store.transaction(lambda d: t.add_node(d, None, "0"))
        writer = WorkPlanStore(self.path)
        def write():
            for i in range(1, 21):
                writer.transaction(lambda d: t.update_node(d, "n1", title=str(i)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write)
            snapshots = [self.store.read_snapshot() for _ in range(100)]
            future.result(timeout=15)
        for doc in snapshots:
            self.assertEqual(doc["version"], int(doc["nodes"][0]["title"]) + 1)
        self.assertEqual(self.store.read_snapshot()["version"], 21)

    def test_update_and_reorder_semantic_noops_preserve_file_bytes_and_timestamps(self):
        def plan(doc):
            t.set_plan(doc, {"title": "first", "children": [{"title": "one"}, {"title": "two"}]}, now=1)
            t.add_node(doc, None, "second", now=1)
            root = t.add_node(doc, None, "third", now=1)
            t.select_root(doc, root, user_requested=True)
        saved, _ = self.store.transaction(plan)
        contents = self.path.read_bytes()
        for mutate in (lambda d: t.update_node(d, "n2", title=" one ", ref=" \n ", now=100),
                       lambda d: t.reorder_node(d, "n2", 1),
                       lambda d: t.reorder_node(d, "n5", 1)):
            doc, _ = self.store.transaction(mutate, expected_version=saved["version"])
            self.assertEqual(doc, saved)
            self.assertEqual(self.path.read_bytes(), contents)

    def test_observers_run_after_replace_outside_all_locks_with_detached_documents(self):
        events = []
        callbacks = []
        def observe(path, before, after):
            # A separate thread must acquire both store and file locks while this
            # callback waits. Reentrant acquisition in this thread is insufficient.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                persisted = pool.submit(self.store.read).result(timeout=5)
            self.assertEqual(persisted, after)
            events.append((path, before, after))
            after["nodes"][0]["title"] = "observer mutation"
            callbacks.append(subscribe_work_plan_changes(lambda *_: None))
        unsubscribe = subscribe_work_plan_changes(observe)
        self.addCleanup(unsubscribe)
        # Observer exceptions are intentionally swallowed by production; retain
        # exceptions here so assertion failures are not hidden from the test.
        errors = []
        unsubscribe()
        def checked(*args):
            try:
                observe(*args)
            except Exception as error:
                errors.append(error)
        unsubscribe = subscribe_work_plan_changes(checked)
        self.addCleanup(unsubscribe)
        untouched = []
        unsubscribe_other = subscribe_work_plan_changes(lambda _, __, after: untouched.append(after))
        self.addCleanup(unsubscribe_other)
        doc, result = self.store.transaction(lambda d: t.add_node(d, None, "committed"))
        for remove in callbacks:
            remove()
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0][0], events[0][1]["version"]), (self.store.path, 0))
        self.assertEqual(doc["nodes"][0]["title"], "committed")
        self.assertEqual(untouched[0]["nodes"][0]["title"], "committed")
        self.assertEqual(result, "n1")
        self.assertEqual(self.store.read_snapshot(), doc)

    def test_failed_and_noop_transactions_never_emit_commit_events(self):
        self.store.transaction(lambda d: t.add_node(d, None, "original"))
        events = []
        unsubscribe = subscribe_work_plan_changes(lambda *event: events.append(event))
        self.addCleanup(unsubscribe)
        before = self.path.read_bytes()
        self.store.transaction(lambda d: t.update_node(d, "n1", title="original"))
        with self.assertRaises(WorkPlanConflict):
            self.store.transaction(lambda d: t.add_node(d, None, "stale"), expected_version=0)
        with self.assertRaises(t.WorkPlanError):
            self.store.transaction(lambda d: t.update_node(d, "missing", title="missing"))
        with patch("ciel_runtime_support.work_plan_store.os.replace", side_effect=OSError("write failed")):
            with self.assertRaises(OSError):
                self.store.transaction(lambda d: t.update_node(d, "n1", title="not committed"))
        self.assertEqual(events, [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_corruption_read_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken JSON", encoding="utf-8")
        with self.assertRaises(t.WorkPlanError):
            self.store.read()

    def test_corruption_transaction_cannot_overwrite(self):
        self.path.parent.mkdir(parents=True)
        original = b'{"nodes": [{"id": "n1", "title": "lost", "parent_id": "missing"}]}'
        self.path.write_bytes(original)
        called = []
        def mutate(doc):
            called.append(True)
            return t.add_node(doc, None, "replacement")
        with self.assertRaises(t.WorkPlanError):
            self.store.transaction(mutate, expected_version=0)
        self.assertEqual(called, [])
        retained = ([self.path] if self.path.exists() else []) + list(self.path.parent.glob("work-plan.json.corrupt-*"))
        self.assertTrue(any(p.read_bytes() == original for p in retained))

    def test_session_stores_resume_same_and_new_sessions_distinct(self):
        a = session_work_plan_store(self.path.parent, "claude:session-a")
        a.transaction(lambda d: t.add_node(d, None, "only session A"))
        self.assertIs(a, session_work_plan_store(self.path.parent, "claude:session-a"))
        self.assertEqual(session_work_plan_store(self.path.parent, "claude:session-a").read()["nodes"][0]["title"],
                         "only session A")
        for workspace, session_id in ((self.path.parent, "claude:session-b"),
                                      (self.path.parent, "codex:session-a"),
                                      (Path(self.temp.name) / "other", "claude:session-a")):
            other = session_work_plan_store(workspace, session_id)
            self.assertIsNot(a, other)
            self.assertEqual(other.read(), t.empty_document())

    def test_session_store_never_reads_legacy_shared_board(self):
        self.store.transaction(lambda d: t.add_node(d, None, "legacy shared data"))
        before = self.path.read_bytes()
        scoped = session_work_plan_store(self.path.parent, "claude:new-session")
        self.assertEqual(scoped.read(), t.empty_document())
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_or_unsafe_session_identity_fails_closed(self):
        for value in (None, "", "unknown", "claude:", "claude:../escape", "codex:/absolute", "claude:x" * 100):
            with self.subTest(value=value), self.assertRaises(t.WorkPlanError):
                session_work_plan_store(self.path.parent, value)


if __name__ == "__main__":
    unittest.main()
