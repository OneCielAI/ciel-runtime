import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'),'node required')
class SideModTests(unittest.TestCase):
    def test_each_external_event_uses_one_fork_then_stops_it(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(2,len(row['spawns']))
        self.assertEqual(['fork','fork'],[spawn['subagentType'] for spawn in row['spawns']])
        self.assertEqual([None,None],[spawn['name'] for spawn in row['spawns']])
        self.assertEqual(['actor1','actor2'],row['stopped'])
        self.assertTrue(row['first']['retired'])
        self.assertTrue(row['second']['retired'])
        self.assertIsNone(row['first']['current'])
        self.assertIsNone(row['second']['current'])
        self.assertEqual(['n21'],row['spawns'][0]['prompt']['workload']['queue_order'])
        self.assertFalse(row['spawns'][0]['prompt']['workload']['truncated'])
        self.assertEqual('question',row['spawns'][0]['prompt']['original'])
        self.assertEqual('claude:parent',row['resolveCalls'][0]['session_id'])
        self.assertEqual('a'*64,row['resolveCalls'][0]['_host']['receipt_id'])
        self.assertEqual({'input_tokens':3},row['resolveCalls'][0]['usage'])

    def test_paged_host_results_reassemble_and_events_render_interpreted(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_paging_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(333,row['nodes'])
        self.assertGreater(row['pageCalls'],1)
        self.assertTrue(row['tokenSent'])
        # The native client's oversize text must be a named failure, not a JSON.parse message.
        self.assertIn('work_side_claim: router did not return JSON: Error: result',row['notJson'])
        self.assertIn('digest mismatch',row['mismatch'])
        self.assertEqual('[com.ciel.walkie.support.message · agent.pending_reminder] 첫 줄 둘째 줄',row['summary'])
        rendered='\n'.join(row['lines'])
        self.assertIn('Text:\n  첫 줄\n  둘째 줄',rendered)
        self.assertIn('"activity_context": "‹hidden 344 chars›"',rendered)
        self.assertIn('"token": "‹hidden 12 chars›"',rendered)
        self.assertNotIn('S'*20,rendered)
        self.assertNotIn('secret-value',rendered)
        self.assertNotIn('"text"',rendered)
        self.assertEqual('plain question second line',row['plainSummary'])
        self.assertEqual(['plain question','second line'],row['plainLines'])

    def test_fork_policy_allows_parent_mcp_and_denies_work_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual({'result':'native'},row['nonWork'])
        self.assertEqual({'deny':'Use work_side_resolve; execution approval is not granted'},row['workDenied'])
        self.assertEqual({'toolCount':5,'serverCount':5},{k:row['parity'][k] for k in ('toolCount','serverCount')})
        self.assertRegex(row['parity']['digest'],r'^[0-9a-f]{16}$')
        self.assertEqual(2,len(row['registrations']))
        self.assertIn('mcp__mail__read',row['registrations'][0]['tools'])
        self.assertIn('mcp__calendar__read',row['registrations'][1]['tools'])

    def test_failed_owned_side_actor_is_removed_on_reload(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_cleanup_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        self.assertEqual([
            {'tool':'TaskStop','task_id':'failed-side'},
            {'tool':'TaskStop','task_id':'idle-side'},
            {'tool':'TaskStop','task_id':'finished-fork'},
        ],json.loads(done.stdout))

    def test_side_prompt_requires_answer_ack_queue_and_status_behaviors(self):
        source=(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js').read_text(encoding='utf8')
        self.assertIn('answer it immediately through the source',source)
        self.assertIn('ACK the exact incoming source message',source)
        self.assertIn('create one root Work item',source)
        self.assertIn('work_status/work_parallel_status',source)
        self.assertIn('Never answer only from the event payload',source)

    def test_reply_route_override_is_not_mcp_inheritance(self):
        from ciel_runtime_support.recent_menu_text import menu_text
        from ciel_runtime_support.work_settings_menu import description
        key='work_side_intake_reply_connectors'
        self.assertEqual('Advanced external-event reply route override (JSON)',menu_text(key))
        text=description({},key)
        self.assertIn('not MCP inheritance',text)
        self.assertIn('Correlated web chat replies automatically',text)
        self.assertIn('ordinary external SSE/CloudEvent replies need',text)
        # The prelaunch menu clips descriptions to one line; keep the distinction visible first.
        self.assertLess(text.index('not MCP inheritance'),100)
        korean=description({'language':'ko'},key)
        self.assertIn('MCP 상속 설정이 아닙니다',korean)
        self.assertIn('일반 외부 SSE/CloudEvent 응답에는',korean)
        for language in ('en','ko','ja','zh'):
            self.assertNotIn('MCP',menu_text(key,language))
