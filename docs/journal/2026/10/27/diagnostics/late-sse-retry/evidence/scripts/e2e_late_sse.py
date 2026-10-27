"""Actual Claude CLI through Ciel router: delayed pre-output SSE error must retry once, no live state or external upstream."""
import os, sys
for k in list(os.environ):
    if k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')) or any(x in k.upper() for x in ('TOKEN','SECRET','PASSWORD','API_KEY','CREDENTIAL')): os.environ.pop(k)
import argparse, hashlib, shutil, json, re, socket, subprocess, threading, time, tempfile, traceback
class _Done(Exception): pass
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
A=argparse.ArgumentParser(); A.add_argument('--provider',default='cielairouter'); A.add_argument('--fail-all',action='store_true'); A.add_argument('--delay',type=float,default=.35,help='seconds between HTTP 200 and the SSE error; upstream pings every 10 s'); args=A.parse_args()
INSTALLED=os.environ.get('LATE_SSE_PROOF_REPO')  # an exact installed snapshot instead of the worktree
ROOT=Path(INSTALLED) if INSTALLED else next(p for p in Path(__file__).resolve().parents if (p/'ciel_runtime.py').exists())
OUT=Path(os.environ.get('LATE_SSE_PROOF_OUT') or Path(__file__).parent)/('run-'+('installed-' if INSTALLED else 'source-')+('failall-' if args.fail_all else '')+(f'delay{int(args.delay)}-' if args.delay>=1 else '')+time.strftime('%H%M%S')); OUT.mkdir()
SCR=Path(tempfile.mkdtemp(prefix='ciel-send-',dir='C:/Windows/Temp'))
CFG, HOME, CC, WS=(SCR/x for x in ('cfg','home','claude','ws'))
for d in (CFG,HOME,CC,WS,HOME/'AppData/Roaming',HOME/'AppData/Local/Temp'): d.mkdir(parents=True,exist_ok=True)
CLI=Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS=[]; STATE={}; HOLD=threading.Event(); LOCK=threading.Lock()
def dump(n,d): (OUT/n).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
def port():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]
def text(o):
    if isinstance(o,str):return o
    if isinstance(o,list):return ' '.join(text(x) for x in o)
    if isinstance(o,dict):return ' '.join(text(o[k]) for k in ('text','content','output') if k in o)
    return ''
class Fixture(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*a):pass
    def js(self,o):
        b=json.dumps(o).encode();self.send_response(200);self.send_header('content-type','application/json');self.send_header('content-length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):return self.js({'object':'list','data':[{'id':'owned-send','object':'model','context_length':200000,'max_output_tokens':4096,'capabilities':{'tool_calling':True,'thinking':True,'reasoning':True}}]})
    def do_POST(self):
        b=json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        if 'count_tokens' in self.path:return self.js({'input_tokens':100})
        with LOCK:
            n=len(ROWS)+1; row={'n':n,'path':self.path,'main':'LATE-SSE-MAIN' in json.dumps(b)};ROWS.append(row); dump(f'request-{n:03}.json',b)
        if not row['main']: return self.respond(b,n,[{'type':'text','text':'AUX-OK'}])
        main=sum(1 for r in ROWS if r.get('main'))
        row['main_attempt']=main
        if main==1 or args.fail_all:
            self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('connection','close');self.end_headers()
            self.wfile.write(b': upstream accepted'+bytes([10,10]));self.wfile.flush()
            end=time.time()+args.delay
            while time.time()<end:
                time.sleep(min(10,max(0,end-time.time())))
                if time.time()<end: self.wfile.write(b'event: ping'+bytes([10])+b'data: {"type":"ping"}'+bytes([10,10]));self.wfile.flush()
            payload={'type':'error','error':{'type':'api_error','message':'codex/gpt-6.1-sol-medium: provider — Model codex/gpt-6.1-sol-medium timed out (HTTP 504); claude/claude-opus-5-5: provider — Model claude/claude-opus-5-5 timed out (HTTP 504)'}}
            self.wfile.write(b'event: error'+bytes([10])+b'data: '+json.dumps(payload).encode()+bytes([10,10]));self.wfile.flush();self.close_connection=True;return
        return self.respond(b,n,[{'type':'text','text':'LATE-SSE-RETRY-SUCCEEDED'}])
    def respond(self, b, n, content):
        if self.path.endswith('/chat/completions'): return self.chat(b,n,content)
        reason = 'tool_use' if any(c['type'] == 'tool_use' for c in content) else 'end_turn'
        msg = {'id': 'msg_' + str(n), 'type': 'message', 'role': 'assistant', 'model': 'owned-send', 'content': content, 'stop_reason': reason, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
        if not b.get('stream'): return self.js(msg)
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None}})]
        for i, c in enumerate(content):
            empty = {'type': 'tool_use', 'id': c['id'], 'name': c['name'], 'input': {}} if c['type'] == 'tool_use' else {'type': 'text', 'text': ''}
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(c['input'])} if c['type'] == 'tool_use' else {'type': 'text_delta', 'text': c['text']}
            events += [('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': empty}), ('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}), ('content_block_stop', {'type': 'content_block_stop', 'index': i})]
        events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
        self.send_response(200); self.send_header('content-type', 'text/event-stream'); self.send_header('connection', 'close'); self.end_headers()
        try:
            for k, v in events: self.wfile.write(('event: ' + k + '\ndata: ' + json.dumps(v) + '\n\n').encode()); self.wfile.flush(); time.sleep(.025)
        except OSError: pass
        self.close_connection = True
    def chat(self,b,n,blocks):
        calls=[{'id':c['id'],'type':'function','function':{'name':c['name'],'arguments':json.dumps(c['input'])}} for c in blocks if c['type']=='tool_use']
        content=' '.join(c['text'] for c in blocks if c['type']=='text')
        finish='tool_calls' if calls else 'stop'
        if not b.get('stream'):return self.js({'id':f'chatcmpl-{n}','object':'chat.completion','model':'owned-send','choices':[{'index':0,'message':{'role':'assistant','content':content or None,**({'tool_calls':calls} if calls else {})},'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}})
        chunks=[{'role':'assistant','content':''}]
        if calls:chunks += [{'tool_calls':[{'index':i,**c}]} for i,c in enumerate(calls)]
        else:chunks += [{'content':content}]
        self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('connection','close');self.end_headers()
        for d in chunks:
            self.wfile.write(('data: '+json.dumps({'id':f'chatcmpl-{n}','object':'chat.completion.chunk','model':'owned-send','choices':[{'index':0,'delta':d,'finish_reason':None}]})+chr(10)*2).encode());self.wfile.flush()
        self.wfile.write(('data: '+json.dumps({'id':f'chatcmpl-{n}','object':'chat.completion.chunk','model':'owned-send','choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}})+chr(10)*2+'data: [DONE]'+chr(10)*2).encode());self.wfile.flush();self.close_connection=True
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=server.serve_forever,daemon=True).start();rp=port()
sys.path.insert(0,str(ROOT))
pcfg={'base_url':f'http://127.0.0.1:{server.server_port}','api_key':'owned-dummy','current_model':'owned-send','native_compat':True,'gateway_selection':'off','context_window':200000,'max_output_tokens':4096}
(CFG/'config.json').write_text(json.dumps({'current_provider':args.provider,'language':'en','work_mode':False,'providers':{args.provider:pcfg}}))
(CC/'.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True,'bypassPermissionsModeAccepted':True,'projects':{str(WS):{'hasTrustDialogAccepted':True}}}))
env={**os.environ,'HOME':str(HOME),'USERPROFILE':str(HOME),'APPDATA':str(HOME/'AppData/Roaming'),'LOCALAPPDATA':str(HOME/'AppData/Local'),'TEMP':str(HOME/'AppData/Local/Temp'),'TMP':str(HOME/'AppData/Local/Temp'),'CLAUDE_CONFIG_DIR':str(CC),'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_ROUTER_PORT':str(rp),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_LOG_LEVEL':'INFO','ANTHROPIC_BASE_URL':f'http://127.0.0.1:{rp}','ANTHROPIC_AUTH_TOKEN':'owned-dummy','ANTHROPIC_API_KEY':'','ANTHROPIC_MODEL':'owned-send','ANTHROPIC_DEFAULT_HAIKU_MODEL':'owned-send','ANTHROPIC_DEFAULT_SONNET_MODEL':'owned-send','ANTHROPIC_DEFAULT_OPUS_MODEL':'owned-send','DISABLE_AUTOUPDATER':'1','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_TELEMETRY':'1','ENABLE_TOOL_SEARCH':'false','HTTP_PROXY':'http://127.0.0.1:1','HTTPS_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':'127.0.0.1,localhost',
     # routed cielairouter launch policy (claude_environment.py): the router is the only retry owner
     'CLAUDE_CODE_MAX_RETRIES':'0','CLAUDE_CODE_RETRY_WATCHDOG':'0','CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES':'0','CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK':'1'}
_policy=(ROOT/'ciel_runtime_support'/'claude_environment.py').read_text(encoding='utf-8')
assert all(f'"{k}": "{v}"' in _policy for k,v in (('CLAUDE_CODE_RETRY_WATCHDOG','0'),('CLAUDE_CODE_NONSTREAMING_TIMEOUT_RETRIES','0'),('CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK','1'))), 'launch policy changed'
router=None;cli=None;result={'scratch':str(SCR),'root':str(ROOT),'provider':args.provider,'ports':[rp,server.server_port]}
try:
    router=subprocess.Popen([sys.executable,'-I','-c',"import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')",str(ROOT)],cwd=WS,env=env,stdout=(OUT/'router.log').open('w'),stderr=subprocess.STDOUT)
    import urllib.request
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with op.open(f'http://127.0.0.1:{rp}/health',timeout=1) as r:result['owned_health']=json.load(r)
            break
        except Exception:time.sleep(.2)
    cmd=[str(CLI),'-p','LATE-SSE-MAIN: answer once.','--output-format','stream-json','--verbose','--dangerously-skip-permissions','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--model','owned-send','--debug-file',str(OUT/'native-debug.log')]
    result['cmd']=cmd;result['version']=subprocess.run([str(CLI),'--version'],env=env,capture_output=True,text=True).stdout.strip()
    cli=subprocess.Popen(cmd,env=env,cwd=WS,stdout=(OUT/'stdout.jsonl').open('w',encoding='utf-8'),stderr=(OUT/'stderr.txt').open('w',encoding='utf-8'))
    result['exit']=cli.wait(timeout=100+4*args.delay)
    out=(OUT/'stdout.jsonl').read_text(encoding='utf-8')
    shutil.copyfile(CFG/'router.log',OUT/'router-file.log'); log=(OUT/'router-file.log').read_text(encoding='utf-8',errors='replace')
    mains=[r for r in ROWS if r.get('main')]
    bodies=[(OUT/f"request-{r['n']:03}.json").read_bytes() for r in mains]
    result['main_body_sha256']=[hashlib.sha256(b).hexdigest() for b in bodies]
    result['retry_log_lines']=[l for l in log.splitlines() if 'late_sse_' in l or 'anthropic_sse_upstream_error_event' in l]
    if args.fail_all:
        result['checks']={'four_upstream_main_requests':len(mains)==4,'identical_payloads':len(set(result['main_body_sha256']))==1,'three_retries_logged':log.count('cielairouter_late_sse_retry operation_id')==3,'exhaustion_logged':'cielairouter_late_sse_stopped' in log,'error_surfaced_to_cli':'timed out (HTTP 504)' in out,'exit_nonzero':result['exit']!=0,'no_cli_nonstreaming_fallback':'falling back to non-streaming' not in (OUT/'native-debug.log').read_text(encoding='utf-8',errors='replace')}
        result['main_attempts']=len(mains)
        raise _Done
    result['checks']={'two_upstream_main_requests':len(mains)==2,'retry_logged':'cielairouter_late_sse_retry' in log,'success_returned':'LATE-SSE-RETRY-SUCCEEDED' in out,'aggregate_error_not_returned':'timed out (HTTP 504)' not in out,'exit_zero':result['exit']==0,'identical_payloads':len(set(result['main_body_sha256']))==1,'no_cli_nonstreaming_fallback':'falling back to non-streaming' not in (OUT/'native-debug.log').read_text(encoding='utf-8',errors='replace')}
    result['main_attempts']=len(mains)
except _Done: pass
except Exception:result['error']=traceback.format_exc()
finally:
    HOLD.set()
    import psutil
    for p in (cli,router):
        if p:
            try:
                proc=psutil.Process(p.pid)
                for c in proc.children(recursive=True):
                    try:c.kill()
                    except psutil.Error:pass
                proc.kill()
            except psutil.Error:pass
    server.shutdown();server.server_close()
    result['requests']=ROWS;dump('result.json',result)
print('RESULT',OUT,json.dumps({k:v for k,v in result.items() if k not in ('requests','owned_health','cmd')}),flush=True)
raise SystemExit(0 if result.get('checks') and all(result['checks'].values()) else 1)
