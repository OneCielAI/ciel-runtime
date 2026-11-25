import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'), 'node required')
class SideReleaseNoticeTests(unittest.TestCase):
    """The side releases a recorded wait and wakes whoever waits on it."""

    def run_case(self, name):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            (path / 'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT / 'tests/side_release_harness.mjs', path / 'test.mjs')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js', path / 'side-intake.js')
            shutil.copyfile(ROOT / 'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js', path / 'texts.generated.js')
            done = subprocess.run(['node', str(path / 'test.mjs'), name], capture_output=True, text=True,
                                  encoding='utf8', check=True, timeout=60)
        return json.loads(done.stdout)

    def test_running_fork_on_the_item_is_messaged_directly(self):
        row = self.run_case('fork-running')
        self.assertEqual([{'agentId': 'agent-run'}], [item['to'] for item in row['sent']])
        self.assertIn('n3 ("approval from Andy via Walkie")', row['sent'][0]['text'])
        self.assertIn('not new execution approval', row['sent'][0]['text'])
        self.assertEqual([], row['appended'])
        self.assertEqual([], row['submitted'])

    def test_busy_main_reads_the_release_in_its_running_turn(self):
        row = self.run_case('main-busy')
        self.assertEqual([], row['sent'])
        self.assertEqual(1, len(row['appended']))
        self.assertIn('The queue runs it automatically; nothing to do.', row['appended'][0])
        self.assertEqual([], row['submitted'])
        # Claimed on the event's receipt before the append, recorded after it.
        self.assertEqual([('e' * 64, 'notice_prepare'), ('e' * 64, 'notice_appended')],
                         [(call['event_id'], call['state']) for call in row['wakes']])

    def test_idle_main_gets_the_release_appended_and_a_wake_prompt_that_holds_none_of_it(self):
        row = self.run_case('main-idle')
        # The notice text is appended once; the prompt only starts the turn.
        self.assertEqual(1, len(row['appended']))
        self.assertIn('Ciel side: released n3', row['appended'][0])
        self.assertEqual(1, len(row['submitted']))
        self.assertTrue(row['submitted'][0].startswith('Ciel side wake eeeeeeeeeeee:'))
        self.assertNotIn('n3', row['submitted'][0])
        self.assertNotIn('Andy', row['submitted'][0])
        # The wake names the words the appended notice really begins with (the
        # notice became "Ciel side: released ..." on 2026-11-18; the wake kept
        # pointing main at "Ciel side released recorded wait", which no message
        # carries).
        self.assertIn('a host message beginning "Ciel side: released"', row['submitted'][0])
        self.assertEqual(['notice_prepare', 'notice_appended'], [call['state'] for call in row['wakes']])

    def test_idle_main_repolled_before_turn_start_does_not_queue_duplicate_notice_wake(self):
        row = self.run_case('main-idle-repolled')
        self.assertEqual(1, len(row['appended']))
        self.assertEqual(1, len(row['submitted']), row['submitted'])

    @unittest.skip('retired 2026-11-18 (user): main only hears that the queue runs the released item automatically')
    def test_parked_worker_is_named_for_mains_gated_resume(self):
        row = self.run_case('fork-parked')
        # No Work host resume callback is registered here, so the automatic
        # resume cannot run and main is named with SendMessage and the reason.
        self.assertEqual([], row['sent'])
        self.assertIn('Parked worker waiting on it: agent-park (lease L2, item n2)', row['appended'][0])
        self.assertIn('resume with SendMessage', row['appended'][0])

    @unittest.skip('retired 2026-11-18 (user): main only hears that the queue runs the released item automatically')
    def test_parked_worker_is_resumed_by_the_host_and_main_is_not_asked(self):
        # Stage 3 (2026-11-17): the host resumes it on its own lease first; main's
        # SendMessage notice stays only for a failed automatic resume.
        row = self.run_case('fork-parked-resumed')
        self.assertEqual([['L2']], row['resumeCalls'])
        notice = row['appended'][0]
        self.assertIn('The Work host resumed the parked worker on its own lease: agent-park (lease L2, item n2).', notice)
        self.assertNotIn('resume with SendMessage', notice)

    @unittest.skip('retired 2026-11-18 (user): main only hears that the queue runs the released item automatically')
    def test_a_failed_automatic_resume_is_named_for_main_with_the_reason(self):
        row = self.run_case('fork-parked-refused')
        notice = row['appended'][0]
        self.assertIn('agent-park (lease L2, item n2) was not resumed automatically: the resume was not delivered', notice)
        self.assertIn('resume with SendMessage', notice)

    def test_a_released_group_wait_reaches_the_forks_of_its_leaves(self):
        # Andy 2026-11-23: a group wait (n2296) gated five leaves; once the side
        # may release it, the forks on those leaves are the waiters.
        parked = self.run_case('group-parked-resumed')
        self.assertEqual([['L3']], parked['resumeCalls'])
        [(lease, message)] = parked['resumeTexts']
        self.assertEqual('L3', lease)
        self.assertIn('Walkie thread 1000, message 1005: Andy: approved', message)
        self.assertIn('not new execution approval', message)
        running = self.run_case('group-running')
        self.assertEqual([{'agentId': 'agent-run'}], [item['to'] for item in running['sent']])
        self.assertIn('n2 ("group approval")', running['sent'][0]['text'])
        # A leaf with a wait of its own is still waiting: it is not resumed.
        still = self.run_case('group-leaf-still-waits')
        self.assertEqual([], still['resumeCalls'])

    def test_undeliverable_fork_message_falls_back_to_main(self):
        row = self.run_case('fork-refuses')
        self.assertEqual(1, len(row['sent']))
        self.assertEqual(1, len(row['appended']))


if __name__ == '__main__':
    unittest.main()
