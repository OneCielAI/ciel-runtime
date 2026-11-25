import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.work_plan_store import session_work_plan_store


class SideMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = 'claude:parent'
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True, 'context_mode':'isolated'},
                       'work_parallel_host': {'token': 'x'*40}}
        self.inbox = SideIntakeInbox(self.root, self.identity)
        self.inbox.receive('Question', 'event')
        self.store = session_work_plan_store(self.root, self.identity)

    def _claim(self):
        result = self.invoke('work_side_claim', {'owner':'actor'})
        self.assertFalse(result['error'], result)
        return json.loads(result['text'])['claim']

    def invoke(self, name, args, host=None, identity=None):
        return dispatch_work_tool(1, name,
            {**args, 'session_id': identity or self.identity, '_host': host or {'token': 'x'*40}},
            load_config=lambda:self.config, state_dir=self.root,
            respond=lambda _id,text,error=False:{'text':text,'error':error})

    def test_claim_prepares_operator_owned_walkie_prefetch(self):
        event_text=json.dumps({'type':'com.ciel.walkie.support.message','data':{'conversation':'34886995-4a66-4527-a618-814e9cb0a5bf','message_id':'118635'}})
        self.inbox=SideIntakeInbox(self.root,self.identity)
        self.inbox.receive(event_text,'walkie',route={'source_kind':'external_event',
            'channel':'external:default','sender_id':'urn:walkie'})
        self.config['work_side_intake']['reply_connectors']=[{
            'source':'urn:walkie','receiver_id':'default','preset':'walkie','server':'walkie'}]
        event=self._claim()
        self.assertEqual({'server':'walkie','classification':'thread',
            'conversation_id':'34886995-4a66-4527-a618-814e9cb0a5bf',
            'root_id':'118635','target_message_id':'118635'},event['source_read'])
        self.assertRegex(event['work_digest'],r'^[0-9a-f]{64}$')

    def test_native_side_code_read_needs_no_host_identity_and_stays_bounded(self):
        import os
        from unittest.mock import patch
        (self.root/'proof.py').write_text('marker = 1\n',encoding='utf8')
        # Owned run 2026-11-09: the side actor's own call reached the router with
        # only its tool arguments (the engine skips the plugin hook on re-entry)
        # and was refused for missing conversation identity.
        def native(name,args):
            return dispatch_work_tool(1,name,args,load_config=lambda:self.config,state_dir=self.root,
                                      respond=lambda _id,text,error=False:{'text':text,'error':error})
        with patch.dict(os.environ,{'CIEL_RUNTIME_LAUNCH_CWD':str(self.root)}):
            result=native('work_side_code_read',{'path':'proof.py'})
            self.assertFalse(result['error'],result)
            self.assertIn('marker = 1',json.loads(result['text'])['text'])
            found=native('work_side_code_search',{'pattern':'marker','glob':'*.py'})
            self.assertFalse(found['error'],found)
            self.assertIn('proof.py',found['text'])
            self.assertTrue(native('work_side_code_read',{'path':'../outside'})['error'])
            self.assertTrue(native('work_side_code_read',{'path':str(self.root/'proof.py')})['error'])
            self.config['work_side_intake']['enabled']=False
            disabled=native('work_side_code_read',{'path':'proof.py'})
            self.assertEqual({'text':'side processing is disabled','error':True},disabled)

    def test_claim_ignores_retired_model_and_context_settings(self):
        # Every SSE event is a native fork that inherits main's model, context,
        # tools and permissions. Values stored by older builds change nothing.
        self.config.update(current_provider='cielairouter', providers={'cielairouter': {'current_model':'main'}})
        self.config['work_side_intake'].update(model='API KEYS - SSE', context_mode='full', context_percent=30)
        stored=json.loads(json.dumps(self.config['work_side_intake']))
        data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
        # No router parent capture exists, and the claim still never waits for one.
        self.assertIsNone(data['waiting_reason'])
        self.assertIsNotNone(data['claim'])
        settings=data['settings']
        self.assertEqual('native_fork',settings['actor'])
        for retired in ('model','model_catalog','model_fingerprint','context_mode','context_percent',
                        'context_ready','parent_model_retry'):
            self.assertNotIn(retired,settings)
        self.assertEqual(stored,self.config['work_side_intake'])

    def test_legacy_model_unavailable_failure_is_retried_by_native_fork(self):
        from ciel_runtime_support.side_intake_mcp import NATIVE_FORK_FINGERPRINT
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        failed=self.invoke('work_side_fail',{'category':'model_not_found','model':'missing-model',
            'model_fingerprint':NATIVE_FORK_FINGERPRINT},host)
        self.assertFalse(failed['error'],failed)
        row=self.inbox._load(event['receipt_id'])
        self.assertFalse(row['side_failure']['quarantined'])
        self.assertNotIn('processing',row)
        retry=json.loads(self.invoke('work_side_claim',{'owner':'actor2'})['text'])
        self.assertEqual(event['receipt_id'],retry['claim']['receipt_id'])
        self.assertNotIn('model',retry['settings'])

    def test_legacy_pending_main_fallback_is_reclaimed_by_side(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        failed=self.invoke('work_side_fail',{'category':'invalid_model','model':'missing'},host)
        self.assertFalse(failed['error'])
        # Reproduce a pending intent written by a pre-fix runtime. A new claim
        # must reclaim its retained original instead of prompting main.
        row=self.inbox._load(event['receipt_id'])
        row['main_fallback']={'state':'pending','reason':'model unavailable','generation':event['generation']}
        row['processing']={'owner':'main-fallback','generation':event['generation'],'lease_until':9999999999}
        self.inbox._save_file(self.inbox._event_path('pending',event['receipt_id']),row)
        self.assertEqual('pending',self.inbox._load(event['receipt_id'])['main_fallback']['state'])
        reclaimed=json.loads(self.invoke('work_side_claim',{'owner':'other'})['text'])
        self.assertEqual(event['receipt_id'],reclaimed['claim']['receipt_id'])
        self.assertEqual('native_fork',reclaimed['settings']['actor'])
        row=self.inbox._load(event['receipt_id'])
        self.assertNotIn('main_fallback',row)
        self.assertEqual('legacy_main_fallback_reclaimed',row['side_failure']['category'])

    def test_side_model_api_error_retries_at_once_on_main_model_and_stays_there(self):
        # User requirement 2026-11-16: a side model failure is re-processed on
        # main's model. Walkie had 154 failures in 3 days and 0 such retries;
        # Celly's side model failed 152 times with the gateway pre-dispatch 503.
        from unittest.mock import patch
        with patch('ciel_runtime_support.side_intake_mcp._side_model_configured', return_value=True):
            event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
            self.assertFalse(event['use_parent_model'])
            host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
            failed=json.loads(self.invoke('work_side_fail',{'category':'turn_error','model':'inherit',
                'model_fingerprint':'native-fork'},host)['text'])
            failure=failed['receipt']['side_failure']
            self.assertEqual((True,False,1),(failure['use_parent_model'],failure['quarantined'],failure['attempt_count']))
            # Claimable at once (no backoff) and marked for main's model.
            again=json.loads(self.invoke('work_side_claim',{'owner':'actor2'})['text'])['claim']
            self.assertEqual((event['receipt_id'],True),(again['receipt_id'],again['use_parent_model']))
            # A failure on main's model keeps main's model, with normal backoff.
            host={'token':'x'*40,'receipt_id':again['receipt_id'],'generation':again['generation']}
            failed=json.loads(self.invoke('work_side_fail',{'category':'turn_error','model':'inherit',
                'model_fingerprint':'native-fork'},host)['text'])['receipt']['side_failure']
            self.assertTrue(failed['use_parent_model'])
            self.assertGreater(failed['retry_not_before'],failed['failed_at'])

    def test_failure_replay_does_not_count_twice_or_touch_new_generation(self):
        from unittest.mock import patch
        with patch('ciel_runtime_support.side_intake_mcp._side_model_configured', return_value=True):
            event = self._claim()
            key = event['receipt_id']
            host = {'token': 'x'*40, 'receipt_id': key, 'generation': event['generation']}
            args = {'category': 'turn_error'}
            first = json.loads(self.invoke('work_side_fail', args, host)['text'])
            again = self.invoke('work_side_fail', args, host)
            self.assertFalse(again['error'], again)
            self.assertEqual(first['receipt']['side_failure'], json.loads(again['text'])['receipt']['side_failure'])
            new = self._claim()
            self.assertNotEqual(event['generation'], new['generation'])
            self.assertTrue(self.invoke('work_side_fail', args, host)['error'])
            self.assertEqual(new['generation'], self.inbox._load(key)['processing']['generation'])

    def test_typed_billing_without_verified_alternate_immediately_offers_forward(self):
        from ciel_runtime_support.side_request_outcome import record
        event = self._claim()
        key = event['receipt_id']
        host = {'token': 'x'*40, 'receipt_id': key, 'generation': event['generation']}
        record(self.root, self.identity, 'agent', key,
               {'status': 402, 'code': 'billing_error', 'message': 'Credit balance too low'})
        args = {'category': 'turn_error', 'agent_id': 'agent'}
        first = json.loads(self.invoke('work_side_fail', args, host)['text'])
        failure = first['receipt']['side_failure']
        self.assertEqual((1, True, False), (failure['attempt_count'], failure['quarantined'], failure['retryable']))
        self.assertIn('API 크레딧 잔액 부족', first['main_forward']['text'])
        self.assertNotIn('Credit balance too low', first['main_forward']['text'])
        again = json.loads(self.invoke('work_side_fail', args, host)['text'])
        self.assertEqual(failure, again['receipt']['side_failure'])
        self.assertEqual(first['main_forward'], again['main_forward'])
        self.assertIsNone(self._claim())

    def test_typed_billing_evidence_survives_mcp_failure_before_durable_write(self):
        from unittest.mock import patch
        from ciel_runtime_support.side_request_outcome import record
        event = self._claim()
        key = event['receipt_id']
        host = {'token': 'x'*40, 'receipt_id': key, 'generation': event['generation']}
        record(self.root, self.identity, 'agent', key, {'code':'billing_error'})
        args = {'category':'turn_error', 'agent_id':'agent'}
        with patch('ciel_runtime_support.side_intake_processing._record_failure', side_effect=OSError('test precommit failure')):
            with self.assertRaises(OSError):
                self.invoke('work_side_fail', args, host)
        data = json.loads(self.invoke('work_side_fail', args, host)['text'])
        self.assertEqual('billing_error', data['receipt']['side_failure']['category'])
        self.assertEqual(1, data['receipt']['side_failure']['attempt_count'])
        self.assertIn('main_forward', data)

    def test_typed_billing_retries_once_on_verified_distinct_main_model(self):
        from unittest.mock import patch
        from ciel_runtime_support.side_request_outcome import record
        model_view = {'models': {'side': 'side-alias'}, 'upstream': {'side': 'side-model'},
                      'main': {'model': 'main-alias', 'upstream': 'main-model'}}
        with patch('ciel_runtime_support.work_fork_models.projection', return_value=model_view):
            event = self._claim()
            key = event['receipt_id']
            host = {'token': 'x'*40, 'receipt_id': key, 'generation': event['generation']}
            record(self.root, self.identity, 'agent', key,
                   {'code': 'billing_error', 'effective_model': 'side-model'})
            args = {'category': 'turn_error', 'agent_id': 'agent'}
            data = json.loads(self.invoke('work_side_fail', args, host)['text'])
            failure = data['receipt']['side_failure']
            self.assertEqual((1, False, True), (failure['attempt_count'],failure['quarantined'],failure['use_parent_model']))
            self.assertEqual(failure['failed_at'], failure['retry_not_before'])
            self.assertNotIn('main_forward', data)
            retry = self._claim()
            self.assertEqual(key, retry['receipt_id'])
            self.assertTrue(retry['use_parent_model'])
            self.assertNotEqual(event['generation'], retry['generation'])
            data = json.loads(self.invoke('work_side_fail', {'category':'billing_error'},
                                          {**host, 'generation':retry['generation']})['text'])
            failure = data['receipt']['side_failure']
            self.assertEqual((2, True, False), (failure['attempt_count'],failure['quarantined'],failure['retryable']))
            self.assertEqual(key, data['main_forward']['receipt_id'])
            self.assertIsNone(self._claim())

    def test_without_a_side_model_an_api_error_does_not_switch(self):
        from unittest.mock import patch
        with patch('ciel_runtime_support.side_intake_mcp._side_model_configured', return_value=False):
            event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
            host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
            failure=json.loads(self.invoke('work_side_fail',{'category':'turn_error'},host)['text'])['receipt']['side_failure']
            self.assertFalse(failure['use_parent_model'])

    def test_main_model_unavailable_after_the_switch_is_quarantined(self):
        from unittest.mock import patch
        with patch('ciel_runtime_support.side_intake_mcp._side_model_configured', return_value=True):
            event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
            host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
            self.invoke('work_side_fail',{'category':'model_not_found','model_fingerprint':'native-fork'},host)
            again=json.loads(self.invoke('work_side_claim',{'owner':'actor2'})['text'])['claim']
            host={'token':'x'*40,'receipt_id':again['receipt_id'],'generation':again['generation']}
            failure=json.loads(self.invoke('work_side_fail',{'category':'model_not_found',
                'model_fingerprint':'native-fork'},host)['text'])['receipt']['side_failure']
            self.assertTrue(failure['quarantined'])

    def test_completed_turn_without_decision_is_never_replayed(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        failed=json.loads(self.invoke('work_side_fail',{'category':'invalid_answer','model':'main'},host)['text'])
        self.assertTrue(failed['receipt']['side_failure']['quarantined'])
        self.assertIsNone(json.loads(self.invoke('work_side_claim',{'owner':'again'})['text'])['claim'])

    def test_transient_500_is_not_main_fallback_eligible(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        failed=json.loads(self.invoke('work_side_fail',{'category':'turn_error','model':'valid'},host)['text'])
        self.assertFalse(failed['main_fallback_eligible'])
        self.assertTrue(failed['receipt']['side_failure']['retryable'])
        denied=self.invoke('work_side_fallback',{'state':'prepare','reason':'not model failure'},host)
        self.assertTrue(denied['error'])

    def test_the_failure_receipt_reports_the_spent_compaction_waits(self):
        # side-intake.js opens the bounded thread for an event whose waits are
        # used up; it learns the count only from this receipt (2026-11-17).
        from ciel_runtime_support.side_intake_processing import MAIN_COMPACTION_WAITS, claim, fail
        event=claim(self.root,self.identity,'actor')
        stored=self.inbox._load(event['receipt_id'])
        stored['side_failure']={'category':'main_compaction_pending','compaction_waits':MAIN_COMPACTION_WAITS-1,
                                'attempt_count':0,'failed_at':0,'retry_not_before':0,'quarantined':False}
        self.inbox._save_file(self.inbox._event_path('pending',event['receipt_id']),stored)
        receipt=fail(self.root,self.identity,event['receipt_id'],event['generation'],{
            'category':'main_compaction_pending','retry_after_seconds':30},now=1000)
        self.assertEqual(('main_compaction_pending',MAIN_COMPACTION_WAITS,0,False),
                         tuple(receipt['side_failure'][key] for key in ('category','compaction_waits','attempt_count','quarantined')))
        again=claim(self.root,self.identity,'actor2',now=1031)
        receipt=fail(self.root,self.identity,again['receipt_id'],again['generation'],{
            'category':'main_compaction_pending','retry_after_seconds':30},now=1031)
        self.assertEqual(('source_context',MAIN_COMPACTION_WAITS,1),
                         tuple(receipt['side_failure'][key] for key in ('category','compaction_waits','attempt_count')))

    def test_main_compaction_wait_is_not_a_counted_failure_and_is_bounded(self):
        from ciel_runtime_support.side_intake_processing import MAIN_COMPACTION_WAITS, claim, fail
        event=claim(self.root,self.identity,'actor')
        receipt=fail(self.root,self.identity,event['receipt_id'],event['generation'],{
            'category':'main_compaction_pending','reason':'Source thread exceeds main context budget',
            'retry_after_seconds':60},now=1000)
        self.assertEqual(event['receipt_id'],receipt['receipt_id'])
        row=self.inbox._load(event['receipt_id'])['side_failure']
        self.assertEqual((0,1,1060,False,'main_compaction_pending'),
                         (row['attempt_count'],row['compaction_waits'],row['retry_not_before'],row['quarantined'],row['category']))
        # Waits are bounded: past MAIN_COMPACTION_WAITS the overflow is an
        # ordinary, counted source_context failure with its normal backoff.
        stored=self.inbox._load(event['receipt_id'])
        stored['side_failure']['compaction_waits']=MAIN_COMPACTION_WAITS
        self.inbox._save_file(self.inbox._event_path('pending',event['receipt_id']),stored)
        again=claim(self.root,self.identity,'actor2',now=2000)
        fail(self.root,self.identity,again['receipt_id'],again['generation'],{
            'category':'main_compaction_pending','retry_after_seconds':60},now=2000)
        row=self.inbox._load(event['receipt_id'])['side_failure']
        self.assertEqual((1,MAIN_COMPACTION_WAITS,2005,'source_context'),
                         (row['attempt_count'],row['compaction_waits'],row['retry_not_before'],row['category']))

    def test_unrelated_404_is_not_model_unavailable(self):
        from ciel_runtime_support.side_intake_processing import fail
        from ciel_runtime_support.side_intake_processing import claim
        event=claim(self.root,self.identity,'actor')
        receipt=fail(self.root,self.identity,event['receipt_id'],event['generation'],{
            'category':'turn_error','outcome':{'status':404,'code':'route_not_found'}})
        self.assertFalse(receipt['side_failure']['main_fallback_eligible'])

    def test_quarantined_rows_stay_terminal_for_every_category_and_fingerprint(self):
        # Release blocker 2026-11-11: the constant native-fork fingerprint made
        # every non-invalid_answer quarantine claimable again (34 rows 3-4 days
        # old on one workspace), so old Walkie messages would be re-answered.
        from ciel_runtime_support.side_intake_mcp import NATIVE_FORK_FINGERPRINT
        cases=[(category,fingerprint,typed)
               for category in ('turn_error','model_error','answer','aborted','processing_error',
                                'source_context','invalid_model','model_not_found')
               for fingerprint in ('old-model','',NATIVE_FORK_FINGERPRINT)
               for typed in (True,False)]
        keys=[]
        inbox=SideIntakeInbox(self.root,self.identity)
        for index,(category,fingerprint,typed) in enumerate(cases):
            key=inbox.receive('legacy '+str(index),'legacy:'+str(index))['receipt_id']
            keys.append(key)
            row=inbox._load(key)
            row['side_failure']={'category':category,'model_fingerprint':fingerprint,'quarantined':True,
                                 'typed':typed,'attempt_count':6,'retry_not_before':0}
            inbox._save_file(inbox._event_path('pending',key),row)
        claimed=[]
        for owner in range(len(cases)+2):
            event=json.loads(self.invoke('work_side_claim',{'owner':'native-'+str(owner)})['text'])['claim']
            if event is None:
                break
            claimed.append(event['receipt_id'])
        # Only setUp's fresh, never-failed event is claimed.
        self.assertEqual(1,len(claimed))
        self.assertFalse(set(claimed)&set(keys))
        for key in keys:
            self.assertTrue(SideIntakeInbox(self.root,self.identity)._load(key)['side_failure']['quarantined'])
            self.assertNotIn('processing',SideIntakeInbox(self.root,self.identity)._load(key))

    def test_message_less_walkie_reminder_is_not_applicable_end_to_end(self):
        # sse-review B2: a reminder with message_id null went route_unavailable
        # and main was then told to send the side reply itself.
        reminder=json.dumps({'type':'com.ciel.walkie.support.message','data':{
            'conversation':'34886995-4a66-4527-a618-814e9cb0a5bf','message_id':None,
            'reply_to_message_id':None,'thread_root_id':None,'event_type':'agent.pending_reminder',
            'text':'reminder'}})
        inbox=SideIntakeInbox(self.root,self.identity)
        key=inbox.receive(reminder,'walkie:reminder',route={'source_kind':'external_event',
            'channel':'external:default','sender_id':'http://walkie.invalid/conversations/x'})['receipt_id']
        claimed=[]
        while True:
            event=json.loads(self.invoke('work_side_claim',{'owner':'o'+str(len(claimed))})['text'])['claim']
            if event is None or event['receipt_id']==key:
                break
            claimed.append(event['receipt_id'])
        self.assertEqual(key,event['receipt_id'])
        self.assertEqual('none_referenced',event['source_context'])
        self.assertEqual('none_referenced',event['source_read']['classification'])
        resolved=self.invoke('work_side_resolve',{'decision':{'action':'answer','kind':'status',
            'summary':'reminder noted','reply':'noted'},'expected_version':event['workload']['version']},
            {'token':'x'*40,'receipt_id':key,'generation':event['generation'],'work_digest':event['work_digest']})
        self.assertFalse(resolved['error'],resolved)
        self.assertEqual('none_referenced',json.loads(resolved['text'])['result']['source_context'])
        prepared=json.loads(self.invoke('work_side_reply',{'receipt_id':key,'state':'prepare'})['text'])
        self.assertEqual('not_applicable',prepared['state'])
        self.assertNotIn('arguments',prepared)
        self.assertEqual('not_applicable',inbox._load(key)['result']['delivery'])
        from ciel_runtime_support.side_reply_outbox import pending
        self.assertNotIn(key,[row['receipt_id'] for row in pending(self.root,self.identity)])
        # A routine answer to a reminder holds no main review (2026-11-17).
        self.assertNotIn(key,self.store.read().get('side_reviews') or {})
        from ciel_runtime_support.side_review import with_delivery
        self.assertEqual('not_applicable',with_delivery([{'receipt_id':key}],self.root,self.identity)[0]['delivery'])
        from ciel_runtime_support.side_review import SENT, UNSENT
        self.assertNotIn('not_applicable',SENT+UNSENT)

    def test_new_contract_status_reply_says_no_queue_item(self):
        event=self._claim()
        result=self.invoke('work_side_resolve',{'decision':{'action':'answer','kind':'status',
            'summary':'current state','reply':'현재 n1을 진행 중입니다.'},'expected_version':event['workload']['version']},
            {'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation'],
             'work_digest':event['work_digest']})
        self.assertFalse(result['error'], result)
        result=json.loads(result['text'])
        self.assertEqual('현재 n1을 진행 중입니다.\n\n큐에 등록하지 않았습니다.',result['result']['final_text'])
        self.assertIsNone(result['receipt']['item_id'])
        self.assertEqual([],self.store.read()['nodes'])

    def test_changed_create_is_released_once_for_fresh_deduplication(self):
        from ciel_runtime_support import work_plan_tree as tree
        event=self._claim()
        self.store.transaction(lambda d:tree.add_node(d,None,'main added same work'))
        first=self.invoke('work_side_resolve',{'decision':{'refs': ['999'], 'action':'create','kind':'request',
            'summary':'new request','reply':'요청을 복기했습니다.','title':'same work','note':'details'},
            'expected_version':event['workload']['version']},
            {'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation'],
             'work_digest':event['work_digest']})
        self.assertFalse(first['error'], first)
        first=json.loads(first['text'])
        self.assertEqual('work_changed',first['recheck'])
        self.assertEqual('received',first['receipt']['stage'])
        self.assertEqual(1,self.inbox._load(event['receipt_id'])['rechecks'])
        self.assertNotIn('side_decisions',self.store.read())

    def test_claim_resolve_answer_without_root_and_duplicate_recovers(self):
        claimed = self.invoke('work_side_claim', {'owner':'actor'})
        self.assertFalse(claimed['error'], claimed)
        event = json.loads(claimed['text'])['claim']
        host = {'token':'x'*40, 'receipt_id':event['receipt_id'], 'generation':event['generation']}
        args = {'decision':{'action':'answer','summary':'Answered the question','reply':'Answer'}, 'expected_version':0}
        result = self.invoke('work_side_resolve',args,host)
        self.assertFalse(result['error'],result)
        data = json.loads(result['text'])
        self.assertEqual('processed',data['receipt']['stage'])
        self.assertEqual([],self.inbox.pending())
        store=session_work_plan_store(self.root,self.identity)
        self.assertEqual([],store.read()['nodes'])
        again=self.invoke('work_side_resolve',args,host)
        self.assertFalse(again['error'])
        self.assertTrue(json.loads(again['text'])['receipt']['duplicate'])
        self.assertEqual(1,store.read()['version'])

    def test_urgent_new_request_is_first_pending_p1_for_parallel_pickup(self):
        from ciel_runtime_support import work_plan_tree as tree
        store=session_work_plan_store(self.root,self.identity)
        store.transaction(lambda doc: tree.add_node(doc,None,'active',priority='P3'))
        store.transaction(lambda doc: tree.add_node(doc,None,'ordinary queued',priority='P3'))
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        version=store.read()['version']
        args={'decision':{'refs': ['999'], 'action':'create','title':'Urgent external task','note':'Blocking deadline',
              'summary':'ACK sent; urgent work queued','reply':'ACK sent','priority':'P1'},
              'expected_version':version}
        result=self.invoke('work_side_resolve',args,host)
        self.assertFalse(result['error'],result)
        document=store.read()
        pending=[node for node in document['nodes'] if node['parent_id'] is None
                 and node['id']!=document['active_root'] and node['status']!='done']
        self.assertEqual('Urgent external task',pending[0]['title'])
        self.assertEqual('P1',pending[0]['priority'])

    def test_resolve_from_older_snapshot_commits_and_reserved_claim_takes_only_p1(self):
        from ciel_runtime_support import work_plan_tree as tree
        store=session_work_plan_store(self.root,self.identity)
        store.transaction(lambda doc: tree.add_node(doc,None,'active',priority='P3'))
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        seen=event['workload']['version']
        # Main keeps committing while the fork decides.
        store.transaction(lambda doc: tree.add_node(doc,None,'main meanwhile',priority='P3'))
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        args={'decision':{'refs': ['999'], 'action':'create','title':'Urgent','note':'now','summary':'ACK sent; queued',
              'reply':'ACK sent','priority':'P1'},'expected_version':seen}
        result=self.invoke('work_side_resolve',args,host)
        self.assertFalse(result['error'],result)
        data=json.loads(result['text'])['result']
        self.assertEqual((seen,'P1','Urgent'),(data['based_on_version'],data['priority'],data['title']))
        self.assertEqual(data['item_id'],tree.queued_roots(store.read())[0]['id'])
        self.inbox.receive('{"data":{"message_context":{"thread_task":{"priority":3}}}}','ordinary')
        reserved=json.loads(self.invoke('work_side_claim',{'owner':'actor','urgent_only':True})['text'])
        self.assertIsNone(reserved['claim'])
        self.inbox.receive('{"data":{"message_context":{"thread_task":{"priority":1}}}}','urgent')
        reserved=json.loads(self.invoke('work_side_claim',{'owner':'actor','urgent_only':True})['text'])
        self.assertEqual('P1',reserved['claim']['source_priority'])

    def test_walkie_emergency_is_p0_and_claimed_before_p1(self):
        from ciel_runtime_support.side_intake_inbox import source_priority
        # Observed 2026-11-15 on Walkie thread #131698: the person's P0 arrived
        # as thread_task {priority: 1, emergency: true} and was indexed P1.
        emergency = {'data': {'message_context': {'thread_task': {'priority': 1, 'emergency': True}}}}
        self.assertEqual('P0', source_priority(json.dumps(emergency)))
        self.assertEqual('P1', source_priority(json.dumps(
            {'data': {'message_context': {'thread_task': {'priority': 1, 'emergency': False}}}})))
        self.assertEqual('P0', source_priority(json.dumps({'data': {'priority': 'P0'}})))
        self.assertEqual('P0', source_priority(json.dumps({'data': {'priority': 0}})))
        self.assertEqual('P0', source_priority(json.dumps({'data': {'emergency': True, 'priority': 3}})))
        self.inbox.receive(json.dumps({'data': {'message_context': {'thread_task': {'priority': 1}}}}), 'p1')
        self.inbox.receive(json.dumps(emergency), 'p0')
        # The urgent-only lane (a side slot reserved for urgent events) takes P0 too.
        reserved = json.loads(self.invoke('work_side_claim', {'owner': 'actor', 'urgent_only': True})['text'])
        self.assertEqual('P0', reserved['claim']['source_priority'])

    def _resolve_event(self, event, decision):
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        response = self.invoke('work_side_resolve', {'decision': decision,
                               'expected_version': event['workload']['version']}, host)
        self.assertFalse(response['error'], response)
        return json.loads(response['text'])['result']

    def test_followup_inherits_walkie_root_p0_before_model_and_board(self):
        from unittest.mock import patch
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.work_parallel import WorkParallelStore
        from ciel_runtime_support.work_parallel_p0 import p0_store
        ledger = WorkParallelStore(self.root / 'work-parallel.json')
        self.store.transaction(lambda doc: tree.add_node(doc, None, 'active', priority='P3'))
        _, item = self.store.transaction(lambda doc: tree.add_node(doc, None, 'approval display', priority='P3'))
        _, step = self.store.transaction(lambda doc: tree.add_node(doc, item, 'display approval button'))
        for i in range(2):
            # The observed Walkie shape of a P0 root (thread_task priority 1 plus
            # emergency). The model returned P1 for such a follow-up in production.
            self.inbox.receive(json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
                'text': 'The approval button is still missing.',
                'message_context': {'thread_task': {'priority': 1, 'emergency': True}}}}), f'walkie:{i}')
            with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
                event = self._claim()
                self.assertEqual('P0', event['source_priority'])
                result = self._resolve_event(event, {'refs': ['999'], 'action': 'update', 'kind': 'request', 'item_id': step,
                    'summary': 'Correct the missing approval button', 'reply': 'Recorded the correction', 'priority': 'P1'})
            root = next(node for node in self.store.read()['nodes'] if node['id'] == item)
            self.assertEqual(('P1', 'P0', 'P0'), (root['priority'], p0_store(ledger).effective_priority(self.identity, root),
                                                 result['priority']))
            outcome = result['final_text'].rsplit('\n', 1)[-1]
            self.assertTrue(outcome.startswith(step + ' 갱신 · P0 · '), outcome)
            self.assertNotIn('P1', outcome)
            self.assertFalse(result['execution_approved'])
        self.assertEqual(1, len(p0_store(ledger).read()['clears']), 'already P0: no second activation')

    def test_existing_p0_without_event_priority_is_not_demoted_by_the_model(self):
        from unittest.mock import patch
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.work_parallel import WorkParallelStore
        from ciel_runtime_support.work_parallel_p0 import p0_store
        from ciel_runtime_support.work_priority_service import set_root_priority
        ledger = WorkParallelStore(self.root / 'work-parallel.json')
        board, item = self.store.transaction(lambda doc: tree.add_node(doc, None, 'current P0', priority='P1'))
        set_root_priority(self.store, ledger, self.identity, item, 'P0',
                          expected_board_version=board['version'], external_operation_id='operator-set')
        before = p0_store(ledger).read()
        with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
            event = self._claim()
            self.assertIsNone(event['source_priority'])
            result = self._resolve_event(event, {'refs': ['999'], 'action': 'update', 'kind': 'request', 'item_id': item,
                'summary': 'A detail for the same incident', 'reply': 'Noted', 'priority': 'P3'})
        self.assertEqual('P0', result['priority'])
        self.assertEqual('P1', self.store.read()['nodes'][0]['priority'])
        self.assertEqual(before, p0_store(ledger).read())

    def test_classified_complaint_cannot_finish_answer_only_and_notice_survives_lost_result(self):
        from unittest.mock import patch
        event = self._claim()
        decision = {'action': 'answer', 'kind': 'info', 'complaint': True, 'no_reply': True, 'ask_main': False,
                    'summary': 'The approval control is missing again', 'reply': 'The approval control is missing'}
        with patch('ciel_runtime_support.side_intake_mcp.finish', side_effect=OSError('lost before receipt write')):
            with self.assertRaisesRegex(OSError, 'lost before receipt write'):
                self._resolve_event(event, decision)
        row = self.inbox._load(event['receipt_id'])
        row['processing']['lease_until'] = 0
        self.inbox._save_file(self.inbox._event_path('pending', event['receipt_id']), row)
        recovered = self.invoke('work_side_claim', {'owner': 'new-actor'})
        self.assertFalse(recovered['error'], recovered)
        data = json.loads(recovered['text'])
        self.assertIsNone(data['claim'])
        self.assertEqual(1, len(self.store.read()['nodes']))
        self.assertEqual(1, len(data['main_notices']))
        notice = data['main_notices'][0]
        self.assertEqual(event['receipt_id'], notice['receipt_id'])
        self.assertIn(decision['summary'], notice['text'])
        result = self.inbox._load(event['receipt_id'])['result']
        self.assertEqual(('create', 'request', True, False),
                         (result['action'], result['kind'], result['ask_main'], result['execution_approved']))
        self.assertNotIn('no_reply', result)
        self.assertIn(result['item_id'], notice['text'])
        self.assertNotIn('큐에 등록하지 않았습니다', result['final_text'])
        self.assertEqual(1, len(data['pending_replies']))

    def test_classified_complaint_updates_related_work_and_replays_once(self):
        from ciel_runtime_support import work_plan_tree as tree
        _, item = self.store.transaction(lambda doc: tree.add_node(doc, None, 'approval display', priority='P3'))
        event = self._claim()
        decision = {'action': 'answer', 'kind': 'status', 'item_id': item, 'complaint': True,
                    'summary': 'The approval button is still missing', 'reply': 'Recorded'}
        result = self._resolve_event(event, decision)
        self.assertEqual(('update', item, True, False),
                         (result['action'], result['item_id'], result['ask_main'], result['execution_approved']))
        self._resolve_event(event, decision)
        self.assertEqual(1, self.store.read()['nodes'][0]['note'].count(decision['summary']))
        wake = {'event_id': event['receipt_id'], 'state': 'notice_prepare', 'owner_nonce': 'fixture'}
        prepared = json.loads(self.invoke('work_side_wake', wake)['text'])
        self.assertEqual('claimed', prepared['state'])
        self.assertEqual('already_issuing', json.loads(self.invoke('work_side_wake', wake)['text'])['state'])
        self.assertEqual([], json.loads(self.invoke('work_side_claim', {'owner': 'other'})['text'])['main_notices'])

    def test_complaint_and_p0_rules_reach_every_side_actor_from_the_prompt_registry(self):
        from ciel_runtime_support.work_workflow import CONFIG
        from ciel_runtime_support.work_workflow_prompts import DEFAULTS, fingerprint, set_prompt
        rule = DEFAULTS['side_complaint_and_urgency']
        for name in ('Daniel', 'Andy', 'Celly', 'KOBE', 'Betty'):
            self.assertNotIn(name, rule + DEFAULTS['side_complaint_notice'])
        for workflow in (False, True):
            self.config[CONFIG] = {'enabled': workflow}
            settings = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['settings']
            self.assertIn(rule, settings['instruction'])
        # Edited through the registry, applied on the next claim without a restart.
        set_prompt(self.root, 'side_complaint_and_urgency', 'edited complaint rule', fingerprint(rule))
        settings = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['settings']
        self.assertIn('edited complaint rule', settings['instruction'])
        self.assertNotIn(rule, settings['instruction'])

    def test_complaint_with_work_off_still_reaches_main_without_a_queue(self):
        self.config['work_mode'] = False
        event = self._claim()
        result = self._resolve_event(event, {'action': 'answer', 'kind': 'info', 'complaint': True,
                                             'summary': 'The result is wrong again', 'reply': 'Recorded'})
        self.assertEqual(('answer', True), (result['action'], result['ask_main']))
        self.assertEqual([], self.store.read()['nodes'])

    def test_queue_decisions_register_every_related_external_id(self):
        # User 2026-11-20: when the side actor queues a request, every related id
        # (message, thread root, parent, conversation) must land on the Work node.
        from ciel_runtime_support import work_plan_tree as tree
        store = session_work_plan_store(self.root, self.identity)
        store.transaction(lambda doc: tree.add_node(doc, None, 'active', priority='P3'))
        event = self._claim()
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        decision = {'action': 'create', 'kind': 'request', 'title': 'Linked request',
                    'summary': 'queued', 'reply': 'Queued', 'refs': ['154304', '153480']}
        result = self.invoke('work_side_resolve', {'decision': decision,
                             'expected_version': store.read()['version']}, host)
        self.assertFalse(result['error'], result)
        item = next(node for node in store.read()['nodes'] if node['title'] == 'Linked request')
        self.assertEqual(['154304', '153480'], item['refs'])
        self.assertEqual('154304', item['ref'])
        for i, (action, refs) in enumerate((('update', ['153480', '154306']), ('prioritize', ['154999']))):
            self.inbox.receive(f'follow up {i}', f'refs follow up {i}')
            event = self._claim()
            host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
            decision = {'action': action, 'kind': 'request', 'item_id': item['id'],
                        'summary': 'follow-up', 'reply': 'Noted', 'refs': refs, 'note': 'n',
                        **({'priority': 'P2'} if action == 'prioritize' else {})}
            result = self.invoke('work_side_resolve', {'decision': decision,
                                 'expected_version': store.read()['version']}, host)
            self.assertFalse(result['error'], result)
            item = next(node for node in store.read()['nodes'] if node['id'] == item['id'])
        self.assertEqual(['154304', '153480', '154306', '154999'], item['refs'])

    def test_claim_names_the_decision_fields_this_router_accepts(self):
        # The mod sends refs only to a router that lists it (2026-11-20): an
        # older router refuses an unknown decision field.
        from ciel_runtime_support.side_work_decision import FIELDS
        event = self._claim()
        self.assertEqual(sorted(FIELDS), event['decision_fields'])
        self.assertIn('refs', event['decision_fields'])

    def test_queue_decision_without_related_ids_is_refused_and_rechecked(self):
        store = session_work_plan_store(self.root, self.identity)
        # setUp's plain event first: a source with no message id queues without refs.
        plain = self._claim()
        host = {'token': 'x'*40, 'receipt_id': plain['receipt_id'], 'generation': plain['generation']}
        result = self.invoke('work_side_resolve', {'decision': {'action': 'answer', 'kind': 'status',
                             'summary': 's', 'reply': 'r'}, 'expected_version': plain['workload']['version']}, host)
        self.assertFalse(result['error'], result)
        self.inbox.receive(json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
            'text': 'A request with a source message.', 'message_id': '999',
            'conversation': '34886995-4a66-4527-a618-814e9cb0a5bf',
            'thread_root_id': '999', 'reply_to_message_id': None}}), 'walkie:refs')
        event = self._claim()
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        decision = {'action': 'create', 'kind': 'request', 'title': 'No refs',
                    'summary': 'queued', 'reply': 'Queued'}
        result = self.invoke('work_side_resolve', {'decision': decision,
                             'expected_version': store.read()['version']}, host)
        self.assertTrue(result['error'], result)
        self.assertIn('refs', result['text'])
        self.assertEqual([], store.read()['nodes'])
        # The same claim can be corrected with the missing ids.
        decision['refs'] = ['999']
        result = self.invoke('work_side_resolve', {'decision': decision,
                             'expected_version': store.read()['version']}, host)
        self.assertFalse(result['error'], result)
        self.assertEqual(['999'], store.read()['nodes'][0]['refs'])

    def test_side_p0_decision_registers_and_activates_p0_without_main_handoff(self):
        from unittest.mock import patch
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.work_parallel import WorkParallelStore
        from ciel_runtime_support.work_parallel_p0 import p0_store
        ledger = WorkParallelStore(self.root / 'work-parallel.json')
        store = session_work_plan_store(self.root, self.identity)
        store.transaction(lambda doc: tree.add_node(doc, None, 'active', priority='P3'))
        event = self._claim()
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        args = {'decision': {'refs': ['999'], 'action': 'create', 'kind': 'request', 'title': 'Production outage',
                             'summary': 'P0 outage queued', 'reply': 'Outage reported', 'priority': 'P0'},
                'expected_version': store.read()['version']}
        with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
            result = self.invoke('work_side_resolve', args, host)
            self.assertFalse(result['error'], result)
            data = json.loads(result['text'])['result']
            replay = json.loads(self.invoke('work_side_resolve', args, host)['text'])['result']
        root = next(node for node in store.read()['nodes'] if node['title'] == 'Production outage')
        # Canonical P1 on the board (older writers stay correct), effective P0 in the sidecar.
        self.assertEqual('P1', root['priority'])
        self.assertEqual('P0', p0_store(ledger).effective_priority(self.identity, root))
        self.assertEqual(('P0', root['id'], {'root_id': root['id'], 'effective_priority': 'P0'}),
                         (data['priority'], data['p0_root_id'], data['p0']))
        # The fork runs it; main is not also asked to schedule it.
        self.assertEqual('not_required', data['handoff'])
        self.assertNotIn('main_handoff', data)
        self.assertEqual(data['item_id'], replay['item_id'])
        self.assertEqual(1, sum(node['title'] == 'Production outage' for node in store.read()['nodes']))
        # The pump's P0 lane (register.js after the side turn) reserves exactly this goal.
        from ciel_runtime_support import work_parallel_mcp
        self.config['work_parallel_host']['capability_ready'] = True
        with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
            reserved = json.loads(work_parallel_mcp.run('work_parallel_event', {
                'event': 'p0_reserve_next', 'role': 'worker', 'expected_version': ledger.read()['version'],
                '_host': {'token': 'x'*40, 'fork': True}}, store, self.identity, self.config, None))
        self.assertEqual('ready', reserved['dispatch'])
        self.assertEqual(root['id'], ledger.read()['leases'][reserved['lease_id']]['item_id'])

    def test_side_p0_prioritize_activates_an_existing_goal(self):
        from unittest.mock import patch
        from ciel_runtime_support import work_plan_tree as tree
        from ciel_runtime_support.work_parallel import WorkParallelStore
        from ciel_runtime_support.work_parallel_p0 import p0_store
        ledger = WorkParallelStore(self.root / 'work-parallel.json')
        store = session_work_plan_store(self.root, self.identity)
        store.transaction(lambda doc: tree.add_node(doc, None, 'active', priority='P3'))
        store.transaction(lambda doc: tree.add_node(doc, None, 'queued fix', priority='P3'))
        item = next(node['id'] for node in store.read()['nodes'] if node['title'] == 'queued fix')
        event = self._claim()
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        args = {'decision': {'refs': ['999'], 'action': 'prioritize', 'kind': 'request', 'item_id': item,
                             'summary': 'raised to P0', 'reply': 'Raised', 'priority': 'P0'},
                'expected_version': store.read()['version']}
        with patch('ciel_runtime_support.work_parallel_mcp.ledger', return_value=ledger):
            result = self.invoke('work_side_resolve', args, host)
        self.assertFalse(result['error'], result)
        data = json.loads(result['text'])['result']
        root = next(node for node in store.read()['nodes'] if node['id'] == item)
        self.assertEqual(('P1', 'P0'), (root['priority'], p0_store(ledger).effective_priority(self.identity, root)))
        self.assertEqual(('P0', 'not_required'), (data['priority'], data['handoff']))
        # Walkie 134499 (2026-11-16): the reply's last line read "P1" for the
        # P0 just activated. The readback states the effective priority.
        self.assertIn(item + ' 갱신 · P0 ·', data['final_text'])
        self.assertNotIn(' · P1 ', data['final_text'])

    def test_side_p0_activation_failure_is_stated_in_the_reply(self):
        from unittest.mock import patch
        from ciel_runtime_support import work_plan_tree as tree
        store = session_work_plan_store(self.root, self.identity)
        store.transaction(lambda doc: tree.add_node(doc, None, 'active', priority='P3'))
        store.transaction(lambda doc: tree.add_node(doc, None, 'queued fix', priority='P3'))
        item = next(node['id'] for node in store.read()['nodes'] if node['title'] == 'queued fix')
        event = self._claim()
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        args = {'decision': {'refs': ['999'], 'action': 'prioritize', 'kind': 'request', 'item_id': item,
                             'summary': 'raised to P0', 'reply': 'Raised', 'priority': 'P0'},
                'expected_version': store.read()['version']}
        with patch('ciel_runtime_support.side_intake_mcp._activate_side_p0',
                   return_value={'root_id': item, 'error': 'ledger unavailable'}):
            data = json.loads(self.invoke('work_side_resolve', args, host)['text'])['result']
        self.assertTrue(data['final_text'].endswith('\nP0 적용 실패: ledger unavailable'), data['final_text'])

    def test_refused_answer_keeps_its_reason_and_is_never_replayed(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        failed=self.invoke('work_side_fail',{'category':'invalid_answer','reason':'Side router operation failed: work_side_resolve: '+'x'*400},host)
        self.assertFalse(failed['error'],failed)
        failure=json.loads(failed['text'])['receipt']['side_failure']
        self.assertTrue(failure['quarantined'])
        self.assertEqual(300,len(failure['reason']))
        self.assertTrue(failure['reason'].startswith('Side router operation failed: work_side_resolve'))
        self.assertIsNone(json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim'])

    def test_main_status_never_contains_side_original_or_answer(self):
        status=self.invoke('work_status',{'format':'json'})
        self.assertFalse(status['error'])
        row=json.loads(status['text'])['side_inbox_receipts'][0]
        self.assertNotIn('original',row)
        self.assertNotIn('recap',row)
        self.assertNotIn('result',row)
        self.assertEqual('received',row['stage'])

    def test_wrong_generation_or_parent_cannot_resolve(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        args={'decision':{'action':'answer','summary':'s','reply':'a'},'expected_version':0}
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':'forged'}
        self.assertTrue(self.invoke('work_side_resolve',args,host)['error'])
        host['generation']=event['generation']
        self.assertTrue(self.invoke('work_side_resolve',args,host,identity='claude:other')['error'])
        self.assertEqual(1,len(self.inbox.pending()))

    def test_off_answers_but_does_not_mutate_queue(self):
        self.config['work_mode']=False
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        args={'decision':{'refs': ['999'], 'action':'create','title':'new','summary':'s','reply':'a'},'expected_version':0}
        self.assertTrue(self.invoke('work_side_resolve',args,host)['error'])
        args['decision']={'action':'answer','summary':'s','reply':'a'}
        self.assertFalse(self.invoke('work_side_resolve',args,host)['error'])

    def test_restart_recovers_board_commit_without_second_ai_decision(self):
        from unittest.mock import patch
        event = json.loads(self.invoke('work_side_claim', {'owner': 'old-actor'})['text'])['claim']
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        args = {'decision': {'refs': ['999'], 'action': 'create', 'title': 'New external task',
                             'summary': 'Independent task', 'reply': 'Queued, not executed'},
                'expected_version': 0}
        with patch('ciel_runtime_support.side_intake_mcp.finish', side_effect=OSError('receipt disk full')):
            with self.assertRaisesRegex(OSError, 'receipt disk full'):
                self.invoke('work_side_resolve', args, host)
        store = session_work_plan_store(self.root, self.identity)
        self.assertEqual(1, len(store.read()['nodes']))
        row = self.inbox._load(event['receipt_id'])
        self.assertEqual('received', row['stage'])
        row['processing']['lease_until'] = 0
        self.inbox._save_file(self.inbox._event_path('pending', event['receipt_id']), row)
        recovered = self.invoke('work_side_claim', {'owner': 'new-actor'})
        self.assertFalse(recovered['error'], recovered)
        data = json.loads(recovered['text'])
        self.assertIsNone(data['claim'])
        self.assertEqual('processed', data['recovered_receipt']['stage'])
        self.assertTrue(data['recovered_receipt']['result']['duplicate'])
        self.assertEqual(1, len(store.read()['nodes']))
        self.assertEqual([], self.inbox.pending())
        self.assertEqual(event['receipt_id'], data['pending_replies'][0]['receipt_id'])

    def test_heartbeat_requires_current_host_claim_and_disabled_revokes_it(self):
        event = json.loads(self.invoke('work_side_claim', {'owner': 'actor'})['text'])['claim']
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        self.assertFalse(self.invoke('work_side_renew', {}, host)['error'])
        self.assertTrue(self.invoke('work_side_renew', {}, {**host, 'generation': 'wrong'})['error'])
        self.config['work_side_intake']['enabled'] = False
        self.assertTrue(self.invoke('work_side_renew', {}, host)['error'])
        self.assertTrue(self.invoke('work_side_resolve', {'decision': {'action': 'answer', 'summary': 's', 'reply': 'a'}, 'expected_version': 0}, host)['error'])
        self.assertEqual(1, len(self.inbox.pending()))

    def test_both_context_profiles_receive_complete_current_work_not_preview(self):
        from ciel_runtime_support import work_plan_tree as tree
        store = session_work_plan_store(self.root,self.identity)
        for index in range(25):
            store.transaction(lambda doc,index=index:tree.add_node(doc,None,'goal '+str(index),priority='P1' if index==24 else 'P3',note='deadline evidence '+str(index)))
        expected=store.read_snapshot()
        # Retired context profile values do not change the complete Work payload.
        first=None
        for mode in ('full','recent'):
            self.config['work_side_intake']['context_mode']=mode
            self.inbox.receive('Question '+mode,'profile:'+mode)
            value=json.loads(self.invoke('work_side_claim',{'owner':mode})['text'])
            if value.get('paged'):
                meta=value['paged']
                chunks=[json.loads(self.invoke('work_host_page',{'id':meta['id'],'part':part})['text'])['text']
                        for part in range(meta['parts'])]
                value=json.loads(''.join(chunks))
            board=value['claim']['workload']
            self.assertEqual(expected['version'],board['version'])
            self.assertEqual(25,len(board['nodes']))
            self.assertEqual(24,len(board['queue_order']))
            target=next(node for node in board['nodes'] if node['title']=='goal 24')
            self.assertEqual('P1',target['priority'])
            self.assertEqual('deadline evidence 24',target['note'])
            self.assertEqual([node['id'] for node in expected['nodes'] if node['parent_id'] is None and node['id']!=expected['active_root']],board['queue_order'])
            self.assertFalse(board['truncated'])
            comparable={k:v for k,v in board.items() if k!='observed_at'}
            if first is None:
                first=comparable
            else:
                self.assertEqual(first,comparable)

    def test_no_host_capability_cannot_claim(self):
        result=self.invoke('work_side_claim',{'owner':'actor'},{'token':'wrong'})
        self.assertTrue(result['error'])
        self.assertEqual(1,len(self.inbox.pending()))


class SideReviewDeliveryStatusTests(unittest.TestCase):
    """Main's Work status says whether the person actually received each side answer."""

    @unittest.skip('retired 2026-11-18 (user): a clarify answer no longer leaves a review row for main')
    def test_status_reviews_carry_the_receipt_delivery_state(self):
        from ciel_runtime_support.side_intake_processing import claim, finish
        from ciel_runtime_support.side_reply_outbox import prepare, record
        from ciel_runtime_support.side_work_decision import apply
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            identity = 'claude:parent'
            config = {'work_mode': True, 'work_side_intake': {'enabled': True}}
            inbox = SideIntakeInbox(root, identity)
            store = session_work_plan_store(root, identity)
            route = {'source_kind': 'external_event', 'channel': 'external:default', 'sender_id': 'urn:mail'}
            keys = []
            for event in ('plain mail question', 'second question'):
                key = inbox.receive(event, event, route=route)['receipt_id']
                leased = claim(root, identity, 'side-1')
                # A clarifying question always reaches main (2026-11-17 rule).
                result = apply(store, key, {'action': 'clarify', 'kind': 'clarify', 'summary': event,
                                            'reply': 'which one?'}, store.read()['version'])
                finish(root, identity, key, leased['generation'], result)
                keys.append(key)
            self.assertEqual('route_unavailable', prepare(root, identity, keys[0], config=config)['state'])
            bound = {'work_side_intake': {'enabled': True, 'reply_connectors': [{
                'source': 'urn:mail', 'receiver_id': 'default', 'server': 'mail', 'tool': 'reply',
                'arguments': {'message': '$reply'}}]}}
            sent = prepare(root, identity, keys[1], config=bound)
            record(root, identity, keys[1], 'submitted', attempt_id=sent['attempt_id'])
            status = dispatch_work_tool(1, 'work_status', {'format': 'json', 'session_id': identity},
                                        load_config=lambda: config, state_dir=root,
                                        respond=lambda _id, text, error=False: {'text': text, 'error': error})
            self.assertFalse(status['error'], status)
            delivery = {row['receipt_id']: row['delivery'] for row in json.loads(status['text'])['side_reviews']}
            self.assertEqual({keys[0]: 'route_unavailable', keys[1]: 'submitted'}, delivery)
