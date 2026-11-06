"""Mailbox filesystem and crash boundaries, independent of native UI."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox


class MailboxTests(unittest.TestCase):
    def test_consumption_removes_mail_but_keeps_dedup_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            receipt = inbox.receive('original', 'event')
            key = receipt['receipt_id']
            self.assertTrue(inbox._event_path('pending', key).exists())
            inbox.link(key, 'n1')
            self.assertFalse(inbox._event_path('pending', key).exists())
            self.assertTrue(inbox._event_path('receipts', key).exists())
            restarted = SideIntakeInbox(Path(directory), 'claude:mailbox')
            self.assertTrue(restarted.receive('original', 'event')['duplicate'])
            self.assertEqual([], restarted.pending())

    def test_receipt_write_failure_preserves_pending_original(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            receipt = inbox.receive('original', 'event')
            with patch.object(inbox, '_save_file', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    inbox.link(receipt['receipt_id'], 'n1')
            self.assertEqual('original', inbox.pending()[0]['text'])

    def test_crash_after_receipt_before_unlink_reconciles(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            receipt = inbox.receive('original', 'event')
            original = Path.unlink
            def fail_pending(path, *args, **kwargs):
                if path.parent.parent.name == 'pending':
                    raise OSError('crash at unlink')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'unlink', fail_pending):
                with self.assertRaises(OSError):
                    inbox.link(receipt['receipt_id'], 'n1')
            self.assertEqual([], inbox.pending())
            self.assertTrue(inbox.receive('original', 'event')['duplicate'])

    def test_legacy_migration_preserves_source_and_content(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            import hashlib
            key = hashlib.sha256(b'event').hexdigest()
            row = {'text': 'original', 'event_key': 'event',
                   'digest': hashlib.sha256(b'original').hexdigest(),
                   'stage': 'received', 'item_id': None, 'received_at': 1,
                   'external_ack': 'not_sent'}
            inbox.path.parent.mkdir(parents=True)
            raw = json.dumps({'version': 1, 'requests': {key: row}})
            inbox.path.write_text(raw, encoding='utf8')
            self.assertEqual('original', inbox.pending()[0]['text'])
            self.assertEqual(raw, inbox.path.read_text(encoding='utf8'))
            self.assertTrue(inbox.receive('original', 'event')['duplicate'])

    def test_concurrent_duplicate_delivery_creates_one_pending_file(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            inbox.pending()  # Complete legacy initialization before concurrent writes.
            def receive(_):
                return inbox.receive('original', 'event')
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                receipts = list(pool.map(receive, range(12)))
            self.assertEqual(1, sum(not row['duplicate'] for row in receipts))
            self.assertEqual(1, len(inbox.pending()))

    def test_pending_keyset_rotation_reaches_independent_later_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            for number in range(30):
                inbox.receive('mail ' + str(number), 'event:' + str(number))
            seen = set()
            after = ''
            for _ in range(6):
                page = inbox.pending(5, after=after)
                seen.update(row['receipt_id'] for row in page)
                after = page[-1]['receipt_id']
            self.assertEqual(30, len(seen))

    def test_processed_unicode_payload_remains_readable_after_pending_consumed(self):
        from ciel_runtime_support.side_intake_processing import claim, finish
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = SideIntakeInbox(root, 'claude:mailbox')
            original = '\U0001f4cc' * 16000
            key = inbox.receive(original, 'unicode')['receipt_id']
            leased = claim(root, 'claude:mailbox', 'actor')
            finish(root, 'claude:mailbox', key, leased['generation'], {
                'action': 'answer', 'summary': '\U0001f4cc' * 16000,
                'reply': '\U0001f4cc' * 16000, 'delivery': 'pending'})
            loaded = inbox._load(key)
            self.assertEqual('processed', loaded['stage'])
            self.assertEqual(original, loaded['text'])
            self.assertFalse(inbox._event_path('pending', key).exists())
            self.assertEqual(1, len(inbox.summaries()))

    def test_oversized_receipt_never_replaces_valid_pending_original(self):
        from ciel_runtime_support.side_intake_processing import claim, finish
        from ciel_runtime_support.side_intake_inbox import MAX_RECEIPT_BYTES
        from ciel_runtime_support.work_plan_tree import WorkPlanError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inbox = SideIntakeInbox(root, 'claude:mailbox')
            key = inbox.receive('original', 'oversized')['receipt_id']
            leased = claim(root, 'claude:mailbox', 'actor')
            with self.assertRaises(WorkPlanError):
                finish(root, 'claude:mailbox', key, leased['generation'], {'reply': 'x' * MAX_RECEIPT_BYTES})
            self.assertEqual('original', inbox._load(key)['text'])
            self.assertEqual('received', inbox._load(key)['stage'])
            self.assertTrue(inbox._event_path('pending', key).exists())
            self.assertFalse(inbox._event_path('receipts', key).exists())

    def test_more_than_hundred_events_are_durable_and_page_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = SideIntakeInbox(Path(directory), 'claude:mailbox')
            for number in range(150):
                inbox.receive('original ' + str(number), 'event:' + str(number))
            self.assertEqual(150, len(list((inbox.directory / 'pending').glob('*/*.json'))))
            self.assertEqual(17, len(inbox.pending(17)))
            self.assertEqual(5, len(inbox.summaries(5)))
            self.assertFalse(inbox.path.exists())
