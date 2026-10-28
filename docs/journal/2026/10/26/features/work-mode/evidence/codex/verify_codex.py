"""Isolated real Codex verification. Run: python -I <absolute script path>. No live state used."""
import os, sys, json, time, tempfile, subprocess, threading, queue, socket, urllib.request, shutil
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# Resolve explicitly; never infer a live workspace from inherited runtime env.
REPO=Path(os.environ.get('WORK_PROOF_REPO') or 'C:/Users/djlov/ciel-runtime-wt-work')
OUT=Path(__file__).resolve().parent
CX=Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
sys.path.insert(0,str(REPO))
from ciel_runtime_support.work_mode_launch import codex_work_mode_args, WORK_MODE_INSTRUCTIONS
ROOT=Path(tempfile.mkdtemp(prefix='codex-work-proof-'))
RUN=OUT/('installed-native-metadata' if os.environ.get('WORK_PROOF_REPO') else 'native-metadata-isolation'); RUN.mkdir(exist_ok=True); OUT=RUN; CFG=ROOT/'runtime'; WS=ROOT/'workspace'
for p in (CFG,WS,ROOT/'home',ROOT/'appdata',ROOT/'localappdata',ROOT/'codex'): p.mkdir()
ENV={k:v for k,v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}
ENV.update(HOME=str(ROOT/'home'),USERPROFILE=str(ROOT/'home'),APPDATA=str(ROOT/'appdata'),LOCALAPPDATA=str(ROOT/'localappdata'),CODEX_HOME=str(ROOT/'codex'))
summary={'temp_root':str(ROOT),'binary':str(CX),'inherited_prefixes_removed':['CIEL_RUNTIME_*','CLAUDE*','ANTHROPIC*','CODEX*','OPENAI*'],'version':subprocess.check_output([str(CX),'--version'],env=ENV,cwd=WS,text=True).strip()}
def save(n,d): (OUT/n).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
args=codex_work_mode_args({},native=False,router_mcp_attached=True)
summary['helper_args']=args
probe_url=['-c','mcp_servers.ciel-runtime-router.url="http://127.0.0.1:9/ca/mcp"']
for name,extra in [('off',probe_url),('on',[*probe_url,*args])]:
    p=subprocess.run([str(CX),*extra,'debug','prompt-input','Verify isolated Work integration.'],cwd=WS,env=ENV,capture_output=True,text=True,timeout=45)
    (OUT/f'prompt-{name}.json').write_text(p.stdout,encoding='utf-8'); (OUT/f'prompt-{name}.stderr.txt').write_text(p.stderr,encoding='utf-8')
    d=json.loads(p.stdout)
    def devs(x):
        if isinstance(x,dict):
            if x.get('role')=='developer': yield json.dumps(x,ensure_ascii=False)
            for v in x.values(): yield from devs(v)
        elif isinstance(x,list):
            for v in x: yield from devs(v)
    summary[f'prompt_{name}']={'exit':p.returncode,'work_in_developer':any('Ciel Work mode is on.' in t for t in devs(d))}
assert summary['prompt_on']['work_in_developer'] and not summary['prompt_off']['work_in_developer']
SCHEMA=ROOT/'schema'; subprocess.run([str(CX),'app-server','generate-json-schema','--out',str(SCHEMA)],cwd=WS,env=ENV,check=True,capture_output=True,timeout=45)
for fn in ('ThreadStartParams','TurnStartParams'):
    shutil.copyfile(SCHEMA/'v2'/f'{fn}.json',OUT/f'{fn}.json')
summary['schema_thread_fields']=list(json.loads((SCHEMA/'v2/ThreadStartParams.json').read_text())['properties'])
tid=None
mcp_calls=[]
requests=[]; replies=[]; all_events=[]; tool_names=[]; gateway_errors=[]
class Gateway(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        body=json.dumps({'object':'list','data':[{'id':'work-probe','object':'model'}]}).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        try:
            raw=self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/mcp-forward':
                obj=json.loads(raw)
                if obj.get('method')=='tools/call':
                    mcp_calls.append(obj); save('mcp-wire-calls.json',mcp_calls)
                forwarded=urllib.request.Request(f'http://127.0.0.1:{port}/ca/mcp',data=raw,headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(forwarded,timeout=15) as res:
                    body=res.read(); self.send_response(res.status); self.send_header('Content-Type',res.headers.get('Content-Type','application/json')); self.end_headers(); self.wfile.write(body)
                return
            req=json.loads(raw); requests.append({'path':self.path,'body':req}); save('gateway-requests.json',requests)
            global tool_names
            tool_names=[x.get('name','') for x in req.get('tools',[]) if x.get('type')=='function']
            namespaces={f['name']:t['name'] for t in req.get('tools',[]) if t.get('type')=='namespace' for f in t.get('tools',[])}
            tool_names.extend(namespaces)
            n=len(requests); desired=['work_plan_set','work_execute','work_status']; output=[]
            if n<=3:
                match=next((x for x in tool_names if x.endswith('__'+desired[n-1]) or x==desired[n-1]),None)
                if not match: raise RuntimeError(f'missing {desired[n-1]} among {tool_names}')
                a={'goal':'Codex isolated Work proof','children':[{'title':'Protocol phase','children':[{'title':'Inspect shared state','children':[{'title':'Observe work_status output'}]}]}]} if n==1 else ({'format':'json'} if n==3 else {})
                output=[{'type':'function_call','id':f'fc_{n}','call_id':f'call_{n}','name':match,'arguments':json.dumps(a),'status':'completed'}]
            else: output=[{'type':'message','id':'msg_final','role':'assistant','status':'completed','content':[{'type':'output_text','text':'CODEX_WORK_PROTOCOL_OK','annotations':[]}]}]
            
            if n<=3 and match in namespaces: output[0]['namespace']=namespaces[match]
            rid=f'resp_{n}'; resp={'id':rid,'object':'response','created_at':int(time.time()),'status':'completed','model':'work-probe','output':output,'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120}}
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Connection','close'); self.end_headers()
            def event(typ,**kw): self.wfile.write(('event: '+typ+'\ndata: '+json.dumps({'type':typ,**kw})+'\n\n').encode()); self.wfile.flush()
            event('response.created',response={**resp,'status':'in_progress','output':[]})
            for i,item in enumerate(output):
                event('response.output_item.added',output_index=i,item=item)
                event('response.output_item.done',output_index=i,item=item)
            event('response.completed',response=resp)
        except Exception as e:
            gateway_errors.append(repr(e)); save('gateway-errors.json',gateway_errors)
            try: self.send_error(500,str(e))
            except Exception: pass
server=ThreadingHTTPServer(('127.0.0.1',0),Gateway); threading.Thread(target=server.serve_forever,daemon=True).start()
with socket.socket() as s: s.bind(('127.0.0.1',0)); port=s.getsockname()[1]
(CFG/'config.json').write_text(json.dumps({'current_provider':'vllm','language':'en','providers':{'vllm':{'base_url':f'http://127.0.0.1:{server.server_port}','current_model':'work-probe','api_key':'dummy','context_window':128000,'max_output_tokens':4096}}}))
RENV={**ENV,'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_ROUTER_PORT':str(port),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_LAUNCH_CWD':str(WS)}
router=None; app=None
rf=(OUT/'router-console.txt').open('w',encoding='utf-8'); ef=(OUT/'app-server-stderr.txt').open('w',encoding='utf-8')
def http_mcp(method,params=None):
    data={'jsonrpc':'2.0','id':int(time.time()*1000),'method':method,'params':params or {}}
    r=urllib.request.Request(f'http://127.0.0.1:{port}/ca/mcp',data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(r,timeout=5) as res: return json.load(res)
try:
    router=subprocess.Popen([sys.executable,str(REPO/'ciel_runtime.py'),'serve'],env=RENV,cwd=WS,stdout=rf,stderr=subprocess.STDOUT)
    deadline=time.time()+60
    while time.time()<deadline:
        try: available=http_mcp('tools/list'); break
        except Exception:
            if router.poll() is not None: raise RuntimeError('isolated router exited')
            time.sleep(.25)
    else: raise RuntimeError('isolated router unavailable')
    save('router-tools.json',available)
    summary['router_pid']=router.pid; summary['router_port']=port
    summary['router_work_tools']=[x['name'] for x in available.get('result',{}).get('tools',[]) if x['name'].startswith('work_')]
    cli=[str(CX),*args,'-c','model="work-probe"','-c','model_provider="workproof"','-c','model_providers.workproof.name="Work proof"','-c',f'model_providers.workproof.base_url="http://127.0.0.1:{server.server_port}/v1"','-c','model_providers.workproof.wire_api="responses"','-c','model_providers.workproof.requires_openai_auth=false','-c',f'mcp_servers.ciel-runtime-router.url="http://127.0.0.1:{server.server_port}/mcp-forward"','app-server']
    summary['app_server_command']=cli
    app=subprocess.Popen(cli,cwd=WS,env=ENV,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=ef,text=True,encoding='utf-8')
    q=queue.Queue()
    def reader():
        for line in app.stdout:
            try: obj=json.loads(line); all_events.append(obj); q.put(obj)
            except Exception: all_events.append({'raw':line})
    threading.Thread(target=reader,daemon=True).start()
    def rpc(i,method,params):
        app.stdin.write(json.dumps({'id':i,'method':method,'params':params})+'\n'); app.stdin.flush()
        end=time.time()+60
        while time.time()<end:
            obj=q.get(timeout=max(.1,end-time.time()))
            if obj.get('id')==i:
                if 'error' in obj: raise RuntimeError(json.dumps(obj))
                return obj['result']
            if 'id' in obj and 'method' in obj:
                raise RuntimeError('unexpected server request '+json.dumps(obj))
        raise TimeoutError(method)
    summary['initialize']=rpc(1,'initialize',{'clientInfo':{'name':'work-proof','version':'1'},'capabilities':{'experimentalApi':True}})
    app.stdin.write(json.dumps({'method':'initialized'})+'\n'); app.stdin.flush()
    started=rpc(2,'thread/start',{'cwd':str(WS),'approvalPolicy':'never','sandbox':'read-only','ephemeral':True,'model':'work-probe'})
    save('thread-start.json',started); tid=started['thread']['id']
    summary['turn_start']=rpc(3,'turn/start',{'threadId':tid,'input':[{'type':'text','text':'Verify Work integration using the router Work plan tools. This is an isolated fixture; no files need changes.'}]})
    end=time.time()+90
    while time.time()<end:
        obj=q.get(timeout=max(.1,end-time.time()))
        if obj.get('method')=='turn/completed': summary['turn_completed']=obj; break
        if 'id' in obj and 'method' in obj:
            raise RuntimeError('unexpected server request '+json.dumps(obj))
    else: raise TimeoutError('turn/completed')
    summary['gateway_request_count']=len(requests); summary['app_server_work_tools']=[x for x in tool_names if 'work_' in x]
    summary['gateway_developer_instructions']=any('Ciel Work mode is on.' in json.dumps(x) for r in requests for x in r['body'].get('input',[]) if x.get('role')=='developer')
    state=http_mcp('tools/call',{'name':'work_status','arguments':{'format':'json'},'_meta':{'sessionId':tid}}); save('router-session-state.json',state)
    other=http_mcp('tools/call',{'name':'work_status','arguments':{'format':'json'},'_meta':{'sessionId':'other-conversation'}}); save('other-session-state.json',other)
    missing=http_mcp('tools/call',{'name':'work_status','arguments':{'format':'json'}}); save('missing-session-state.json',missing)
    snap=json.loads(state['result']['content'][0]['text']); other_snap=json.loads(other['result']['content'][0]['text'])
    assert snap['phase']=='executing' and snap['total']==1, state
    assert other_snap['active_root'] is None and other_snap['phase']=='planning', other
    assert missing['result']['isError'], missing
    summary['session_isolation']={'current_phase':snap['phase'],'other_goal':other_snap['active_root'],'missing_id_rejected':True}
    summary['thread_identity_in_request']=any(tid in json.dumps(r['body']) for r in requests)
    assert len(mcp_calls)==3, mcp_calls
    assert all(c['params']['_meta']['sessionId']==tid for c in mcp_calls), mcp_calls
    assert all('session_id' not in c['params'].get('arguments',{}) for c in mcp_calls), mcp_calls
    summary['native_mcp_metadata_verified']=True
    summary['gateway_errors']=gateway_errors
except Exception as e: summary['error']=repr(e)
finally:
    save('app-server-events.json',all_events); save('gateway-requests.json',requests)
    for p in (app,router):
        if p and p.poll() is None:
            p.terminate()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill(); p.wait(timeout=5)
    summary['owned_processes_exited']={str(p.pid):p.poll() for p in (app,router) if p}
    server.shutdown(); rf.close(); ef.close()
    for f in CFG.rglob('work-plan.json'): shutil.copyfile(f,OUT/'persisted-work-plan.json')
    for f in CFG.rglob('router.log'): shutil.copyfile(f,OUT/'isolated-router.log')
    save('summary.json',summary); print(json.dumps(summary,indent=2))
