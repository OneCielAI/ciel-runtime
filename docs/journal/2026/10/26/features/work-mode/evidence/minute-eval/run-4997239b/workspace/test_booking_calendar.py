import random
import unittest

from booking_calendar import Calendar, CapacityError, ConflictError


def naive_ok(active, start, end, units, capacity):
    for t in range(start, end):
        used = sum(u for s, e, u in active.values() if s <= t < e)
        if used + units > capacity:
            return False
    return True


class Validation(unittest.TestCase):
    def test_exceptions_exist(self):
        self.assertTrue(issubclass(CapacityError, Exception))
        self.assertTrue(issubclass(ConflictError, Exception))

    def test_capacity_validation(self):
        for bad in (0, -1, True, False, 1.0, "2", None):
            with self.assertRaises(ValueError):
                Calendar(bad)
        self.assertEqual(Calendar().allocations(), [])
        Calendar(capacity=5)

    def test_reserve_validation(self):
        c = Calendar(2)
        bad_calls = [
            ("", 0, 1, 1), (None, 0, 1, 1), (3, 0, 1, 1),
            ("a", 1, 1, 1), ("a", 2, 1, 1),
            ("a", True, 2, 1), ("a", 0, False, 1), ("a", 0.0, 1, 1), ("a", 0, "1", 1),
            ("a", 0, 1, 0), ("a", 0, 1, -1), ("a", 0, 1, True), ("a", 0, 1, 1.0),
        ]
        for args in bad_calls:
            with self.assertRaises(ValueError, msg=args):
                c.reserve(*args)
        self.assertEqual(c.allocations(), [])

    def test_available_validation(self):
        c = Calendar()
        for args in ((1, 1), (2, 1), (True, 2), (0, 1, 0), (0, 1, True)):
            with self.assertRaises(ValueError, msg=args):
                c.available(*args)

    def test_negative_times(self):
        c = Calendar()
        c.reserve("n", -10, -5)
        self.assertFalse(c.available(-6, 0))
        self.assertTrue(c.available(-5, 0))
        self.assertEqual(c.allocations(), [("n", -10, -5, 1)])


class Semantics(unittest.TestCase):
    def test_half_open(self):
        c = Calendar()
        c.reserve("a", 0, 10)
        c.reserve("b", 10, 20)
        c.reserve("z", -5, 0)
        with self.assertRaises(CapacityError):
            c.reserve("c", 9, 11)
        self.assertEqual([a[0] for a in c.allocations()], ["z", "a", "b"])

    def test_cumulative_units(self):
        c = Calendar(5)
        c.reserve("a", 0, 10, 3)
        c.reserve("b", 5, 15, 2)
        self.assertFalse(c.available(5, 6))
        self.assertTrue(c.available(0, 5, 2))
        self.assertTrue(c.available(10, 15, 3))
        self.assertFalse(c.available(10, 15, 4))
        with self.assertRaises(CapacityError):
            c.reserve("c", 9, 10, 1)
        with self.assertRaises(CapacityError):
            c.reserve("big", 100, 101, 6)

    def test_peak_not_sum_of_overlaps(self):
        # Two reservations both overlap the request but not each other.
        c = Calendar(2)
        c.reserve("a", 0, 5)
        c.reserve("b", 5, 10)
        self.assertTrue(c.available(0, 10, 1))
        c.reserve("c", 0, 10)
        self.assertFalse(c.available(0, 10, 1))

    def test_duplicate_and_cancel(self):
        c = Calendar(3)
        c.reserve("a", 0, 1)
        with self.assertRaises(ValueError):
            c.reserve("a", 5, 6)
        self.assertEqual(c.allocations(), [("a", 0, 1, 1)])
        self.assertTrue(c.cancel("a"))
        self.assertFalse(c.cancel("a"))
        self.assertFalse(c.cancel("never"))
        c.reserve("a", 5, 6)  # id reusable once cancelled
        self.assertEqual(c.allocations(), [("a", 5, 6, 1)])
        with self.assertRaises(ValueError):
            c.cancel("")

    def test_failures_atomic(self):
        c = Calendar(2)
        c.reserve("a", 0, 10, 2)
        before = c.allocations()
        with self.assertRaises(CapacityError):
            c.reserve("b", 5, 15)
        with self.assertRaises(ValueError):
            c.reserve("a", 20, 30)
        with self.assertRaises(ValueError):
            c.reserve("b", 30, 20)
        self.assertEqual(c.allocations(), before)
        self.assertFalse(c.cancel("b"))
        c.reserve("b", 10, 15, 2)

    def test_allocations_sort_start_then_id(self):
        c = Calendar(10)
        c.reserve("zeta", 0, 1)
        c.reserve("alpha", 0, 3)
        c.reserve("beta", -1, 9)
        c.reserve("Alpha", 0, 2)
        self.assertEqual(c.allocations(), [
            ("beta", -1, 9, 1), ("Alpha", 0, 2, 1), ("alpha", 0, 3, 1), ("zeta", 0, 1, 1)])
        # returned list is a copy
        c.allocations().clear()
        self.assertEqual(len(c.allocations()), 4)


class ManyOverlapping(unittest.TestCase):
    def test_many_identical_overlaps(self):
        c = Calendar(100)
        for i in range(100):
            c.reserve("r%03d" % i, 0, 1000)
        self.assertFalse(c.available(500, 501))
        with self.assertRaises(CapacityError):
            c.reserve("extra", 999, 1001)
        c.reserve("after", 1000, 1001, 99)
        with self.assertRaises(CapacityError):
            c.reserve("extra", 999, 1001)
        self.assertTrue(c.cancel("r050"))
        c.reserve("extra", 999, 1001)
        self.assertEqual(len(c.allocations()), 101)

    def test_staircase(self):
        c = Calendar(10)
        for i in range(10):
            c.reserve("s%d" % i, i, i + 10)
        # peak at t=9 is 10
        self.assertFalse(c.available(9, 10))
        self.assertTrue(c.available(0, 1, 9))
        self.assertTrue(c.available(18, 19, 9))
        self.assertFalse(c.available(18, 19, 10))

    def test_randomized_vs_naive(self):
        rng = random.Random(7)
        for trial in range(60):
            cap = rng.randint(1, 6)
            c = Calendar(cap)
            model = {}
            for step in range(120):
                op = rng.random()
                if op < 0.65:
                    rid = "id%d" % rng.randint(0, 40)
                    s = rng.randint(-20, 20)
                    e = s + rng.randint(1, 12)
                    u = rng.randint(1, cap + 1)
                    expect_ok = naive_ok(model, s, e, u, cap)
                    self.assertEqual(c.available(s, e, u), expect_ok)
                    if rid in model:
                        with self.assertRaises(ValueError):
                            c.reserve(rid, s, e, u)
                    elif expect_ok:
                        c.reserve(rid, s, e, u)
                        model[rid] = (s, e, u)
                    else:
                        with self.assertRaises(CapacityError):
                            c.reserve(rid, s, e, u)
                else:
                    rid = "id%d" % rng.randint(0, 40)
                    self.assertEqual(c.cancel(rid), rid in model)
                    model.pop(rid, None)
                expected = sorted(((i, s, e, u) for i, (s, e, u) in model.items()),
                                  key=lambda a: (a[1], a[0]))
                self.assertEqual(c.allocations(), expected)


class ReserveMany(unittest.TestCase):
    """REQ-B1 reserve_many."""

    def test_all_reserved(self):
        c = Calendar(2)
        c.reserve_many([("b", 0, 5, 1), ("a", 0, 5, 1), ("c", 5, 9, 2)])
        self.assertEqual(c.allocations(),
                         [("a", 0, 5, 1), ("b", 0, 5, 1), ("c", 5, 9, 2)])
        c.reserve_many([])
        c.reserve_many(iter([("d", 9, 10, 2)]))
        self.assertEqual(len(c.allocations()), 4)

    def test_batch_capacity_is_cumulative_within_batch(self):
        c = Calendar(2)
        c.reserve("x", 0, 10)
        before = c.allocations()
        with self.assertRaises(CapacityError):
            c.reserve_many([("a", 0, 3, 1), ("b", 2, 5, 1)])
        self.assertEqual(c.allocations(), before)
        # each alone would fit; together they overload [2,3)
        self.assertTrue(c.available(0, 3))
        self.assertTrue(c.available(2, 5))

    def test_failures_leave_state_unchanged(self):
        c = Calendar(3)
        c.reserve("x", 0, 10)
        before = c.allocations()
        bad_batches = [
            [("a", 0, 1, 1), ("a", 5, 6, 1)],          # dup inside batch
            [("a", 0, 1, 1), ("x", 20, 21, 1)],        # dup with active
            [("a", 0, 1, 1), ("b", 3, 2, 1)],          # invalid interval
            [("a", 0, 1, 1), ("", 3, 4, 1)],           # invalid id
            [("a", 0, 1, 1), ("b", 3, 4, True)],       # bool units
            [("a", 0, 1, 1), ("b", 3, 4)],             # wrong arity
            [("a", 0, 1, 1), None],                    # not a tuple
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError, msg=batch):
                c.reserve_many(batch)
            self.assertEqual(c.allocations(), before)
        with self.assertRaises(CapacityError):
            c.reserve_many([("a", 0, 1, 1), ("b", 50, 60, 1), ("big", 5, 6, 3)])
        self.assertEqual(c.allocations(), before)
        with self.assertRaises(ValueError):
            c.reserve_many(5)
        self.assertEqual(c.allocations(), before)

    def test_many_overlapping_batch(self):
        c = Calendar(50)
        c.reserve_many([("r%02d" % i, i, 100 + i, 1) for i in range(50)])
        self.assertFalse(c.available(49, 100))
        with self.assertRaises(CapacityError):
            c.reserve_many([("z%02d" % i, 200 + i, 201 + i, 1) for i in range(10)]
                           + [("over", 60, 61, 1)])
        self.assertEqual(len(c.allocations()), 50)


class Replace(unittest.TestCase):
    """REQ-B1 replace."""

    def test_capacity_excludes_own_old_booking(self):
        c = Calendar(1)
        c.reserve("a", 0, 10)
        c.replace("a", 5, 15)
        self.assertEqual(c.allocations(), [("a", 5, 15, 1)])
        c2 = Calendar(3)
        c2.reserve("a", 0, 10, 2)
        c2.reserve("b", 0, 10, 1)
        c2.replace("a", 0, 10, 2)
        with self.assertRaises(CapacityError):
            c2.replace("a", 0, 10, 3)
        self.assertEqual(c2.allocations(), [("a", 0, 10, 2), ("b", 0, 10, 1)])
        c2.replace("a", 5, 20)  # default units=1
        self.assertEqual(c2.allocations(), [("b", 0, 10, 1), ("a", 5, 20, 1)])

    def test_failed_replacement_retains_original(self):
        c = Calendar(2)
        c.reserve("a", 0, 10)
        c.reserve("b", 20, 30, 2)
        before = c.allocations()
        with self.assertRaises(CapacityError):
            c.replace("a", 25, 26)
        for args in (("a", 5, 5), ("a", 5, 4), ("a", 0, 1, 0), ("a", True, 3), ("", 0, 1)):
            with self.assertRaises(ValueError, msg=args):
                c.replace(*args)
        with self.assertRaises(ValueError):
            c.replace("missing", 0, 1)
        self.assertEqual(c.allocations(), before)

    def test_replace_cancelled_id_unknown(self):
        c = Calendar()
        c.reserve("a", 0, 1)
        c.cancel("a")
        with self.assertRaises(ValueError):
            c.replace("a", 0, 1)
        self.assertEqual(c.allocations(), [])


class WaitingList(unittest.TestCase):
    """REQ-B2."""

    def test_wait_validation_and_overcapacity(self):
        c = Calendar(2)
        c.wait("huge", 0, 10, 5)  # overcapacity accepted
        c.wait("neg", 0, 10, 1, -3)
        for args, kw in ((("", 0, 1), {}), (("x", 1, 1), {}), (("x", 0, 1, 0), {}),
                         (("x", 0, 1, True), {}), (("x", 0, 1), {"priority": True}),
                         (("x", 0, 1), {"priority": 1.5}), (("x", 0, 1), {"priority": "1"})):
            with self.assertRaises(ValueError, msg=(args, kw)):
                c.wait(*args, **kw)
        c.reserve("act", 0, 1)
        with self.assertRaises(ValueError):
            c.wait("act", 5, 6)
        with self.assertRaises(ValueError):
            c.wait("huge", 5, 6)
        self.assertEqual(c.waiting(), ["huge", "neg"])

    def test_waiting_order_priority_then_fifo(self):
        c = Calendar()
        c.wait("z", 0, 1, priority=0)
        c.wait("a", 0, 1, priority=0)
        c.wait("m", 0, 1, priority=5)
        c.wait("b", 0, 1, priority=-1)
        c.wait("c", 0, 1, priority=5)
        self.assertEqual(c.waiting(), ["m", "c", "z", "a", "b"])

    def test_waiting_consumes_no_capacity(self):
        c = Calendar(1)
        c.wait("w", 0, 10)
        self.assertTrue(c.available(0, 10))
        c.reserve("r", 0, 10)
        self.assertEqual(c.allocations(), [("r", 0, 10, 1)])
        self.assertEqual(c.waiting(), ["w"])

    def test_promote_skips_and_continues(self):
        c = Calendar(3)
        c.reserve("base", 0, 10, 1)
        c.wait("big", 0, 10, 3, priority=9)   # never fits while base active
        c.wait("p1", 0, 5, 2, priority=1)     # fits
        c.wait("p2", 3, 8, 1, priority=1)     # blocked by p1 at [3,5)
        c.wait("p3", 5, 10, 2, priority=0)    # fits after p1 ends
        c.wait("p4", 20, 30, 3, priority=-5)  # fits
        self.assertEqual(c.promote(), ["p1", "p3", "p4"])
        self.assertEqual(c.waiting(), ["big", "p2"])
        self.assertEqual(c.allocations(), [
            ("base", 0, 10, 1), ("p1", 0, 5, 2), ("p3", 5, 10, 2), ("p4", 20, 30, 3)])
        self.assertEqual(c.promote(), [])

    def test_earlier_success_blocks_later(self):
        c = Calendar(1)
        c.wait("second", 0, 2, priority=0)
        c.wait("first", 1, 3, priority=1)
        self.assertEqual(c.promote(), ["first"])
        self.assertEqual(c.waiting(), ["second"])

    def test_cancel_waiting_no_auto_promotion(self):
        c = Calendar(1)
        c.reserve("r", 0, 10)
        c.wait("w1", 0, 10)
        c.wait("w2", 0, 10)
        self.assertTrue(c.cancel("w1"))
        self.assertFalse(c.cancel("w1"))
        self.assertTrue(c.cancel("r"))
        self.assertEqual(c.allocations(), [])  # no auto promotion
        self.assertEqual(c.waiting(), ["w2"])
        self.assertEqual(c.promote(), ["w2"])
        self.assertEqual(c.waiting(), [])
        c.wait("w1", 0, 1)  # cancelled waiting id reusable

    def test_no_stealing_waiting_ids(self):
        c = Calendar(5)
        c.wait("w", 0, 1)
        with self.assertRaises(ValueError):
            c.reserve("w", 0, 1)
        with self.assertRaises(ValueError):
            c.reserve_many([("x", 0, 1, 1), ("w", 0, 1, 1)])
        with self.assertRaises(ValueError):
            c.replace("w", 0, 1)
        self.assertEqual(c.allocations(), [])
        self.assertEqual(c.waiting(), ["w"])

    def test_promoted_then_active_semantics(self):
        c = Calendar(2)
        c.wait("w", 0, 5, 2)
        c.promote()
        c.replace("w", 0, 5, 1)
        self.assertEqual(c.allocations(), [("w", 0, 5, 1)])
        with self.assertRaises(ValueError):
            c.wait("w", 10, 11)


class CheckpointAndVersion(unittest.TestCase):
    """REQ-B3."""

    def test_restore_bookings_queue_fifo(self):
        c = Calendar(2)
        c.reserve("a", 0, 10)
        c.wait("w1", 0, 10, 2, priority=1)
        c.wait("w2", 0, 10, 1, priority=1)
        c.wait("w3", 0, 10, 1, priority=5)
        cp = c.checkpoint()
        before = (c.allocations(), c.waiting())
        c.cancel("w3")
        c.cancel("a")
        c.reserve("b", 0, 1, 2)
        c.wait("w3", 50, 60)
        c.restore(cp)
        self.assertEqual((c.allocations(), c.waiting()), before)
        self.assertEqual(c.waiting(), ["w3", "w1", "w2"])
        c.wait("w4", 0, 1, priority=1)  # new waits queue behind restored ones
        self.assertEqual(c.waiting(), ["w3", "w1", "w2", "w4"])
        self.assertEqual(c.promote(), ["w3"])
        # checkpoint reusable and unaffected by later changes
        c.restore(cp)
        self.assertEqual((c.allocations(), c.waiting()), before)
        self.assertEqual(c.capacity, 2)

    def test_checkpoint_opaque_immutable_and_foreign(self):
        c, other = Calendar(), Calendar()
        cp = c.checkpoint()
        with self.assertRaises(AttributeError):
            cp._state = None
        with self.assertRaises(AttributeError):
            cp.anything = 1
        for bad in (other.checkpoint(), None, "cp", (), object()):
            with self.assertRaises(ValueError):
                c.restore(bad)
        self.assertEqual(c.version, 0)

    def test_version_rules(self):
        c = Calendar(2)
        self.assertEqual(c.version, 0)
        c.reserve("a", 0, 5); self.assertEqual(c.version, 1)
        with self.assertRaises(ValueError):
            c.reserve("a", 0, 5)
        with self.assertRaises(CapacityError):
            c.reserve("b", 0, 5, 2)
        self.assertEqual(c.version, 1)
        c.replace("a", 0, 5); self.assertEqual(c.version, 1)  # identical: no-op
        c.replace("a", 1, 5); self.assertEqual(c.version, 2)
        c.reserve_many([]); self.assertEqual(c.version, 2)
        c.reserve_many([("b", 10, 11, 1), ("c", 10, 11, 1)]); self.assertEqual(c.version, 3)
        c.wait("w1", 20, 21); c.wait("w2", 20, 21); c.wait("big", 0, 5, 9)
        self.assertEqual(c.version, 6)
        self.assertEqual(c.promote(), ["w1", "w2"]); self.assertEqual(c.version, 7)
        self.assertEqual(c.promote(), []); self.assertEqual(c.version, 7)
        self.assertFalse(c.cancel("nope")); self.assertEqual(c.version, 7)
        self.assertTrue(c.cancel("big")); self.assertEqual(c.version, 8)
        self.assertTrue(c.cancel("w1")); self.assertEqual(c.version, 9)
        cp = c.checkpoint()
        self.assertEqual(c.version, 9)
        c.restore(cp); self.assertEqual(c.version, 9)  # identical state: no-op
        c.cancel("a"); self.assertEqual(c.version, 10)
        c.restore(cp); self.assertEqual(c.version, 11)  # forward, never rewinds
        self.assertEqual([a[0] for a in c.allocations()], ["a", "b", "c", "w2"])
        with self.assertRaises(AttributeError):
            c.version = 0

    def test_cancel_after_restore_no_auto_promotion(self):
        c = Calendar(1)
        c.reserve("a", 0, 10)
        c.wait("w", 0, 10)
        cp = c.checkpoint()
        c.restore(cp)
        c.cancel("a")
        self.assertEqual(c.allocations(), [])
        self.assertEqual(c.waiting(), ["w"])


if __name__ == "__main__":
    unittest.main()
