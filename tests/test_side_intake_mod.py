import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'),'node required')
class SideModTests(unittest.TestCase):
    def test_each_external_event_uses_one_constrained_actor_then_stops_it(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(3,len(row['spawns']))
        # A native fork inherits main's shell/edit/runtime tools; external data
        # always gets the registered, constrained side type instead.
        self.assertEqual(['ciel-work:side-intake']*3,[spawn['subagentType'] for spawn in row['spawns']])
        # agent.register applies from the next turn: the spawn itself names the
        # event's model, so a live model change cannot run on the previous one.
        self.assertEqual(['fixture-main','fixture-main','ciel-runtime-fixture-_selected_b'],
                         [spawn['model'] for spawn in row['spawns']])
        self.assertEqual('ciel-runtime-fixture-_selected_b',row['registeredModels'][-1])
        # A durable main wake is submitted once although later claims offer it again.
        self.assertEqual(1,len(row['submits']))
        self.assertIn('"kind":"resume"',row['submits'][0])
        self.assertIn('clear_wait',row['submits'][0])
        self.assertIn('no new execution approval',row['submits'][0])
        self.assertEqual([None]*3,[spawn['name'] for spawn in row['spawns']])
        self.assertEqual(['actor1','actor2','actor3'],row['stopped'])
        # Prose + fenced JSON and prose + raw JSON both commit; no-decision is
        # recorded once as invalid_answer, never as a retryable failure.
        self.assertEqual(['a'*64,'b'*64],[call['_host']['receipt_id'] for call in row['resolveCalls']])
        self.assertEqual(['mcp__mail__read'],row['resolveCalls'][0]['_host']['reported_sends'])
        self.assertEqual({'action':'answer','summary':'done2','reply':'answer2'},row['resolveCalls'][1]['decision'])
        self.assertEqual(['mcp__mail__read'],row['resolveCalls'][1]['_host']['reported_sends'])
        self.assertEqual([{'category':'invalid_answer','receipt':'c'*64,
                           'reason':'side answer carries no decision JSON'}],row['failures'])
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

    def test_pending_main_wake_never_holds_side_claims(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_wake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        row=json.loads(done.stdout)
        # Prody 2026-11-09: the wake prompt waited 63 minutes for main's turn to
        # end, and every claim waited with it. Polls now return while it waits.
        self.assertEqual(['returned','returned'],[row['firstPoll'],row['secondPoll']])
        self.assertEqual(['Side SSE '+'a'*12],row['whileBusy']['spawns'])
        # One wake prompt at a time: the second is not queued behind the first.
        self.assertEqual(1,row['whileBusy']['submits'])
        self.assertEqual(['w1','w2'],row['submits'])
        self.assertEqual([{'event_id':'w1','state':'prepare','attempt_id':None},
                          {'event_id':'w1','state':'queued','attempt_id':'attempt-w1'},
                          {'event_id':'w2','state':'prepare','attempt_id':None}],row['wakeCalls'])

    def test_p1_uses_reserved_slot_and_is_shared_with_main(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_urgency_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        # Two ordinary slots full: one reserved claim for P1 only, then none
        # until the reserved actor finishes.
        self.assertEqual([False,False,True],row['whileFull'])
        self.assertEqual([False,False,True,True],row['claims'])
        self.assertEqual(['a','b','c','d'],row['spawned'])
        self.assertEqual(['P3',None,'P1','P1'],row['sourcePriority'])
        self.assertEqual([7,7],row['resolveVersions'])
        # Main is reached by the durable wake prompt only; the person still sees
        # a system notice. A hidden user row as well duplicated every handoff.
        self.assertEqual(1,row['appendsAfterUrgent'])
        self.assertEqual(['system'],[a['type'] for a in row['appends']])
        self.assertEqual('Side P1 queued n9 · Fix outage',row['appends'][0]['text'])
        self.assertNotIn('customer down',row['appends'][0]['text'])

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

    def test_side_actor_offers_only_read_reply_and_status_tools(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual({'result':'native'},row['nonWork'])
        self.assertEqual({'deny':'Return a decision JSON; the host commits it. Execution approval is not granted'},row['workDenied'])
        self.assertRegex(row['parity']['digest'],r'^[0-9a-f]{16}$')
        self.assertGreaterEqual(len(row['registrations']),2)
        safe={'mcp__ciel-runtime-router__work_status','mcp__mail__read',
              'mcp__walkie__walkie_read_messages','mcp__walkie__walkie_send_message'}
        # Prody 2026-11-08: a side actor queued restart_session through the
        # inherited router MCP. Execution and runtime controls are never offered.
        dangerous={'mcp__github__create_issue','mcp__database__query','mcp__ciel-runtime-router__restart_session',
                   'mcp__ciel-runtime-router__work_node_update','mcp__ciel-runtime-router__work_side_resolve',
                   'mcp__ciel-runtime-router__submit_input','mcp__walkie__walkie_call_tool',
                   'mcp__walkie__walkie_threads_stop','mcp__aap__run_agent',
                   'mcp__x__get_shell','mcp__x__read_and_post','mcp__x__search_and_replace',
                   'mcp__x__fetch_and_save','mcp__x__reply_and_close','mcp__x__ack_then_delete_all'}
        for registration in row['registrations']:
            self.assertTrue(safe <= set(registration['tools']),registration)
            self.assertFalse(dangerous & set(registration['tools']),registration)
        self.assertEqual(sorted(safe),row['registrations'][0]['tools'])
        self.assertIn('mcp__calendar__read',row['registrations'][-1]['tools'])
        self.assertEqual({'toolCount':5,'serverCount':4},{k:row['parity'][k] for k in ('toolCount','serverCount')})

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
        self.assertIn('answer through the source reply tool',source)
        self.assertIn('ACK the exact incoming source message',source)
        self.assertIn('create one root Work item',source)
        self.assertIn('work_status/work_parallel_status',source)
        self.assertIn('source_priority, when present',source)
        # Code-change requests are investigated read-only and queued for main.
        self.assertIn('code/config/test/build/deploy/implementation request, do only basic read-only investigation',source)
        self.assertIn('Do not perform the change',source)
        self.assertIn('restart or control sessions/runtime',source)
        self.assertIn('never that implementation is proceeding',source)
        self.assertNotIn('act when the event requests it',source)

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
