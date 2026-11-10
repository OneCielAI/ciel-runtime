"""Web-safe Side inbox receipt projection and change watermark."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, fail, finish
from ciel_runtime_support.side_reply_outbox import prepare, record
from ciel_runtime_support.work_plan_tree import WorkPlanError


class SideReceiptProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.identity = 'claude:web-receipts'
        self.inbox = SideIntakeInbox(self.state, self.identity)

    def tearDown(self):
        self.temp.cleanup()

    def receive(self, number, *, priority=None, text=None, route=None):
        body = {'type': 'message.created', 'data': {'text': text or f'message {number}'}}
        if priority:
            body['data']['priority'] = priority
        return self.inbox.receive(json.dumps(body), f'event:{number}', route=route)

    def test_newest_keyset_page_filters_and_deduplicates_interrupted_unlink(self):
        keys = []
        for number in range(7):
            receipt = self.receive(number, priority='P1' if number in (1, 5) else 'P3')
            keys.append(receipt['receipt_id'])
            path = self.inbox._event_path('pending', receipt['receipt_id'])
            os.utime(path, ns=(1_700_000_000_000_000_000 + number,
                               1_700_000_000_000_000_000 + number))
        # A committed receipt wins over a stale pending file for the same id.
        row = self.inbox._load(keys[6])
        row.update(stage='processed', consumed_at=20, result={'action': 'answer', 'kind': 'info',
                   'reply': 'done', 'delivery': 'submitted', 'handoff': 'not_required'})
        receipt_path = self.inbox._event_path('receipts', keys[6])
        self.inbox._save_file(receipt_path, row)
        os.utime(receipt_path, ns=(1_800_000_000_000_000_000, 1_800_000_000_000_000_000))

        first = self.inbox.receipt_page(limit=3)
        self.assertEqual(3, len(first['items']))
        self.assertEqual(keys[6], first['items'][0]['receipt_id'])
        self.assertEqual('processed', first['items'][0]['stage'])
        self.assertTrue(first['has_more'])
        second = self.inbox.receipt_page(limit=3, cursor=first['next_cursor'])
        self.assertFalse({row['receipt_id'] for row in first['items']} &
                         {row['receipt_id'] for row in second['items']})
        self.assertEqual(7, first['total'])

        p1 = self.inbox.receipt_page(limit=10, priority='P1')
        self.assertEqual({keys[1], keys[5]}, {row['receipt_id'] for row in p1['items']})
        searched = self.inbox.receipt_page(limit=10, search='message 4')
        self.assertEqual([keys[4]], [row['receipt_id'] for row in searched['items']])
        processed = self.inbox.receipt_page(limit=10, stage='processed')
        self.assertEqual([keys[6]], [row['receipt_id'] for row in processed['items']])

    def test_summary_and_detail_remove_secrets_and_host_internal_fields(self):
        original = json.dumps({'type': 'message.created', 'authorization': 'Bearer original-secret',
            'data': {'text': 'please use api_key=ABCDEFGH12345678', 'reply_token': 'original-token',
                     'cookie': 'private-cookie', 'activity_context': {'preview': 'main transcript'},
                     'message_id': 'm1', 'conversation_id': 'c1'}})
        receipt = self.inbox.receive(original, 'event:secret', route={
            'source_kind': 'web_chat', 'reply_token': 'route-token', 'authorization': 'route-secret',
            'channel': 'web'})
        key = receipt['receipt_id']
        row = self.inbox._load(key)
        row.update(processing={'owner': 'actor', 'generation': 'claim-generation',
                               'started_at': 10, 'lease_until': 20},
                   context={'mode': 'full', 'preview': [{'text': 'main transcript'}]},
                   main_fallback={'state': 'pending', 'reason': 'safe reason',
                                  'generation': 'fallback-generation', 'delivery_id': 'private-delivery'},
                   outbox={'state': 'submitting', 'attempt_id': 'outbox-attempt'},
                   side_failure={'category': 'model_error', 'attempt_count': 2,
                                 'model': 'model-name', 'reason': 'Bearer FAILURESECRET'})
        row['stage'] = 'processed'
        row['result'] = {'action': 'answer', 'kind': 'info', 'summary': 'safe summary',
                         'reply': 'safe reply; password=supersecret99',
                         'final_text': 'safe final', 'delivery': 'submitted',
                         'handoff': 'not_required', 'reported_sends': ['mcp__private']}
        self.inbox._save_file(self.inbox._event_path('receipts', key), row)
        self.inbox._event_path('pending', key).unlink(missing_ok=True)

        page = self.inbox.receipt_page(limit=10)
        detail = self.inbox.receipt_detail(key)
        rendered = json.dumps({'page': page, 'detail': detail})
        for forbidden in ('Bearer original-secret', 'ABCDEFGH12345678', 'original-token',
                          'private-cookie', 'main transcript', 'claim-generation',
                          'fallback-generation', 'private-delivery', 'outbox-attempt',
                          'mcp__private', 'supersecret99'):
            self.assertNotIn(forbidden, rendered)
        for forbidden_key in ('reply_token', 'authorization', 'cookie', 'activity_context',
                              'generation', 'attempt_id', 'context', 'route'):
            self.assertNotIn(forbidden_key, rendered)
        self.assertEqual('model_error', detail['failure']['category'])
        self.assertEqual('safe final', detail['decision']['final_text'])
        self.assertEqual('m1', detail['source']['id'])
        self.assertIn('‹hidden›', detail['original'])

    def test_full_original_and_replies_are_bounded(self):
        huge = 'A' * 20_000
        key = self.inbox.receive(huge, 'event:huge')['receipt_id']
        row = self.inbox._load(key)
        row.update(stage='processed', result={'action': 'answer', 'summary': 'S' * 20_000,
                                              'reply': 'R' * 20_000, 'final_text': 'F' * 20_000})
        self.inbox._save_file(self.inbox._event_path('receipts', key), row)
        detail = self.inbox.receipt_detail(key)
        self.assertTrue(detail['original_truncated'])
        self.assertLessEqual(len(detail['original']), 16_000)
        self.assertLessEqual(len(detail['decision']['reply']), 4_000)
        self.assertLessEqual(len(detail['decision']['summary']), 4_000)

    def test_invalid_cursor_and_detail_identity_fail_closed(self):
        for cursor in ('bad', '1-not-hex', '1-' + 'a' * 65):
            with self.assertRaises(WorkPlanError):
                self.inbox.receipt_page(cursor=cursor)
        for receipt_id in ('', '../secrets', 'A' * 64):
            with self.assertRaises(WorkPlanError):
                self.inbox.receipt_detail(receipt_id)
        self.assertIsNone(self.inbox.receipt_detail('a' * 64))

    def test_unreadable_selected_row_fails_closed(self):
        key = self.receive(1)['receipt_id']
        path = self.inbox._event_path('pending', key)
        path.write_text('{not json', encoding='utf-8')
        with self.assertRaises(WorkPlanError):
            self.inbox.receipt_page(limit=10)

    def receipt_reads(self, read):
        return [call for call in read.call_args_list
                if call.args and Path(call.args[0]).parent.parent.name in ('pending', 'receipts')]

    def test_counts_cover_every_receipt_and_repeat_requests_reread_nothing(self):
        for number in range(30):
            self.receive(number, priority='P1' if number % 10 == 0 else None)
        with patch.object(self.inbox, '_read_file', wraps=self.inbox._read_file) as read:
            page = self.inbox.receipt_page(limit=5)
        self.assertEqual(30, len(self.receipt_reads(read)))
        self.assertEqual((5, True, 30), (len(page['items']), page['has_more'], page['counts']['received']))
        self.assertTrue(page['counts_complete'])
        with patch.object(self.inbox, '_read_file', wraps=self.inbox._read_file) as read:
            p1 = self.inbox.receipt_page(limit=2, priority='P1')
            older = self.inbox.receipt_page(limit=2, priority='P1', cursor=p1['next_cursor'])
        self.assertEqual([], self.receipt_reads(read))
        self.assertEqual((2, True), (len(p1['items']), p1['has_more']))
        # The last P1 match ends the scan exactly: no phantom "more" page.
        self.assertEqual((1, False, None), (len(older['items']), older['has_more'], older['next_cursor']))
        # Counts are filter-independent.
        self.assertEqual(page['counts'], p1['counts'])

    def test_stage_follows_claim_lease_and_committed_finish(self):
        key = self.receive(1)['receipt_id']
        picked = claim(self.state, self.identity, 'actor', now=1_000, lease_seconds=60)
        self.assertEqual(key, picked['receipt_id'])
        rows = {now: self.inbox._public_summary(key, self.inbox._load(key), now=now)['stage']
                for now in (1_030, 1_061)}
        # claim() reclaims an expired lease, so the web must not call it processing.
        self.assertEqual({1_030: 'processing', 1_061: 'received'}, rows)
        finish(self.state, self.identity, key, picked['generation'], {
            'action': 'answer', 'item_id': None, 'summary': 's', 'reply': 'r', 'delivery': 'pending'},
            now=1_040)
        self.assertIn('processing', self.inbox._load(key))  # retained as the duplicate-finish fence
        self.assertEqual('processed', self.inbox.receipt_page(limit=5)['items'][0]['stage'])

    def test_cached_projection_rereads_changed_records_only(self):
        keys = [self.receive(number)['receipt_id'] for number in range(4)]
        self.inbox.receipt_page(limit=10)
        claim(self.state, self.identity, 'actor')
        with patch.object(self.inbox, '_read_file', wraps=self.inbox._read_file) as read:
            page = self.inbox.receipt_page(limit=10)
        self.assertEqual(1, len(self.receipt_reads(read)))
        self.assertEqual(1, [row['stage'] for row in page['items']].count('processing'))
        self.assertEqual(set(keys), {row['receipt_id'] for row in page['items']})

    def test_watermark_write_survives_a_reader_holding_the_marker_open(self):
        import threading
        self.receive(1)
        before = self.inbox.watermark()
        marker = self.inbox.directory / SideIntakeInbox.WATERMARK_FILE
        reader = marker.open('rb')
        writer = threading.Thread(target=SideIntakeInbox._touch_watermark, args=(self.inbox.directory,))
        writer.start()
        threading.Event().wait(0.01)
        reader.close()
        writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertNotIn(self.inbox.watermark(), (before, None, ''))
        self.assertEqual([], list(self.inbox.directory.glob('.web-watermark.*.tmp')))

    def test_concurrent_receipt_writes_always_leave_a_new_watermark(self):
        import concurrent.futures
        before = self.inbox.watermark()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda number: self.receive(number), range(24)))
        self.assertEqual(24, self.inbox.receipt_page(limit=100)['total'])
        self.assertNotIn(self.inbox.watermark(), (before, None))
        self.assertEqual([], list(self.inbox.directory.glob('.web-watermark.*.tmp')))

    def test_watermark_changes_across_receive_claim_fail_finish_and_outbox(self):
        first = self.receive(1, route={'source_kind': 'web_chat', 'channel': 'web',
                                      'reply_token': 'r1'})
        w_receive = self.inbox.watermark()
        claimed = claim(self.state, self.identity, 'actor', model_fingerprint='m')
        w_claim = self.inbox.watermark()
        fail(self.state, self.identity, first['receipt_id'], claimed['generation'], {
            'category': 'model_error', 'reason': 'transient', 'model_fingerprint': 'm'})
        w_fail = self.inbox.watermark()
        self.assertEqual(3, len({w_receive, w_claim, w_fail}))

        second = self.receive(2, route={'source_kind': 'web_chat', 'channel': 'web',
                                       'reply_token': 'r2'})
        claimed = claim(self.state, self.identity, 'actor-2')
        result = {'action': 'answer', 'kind': 'info', 'item_id': None, 'summary': 'done',
                  'reply': 'answer', 'final_text': 'answer', 'delivery': 'pending',
                  'execution_approved': False, 'handoff': 'not_required'}
        finish(self.state, self.identity, second['receipt_id'], claimed['generation'], result)
        w_finish = self.inbox.watermark()
        outgoing = prepare(self.state, self.identity, second['receipt_id'])
        w_prepare = self.inbox.watermark()
        record(self.state, self.identity, second['receipt_id'], 'submitted',
               attempt_id=outgoing['attempt_id'])
        w_record = self.inbox.watermark()
        self.assertEqual(3, len({w_finish, w_prepare, w_record}))


if __name__ == '__main__':
    unittest.main()
