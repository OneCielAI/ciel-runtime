"""Unit tests for raw_text_inventory on the recorded 378f6d73 runs, plus negatives.

The positive cases run the inventory on the real main transcripts and raw CloudEvents
of run-a-20261111-161820 and run-b-20261111-162135 (read-only). The negatives
mutate run B's real wake and review rows.
Run: py -3.11 -B -m unittest test_raw_text_inventory -v
"""
import sys
sys.dont_write_bytecode = True
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import raw_text_inventory as rti  # noqa: E402

REPO = Path(os.environ.get('RTI_RECHECK_REPO', r'C:\Users\djlov\ciel-runtime\.local\tmp\work-parallel'))
E2E_SOURCE = REPO / 'docs/journal/2026/11/11/features/sse-native-fork/e2e-source'
RUN_A = E2E_SOURCE / 'run-a-20261111-161820'
RUN_B = E2E_SOURCE / 'run-b-20261111-162135'
RECHECK = E2E_SOURCE / 'recheck-378f6d73/raw-text-inventory-recheck.json'
SNAPSHOT = REPO / '.local/sse-true-fork/e2e/snapshot-378f6d73'
NEXT_SNAPSHOT = REPO / '.local/sse-true-fork/e2e/snapshot-0d1b4afc'


def _driver_constants():
    spec = importlib.util.spec_from_file_location('driver_constants', HERE / 'walkie_e2e_driver_v4.py')
    module = importlib.util.module_from_spec(spec)
    argv = sys.argv
    try:
        sys.argv = [argv[0]]
        spec.loader.exec_module(module)
    finally:
        sys.argv = argv
    return module


D = _driver_constants()
SOURCES = {'1000': D.ROOT_TEXT, '1001': D.PRIOR_TEXT, '1002': D.TARGET_TEXT, '1003': D.REPLY_ONE, '1004': D.REPLY_TWO}


def transcript(run):
    return [json.loads(line) for line in (run / 'evidence/main-transcript.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


def raw_events(run):
    inbox = run / 'evidence/side-inboxes'
    events = []
    for path in sorted(list(inbox.glob('*/receipts/*/*.json')) + list(inbox.glob('*/pending/*/*.json'))):
        text = json.loads(path.read_text(encoding='utf-8')).get('text')
        if isinstance(text, str) and text.startswith('{"specversion"'):
            events.append(text)
    return events


def key(row):
    return (row['source_message_id'], row['source_offset'], row['destination_field'], row['destination_offset'], row['length'], row['sha256'])


ROWS_B = transcript(RUN_B)
EVENTS_B = raw_events(RUN_B)
WAKE_ROW, REVIEW_ROW = 61, 81
EXCERPT_56 = D.TARGET_TEXT[194:250]


def get_text(row):
    content = row['message']['content']
    return content if isinstance(content, str) else content[0]['text']


def set_text(row, text):
    if isinstance(row['message']['content'], str):
        row['message']['content'] = text
    else:
        row['message']['content'][0]['text'] = text


def wake_parts(row):
    text = get_text(row)
    start = text.index(rti.WAKE_MARKER) + len(rti.WAKE_MARKER)
    value, end = json.JSONDecoder().raw_decode(text, start)
    return text[:start], value, text[end:]


def mutated_wake(mutate, suffix_extra=''):
    row = copy.deepcopy(ROWS_B[WAKE_ROW])
    head, value, tail = wake_parts(row)
    mutate(value)
    set_text(row, head + json.dumps(value, ensure_ascii=False, separators=(',', ':')) + tail + suffix_extra)
    return row


def review_parts(row):
    text = get_text(row)
    start = text.index('[{')
    value, end = json.JSONDecoder().raw_decode(text, start)
    return text[:start], value, text[end:]


def mutated_review(mutate):
    row = copy.deepcopy(ROWS_B[REVIEW_ROW])
    head, value, tail = review_parts(row)
    mutate(value)
    set_text(row, head + json.dumps(value, ensure_ascii=False, separators=(',', ':')) + tail)
    return row


def inventory(rows, events=EVENTS_B):
    return rti.inventory_main_rows(rows, SOURCES, events)


def only(row):
    return inventory([row])


class RecheckDataTest(unittest.TestCase):
    def test_run_b_passes_and_reproduces_recorded_excerpts(self):
        self.assertEqual(len(EVENTS_B), 1)
        report = inventory(ROWS_B)
        self.assertTrue(report['passed'], report['failures'])
        self.assertEqual([(i['row'], i['kind']) for i in report['injected']], [(WAKE_ROW, 'wake'), (REVIEW_ROW, 'review')])
        recorded = json.loads(RECHECK.read_text(encoding='utf-8'))['runs']['run-b-20261111-162135']['host_injected_rows']
        for item, saved in zip(report['injected'], recorded):
            self.assertEqual(item['row'], saved['row'])
            self.assertEqual(sorted(key(m) for m in item['fields']['matches']), sorted(key(m) for m in saved['matches']))
            self.assertEqual(item['fields']['disallowed_matches'], [])
            self.assertEqual(item['fields']['bounds_exceeded'], [])
        found = {(m['source_message_id'], m['source_offset'], m['length'], m['destination_field'])
                 for item in report['injected'] for m in item['fields']['matches']}
        self.assertIn(('1002', 72, 29, 'wake.summary'), found)
        self.assertIn(('1002', 72, 29, 'review.summary'), found)
        self.assertIn(('1002', 194, 56, 'wake.time_limit.text'), found)

    def test_run_b_main_own_read_is_an_observation_not_a_failure(self):
        report = inventory(ROWS_B)
        self.assertEqual([r['row'] for r in report['observations']['main_own_source_reads']], [91])
        whole = [r for r in report['observations']['main_tool_results_with_source_text'] if r['whole_source_messages']]
        self.assertEqual([(r['row'], r['tool'], r['whole_source_messages']) for r in whole],
                         [(92, 'mcp__walkie__walkie_read_messages', ['1000', '1001', '1002'])])
        self.assertEqual(report['failures'], [])

    def test_run_a_passes_with_no_excerpts(self):
        rows = transcript(RUN_A)
        events = raw_events(RUN_A)
        self.assertGreaterEqual(len(events), 1)
        report = inventory(rows, events)
        self.assertTrue(report['passed'], report['failures'])
        self.assertEqual([(i['row'], i['kind']) for i in report['injected']], [(65, 'wake'), (72, 'review')])
        # No thread-message excerpt. The scripted summary shares 25 characters with the
        # none_referenced event text ('ynthetic readiness review'), in allowed summaries only.
        matches = [m for i in report['injected'] for m in i['fields']['matches']]
        self.assertEqual(sorted((m['destination_field'], m['source_offset'], m['length']) for m in matches),
                         [('review.summary', 34, 25), ('wake.summary', 34, 25)])
        self.assertTrue(all(m['source_message_id'].startswith('event:') and m['allowed'] for m in matches))
        # Row 62: prompt.submit context carried the pending handoff and review views; its only
        # excerpt is a 25-character overlap with the none_referenced event text, in wake.summary.
        # Row 66 is the same prompt.submit context as Claude Code's hook_additional_context attachment.
        for row in (62, 66):
            context = [c for c in report['request_context'] if c['row'] == row]
            self.assertEqual(len(context), 1, row)
            self.assertEqual(context[0]['views'], {'wake_views': 1, 'review_views': 1})
            self.assertTrue(context[0]['passed'])
        self.assertEqual(report['observations']['attachment_rows_with_source_text'], [])
        self.assertTrue(all(m['allowed'] for c in report['request_context'] for m in c['fields']['matches']))

    def test_recorded_rows_are_allowed_under_the_ruling(self):
        recorded = json.loads(RECHECK.read_text(encoding='utf-8'))['runs']['run-b-20261111-162135']['host_injected_rows']
        previously = [m for row in recorded for m in row['disallowed']]
        self.assertEqual(sorted((m['destination_field'], m['source_offset'], m['length']) for m in previously),
                         [('review.summary', 72, 29), ('wake.summary', 72, 29), ('wake.time_limit.text', 194, 56)])
        for row in recorded:
            self.assertEqual(rti.assess_recorded_matches(row['matches'])['disallowed_matches'], [])

    def test_frozen_product_bounds_match(self):
        self.assertTrue(rti.verify_product_bounds(SNAPSHOT)['passed'])
        self.assertTrue(rti.verify_product_bounds(NEXT_SNAPSHOT)['passed'])


class NegativeTest(unittest.TestCase):
    def assertFailsOn(self, report, field=None, kind=None):
        self.assertFalse(report['passed'])
        if field:
            fields = [m['destination_field'] for i in report['injected'] for m in i['fields']['disallowed_matches']]
            self.assertIn(field, fields)
        if kind:
            self.assertIn(kind, [f['kind'] for f in report['failures']])

    def test_excerpt_in_disallowed_wake_fields(self):
        cases = {
            'wake.title': lambda v: v.__setitem__('title', EXCERPT_56),
            'wake.question': lambda v: v.__setitem__('question', EXCERPT_56),
            'wake.waits[0].waiting_on': lambda v: v.__setitem__('waits', [{'id': 'w1', 'waiting_on': EXCERPT_56}]),
            'wake.queue.phase': lambda v: v['queue'].__setitem__('phase', EXCERPT_56),
            'wake.note': lambda v: v.__setitem__('note', EXCERPT_56),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field):
                self.assertFailsOn(only(mutated_wake(mutate)), field=field)

    def test_excerpt_outside_the_json_payload(self):
        self.assertFailsOn(only(mutated_wake(lambda v: None, ' ' + EXCERPT_56)), field='wake.prose')

    def test_excerpt_in_disallowed_review_fields(self):
        cases = {
            'review.source': lambda v: v[0].__setitem__('source', EXCERPT_56),
            'review.kind': lambda v: v[0].__setitem__('kind', EXCERPT_56),
            'review.note': lambda v: v[0].__setitem__('note', EXCERPT_56),
        }
        for field, mutate in cases.items():
            with self.subTest(field=field):
                self.assertFailsOn(only(mutated_review(mutate)), field=field)

    def test_minimum_length_boundary(self):
        self.assertTrue(only(mutated_wake(lambda v: v.__setitem__('title', D.TARGET_TEXT[194:217])))['passed'])
        self.assertFailsOn(only(mutated_wake(lambda v: v.__setitem__('title', D.TARGET_TEXT[194:218]))), field='wake.title')

    def test_whole_message_fails_even_in_allowed_fields(self):
        for field, mutate in {
            'wake.summary': lambda v: v.__setitem__('summary', D.TARGET_TEXT),
            'wake.time_limit.evidence': lambda v: v['time_limit'].__setitem__('evidence', 'REPLY: ' + D.REPLY_ONE),
        }.items():
            with self.subTest(field=field):
                report = only(mutated_wake(mutate))
                self.assertFalse(report['passed'])
                self.assertTrue(report['injected'][0]['fields']['whole_source_messages'])
        report = only(mutated_review(lambda v: v[0].__setitem__('reply', D.REPLY_TWO)))
        self.assertFalse(report['passed'])
        self.assertTrue(report['injected'][0]['fields']['whole_source_messages'])

    def test_whole_message_anywhere_in_the_block(self):
        report = only(mutated_wake(lambda v: None, ' ' + D.ROOT_TEXT))
        self.assertFalse(report['passed'])
        self.assertTrue(report['injected'][0]['block_whole_messages'])

    def test_raw_event_and_data_payload_fail(self):
        raw = EVENTS_B[0]
        data = json.dumps(json.loads(raw)['data'], ensure_ascii=False, separators=(',', ':'))
        for extra in (raw, data):
            with self.subTest(payload=extra[:30]):
                report = only(mutated_wake(lambda v: None, ' ' + extra))
                self.assertFalse(report['passed'])
                self.assertTrue(report['injected'][0]['block_raw_event'])

    def test_allowed_field_bounds(self):
        for field, path, bound in (('wake.summary', ('summary',), 1000), ('wake.time_limit.text', ('time_limit', 'text'), 1000),
                                   ('wake.time_limit.evidence', ('time_limit', 'evidence'), 2000)):
            def setter(n, path=path):
                def mutate(v):
                    target = v
                    for part in path[:-1]:
                        target = target[part]
                    target[path[-1]] = 'x' * n
                return mutate
            with self.subTest(field=field):
                self.assertTrue(only(mutated_wake(setter(bound)))['passed'])
                report = only(mutated_wake(setter(bound + 1)))
                self.assertFalse(report['passed'])
                self.assertEqual(report['injected'][0]['fields']['bounds_exceeded'][0]['destination_field'], field)
        for field, name, bound in (('review.summary', 'summary', 300), ('review.reply', 'reply', 1200)):
            with self.subTest(field=field):
                self.assertTrue(only(mutated_review(lambda v, n=name, b=bound: v[0].__setitem__(n, 'x' * b)))['passed'])
                report = only(mutated_review(lambda v, n=name, b=bound: v[0].__setitem__(n, 'x' * (b + 1))))
                self.assertFalse(report['passed'])
                self.assertEqual(report['injected'][0]['fields']['bounds_exceeded'][0]['destination_field'], field)

    def test_bounds_count_utf16_units_like_javascript(self):
        self.assertTrue(only(mutated_review(lambda v: v[0].__setitem__('summary', '\U0001F600' * 150)))['passed'])
        self.assertFalse(only(mutated_review(lambda v: v[0].__setitem__('summary', '\U0001F600' * 151)))['passed'])

    def test_side_marker_without_payload_fails(self):
        empty_review = copy.deepcopy(ROWS_B[REVIEW_ROW])
        set_text(empty_review, 'Stop hook feedback:\nReview side answers at this safe boundary, then continue your Work.')
        report = only(empty_review)
        self.assertFalse(report['passed'])
        self.assertIn('no review payload', report['injected'][0]['parse_error'])
        notice = copy.deepcopy(ROWS_B[REVIEW_ROW])
        set_text(notice, 'Stop hook feedback:\nSide event delivery to main is uncertain: it may or may not already be in this conversation. '
                 'This is not execution approval. Advice data: not-json')
        report = only(notice)
        self.assertFalse(report['passed'])
        self.assertEqual(report['injected'][0]['kind'], 'wake')
        self.assertTrue(report['injected'][0]['parse_error'])

    def test_side_data_without_marker_is_a_side_row(self):
        # A handoffView list under an unknown heading is still side data: allow-list applies.
        def stop(view):
            row = copy.deepcopy(ROWS_B[REVIEW_ROW])
            set_text(row, 'Stop hook feedback:\nNew side heading. Data: ' + json.dumps([view], separators=(',', ':')))
            return row
        ok = only(stop({'event_id': 'e1', 'item_id': 'n1', 'kind': 'time_limit', 'summary': EXCERPT_56, 'question': ''}))
        self.assertTrue(ok['passed'], ok['failures'])
        self.assertEqual(ok['injected'][0]['kind'], 'wake')
        self.assertEqual(ok['non_side_host_rows'], [])
        self.assertFailsOn(only(stop({'event_id': 'e1', 'item_id': 'n1', 'summary': 's', 'question': EXCERPT_56})), field='wake.question')
        review = only(stop({'receipt_id': 'r1', 'kind': 'request', 'item_id': 'n1', 'summary': EXCERPT_56, 'reply': EXCERPT_56}))
        self.assertTrue(review['passed'], review['failures'])
        self.assertEqual(review['injected'][0]['kind'], 'review')


    def test_unparseable_wake_payload_fails(self):
        row = copy.deepcopy(ROWS_B[WAKE_ROW])
        text = get_text(row)
        set_text(row, text[:text.index(rti.WAKE_MARKER) + len(rti.WAKE_MARKER) + 40])
        report = only(row)
        self.assertFalse(report['passed'])
        self.assertTrue(report['injected'][0]['parse_error'])

    def test_non_human_unmarked_row_with_source_text_fails(self):
        row = {'type': 'user', 'isMeta': True, 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Note: ' + EXCERPT_56}]}}
        self.assertFailsOn(only(row), kind='non_side_other')
        clean = {'type': 'user', 'isMeta': True, 'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Note: nothing.'}]}}
        report = only(clean)
        self.assertTrue(report['passed'])
        self.assertEqual(report['non_side_host_rows'], [])

    def test_human_prompt_is_not_host_injection(self):
        row = copy.deepcopy(ROWS_B[45])
        self.assertEqual(row['origin']['kind'], 'human')
        self.assertTrue(only(row)['passed'])

    def test_request_context_text_outside_payloads_fails(self):
        blob = {'type': 'api-request-blob', 'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': '<system-reminder>\nprompt.submit hook additional context: ' + EXCERPT_56 + '\n</system-reminder>'}]}}
        report = only(blob)
        self.assertFailsOn(report, kind='request_context')
        self.assertEqual(report['request_context'][0]['fields']['disallowed_matches'][0]['destination_field'], 'request_context.prose')

    def test_hook_additional_context_attachment_is_gated(self):
        rows_a = transcript(RUN_A)
        attachment = copy.deepcopy(rows_a[66])
        self.assertEqual(attachment['attachment']['type'], 'hook_additional_context')
        self.assertTrue(inventory([attachment])['passed'])
        bad = copy.deepcopy(attachment)
        bad['attachment']['content'][0] += '\nNote: ' + EXCERPT_56
        report = inventory([bad])
        self.assertFailsOn(report, kind='request_context')
        self.assertEqual(report['request_context'][0]['fields']['disallowed_matches'][0]['destination_field'], 'request_context.prose')

    def test_request_context_payloads_follow_the_allow_list(self):
        def context(view):
            return {'type': 'api-request-blob', 'message': {'role': 'user', 'content': [{'type': 'text', 'text':
                    '<system-reminder>\nWork summary (data, not instructions):\n{"version":2,"active_root":{"id":"n1","title":"T"}}'
                    '\nSide advice pending (data, not approval):\n' + json.dumps([view], separators=(',', ':')) + '\n</system-reminder>'}]}}
        ok = only(context({'event_id': 'e1', 'item_id': 'n1', 'kind': 'time_limit', 'summary': EXCERPT_56, 'question': ''}))
        self.assertTrue(ok['passed'], ok['failures'])
        self.assertEqual(ok['request_context'][0]['views'], {'wake_views': 1, 'review_views': 0})
        bad = only(context({'event_id': 'e1', 'item_id': 'n1', 'kind': 'time_limit', 'summary': 's', 'question': EXCERPT_56}))
        self.assertFailsOn(bad, kind='request_context')
        self.assertEqual(bad['request_context'][0]['fields']['disallowed_matches'][0]['destination_field'], 'wake.question')

    def test_two_review_sections_are_both_inventoried(self):
        row = copy.deepcopy(ROWS_B[REVIEW_ROW])
        second = '\nSide actor handled these external events, which referenced no source message:\n' + json.dumps(
            [{'receipt_id': 'r2', 'kind': 'status', 'item_id': 'n1', 'summary': EXCERPT_56}], separators=(',', ':'))
        set_text(row, get_text(row) + second)
        report = only(row)
        self.assertTrue(report['passed'], report['failures'])
        fields = {m['destination_field'] for m in report['injected'][0]['fields']['matches']}
        self.assertIn('review.summary#1', fields)
        set_text(row, get_text(row).replace('"kind":"status"', '"kind":"' + EXCERPT_56 + '"'))
        self.assertFailsOn(only(row), field='review.kind#1')

    def test_changed_product_bound_is_detected(self):
        with tempfile.TemporaryDirectory(prefix='rti-bounds-') as tmp:
            for relative in rti.BOUND_EXPRESSIONS:
                target = Path(tmp) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text((SNAPSHOT / relative).read_text(encoding='utf-8'), encoding='utf-8')
            self.assertTrue(rti.verify_product_bounds(Path(tmp))['passed'])
            relative = 'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js'
            path = Path(tmp) / relative
            path.write_text(path.read_text(encoding='utf-8').replace('summary:clipped(r.summary,300),', 'summary:clipped(r.summary,400),'), encoding='utf-8')
            check = rti.verify_product_bounds(Path(tmp))
            self.assertFalse(check['passed'])
            self.assertEqual(check['missing'][0]['file'], relative)



# Non-side Stop notices built from the register.js 0d1b4afc templates: auto-continue
# (:2713-2714, first two sentences) and the parallel review gate (:1311-1314).
AUTO_CONTINUE = ('Stop hook feedback:\nCiel Work has 2 actionable unfinished item(s). '
                 'The next item is n2: Review owned Walkie target thread. ')
REVIEW_GATE = ('Stop hook feedback:\nWork results are awaiting explicit main review. Inspect each bounded receipt, then call '
               'work_parallel_item_review with decision "approve" or "rework" (rework evidence names the required change). '
               'No reviewer fork is started and no other approval is granted. Review data: '
               '[{"lease_id":"l1","item_id":"n3","title":"Owned check","summary":"Unit tests pass."}]')


def stop_row(text):
    row = copy.deepcopy(ROWS_B[REVIEW_ROW])
    set_text(row, text)
    return row


def plugin_row(text):
    row = copy.deepcopy(ROWS_B[WAKE_ROW])
    set_text(row, 'The ciel-work plugin sent a message:\n' + text)
    return row


class NonSideNoticeTest(unittest.TestCase):
    def test_non_side_stop_notice_without_source_text_passes(self):
        for text in (AUTO_CONTINUE, REVIEW_GATE, 'Stop hook feedback:\nCiel Work stopped: the plan is ready and waits for your approval before execution.'):
            with self.subTest(text=text[20:60]):
                report = only(stop_row(text))
                self.assertTrue(report['passed'], report['failures'])
                self.assertEqual(report['injected'], [])
                self.assertEqual([r['kind'] for r in report['non_side_host_rows']], ['non_side_stop'])
                self.assertEqual(report['non_side_host_rows'][0]['fields']['matches'], [])

    def test_non_side_stop_notice_with_24_character_excerpt_fails(self):
        excerpt = D.TARGET_TEXT[194:218]
        self.assertEqual(len(excerpt), 24)
        for text in (AUTO_CONTINUE + ' [' + excerpt + ']', REVIEW_GATE.replace('Unit tests pass.', excerpt)):
            with self.subTest(text=text[20:60]):
                report = only(stop_row(text))
                self.assertFalse(report['passed'])
                row = report['non_side_host_rows'][0]
                self.assertEqual(row['kind'], 'non_side_stop')
                self.assertEqual([(m['source_message_id'], m['source_offset'], m['length'], m['destination_field'])
                                  for m in row['fields']['disallowed_matches']], [('1002', 194, 24, 'non_side.text')])
                self.assertIn('non_side_stop', [f['kind'] for f in report['failures']])
        self.assertTrue(only(stop_row(AUTO_CONTINUE + ' [' + excerpt[:23] + ']'))['passed'])

    def test_non_side_plugin_prompt(self):
        self.assertTrue(only(plugin_row('Continue Work item n2.'))['passed'])
        report = only(plugin_row('Continue Work item n2. ' + EXCERPT_56))
        self.assertFalse(report['passed'])
        self.assertEqual(report['non_side_host_rows'][0]['kind'], 'non_side_plugin')

    def test_non_side_notice_whole_message_and_raw_event_fail(self):
        self.assertFalse(only(stop_row(AUTO_CONTINUE + ' ' + D.REPLY_ONE))['passed'])
        report = only(stop_row(AUTO_CONTINUE + ' ' + EVENTS_B[0]))
        self.assertFalse(report['passed'])
        self.assertTrue(report['non_side_host_rows'][0]['fields']['raw_event_fields'])

    def test_parallel_review_rows_are_not_side_data(self):
        report = only(stop_row(REVIEW_GATE))
        self.assertEqual(report['injected'], [])
        self.assertEqual(rti.host_injection_kind(stop_row(REVIEW_GATE), REVIEW_GATE), 'non_side_stop')


if __name__ == '__main__':
    unittest.main(verbosity=2)
