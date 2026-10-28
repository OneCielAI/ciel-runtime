"""Owned E2E: production prelaunch menu, LLM options -> Work mode / Work plan approval, real key input."""
from __future__ import annotations
import ctypes, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=next(p for p in HERE.parents if (p/'ciel_runtime.py').is_file())
if '--detached' not in sys.argv:
    p=subprocess.Popen([sys.executable,'-I',str(Path(__file__).resolve()),'--detached'],creationflags=8)
    print('owned driver pid='+str(p.pid)); rc=p.wait(timeout=600)
    r=json.loads((HERE/'result.json').read_text(encoding='utf-8')); print('driver exit',rc,'status',r.get('status'),'console',r.get('console_host'))
    print('shots',[(a['capture'],a['exit_code'],a['png_bytes']) for a in r.get('pixel_capture_attempts',[])]); raise SystemExit(rc)
LOG=(HERE/'driver.log').open('w',encoding='utf-8')
def log(s): LOG.write(time.strftime('%H:%M:%S')+' '+s+chr(10)); LOG.flush()
SCRATCH=Path(tempfile.mkdtemp(prefix='ciel-work-menu-'))
CFG,HOME,WS=SCRATCH/'cfg',SCRATCH/'home',SCRATCH/'workspace'
for p in (CFG,HOME,WS,HOME/'.claude',HOME/'.codex',HOME/'AppData/Roaming',HOME/'AppData/Local',SCRATCH/'tmp'): p.mkdir(parents=True,exist_ok=True)
REQS=[]
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,v):
        raw=json.dumps(v).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        REQS.append('GET '+self.path)
        if self.path=='/health': return self.send({'ok':True,'workspace':str(WS)})
        if self.path=='/ca/events/receivers': return self.send({'ok':True,'receivers':[{'id':'default','status':{'state':'disabled','phase':'fixture','delivery':{'state':'ok','known_queued_count':0,'queued_coverage':'complete'}}}]})
        return self.send({'object':'list','data':[{'id':'EVIDENCE-MODEL','object':'model'}]})
    def do_POST(self):
        REQS.append('POST '+self.path); self.send_response(404); self.end_headers()
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture); port=server.server_address[1]
threading.Thread(target=server.serve_forever,daemon=True).start()
config={'current_provider':'cielairouter','language':'en','providers':{'cielairouter':{'base_url':f'http://127.0.0.1:{port}','current_model':'EVIDENCE-MODEL','gateway_retries':3}},'web_backend':{'enabled':False}}
(CFG/'config.json').write_text(json.dumps(config),encoding='utf-8')
blocked=('CIEL_RUNTIME_','CLAUDE','ANTHROPIC_','CODEX_','OPENAI_','OLLAMA_','GEMINI_','GOOGLE_','AZURE_','AWS_','DEEPSEEK_','MISTRAL_','NVIDIA_','ZAI_','KIMI_','GROQ_','OPENCODE_','OPENROUTER_','XAI_','GITHUB_','COPILOT_')
stripped=sorted(k for k in os.environ if k.upper().startswith(blocked) or any(w in k.upper() for w in ('TOKEN','SECRET','PASSWORD','API_KEY','CREDENTIAL','PROXY')))
env={k:v for k,v in os.environ.items() if k not in stripped}
env.update({'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_ROUTER_PORT':str(port),'CIEL_RUNTIME_LAUNCH_CWD':str(WS),'CIEL_RUNTIME_SKIP_SELF_UPDATE':'1','HOME':str(HOME),'USERPROFILE':str(HOME),'APPDATA':str(HOME/'AppData/Roaming'),'LOCALAPPDATA':str(HOME/'AppData/Local'),'CLAUDE_CONFIG_DIR':str(HOME/'.claude'),'CODEX_HOME':str(HOME/'.codex'),'PYTHONIOENCODING':'utf-8','TMP':str(SCRATCH/'tmp'),'TEMP':str(SCRATCH/'tmp'),'TMPDIR':str(SCRATCH/'tmp'),'NO_PROXY':'*','no_proxy':'*'})
meta={'scratch':str(SCRATCH),'port':port,'workspace':str(WS),'scope':'Actual production portable_prelaunch_menu (worktree source) with Win32 key input in a harness-owned console; catalogue-only owned loopback fixture on an OS-assigned free port; temporary HOME/APPDATA/config. No Claude/Codex launch, no live router contact.','cleared_environment_prefixes':list(blocked),'stripped_variable_names_count':len(stripped),'pixel_capture_attempts':[],'steps':[]}
meta['source_sha256']={n:hashlib.sha256((REPO/n).read_bytes()).hexdigest() for n in ('ciel_runtime_support/provider_option_panel.py','ciel_runtime_support/llm_option_config.py','ciel_runtime_support/work_mode_launch.py','ciel_runtime.py')}
(HERE/'scope.json').write_text(json.dumps({k:meta[k] for k in ('scratch','port','scope','cleared_environment_prefixes','stripped_variable_names_count','source_sha256')},indent=2),encoding='utf-8')
k32=ctypes.WinDLL('kernel32',use_last_error=True)
def pid_alive(pid):
    h=k32.OpenProcess(0x1000|0x100000,False,pid)
    if not h: return False
    code=ctypes.c_ulong(); k32.GetExitCodeProcess(h,ctypes.byref(code)); k32.CloseHandle(h); return code.value==259
def kill_pid(pid):
    h=k32.OpenProcess(0x0001,False,pid)
    if h: k32.TerminateProcess(h,1); k32.CloseHandle(h)
def saved(): return json.loads((CFG/'config.json').read_text(encoding='utf-8'))
process=None; menu_pid=None
try:
    boot=[sys.executable,'-I',str(HERE/'menu_bootstrap.py'),str(REPO),str(HERE)]
    conhost=os.path.join(os.environ.get('SystemRoot','C:/Windows'),'System32','conhost.exe')
    if Path(conhost).is_file():
        process=subprocess.Popen([conhost]+boot,cwd=WS,env=env); meta['console_host']='conhost.exe (classic window, owned)'
    else:
        process=subprocess.Popen(boot,cwd=WS,env=env,creationflags=16); meta['console_host']='CREATE_NEW_CONSOLE'
    for _ in range(300):
        if (HERE/'menu.pid').is_file(): menu_pid=int((HERE/'menu.pid').read_text()); break
        if process.poll() is not None: raise RuntimeError('owned console host exited early')
        time.sleep(.2)
    else: raise RuntimeError('menu.pid not written')
    meta['owned_pids']={'console_host':process.pid,'menu':menu_pid}
    log(f'owned host pid={process.pid} menu pid={menu_pid} scratch={SCRATCH} port={port}')
    spec=importlib.util.spec_from_file_location('owned_console_driver',HERE/'console_driver.py'); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    console=None
    for _ in range(50):
        try: console=module.Console(menu_pid); break
        except OSError: time.sleep(.2)
    if console is None: raise RuntimeError('owned console did not become attachable')
    k=module.kernel32; k.SetConsoleScreenBufferSize(console.conout,module.COORD(150,100)); rect=module.SMALL_RECT(0,0,149,59); k.SetConsoleWindowInfo(console.conout,True,ctypes.byref(rect))
    def expect(text,timeout=45):
        if not console.wait_for(text,timeout):
            (HERE/'failed-screen.txt').write_text(console.screen(),encoding='utf-8'); raise AssertionError('Missing screen text: '+text)
    def shot(name):
        time.sleep(.5); (HERE/(name+'.txt')).write_text(console.screen(),encoding='utf-8'); png=HERE/(name+'.png')
        r=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HERE/'shot_hwnd.ps1'),str(int(console.hwnd or 0)),str(png)],capture_output=True,text=True,creationflags=0x08000000)
        meta['pixel_capture_attempts'].append({'capture':name,'hwnd':int(console.hwnd or 0),'exit_code':r.returncode,'message':(r.stdout.strip()+r.stderr.strip())[:200],'png_bytes':png.stat().st_size if png.exists() else 0})
    def select(label,limit=60):
        for _ in range(limit):
            if any(l.lstrip().startswith('>') and label in l for l in console.screen().splitlines()): return
            console.key('down',pause=.3)
        (HERE/'failed-screen.txt').write_text(console.screen(),encoding='utf-8'); raise AssertionError('Unable to select '+label)
    def step(name,label,value,cfg_check):
        lines=[l.strip() for l in console.screen().splitlines() if label in l and value in l and not l.strip().startswith(label+' '+value+'.')]
        assert lines,(name,label,value,[l for l in console.screen().splitlines() if label in l])
        c=saved(); assert cfg_check(c),(name,c)
        meta['steps'].append({'step':name,'row':lines[0],'saved_work_mode':c.get('work_mode','<absent>'),'saved_work_plan_approval_ask':c.get('work_plan_approval_ask','<absent>')})
    expect('10. External event inputs'); shot('01-main')
    select('6. LLM options'); console.key('enter'); expect('Work plan approval')
    select('Work mode'); shot('02-default-work-on-approval-auto')
    step('default-work','Work mode','on (default)',lambda c:'work_mode' not in c)
    step('default-approval','Work plan approval','auto (default)',lambda c:'work_plan_approval_ask' not in c)
    console.key('enter'); expect('Work mode off.'); shot('03-work-off')
    step('enter-work-off','Work mode','off',lambda c:c['work_mode'] is False)
    select('Work mode'); console.key('enter'); expect('Work mode on (default).'); shot('04-work-on')
    step('enter-work-on','Work mode','on (default)',lambda c:c['work_mode'] is True)
    select('Work plan approval'); console.key('enter'); expect('Work plan approval ask user.'); shot('05-approval-ask')
    step('enter-approval-ask','Work plan approval','ask user',lambda c:c['work_plan_approval_ask'] is True and c['work_mode'] is True)
    select('Work plan approval'); console.key('enter'); expect('Work plan approval auto (default).'); shot('06-approval-auto')
    step('enter-approval-auto','Work plan approval','auto (default)',lambda c:c['work_plan_approval_ask'] is False and c['work_mode'] is True)
    console.key('esc'); time.sleep(.8); console.text('q')
    for _ in range(100):
        if not pid_alive(menu_pid): break
        time.sleep(.2)
    meta['menu_exit_txt']=(HERE/'menu-exit.txt').read_text(encoding='utf-8') if (HERE/'menu-exit.txt').exists() else None
    assert not pid_alive(menu_pid),'menu did not exit on esc,q'
    meta['final_saved_config']={k:v for k,v in saved().items() if k in ('work_mode','work_plan_approval_ask','current_provider')}
    meta['status']='passed'; log('PASS')
except BaseException:
    meta['status']='failed'; meta['failure']=traceback.format_exc(); log(meta['failure']); (HERE/'failure.txt').write_text(meta['failure'],encoding='utf-8')
finally:
    if menu_pid and pid_alive(menu_pid): kill_pid(menu_pid); meta['menu_force_terminated']=True
    if process is not None and process.poll() is None:
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=10); meta['host_force_terminated']=True
    server.shutdown(); server.server_close()
    meta['owned_menu_exited']=bool(menu_pid) and not pid_alive(menu_pid); meta['owned_host_exited']=process is not None and process.poll() is not None
    meta['fixture_requests']=sorted(set(REQS))
    (HERE/'result.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); log('owned resources closed')
    shutil.rmtree(SCRATCH,ignore_errors=True)
    raise SystemExit(0 if meta.get('status')=='passed' else 1)
