import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.submission_receipt import TranscriptSubmissionReceipt
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
