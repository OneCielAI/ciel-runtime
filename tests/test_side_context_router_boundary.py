"""Production Claude router: native side forks need no parent capture or projection."""
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
import test_router_raw_context_observers as fixtures


class SideContextBoundaryTests(unittest.TestCase):
    def services(self, outputs):
        services=fixtures.RawContextObserverTests().claude_services(None,[])
        return replace(services,
            context_recovery=replace(services.context_recovery,
                stream_scope=lambda headers,body:None if headers.get('x-claude-code-agent-id') else CompactionScope('claude','parent','epoch'),
                reject_request=lambda *_:None),
            pipeline=replace(services.pipeline,inject_channel_context=lambda body:body),
            routing=replace(services.routing,forward_responses=lambda handler,provider,pcfg,body,model:outputs.append(copy.deepcopy(body))))

    def test_main_turn_is_never_captured_even_with_legacy_context_mode(self):
        # Side events inherit main through a native fork. A stored context_mode
        # from an older build is ignored and never writes the main request to disk.
        for side in ({'enabled':True,'context_mode':'full','context_percent':30},
                     {'enabled':True,'context_mode':'recent'},{'enabled':True}):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as raw:
                root=Path(raw)
                outputs=[]
                parent={'model':'model','system':[{'type':'text','text':'parent system'}],'tools':[],
                        'messages':[{'role':'user','content':'main history'}]}
                main=SimpleNamespace(headers={'x-claude-code-session-id':'parent','x-claude-code-request-class':'main'},
                                     path='/v1/messages',wfile=io.BytesIO())
                (root/'compaction-scope.json').write_text(json.dumps({'epoch':'epoch','session_id':'parent'}),encoding='utf8')
                with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root), \
                        patch('ciel_runtime_support.runtime_paths.ROUTER_INSTANCE_DIR',root):
                    claude_router.handle_claude_messages_post(self.services(outputs),main,{'work_side_intake':side},
                                                              'fixture',{},'/v1/messages',copy.deepcopy(parent))
                self.assertEqual([parent],outputs)
                self.assertFalse((root/'side-contexts').exists())

    def test_native_fork_request_reaches_upstream_unprojected(self):
        # The fork's own request already carries main's inherited history; the
        # router forwards it as-is (its event prompt carries no side_context_key).
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            outputs=[]
            fork={'model':'model','system':[{'type':'text','text':'parent system'}],'tools':[],
                  'messages':[{'role':'user','content':'main history'},
                              # A legacy side envelope anywhere in inherited
                              # history must not trigger projection or a 409.
                              {'role':'user','content':json.dumps({'side_context_key':'f'*32,
                                  'event_id':'b'*64,'workload':{'version':1,'nodes':[]}})},
                              {'role':'assistant','content':[{'type':'text','text':'main answer'}]},
                              {'role':'user','content':json.dumps({'event_id':'a'*64,'workload':{'version':5,'nodes':[{'id':'n21'}]}})}]}
            child=SimpleNamespace(headers={'x-claude-code-session-id':'parent','x-claude-code-agent-id':'fork-1'},
                                  path='/v1/messages',wfile=io.BytesIO())
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root):
                claude_router.handle_claude_messages_post(self.services(outputs),child,{'work_side_intake':{'enabled':True}},
                                                          'fixture',{},'/v1/messages',copy.deepcopy(fork))
            self.assertEqual([fork],outputs)
            self.assertFalse((root/'side-contexts').exists())

    def test_unbound_main_never_captures_another_conversation(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            services=fixtures.RawContextObserverTests().claude_services(None,[])
            services=replace(services,context_recovery=replace(services.context_recovery,stream_scope=lambda *_:None,reject_request=lambda *_:None))
            handler=SimpleNamespace(headers={'x-claude-code-session-id':'unknown'},path='/v1/messages')
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root):
                claude_router.handle_claude_messages_post(services,handler,{'work_side_intake':{'enabled':True,'context_mode':'full'}},'fixture',{},'/v1/messages',{'model':'model','messages':[]})
            self.assertFalse((root/'side-contexts').exists())
