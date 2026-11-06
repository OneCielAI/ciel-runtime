import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'),'node required')
class SideModTests(unittest.TestCase):
    def test_external_event_ids_reuse_actor_and_host_commits_parent_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(1,row['spawns'])
        self.assertEqual(1,row['resumes'])
        self.assertEqual('claude:parent',row['parent'])
        self.assertEqual('a'*64,row['hostReceipt'])
        self.assertEqual({'input_tokens':3},row['usage'])
        self.assertEqual('b'*64,row['current'])
        self.assertIn('mcp__mail__read',row['tools'])
        self.assertTrue(row['heartbeat'])
        self.assertEqual('answered',row['handoff'])
        self.assertEqual(['n21'],row['workload']['queue_order'])
        self.assertFalse(row['workload']['truncated'])
        self.assertTrue(any('question second line' in line for line in row['logs']))
        self.assertFalse(any('\n' in line for line in row['logs']))
