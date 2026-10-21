from __future__ import annotations
import ctypes, importlib.util, json, os, subprocess, sys, tempfile, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'ciel_runtime.py').is_file())
if '--detached' not in sys.argv:
    p=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--detached'],creationflags=8)
    print('owned driver pid='+str(p.pid)); raise SystemExit(0)
LOG=(HERE/'driver.log').open('w',encoding='utf-8')
def log(s): LOG.write(time.strftime('%H:%M:%S')+' '+s+chr(10)); LOG.flush()
SCRATCH=Path(tempfile.mkdtemp(prefix='ciel-menu-evidence-'))
CFG,HOME,WS=SCRATCH/'cfg',SCRATCH/'home',SCRATCH/'workspace'
for p in (CFG,HOME,WS,HOME/'.claude',HOME/'.codex',HOME/'AppData/Roaming',HOME/'AppData/Local'): p.mkdir(parents=True,exist_ok=True)
CALLS=[]
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,v):
        raw=json.dumps(v).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/health': return self.send({'ok':True,'workspace':str(WS)})
        if self.path=='/ca/events/receivers': return self.send({'ok':True,'receivers':[{'id':'default','status':{'state':'disabled','phase':'fixture','delivery':{'state':'ok','known_queued_count':0,'queued_coverage':'complete'}}}]})
        return self.send({'object':'list','data':[{'id':'EVIDENCE-MODEL','object':'model'}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        CALLS.append({'path':self.path,'body':body}); (HERE/'http-fixture-calls.json').write_text(json.dumps(CALLS,indent=2),encoding='utf-8')
        assert self.path=='/ca/mcp' and body['params']['name']=='recover_sse_delivery'
        log('fixture tools/call recover_sse_delivery action='+body['params']['arguments']['action'])
        result={'problem':False,'reset':False,'reason':'no_sse_delivery_problem','evidence':{'reasons':[],'known_queued_count':0,'oldest_queued_age_seconds':None}}
        self.send({'jsonrpc':'2.0','id':body['id'],'result':{'content':[{'type':'text','text':json.dumps(result)}]}})
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture); port=server.server_address[1]
threading.Thread(target=server.serve_forever,daemon=True).start()
config={'current_provider':'cielairouter','language':'en','providers':{'cielairouter':{'base_url':f'http://127.0.0.1:{port}','current_model':'EVIDENCE-MODEL','gateway_retries':3}},'web_backend':{'enabled':False}}
(CFG/'config.json').write_text(json.dumps(config),encoding='utf-8')
blocked=('CIEL_RUNTIME_','CLAUDE_','ANTHROPIC_','CODEX_','OPENAI_','OLLAMA_','GEMINI_','GOOGLE_','AZURE_','AWS_','DEEPSEEK_','MISTRAL_','NVIDIA_','ZAI_','KIMI_','GROQ_','OPENCODE_','GITHUB_','COPILOT_')
env={k:v for k,v in os.environ.items() if not k.startswith(blocked) and k!='CLAUDECODE'}
env.update({'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_ROUTER_PORT':str(port),'CIEL_RUNTIME_LAUNCH_CWD':str(WS),'CIEL_RUNTIME_SKIP_SELF_UPDATE':'1','HOME':str(HOME),'USERPROFILE':str(HOME),'APPDATA':str(HOME/'AppData/Roaming'),'LOCALAPPDATA':str(HOME/'AppData/Local'),'CLAUDE_CONFIG_DIR':str(HOME/'.claude'),'CODEX_HOME':str(HOME/'.codex'),'PYTHONIOENCODING':'utf-8'})
meta={'scratch':str(SCRATCH),'port':port,'workspace':str(WS),'scope':'Actual production portable_prelaunch_menu and renderer with Win32 key input. Router control uses synthetic loopback MCP fixture, not real runtime restart. No provider credentials.','cleared_environment_prefixes':list(blocked)}
(HERE/'scope.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
process=None
try:
    process=subprocess.Popen([sys.executable,'-I',str(HERE/'menu_bootstrap.py'),str(REPO),str(HERE)],cwd=WS,env=env,creationflags=16)
    log(f'owned menu pid={process.pid} scratch={SCRATCH} fixture_port={port}')
    spec=importlib.util.spec_from_file_location('owned_console_driver',HERE/'console_driver.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    for attempt in range(50):
        try:
            console=module.Console(process.pid); break
        except OSError:
            if process.poll() is not None: raise
            time.sleep(.2)
    else: raise RuntimeError("owned console did not become attachable")
    k=module.kernel32; k.SetConsoleScreenBufferSize(console.conout,module.COORD(150,100)); rect=module.SMALL_RECT(0,0,149,89); k.SetConsoleWindowInfo(console.conout,True,ctypes.byref(rect))
    def expect(text,timeout=45):
        if not console.wait_for(text,timeout):
            (HERE/'failed-screen.txt').write_text(console.screen(),encoding='utf-8'); raise AssertionError('Missing screen text: '+text)
    def shot(name):
        (HERE/(name+'.txt')).write_text(console.screen(),encoding='utf-8')
        r=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'shot_hwnd.ps1'),str(int(console.hwnd or 0)),str(HERE/(name+'.png'))],capture_output=True,text=True,creationflags=0x08000000)
        log(name+' screenshot='+r.stdout.strip()+r.stderr.strip()[:150])
    def select(label,limit=45):
        for _ in range(limit):
            if any(line.lstrip().startswith('>') and label in line for line in console.screen().splitlines()): return
            console.key('down',pause=.3)
        raise AssertionError('Unable to select '+label)
    expect('10. External event inputs'); shot('01-main')
    select('6. LLM options'); console.key('enter'); expect('CielAiRouter retries'); select('CielAiRouter retries'); shot('02-retry-default')
    console.key('enter'); expect('gateway_retries for cielairouter'); console.text('0'); console.key('enter'); expect('0 extra; 0 off'); shot('03-retry-disabled')
    saved=json.loads((CFG/'config.json').read_text(encoding='utf-8')); assert saved['providers']['cielairouter']['gateway_retries']==0
    console.key('enter'); expect('gateway_retries for cielairouter'); console.text('3'); console.key('enter'); expect('3 extra; 0 off'); shot('04-retry-restored')
    console.key('esc'); select('10. External event inputs'); console.key('enter'); select('Recover SSE delivery now'); expect('SSE delivery recovery'); shot('05-sse-menu')
    console.key('enter'); expect('No session was restarted'); shot('06-sse-action-result')
    assert any(c['body']['params']['arguments']=={'action':'auto'} for c in CALLS)
    console.key('esc'); console.text('q'); process.wait(timeout=20)
    meta['menu_exit_code']=process.returncode; meta['assertions']=['retry default 3 rendered','real key input changed setting to 0 and persisted','restored setting to 3','SSE recovery row rendered','Recover SSE delivery now invoked MCP recover_sse_delivery action=auto','no-reset response displayed no session restarted']
    (HERE/'result.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); log('PASS')
except BaseException:
    log(traceback.format_exc()); (HERE/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
finally:
    if process is not None and process.poll() is None: process.terminate(); process.wait(timeout=10)
    server.shutdown(); server.server_close(); log('owned resources closed')
