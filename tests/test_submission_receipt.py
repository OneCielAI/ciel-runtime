import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.submission_receipt import LatestTranscriptSubmissionReceipt, TranscriptSubmissionReceipt
from ciel_runtime_support.windows_conpty import WindowsConPtySession
from ciel_runtime_support.channel_injection import ChannelPromptInjector, PromptInjection, RuntimeInjectionPolicy


class SubmissionReceiptTests(unittest.TestCase):
    def test_receipt_requires_new_matching_user_record(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'transcript.jsonl'
            record = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'example prompt'}]}}
            line = json.dumps(record).encode() + b'\n'
            p.write_bytes(line)
            receipt = TranscriptSubmissionReceipt(p, 'example prompt')
            self.assertFalse(receipt())
            with p.open('ab') as f:
                f.write(line[:-1])
            self.assertFalse(receipt())
            with p.open('ab') as f:
                f.write(b'\n')
            self.assertTrue(receipt())

    def test_latest_receipt_confirms_from_a_transcript_created_after_typing(self):
        with tempfile.TemporaryDirectory() as d:
            old, new = Path(d) / 'old.jsonl', Path(d) / 'new.jsonl'
            old.write_text('{}\n')
            current = {'path': old}
            receipt = LatestTranscriptSubmissionReceipt(lambda: current['path'], 'id=3 GAMMA', now=lambda: 1790000000.0)
            self.assertFalse(receipt())
            record = {'timestamp': '2026-09-21T14:13:21Z', 'type': 'response_item',
                      'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'id=3 GAMMA'}]}}
            new.write_text(json.dumps(record) + '\n')
            current['path'] = new
            self.assertTrue(receipt())

    def test_latest_receipt_ignores_older_records_in_a_newly_seen_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            other = Path(d) / 'other.jsonl'
            current = {'path': None}
            receipt = LatestTranscriptSubmissionReceipt(lambda: current['path'], 'repeat me', now=lambda: 1790000000.0)
            self.assertFalse(receipt.watching)
            stale = {'timestamp': '2026-09-01T00:00:00Z', 'message': {'role': 'user', 'content': 'repeat me'}}
            unstamped = {'message': {'role': 'user', 'content': 'repeat me'}}
            other.write_text(json.dumps(stale) + '\n' + json.dumps(unstamped) + '\n')
            current['path'] = other
            self.assertFalse(receipt())

    def test_receipt_accepts_a_record_with_the_same_ciel_header_and_ids(self):
        # ara 2026-10-03: Codex recorded the prompt with U+2026/U+2014 missing.
        sent = '[ciel-runtime external channel message] channel=ch room=r from=a id=590 text="x — y…"'
        typed = '[ciel-runtime external channel message] channel=ch room=r from=a id=590 text="x y"'
        other_id = '[ciel-runtime external channel message] channel=ch room=r from=a id=5901 text="x y"'
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'transcript.jsonl'
            p.write_bytes(b'')
            receipt = TranscriptSubmissionReceipt(p, sent)

            def append(text):
                record = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                          'content': [{'type': 'input_text', 'text': text}]}}
                with p.open('ab') as f:
                    f.write(json.dumps(record).encode() + b'\n')

            append(other_id)
            append('quoted: ' + typed)
            self.assertFalse(receipt())
            append(typed)
            self.assertTrue(receipt())
            self.assertIn('matched_by=identity_prefix', receipt.describe())

    def test_wake_block_receipt_matches_its_ids_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'transcript.jsonl'
            p.write_bytes(b'')
            receipt = TranscriptSubmissionReceipt(p, '[ciel-wake] pending_ids=589,590')
            def append(text):
                record = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                          'content': [{'type': 'input_text', 'text': text}]}}
                with p.open('ab') as f:
                    f.write(json.dumps(record).encode() + b'\n')

            append('[ciel-wake] pending_ids=591')
            self.assertFalse(receipt())
            append('[ciel-wake] pending_ids=589,590')
            self.assertTrue(receipt())

    def test_describe_reports_what_the_receipt_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'transcript.jsonl'
            p.write_bytes(b'{}\n')
            receipt = LatestTranscriptSubmissionReceipt(lambda: p, 'expected prompt')
            other = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'another prompt'}]}}
            with p.open('ab') as f:
                f.write(json.dumps(other).encode() + b'\n')
            self.assertFalse(receipt())
            text = receipt.describe()
            self.assertIn(f'resolved={p}', text)
            self.assertIn('start=3 offset=', text)
            self.assertIn('checks=1 read_errors=0 user_records=1 accepted=False', text)
            self.assertIn("last_user='another prompt'", text)
            p.write_bytes(b'')
            self.assertFalse(receipt())
            self.assertIn('invalidated=truncated', receipt.describe())

    def test_display_wrap_and_ansi_paste_marker(self):
        self.assertTrue(WindowsConPtySession._prompt_rendered_in_output(
            b'[external mes\r\nsage] example', '[external message] example'))
        self.assertTrue(WindowsConPtySession._prompt_rendered_in_output(
            b'[Pasted\x1b[1CContent 1024 chars]', 'different long payload'))

    def test_truncation_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'transcript.jsonl'
            p.write_bytes(b'old history\n')
            receipt = TranscriptSubmissionReceipt(p, 'example')
            p.write_bytes(b'')
            self.assertFalse(receipt())
            p.write_text(json.dumps({'message': {'role': 'user', 'content': 'example'}}) + '\n')
            self.assertFalse(receipt())

    def test_receipt_stops_additional_enter_keys(self):
        class Transport:
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(data)
            def wait_until_input_consumed(self, timeout):
                return True
        t = Transport()
        receipts = iter([False, True])
        injector = ChannelPromptInjector(sleep=lambda _: None, retry_delay_seconds=lambda: 0,
            snapshot=lambda: 'unchanged', log=lambda *_: None,
            submission_receipt=lambda: next(receipts))
        self.assertTrue(injector.inject(t, PromptInjection('example', RuntimeInjectionPolicy(
            runtime='fixture', clear_input=b'\x15', submit_input=b'\r', submit_delay_seconds=0,
            submit_attempts=4, confirm_submission=True))))
        self.assertEqual([b'\x15example', b'\r'], t.writes)

    def test_repaint_does_not_confirm_submission_and_body_is_not_retyped(self):
        class Transport:
            def __init__(self):
                self.writes = []
            def write(self, data):
                self.writes.append(data)
            def wait_until_input_consumed(self, timeout):
                return True
        t = Transport()
        snapshots = iter(['draft', 'draft repaint', 'another repaint'])
        injector = ChannelPromptInjector(sleep=lambda _: None, retry_delay_seconds=lambda: 0,
            snapshot=lambda: next(snapshots), log=lambda *_: None, submission_receipt=lambda: False)
        result = injector.inject(t, PromptInjection('example', RuntimeInjectionPolicy(
            runtime='fixture', clear_input=b'\x15', submit_input=b'\r', submit_delay_seconds=0,
            submit_attempts=2, confirm_submission=True)))
        self.assertFalse(result)
        self.assertEqual([b'\x15example', b'\r', b'\r'], t.writes)


if __name__ == '__main__':
    unittest.main()
