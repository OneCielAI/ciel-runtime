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
        event_text=json.dumps({'data':{'conversation':'34886995-4a66-4527-a618-814e9cb0a5bf','message_id':'118635'}})
        self.inbox=SideIntakeInbox(self.root,self.identity)
        self.inbox.receive(event_text,'walkie',route={'source_kind':'external_event',
            'channel':'external:default','sender_id':'urn:walkie'})
        self.config['work_side_intake']['reply_connectors']=[{
            'source':'urn:walkie','receiver_id':'default','preset':'walkie','server':'walkie'}]
        event=self._claim()
        self.assertEqual({'server':'walkie','tool':'walkie_read_messages',
            'arguments':{'conversation_id':'34886995-4a66-4527-a618-814e9cb0a5bf','after':'118634'},
            'message_id':'118635'},event['source_read'])
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

    def test_claim_routes_selected_same_provider_model_without_mutating_config(self):
        self.config.update(current_provider='cielairouter', providers={'cielairouter': {'current_model':'main'}})
        self.config['work_side_intake'].update(model='API KEYS - SSE', context_mode='full')
        from ciel_runtime_support.side_context_store import SideContextStore
        SideContextStore(self.root,self.identity).capture({'model':'main','system':[],'tools':[],
            'messages':[{'role':'user','content':[{'type':'text','text':'main'}]}]},'cielairouter')
        claimed=self.invoke('work_side_claim',{'owner':'actor'})
        settings=json.loads(claimed['text'])['settings']
        self.assertEqual('ciel-runtime-cielairouter-api-keys-sse',settings['model'])
        self.assertEqual('API KEYS - SSE',settings['model_catalog'])
        self.assertEqual('full',settings['context_mode'])
        self.assertEqual('API KEYS - SSE',self.config['work_side_intake']['model'])

    def test_inherit_fork_claims_in_fresh_session_without_parent_capture(self):
        self.config['work_side_intake']['context_mode']='full'
        data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
        self.assertIsNone(data['waiting_reason'])
        self.assertIsNotNone(data['claim'])
        self.assertEqual('inherit',data['settings']['model'])

    def test_full_context_waits_without_lease_until_parent_capture(self):
        self.config.update(current_provider='cielairouter', providers={'cielairouter': {'current_model':'main'}})
        self.config['work_side_intake'].update(context_mode='full', model='API KEYS - SSE')
        data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
        self.assertIsNone(data['claim'])
        self.assertEqual('parent_context',data['waiting_reason'])
        row=self.inbox.pending()[0]
        self.assertNotIn('processing',row)
        from ciel_runtime_support.side_context_store import SideContextStore
        SideContextStore(self.root,self.identity).capture({
            'model':'main','system':[],'tools':[],
            'messages':[{'role':'user','content':[{'type':'text','text':'main context'}]}]},'cielairouter')
        data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
        self.assertIsNotNone(data['claim'])

    def test_resumed_session_reuses_bound_capture_from_earlier_launch_epoch(self):
        # Walkie 2026-11-07 11:18: --continue kept session 67746a0a but started
        # a new launch epoch before any main request; full-context claims waited
        # forever while the exact conversation's capture sat on disk.
        from unittest.mock import patch
        from ciel_runtime_support.side_context_store import SideContextStore
        # A selected model uses the router capture; inherit forks need none.
        self.config.update(current_provider='cielairouter', providers={'cielairouter': {'current_model':'main'}})
        self.config['work_side_intake'].update(context_mode='full', model='API KEYS - SSE')
        SideContextStore(self.root,self.identity).capture({'model':'main','system':[],'tools':[],
            'messages':[{'role':'user','content':[{'type':'text','text':'resumed history'}]}]},'cielairouter','old-launch')
        scope=self.root/'compaction-scope.json'
        with patch('ciel_runtime_support.runtime_paths.ROUTER_INSTANCE_DIR',self.root):
            scope.write_text(json.dumps({'epoch':'new-launch','session_id':'other'}),encoding='utf8')
            data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
            self.assertIsNone(data['claim'])
            self.assertEqual('parent_context',data['waiting_reason'])
            scope.write_text(json.dumps({'epoch':'new-launch','session_id':'parent'}),encoding='utf8')
            data=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])
            self.assertIsNotNone(data['claim'])
            host={'token':'x'*40,'receipt_id':data['claim']['receipt_id'],'generation':data['claim']['generation']}
            prepared=self.invoke('work_side_context',{'model':'main'},host)
            self.assertFalse(prepared['error'],prepared)
            self.assertEqual('resumed history',json.loads(prepared['text'])['parent_transcript_preview'][0]['text'])

    def test_context_prepare_persists_visible_parent_transcript_preview(self):
        self.config['work_side_intake']['context_mode']='full'
        from ciel_runtime_support.side_context_store import SideContextStore
        SideContextStore(self.root,self.identity).capture({'model':'main','system':[],'tools':[],
            'messages':[{'role':'user','content':[{'type':'text','text':'current main work'}]},
                        {'role':'assistant','content':[{'type':'text','text':'working on verified task'}]}]},'cielairouter')
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        prepared=self.invoke('work_side_context',{'model':'main'},host)
        self.assertFalse(prepared['error'],prepared)
        row=self.inbox._load(event['receipt_id'])
        self.assertEqual('full',row['context']['mode'])
        self.assertEqual('current main work',row['context']['preview'][0]['text'])
        status=json.loads(self.invoke('work_side_status',{})['text'])
        receipt=next(r for r in status['side_inbox_receipts'] if r['receipt_id']==event['receipt_id'])
        self.assertEqual('working on verified task',receipt['context']['preview'][-1]['text'])

    def test_unavailable_legacy_model_retries_with_main_session_connection(self):
        self.config.update(current_provider='cielairouter', providers={'cielairouter': {'current_model':'main'}})
        self.config['work_side_intake']['model']='missing-model'
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        host={'token':'x'*40,'receipt_id':event['receipt_id'],'generation':event['generation']}
        from ciel_runtime_support.side_intake_settings import runtime_settings
        failed=self.invoke('work_side_fail',{'category':'model_not_found','model':'missing-model',
            'model_fingerprint':runtime_settings(self.config)['model_fingerprint']},host)
        self.assertFalse(failed['error'],failed)
        row=self.inbox._load(event['receipt_id'])
        self.assertFalse(row['side_failure']['quarantined'])
        self.assertTrue(row['side_failure']['use_parent_model'])
        self.assertNotIn('processing',row)
        retry=json.loads(self.invoke('work_side_claim',{'owner':'actor2'})['text'])
        self.assertEqual(event['receipt_id'],retry['claim']['receipt_id'])
        self.assertEqual('inherit',retry['settings']['model'])

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
        self.assertEqual('inherit',reclaimed['settings']['model'])
        row=self.inbox._load(event['receipt_id'])
        self.assertNotIn('main_fallback',row)
        self.assertEqual('legacy_main_fallback_reclaimed',row['side_failure']['category'])

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

    def test_unrelated_404_is_not_model_unavailable(self):
        from ciel_runtime_support.side_intake_processing import fail
        from ciel_runtime_support.side_intake_processing import claim
        event=claim(self.root,self.identity,'actor')
        receipt=fail(self.root,self.identity,event['receipt_id'],event['generation'],{
            'category':'turn_error','outcome':{'status':404,'code':'route_not_found'}})
        self.assertFalse(receipt['side_failure']['main_fallback_eligible'])

    def test_legacy_generic_model_error_is_rechecked_once(self):
        event=json.loads(self.invoke('work_side_claim',{'owner':'actor'})['text'])['claim']
        row=self.inbox._load(event['receipt_id'])
        row.pop('processing',None)
        row['side_failure']={'category':'model_error','model_fingerprint':'legacy','quarantined':True,
                             'attempt_count':1,'retry_not_before':9999999999}
        self.inbox._save_file(self.inbox._event_path('pending',event['receipt_id']),row)
        self.config.update(current_provider='cielairouter',providers={'cielairouter':{'current_model':'main'}})
        self.config['work_side_intake']['model']='legacy'
        from ciel_runtime_support.side_intake_settings import runtime_settings
        row['side_failure']['model_fingerprint']=runtime_settings(self.config)['model_fingerprint']
        self.inbox._save_file(self.inbox._event_path('pending',event['receipt_id']),row)
        claimed=json.loads(self.invoke('work_side_claim',{'owner':'actor2'})['text'])['claim']
        self.assertEqual(event['receipt_id'],claimed['receipt_id'])
        self.assertTrue(self.inbox._load(event['receipt_id'])['side_failure']['legacy_recheck'])

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
        first=self.invoke('work_side_resolve',{'decision':{'action':'create','kind':'request',
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
        args={'decision':{'action':'create','title':'Urgent external task','note':'Blocking deadline',
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
        args={'decision':{'action':'create','title':'Urgent','note':'now','summary':'ACK sent; queued',
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
        args={'decision':{'action':'create','title':'new','summary':'s','reply':'a'},'expected_version':0}
        self.assertTrue(self.invoke('work_side_resolve',args,host)['error'])
        args['decision']={'action':'answer','summary':'s','reply':'a'}
        self.assertFalse(self.invoke('work_side_resolve',args,host)['error'])

    def test_restart_recovers_board_commit_without_second_ai_decision(self):
        from unittest.mock import patch
        event = json.loads(self.invoke('work_side_claim', {'owner': 'old-actor'})['text'])['claim']
        host = {'token': 'x'*40, 'receipt_id': event['receipt_id'], 'generation': event['generation']}
        args = {'decision': {'action': 'create', 'title': 'New external task',
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
        from ciel_runtime_support.side_context_store import SideContextStore
        SideContextStore(self.root,self.identity).capture({'model':'main','system':[],'tools':[],
            'messages':[{'role':'user','content':[{'type':'text','text':'main'}]}]},'cielairouter')
        first=None
        for mode in ('full','recent'):
            self.config['work_side_intake']['context_mode']=mode
            self.inbox.receive('Question '+mode,'profile:'+mode)
            value=json.loads(self.invoke('work_side_claim',{'owner':mode})['text'])
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
                result = apply(store, key, {'action': 'answer', 'kind': 'info', 'summary': event, 'reply': 'answer'},
                               store.read()['version'])
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
