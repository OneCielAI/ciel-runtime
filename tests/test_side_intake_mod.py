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
    def run_harness(self, name):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests'/name,path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        return json.loads(done.stdout)

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
        # A durable main wake is submitted once although later claims offer it again.
        self.assertEqual(1,len(row['submits']))
        self.assertIn('"kind":"resume"',row['submits'][0])
        self.assertIn('clear_wait',row['submits'][0])
        self.assertIn('no new execution approval',row['submits'][0])
        self.assertEqual(['actor1','actor2','actor3'],row['stopped'])
        # Prose + fenced JSON and prose + raw JSON both commit; no-decision is
        # recorded once as invalid_answer, never as a retryable failure.
        self.assertEqual(['a'*64,'b'*64],[call['_host']['receipt_id'] for call in row['resolveCalls']])
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
        self.assertEqual(['n21'],prompt['workload']['queue_order'])
        self.assertFalse(prompt['workload']['truncated'])
        self.assertEqual('question',prompt['original'])
        self.assertNotIn('side_context_key',prompt)
        self.assertNotIn('parent_context',prompt)
        self.assertEqual('claude:parent',row['resolveCalls'][0]['session_id'])
        self.assertEqual({'input_tokens':3},row['resolveCalls'][0]['usage'])

    def test_walkie_prefetch_carries_full_bounded_conversation_exactly(self):
        row=self.run_harness('side_intake_harness.mjs')
        # One host read of the whole conversation from the start.
        self.assertEqual({'conversation_id':'34886995-4a66-4527-a618-814e9cb0a5bf','after':'39'},row['walkieReads'][0])
        self.assertEqual(('threads.read','40','0'),(row['walkieReads'][1]['operation_id'],row['walkieReads'][1]['arguments']['root_id'],row['walkieReads'][1]['arguments']['after']))
        source=row['spawns'][0]['prompt']['source_message']
        self.assertEqual('41',source['target_message_id'])
        self.assertEqual(('40','40'),(source['reply_to_message_id'],source['thread_root_id']))
        self.assertEqual(['40','41','42'],[m['id'] for m in source['messages']])
        target=source['messages'][1]
        # Exact text; the old 300-character display clip no longer applies.
        expected='move the existing work to queue position 2 '+'and keep every detail '*30
        self.assertEqual(expected,target['text'])
        self.assertGreater(len(target['text']),300)
        self.assertEqual('a later unrelated message',source['messages'][2]['text'])
        # Credential-shaped fields are still hidden.
        self.assertEqual('\u2039hidden 12 chars\u203a',target['sender_token'])
        self.assertNotIn('secret-value',json.dumps(row['spawns'][0]['prompt']))

    def test_walkie_prefetch_failure_fails_closed_before_spawn_or_reply(self):
        row=self.run_harness('side_prefetch_harness.mjs')
        self.assertEqual(18,len(row))
        self.assertEqual(1,row['ok_paged']['spawns'])
        self.assertEqual('verified_thread',row['ok_paged']['source'])
        self.assertEqual(['0','240'],row['ok_paged']['pages'])
        self.assertEqual(1,row['none_referenced']['spawns'])
        self.assertEqual('none_referenced',row['none_referenced']['source'])
        self.assertEqual((1,'none_referenced',True),(row['null_none_referenced']['spawns'],row['null_none_referenced']['source'],row['null_none_referenced']['truncated']))
        self.assertIn('do not assume or invent the missing content',row['null_none_referenced']['instruction'])
        self.assertIn('[잘림]',row['null_none_referenced']['instruction'])
        self.assertEqual('900719925474099311',row['big_id']['roots'][0]['after'])
        self.assertEqual((1,'verified_thread'),(row['big_id']['spawns'],row['big_id']['source']))
        for name,case in row.items():
            if name in ('ok_paged','none_referenced','null_none_referenced','big_id'):
                continue
            with self.subTest(case=name):
                self.assertEqual(0,case['spawns'])
                self.assertEqual(1,len(case['failures']))
                self.assertEqual('invalid_request' if name in ('malformed_spec','invalid_classification','null_thread_reference') else 'source_context',
                                 case['failures'][0]['category'])
                self.assertTrue(case['failures'][0]['reason'].startswith(('Source ', 'Walkie ')))
        self.assertEqual('Source thread root missing',row['missing_root']['failures'][0]['reason'])
        self.assertEqual('Source thread target missing',row['target_missing']['failures'][0]['reason'])
        self.assertEqual('Source thread reply lineage mismatch',row['wrong_lineage']['failures'][0]['reason'])
        self.assertEqual('Source thread root lineage malformed',row['null_lineage_mismatch']['failures'][0]['reason'])
        # A single 4 MiB+ page is refused at the per-call cap before parsing.
        self.assertEqual('Source read exceeds per-call limit',row['oversize']['failures'][0]['reason'])

    def test_persisted_mcp_result_context_budget_and_spawn_proof(self):
        # Owned probe mcp-size-none-ee41a3e2 (Claude Code 2.1.321): a >25K-token
        # MCP text result reaches $.mcp.call as a stub naming the saved file.
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/side_persisted_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'tests/side_persisted_stub_fixture.json',path/'persisted-stub-fixture.json')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True,timeout=60)
        row=json.loads(done.stdout)
        self.assertEqual((1,61,None),(row['persisted_ok']['spawns'],row['persisted_ok']['messages'],row['persisted_ok']['failure']))
        refusals={'other_session':"Source persisted result is not this session's exact file",
                  'other_tool':"Source persisted result is not this session's exact file",
                  'outside_config':"Source persisted result is not this session's exact file",
                  'size_mismatch':'Source persisted result size mismatch',
                  'unreadable':'Source persisted result unreadable',
                  'per_call_limit':'Source read exceeds per-call limit'}
        for name,reason in refusals.items():
            with self.subTest(case=name):
                self.assertEqual(0,row[name]['spawns'])
                self.assertEqual({'category':'source_context','reason':reason},row[name]['failure'])
        self.assertEqual(0,row['context_budget']['spawns'])
        self.assertIn('exceeds main context budget',row['context_budget']['failure']['reason'])
        self.assertEqual(1,row['context_room']['spawns'])
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
            done=subprocess.run(['node',str(path/'test.mjs'),mode],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        return json.loads(done.stdout)

    def assert_one_safe_wake(self, row, busy, attempt='w1', record_calls=1):
        delivered=row['appends']+row['submits']
        self.assertEqual(1,len(delivered))
        self.assertEqual(1,len(row['appends'] if busy else row['submits']))
        self.assertIn('related Work n7',delivered[0])
        self.assertIn('work_side_handoff',delivered[0])
        self.assertIn('within 4 hours',delivered[0])
        self.assertNotIn('raw secret thread',delivered[0])
        # No attempt id or marker reaches main; transcript text is not evidence.
        self.assertNotIn('ciel-side-wake',delivered[0])
        self.assertNotIn(row['durable']['attempt_id'],delivered[0].replace('"'+'e'*64+'"',''))
        outcome='appended' if busy else 'queued'
        self.assertEqual((outcome,'started',attempt),
                         (row['durable']['state'],row['durable']['phase'],row['durable']['attempt_id']))
        self.assertEqual(record_calls,sum(w['state']==outcome for w in row['wakes']))
        if busy:
            # The append uuid is the durable receipt; message text is not stored.
            self.assertEqual('append-1',row['durable']['receipt'])

    def test_time_limit_wakes_main_immediately_busy_or_idle_without_raw_thread(self):
        for mode in ('busy','idle'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assert_one_safe_wake(row,mode=='busy')
                self.assertEqual(['prepare','start','appended' if mode=='busy' else 'queued'],
                                 [w['state'] for w in row['wakes']])
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
        # Review blocker 1: B lost while A held started; A's prompt dropped
        # (definite failure); B must re-elect once instead of suppressing forever.
        for mode in ('reload-failed','stale-loser'):
            with self.subTest(mode=mode):
                row=self.run_time_limit(mode)
                self.assertEqual((2,0),(len(row['submits']),len(row['appends'])))
                self.assertEqual(('queued','w2'),(row['durable']['state'],row['durable']['attempt_id']))
                states=[w['state'] for w in row['wakes']]
                self.assertEqual((1,1),(states.count('failed'),states.count('queued')))

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

    def test_prompt_drop_reelects_and_throw_is_uncertain(self):
        row=self.run_time_limit('drop')
        self.assertEqual(2,len(row['submits']))
        self.assertEqual(('queued','w2'),(row['durable']['state'],row['durable']['attempt_id']))
        row=self.run_time_limit('throw')
        self.assertEqual(1,len(row['submits']))
        self.assertEqual('uncertain',row['durable']['state'])
        self.assertEqual(1,sum(w['state']=='prepare' for w in row['wakes']))

    def test_transcript_rows_never_settle_an_uncertain_prompt(self):
        # Review: marker-substring reconciliation was spoofable. Unrelated
        # assistant/user rows with the marker or event text never settle.
        row=self.run_time_limit('spoof')
        self.assertEqual(1,len(row['submits']))
        self.assertEqual(('uncertain','started'),(row['durable']['state'],row['durable']['phase']))
        self.assertNotIn('reconcile',[w['state'] for w in row['wakes']])

    def test_append_deny_fails_and_reelects_and_idle_after_append_never_prompts(self):
        row=self.run_time_limit('deny')
        self.assertEqual((2,0),(len(row['appends']),len(row['submits'])))
        self.assertEqual(('appended','w2','append-2'),
                         (row['durable']['state'],row['durable']['attempt_id'],row['durable']['receipt']))
        self.assertEqual(1,sum(w['state']=='failed' for w in row['wakes']))
        row=self.run_time_limit('append-idle')
        self.assert_one_safe_wake(row,True)

    def test_new_host_on_old_router_delivers_once_and_records_legacy_attempt(self):
        # A new host on an old/reused router (side_wake 4b85569d) lost the wake:
        # protocol fields were ignored, and the host required phase 'intent'.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router')
        self.assertEqual((1,0),(len(row['submits']),len(row['appends'])))
        self.assertEqual(['prepare','queued'],[w['state'] for w in row['wakes']])
        self.assertEqual(('queued',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        # The old duplicate prepare returns the stored submitting row with its
        # attempt; the host must not deliver that same attempt twice.
        row=self.run_time_limit('old-router-dup')
        self.assertEqual({'state':'submitting','deliveries':1},row['after_record_failure'])
        # Repeated prepares return the same old attempt: no second delivery,
        # only the record is retried until it commits.
        self.assertEqual(1,len(row['submits'])+len(row['appends']))
        self.assertGreaterEqual(sum(w['state']=='prepare' for w in row['wakes']),2)
        self.assertEqual(('queued',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))

    def test_old_router_attempt_is_delivered_once_across_module_instances(self):
        # review-side-wake: with the old side_wake contract both instances got
        # the same {state:'submitting', attempt_id} and both submitted. The
        # host-held $.state claim (ifVersion CAS) lets exactly one deliver.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router-reload')
        self.assertEqual(2,sum(w['state']=='prepare' for w in row['wakes']))
        self.assertEqual((1,0),(len(row['submits']),len(row['appends'])))
        self.assertEqual(('queued',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        # The delivery's own identity was claimed, then replaced by its bounded
        # terminal row (version 2): the same attempt is never sent again.
        self.assertEqual(['ciel-work/legacyWakes',{'value':[old_attempt],'version':1}],row['host_state'][0])
        name,held=row['host_state'][1]
        self.assertEqual(('ciel-work/promptPending',2),(name,held['version']))
        self.assertEqual([('side:'+'e'*64,'terminal','queued',16)],
                         [(r['key'],r['status'],r['outcome'],len(r['ref'])) for r in held['value']])

    def test_legacy_fence_exhaustion_still_delivers_once(self):
        # This harness contends only legacyWakes, so all 32 legacy claims miss
        # but the per-key prompt-registry claim lands. The attempt is therefore
        # delivered once and recorded through the legacy path. The reviewer's
        # `exhaust` probe contends both keys, so that probe fails closed instead.
        old_attempt='a'*31+'1'
        row=self.run_time_limit('old-router-exhaust')
        self.assertEqual(32,row['foreign_writes'])
        self.assertEqual((1,0),(len(row['submits']),len(row['appends'])))
        self.assertEqual(('queued',old_attempt),(row['durable']['state'],row['durable']['attempt_id']))
        self.assertEqual(['prepare','queued'],[w['state'] for w in row['wakes']])

    def test_legacy_fence_without_host_state_fails_closed_at_the_prompt_registry(self):
        # On an engine without $.state the legacy claim still falls back to the
        # module Set (unchanged), but the per-key prompt registry cannot claim
        # the delivery key, so neither instance raw-submits. Before the
        # registry this delivered twice. The started row stays visible as
        # delivery uncertain; this harness's clock never reaches the window.
        row=self.run_time_limit('old-router-reload:nostate')
        self.assertEqual(0,len(row['submits'])+len(row['appends']))
        self.assertEqual([],row['host_state'])
        self.assertEqual('submitting',row['durable']['state'])

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
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',
                                check=True,timeout=60)
        row=json.loads(done.stdout)
        # The fresh wake was delivered and one fork prompt was built.
        self.assertEqual(1,len(row['sinks']['prompts']))
        self.assertEqual(1,len(row['sinks']['spawns']))
        for name,values in row['sinks'].items():
            text=json.dumps(values)
            with self.subTest(sink=name):
                for secret in row['secrets']:
                    self.assertNotIn(secret,text)
                self.assertEqual([],re.findall(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])',text))

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
        # Each event is elected and started once and duplicate offers are
        # suppressed. Each started wake issues its own prompt at once under its
        # own key, never holding claims; the engine, not the module, holds the
        # second until main is idle (owned probes prompt-gate-0dd78bf3,
        # prompt-registry-e2bed500). There is no module-wide prompt queue.
        self.assertEqual(2,row['whileBusy']['submits'])
        self.assertEqual(['w1','w2'],sorted(row['submits']))
        self.assertEqual([{'event_id':'w1','state':'prepare','attempt_id':None},
                          {'event_id':'w1','state':'start','attempt_id':'attempt-w1'},
                          {'event_id':'w2','state':'prepare','attempt_id':None},
                          {'event_id':'w2','state':'start','attempt_id':'attempt-w2'},
                          {'event_id':'w1','state':'queued','attempt_id':'attempt-w1'}],row['wakeCalls'])

    # Per-key prompt registry (side-intake.js sidePromptSubmit). Owned real
    # engine run prompt-registry-e2bed500 (Claude Code 2.1.322) measured the
    # same shapes; these fake-host cases pin each branch.
    def run_registry(self, *args):
        with tempfile.TemporaryDirectory() as raw:
            path=Path(raw)
            (path/'package.json').write_text('{"type":"module"}')
            shutil.copyfile(ROOT/'tests/prompt_registry_harness.mjs',path/'test.mjs')
            shutil.copyfile(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js',path/'side-intake.js')
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

    def test_notice_after_clear_and_reload_is_a_documented_state_loss_residual(self):
        row=self.run_registry('notice-clear')
        # A notice has no durable backend fence. /clear erases its terminal row;
        # reassert handles unresolved entries only, so a reload followed by the
        # reproduced notice issues it once more. This is the documented
        # at-least-once state-loss residual, not a product regression.
        self.assertEqual((['notice:clear:queued'],[],1,'queued'),
                         (row['beforeClear'],row['afterReassert'],row['reloadRaw'],row['reloadValue']))
        # The original module still has its bounded local tombstone.
        self.assertEqual(('queued',1),(row['sameInstanceValue'],row['firstInstanceRaw']))

    def test_release_notice_liveness_table(self):
        expected={'finite':'Side released n3 → main (queued)','anomaly':'Side released n3 → main (uncertain)'}
        for condition,log in expected.items():
            with self.subTest(condition=condition):
                row=self.run_registry('notice',condition)
                self.assertEqual({'calls':1,'registry':['notice:'],'live':1},row['issued'])
                self.assertEqual([log],row['logs'])
                self.assertEqual(([],1),(row['registry'],row['submits']))
        # reload: the hydrated key is not issued again; its window ends it.
        row=self.run_registry('notice','reload')
        self.assertEqual((0,[]),(row['secondCalls'],row['registryAfter']))
        self.assertEqual(['Side released n3 → main (uncertain)'],row['logs2'])
        # restart: nothing carries over; a reproduced notice is issued again.
        row=self.run_registry('notice','restart')
        self.assertEqual(1,row['secondCalls'])
        self.assertEqual(['Side released n3 → main (queued)'],row['logs2'])

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
            done=subprocess.run(['node',str(path/'test.mjs')],capture_output=True,text=True,encoding='utf8',check=True)
        self.assertEqual([
            {'tool':'TaskStop','task_id':'failed-side'},
            {'tool':'TaskStop','task_id':'idle-side'},
            {'tool':'TaskStop','task_id':'finished-fork'},
        ],json.loads(done.stdout))

    def test_side_prompt_requires_answer_ack_queue_and_status_behaviors(self):
        source=(ROOT/'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js').read_text(encoding='utf8')
        self.assertIn('status: report the current Work state directly. No request recap and no Work',source)
        self.assertIn('info: gather only what is needed, organize it, and answer. No Work',source)
        self.assertIn('request: restate what the person asks using source_message plus the transcript',source)
        # ciel-walkie 2026-11-10: Celly 121710 and Daniel 121677 were both
        # replaced by a stale Grant 121686 decision copied from main context.
        self.assertIn("The decision MUST describe this event's source_message", source)
        self.assertIn('never substitute a stale prior event', source)
        self.assertIn('verify every person, message id, approval, result and', source)
        # Owned run 2026-11-09: the recap dropped "mobile browsers", and a new
        # payment outage was merged into the active payment refactoring item.
        self.assertIn('every\n  concrete requirement, constraint, target, environment and deadline',source)
        self.assertIn("Update an existing item only when the event is\n  about that item's own requested outcome",source)
        self.assertIn('A different problem, failure, incident or\n  feature is a new item even in the same area or system',source)
        self.assertIn('an urgent new problem is create with priority P1',source)
        self.assertIn('queue_position is 1-based among waiting',source)
        self.assertIn('Never claim a queue position in reply; the host appends the committed one',source)
        self.assertNotIn('act when the event requests it',source)

    def test_every_spawned_fork_prompt_states_prompt_only_prohibitions(self):
        row=self.run_harness('side_intake_harness.mjs')
        for spawn in row['spawns']:
            instruction=spawn['prompt']['instruction']
            # Prompt-only restriction (no tool fence), stated in every event.
            self.assertIn("inherits main's model, context, tools and permission mode",instruction)
            self.assertIn('not a technical tool fence',instruction)
            for rule in ('- write, edit or create code, config or any file;',
                         '- run Bash, PowerShell or any shell command;',
                         '- build, test, deploy, install, commit or push;',
                         '- restart, configure or control the runtime, session, model or settings;',
                         '- mutate Work (work_* create/update/execute/report/handoff/resolve or similar);',
                         '- call Agent, start, resume or message any agent/subagent/fork;',
                         '- ACK, reply, SendMessage or send anything to the source or any channel.'):
                self.assertIn(rule,instruction)
            self.assertIn('The host alone commits one decision and sends the one\noutbox reply.',instruction)
            self.assertIn('Your final answer MUST be exactly the decision JSON',instruction)
            self.assertIn('Return one JSON object: {"decision":{...},"expected_version":N}.',instruction)
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
