import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support import side_comparison
from ciel_runtime_support.side_context_store import SideContextStore
from ciel_runtime_support.side_context_router import project
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, finish


class SideComparisonTests(unittest.TestCase):
    def test_four_profiles_share_work_snapshot_and_never_apply_proposals(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            sid='claude:test'
            inbox=SideIntakeInbox(root,sid)
            key=inbox.receive('Prioritize n2','event')['receipt_id']
            event=claim(root,sid,'actor')
            finish(root,sid,key,event['generation'],{'action':'answer','reply':'primary','delivery':'submitted'})
            parent={'model':'main','system':'same system','tools':[{'name':'write_tool','input_schema':{'type':'object'}}],
                    'messages':[{'role':'user','content':'history'}]}
            store=SideContextStore(root,sid)
            store.capture(parent,'fixture')
            settings={'enabled':True,'comparison_enabled':True,'comparison_model':'other','context_mode':'isolated','context_percent':30}
            profiles=side_comparison.plan(root,sid,key,'main',settings)
            self.assertEqual(4,len(profiles))
            workload={'version':9,'nodes':[{'id':'n2','priority':'P1'}],'queue_order':['n2'],'truncated':False}
            side_comparison.freeze(root,sid,key,workload)
            # Later main/Work updates cannot skew the experiment's other arm.
            store.capture({**parent,'system':'changed'},'fixture')
            side_comparison.freeze(root,sid,key,{'version':99})
            for profile in profiles:
                context=side_comparison.context(root,sid,key,profile['profile_id'],'main',settings)
                self.assertEqual(workload,context['workload'])
                body={'model':profile['model'],'tools':[],'messages':[{'role':'user','content':json.dumps({
                    'event_id':key,'side_context_key':context['key'],'workload':context['workload']})}]}
                projected=project(root,sid,'shadow-'+profile['profile_id'],body,{'work_side_intake':settings},'fixture',lambda _:100)
                self.assertEqual('same system',projected['system'])
                self.assertEqual({'type':'none'},projected['tool_choice'])
                self.assertEqual(parent['tools'],projected['tools'])
                side_comparison.record(root,sid,key,profile['profile_id'],{'state':'answered','proposal':{'action':'create','title':'Must not execute'}})
            receipt=inbox._load(key)
            self.assertEqual('submitted',receipt['result']['delivery'])
            self.assertEqual(4,len(receipt['result']['comparisons']))
            self.assertTrue(all(not row['work_applied'] and not row['reply_sent'] for row in receipt['result']['comparisons'].values()))
            self.assertFalse((root/'work-sessions').exists())
