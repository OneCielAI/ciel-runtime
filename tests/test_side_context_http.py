"""Real loopback provider count endpoint through production Claude router."""
import copy
from dataclasses import replace
import http.server
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request

from ciel_runtime_support import claude_router
from ciel_runtime_support.side_context_store import SideContextStore
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim
from test_claude_transient_context_recovery import route_services


class SideContextHttpTests(unittest.TestCase):
    def test_recent_count_and_projection_reach_real_provider_endpoint(self):
        calls=[]
        class Provider(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['content-length'])))
                calls.append({'path':self.path,'body':copy.deepcopy(body)})
                if self.path.endswith('/count_tokens'):
                    count=1000 if body['messages'][0]['content']=='old' else 350
                    result={'input_tokens':count}
                else:
                    result={'id':'owned','type':'message','role':'assistant','model':'model',
                            'content':[{'type':'text','text':'answer'}],'stop_reason':'end_turn',
                            'usage':{'input_tokens':400,'output_tokens':1}}
                raw=json.dumps(result).encode()
                self.send_response(200)
                self.send_header('content-type','application/json')
                self.send_header('content-length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Provider)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        endpoint='http://127.0.0.1:'+str(server.server_port)+'/v1/messages'
        def open_request(url,body,headers,timeout,*args,**kwargs):
            return urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json',**headers}),timeout=timeout)
        services=route_services(endpoint,open_request)
        services=replace(services,routing=replace(services.routing,write_json=lambda h,b,s=200:h.wfile.write(json.dumps(b).encode())))
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            identity='claude:test'
            config={'work_side_intake':{'enabled':True,'context_mode':'recent','context_percent':30}}
            parent={'model':'model','system':'exact system','tools':[],
                'messages':[{'role':'user','content':'old'},{'role':'assistant','content':'done'},
                            {'role':'user','content':'recent'},{'role':'assistant','content':'done'}]}
            contexts=SideContextStore(root,identity)
            contexts.capture(parent,'fixture')
            inbox=SideIntakeInbox(root,identity)
            inbox.receive('Question','event')
            event=claim(root,identity,'actor')
            context=contexts.prepare(event,config['work_side_intake'],'model')
            request={'model':'model','system':'dedicated','tools':[],'stream':False,'max_tokens':100,
                'messages':[{'role':'user','content':json.dumps({'side_context_key':context['key'],
                    'event_id':event['receipt_id'],'workload':{'version':99,'nodes':[{'id':'n21','priority':'P1'}]}})}]}
            handler=SimpleNamespace(headers={'x-claude-code-session-id':'test','x-claude-code-agent-id':'owned'},
                path='/v1/messages',wfile=io.BytesIO(),send_response=Mock(),send_header=Mock(),end_headers=Mock())
            with patch('ciel_runtime_support.runtime_paths.WORKSPACE_STATE_DIR',root):
                claude_router.handle_claude_messages_post(services,handler,config,'fixture',{'stream_enabled':False},'/v1/messages',request)
            counts=[c for c in calls if c['path'].endswith('/count_tokens')]
            final=[c for c in calls if c['path']=='/v1/messages']
            self.assertGreaterEqual(len(counts),2)
            self.assertEqual(1,len(final))
            self.assertEqual('exact system',final[0]['body']['system'])
            self.assertEqual('recent',final[0]['body']['messages'][0]['content'])
            self.assertIn('n21',final[0]['body']['messages'][-1]['content'][0]['text'])
            self.assertTrue(final[0]['body']['messages'][-1]['content'][-1]['text'].startswith('SIDE ACTOR TURN.'))
            self.assertEqual(35,contexts.read(contexts.path(context['key']))['selection']['selected_percent'])
            self.assertEqual('answer',json.loads(handler.wfile.getvalue())['content'][0]['text'])
            print('owned_provider_counts='+str([c['body']['messages'][0]['content'] for c in counts])+' selected=35% work_version=99 upstream=200')
