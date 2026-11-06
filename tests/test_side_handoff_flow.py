import json
import tempfile
import unittest
from pathlib import Path

from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import session_work_plan_store
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox


class SideHandoffFlowTests(unittest.TestCase):
    def test_coordinator_answer_is_durable_side_followup_not_new_work(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_mode':True,'work_side_intake':{'enabled':True},'work_parallel_host':{'token':'x'*40}}
            store=session_work_plan_store(root,identity)
            _,item=store.transaction(lambda d:tree.add_node(d,None,'existing'))
            inbox=SideIntakeInbox(root,identity)
            inbox.receive('Need a main decision','event',route={'channel':'source','thread_id':'t'})
            def call(name,args,extra=None):
                result=dispatch_work_tool(1,name,{**args,'session_id':identity,'_host':{'token':'x'*40,**(extra or {})}},
                    load_config=lambda:config,state_dir=root,respond=lambda _id,text,error=False:{'text':text,'error':error})
                self.assertFalse(result['error'],result)
                return json.loads(result['text'])
            claim=call('work_side_claim',{'owner':'actor'})['claim']
            decision=call('work_side_resolve',{'decision':{'action':'advise','item_id':item,'summary':'Need coordinator input','reply':'Which target?'},'expected_version':store.read()['version']},
                {'receipt_id':claim['receipt_id'],'generation':claim['generation']})
            answered=call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Use target A','activate':False,'expected_version':decision['result']['version']})
            self.assertEqual('answered',answered['state'])
            self.assertEqual('answered',inbox._load(claim['receipt_id'])['result']['handoff'])
            pending=inbox.pending()
            self.assertEqual(1,len(pending))
            self.assertIn('Use target A',pending[0]['text'])
            self.assertEqual(1,len(store.read()['nodes']))
            call('work_side_handoff',{'event_id':claim['receipt_id'],'answer':'Use target A','activate':False,'expected_version':answered['version']})
            self.assertEqual(1,len(inbox.pending()))
            # Simulate restart after board acknowledgment but before follow-up
            # mailbox persistence; the next host pickup repairs the intent.
            inbox._event_path('pending', pending[0]['receipt_id']).unlink()
            prior = inbox._load(claim['receipt_id'])
            prior['result'].update(handoff='requested', delivery='submitted')
            inbox._save_file(inbox._event_path('receipts', claim['receipt_id']), prior)
            pickup = call('work_side_claim', {'owner': 'restarted-actor'})
            recovered = pickup['claim']
            displayed = pickup['handoff_replies'][0]['receipt']['result']
            self.assertEqual('answered', displayed['handoff'])
            self.assertEqual('submitted', displayed['delivery'])
            self.assertEqual('answered', inbox._load(claim['receipt_id'])['result']['handoff'])
            self.assertEqual(pending[0]['receipt_id'], recovered['receipt_id'])
            self.assertIn('Use target A', recovered['text'])
            self.assertEqual(1, len(store.read()['nodes']))
