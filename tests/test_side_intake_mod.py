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
        self.assertIn('mcp__github__create_issue',row['tools'])
        self.assertIn('mcp__database__query',row['tools'])
        self.assertTrue(row['heartbeat'])
        self.assertEqual('answered',row['handoff'])
        self.assertEqual(['n21'],row['workload']['queue_order'])
        self.assertFalse(row['workload']['truncated'])
        self.assertFalse(any(line.startswith(('Side processing', 'Side processed', 'Side MCP parity', 'Side event', 'btw_with_mcp')) for line in row['logs']))

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

    def test_primary_side_inherits_changed_mcp_at_next_event_without_cancelling_actor(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(1,row['nonWorkNext'])
        self.assertEqual({'deny':'Use work_side_resolve; execution approval is not granted'},row['workDenied'])
        self.assertEqual({'toolCount':4,'serverCount':4},{k:row['beforeParity'][k] for k in ('toolCount','serverCount')})
        self.assertEqual({'toolCount':5,'serverCount':5},{k:row['afterParity'][k] for k in ('toolCount','serverCount')})
        self.assertRegex(row['beforeParity']['digest'],r'^[0-9a-f]{16}$')
        self.assertNotEqual(row['beforeParity']['digest'],row['afterParity']['digest'])
        self.assertEqual(2,row['registrations'])
        self.assertEqual(2,row['changedSpawns'])
        self.assertEqual('b'*64,row['current'])
        self.assertEqual('c'*64,row['changedCurrent'])
        # Native, malformed and non-boolean MCP rows from the fixture are never inherited.
        self.assertEqual(['mcp__ciel-runtime-router__work_status','mcp__database__query','mcp__github__create_issue','mcp__mail__read'],row['tools'])
        self.assertEqual(['mcp__calendar__read','mcp__ciel-runtime-router__work_status','mcp__database__query','mcp__github__create_issue','mcp__mail__read'],row['changedTools'])
        self.assertEqual(['calendar','ciel-runtime-router','database','github','mail'],row['changedServers'])
        self.assertEqual([],row['changedDisallowed'])
        self.assertEqual([{'tools':[],'mcpServers':[]}],row['shadow'])
        self.assertEqual({'spawns':3,'resumes':2,'to':'actor2'},row['sameDigest'])
        replaced=row['replaced']
        self.assertEqual(4,replaced['spawns'])
        self.assertEqual(2,replaced['resumes'])
        self.assertEqual('e'*64,replaced['current'])
        self.assertEqual(3,replaced['registrations'])
        self.assertEqual(['mcp__ciel-runtime-router__work_status','mcp__database__query','mcp__drive__read','mcp__github__create_issue','mcp__mail__read'],replaced['tools'])
        self.assertNotIn(replaced['parity']['digest'],{row['beforeParity']['digest'],row['afterParity']['digest']})
        self.assertFalse(any(line.startswith(('Side processing', 'Side processed', 'Side MCP parity', 'Side event', 'btw_with_mcp')) for line in row['logs']))

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
