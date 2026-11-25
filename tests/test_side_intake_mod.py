import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node'),'node required')
class SideModTests(unittest.TestCase):
    def run_harness(self, name, *args, timeout=60):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests'/name,path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs'),*args],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=timeout)
        return json.loads(done.stdout)

    def test_side_failure_retries_only_the_same_mcp_write(self):
        for mode in ('before', 'after'):
            with self.subTest(mode=mode):
                row = self.run_harness('side_intake_harness.mjs', mode)
                self.assertEqual((2,1,1), (row['failCalls'],row['writes'],row['spawns']))
                first, second = row['failures']
                self.assertEqual(first,second)
                self.assertEqual('billing_error',first['category'])
                self.assertEqual('1'*32,first['_host']['generation'])

    def test_exhausted_failure_write_does_not_spawn_the_same_generation(self):
        row = self.run_harness('side_intake_harness.mjs', 'exhausted')
        self.assertEqual((4,0,1), (row['failCalls'],row['writes'],row['spawns']))
        recovered = self.run_harness('side_intake_harness.mjs', 'recovered')
        self.assertEqual((4,1,1), (recovered['failCalls'],recovered['writes'],recovered['spawns']))
        self.assertTrue(all(call == recovered['failures'][0] for call in recovered['failures']))

    def test_a_claim_with_a_saved_proposal_commits_it_without_a_fork(self):
        # Plan 2026-11-17: after a restart the model does not run again and the
        # understanding already sent is not sent again.
        row=self.run_harness('side_resume_harness.mjs')
        self.assertEqual(0,row['spawns'])
        self.assertEqual([],row['failures'])
        order=row['order']
        self.assertLess(order.index('work_side_understanding:prepare'),order.index('work_side_resolve'))
        self.assertLess(order.index('work_side_resolve'),order.index('work_side_reply:prepare'))
        saved={'refs': ['999'], 'action':'create','kind':'request','title':'retry fix','summary':'retry fix','reply':'queued','priority':'P3'}
        self.assertEqual([{'understanding':'understood in my own words','decision':saved,'expected_version':4}],
                         row['understanding'])
        self.assertEqual([{'decision':saved,'expected_version':4,'generation':'5'*32}],row['resolve'])
        # Only the final reply is sent; the understanding prepare offered no send.
        self.assertEqual(['final'],row['walkieSends'])

    def test_a_malformed_saved_proposal_is_a_retryable_failure_not_a_fork(self):
        row=self.run_harness('side_resume_harness.mjs','malformed')
        self.assertEqual(0,row['spawns'])
        self.assertEqual(['resume_error'],row['failures'])
        self.assertEqual([],row['resolve'])

    def test_a_lapsed_lease_of_a_working_actor_is_renewed_not_claimed_twice(self):
        # Andy 2026-11-23: 336 events ran on two side actors; 7d31178bb61e's
        # first actor was stopped at 23:42:51Z when the pump came back after a
        # 7-minute gap and the event was claimed again (second actor 23:43:17Z).
        row=self.run_harness('side_lease_revival_harness.mjs','revived')
        self.assertEqual(1,row['spawns'])
        self.assertEqual(1,row['renews'])
        self.assertEqual([],row['stopped'])
        self.assertEqual('a'*64,row['current'])
        # The router refused the renew (the event was claimed elsewhere): the
        # actor no longer owns it and is stopped, as before.
        refused=self.run_harness('side_lease_revival_harness.mjs','refused')
        self.assertEqual(['TaskStop:actor1'],refused['stopped'])
        self.assertIsNone(refused['current'])
        # The lease lapsed while the claim call was in flight and the router
        # handed this host its own claim back: no second actor, nothing stopped,
        # and the working actor holds the refreshed lease.
        reoffered=self.run_harness('side_lease_revival_harness.mjs','reoffered')
        self.assertEqual((1,[],'a'*64,True),
                         (reoffered['spawns'],reoffered['stopped'],reoffered['current'],reoffered['lease_live']))
        # The renew got no answer (an MCP timeout is not the router refusing the
        # claim): the working actor is kept and the re-offered claim is its own.
        timedout=self.run_harness('side_lease_revival_harness.mjs','timedout')
        self.assertEqual((1,[],'a'*64,True),
                         (timedout['spawns'],timedout['stopped'],timedout['current'],timedout['lease_live']))

    def test_session_fresh_starts_each_event_empty_with_main_system_prompt(self):
        # User 2026-11-20: a side actor may start from an empty conversation
        # with main's own system prompt and MCP tools instead of a fork.
        fork=self.run_harness('side_fresh_harness.mjs','fork')
        fresh_prompt=fork['spawns'][0]['prompt']
        referenced={node['id']:node for node in fresh_prompt['workload']['referenced']}
        self.assertEqual('Read message 42 and verify the explicit answer',
                         referenced['n21']['release_hint']['lookup'])
        self.assertEqual(['fork']*3,[spawn['subagentType'] for spawn in fork['spawns']])
        self.assertEqual([],fork['registrations'])
        fresh=self.run_harness('side_fresh_harness.mjs','fresh')
        self.assertEqual(['ciel-work:side-fresh']*3,[spawn['subagentType'] for spawn in fresh['spawns']])
        # One registration per module load: main's composed prompt, no tool
        # list (every tool the session has, its MCP servers included).
        self.assertEqual([{'name':'side-fresh','prompt':'MAIN SYSTEM PROMPT\n\nENV','tools':None}],fresh['registrations'])
        self.assertEqual([True]*3,fresh['rewrites'])
        # The same decision path and one-event lifetime as a fork.
        self.assertEqual([r['decision']['action'] for r in fork['resolveCalls']],
                         [r['decision']['action'] for r in fresh['resolveCalls']])
        self.assertEqual(['actor1','actor2','actor3'],fresh['stopped'])

    def test_fresh_side_runs_single_active_actor_and_recovers_missing_closing_brace_and_priority(self):
        # KOBE & Celly 2026-11-25:
        # 1) fresh mode ran 2-3 side-fresh actors concurrently (overlapping work,
        #    burning tokens and stalling the queue). Fresh mode keeps 1 active
        #    side-fresh actor at a time unless P0/P1 urgent slot is needed.
        # 2) side models repeatedly dropped the outer closing brace '}' on
        #    {"understanding":"...","decision":{...}}, failed on missing
        #    understanding or reply, or emitted invalid priority/queue_position
        #    — quarantining and dumping raw originals into main's context.
        row=self.run_harness('side_fresh_reliability_harness.mjs', timeout=10)
        self.assertEqual(1, row['freshConcurrentSpawns'])
        self.assertEqual(1, row['freshUrgentWhileBusySpawns'])
        # Missing closing brace repaired without losing the decision or sending a correction turn.
        self.assertEqual('answer', row['missingBrace']['decision']['action'])
        self.assertEqual('게이트 유지', row['missingBrace']['decision']['reply'])
        self.assertEqual('요약', row['missingBrace']['understanding'])
        # Missing closing brace with expected_version inside decision object: repaired too.
        self.assertEqual('answer', row['missingBraceWithExpected']['decision']['action'])
        self.assertEqual(198, row['missingBraceWithExpected']['expected_version'])
        # Escaped "decision" text inside summary still extracts the real outer decision JSON.
        self.assertEqual('answer', row['escapedDecisionInSummary']['decision']['action'])
        self.assertEqual(7, row['escapedDecisionInSummary']['expected_version'])
        # Leading '{' or '}' without a valid decision object must reject cleanly instead of
        # infinite-looping in lastIndexOf(..., -1) and crashing Claude Code's hooks worker
        # (Prody 03:30Z/07:50Z, KOBE 01:00Z/03:09Z).
        self.assertEqual(['side answer carries no decision JSON'] * 3, row['noDecisionErrors'])
        # Invalid priority, queue_position, and missing target item_id are correctable.
        self.assertEqual(1, row['priorityCorrection']['sends'])
        self.assertEqual(1, row['priorityCorrection']['resolves'])
        self.assertEqual(0, row['priorityCorrection']['failures'])
        self.assertEqual(1, row['queuePositionCorrection']['sends'])
        self.assertEqual(1, row['queuePositionCorrection']['resolves'])
        self.assertEqual(0, row['queuePositionCorrection']['failures'])
        self.assertEqual(1, row['missingTargetCorrection']['sends'])
        self.assertEqual(1, row['missingTargetCorrection']['resolves'])
        self.assertEqual(0, row['missingTargetCorrection']['failures'])
        # Missing understanding on a valid decision is synthesized from decision.summary
        # rather than failing on bounded understanding, while still refusing ACK/echo.
        self.assertEqual('게이트 재확인: 변동 없음', row['missingUnderstanding']['understanding'])
        # Missing reply on no_reply=true answer is filled from summary instead of quarantining.
        self.assertEqual('중복 알림 확인, 기록만 남김', row['missingNoReply']['decision']['reply'])
        # Previous valid JSON turn in same actor is recovered if a background task
        # notification triggers a prose-only final turn (Andy 50f0bcedf586).
        self.assertEqual(1, row['bgFollowup']['resolves'])
        self.assertEqual(0, row['bgFollowup']['failures'])

    def test_a_side_actor_gets_its_session_mode_transcript_and_the_items_records(self):
        # Andy 2026-11-23: a fresh actor starts with no conversation, yet its
        # instruction said it inherited main's, and the workload it got had no
        # note or progress; items linked through refs were never picked.
        fresh=self.run_harness('side_fresh_harness.mjs','fresh')['spawns'][0]['prompt']
        fork=self.run_harness('side_fresh_harness.mjs','fork')['spawns'][0]['prompt']
        self.assertEqual(('fresh','fork'),(fresh['session_mode'],fork['session_mode']))
        self.assertEqual('C:/fixture/projects/parent.jsonl',fresh['main_transcript'])
        referenced={node['id']:node for node in fresh['workload']['referenced']}
        # Picked through refs: the event's target message 41 is one of n23's refs.
        self.assertIn('n23',referenced)
        self.assertNotIn('n24',referenced)
        self.assertNotIn('n27',referenced)
        self.assertEqual('run 455 attempt 2 completed',referenced['n26']['waiting_on'])
        self.assertEqual('n26',fresh['workload']['referenced'][0]['id'])
        self.assertEqual('MAIN DECISION: only 120px is approved; the rest stays held',referenced['n23']['progress'])
        self.assertTrue(referenced['n23']['note'].endswith('NOTE-TAIL'))
        self.assertTrue(referenced['n23']['note'].startswith('…'))
        self.assertLessEqual(len(referenced['n23']['note']),400)
        self.assertEqual('checked 41',referenced['n23']['evidence'])
        self.assertEqual(['ticket-77','41'],referenced['n23']['refs'])
        # Ancestors stay brief.
        self.assertNotIn('note',referenced['n1'])

    def test_each_external_event_uses_one_native_fork_then_stops_it(self):
        row=self.run_harness('side_intake_harness.mjs')
        self.assertEqual(3,len(row['spawns']))
        # Operator decision 2026-11-11: every SSE event is a true native fork
        # that inherits main's model, context, tools and permissions.
        self.assertEqual(['fork']*3,[spawn['subagentType'] for spawn in row['spawns']])
        # No model/name override reaches the spawn, and the agent.spawn hook
        # authorizes the pending fork without rewriting it.
        self.assertEqual([None]*3,[spawn['model'] for spawn in row['spawns']])
        # Unnamed: owned probe named-fork-d48887c3 showed a named native fork
        # stays idle after its turn and TaskStop; an unnamed one completes.
        self.assertEqual([None]*3,[spawn['name'] for spawn in row['spawns']])
        self.assertEqual([True]*3,row['rewrites'])
        for spawn in row['spawns']:
            self.assertNotIn('model',spawn['keys'])
        # No registered side agent type and no projected parent context.
        self.assertEqual(0,row['registrations'])
        self.assertEqual(0,row['contextCalls'])
        self.assertEqual('inherit',row['first']['model'])
        # A stored model/context value from an older build changed nothing.
        self.assertEqual(row['spawns'][1]['keys'],row['spawns'][0]['keys'])
        self.assertEqual(['native-fork-inherited',0,0],
                         [row['parity']['digest'],row['parity']['toolCount'],row['parity']['serverCount']])
        # A durable main wake is appended once although later claims offer it
        # again; the one prompt that wakes the idle main holds none of it.
        self.assertEqual(1,len(row['appends']))
        self.assertIn('"kind":"resume"',row['appends'][0])
        self.assertIn('clear_wait',row['appends'][0])
        self.assertIn('no new execution approval',row['appends'][0])
        # This legacy scripted backend does not implement the protocol-2 prompt
        # lease, so the current host fails closed instead of issuing an unfenced
        # wake prompt. Real-backend coverage proves the one-prompt path.
        self.assertEqual([],row['submits'])
        self.assertEqual(['actor1','actor2','actor3'],row['stopped'])
        # Prose + fenced JSON and prose + raw JSON both commit; no-decision is
        # recorded once as invalid_answer, never as a retryable failure.
        self.assertEqual(['a'*64,'b'*64],[call['_host']['receipt_id'] for call in row['resolveCalls']])
        self.assertEqual(['understood in my own words']*2,
                         [call['understanding'] for call in row['understandingCalls'] if call['state']=='prepare'])
        # prepare/record for understanding finish before the board commit.
        self.assertEqual(['work_side_understanding:a','work_side_understanding:a','work_side_resolve:a',
                          'work_side_understanding:b','work_side_understanding:b','work_side_resolve:b'],
                         row['sideOrder'])
        self.assertEqual([],row['resolveCalls'][0]['_host']['reported_sends'])
        self.assertNotIn('context_key',row['resolveCalls'][0]['_host'])
        self.assertEqual({'action':'answer','kind':'info','summary':'done2','reply':'answer2'},row['resolveCalls'][1]['decision'])
        self.assertEqual([{'category':'invalid_answer','receipt':'c'*64,
                           'reason':'side answer carries no decision JSON'}],row['failures'])
        self.assertTrue(row['first']['retired'])
        self.assertTrue(row['second']['retired'])
        self.assertIsNone(row['first']['current'])
        self.assertIsNone(row['second']['current'])
        prompt=row['spawns'][0]['prompt']
        # Router model remapping authenticates the side claim by this exact
        # non-secret generation together with event_id and parent_session_id.
        self.assertEqual(('a'*64,'claude:parent','1'*32),
                         (prompt['event_id'],prompt['parent_session_id'],prompt['claim_generation']))
        self.assertEqual(['1'*32,'2'*32,'3'*32],
                         [spawn['prompt']['claim_generation'] for spawn in row['spawns']])
        # The fork gets the board version and the items the event references,
        # never the whole board; it reads more with work_status on demand
        # (user 2026-11-18). The host keeps the full projection.
        self.assertTrue(prompt['workload']['compact'])
        self.assertNotIn('nodes',prompt['workload'])
        self.assertNotIn('queue_order',prompt['workload'])
        self.assertIn('work_status',prompt['workload']['read_more'])
        referenced={node['id']:node for node in prompt['workload']['referenced']}
        self.assertEqual({
            'condition':'Message 42 has an explicit yes or no answer',
            'lookup':'Read message 42 and verify the explicit answer',
            'prompt':'Ignore mentions; the answer must be explicit'},
            referenced['n21']['release_hint'])
        self.assertEqual('Message 42 yes/no answer',referenced['n21']['waiting_on'])
        self.assertNotIn('release_hint',referenced.get('n22',{}))
        self.assertEqual('question',prompt['original'])
        self.assertNotIn('side_context_key',prompt)
        self.assertNotIn('parent_context',prompt)
        self.assertEqual('claude:parent',row['resolveCalls'][0]['session_id'])
        self.assertEqual({'input_tokens':3},row['resolveCalls'][0]['usage'])

    def test_claim_generation_is_validated_before_native_spawn(self):
        invalid = ('missing', 'undefined', 'null', 'number', 'object', 'short', 'long', 'uppercase', 'nonhex', 'suffix')
        for kind in invalid:
            with self.subTest(kind=kind):
                row = self.run_harness('side_claim_generation_harness.mjs', kind)
                self.assertTrue(row['claimed'], row)
                self.assertEqual([], row['spawns'], row)
                self.assertEqual(1, len(row['failures']), row)
                self.assertEqual('processing_error', row['failures'][0]['category'])
                self.assertEqual('invalid_claim_generation', row['failures'][0]['reason'])
                self.assertEqual(1, len(row['receipts']), row)
                self.assertEqual(('pending', 'processing_error', 'invalid_claim_generation', True),
                                 (row['receipts'][0]['stage'], row['receipts'][0]['failure']['category'],
                                  row['receipts'][0]['failure']['reason'], row['receipts'][0]['failure']['recoverable']))
                self.assertEqual(0, row['mainCalls'])
                self.assertNotIn('claim generation check', json.dumps(row['logs']))
        exact = self.run_harness('side_claim_generation_harness.mjs', 'exact')
        self.assertTrue(exact['claimed'], exact)
        self.assertEqual(1, len(exact['spawns']), exact)
        self.assertEqual('a' * 32, exact['spawns'][0]['claim_generation'])
        self.assertEqual([], exact['failures'])
        self.assertEqual(1, len(exact['receipts']))
        self.assertEqual('processing', exact['receipts'][0]['stage'])
        self.assertNotIn('failure', exact['receipts'][0])
        self.assertEqual(0, exact['mainCalls'])

    def test_main_read_proof_is_wired_and_has_no_dead_export(self):
        side=(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js').read_text(encoding='utf8')
        register=(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js').read_text(encoding='utf8')
        # Causal proof only: no messageCount or plain messages() comparison
        # (owned probe append-views-54579a2e shows neither counts plugin rows).
        for export in ('export async function sideMainTurnStart($,text)', 'export async function sideMainStep($)',
                       'export async function sideMainFence($)'):
            self.assertIn(export, side)
        self.assertIn('await sideMainTurnStart($, e.text)', register)
        self.assertIn('await sideMainStep($)', register)
        self.assertIn('await sideMainFence($)', register)
        self.assertNotIn('$,messageCount', side)
        self.assertNotIn('sideMainStep($, e.messageCount)', register)
        self.assertNotIn('await $.session.messages()', side)
        self.assertNotIn('sideMainRequest',side+register)

    def test_an_event_with_its_own_text_needs_no_walkie_read(self):
        # Andy 2026-11-18 (user design): no host thread read before a decision;
        # the fork decides from the event and main's inherited transcript.
        row=self.run_harness('side_intake_harness.mjs')
        # Only the host's own sends (the understanding) reach Walkie; no read.
        self.assertEqual([],[args for args in row['walkieReads'] if 'after' in args or 'operation_id' in args])
        self.assertEqual([{'text':'understood in my own words'}]*2,row['walkieReads'])
        prompt=row['spawns'][0]['prompt']
        self.assertNotIn('source_message',prompt)
        self.assertEqual({'conversation_id':'34886995-4a66-4527-a618-814e9cb0a5bf','root_id':'40','target_message_id':'41'},
                         prompt['source_ref'])
        self.assertEqual('event_only',prompt['source_context'])
        self.assertIn('호스트는 원본 스레드를 대신 읽어 주지 않습니다',prompt['instruction'])
        self.assertIn('스레드\n전체는 읽지 마십시오',prompt['instruction'])

    def test_walkie_source_reads_only_a_cut_events_target_and_fails_closed(self):
        row=self.run_harness('side_prefetch_harness.mjs')
        self.assertEqual(20,len(row))
        # An event on a 2,400-reply thread: decided with 0 Walkie reads.
        self.assertEqual((1,'event_only',[]),(row['long_thread']['spawns'],row['long_thread']['source'],row['long_thread']['walkieReads']))
        self.assertEqual('2440',row['long_thread']['sourceRef']['target_message_id'])
        self.assertFalse(row['long_thread']['hasThread'])
        for name in ('plain_text','none_referenced','null_none_referenced'):
            with self.subTest(case=name):
                self.assertEqual((1,[]),(row[name]['spawns'],row[name]['walkieReads']))
        self.assertEqual((1,'none_referenced',True),(row['null_none_referenced']['spawns'],row['null_none_referenced']['source'],row['null_none_referenced']['truncated']))
        self.assertIn('빠진 내용을 가정하거나 지어내지 말고',row['null_none_referenced']['instruction'])
        self.assertIn('[잘림]',row['null_none_referenced']['instruction'])
        # A cut or textless event: exactly one read, of its target (after target-1).
        for name,after,target in (('truncated_ok','40','41'),('textless_ok','40','41'),('root_target_ok','39','40'),
                                  ('big_id','900719925474099312','900719925474099313')):
            with self.subTest(case=name):
                case=row[name]
                self.assertEqual((1,'verified_target',[{'tool':'walkie_read_messages','after':after}],target),
                                 (case['spawns'],case['source'],case['walkieReads'],case['target']['id']))
        self.assertEqual('the whole message',row['truncated_ok']['target']['text'])
        # Credential-shaped fields of the read message stay hidden.
        self.assertFalse(row['truncated_ok']['secretLeak'])
        self.assertEqual('\u2039hidden 12 chars\u203a',row['truncated_ok']['hiddenToken'])
        refusals={'null_thread_reference':('invalid_request','Walkie source classification missing'),
                  'malformed_spec':('invalid_request','Source context specification malformed'),
                  'invalid_classification':('invalid_request','Walkie message_id malformed'),
                  # A forged target outside the event's thread is still refused.
                  'forged_target':('source_context','Source target is not in the event thread'),
                  'forged_root_target':('source_context','Source target is not in the event thread'),
                  'wrong_lineage':('source_context','Source thread target lineage mismatch'),
                  'other_conversation':('source_context','Source thread message identity malformed'),
                  'target_missing':('source_context','Source target message missing'),
                  'read_error':('source_context','Source target read failed'),
                  'read_throws':('source_context','Source target read failed'),
                  'malformed_response':('source_context','Source target response malformed'),
                  'oversize':('source_context','Source read exceeds per-call limit')}
        for name,(category,reason) in refusals.items():
            with self.subTest(case=name):
                self.assertEqual(0,row[name]['spawns'])
                self.assertEqual([{'category':category,'reason':reason}],row[name]['failures'])
                self.assertLessEqual(len(row[name]['walkieReads']),1)

    def run_persisted_harness(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_persisted_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'tests/side_persisted_stub_fixture.json',path/'persisted-stub-fixture.json')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True,timeout=60)
        return json.loads(done.stdout)

    def test_a_cut_events_persisted_target_read_and_spawn_proof(self):
        # Owned probe mcp-size-none-ee41a3e2 (Claude Code 2.1.321): a >25K-token
        # MCP text result reaches $.mcp.call as a stub naming the saved file. The
        # one host read left (a cut event's target) still follows it exactly.
        row=self.run_persisted_harness()
        self.assertEqual((1,1,None),(row['persisted_ok']['spawns'],row['persisted_ok']['reads'],row['persisted_ok']['failure']))
        self.assertEqual('45',row['persisted_ok']['target']['id'])
        # Claude Code 2.1.326 adds -<1-6 lowercase base-36> after the ms (official
        # CHANGELOG 2.1.326, #105138). KOBE 2026-11-17 quarantined 18 P3 events
        # because this name was refused; both forms must be read.
        for name,saved in (('persisted_suffix_ok','-la554o.txt'),('persisted_suffix_digits_ok','-123456.txt'),
                           ('persisted_suffix_short_ok','-a.txt')):
            with self.subTest(case=name):
                self.assertTrue(row[name]['saved'].endswith(saved))
                self.assertEqual((1,None),(row[name]['spawns'],row[name]['failure']))
        exact="Source persisted result is not this session's exact file"
        refusals={'other_session':exact,
                  'other_tool':exact,
                  'outside_config':exact,
                  # The suffix is 1-6 of [0-9a-z]: nothing else, and only one.
                  'suffix_upper':exact,'suffix_long':exact,'suffix_empty':exact,'suffix_double':exact,
                  'suffix_dot':exact,'suffix_underscore':exact,
                  'suffix_other_session':exact,'suffix_other_tool':exact,'suffix_outside_config':exact,
                  # projects/./ or projects/../ would resolve outside projects/.
                  'project_dot':exact,'project_dotdot':exact,
                  'size_mismatch':'Source persisted result size mismatch',
                  'unreadable':'Source persisted result unreadable',
                  'per_call_limit':'Source read exceeds per-call limit'}
        for name,reason in refusals.items():
            with self.subTest(case=name):
                self.assertEqual(0,row[name]['spawns'])
                self.assertEqual({'category':'source_context','reason':reason},row[name]['failure'])
        proof={p['label']:p['result'] for p in row['persisted_ok']['proof']}
        self.assertEqual({'not_fork':'deny','named':'deny','subagent':'deny','wrong_type':'deny',
                          'other_plugin':'deny','unrelated':None,'exact':'authorized'},proof)

    def test_non_ascii_persisted_result_is_compared_in_utf16_units(self):
        # Owned probes mcp-size-unicode-http-fafc8267 and -stdio-40e5d046
        # (Claude Code 2.1.321): the stub's count is the saved text's UTF-16
        # length (JS .length), never its code points or UTF-8 bytes.
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_persisted_unicode_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'tests/side_persisted_stub_unicode_fixture.json',path/'persisted-stub-unicode-fixture.json')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True,timeout=60)
        row=json.loads(done.stdout)
        meta=row['meta']
        self.assertTrue(meta['surrogates'])
        self.assertEqual(meta['stated'],meta['js_length'])
        self.assertLess(meta['code_points'],meta['js_length'])
        self.assertGreater(meta['utf8_bytes'],meta['js_length'])
        self.assertEqual((1,None,True),(row['utf16_exact']['spawns'],row['utf16_exact']['failure'],row['utf16_exact']['exact_text']))
        for name in ('code_point_count','utf8_count'):
            with self.subTest(case=name):
                self.assertEqual(0,row[name]['spawns'])
                self.assertEqual({'category':'source_context','reason':'Source persisted result size mismatch'},row[name]['failure'])

    def run_time_limit(self, mode):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_time_limit_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs'),mode],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        return json.loads(done.stdout)

    def assert_one_safe_wake(self, row, busy, attempt='w1', record_calls=1):
        # The payload is appended exactly once, busy or idle. No prompt holds
        # it: an idle main gets one wake prompt, a busy main none.
        delivered=row['appends']
        self.assertEqual(1,len(delivered))
        self.assertEqual(0,row['payload_submits'])
        self.assertEqual(0 if busy else 1,row['wake_prompts'])
        self.assertIn('related Work n7',delivered[0])
        self.assertIn('work_side_handoff',delivered[0])
        self.assertIn('within 4 hours',delivered[0])
        self.assertNotIn('raw secret thread',delivered[0])
        for text in delivered+row['submits']:
            # No attempt id or marker reaches main; transcript text is not evidence.
            self.assertNotIn('ciel-side-wake',text)
            self.assertNotIn(row['durable']['attempt_id'],text.replace('"'+'e'*64+'"','').replace('e'*12,''))
            self.assertNotIn('raw secret thread',text)
        for text in row['submits']:
            self.assertNotIn('within 4 hours',text)
        self.assertEqual(('appended','started',attempt),
                         (row['durable']['state'],row['durable']['phase'],row['durable']['attempt_id']))
        self.assertEqual(record_calls,sum(w['state']=='appended' for w in row['wakes']))
        # The append uuid is the durable receipt; message text is not stored.
        self.assertEqual('append-1',row['durable']['receipt'])
        # The idle wake prompt started main's turn, and that turn's first step's
        # exact messageCount covered the row; a queued prompt alone never reads.
        self.assertEqual(not busy,bool(row['durable'].get('read_at')))

    def test_time_limit_wakes_main_immediately_busy_or_idle_without_raw_thread(self):
        for mode in ('busy','idle'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assert_one_safe_wake(row,mode=='busy')
                states=[w['state'] for w in row['wakes']]
                # The prompt's turn steps (read) independently of its submit
                # record (prompt_queued); either may land first.
                self.assertEqual(['prepare','start','appended']+([] if mode=='busy' else ['prompt_prepare']),
                                 states[:3 if mode=='busy' else 4])
                self.assertEqual([] if mode=='busy' else ['prompt_queued','read'],sorted(states[4:]))
                self.assertEqual(1,row['claims'])

    def test_concurrent_completion_and_poll_prepare_one_wake(self):
        # E2E run-a-20261111-105350: the time-limit commit and a concurrent
        # claim both scheduled one event; two prepares, two prompt.submit calls.
        for mode in ('race:idle','race:busy'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assertEqual(1,row['prepares_while_paused'])
                self.assertTrue(row['poll_returned_while_paused'])
                self.assertEqual(1,sum(w['state']=='prepare' for w in row['wakes']))
                self.assert_one_safe_wake(row,mode=='race:busy')

    def test_reloaded_instances_both_paused_in_prepare_submit_once(self):
        # Two module instances reach prepare together. The first intent is
        # leased; the other gets already_intent and never starts.
        row=self.run_time_limit('reload')
        self.assertEqual(2,row['prepares_while_paused'])
        self.assertEqual(1,sum(w['state']=='start' for w in row['wakes']))
        self.assert_one_safe_wake(row,False,'w1')

    def test_same_process_reload_supersedes_intent_and_fences_old_start(self):
        # owner_nonce is a fence only. Inside the old module's intent lease the
        # reloaded module does not supersede; after it, it does (no delivery was
        # authorized) and the old module's start is refused.
        row=self.run_time_limit('reload-intent')
        self.assertEqual({'prepares':2,'deliveries':0},row['inside_lease'])
        self.assertEqual(2,row['start_calls'])
        self.assert_one_safe_wake(row,False,'w2')

    def test_intent_lease_ends_reviewer_livelock_schedule(self):
        # Without a lease each instance superseded the other before start.
        row=self.run_time_limit('livelock')
        self.assert_one_safe_wake(row,False,'w1')
        self.assertLessEqual(row['rounds_clock'],6000)
        self.assertEqual(1,row['start_calls'])

    def test_crashed_intent_recovers_only_after_its_lease(self):
        row=self.run_time_limit('expired-intent')
        self.assertEqual({'deliveries':0,'owner':'crashed-module'},row['inside_lease'])
        self.assert_one_safe_wake(row,False,'w1')

    def test_reload_loser_is_not_cached_and_reelects_after_owner_fails(self):
        # Review blocker 1: B lost while A held started; A's append was denied
        # (definite failure); B must re-elect once instead of suppressing forever.
        for mode in ('reload-failed','stale-loser'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                # Two append calls, the first denied; no prompt holds the payload.
                self.assertEqual((2,0),(len(row['appends']),row['payload_submits']))
                self.assertEqual(('appended','w2','append-2'),
                                 (row['durable']['state'],row['durable']['attempt_id'],row['durable']['receipt']))
                states=[w['state'] for w in row['wakes']]
                self.assertEqual((1,1),(states.count('failed'),states.count('appended')))

    def test_started_row_without_outcome_is_never_auto_delivered(self):
        # Review blocker 2: started by a module that is gone. No delivery and
        # no fabricated failure; it stays started (shown as delivery uncertain).
        row=self.run_time_limit('orphan-started')
        self.assertEqual([],row['appends']+row['submits'])
        self.assertEqual(('submitting','started','w0'),
                         (row['durable']['state'],row['durable']['phase'],row['durable']['attempt_id']))
        self.assertNotIn('prepare',[w['state'] for w in row['wakes']])

    def test_lost_prepare_response_is_adopted_by_same_owner(self):
        row=self.run_time_limit('lost-prepare')
        self.assertEqual({'deliveries':0,'state':'submitting','phase':'intent'},row['after_loss'])
        self.assert_one_safe_wake(row,False)

    def test_prepare_failure_releases_reservation_and_next_poll_retries(self):
        row=self.run_time_limit('retry')
        self.assertEqual({'prepares':1,'deliveries':0,'state':'pending'},row['after_failure'])
        self.assertEqual(2,sum(w['state']=='prepare' for w in row['wakes']))
        self.assert_one_safe_wake(row,False)

    def test_record_failure_retries_record_only(self):
        for mode,busy in (('record-fail',False),('record-fail:busy',True)):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                # Three transport failures; the owner's own result is recorded
                # again and the 4th call commits, for a prompt and an append
                # alike. Owned probe state-fence-74f330ba: no
                # $.session.messages() row carries the append uuid, so the host
                # never reconciles from the transcript.
                self.assert_one_safe_wake(row,busy,record_calls=4)
                self.assertEqual(1,sum(w['state']=='start' for w in row['wakes']))
                self.assertEqual(0,sum(w['state']=='reconcile' for w in row['wakes']))

    def test_wake_prompt_drop_or_throw_is_submitted_once_more_and_never_touches_the_wake(self):
        # Item 4. A wake prompt holds no payload, so its drop or throw decides
        # nothing about the event: the row is already appended under the one
        # attempt. The prompt is submitted once more and the row is read.
        for mode in ('drop','throw'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assertEqual((1,2,0),(len(row['appends']),row['wake_prompts'],row['payload_submits']))
                self.assertEqual(('appended','w1','append-1',True),
                                 (row['durable']['state'],row['durable']['attempt_id'],row['durable']['receipt'],
                                  bool(row['durable'].get('read_at'))))
                states=[w['state'] for w in row['wakes']]
                self.assertEqual(['prepare','start','appended','prompt_prepare','prompt_'+('failed' if mode == 'drop' else 'uncertain'),
                                  'prompt_prepare'],states[:6])
                self.assertEqual(['prompt_queued','read'],sorted(states[6:]))

    def test_transcript_rows_never_settle_an_append_with_no_known_outcome(self):
        # Review: marker-substring reconciliation was spoofable. Unrelated
        # assistant/user rows with the marker or event text never settle. The
        # append threw, so its outcome is unknown: uncertain, never sent again.
        for mode in ('spoof','append-throw'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assertEqual((1,0),(len(row['appends']),len(row['submits'])))
                self.assertEqual(('uncertain','started'),(row['durable']['state'],row['durable']['phase']))
                self.assertEqual(['prepare','start','uncertain'],[w['state'] for w in row['wakes']])

    def test_append_deny_fails_and_reelects_and_a_turn_that_ended_meanwhile_is_woken(self):
        row=self.run_time_limit('deny')
        self.assertEqual((2,0),(len(row['appends']),len(row['submits'])))
        self.assertEqual(('appended','w2','append-2'),
                         (row['durable']['state'],row['durable']['attempt_id'],row['durable']['receipt']))
        self.assertEqual(1,sum(w['state']=='failed' for w in row['wakes']))
        # Item 7. Main was busy when the wake was elected and idle when the
        # append answered: no request of that turn carried the row. It is
        # appended once and main is woken by a prompt that holds none of it.
        row=self.run_time_limit('append-idle')
        self.assert_one_safe_wake(row,False)

    def test_new_host_on_old_router_delivers_once_and_records_legacy_attempt(self):
        # A new host on an old/reused router (side_wake 4b85569d) lost the wake:
        # protocol fields were ignored, and the host required phase 'intent'.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router')
        self.assertEqual((1,0,1),(len(row['appends']),row['payload_submits'],row['wake_prompts']))
        # No read is sent to an old router (it has no such state).
        self.assertEqual(['prepare','appended'],[w['state'] for w in row['wakes']])
        self.assertEqual(('appended',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        # The old duplicate prepare returns the stored submitting row with its
        # attempt; the host must not deliver that same attempt twice.
        row=self.run_time_limit('old-router-dup')
        self.assertEqual({'state':'submitting','deliveries':1},row['after_record_failure'])
        # Repeated prepares return the same old attempt: no second delivery,
        # only the record is retried until it commits.
        self.assertEqual((1,0),(len(row['appends']),row['payload_submits']))
        self.assertGreaterEqual(sum(w['state']=='prepare' for w in row['wakes']),2)
        self.assertEqual(('appended',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))

    def test_old_router_attempt_is_delivered_once_across_module_instances(self):
        # review-side-wake: with the old side_wake contract both instances got
        # the same {state:'submitting', attempt_id} and both submitted. The
        # host-held $.state claim (ifVersion CAS) lets exactly one deliver.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router-reload')
        self.assertEqual(2,sum(w['state']=='prepare' for w in row['wakes']))
        self.assertEqual((1,0),(len(row['appends']),row['payload_submits']))
        self.assertEqual(('appended',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        # The delivery's own identity was claimed, then replaced by its bounded
        # terminal row: the same attempt is never sent again. The wake prompt
        # (no payload) has its own identity beside it.
        self.assertEqual(['ciel-work/legacyWakes',{'value':[old_attempt],'version':1}],row['host_state'][0])
        name,held=row['host_state'][1]
        self.assertEqual('ciel-work/promptPending',name)
        self.assertEqual([('side:'+'e'*64,'terminal','queued',16)],
                         [(r['key'],r['status'],r['outcome'],len(r['ref'])) for r in held['value'] if r['key'].startswith('side:')])
        self.assertEqual(['wake:'+'e'*64],[r['key'] for r in held['value'] if not r['key'].startswith('side:')])

    def test_legacy_fence_exhaustion_still_delivers_once(self):
        # This harness contends only legacyWakes, so all 32 legacy claims miss
        # but the per-key prompt-registry claim lands. The attempt is therefore
        # delivered once and recorded through the legacy path. The reviewer's
        # `exhaust` probe contends both keys, so that probe fails closed instead.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router-exhaust')
        self.assertEqual(32,row['foreign_writes'])
        self.assertEqual((1,0),(len(row['appends']),row['payload_submits']))
        self.assertEqual(('appended',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        self.assertEqual(['prepare','appended'],[w['state'] for w in row['wakes']])

    def test_legacy_fence_without_host_state_fails_closed_at_the_prompt_registry(self):
        # On an engine without $.state the legacy claim still falls back to the
        # module Set (unchanged), but the host-held claim of the delivery key
        # cannot land, so neither instance delivers. Before the registry this
        # delivered twice. The attempt is recorded uncertain (as it was at the
        # prompt's idle window): visible, never sent.
        row=self.run_time_limit('old-router-reload:nostate')
        self.assertEqual(0,len(row['submits'])+len(row['appends']))
        self.assertEqual([],row['host_state'])
        self.assertEqual('uncertain',row['durable']['state'])

    def test_malformed_protocol2_prepare_fails_closed_with_bounded_log(self):
        row=self.run_time_limit('malformed')
        self.assertEqual([],row['appends']+row['submits'])
        self.assertEqual(['prepare'],[w['state'] for w in row['wakes']])
        self.assertEqual(1,len(row['logs']))
        self.assertIn('malformed protocol 2 prepare',row['logs'][0])
        self.assertNotIn('b'*32,row['logs'][0])
        self.assertLess(len(row['logs'][0]),300)

    def test_host_never_writes_wake_secrets_to_prompt_log_pane_or_fork(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_secret_walk_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        row=json.loads(done.stdout)
        # The fresh wake was appended and one fork prompt was built. This
        # legacy scripted backend has no prompt-lease state, so no wake prompt
        # is issued without the durable fence.
        self.assertEqual(1,len(row['sinks']['appends']))
        self.assertIn('Ciel side event requires main action',row['sinks']['appends'][0])
        self.assertEqual([],row['sinks']['prompts'])
        self.assertEqual(1,len(row['sinks']['spawns']))
        for name,values in row['sinks'].items():
            text=json.dumps(values)
            with self.subTest(sink=name):
                for secret in row['secrets']:
                    self.assertNotIn(secret,text)
                if name == 'spawns':
                    self.assertEqual(1,text.count(row['claim_generation']))
                    self.assertIn('claim_generation',text)
                else:
                    self.assertNotIn(row['claim_generation'],text)
                # No unrelated secret-shaped 32-hex value may leak. The one
                # exact generation in the owned fork prompt is intentional.
                shaped=re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])',text)
                self.assertEqual([row['claim_generation']] if name == 'spawns' else [],shaped)

    def test_pending_main_wake_never_holds_side_claims(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_wake_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        row=json.loads(done.stdout)
        # Prody 2026-11-09: the wake prompt waited 63 minutes for main's turn to
        # end, and every claim waited with it. Polls now return while it waits.
        self.assertEqual(['returned','returned'],[row['firstPoll'],row['secondPoll']])
        self.assertEqual(['Side SSE '+'a'*12],row['whilePending']['spawns'])
        # Each event is elected and started once and duplicate offers are
        # suppressed. Each started wake appends its payload at once and submits
        # its own wake prompt under its own key, never holding claims; the
        # engine, not the module, holds a prompt until main is idle (owned
        # probes prompt-gate-0dd78bf3, prompt-registry-e2bed500). There is no
        # module-wide prompt queue, and no prompt holds a payload.
        self.assertEqual((2,2),(row['whilePending']['appends'],row['whilePending']['submits']))
        self.assertEqual((['w1','w2'],['w1','w2'],0),(sorted(row['appends']),sorted(row['wakePrompts']),row['payloadPrompts']))
        calls=[(call['event_id'],call['state'],call['attempt_id']) for call in row['wakeCalls']]
        self.assertEqual(sorted([('w1','prepare',None),('w1','start','attempt-w1'),('w1','appended','attempt-w1'),
                                 ('w1','prompt_prepare',None),('w1','prompt_queued','prompt-w1'),
                                 ('w2','prepare',None),('w2','start','attempt-w2'),('w2','appended','attempt-w2'),
                                 ('w2','prompt_prepare',None),
                                 # Only w1's wake prompt entered: its own token
                                 # proves only w1 read in that turn's first
                                 # request; w2 is fenced for the next request.
                                 ('w1','read',None)],key=str),sorted(calls,key=str))

    # Per-key prompt registry (side-intake.js sidePromptSubmit). Owned real
    # engine run prompt-registry-e2bed500 (Claude Code 2.1.322) measured the
    # same shapes; these fake-host cases pin each branch.
    def run_registry(self, *args):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/prompt_registry_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs'),*args],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        return json.loads(done.stdout)

    def test_duplicate_prompt_key_returns_the_exact_promise_and_submits_once(self):
        row=self.run_registry('identity')
        # Direct identity of the exported return value (no .then wrapper).
        self.assertTrue(row['identical'])
        self.assertEqual(['first'],row['calls'])
        self.assertEqual((True,False),(row['pendingBefore'],row['pendingAfter']))
        self.assertEqual(['queued','queued'],row['results'])
        self.assertEqual((['side:state'],[]),(row['registryBefore'],row['registryAfter']))

    def test_latent_prompt_key_never_blocks_other_keys(self):
        row=self.run_registry('independent')
        self.assertEqual(['A','B','N'],row['issued'])
        self.assertEqual({'a':None,'b':'queued','n':'queued','registry':['side:A'],'pendingA':True},row['whileA'])
        self.assertEqual({'a':'uncertain','registry':[],'pendingA':False},row['afterWindow'])

    def test_idle_window_never_runs_during_a_main_turn(self):
        row=self.run_registry('turns')
        self.assertEqual((1,0),(row['liveAtIssue'],row['liveInTurn']))
        self.assertEqual({'value':None,'pending':True},row['inTurn'])
        # Issued while busy: no window until turn.complete arms one for each key.
        self.assertEqual((0,2),(row['liveBusyIssue'],row['liveAfterTurn']))
        self.assertEqual({'a':'uncertain','b':'uncertain','registry':[]},row['after'])

    def test_same_prompt_key_from_two_module_instances_is_submitted_once(self):
        row=self.run_registry('cross-instance')
        self.assertEqual(1,row['submits'])
        self.assertEqual(['parallel:L1'],row['registryWhile'])
        self.assertEqual('queued',row['ownerValue'])
        # The waiting instance keeps its marker until its own idle window, then
        # reads the owner's durable terminal row instead of inventing uncertain.
        self.assertTrue(row['waiterPendingAfterOwner'])
        self.assertEqual(('queued',[]),(row['waiterValue'],row['registryAfter']))

    def test_reload_expiry_is_terminal_for_the_same_ref_and_a_new_ref_may_issue(self):
        row=self.run_registry('reload')
        self.assertFalse(row['beforeHydrate'])
        self.assertEqual({'pending':True,'exactRef':True,'otherRef':False,'live':1},row['hydrated'])
        self.assertEqual(0,row['noResubmit'])
        self.assertEqual({'duplicate':'uncertain','registry':[],'terminals':['side:R:uncertain'],'pending':False},
                         row['released'])
        # Same key + ref after expiry: terminal in this and a later instance.
        self.assertEqual({'second':'uncertain','third':'uncertain','rawSubmits':0},row['sameRef'])
        # A late raw settlement of the old attempt keeps the terminal outcome
        # and never clears the new attempt's pending row.
        self.assertEqual({'old':'uncertain','registry':['side:R'],'terminals':['side:R:uncertain'],
                          'sameGeneration':True,'retryPending':True},row['afterStale'])
        self.assertEqual({'retry':'queued','registry':[],'terminals':['side:R:uncertain','side:R:queued']},row['final'])
        # Old attempt once, person-confirmed new attempt once.
        self.assertEqual(2,row['submits'])

    def test_duplicate_during_or_after_terminalization_never_resubmits(self):
        row=self.run_registry('dup-terminal')
        self.assertEqual(('uncertain','uncertain','uncertain'),(row['first'],row['during'],row['after']))
        # A reproduced released-wait notice with the same text is terminal too.
        self.assertEqual(['queued','queued'],row['notice'])
        self.assertEqual(['D','N'],row['rawSubmits'])
        self.assertEqual((['side:D:uncertain','notice:N1:queued'],[]),(row['terminals'],row['registry']))

    def test_terminal_rows_are_capped_without_evicting_pending_rows(self):
        row=self.run_registry('terminal-cap')
        self.assertEqual('queued',row['value'])
        self.assertEqual(['parallel:P0','parallel:P1','parallel:P2'],row['pending'])
        self.assertEqual((200,False,True,True),(row['terminalCount'],row['oldestKept'],row['secondOldestKept'],row['newKept']))

    def test_clock_failure_at_idle_settles_uncertain_without_raw_submit(self):
        row=self.run_registry('clock-failure')
        self.assertEqual({'value':'uncertain','raw':0,'pending':False,'terminals':['side:CF:uncertain']},
                         {k:row[k] for k in ('value','raw','pending','terminals')})

    def test_transient_reassert_failure_keeps_the_exact_row_clearable(self):
        row=self.run_registry('reassert-transient')
        self.assertEqual({'value':'queued','registry':[],'terminals':['side:O:queued']},
                         {k:row[k] for k in ('value','registry','terminals')})

    def test_prompt_drop_or_throw_clears_the_key(self):
        row=self.run_registry('faults')
        self.assertEqual({'value':'failed','registry':[],'pending':False},row['drop'])
        self.assertEqual({'value':'uncertain','registry':[],'pending':False},row['throw'])

    def test_unclaimable_prompt_fails_closed_until_its_idle_window(self):
        row=self.run_registry('unclaimable')
        # Full: the 200 unresolved rows are never evicted or rewritten.
        self.assertEqual((200,7,False),(row['full']['before']['rows'],row['full']['before']['version'],
                                        row['full']['before']['hasKey']))
        self.assertEqual((200,7),(row['full']['rowsAfter'],row['full']['versionAfter']))
        for name in ('full','contended','oversized','throws','absent'):
            with self.subTest(case=name):
                case=row[name]
                self.assertEqual((0,True,None,1),(case['before']['calls'],case['before']['pending'],
                                                  case['before']['value'],case['before']['live']))
                self.assertFalse(case['before']['hasKey'])
                # Same-module duplicate shares. Another instance also fails
                # closed: no raw submit, not an at-least-once fallback.
                self.assertTrue(case['dupShared'])
                self.assertEqual(0,case['callsAfter'])
                self.assertEqual(('uncertain','uncertain',False),
                                 (case['value'],case['secondValue'],case['pendingAfter']))

    def test_idle_windows_are_anchored_to_turn_events_and_late_raw_resolution_stays_uncertain(self):
        row=self.run_registry('windows')
        self.assertEqual((1,0),(row['armedAtIdleIssue'],row['liveInTurn']))
        # Three attempted expiries while main is busy (>15 s logically) do nothing.
        self.assertEqual({'value':None,'pending':True},row['afterLongTurn'])
        self.assertEqual((1,5000),(row['liveAfterComplete'],row['insideWindowMs']))
        # A new turn inside the window cancels it; a stale callback cannot release.
        self.assertEqual({'value':None,'pending':True,'live':0},row['afterCancelled'])
        self.assertEqual({'value':'uncertain','pending':False,'registry':[]},row['released'])
        # Raw settlement after expiry never overwrites uncertain or re-hides the row.
        self.assertEqual(('uncertain',[],1),(row['lateValue'],row['lateRegistry'],row['submits']))

    def test_clear_reasserts_unresolved_raw_fence_before_reload(self):
        row=self.run_registry('clear-reload')
        reassert=row['reassert']
        self.assertEqual((['parallel:Creassert'],True,True,1),
                         (reassert['afterReassert']['registry'],reassert['afterReassert']['sameGeneration'],
                          reassert['afterReassert']['pending'],reassert['afterReassert']['live']))
        self.assertTrue(reassert['hydrated'])
        self.assertEqual((0,1), (reassert['reloadRawSubmits'],reassert['firstRawSubmits']))
        self.assertEqual(['uncertain','uncertain'],reassert['values'])
        # Controls: without re-claim, or when it throws, reload sees no fence and
        # makes one raw submit. Durable side/review producers prevent this shape;
        # the direct harness isolates only the registry consequence.
        for variant in ('skip','fails'):
            with self.subTest(variant=variant):
                case=row[variant]
                self.assertEqual(([],False,True), (case['afterReassert']['registry'],
                                                   case['afterReassert']['sameGeneration'],
                                                   case['afterReassert']['pending']))
                self.assertFalse(case['hydrated'])
                self.assertEqual((1,1),(case['reloadRawSubmits'],case['firstRawSubmits']))
                self.assertEqual(['uncertain','queued'],case['values'])

    def test_registry_alone_forgets_a_terminal_identity_after_clear_and_reload(self):
        row=self.run_registry('notice-clear')
        # What the registry cannot do by itself: /clear erases a terminal row and
        # reassert handles unresolved entries only, so a reload followed by the
        # same key issues it once more. No payload rides on this any more: a
        # released-wait notice is claimed in the backend first
        # (test_release_notice_is_issued_at_most_once_across_state_loss) and a
        # wake prompt holds no payload.
        self.assertEqual((['notice:clear:queued'],[],1,'queued'),
                         (row['beforeClear'],row['afterReassert'],row['reloadRaw'],row['reloadValue']))
        # The original module still has its bounded local tombstone.
        self.assertEqual(('queued',1),(row['sameInstanceValue'],row['firstInstanceRaw']))

    # What main's conversation receives for one event (side_delivery_harness.mjs).
    # The host is an engine model from owned probes delivery-4f4c90b8,
    # prompt-gate-0dd78bf3 and prompt-registry-e2bed500; the backend is a fake
    # that follows side_wake.py. No engine is run here.
    def run_delivery(self, mode):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_delivery_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs'),mode],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        return json.loads(done.stdout)

    def test_idle_wake_appends_the_payload_once_and_wakes_main_with_a_prompt_that_holds_none(self):
        row=self.run_delivery('wake-idle')
        for name in ('before','after'):
            with self.subTest(at=name):
                view=row[name]
                # One stored payload row, appended; the one prompt holds no payload.
                self.assertEqual((1,1,0,1),(view['payloadRows'],view['payloadAppends'],view['payloadPrompts'],view['plainPrompts']))
        self.assertEqual('appended',row['before']['wake']['state'])
        # The turn that prompt started carried the payload exactly once.
        self.assertEqual(([1],1),(row['after']['requests'],row['after']['turns']))
        self.assertEqual({'state':'appended','phase':'started','read':True,'attempts':1},row['after']['wake'])

    def test_time_limit_wake_reaches_a_busy_main_in_its_next_request_before_the_turn_ends(self):
        # Item 7. The append is made while the turn runs and the next request of
        # that same turn carries it (owned probe delivery-4f4c90b8: inside the
        # tool-result message). No prompt is submitted and no turn end is awaited.
        row=self.run_delivery('wake-busy-next-request')
        running=row['whileTurnRuns']
        self.assertEqual((1,0,0,0),(running['payloadAppends'],running['payloadPrompts'],running['plainPrompts'],running['turns']))
        self.assertEqual('appended',running['wake']['state'])
        self.assertEqual(1,row['carriedByNextRequest'])
        self.assertEqual(([0,1,0],0,0),(row['after']['requests'],row['after']['plainPrompts'],row['after']['payloadPrompts']))

    def test_a_step_after_the_fence_marks_the_busy_append_read(self):
        # Causal proof (owned probe append-views-54579a2e, 2.1.323): the first
        # main step after a busy append may have been built before it, so it only
        # fences the row; the next request of the turn is built after that fence.
        row=self.run_delivery('wake-busy-next-request')
        self.assertFalse(row['whileTurnRuns']['wake']['read'])
        self.assertFalse(row['afterFirstStep']['wake']['read'])
        self.assertTrue(row['after']['wake']['read'])

    def test_a_step_that_may_predate_the_append_never_marks_read_and_main_is_woken(self):
        # The turn ended after the only step that may have been built before the
        # append: nothing proves the read, so the idle wake later carries the row.
        row=self.run_delivery('wake-busy-next-request:young')
        self.assertFalse(row['afterFirstStep']['wake']['read'])
        after=row['after']
        self.assertEqual((1,1,0,1),(after['payloadRows'],after['payloadAppends'],after['payloadPrompts'],after['plainPrompts']))

    def test_wake_appended_after_the_last_request_of_a_turn_still_wakes_main(self):
        # Item 7. The turn ended with no further request, so the appended row was
        # in no request of that turn. Main is woken; that turn carries it once.
        row=self.run_delivery('wake-busy-turn-ends')
        self.assertEqual((1,[0]),(row['afterTurnEnd']['payloadAppends'],row['afterTurnEnd']['requests']))
        after=row['after']
        self.assertEqual((1,1,0),(after['payloadRows'],after['plainPrompts'],after['payloadPrompts']))
        self.assertEqual(([0,1],1),(after['requests'],after['turns']))
        self.assertTrue(after['wake']['read'])

    def test_a_prompt_that_throws_leaves_the_event_delivered_once_and_never_uncertain(self):
        # Item 4. The prompt whose submit threw held no payload; the payload was
        # appended once and a second wake prompt starts the turn. No person acts.
        row=self.run_delivery('wake-prompt-throw')
        self.assertEqual('appended',row['afterThrow']['wake']['state'])
        after=row['after']
        self.assertEqual((1,1,0,2),(after['payloadRows'],after['payloadAppends'],after['payloadPrompts'],after['plainPrompts']))
        self.assertEqual(([1],1),(after['requests'],after['turns']))
        self.assertEqual({'state':'appended','phase':'started','read':True,'attempts':1},after['wake'])

    def test_a_prompt_held_past_its_idle_window_makes_nothing_uncertain_and_holds_no_payload(self):
        # Item 2. The idle window releases a wake prompt, never the event.
        row=self.run_delivery('wake-prompt-held')
        self.assertEqual(('appended',1,0),(row['afterWindow']['wake']['state'],row['afterWindow']['payloadAppends'],
                                           row['afterWindow']['payloadPrompts']))
        after=row['after']
        self.assertEqual((1,0,'appended',1),(after['payloadRows'],after['payloadPrompts'],after['wake']['state'],
                                             after['wake']['attempts']))
        self.assertEqual(1,sum(after['requests']))

    def test_append_hook_reconciles_store_then_throw_by_pinned_uuid(self):
        after=self.run_delivery('wake-append-throw:stored')['after']
        self.assertEqual((1,1,0),(after['payloadRows'],after['payloadAppends'],after['payloadPrompts']))
        self.assertEqual('appended',after['wake']['state'])

    def test_timer_append_store_then_throw_settles_from_one_exact_api_ref_row(self):
        # 2.1.323 (owned probe append-views-54579a2e L2): an append raised from a
        # timer skips this plugin's own session.append hook, but messages({as:
        # 'api'}) holds its user text. Exactly one row with the exact ref settles
        # it with a non-uuid receipt; nothing is sent again.
        row=self.run_delivery('wake-append-throw:timer')
        after=row['after']
        self.assertEqual((1,1,0),(after['payloadRows'],after['payloadAppends'],after['payloadPrompts']))
        self.assertEqual('appended',after['wake']['state'])
        self.assertRegex(row['receipt'],r'^ref:[0-9a-f]{16}$')
        self.assertEqual(1,row['appendCalls'])
        # The same ref in two user rows is an anomaly: it stays uncertain, is
        # logged as such, and is never resent.
        row=self.run_delivery('wake-append-throw:timer-twice')
        self.assertEqual('uncertain',row['after']['wake']['state'])
        self.assertEqual(1,row['appendCalls'])
        self.assertTrue(any('anomaly' in line and 'remains uncertain' in line for line in row['logs']))
        self.assertIsNone(row['receipt'])

    def test_an_append_with_no_known_outcome_stays_uncertain_and_is_never_sent_again(self):
        # Not solved (design.md): the row id cannot be chosen or looked up, so
        # neither a resend nor a silent drop is safe. It stays visible.
        for mode in ('wake-append-throw','wake-append-throw:busy'):
            with self.subTest(mode=mode):
                after=self.run_delivery(mode)['after']
                self.assertEqual((0,0,0),(after['payloadRows'],after['payloadPrompts'],after['plainPrompts']))
                self.assertEqual({'state':'uncertain','phase':'started','read':False,'attempts':1},after['wake'])

    def test_release_notice_is_issued_at_most_once_across_state_loss(self):
        # Item 1. The same event's completion is driven a second time after the
        # notice identity was lost: a fresh process, /clear then a reload, and 200
        # newer terminal rows. The backend claim on the event's receipt is what
        # remains. (That the real backend hands the same claim out twice is not
        # shown; test_side_notice_claim covers the real claim.) A same-process
        # reload keeps $.state, so that variant held before the claim existed.
        for variant in ('restart','reload','clear-reload','evict'):
            with self.subTest(variant=variant):
                row=self.run_delivery('notice:'+variant)
                self.assertEqual(1,row['firstIssue']['payloadRows'])
                after=row['after']
                # The notice text is stored in main's conversation exactly once.
                self.assertEqual(1,after['payloadRows'])
                self.assertEqual(1,sum(after['requests']))

    def test_release_notice_is_claimed_on_the_backend_each_time_and_appended_once(self):
        # How the fence holds: each instance asks the backend for the claim (2
        # asks), one is granted, and the one payload row is an append.
        for variant in ('restart','reload','clear-reload','evict'):
            with self.subTest(variant=variant):
                row=self.run_delivery('notice:'+variant)
                self.assertEqual((1,0),(row['after']['payloadAppends'],row['after']['payloadPrompts']))
                self.assertEqual(2,row['noticeClaims'])

    def test_release_notice_is_claimed_then_appended_for_an_idle_and_a_busy_main(self):
        first=self.run_delivery('notice:once')['firstIssue']
        self.assertEqual((1,1,0,1,'appended'),(first['payloadRows'],first['payloadAppends'],first['payloadPrompts'],
                                               first['plainPrompts'],first['notice']))
        self.assertEqual([1],first['requests'])
        first=self.run_delivery('notice:busy')['firstIssue']
        # Busy: the next step of the running turn carries the notice, but only
        # fences it (its build may predate the append); the turn then ends, so
        # one payload-free wake prompt follows.
        self.assertEqual((1,1,0,1,'appended'),(first['payloadRows'],first['payloadAppends'],first['payloadPrompts'],
                                               first['plainPrompts'],first['notice']))
        self.assertEqual([0,1],first['requests'])

    def test_release_notice_on_an_older_router_keeps_its_module_only_fence(self):
        # An older router has no notice claim. The notice is still delivered,
        # fenced by the module and $.state only, so a fresh process issues it
        # again. This path exists only against an older router build.
        row=self.run_delivery('notice:old-router')
        self.assertEqual((1,0),(row['firstIssue']['payloadRows'],row['firstIssue']['payloadPrompts']))
        self.assertEqual((2,0,None),(row['after']['payloadRows'],row['after']['payloadPrompts'],row['after']['notice']))

    def test_p1_uses_reserved_slot_and_is_shared_with_main(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_urgency_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
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

    def run_p0(self, activation):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_p0_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs'),activation],capture_output=True,text=True,encoding='utf8',check=True)
        return json.loads(done.stdout)

    def test_p0_event_is_decided_and_shown_as_p0(self):
        # Walkie P0 (emergency) reached the side actor as P1 on 2026-11-15 and
        # no fork opened. The fork pump after the side turn (register.js) takes
        # the P0 the host activated; this covers what the side actor is given.
        row=self.run_p0('ok')
        self.assertEqual('P0',row['sourcePriority'])
        self.assertTrue(row['instructsP0'])
        self.assertEqual(['P0'],row['resolvedPriority'])
        self.assertEqual(['Side P0 queued n9 · Production outage · P0 registered; fork launch pending (the outcome follows in a separate notice)'],row['appends'])

    def test_p0_activation_failure_is_shown_to_the_person(self):
        row=self.run_p0('fail')
        self.assertEqual(['Side P0 queued n9 · Production outage · P0 not activated: WorkParallelError: device ledger unavailable'],
                         row['appends'])

    def test_paged_host_results_reassemble_and_events_render_interpreted(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_intake_paging_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        row=json.loads(done.stdout)
        self.assertEqual(333,row['nodes'])
        self.assertGreater(row['pageCalls'],1)
        self.assertTrue(row['tokenSent'])
        # The native client's oversize text must be a named failure, not a JSON.parse message.
        self.assertIn('work_side_claim: router did not return JSON: Error: result',row['notJson'])
        self.assertIn('digest mismatch',row['mismatch'])
        self.assertEqual('[com.ciel.walkie.support.message · agent.pending_reminder] 첫 줄 둘째 줄',row['summary'])
        self.assertEqual('[com.ciel.walkie.support.message · agent.pending_reminder]', row['meta'])
        self.assertEqual('첫 줄 둘째 줄', row['message'])
        self.assertEqual('', row['plainMeta'])
        self.assertEqual('plain question second line', row['plainMessage'])
        rendered='\n'.join(row['lines'])
        self.assertIn('Text:\n  첫 줄\n  둘째 줄',rendered)
        self.assertIn('"activity_context": "‹hidden 344 chars›"',rendered)
        self.assertIn('"token": "‹hidden 12 chars›"',rendered)
        self.assertNotIn('S'*20,rendered)
        self.assertNotIn('secret-value',rendered)
        self.assertNotIn('"text"',rendered)
        self.assertEqual('plain question second line',row['plainSummary'])
        self.assertEqual(['plain question','second line'],row['plainLines'])

    def test_side_fork_has_no_technical_tool_fence(self):
        row=self.run_harness('side_intake_harness.mjs')
        # Operator decision 2026-11-11: restriction is prompt-only. The side hook
        # never denies or rewrites a call; every inherited tool (shell, edit,
        # Agent, send, runtime, Work) takes main's own native/Work-gate path.
        self.assertEqual([None]*8,row['passThrough'])

    def test_failed_owned_side_actor_is_removed_on_reload(self):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_cleanup_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js',path/'texts.generated.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        # Only the owned actor still listed live is stopped. A failed or completed
        # one has already ended; Claude Code 2.1.330 answers TaskStop on it "is
        # not running" (real load 2026-11-23). The foreign fork is never touched.
        self.assertEqual([
            {'tool':'TaskStop','task_id':'idle-side'},
        ],json.loads(done.stdout))

    def test_side_prompt_requires_answer_ack_queue_and_status_behaviors(self):
        source=(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js').read_text(encoding='utf8')
        self.assertIn('status: 현재 Work 상태를 직접 보고합니다',source)
        self.assertIn('info: 필요한 것만 모아 정리해서 답합니다',source)
        self.assertIn('request: 이 이벤트와 대화 기록으로 사용자의 요청을 이해한 뒤',source)
        # 2026-11-17: the restatement is understanding, sent before the board commit.
        self.assertIn('understanding: 이 메시지가 무엇을 묻거나 말한다고 이해했는지를',source)
        self.assertIn('"ACK"로 시작하지 말고',source)
        # ciel-walkie 2026-11-10: Celly 121710 and Daniel 121677 were both
        # replaced by a stale Grant 121686 decision copied from main context.
        # 2026-11-18: the instruction is written in Korean; the same rules, same order.
        self.assertIn('decision은 반드시 이 이벤트의 target_message_id', source)
        self.assertIn('오래된 이전 이벤트를 대신 쓰지 마십시오', source)
        self.assertIn('사람, 메시지 id, 승인, 결과, 요청된 결과가', source)
        # Owned run 2026-11-09: the recap dropped "mobile browsers", and a new
        # payment outage was merged into the active payment refactoring item.
        self.assertIn('understanding에는 사용자가 준 구체적 요구, 제약, 대상, 환경, 기한을 모두',source)
        self.assertIn('기존 항목은 이벤트가 그 항목이 요청한 결과에 관한\n  것일 때만 update합니다',source)
        self.assertIn('다른 문제, 실패, 사고, 기능은 같은 영역이나 시스템이라도 새 항목입니다',source)
        self.assertIn('급한 새 문제는 priority P1로 create합니다',source)
        self.assertIn('queue_position은 활성 목표 뒤 대기 목표들',source)
        self.assertIn('reply에서 큐 자리를 주장하지 마십시오. 호스트가 기록된 자리를 덧붙입니다',source)
        self.assertNotIn('act when the event requests it',source)

    def test_every_spawned_fork_prompt_states_prompt_only_prohibitions(self):
        row=self.run_harness('side_intake_harness.mjs')
        for spawn in row['spawns']:
            instruction=spawn['prompt']['instruction']
            # Prompt-only restriction (no tool fence), stated in every event.
            # Andy 2026-11-23: true for a fork only; a fresh actor starts empty.
            self.assertIn('이 액터는 메인의 모델, 도구, 권한 모드를 씁니다. session_mode가 "fork"이면',instruction)
            self.assertIn('"fresh"이면 메인의 대화 없이 빈 대화로 시작했습니다',instruction)
            self.assertIn('도구를 막는 장치가 아니라',instruction)
            for rule in ('- 코드, 설정, 그 밖의 파일을 쓰거나 고치거나 만들기',
                         '- Bash, PowerShell 등 셸 명령 실행',
                         '- 빌드, 테스트, 배포, 설치, 커밋, 푸시',
                         '- 런타임, 세션, 모델, 설정을 재시작하거나 바꾸거나 제어하기',
                         '- Work를 직접 변경하기 (work_* create/update/execute/report/handoff/resolve 등)',
                         '- Agent 호출, 에이전트·서브에이전트·포크를 시작하거나 재개하거나 메시지 보내기',
                         '- 원본이나 다른 채널에 ACK, 답장, SendMessage 등 무엇이든 보내기'):
                self.assertIn(rule,instruction)
            self.assertIn('판단 한 건을 기록하고 원본에 메시지를 보내는 일은 호스트만\n합니다.',instruction)
            self.assertIn('최종 답은 아래에 설명한 decision JSON 그대로여야 합니다',instruction)
            self.assertIn('{"understanding":"...","decision":{...},"expected_version":N}.',instruction)
            self.assertIn('target_message_id',instruction)

    def test_reply_route_override_is_not_mcp_inheritance(self):
        from ciel_runtime_support.recent_menu_text import menu_text
        from ciel_runtime_support.work_settings_menu import description
        key='work_side_intake_reply_connectors'
        self.assertEqual('External reply route / Walkie preset (JSON)',menu_text(key))
        text=description({},key)
        self.assertIn('Walkie preset',text)
        self.assertIn('The side model never sends',text)
        korean=description({'language':'ko'},key)
        self.assertIn('Walkie 프리셋',korean)
        self.assertIn('사이드 모델은 전송하지 않습니다',korean)
        for language in ('en','ko','ja','zh'):
            self.assertNotIn('MCP',menu_text(key,language))
