import concurrent.futures
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from ciel_runtime_support import work_plan_tree as t
from ciel_runtime_support.work_plan_store import WorkPlanConflict, WorkPlanStore, session_work_plan_store


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
