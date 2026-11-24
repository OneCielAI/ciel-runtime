"""The side answers a fork parked on a question; the Work host resumes it (user 2026-11-23).

Drives side-intake.js with a scripted host (tests/side_fork_answer_harness.mjs).
The register.js half (the resume itself) is tests/test_work_mod_write_gate.py
test_a_side_answer_resumes_the_question_parked_worker_with_it.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node required')
class SideForkAnswerTests(unittest.TestCase):
    def run_case(self, name):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests/side_fork_answer_harness.mjs', path / 'test.mjs')
            for name_ in ('side-intake.js', 'texts.generated.js'):
                shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks' / name_, path / name_)
            done = subprocess.run(['node', str(path / 'test.mjs'), name], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        return json.loads(done.stdout)

    def test_the_decision_field_reaches_only_a_router_that_lists_it(self):
        row = self.run_case('fields')
        self.assertEqual('L2', row['kept'][0]['lease_id'])
        self.assertNotIn('fork_answers', row['oldRouter'])
        self.assertEqual('L2', row['newRouter']['fork_answers'][0]['lease_id'])

    def test_a_delivered_answer_resumes_the_fork_and_main_hears_nothing(self):
        row = self.run_case('delivered')
        self.assertEqual('L2', row['resolved'][0]['fork_answers'][0]['lease_id'])
        self.assertEqual([['L2']], [call['ids'] for call in row['resumeCalls']])
        text = row['resumeCalls'][0]['texts']['L2']
        self.assertIn('MAIN DECISION 13:16Z: merge now; no notice needed', text)
        self.assertIn('source: n2 progress', text)
        self.assertIn('lease L2', text)
        self.assertIn('not new execution approval', text)
        self.assertEqual([], row['appended'])
        self.assertTrue(any(line.startswith('Side answered parked fork L2 (n2)') for line in row['logs']), row['logs'])

    def test_an_undelivered_answer_reaches_main_with_the_reason_and_the_answer(self):
        row = self.run_case('undelivered')
        self.assertEqual(1, len(row['appended']), row['appended'])
        notice = row['appended'][0]
        self.assertIn('lease L2 (item n2): the resume was not delivered: declined', notice)
        self.assertIn('MAIN DECISION 13:16Z: merge now', notice)
        self.assertIn('SendMessage', notice)

    def test_a_router_listing_no_fields_gets_only_the_fields_it_knew(self):
        # A router from before decision_fields (2026-11-20) refuses an unknown
        # field; fork_answers and stall_notices came after it.
        row = self.run_case('fields')
        self.assertNotIn('fork_answers', row['noFieldsRouter'])
        self.assertEqual({'action', 'kind', 'summary', 'reply'}, set(row['noFieldsRouter']))

    def test_a_stall_notice_reaches_main_once_with_what_was_checked(self):
        # User 2026-11-24: a fork that only waits for hours is told to main.
        row = self.run_case('stalled')
        self.assertEqual(['production release 0.4.509 deployed'],
                         [s['waiting_on'] for s in row['resolved'][0]['stall_notices']])
        self.assertEqual(1, len(row['appended']), row['appended'])
        notice = row['appended'][0]
        self.assertIn('Ciel side: stalled Work fork', notice)
        self.assertIn('n2 (lease L3)', notice)
        self.assertIn('production release 0.4.509 deployed', notice)
        self.assertIn('release.json says 0.4.508 (#162759)', notice)
        self.assertIn('deploy 0.4.509 or decide the order', notice)
        self.assertIn('no execution approval', notice)

    def test_a_silent_running_fork_reaches_main_once_with_its_last_record(self):
        # User 2026-11-24 (Prody n1200): a running fork that stopped without an
        # answer is told to main, with the silence the router confirmed.
        row = self.run_case('silent')
        self.assertEqual(['silent'], [s['kind'] for s in row['resolved'][0]['stall_notices']])
        self.assertEqual(1, len(row['appended']), row['appended'])
        notice = row['appended'][0]
        self.assertIn('Ciel side: stalled Work fork', notice)
        self.assertIn('n2 (lease L4)', notice)
        self.assertIn('2026-11-24T10:58:19Z', notice)
        self.assertIn('tool_use:Bash', notice)
        self.assertIn('n2 progress unchanged since 10:31Z', notice)
        self.assertIn('check the fork, resume or stop it', notice)
        self.assertNotIn('parked on a question', notice)
        self.assertNotIn('the wait still holds', notice)
        self.assertIn('no execution approval', notice)

    def test_an_idle_main_is_woken_toward_the_stall_notice(self):
        row = self.run_case('stalled-idle')
        self.assertEqual(1, len(row['appended']), row['appended'])
        self.assertEqual(1, len(row['submitted']), row['submitted'])
        self.assertIn('a host message beginning "Ciel side: stalled Work fork"', row['submitted'][0])
        self.assertNotIn('production release', row['submitted'][0])

    def test_a_refused_answer_rides_on_the_ask_main_notice(self):
        row = self.run_case('refused-ask')
        self.assertEqual([], row['resumeCalls'])
        # side_notice claims one notice per receipt: both go in that one notice.
        self.assertEqual(1, len(row['appended']), row['appended'])
        self.assertIn('Ciel side asks main', row['appended'][0])
        self.assertIn('lease L9: not a parked worker of this conversation', row['appended'][0])


if __name__ == '__main__':
    unittest.main()
