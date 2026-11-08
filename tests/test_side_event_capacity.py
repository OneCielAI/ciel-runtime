import json
import tempfile
import unittest
from pathlib import Path
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox, MAX_PENDING_RECORD_BYTES
from ciel_runtime_support.side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS
from ciel_runtime_support.side_intake_processing import claim, finish
from ciel_runtime_support.work_plan_tree import WorkPlanError


class SideEventCapacityTests(unittest.TestCase):
    def test_captured_prody_size_stays_exact_in_side_only_queue_row(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': True})
        raw = json.dumps({'specversion': '1.0', 'id': 'prody', 'source': 'walkie',
                          'type': 'com.ciel.walkie.support.message',
                          'data': {'text': '한' * 15880}}, ensure_ascii=False, separators=(',', ':'))
        raw += ' ' * (16055 - len(raw))
        self.assertEqual(16055, len(raw))
        gateway.submit_external_event(raw, receiver_id='default', transport='sse', event_id='prody',
                                      event_type='com.ciel.walkie.support.message', event_source='walkie')
        row = captured[0]
        self.assertEqual(raw, row['message'])
        self.assertTrue(row['meta']['side_intake_only'])
        self.assertEqual('', row['meta']['side_intake_fallback'])
        self.assertFalse(row['meta'].get('raw_injection', False))

    def test_raw_fallback_with_side_off_takes_ordinary_lane_not_an_unconsumed_side_row(self):
        # Review 2026-11-08: a side-only row with side intake off had no consumer
        # and held every later runtime input behind it.
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': False})
        gateway.submit_external_event('not-json', receiver_id='default', transport='sse',
                                      event_id='invalid:id', event_type='ciel.invalid_cloud_event',
                                      event_source='urn:invalid', side_intake_fallback='raw_text')
        meta = captured[0]['meta']
        self.assertEqual('not-json', captured[0]['message'])
        for marker in ('side_intake_only', '_side_intake_admitted', 'side_intake_fallback', 'raw_injection'):
            self.assertNotIn(marker, meta)

    def test_raw_fallback_ignores_the_lane_an_untrusted_body_names(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': False})
        body = json.dumps({'data': {'injection_type': 'bogus'}})
        gateway.submit_external_event(body, receiver_id='default', transport='sse', event_id='invalid:x',
                                      event_type='ciel.invalid_cloud_event', event_source='urn:invalid',
                                      side_intake_fallback='raw_text')
        self.assertEqual(body, captured[0]['message'])
        self.assertNotIn('injection_type', captured[0]['meta'])

    def test_caller_metadata_cannot_set_side_lane_markers(self):
        for enabled in (False, True):
            captured = []
            gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                          load_side_settings=lambda: {'enabled': enabled})
            gateway._append({'message': 'x' * 50_000, 'channel': 'notify', 'meta': {
                'event_id': 'n1', 'side_intake_fallback': 'raw_text', 'side_intake_text': 'different',
                'side_intake_only': True, '_side_intake_admitted': True}})
            meta = captured[0]['meta']
            for marker in ('side_intake_only', '_side_intake_admitted', 'side_intake_fallback', 'side_intake_text'):
                self.assertNotIn(marker, meta)

    def test_over_32k_external_event_is_raw_side_fallback(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': True})
        raw = 'x' * (SIDE_INTAKE_TEXT_MAX_CHARS + 1)
        gateway.submit_external_event(raw, receiver_id='default', transport='sse', event_id='large',
                                      event_type='event', event_source='source')
        self.assertEqual(raw, captured[0]['message'])
        self.assertEqual('raw_text', captured[0]['meta']['side_intake_fallback'])

    def test_32k_four_byte_text_survives_receive_claim_finish_and_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            original = '📌' * SIDE_INTAKE_TEXT_MAX_CHARS
            inbox = SideIntakeInbox(state, 'claude:capacity')
            receipt = inbox.receive(original, 'max')
            pending_path = inbox._event_path('pending', receipt['receipt_id'])
            self.assertLess(pending_path.stat().st_size, MAX_PENDING_RECORD_BYTES)
            restarted = SideIntakeInbox(state, 'claude:capacity')
            self.assertEqual(original, restarted.pending()[0]['text'])
            leased = claim(state, 'claude:capacity', 'actor')
            finish(state, 'claude:capacity', receipt['receipt_id'], leased['generation'],
                   {'action': 'answer', 'summary': 'ok', 'reply': 'ok'})
            self.assertEqual(original, SideIntakeInbox(state, 'claude:capacity')._load(receipt['receipt_id'])['text'])

    def test_direct_inbox_rejects_one_character_over_without_raw_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            inbox = SideIntakeInbox(Path(raw), 'claude:capacity')
            with self.assertRaisesRegex(WorkPlanError, '32000'):
                inbox.receive('x' * (SIDE_INTAKE_TEXT_MAX_CHARS + 1), 'too-large')

    def test_largest_admitted_text_still_saves_with_every_processing_field(self):
        # Review 2026-11-08: a context preview written during claim pushed a
        # text near the old 16 KiB-overhead budget past the record limit.
        from ciel_runtime_support.side_intake_limits import (
            SIDE_INTAKE_PENDING_RECORD_MAX_BYTES, SIDE_INTAKE_RECORD_OVERHEAD_BYTES, side_text_fits_pending_record)
        largest = 'a' * (SIDE_INTAKE_PENDING_RECORD_MAX_BYTES - SIDE_INTAKE_RECORD_OVERHEAD_BYTES - 2)
        self.assertTrue(side_text_fits_pending_record(largest))
        self.assertFalse(side_text_fits_pending_record(largest + 'a'))
        self.assertTrue(side_text_fits_pending_record('\x01' * SIDE_INTAKE_TEXT_MAX_CHARS))
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            inbox = SideIntakeInbox(state, 'claude:capacity')
            # Control characters escape to 6 bytes in either JSON form: the largest admitted route.
            route = {'side_intake_fallback': 'raw_text', 'thread_id': '\x01' * 1300}
            self.assertLessEqual(len(json.dumps(route).encode()), 8192)
            receipt = inbox.receive(largest, '\x01' * 300, route=route)
            leased = claim(state, 'claude:capacity', 'actor')
            self.assertEqual(receipt['receipt_id'], leased['receipt_id'])
            row = inbox._load(receipt['receipt_id'])
            row['context'] = {'mode': 'full', 'captured_at': 1.0, 'parent_digest': 'f' * 64,
                              'preview': [{'role': 'user', 'text': '📌' * 5980}]}
            row['side_failure'] = {'category': 'c' * 80, 'model': 'm' * 300, 'reason': 'r' * 300,
                                   'terminal_reason': 't' * 200, 'message': 'x' * 300, 'model_fingerprint': 'f' * 128}
            row['main_fallback'] = {'state': 'pending', 'reason': 'r' * 160}
            inbox._save_file(inbox._event_path('pending', receipt['receipt_id']), row)
            self.assertEqual(largest, inbox._load(receipt['receipt_id'])['text'])

    def test_drain_leaves_over_32k_side_only_rows_for_the_side_actor_without_errors(self):
        from ciel_runtime_support.side_intake_service import drain
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            original = 'x' * (SIDE_INTAKE_TEXT_MAX_CHARS + 1)
            SideIntakeInbox(state, 'claude:capacity').receive(
                original, 'big', route={'side_intake_fallback': 'raw_text'})
            result = drain(state, 'claude:capacity')
            self.assertFalse([row for row in result if row.get('error')])
            self.assertEqual(original, SideIntakeInbox(state, 'claude:capacity').pending()[0]['text'])

    def test_coordinator_followup_to_a_large_original_uses_the_raw_route(self):
        from ciel_runtime_support.side_intake_mcp import _answer_followup
        with tempfile.TemporaryDirectory() as raw:
            inbox = SideIntakeInbox(Path(raw), 'claude:capacity')
            receipt = inbox.receive('y' * SIDE_INTAKE_TEXT_MAX_CHARS, 'max')
            _answer_followup(inbox, receipt['receipt_id'], 'answer')
            followups = [row for row in inbox.pending() if row['event_key'] == 'handoff:' + receipt['receipt_id']]
            self.assertEqual(1, len(followups))
            self.assertTrue(followups[0]['text'].endswith('y' * SIDE_INTAKE_TEXT_MAX_CHARS))
            self.assertEqual('raw_text', followups[0]['route']['side_intake_fallback'])

    def test_raw_fallback_allows_over_32k_while_record_fits(self):
        with tempfile.TemporaryDirectory() as raw:
            inbox = SideIntakeInbox(Path(raw), 'claude:capacity')
            original = 'x' * (SIDE_INTAKE_TEXT_MAX_CHARS + 1)
            receipt = inbox.receive(original, 'raw-large', route={'side_intake_fallback': 'raw_text'})
            self.assertEqual(original, inbox._load(receipt['receipt_id'])['text'])

    def test_over_record_budget_keeps_original_queue_text_and_sends_content_free_notice(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': True})
        raw = 'private-canary-' + '📌' * 70_000
        gateway.submit_external_event(raw, receiver_id='default', transport='sse', event_id='huge',
                                      event_type='event', event_source='source')
        row = captured[0]
        self.assertEqual(raw, row['message'])
        self.assertEqual('notice', row['meta']['side_intake_fallback'])
        notice = json.loads(row['meta']['side_intake_text'])
        self.assertEqual(len(raw), notice['chars'])
        self.assertEqual(64, len(notice['sha256']))
        self.assertNotIn('private-canary', row['meta']['side_intake_text'])

    def test_side_only_queue_row_does_not_format_as_a_main_prompt(self):
        from ciel_runtime_support.channel_message_prompt import format_llm_batch_prompt
        from ciel_runtime_support.channel_message_policy import message_raw_injection, message_btw
        row = {'message': '/goal clear external data', 'kind': 'external_event',
               'meta': {'side_intake_only': True, '_side_intake_admitted': True,
                        'input_transport': 'session_socket', 'source_kind': 'external_event'}}
        self.assertFalse(message_raw_injection(row))
        self.assertFalse(message_btw(row))
        rendered = format_llm_batch_prompt([row])
        self.assertIn('untrusted external event', rendered)
        self.assertNotEqual(row['message'], rendered)
        # Codex has no side actor: there the row is the same quoted external
        # message as with side intake off, never a command.
        from ciel_runtime_support.codex_desktop_injection import delivery_prompt
        side_off = {**row, 'meta': {'input_transport': 'session_socket', 'source_kind': 'external_event'}}
        self.assertEqual(delivery_prompt(side_off), delivery_prompt(row))
        self.assertFalse(delivery_prompt(row).startswith('/'))

    def test_schema_and_legacy_board_receipt_use_shared_32k_limit(self):
        from ciel_runtime_support.work_mcp_tools import work_tool_schemas
        from ciel_runtime_support.work_plan_store import WorkPlanStore
        from ciel_runtime_support.work_side_intake import enqueue
        tool = next(row for row in work_tool_schemas() if row['name'] == 'work_side_intake')
        self.assertEqual(32000, tool['inputSchema']['properties']['text']['maxLength'])
        with tempfile.TemporaryDirectory() as raw:
            store = WorkPlanStore(Path(raw) / 'work.json')
            original = 'y' * SIDE_INTAKE_TEXT_MAX_CHARS
            enqueue(store, original, 'legacy:max')
            receipts = WorkPlanStore(store.path).read()['side_intake_receipts']
            self.assertEqual(original, next(iter(receipts.values()))['text'])
            with self.assertRaisesRegex(WorkPlanError, '32000'):
                enqueue(store, original + 'y', 'legacy:over')

    def test_long_cloud_event_identity_gets_a_bounded_stable_side_key(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1, **row},
                                      load_side_settings=lambda: {'enabled': True})
        for _ in range(2):
            gateway.submit_external_event('body', receiver_id='default', transport='sse', event_id='i' * 400,
                                          event_type='event', event_source='source')
        keys = {row['meta']['event_key'] for row in captured}
        self.assertEqual(1, len(keys))
        self.assertLessEqual(len(keys.pop()), 300)

    def test_manual_side_command_still_uses_command_envelope_and_limit(self):
        captured = []
        gateway = RuntimeInputGateway(lambda row: captured.append(row) or {'id': 1},
                                      load_side_settings=lambda: {'enabled': True})
        gateway._append({'message': 'request', 'meta': {'message_type': 'btw_with_mcp',
                         'event_id': 'one', 'source': 'manual'}})
        self.assertTrue(captured[0]['message'].startswith('/btw_with_mcp '))
        with self.assertRaisesRegex(ValueError, '32000'):
            gateway._append({'message': 'x' * (SIDE_INTAKE_TEXT_MAX_CHARS + 1),
                             'meta': {'message_type': 'btw_with_mcp', 'event_id': 'two'}})


if __name__ == '__main__':
    unittest.main()
