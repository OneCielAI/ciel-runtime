"""Production Claude router captures only bound main and projects only owned side."""
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ciel_runtime_support import claude_router
from ciel_runtime_support.runtime_compaction_control import CompactionScope
from ciel_runtime_support.side_context_store import SideContextStore
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim
import test_router_raw_context_observers as fixtures


class SideContextBoundaryTests(unittest.TestCase):
    def test_bound_main_capture_and_child_projection_reach_real_dispatch(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            config={'work_side_intake':{'enabled':True,'context_mode':'full','context_percent':30}}
            parent={'model':'model','system':[{'type':'text','text':'parent system'}],'tools':[],
                    'messages':[{'role':'user','content':'main history'}]}
            outputs=[]
            services=fixtures.RawContextObserverTests().claude_services(None,[])
            services=replace(services,
                context_recovery=replace(services.context_recovery,
                    stream_scope=lambda headers,body:None if headers.get('x-claude-code-agent-id') else CompactionScope('claude','parent','epoch'),
                    reject_request=lambda *_:None),
                pipeline=replace(services.pipeline,inject_channel_context=lambda body:body),
                routing=replace(services.routing,forward_responses=lambda handler,provider,pcfg,body,model:outputs.append(copy.deepcopy(body))))
            main=SimpleNamespace(headers={'x-claude-code-session-id':'parent'},path='/v1/messages',wfile=io.BytesIO())
            child=SimpleNamespace(headers={'x-claude-code-session-id':'parent','x-claude-code-agent-id':'child'},path='/v1/messages',wfile=io.BytesIO())
            (root/'compaction-scope.json').write_text(json.dumps({'epoch':'epoch','session_id':'parent'}),encoding='utf8')
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root), patch('ciel_runtime_support.runtime_paths.ROUTER_INSTANCE_DIR',root):
                claude_router.handle_claude_messages_post(services,main,config,'fixture',{},'/v1/messages',copy.deepcopy(parent))
                store=SideContextStore(root,'claude:parent')
                self.assertEqual(parent,store.read(store.directory/'latest.json')['parent'])
                inbox=SideIntakeInbox(root,'claude:parent')
                inbox.receive('event','event')
                event=claim(root,'claude:parent','actor')
                handle=store.prepare(event,config['work_side_intake'],'model')
                side={'model':'model','system':'side-specific','tools':[], 'messages':[{'role':'user','content':json.dumps({
                    'side_context_key':handle['key'],'event_id':event['receipt_id'],'workload':{'version':5,'nodes':[{'id':'n21'}]}})}]}
                claude_router.handle_claude_messages_post(services,child,config,'fixture',{},'/v1/messages',side)
                self.assertEqual(parent,outputs[0])
                self.assertEqual(parent['system'],outputs[1]['system'])
                projected=copy.deepcopy(parent['messages'])
                projected[-1]['content']=[{'type':'text','text':'main history','cache_control':{'type':'ephemeral'}}]
                self.assertEqual(projected,outputs[1]['messages'][:len(projected)])
                self.assertEqual('assistant',outputs[1]['messages'][len(projected)]['role'])
                self.assertIn('n21',outputs[1]['messages'][-1]['content'][0]['text'])
                self.assertEqual(parent,store.read(store.directory/'latest.json')['parent'])

    def test_auxiliary_request_never_replaces_main_capture(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            scope=CompactionScope('claude','parent','epoch')
            services=fixtures.RawContextObserverTests().claude_services(None,[])
            services=replace(services,
                context_recovery=replace(services.context_recovery,
                    stream_scope=lambda *_:scope,reject_request=lambda *_:None),
                pipeline=replace(services.pipeline,inject_channel_context=lambda body:body))
            config={'work_side_intake':{'enabled':True,'context_mode':'full'}}
            main={'model':'model','messages':[{'role':'user','content':'main history'}]}
            # The fork Claude Code 2.1.319 sends after a main turn (captured on Prody).
            suggestion={'model':'model','messages':[
                {'role':'user','content':'main history'},
                {'role':'assistant','content':[{'type':'text','text':'main answer'}]},
                {'role':'user','content':'[SUGGESTION MODE: Suggest what the user might naturally '
                                        'type next into Claude Code.]\n\nReply with ONLY the suggestion'}]}

            def post(headers,body):
                handler=SimpleNamespace(headers={'x-claude-code-session-id':'parent',**headers},
                                        path='/v1/messages',wfile=io.BytesIO())
                claude_router.handle_claude_messages_post(
                    services,handler,config,'fixture',{},'/v1/messages',copy.deepcopy(body))
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root):
                store=SideContextStore(root,'claude:parent')
                post({'x-claude-code-request-class':'main'},main)
                for headers in ({'x-claude-code-request-class':'auxiliary'},
                                {'x-claude-code-request-class':'compaction'},
                                {}):
                    with self.subTest(headers=headers):
                        post(headers,suggestion)
                        self.assertEqual(main,store.read(store.directory/'latest.json')['parent'])
                # A headerless main turn (older launch) still captures.
                newer={'model':'model','messages':main['messages']+[
                    {'role':'assistant','content':[{'type':'text','text':'main answer'}]},
                    {'role':'user','content':'next main turn'}]}
                post({},newer)
                self.assertEqual(newer,store.read(store.directory/'latest.json')['parent'])

    def test_unbound_main_never_captures_another_conversation(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            services=fixtures.RawContextObserverTests().claude_services(None,[])
            services=replace(services,context_recovery=replace(services.context_recovery,stream_scope=lambda *_:None,reject_request=lambda *_:None))
            handler=SimpleNamespace(headers={'x-claude-code-session-id':'unknown'},path='/v1/messages')
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root):
                claude_router.handle_claude_messages_post(services,handler,{'work_side_intake':{'enabled':True,'context_mode':'full'}},'fixture',{},'/v1/messages',{'model':'model','messages':[]})
            self.assertFalse((root/'side-contexts').exists())
