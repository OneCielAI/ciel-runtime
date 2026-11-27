"""The side host requires the Walkie ACK and note the event's guidance asks for.

Daniel 2026-11-27 (Walkie 162826/162830): side events finished as Work updates
with no ACK or memo on the source message (162787: three receipts, sent_via
null). When the guidance asks for messages.work.update state="ack" first, the
host reads this agent's own status back before a decision commits.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks'


class SideAckGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests' / 'side_ack_gate_harness.mjs', path / 'test.mjs')
            for name in ('side-intake.js', 'texts.generated.js'):
                shutil.copyfile(HOOKS / name, path / name)
            done = subprocess.run(['node', str(path / 'test.mjs')], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        cls.result = json.loads(done.stdout)

    def test_a_missing_ack_is_corrected_with_the_ack_instruction_then_commits(self):
        row = self.result['missing_then_acked']
        self.assertEqual([{'to': 'actor-missing_then_acked', 'ackText': True, 'namesMessage': True,
                           'allowsTools': True, 'namesClient': True}], row['sends'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))
        self.assertEqual(2, len(row['readbacks']))
        self.assertEqual({'conversation_id': '34886995-4a66-4527-a618-814e9cb0a5bf', 'message_id': '41'},
                         row['readbacks'][0])

    def test_a_second_missing_ack_still_commits_the_decision_and_logs_the_shortfall(self):
        # Andy 2026-11-27 9d0578fb1be6: the 163057 approval's release of
        # n2967/n2968 was dropped as invalid_answer because the ACK was missing.
        row = self.result['missing_twice']
        self.assertEqual(1, len(row['sends']))
        self.assertTrue(row['sends'][0]['ackText'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))
        self.assertTrue(any('ACK' in text for text in row['shortfallLogs']), row['shortfallLogs'])

    def test_an_ack_without_a_memo_is_not_the_required_ack(self):
        row = self.result['ack_without_memo']
        self.assertEqual(1, len(row['sends']))
        self.assertTrue(row['sends'][0]['ackText'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))

    def test_the_prompt_and_correction_carry_the_events_own_ack_client_id(self):
        # Andy 2026-11-27: actors invented textbook UUIDs (a1b2c3d4-e5f6-...,
        # 123e4567-e89b-...) as client_id; a reused key got 409 and no ACK.
        row = self.result['missing_then_acked']
        self.assertEqual({'conversation_id': '34886995-4a66-4527-a618-814e9cb0a5bf', 'message_id': '41',
                          'client_id': 'eeeeeeee-eeee-4eee-aeee-eeeeeeeeeeee'},
                         {key: row['promptAck'][key] for key in ('conversation_id', 'message_id', 'client_id')})
        self.assertTrue(row['promptAck']['rule'])
        self.assertIsNone(self.result['no_guidance']['promptAck'])

    def test_a_gate_correction_leaves_the_shape_correction_available(self):
        row = self.result['ack_then_bad_summary']
        self.assertEqual(2, len(row['sends']))
        self.assertTrue(row['sends'][0]['ackText'])
        self.assertFalse(row['sends'][1]['ackText'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))

    def test_prose_after_a_gate_correction_commits_the_decision_the_gate_held(self):
        # Andy 2026-11-27 965d84239d22: the held decision was lost to prose.
        row = self.result['ack_then_prose']
        self.assertEqual(1, len(row['sends']))
        self.assertTrue(row['sends'][0]['ackText'])
        self.assertEqual((1, []), (row['resolves'], row['failures']))
        self.assertTrue(any('ACK' in text for text in row['shortfallLogs']), row['shortfallLogs'])

    def test_a_recorded_ack_with_memo_commits_without_correction(self):
        row = self.result['acked']
        self.assertEqual(([], 1, []), (row['sends'], row['resolves'], row['failures']))

    def test_events_without_ack_guidance_are_not_read_back(self):
        row = self.result['no_guidance']
        self.assertEqual(([], [], 1), (row['readbacks'], row['sends'], row['resolves']))

    def test_an_expired_claim_still_owned_by_the_actor_does_not_lose_its_decision(self):
        row = self.result['expired_before_prepare']
        self.assertEqual(([], 1, []), (row['sends'], row['resolves'], row['failures']))

    def test_an_unavailable_readback_does_not_block_the_decision(self):
        row = self.result['readback_unavailable']
        self.assertEqual(([], 1, []), (row['sends'], row['resolves'], row['failures']))


if __name__ == '__main__':
    unittest.main()
