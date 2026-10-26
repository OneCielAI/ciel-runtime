import unittest

from dag_scheduler import CycleError, Scheduler, StateError


class AddAndReadyTests(unittest.TestCase):
    def test_higher_priority_first_then_insertion_order(self):
        s = Scheduler()
        s.add("b", priority=1)
        s.add("z", priority=5)
        s.add("a", priority=1)
        s.add("m", priority=5)
        # Equal priority keeps insertion order, not lexical order.
        self.assertEqual(s.ready(), ["z", "m", "b", "a"])

    def test_default_priority_zero_and_negative(self):
        s = Scheduler()
        s.add("neg", priority=-1)
        s.add("zero")
        self.assertEqual(s.ready(), ["zero", "neg"])

    def test_unknown_dependency_rejected(self):
        s = Scheduler()
        with self.assertRaises(ValueError):
            s.add("a", deps=["missing"])
        with self.assertRaises(KeyError):
            s.status("a")

    def test_duplicate_task_rejected(self):
        s = Scheduler()
        s.add("a")
        with self.assertRaises(ValueError):
            s.add("a")

    def test_self_dependency_on_add(self):
        s = Scheduler()
        with self.assertRaises(CycleError):
            s.add("a", deps=["a"])

    def test_ready_requires_all_deps_done(self):
        s = Scheduler()
        s.add("a")
        s.add("b")
        s.add("c", deps=["a", "b"], priority=10)
        self.assertEqual(s.ready(), ["a", "b"])
        s.start("a")
        s.finish("a")
        self.assertEqual(s.ready(), ["b"])
        s.start("b")
        s.finish("b")
        self.assertEqual(s.ready(), ["c"])


class StateTransitionTests(unittest.TestCase):
    def test_lifecycle_statuses(self):
        s = Scheduler()
        s.add("a")
        self.assertEqual(s.status("a"), "pending")
        s.start("a")
        self.assertEqual(s.status("a"), "running")
        self.assertEqual(s.ready(), [])
        s.finish("a")
        self.assertEqual(s.status("a"), "done")

    def test_start_non_ready_raises(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        with self.assertRaises(StateError):
            s.start("b")
        s.start("a")
        with self.assertRaises(StateError):
            s.start("a")
        s.finish("a")
        with self.assertRaises(StateError):
            s.start("a")

    def test_finish_requires_running(self):
        s = Scheduler()
        s.add("a")
        with self.assertRaises(StateError):
            s.finish("a")
        s.start("a")
        s.finish("a", success=False)
        self.assertEqual(s.status("a"), "failed")
        with self.assertRaises(StateError):
            s.finish("a")

    def test_unknown_task_ids(self):
        s = Scheduler()
        for call in (s.start, s.finish, s.status, s.retry):
            with self.assertRaises(KeyError):
                call("nope")
        with self.assertRaises(KeyError):
            s.replace_deps("nope", [])

    def test_failed_dependency_blocks_descendants(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        s.add("c", deps=["b"])
        s.add("x")
        s.start("a")
        s.finish("a", success=False)
        self.assertEqual(s.ready(), ["x"])
        with self.assertRaises(StateError):
            s.start("b")
        self.assertEqual(s.status("b"), "pending")
        self.assertEqual(s.status("c"), "pending")


class RetryTests(unittest.TestCase):
    def test_retry_only_failed(self):
        s = Scheduler()
        s.add("a")
        with self.assertRaises(StateError):
            s.retry("a")
        s.start("a")
        with self.assertRaises(StateError):
            s.retry("a")
        s.finish("a")
        with self.assertRaises(StateError):
            s.retry("a")

    def test_descendants_ready_only_after_retry_completes(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        s.start("a")
        s.finish("a", success=False)
        self.assertEqual(s.ready(), [])
        s.retry("a")
        self.assertEqual(s.status("a"), "pending")
        self.assertEqual(s.ready(), ["a"])
        s.start("a")
        self.assertEqual(s.ready(), [])
        s.finish("a")
        self.assertEqual(s.ready(), ["b"])

    def test_retry_keeps_original_insertion_order(self):
        s = Scheduler()
        s.add("first")
        s.add("second")
        s.start("first")
        s.finish("first", success=False)
        s.retry("first")
        self.assertEqual(s.ready(), ["first", "second"])


class ReplaceDepsTests(unittest.TestCase):
    def _snapshot(self, s, ids):
        return (s.ready(), {i: s.status(i) for i in ids},
                {i: s._tasks[i].deps for i in ids})

    def test_replace_deps_changes_readiness(self):
        s = Scheduler()
        s.add("a")
        s.add("b")
        s.add("c", deps=["a"])
        s.replace_deps("c", ["b"])
        s.start("b")
        s.finish("b")
        self.assertEqual(s.ready(), ["a", "c"])
        s.replace_deps("c", [])
        self.assertIn("c", s.ready())

    def test_replace_deps_unknown_is_atomic(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        before = self._snapshot(s, ["a", "b"])
        with self.assertRaises(ValueError):
            s.replace_deps("b", ["a", "ghost"])
        self.assertEqual(self._snapshot(s, ["a", "b"]), before)

    def test_replace_deps_self_is_cycle_and_atomic(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        before = self._snapshot(s, ["a", "b"])
        with self.assertRaises(CycleError):
            s.replace_deps("b", ["b"])
        self.assertEqual(self._snapshot(s, ["a", "b"]), before)

    def test_replace_deps_indirect_cycle_is_atomic(self):
        s = Scheduler()
        s.add("a")
        s.add("b", deps=["a"])
        s.add("c", deps=["b"])
        s.add("d")
        ids = ["a", "b", "c", "d"]
        before = self._snapshot(s, ids)
        with self.assertRaises(CycleError):
            s.replace_deps("a", ["d", "c"])
        self.assertEqual(self._snapshot(s, ids), before)

    def test_replace_deps_nonpending_raises(self):
        s = Scheduler()
        s.add("a")
        s.add("b")
        s.start("a")
        with self.assertRaises(StateError):
            s.replace_deps("a", ["b"])
        s.finish("a", success=False)
        with self.assertRaises(StateError):
            s.replace_deps("a", ["b"])
        s.start("b")
        s.finish("b")
        with self.assertRaises(StateError):
            s.replace_deps("b", [])


if __name__ == "__main__":
    unittest.main()
