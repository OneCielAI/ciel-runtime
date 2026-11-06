"""Bounded inbox projection reads and authoritative receipt selection."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox


class SideSummaryTests(unittest.TestCase):
    def test_recent_activity_reads_only_selected_records(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:summary')
            keys = []
            for number in range(25):
                key = inbox.receive('mail ' + str(number), 'event:' + str(number))['receipt_id']
                keys.append(key)
                os.utime(inbox._event_path('pending', key), (1_700_000_000 + number, 1_700_000_000 + number))
            with patch.object(inbox, '_read_file', wraps=inbox._read_file) as read:
                rows = inbox.summaries(3)
            self.assertEqual(keys[-3:][::-1], [row['receipt_id'] for row in rows])
            self.assertEqual(3, read.call_count)

    def test_receipt_wins_over_interrupted_pending_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:summary')
            key = inbox.receive('original', 'event')['receipt_id']
            pending = inbox._event_path('pending', key)
            row = inbox._load(key)
            row.update(stage='processed', result={'action': 'answer', 'reply': 'yes'})
            inbox._save_file(inbox._event_path('receipts', key), row)
            os.utime(pending, ns=(9_000_000_000, 9_000_000_000))
            rows = inbox.summaries(5)
            self.assertEqual(1, len(rows))
            self.assertEqual('processed', rows[0]['stage'])
            self.assertEqual('yes', rows[0]['result']['reply'])

    def test_empty_limit_reads_no_event(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:summary')
            inbox.receive('original', 'event')
            with patch.object(inbox, '_read_file', wraps=inbox._read_file) as read:
                self.assertEqual([], inbox.summaries(0))
            self.assertEqual(0, read.call_count)
