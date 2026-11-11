"""Large host Work results are paged instead of exceeding the native MCP result limit."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import host_result_pages as pages
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.work_plan_store import session_work_plan_store

TOKEN = 'paging-host-token-' * 3
IDENTITY = 'claude:paging-fixture'


def respond(_request_id, text, is_error=False):
    return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}


class HostResultPageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.config = {'work_mode': True, 'work_parallel_host': {'token': TOKEN},
                       'work_side_intake': {'enabled': True, 'type': 'btw_with_mcp'}}

    def call(self, name, args):
        return dispatch_work_tool(1, name, {**args, 'session_id': IDENTITY}, load_config=lambda: self.config,
                                  state_dir=self.state, respond=respond)

    def reassemble(self, result):
        envelope = json.loads(result['content'][0]['text'])['paged']
        pieces = []
        for part in range(envelope['parts']):
            page = self.call('work_host_page', {'id': envelope['id'], 'part': part, '_host': {'token': TOKEN}})
            self.assertFalse(page['isError'], page)
            text = page['content'][0]['text']
            self.assertLessEqual(len(text), 2 * pages.PAGE_CHARS + 200)
            pieces.append(json.loads(text)['text'])
        whole = ''.join(pieces)
        self.assertEqual(envelope['id'], hashlib.sha256(whole.encode('utf-8')).hexdigest())
        return json.loads(whole)

    def big_board(self, roots=333):
        store = session_work_plan_store(self.state, IDENTITY)

        def grow(doc):
            for index in range(roots):
                tree.add_node(doc, None, '대기 목표 ' + str(index) + ' ' + '원요청 검토 ' * 10, note='진행 메모 ' * 40)
        store.transaction(grow)

    def test_small_results_are_unchanged(self):
        self.assertEqual('{"a": 1}', pages.bound(self.state, IDENTITY, '{"a": 1}'))

    def test_large_side_claim_is_paged_and_reassembles_exactly(self):
        self.big_board()
        from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
        self.config['work_side_intake']['context_mode'] = 'isolated'
        SideIntakeInbox(self.state, IDENTITY).receive('question', 'source:event-1', route={})
        result = self.call('work_side_claim', {'owner': 'side-1', '_host': {'token': TOKEN}})
        self.assertFalse(result['isError'])
        self.assertLessEqual(len(result['content'][0]['text']), 400)
        claim = self.reassemble(result)['claim']
        self.assertEqual('question', claim['text'])
        self.assertEqual(334, len(claim['workload']['nodes']) + 1)
        self.assertIs(False, claim['workload']['truncated'])

    def test_large_status_pages_only_for_an_authenticated_host(self):
        self.big_board()
        paged = self.call('work_status', {'format': 'json', '_host': {'token': TOKEN}})
        self.assertIn('"paged"', paged['content'][0]['text'])
        self.assertEqual(333, len(self.reassemble(paged)['root_index']))
        # Without a valid host token the router answers as before (the model's own call).
        for args in ({'format': 'json'}, {'format': 'json', '_host': {'token': 'x' * 40}}):
            with self.subTest(args=args):
                plain = self.call('work_status', args)
                self.assertNotIn('"paged"', plain['content'][0]['text'][:20])
                self.assertEqual(333, len(json.loads(plain['content'][0]['text'])['root_index']))

    def test_pages_are_scoped_to_the_session_and_reject_bad_requests(self):
        text = json.dumps({'big': 'é' * 30000})
        envelope = json.loads(pages.bound(self.state, IDENTITY, text))['paged']
        with self.assertRaises(tree.WorkPlanError):
            pages.page(self.state, 'claude:other-session', envelope['id'], 0)
        for page_id, part in ((envelope['id'], envelope['parts']), ('../x', 0), (envelope['id'], '0')):
            with self.subTest(page_id=page_id, part=part), self.assertRaises(tree.WorkPlanError):
                pages.page(self.state, IDENTITY, page_id, part)
        denied = self.call('work_host_page', {'id': envelope['id'], 'part': 0, '_host': {'token': 'y' * 40}})
        self.assertTrue(denied['isError'])

    def test_prune_removes_expired_payloads_and_orphan_lock_sidecars(self):
        import os
        import time
        directory = pages._directory(self.state, IDENTITY)
        directory.mkdir(parents=True)
        old = time.time() - pages.TTL_SECONDS - 10
        expired, live = directory / ('a' * 64 + '.json'), directory / ('b' * 64 + '.json')
        expired_lock, live_lock = Path(str(expired) + '.io.lock'), Path(str(live) + '.io.lock')
        orphan_lock = directory / ('c' * 64 + '.json.io.lock')
        for path in (expired, live, expired_lock, live_lock, orphan_lock):
            path.write_text('{}', encoding='utf-8')
        for path in (expired, expired_lock, orphan_lock):
            os.utime(path, (old, old))
        pages._prune(directory)
        self.assertFalse(expired.exists() or expired_lock.exists() or orphan_lock.exists())
        self.assertTrue(live.exists() and live_lock.exists())

    def test_pages_respect_the_byte_budget_for_multibyte_text(self):
        text = '가' * 50000
        parts = pages._split(text)
        self.assertEqual(text, ''.join(parts))
        self.assertTrue(all(len(part.encode('utf-8')) <= pages.PAGE_BYTES for part in parts))

    def test_digest_matches_text_encoder_for_lone_surrogates(self):
        text = json.dumps({'value': '\ud800' + 'a' * 20000}, ensure_ascii=False)
        envelope = json.loads(pages.bound(self.state, IDENTITY, text))['paged']
        encoder_bytes = text.replace('\ud800', '�').encode('utf-8')
        self.assertEqual(hashlib.sha256(encoder_bytes).hexdigest(), envelope['id'])
        whole = ''.join(pages.page(self.state, IDENTITY, envelope['id'], part)['text']
                        for part in range(envelope['parts']))
        self.assertEqual(encoder_bytes, whole.encode('utf-8'))


if __name__ == '__main__':
    unittest.main()
