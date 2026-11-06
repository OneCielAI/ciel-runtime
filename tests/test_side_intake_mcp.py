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
        self.config = {'work_mode': True, 'work_side_intake': {'enabled': True},
                       'work_parallel_host': {'token': 'x'*40}}
        self.inbox = SideIntakeInbox(self.root, self.identity)
        self.inbox.receive('Question', 'event')

    def invoke(self, name, args, host=None, identity=None):
        return dispatch_work_tool(1, name,
            {**args, 'session_id': identity or self.identity, '_host': host or {'token': 'x'*40}},
            load_config=lambda:self.config, state_dir=self.root,
            respond=lambda _id,text,error=False:{'text':text,'error':error})

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
