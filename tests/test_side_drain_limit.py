"""side_intake_service.drain honours its validated batch limit.

drain(limit=...) validates 1..100 and the MCP work_side_drain schema
advertises that range, but the summaries it returns were always the default
10 newest (side_intake_inbox.summaries(limit=10)), whatever the caller asked.
"""
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_service import drain

IDENTITY = 'claude:drain-limit'


class DrainLimitTests(unittest.TestCase):
    def seeded(self, raw, count):
        inbox = SideIntakeInbox(Path(raw), IDENTITY)
        for index in range(count):
            inbox.receive('side request %02d' % index, 'source:%02d' % index)
        return Path(raw)

    def test_drain_returns_exactly_the_requested_number_of_receipts(self):
        with tempfile.TemporaryDirectory() as raw:
            state = self.seeded(raw, 15)
            for limit, expected in ((1, 1), (3, 3), (10, 10), (12, 12), (15, 15), (100, 15)):
                with self.subTest(limit=limit):
                    rows = drain(state, IDENTITY, admit=False, limit=limit)
                    self.assertEqual(expected, len(rows))
                    self.assertFalse([row for row in rows if row.get('error')])

    def test_the_default_stays_ten(self):
        with tempfile.TemporaryDirectory() as raw:
            state = self.seeded(raw, 12)
            self.assertEqual(10, len(drain(state, IDENTITY, admit=False)))

    def test_an_out_of_range_limit_is_still_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            for limit in (0, 101, '5', True):
                with self.subTest(limit=limit), self.assertRaises(ValueError):
                    drain(Path(raw), IDENTITY, admit=False, limit=limit)


if __name__ == '__main__':
    unittest.main()
