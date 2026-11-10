"""Parent system/history projection without rewriting either native transcript."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from ciel_runtime_support.side_context_projection import select, SideContextUnavailable
from ciel_runtime_support.side_context_store import SideContextStore
from ciel_runtime_support.side_context_router import project
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim


def event_text(message):
    """The projected event JSON block; the side contract follows it as its own block."""
    content = message['content']
    return content[0]['text'] if isinstance(content, list) else content


class SideContextRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = 'claude:parent'
        self.parent = {'model': 'model-a', 'system': [{'type':'text','text':'Exact parent system','cache_control':{'type':'ephemeral'}}],
            'tools': [{'name':'mcp__mail__read','description':'Read mail','input_schema':{'type':'object'}}],
            'messages': [{'role':'user','content':'old'}, {'role':'assistant','content':'old answer'},
                         {'role':'user','content':'recent'}, {'role':'assistant','content':'recent answer'},
                         {'role':'user','content':'current main request'}]}
        self.store = SideContextStore(self.root,self.identity)
        self.store.capture(self.parent,'fixture')
        self.inbox = SideIntakeInbox(self.root,self.identity)
        self.inbox.receive('external question','event')
        self.event = claim(self.root,self.identity,'actor')
        self.settings = {'enabled':True,'context_mode':'full','context_percent':30}
        self.handle = self.store.prepare(self.event,self.settings,'model-a')
        self.envelope = {'event_id':self.event['receipt_id'],'side_context_key':self.handle['key'],
                         'workload':{'version':7,'nodes':[{'id':'n21','priority':'P1'}],'truncated':False},
                         'original':'external question'}
        self.body = {'model':'model-a','system':'Dedicated side prompt','tools':copy.deepcopy(self.parent['tools']),
                     'messages':[{'role':'user','content':json.dumps(self.envelope)}]}

    def test_teammate_wrapper_is_recognized_and_projected(self):
        wrapped=copy.deepcopy(self.body)
        raw=wrapped['messages'][0]['content']
        wrapped['messages'][0]['content']='<teammate-message teammate_id="team-lead" summary="External message decision">\n'+raw+'\n</teammate-message>'
        result=project(self.root,self.identity,'wrapped-agent',wrapped,
                       {'work_side_intake':self.settings},'fixture',None)
        expected=copy.deepcopy(self.parent['messages'])
        expected[-1]['content']=[{'type':'text','text':'current main request','cache_control':{'type':'ephemeral'}}]
        self.assertEqual(expected,result['messages'][:len(expected)])
        self.assertIn('external question',event_text(result['messages'][-1]))

    def test_teammate_wrapper_with_trailing_newline_is_recognized(self):
        wrapped=copy.deepcopy(self.body)
        raw=wrapped['messages'][0]['content']
        wrapped['messages'][0]['content']='<teammate-message teammate_id="team-lead" summary="External message decision">\n'+raw+'\n</teammate-message>\n'
        result=project(self.root,self.identity,'wrapped-newline',wrapped,
                       {'work_side_intake':self.settings},'fixture',None)
        expected=copy.deepcopy(self.parent['messages'])
        expected[-1]['content']=[{'type':'text','text':'current main request','cache_control':{'type':'ephemeral'}}]
        self.assertEqual(expected,result['messages'][:len(expected)])
        self.assertIn('external question',event_text(result['messages'][-1]))

    def test_full_parent_prefix_and_current_board_are_exact_and_main_untouched(self):
        original = copy.deepcopy(self.parent)
        body = copy.deepcopy(self.body)
        result = project(self.root,self.identity,'agent-1',body,{'work_side_intake':self.settings},'fixture',lambda _: self.fail('full never needs counts'))
        self.assertEqual(original,self.parent)
        self.assertEqual(body,self.body)
        self.assertEqual(original['system'],result['system'])
        self.assertEqual(original['tools'],result['tools'])
        projected=copy.deepcopy(original['messages'])
        projected[-1]['content']=[{'type':'text','text':'current main request','cache_control':{'type':'ephemeral'}}]
        self.assertEqual(projected,result['messages'][:len(projected)])
        self.assertIn('n21',event_text(result['messages'][-1]))
        self.assertEqual('agent-1',self.store.read(self.store.path(self.handle['key']))['agent_id'])

    def test_side_contract_is_last_block_after_projected_main_history(self):
        # Walkie 2026-11-09: with the contract only inside the event JSON, actors
        # under main's projected history answered as main, without decision JSON.
        envelope={**self.envelope,'instruction':'Return one JSON decision.'}
        body={**self.body,'messages':[{'role':'user','content':json.dumps(envelope)}]}
        result=project(self.root,self.identity,'contract',body,{'work_side_intake':self.settings},'fixture',None)
        blocks=result['messages'][-1]['content']
        self.assertEqual(2,len(blocks))
        self.assertNotIn('instruction',json.loads(blocks[0]['text']))
        self.assertTrue(blocks[1]['text'].startswith('SIDE ACTOR TURN.'))
        self.assertIn('Return one JSON decision.',blocks[1]['text'])
        self.assertTrue(blocks[1]['text'].endswith('"expected_version":N} and nothing else.'))

    def test_main_unanswered_turn_is_closed_before_the_side_event(self):
        # Owned run 2026-11-09: the actor answered main's pending "reply exactly:
        # TURN-2-DONE" because the side event merged into main's open turn.
        from ciel_runtime_support.side_context_router import MAIN_PAUSED
        result=project(self.root,self.identity,'closed',self.body,{'work_side_intake':self.settings},'fixture',None)
        parent_count=len(self.parent['messages'])
        self.assertEqual('user',result['messages'][parent_count-1]['role'])
        self.assertEqual({'role':'assistant','content':[{'type':'text','text':MAIN_PAUSED}]},
                         result['messages'][parent_count])
        self.assertEqual('user',result['messages'][parent_count+1]['role'])
        self.assertEqual(parent_count+2,len(result['messages']))

    def test_primary_projection_shows_only_tools_the_side_actor_can_run(self):
        # Prody 2026-11-08: projected parent Bash/Write/restart schemas made side
        # actors call tools their AgentSpec refused. Only the offered subset stays.
        shell = [{'name':'Bash','input_schema':{'type':'object'}},
                 {'name':'mcp__ciel-runtime-router__restart_session','input_schema':{'type':'object'}}]
        self.store.capture({**self.parent,'tools':shell+self.parent['tools']},'fixture')
        handle = self.store.prepare(self.event,self.settings,'model-a')
        body = {**self.body,'messages':[{'role':'user','content':json.dumps({**self.envelope,'side_context_key':handle['key']})}]}
        result = project(self.root,self.identity,'filtered',body,{'work_side_intake':self.settings},'fixture',None)
        self.assertEqual(self.parent['tools'],result['tools'])
        self.assertNotIn('tool_choice',result)

    def test_preview_is_readable_bounded_and_not_duplicated_upstream(self):
        preview=self.handle['parent_transcript_preview']
        self.assertTrue(preview)
        self.assertEqual('current main request',preview[-1]['text'])
        envelope={**self.envelope,'parent_context':{'display_preview':preview}}
        body={**self.body,'messages':[{'role':'user','content':json.dumps(envelope)}]}
        result=project(self.root,self.identity,'agent-preview',body,
                       {'work_side_intake':self.settings},'fixture',None)
        forwarded=json.loads(event_text(result['messages'][-1]))
        self.assertNotIn('parent_context',forwarded)
        self.assertEqual('external question',forwarded['original'])

    def test_preview_redacts_common_credentials(self):
        preview=self.store.preview({'messages':[{'role':'user','content':'password=supersecret api_key=abcdefgh123456 ghp_abcdefgh123456 AKIAABCDEFGH123456'}]})
        text=preview[0]['text']
        self.assertNotIn('supersecret',text)
        self.assertNotIn('abcdefgh123456',text)
        self.assertNotIn('AKIAABCDEFGH123456',text)

    def test_recent_uses_provider_count_not_message_count_and_keeps_tool_group(self):
        parent=copy.deepcopy(self.parent)
        parent['messages']=[{'role':'user','content':'old'}, {'role':'assistant','content':'old answer'},
            {'role':'user','content':'recent'},
            {'role':'assistant','content':[{'type':'thinking','thinking':'private','signature':'sig'},
                                          {'type':'tool_use','id':'use','name':'mcp__mail__read','input':{}}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'use','content':'fact'}]},
            {'role':'assistant','content':'answer'}]
        counts=[]
        def count(body):
            counts.append(copy.deepcopy(body))
            return 1000 if body['messages'][0]['content']=='old' else 350
        messages,info=select(parent,'recent',30,'model-a',count)
        self.assertEqual('recent',messages[0]['content'])
        self.assertEqual(350,info['selected_tokens'])
        self.assertEqual(35,info['selected_percent'])
        self.assertEqual(1,info['thinking_blocks_omitted'])
        self.assertFalse(info['work_board_in_percentage'])
        self.assertTrue(all(set(row)=={'model','messages'} for row in counts))
        self.assertTrue(any(b.get('type')=='tool_result' for m in messages for b in m.get('content',[]) if isinstance(b,dict)))

    def test_explicit_custom_model_omits_bound_reasoning_at_branch_boundary(self):
        parent=copy.deepcopy(self.parent)
        parent['messages'][1]['content']=[{'type':'thinking','signature':'sig','thinking':'private'}, {'type':'text','text':'old answer'}]
        same,info=select(parent,'full',30,'model-a',None)
        self.assertEqual(0,info['thinking_blocks_omitted'])
        self.assertEqual(parent['messages'],same)
        switched,info=select(parent,'full',30,'model-b',None)
        self.assertEqual(1,info['thinking_blocks_omitted'])
        self.assertNotIn('signature',json.dumps(switched))

    def test_cache_boundary_never_exceeds_four_existing_marks(self):
        parent=copy.deepcopy(self.parent)
        parent['system']=[{'type':'text','text':str(i),'cache_control':{'type':'ephemeral'}} for i in range(4)]
        store=SideContextStore(self.root,'claude:cache-full')
        store.capture(parent,'fixture')
        inbox=SideIntakeInbox(self.root,'claude:cache-full')
        inbox.receive('event','event')
        event=claim(self.root,'claude:cache-full','a')
        handle=store.prepare(event,self.settings,'model-a')
        body={**self.body,'messages':[{'role':'user','content':json.dumps({**self.envelope,
              'side_context_key':handle['key'],'event_id':event['receipt_id']})}]}
        result=project(self.root,'claude:cache-full','agent',body,{'work_side_intake':self.settings},'fixture',None)
        self.assertEqual(4,json.dumps(result).count('"cache_control"'))
        self.assertEqual('current main request',result['messages'][4]['content'])

    def _project_marked(self, identity, parent, tools=None):
        store=SideContextStore(self.root,identity)
        store.capture(parent,'fixture')
        inbox=SideIntakeInbox(self.root,identity)
        inbox.receive('event','event')
        event=claim(self.root,identity,'a')
        handle=store.prepare(event,self.settings,'model-a')
        envelope=json.dumps({**self.envelope,'side_context_key':handle['key'],'event_id':event['receipt_id']})
        # Claude Code marks the side actor's own last message as well.
        body={**self.body,'tools':copy.deepcopy(tools if tools is not None else self.body['tools']),
              'messages':[{'role':'user','content':[{'type':'text','text':envelope,'cache_control':{'type':'ephemeral'}}]}]}
        result=project(self.root,identity,'agent',body,{'work_side_intake':self.settings},'fixture',None)
        def marks(items):
            if not isinstance(items,list):
                return 0
            return sum(('cache_control' in b)+marks(b.get('content')) for b in items if isinstance(b,dict))
        total=marks(result['system'])+marks(result['tools'])+sum(marks(m.get('content')) for m in result['messages'])
        return result,total

    def test_parent_boundary_counts_the_child_marks_of_the_assembled_request(self):
        # Prody 2026-11-09 on 30b0d70: two system marks, the parent's last tool
        # result mark, the parent-boundary mark and the child's own mark made 5,
        # and anthropic/claude-haiku-5-5-max refused every side event with 400.
        parent=copy.deepcopy(self.parent)
        parent['system']=[{'type':'text','text':'a','cache_control':{'type':'ephemeral'}},
                          {'type':'text','text':'b','cache_control':{'type':'ephemeral'}}]
        parent['messages']=[{'role':'user','content':'question'},
            {'role':'assistant','content':[{'type':'text','text':'reading'},{'type':'tool_use','id':'t1','name':'mcp__mail__read','input':{}}]},
            {'role':'user','content':[{'type':'tool_result','tool_use_id':'t1','content':'mail','cache_control':{'type':'ephemeral'}}]}]
        result,total=self._project_marked('claude:cache-child',parent)
        self.assertEqual(4,total)
        self.assertIn('cache_control',json.dumps(result['messages'][-1]))
        self.assertNotIn('cache_control',json.dumps(result['messages'][1]))

    def test_parent_and_child_marks_over_four_drop_the_parent_message_marks(self):
        parent=copy.deepcopy(self.parent)
        parent['system']=[{'type':'text','text':'a','cache_control':{'type':'ephemeral'}},
                          {'type':'text','text':'b','cache_control':{'type':'ephemeral'}}]
        parent['tools']=[{**parent['tools'][0],'cache_control':{'type':'ephemeral'}}]
        parent['messages'][-1]['content']=[{'type':'text','text':'current','cache_control':{'type':'ephemeral'}}]
        result,total=self._project_marked('claude:cache-over',parent,tools=parent['tools'])
        self.assertEqual(4,total)
        # System, tools and the child's own mark are kept as sent.
        self.assertEqual(2,json.dumps(result['system']).count('"cache_control"'))
        self.assertIn('cache_control',json.dumps(result['tools']))
        self.assertIn('cache_control',json.dumps(result['messages'][-1]))

    def test_missing_context_mode_uses_full_semantic_default(self):
        config={'work_side_intake':{'enabled':True,'context_percent':30}}
        result=project(self.root,self.identity,'default-full',self.body,config,'fixture',None)
        expected=copy.deepcopy(self.parent['messages'])
        expected[-1]['content']=[{'type':'text','text':'current main request',
                                  'cache_control':{'type':'ephemeral'}}]
        self.assertEqual(expected,result['messages'][:len(expected)])
        # The external event comes after the reusable parent cache boundary.
        self.assertNotIn('cache_control',json.dumps(result['messages'][-1]))

    def test_other_agent_identity_profile_and_tool_drift_are_rejected(self):
        config={'work_side_intake':self.settings}
        project(self.root,self.identity,'a',self.body,config,'fixture',None)
        with self.assertRaises(SideContextUnavailable):
            project(self.root,self.identity,'b',self.body,config,'fixture',None)
        with self.assertRaises(SideContextUnavailable):
            project(self.root,'claude:other','a',self.body,config,'fixture',None)
        with self.assertRaises(SideContextUnavailable):
            project(self.root,self.identity,'a',self.body,{'work_side_intake':{**self.settings,'context_mode':'recent'}},'fixture',None)
        with self.assertRaises(SideContextUnavailable):
            project(self.root,self.identity,'a',{**self.body,'tools':self.body['tools']+[{'name':'new-tool'}]},config,'fixture',None)

    def test_persistent_side_uses_frozen_parent_but_new_work_board_each_event(self):
        first=project(self.root,self.identity,'a',self.body,{'work_side_intake':self.settings},'fixture',None)
        self.inbox.receive('new event','second')
        event=claim(self.root,self.identity,'a')
        self.store.prepare(event,self.settings,'model-a',key=self.handle['key'])
        envelope={**self.envelope,'event_id':event['receipt_id'],'workload':{'version':8,'nodes':[{'id':'n21','priority':'P2'}]}}
        body={**self.body,'messages':self.body['messages']+[{'role':'assistant','content':'done'}, {'role':'user','content':'The coordinator sent a message while you were working:\n'+json.dumps(envelope)+'\n\nAddress this before completing your current task.'}]}
        next_request=project(self.root,self.identity,'a',body,{'work_side_intake':self.settings},'fixture',None)
        self.assertEqual(first['system'],next_request['system'])
        # One actor serves one event in production; the projected parent prefix
        # is what must stay byte-identical between events.
        parent_count=len(self.parent['messages'])
        self.assertEqual(first['messages'][:parent_count],next_request['messages'][:parent_count])
        self.assertIn('"version": 8',event_text(next_request['messages'][-1]))

    def test_new_parent_version_rebases_between_events_not_inside_signed_history(self):
        project(self.root,self.identity,'a',self.body,{'work_side_intake':self.settings},'fixture',None)
        self.store.capture({**self.parent,'system':'new main system'},'fixture')
        self.inbox.receive('new event','second')
        event=claim(self.root,self.identity,'a')
        prepared=self.store.prepare(event,self.settings,'model-a',key=self.handle['key'])
        self.assertTrue(prepared['rebased'])
        self.assertNotEqual(self.handle['key'],prepared['key'])
        previous=self.store.read(self.store.path(self.handle['key']))
        self.assertEqual(self.parent['system'],previous['parent']['system'])
        self.assertEqual(self.event['receipt_id'],previous['receipt_id'])

    def test_provider_change_rebases_even_when_parent_bytes_are_identical(self):
        project(self.root,self.identity,'a',self.body,{'work_side_intake':self.settings},'fixture',None)
        self.store.capture(self.parent,'other-provider')
        self.inbox.receive('new event','second')
        event=claim(self.root,self.identity,'a')
        prepared=self.store.prepare(event,self.settings,'model-a',key=self.handle['key'])
        self.assertTrue(prepared.get('rebased'))
        self.assertNotEqual(self.handle['key'],prepared['key'])
        envelope={**self.envelope,'event_id':event['receipt_id'],'side_context_key':prepared['key']}
        body={**self.body,'messages':[{'role':'user','content':json.dumps(envelope)}]}
        result=project(self.root,self.identity,'new-agent',body,{'work_side_intake':self.settings},'other-provider',None)
        self.assertEqual(self.parent['system'],result['system'])

    def test_primary_projection_cannot_add_provider_hosted_execution(self):
        parent={**self.parent,'tools':self.parent['tools']+[{'type':'web_search_20260209','name':'web_search'}]}
        self.store.capture(parent,'fixture')
        handle=self.store.prepare(self.event,self.settings,'model-a')
        body={**self.body,'messages':[{'role':'user','content':json.dumps({**self.envelope,'side_context_key':handle['key']})}]}
        with self.assertRaisesRegex(SideContextUnavailable,'provider-hosted'):
            project(self.root,self.identity,'a',body,{'work_side_intake':self.settings},'fixture',None)
        self.assertIsNone(self.store.read(self.store.path(handle['key']))['agent_id'])

    def test_oversized_new_parent_cannot_silently_reuse_old_full_snapshot(self):
        from unittest.mock import patch
        project(self.root,self.identity,'a',self.body,{'work_side_intake':self.settings},'fixture',None)
        with patch('ciel_runtime_support.side_context_store.MAX_PARENT_BYTES',1):
            with self.assertRaises(SideContextUnavailable):
                self.store.capture({**self.parent,'system':'new oversized parent'},'fixture')
        self.inbox.receive('new event','second')
        event=claim(self.root,self.identity,'a')
        for key in (None,self.handle['key']):
            with self.subTest(existing_actor=bool(key)), self.assertRaises(SideContextUnavailable):
                self.store.prepare(event,self.settings,'model-a',key=key)
        previous=self.store.read(self.store.path(self.handle['key']))
        self.assertEqual(self.parent['system'],previous['parent']['system'])
        self.store.capture(self.parent,'fixture')
        self.assertTrue(self.store.prepare(event,self.settings,'model-a')['key'])

    def test_count_failure_does_not_fall_back_to_character_ratio(self):
        with self.assertRaises(SideContextUnavailable):
            select(self.parent,'recent',30,'model-a',lambda _:None)

    def test_unbound_and_expired_requests_cannot_read_parent(self):
        with self.assertRaises(SideContextUnavailable):
            project(self.root,self.identity,None,self.body,{'work_side_intake':self.settings},'fixture',None)
        row=self.inbox._load(self.event['receipt_id'])
        row['processing']['lease_until']=0
        self.inbox._save_file(self.inbox._event_path('pending',self.event['receipt_id']),row)
        with self.assertRaises(ValueError):
            project(self.root,self.identity,'a',self.body,{'work_side_intake':self.settings},'fixture',None)
