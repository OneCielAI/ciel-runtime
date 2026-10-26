import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from mvcc_cache import Cache, ConflictError


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cache = Cache(self.clock)

    def test_ttl_boundary_zero_and_delete(self):
        c = self.cache
        self.assertEqual(c.get('missing', 4), 4)
        c.set('none', None)
        self.assertIsNone(c.get('none', 4))
        c.set('x', 1, ttl=5)
        self.clock.now = 4
        self.assertEqual(c.get('x'), 1)
        self.clock.now = 5
        self.assertEqual(c.get('x', 'expired'), 'expired')
        self.assertFalse(c.delete('x'))
        c.set('x', 2, ttl=0)
        self.assertIsNone(c.get('x'))
        c.set('x', 3)
        self.assertTrue(c.delete('x'))
        self.assertFalse(c.delete('x'))

    def test_snapshot_freezes_bindings_and_ttl_visibility(self):
        c = self.cache
        c.set('a', 'old', ttl=5)
        c.set('b', 'kept')
        c.set('expired', 1, ttl=0)
        s = c.snapshot()
        c.set('a', 'new')
        c.delete('b')
        c.set('later', 8)
        self.clock.now = 100
        self.assertEqual(s.get('a'), 'old')
        self.assertEqual(s.get('b'), 'kept')
        self.assertEqual(s.get('later', 9), 9)
        self.assertIsNone(s.get('expired'))
        self.assertIsNone(c.snapshot().get('b'))

    def test_transaction_snapshot_and_read_your_writes_commit_ttl(self):
        c = self.cache
        c.set('a', 'original', ttl=2)
        tx = c.transaction()
        self.clock.now = 50
        self.assertEqual(tx.get('a'), 'original')
        tx.set('b', 7, ttl=5)
        self.assertEqual(tx.get('b'), 7)
        self.assertTrue(tx.delete('a'))
        self.assertEqual(tx.get('a', 9), 9)
        tx.set('z', 1, ttl=0)
        self.assertIsNone(tx.get('z'))
        self.clock.now = 100
        tx.commit()
        self.assertEqual(c.get('b'), 7)
        self.assertIsNone(c.get('a'))
        self.clock.now = 105
        self.assertIsNone(c.get('b'))

    def test_read_and_write_conflicts_are_atomic(self):
        for operation in ('read', 'blind_write', 'delete', 'absent_read'):
            with self.subTest(operation=operation):
                c = Cache(self.clock)
                c.set('x', 1)
                tx = c.transaction()
                if operation == 'read':
                    tx.get('x')
                elif operation == 'blind_write':
                    tx.set('x', 10)
                elif operation == 'delete':
                    tx.delete('x')
                else:
                    tx.get('absent')
                tx.set('unrelated', 5)
                if operation == 'absent_read':
                    c.set('absent', 1)
                    c.delete('absent')
                else:
                    c.set('x', 2)
                    c.set('x', 1)  # ABA still conflicts.
                with self.assertRaises(ConflictError):
                    tx.commit()
                self.assertIsNone(c.get('unrelated'))
                with self.assertRaises(RuntimeError):
                    tx.get('x')

    def test_late_read_and_missing_delete_conflict(self):
        c = self.cache
        tx = c.transaction()
        c.set('late', 1)
        self.assertIsNone(tx.get('late'))
        with self.assertRaises(ConflictError):
            tx.commit()
        tx = c.transaction()
        tx.set('absent', 1)
        c.delete('absent')
        with self.assertRaises(ConflictError):
            tx.commit()

    def test_versions_are_per_key_monotonic_including_tombstones(self):
        c = self.cache
        c.set('x', 1)
        first = c._versions['x']
        c.delete('x')
        self.assertGreater(c._versions['x'], first)
        second = c._versions['x']
        c.delete('x')
        self.assertGreater(c._versions['x'], second)
        c.set('other', 9)
        tx = c.transaction()
        tx.get('x')
        c.set('other', 10)
        tx.commit()  # Unrelated key writes do not conflict.

    def test_closed_after_commit_or_rollback(self):
        for action in ('commit', 'rollback'):
            tx = self.cache.transaction()
            tx.set('x', 1)
            getattr(tx, action)()
            for operation in (lambda: tx.get('x'), lambda: tx.set('x', 2),
                              lambda: tx.delete('x'), tx.commit, tx.rollback):
                with self.assertRaises(RuntimeError):
                    operation()
        self.assertEqual(self.cache.get('x'), 1)

    def test_invalid_ttl_does_not_partially_commit(self):
        tx = self.cache.transaction()
        tx.set('good', 1)
        tx.set('bad', 2, ttl='not numeric')
        with self.assertRaises(TypeError):
            tx.commit()
        self.assertIsNone(self.cache.get('good'))

    def test_concurrent_conflict(self):
        c = self.cache
        barrier = Barrier(2)

        def contender(value):
            tx = c.transaction()
            tx.set('x', value)
            barrier.wait()
            try:
                tx.commit()
                return True
            except ConflictError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contender, [1, 2]))
        self.assertEqual(sorted(results), [False, True])


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def test_validation_and_default_unlimited(self):
        for size in (0, -1):
            with self.assertRaises(ValueError):
                Cache(self.clock, size)
        for c in (Cache(self.clock), Cache(self.clock, None)):
            for i in range(10):
                c.set(i, i)
            self.assertEqual([c.get(i) for i in range(10)], list(range(10)))

    def test_fifo_not_lru_or_overwrite_refresh(self):
        c = Cache(self.clock, capacity=2)
        c.set('a', 1)
        c.set('b', 2)
        c.get('a')
        c.set('a', 3)
        snap = c.snapshot()
        tx = c.transaction()
        tx.get('a')
        old_version = c._versions['a']
        c.set('c', 4)
        self.assertIsNone(c.get('a'))
        self.assertEqual(c.get('b'), 2)
        self.assertEqual(snap.get('a'), 3)
        self.assertGreater(c._versions['a'], old_version)
        with self.assertRaises(ConflictError):
            tx.commit()

    def test_expired_dont_count_and_reinsert_is_new(self):
        c = Cache(self.clock, 2)
        c.set('a', 1)
        c.set('b', 2, ttl=1)
        self.clock.now = 1
        c.set('c', 3)
        self.assertEqual(c.get('a'), 1)
        c.set('b', 4)
        self.assertIsNone(c.get('a'))
        self.assertEqual(c.get('c'), 3)
        c.delete('c')
        c.set('c', 5)
        c.set('d', 6)
        self.assertIsNone(c.get('b'))

    def test_commit_operations_call_order_before_eviction(self):
        c = Cache(self.clock, 2)
        c.set('a', 1)
        c.set('b', 2)
        tx = c.transaction()
        tx.set('c', 3)
        tx.set('a', 9)  # Must not resurrect a after premature eviction.
        tx.commit()
        self.assertIsNone(c.get('a'))
        self.assertEqual(c.get('b'), 2)
        self.assertEqual(c.get('c'), 3)
        c = Cache(self.clock, 2)
        tx = c.transaction()
        tx.set('x', 1)
        tx.set('y', 2)
        tx.delete('x')
        tx.set('x', 3)
        tx.set('z', 4)
        tx.commit()
        self.assertIsNone(c.get('y'))
        self.assertEqual(c.get('x'), 3)
        self.assertEqual(c.get('z'), 4)


if __name__ == '__main__':
    unittest.main()
