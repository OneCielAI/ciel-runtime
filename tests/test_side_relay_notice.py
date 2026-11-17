import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MESSAGE = 'Andy: the CSV delimiter must be a semicolon, not a comma'


@unittest.skipUnless(shutil.which('node'), 'node required')
class SideRelayNoticeTests(unittest.TestCase):
    """A follow-up about an item in progress reaches main and that item's fork at once."""

    def run_case(self, name, *extra):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests/side_relay_harness.mjs', path / 'test.mjs')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', path / 'side-intake.js')
            done = subprocess.run(['node', str(path / 'test.mjs'), name, *extra], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        return json.loads(done.stdout)

    def test_idle_main_is_woken_for_a_failed_event_original_as_its_own_to_answer(self):
        # KOBE 2026-11-17: the wake for a forwarded original said "released
        # recorded wait ... Read it there and continue ... needs no acknowledgment".
        from ciel_runtime_support import side_forward
        text = side_forward.text('f' * 64, {'text': MESSAGE,
                                            'side_failure': {'category': 'source_context', 'attempt_count': 6}})
        row = self.run_case('forward-idle', text)
        self.assertEqual(1, len(row['appended']))
        self.assertTrue(row['appended'][0].startswith(text))
        self.assertEqual(1, len(row['submitted']))
        wake = row['submitted'][0]
        self.assertNotIn('semicolon', wake)
        self.assertIn('a host message beginning "[Ciel side 처리 실패 · 원문 전달"', wake)
        self.assertIn('work_side_claim main_forwards event ref ffffffffffff', wake)
        self.assertIn('decide yourself what reply or work it needs', wake)
        self.assertNotIn('released recorded wait', wake)
        self.assertNotIn('Read it there and continue', wake)
        self.assertNotIn('needs no acknowledgment', wake)
        self.assertIn('Ciel side: original of failed event ffffffffffff → main (appended)', row['logs'])

    def test_fork_on_the_item_and_main_both_get_the_original_message(self):
        row = self.run_case('relay-running')
        self.assertIs(True, row['resolved'][0]['relay'])
        # The fork on a descendant step of n2 gets it; the fork on another goal does not.
        self.assertEqual([{'agentId': 'agent-run'}], [item['to'] for item in row['sent']])
        fork = row['sent'][0]['text']
        self.assertIn('Ciel side relay for Work item n2 (csv writer)', fork)
        self.assertIn(MESSAGE, fork)
        self.assertIn('Decide whether this matters to your current work; if not, ignore it.', fork)
        self.assertIn('not new execution approval', fork)
        self.assertEqual(1, len(row['appended']))
        main = row['appended'][0]
        self.assertIn(MESSAGE, main)
        self.assertIn('Also delivered to the Work fork running it: agent-run.', main)
        self.assertIn('Decide whether this matters to your current work', main)
        self.assertEqual([('e' * 64, 'notice_prepare'), ('e' * 64, 'notice_appended')],
                         [(call['event_id'], call['state']) for call in row['wakes']])

    def test_parked_worker_is_named_for_main_and_never_resumed_here(self):
        row = self.run_case('relay-parked')
        self.assertEqual([], row['sent'])
        self.assertIn('Parked worker on it: agent-park (lease L2, item n2)', row['appended'][0])
        self.assertIn('resume with SendMessage to that worker only if this matters to it', row['appended'][0])

    def test_no_fork_on_the_item_reaches_main_only(self):
        row = self.run_case('relay-other')
        self.assertEqual([], row['sent'])
        self.assertIn('No Work fork received it.', row['appended'][0])

    def test_undeliverable_fork_still_reaches_main(self):
        row = self.run_case('relay-refuses')
        self.assertEqual(1, len(row['sent']))
        self.assertIn('No Work fork received it.', row['appended'][0])

    def test_idle_main_is_woken_by_a_prompt_that_holds_none_of_the_message(self):
        row = self.run_case('relay-idle')
        self.assertEqual(1, len(row['appended']))
        self.assertEqual(1, len(row['submitted']))
        self.assertNotIn('semicolon', row['submitted'][0])
        # The wake names the relay notice, not a released wait.
        self.assertIn('a host message beginning "Ciel side relay for Work item"', row['submitted'][0])
        self.assertIn('decide whether it matters to your current work', row['submitted'][0])
        self.assertNotIn('released recorded wait', row['submitted'][0])

    def test_update_without_relay_tells_nobody(self):
        row = self.run_case('no-relay')
        self.assertFalse(row['resolved'][0].get('relay'))
        self.assertEqual(([], []), (row['sent'], row['appended']))


if __name__ == '__main__':
    unittest.main()
