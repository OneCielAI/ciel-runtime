import unittest
from booking_calendar import Calendar, CapacityError


class CalendarTests(unittest.TestCase):
    def test_half_open_cumulative_and_sorting(self):
        calendar = Calendar(3)
        calendar.reserve("z", -3, 2, 2)
        calendar.reserve("a", -3, 0)
        calendar.reserve("b", 2, 3, 3)
        self.assertEqual([row[0] for row in calendar.allocations()], ["a", "z", "b"])
        self.assertFalse(calendar.available(-1, 1, 2))
        self.assertTrue(calendar.available(0, 2))
        before = calendar.allocations()
        with self.assertRaises(CapacityError): calendar.reserve("bad", -2, 3)
        self.assertEqual(calendar.allocations(), before)

    def test_validation_and_cancel(self):
        for capacity in (True, 0, -1, 1.0):
            with self.assertRaises(ValueError): Calendar(capacity)
        calendar = Calendar()
        for args in [("", 0, 1, 1), ("a", True, 2, 1), ("a", 2, 2, 1), ("a", 0, 1, True)]:
            with self.assertRaises(ValueError): calendar.reserve(*args)
        calendar.reserve("a", 0, 1)
        with self.assertRaises(ValueError): calendar.reserve("a", 1, 2)
        self.assertTrue(calendar.cancel("a"))
        self.assertFalse(calendar.cancel("a"))

    def test_many_overlaps(self):
        calendar = Calendar(100)
        for i in range(100): calendar.reserve(str(i), i - 100, i + 100)
        self.assertFalse(calendar.available(-1, 1))
        self.assertEqual(len(calendar.allocations()), 100)
        self.assertTrue(calendar.available(200, 201, 100))

    def test_b1_atomic_batch(self):
        calendar = Calendar(2)
        calendar.reserve("original", 0, 5)
        before = calendar.allocations()
        for requests, error in [([("a", 0, 5, 1), ("b", 0, 5, 1)], CapacityError),
                                ([("a", 5, 6, 1), ("a", 6, 7, 1)], ValueError),
                                ([("a", 5, 6, 1), ("bad", 7, 7, 1)], ValueError)]:
            with self.assertRaises(error): calendar.reserve_many(iter(requests))
            self.assertEqual(calendar.allocations(), before)
        calendar.reserve_many([("a", 0, 5, 1), ("b", 5, 6, 2)])
        self.assertEqual(len(calendar.allocations()), 3)

    def test_b1_replace_excludes_old(self):
        calendar = Calendar()
        calendar.reserve("a", 0, 5)
        calendar.reserve("b", 5, 10)
        calendar.replace("a", -1, 5)
        before = calendar.allocations()
        with self.assertRaises(CapacityError): calendar.replace("a", 0, 6)
        with self.assertRaises(ValueError): calendar.replace("missing", 0, 1)
        self.assertEqual(calendar.allocations(), before)

    def test_b2_waiting_priority_fifo_and_skip(self):
        calendar = Calendar(2)
        calendar.wait("huge", 0, 5, 3, priority=9)
        calendar.wait("z", 0, 5, priority=1)
        calendar.wait("a", 0, 5, priority=1)
        calendar.wait("later", 0, 5, priority=-1)
        self.assertEqual(calendar.waiting(), ["huge", "z", "a", "later"])
        self.assertEqual(calendar.promote(), ["z", "a"])
        self.assertEqual(calendar.waiting(), ["huge", "later"])
        calendar.cancel("z")
        self.assertEqual(calendar.waiting(), ["huge", "later"])
        self.assertEqual(calendar.promote(), ["later"])
        self.assertTrue(calendar.cancel("huge"))
        self.assertFalse(calendar.cancel("huge"))

    def test_b2_wait_ids_protected(self):
        calendar = Calendar()
        calendar.wait("w", 0, 1)
        with self.assertRaises(ValueError): calendar.reserve("w", 1, 2)
        with self.assertRaises(ValueError): calendar.replace("w", 1, 2)
        with self.assertRaises(ValueError): calendar.reserve_many([("a", 2, 3, 1), ("w", 3, 4, 1)])
        with self.assertRaises(ValueError): calendar.wait("bad", 0, 1, priority=True)
        self.assertEqual(calendar.allocations(), [])
        self.assertEqual(calendar.waiting(), ["w"])

    def test_b3_version_counts_state_changes_once(self):
        calendar = Calendar(2)
        self.assertEqual(calendar.version, 0)
        calendar.reserve("a", 0, 5)
        calendar.reserve_many([])
        with self.assertRaises(CapacityError): calendar.reserve_many([("b", 0, 5, 1), ("c", 0, 5, 1)])
        self.assertEqual(calendar.version, 1)
        calendar.reserve_many([("b", 5, 6, 1), ("c", 6, 7, 1)])
        calendar.replace("a", 0, 5)
        self.assertEqual(calendar.version, 2)
        calendar.replace("a", 0, 4)
        calendar.wait("w1", 0, 1)
        calendar.wait("w2", 1, 2)
        self.assertEqual(calendar.version, 5)
        self.assertEqual(calendar.promote(), ["w1", "w2"])
        self.assertEqual(calendar.promote(), [])
        self.assertEqual(calendar.version, 6)
        self.assertFalse(calendar.cancel("missing"))
        self.assertTrue(calendar.cancel("w1"))
        self.assertEqual(calendar.version, 7)

    def test_b3_checkpoint_restore_exact_fifo_and_foreign(self):
        calendar = Calendar(1)
        calendar.reserve("a", 0, 5)
        calendar.wait("late", 0, 5, priority=1)
        calendar.wait("early", 0, 5, priority=1)
        calendar.wait("low", 10, 11, priority=-5)
        checkpoint = calendar.checkpoint()
        with self.assertRaises(AttributeError): checkpoint._state = None
        expected = (calendar.allocations(), calendar.waiting())
        calendar.cancel("late")
        calendar.wait("late", 0, 5, priority=1)
        calendar.cancel("a")
        calendar.promote()
        before = calendar.version
        calendar.restore(checkpoint)
        self.assertEqual((calendar.allocations(), calendar.waiting()), expected)
        self.assertEqual(calendar.waiting(), ["late", "early", "low"])
        self.assertEqual(calendar.version, before + 1)
        calendar.restore(checkpoint)
        self.assertEqual(calendar.version, before + 1)
        calendar.cancel("a")
        calendar.restore(checkpoint)
        self.assertEqual(calendar.version, before + 3)
        with self.assertRaises(ValueError): calendar.reserve("late", 20, 21)
        for foreign in (Calendar(1).checkpoint(), None, (1, (), ())):
            with self.assertRaises(ValueError): calendar.restore(foreign)
        self.assertEqual(calendar.version, before + 3)


if __name__ == "__main__":
    unittest.main()
