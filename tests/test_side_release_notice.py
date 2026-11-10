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
        self.assertIn('no acknowledgment is needed', row['appended'][0])
        self.assertEqual([], row['submitted'])

    def test_idle_main_is_prompted_at_once(self):
        row = self.run_case('main-idle')
        self.assertEqual([], row['appended'])
        self.assertEqual(1, len(row['submitted']))
        self.assertIn('Ciel side released recorded wait n3', row['submitted'][0])

    def test_parked_worker_is_named_for_mains_gated_resume(self):
        row = self.run_case('fork-parked')
        # A parked worker is never resumed from here: the lease reacquire and
        # resume authorization stay on main's SendMessage path.
        self.assertEqual([], row['sent'])
        self.assertIn('Parked worker waiting on it: agent-park (lease L2, item n2)', row['appended'][0])
        self.assertIn('resume with SendMessage', row['appended'][0])

    def test_undeliverable_fork_message_falls_back_to_main(self):
        row = self.run_case('fork-refuses')
        self.assertEqual(1, len(row['sent']))
        self.assertEqual(1, len(row['appended']))


if __name__ == '__main__':
    unittest.main()
