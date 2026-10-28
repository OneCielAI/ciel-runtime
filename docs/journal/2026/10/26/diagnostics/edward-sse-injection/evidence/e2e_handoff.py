"""Real Claude CLI against an owned loopback gateway and router, isolated environment: Work mode usage hints.
Run: python -I <absolute path>. Set HANDOFF_PROOF_REPO to test an installed snapshot instead of the worktree."""
import json, os, re, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('HANDOFF_PROOF_REPO') or SOURCE)
NAV = SOURCE / 'docs/journal/2026/10/26/features/work-mode/evidence/navigation'
HELP = SOURCE / 'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN = Path('C:/Users/djlov/AppData/Local/Temp') / ('ciel-handoff-' + uuid.uuid4().hex[:8]); RUN.mkdir()
PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI')
STRIPPED = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED: os.environ.pop(k)
HOME, CFG, CCFG, WS = [RUN / x for x in ('home', 'cfg', 'home/.claude', 'ws')]
for d in (HOME / 'AppData/Roaming', HOME / 'AppData/Local/Temp', CFG, CCFG, WS): d.mkdir(parents=True, exist_ok=True)
TARGET = WS / 'subject.txt'; TARGET.write_text('BEFORE\n', encoding='utf-8')
CLI = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS, EGRESS, STATE, OWNED, RESULT = [], [], {}, [], {'repo': str(REPO), 'stripped_env': STRIPPED}
TITLE = 'CIELE2E-HANDOFF-' + RUN.name
SESSION = str(uuid.uuid4())


def dump(n, o): (RUN / n).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding='utf-8')
def log(s):
    print(s, flush=True)
    with (RUN / 'driver.txt').open('a', encoding='utf-8') as f: f.write(s + '\n')
def port():
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); return s.getsockname()[1]
def http(url, body=None):
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None, headers={'Content-Type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3) as r: return json.loads(r.read())
    except Exception as e: return {'error': str(e)}
def health():
    out = {}
    for p in (9505, 9524):
        h = http(f'http://127.0.0.1:{p}/health'); out[str(p)] = {k: h.get(k) for k in ('pid', 'version', 'source_fingerprint', 'error')}
    return out
def procs():
    try: return json.loads(subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-CimInstance Win32_Process | Select ProcessId,Name,CommandLine | ConvertTo-Json -Compress'], capture_output=True, text=True).stdout)
    except Exception: return []
def wait(pred, secs, label):
    end = time.time() + secs
    while time.time() < end:
        if pred(): log(label + ': yes'); return True
        time.sleep(.25)
    log(label + ': NO'); return False
# The owned window is kept minimized so keystrokes meant for other windows cannot land in it;
# it is shown without activation only for the moment a screenshot is taken.
import ctypes
from ctypes import wintypes
USER32 = ctypes.windll.user32
def owned_window():
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(h, _):
        buf = ctypes.create_unicode_buffer(512); USER32.GetWindowTextW(h, buf, 512)
        if buf.value.startswith(TITLE): found.append(h); return False
        return True
    USER32.EnumWindows(proc(visit), 0)
    return found[0] if found else None
def stop(pid):
    if pid not in OWNED: raise RuntimeError('Unowned termination refused ' + str(pid))
    r = subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True, text=True); log('owned stop ' + str(pid) + ' ' + r.stdout.strip())


class Sentinel(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def deny(self):
        EGRESS.append({'method': self.command, 'path': self.path}); self.send_response(403); self.send_header('content-length', '0'); self.end_headers()
    do_CONNECT = do_GET = do_POST = do_HEAD = deny



sys.path.insert(0,str(REPO))
from ciel_runtime_support.claude_session_socket import ClaudeSessionSocketClient, generated_socket_path
from ciel_runtime_support.runtime_compaction_binding import RuntimeCompactionBinding
from types import SimpleNamespace

EVENTS=[]
class Fake(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/events":
            self.send_response(404);self.end_headers();return
        self.send_response(200);self.send_header("content-type","text/event-stream");self.end_headers()
        seen=0
        try:
            while len(EVENTS)>seen or not STATE.get("stop_events"):
                if len(EVENTS)>seen:
                    ev=EVENTS[seen];seen+=1
                    self.wfile.write(("data: "+json.dumps(ev)+chr(10)*2).encode());self.wfile.flush()
                else:time.sleep(.05)
        except OSError:pass
    def log_message(self,*a):pass
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get("content-length",0))))
        txt=json.dumps(body.get("messages",[]))
        row=dict(sid=self.headers.get("x-claude-code-session-id"),before="HANDOFF-BEFORE" in txt,after="HANDOFF-AFTER" in txt)
        ROWS.append(row);dump("requests.json",ROWS)
        if "count_tokens" in self.path:
            data=json.dumps(dict(input_tokens=100)).encode()
            self.send_response(200);self.send_header("content-length",str(len(data)));self.end_headers();self.wfile.write(data);return
        reply="HANDOFF-AFTER-REPLIED" if row["after"] else "HANDOFF-BEFORE-REPLIED" if row["before"] else "HANDOFF-SEED-READY"
        events=[("message_start",dict(type="message_start",message=dict(id="msg_"+uuid.uuid4().hex,type="message",role="assistant",model=body.get("model"),content=[],stop_reason=None,stop_sequence=None,usage=dict(input_tokens=100,output_tokens=1)))),
                ("content_block_start",dict(type="content_block_start",index=0,content_block=dict(type="text",text=""))),
                ("content_block_delta",dict(type="content_block_delta",index=0,delta=dict(type="text_delta",text=reply))),
                ("content_block_stop",dict(type="content_block_stop",index=0)),
                ("message_delta",dict(type="message_delta",delta=dict(stop_reason="end_turn",stop_sequence=None),usage=dict(output_tokens=10))),
                ("message_stop",dict(type="message_stop"))]
        self.send_response(200);self.send_header("content-type","text/event-stream");self.send_header("connection","close");self.end_headers()
        try:
            for k,v in events:self.wfile.write(("event: "+k+chr(10)+"data: "+json.dumps(v)+chr(10)*2).encode());self.wfile.flush()
        except OSError:pass
        self.close_connection=True

GATE,SENT,ROUTER=port(),port(),port()
SERVERS=[]
for pn,handler in ((GATE,Fake),(SENT,Sentinel)):
    server=ThreadingHTTPServer(("127.0.0.1",pn),handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();SERVERS.append(server)
BASE=os.environ.copy()
BASE.update(HOME=str(HOME),USERPROFILE=str(HOME),APPDATA=str(HOME/"AppData/Roaming"),LOCALAPPDATA=str(HOME/"AppData/Local"),
TEMP=str(HOME/"AppData/Local/Temp"),TMP=str(HOME/"AppData/Local/Temp"),CLAUDE_CONFIG_DIR=str(CCFG),ANTHROPIC_BASE_URL=f"http://127.0.0.1:{GATE}",ANTHROPIC_AUTH_TOKEN="fixture",ANTHROPIC_API_KEY="",ANTHROPIC_MODEL="fake",ANTHROPIC_DEFAULT_OPUS_MODEL="fake",ANTHROPIC_DEFAULT_SONNET_MODEL="fake",ANTHROPIC_DEFAULT_HAIKU_MODEL="fake",HTTP_PROXY=f"http://127.0.0.1:{SENT}",HTTPS_PROXY=f"http://127.0.0.1:{SENT}",ALL_PROXY=f"http://127.0.0.1:{SENT}",NO_PROXY="127.0.0.1,localhost,::1",DISABLE_AUTOUPDATER="1",CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",DISABLE_TELEMETRY="1")
(CCFG/".claude.json").write_text(json.dumps(dict(hasCompletedOnboarding=True,lastOnboardingVersion="2.1.310",numStartups=3,bypassPermissionsModeAccepted=True,projects={k:dict(hasTrustDialogAccepted=True,allowedTools=[]) for k in (str(WS),WS.as_posix(),str(WS).lower(),WS.as_posix().lower())})),encoding="utf-8")
pipe=generated_socket_path();logs=[]
def clog(level,text):logs.append(dict(level=level,text=text,at=time.time()))
os.environ["CLAUDE_CONFIG_DIR"]=str(CCFG)
client=ClaudeSessionSocketClient(HOME,clog);client.configure(pipe)
budget=SimpleNamespace(compact_kind=lambda body:None,context_limit=lambda *a:128000,configured_output=lambda *a:1000,cap_output_ratio=lambda *a:1000,reserve=lambda *a:1000,positive_int=lambda v:int(v or 0))
binding=RuntimeCompactionBinding(RUN/"binding",HOME,SimpleNamespace(queue_automatic=lambda *a:None,clear=lambda *a:None),lambda:SimpleNamespace(budget=budget),lambda body:100,lambda:("other",{}),clog)
binding.bind("claude",cwd=WS,messaging_socket_path=pipe)
def sessions():
    out=[]
    for p in (CCFG/"sessions").glob("*.json"):
        try:out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError,ValueError):pass
    return out
BEFORE=health();dump("live-before.json",BEFORE)
old_daemons={r["ProcessId"] for r in procs() if "daemon run" in (r.get("CommandLine") or "")}
if os.environ.get("HANDOFF_LAUNCHER") == "1":
    BASE.update(CIEL_RUNTIME_CONFIG_DIR=str(CFG),CIEL_RUNTIME_WORKSPACE_STATE_DIR=str(CFG),CIEL_RUNTIME_TEST_ISOLATED="1",CIEL_RUNTIME_ROUTER_PORT=str(ROUTER))
    (CFG/"config.json").write_text(json.dumps(dict(current_provider="cielairouter",language="en",work_mode=False,providers=dict(cielairouter=dict(base_url=f"http://127.0.0.1:{GATE}",api_key="fixture",current_model="fake",context_window=128000,max_output_tokens=4096,gateway_data_path="router")))),encoding="utf-8")
    (CFG/"log-level").write_text("INFO")
checks={};hwnd=None;cmdpid=0

from ciel_runtime_support.channel_message_repository import ChannelMessageAppendPorts, ChannelMessageRepository, exclusive_file_lock
from ciel_runtime_support.channel_message_policy import string_list
from ciel_runtime_support.runtime_input_gateway import RuntimeInputGateway
from ciel_runtime_support.runtime_input_status import RuntimeInputStatusRepository
from ciel_runtime_support.channel_wake_delivery_repository import ChannelWakeDeliveryRepository
from ciel_runtime_support.channel_pending_injection import (ChannelInjectionIO, ChannelInjectionPolicy, ChannelInjectionPrompts,
    ChannelInjectionServices, ChannelInjectionState, ChannelInjectionWakeStore, inject_pending_channel_messages)
from ciel_runtime_support.external_event_receiver import EventReceiverSecretVault, ExternalEventReceiverService
from ciel_runtime_support.channel_wake_claim_repository import prompt_message_ids, prompt_references_message_id
from ciel_runtime_support.channel_transcript import ChannelWakeTranscriptServices, wake_state_evidence_from_text
queue=ChannelMessageRepository(RUN/"runtime-inputs.jsonl",clog)
append_ports=ChannelMessageAppendPorts(condition=threading.Condition(),file_lock=lambda:exclusive_file_lock(queue.path),duplicate=lambda _:None,normalize_recipients=string_list)
transitions=[]
def status_event(**event):transitions.append((event["data"]["request_id"],event["data"]["status"]))
status=RuntimeInputStatusRepository(RUN/"runtime-input-status.jsonl",status_event,clog,threading.RLock())
gateway=RuntimeInputGateway(lambda body:queue.append(body,append_ports),status=status)
cursor=[0]
lifecycle=ChannelWakeDeliveryRepository(lock=threading.RLock(),delivered=set(),prompts={},batches={},clear_claim=lambda _:None,commit_cursor=lambda n:cursor.__setitem__(0,n),status=status)
lifecycle.bind_child_epoch("owned-native-handoff")
tservices=ChannelWakeTranscriptServices(claim_prompt=lifecycle.prompt,prompt_references_message_id=prompt_references_message_id,prompt_message_ids=prompt_message_ids,now=time.time)
def wake(mid,prompt=None):
    path=binding.transcript_path()
    text=path.read_text(encoding="utf-8") if path else ""
    return wake_state_evidence_from_text(mid,text,[prompt] if prompt else (),tservices).state
def no_tty(*a,**k):raise AssertionError("handoff tried terminal fallback")
def format_prompt(rows):return " ".join(r["message"] for r in rows)
injection=ChannelInjectionServices(
    state=ChannelInjectionState(active_tool_call=lambda:False,active_turn=lambda:False,recover_cursor=lambda n:n,pending_scan_limit=lambda:20,superseded_ids=lambda _:set(),message_is_web_chat=lambda _:False,message_skip_reason=lambda _:"",event_identity_key=lambda _:(),wake_state_for_message=lambda r,p:wake(r["id"],p),queued_wake_is_stale=lambda *a:False),
    prompts=ChannelInjectionPrompts(llm_delivery=format_prompt,visible_llm_delivery=format_prompt,web_chat=format_prompt,standard=format_prompt,enter_bytes=lambda _:bytes([13]),enter_label=lambda _:"cr"),
    wake_store=ChannelInjectionWakeStore(claim_for_nonblocking_scan=lambda _:False,claim_prompt=lambda *a:True,clear_claim=lambda _:None,release_stale=lambda *a:None,lifecycle=lifecycle,commit_cursor=lambda n:cursor.__setitem__(0,n)),
    io=ChannelInjectionIO(inject_lock=threading.Lock(),read_messages=lambda n,*a:queue.read(n),write_prompt=no_tty,log=clog,write_session_socket=client.send,session_socket_available=client.available,external_admission_paused=binding.external_admission_paused),
    policy=ChannelInjectionPolicy(wake_batch_limit=lambda:1,replay_skip_reason=lambda _:""))
rconfig=dict(external_event_receivers=dict(fixture=dict(default=dict(enabled=True,transport="sse",input_transport="session_socket",url=f"http://127.0.0.1:{GATE}/events"))))
receiver=ExternalEventReceiverService(load_config=lambda:rconfig,save_config=lambda _:None,write_json=lambda *a:None,submit_event=gateway.submit_external_event,vault=EventReceiverSecretVault(RUN/"fixture.vault.json"),workspace_key="fixture",log=clog,cursor_path=RUN/"sse-cursor.json")
receiver.start()
def send_event(marker):
    if os.environ.get("HANDOFF_LAUNCHER") == "1":
        response=http(f"http://127.0.0.1:{ROUTER}/ca/events/receivers/default",dict(enabled=True,transport="sse",input_transport="session_socket",url=f"http://127.0.0.1:{GATE}/events",event_types=["com.ciel.handoff.message"]))
        if not response.get("ok"):raise RuntimeError("production receiver configuration failed: "+str(response))
        EVENTS.append(dict(specversion="1.0",id=uuid.uuid4().hex,source="fixture",type="com.ciel.handoff.message",data=dict(text=marker+" reply briefly without tools")))
        wanted="after" if marker=="HANDOFF-AFTER" else "before"
        if not wait(lambda:any(r[wanted] for r in ROWS),45,"production SSE model request"):return False
        def settled():
            for f in CFG.rglob("runtime-input-status.jsonl"):
                try:
                    latest={}
                    for line in f.read_text(encoding="utf-8").splitlines():
                        try:
                            row=json.loads(line);latest[row["request_id"]]=row
                        except (ValueError,KeyError):pass
                    if sum(r.get("status")=="replied" for r in latest.values())>=len(EVENTS):return True
                except OSError:pass
            return False
        return wait(settled,45,"production delivery replied")
    old=queue.max_id()
    EVENTS.append(dict(specversion="1.0",id=uuid.uuid4().hex,source="fixture",type="com.ciel.handoff.message",data=dict(text=marker+" reply briefly without tools")))
    if not wait(lambda:queue.max_id()>old,10,"SSE admitted"):return False
    row=queue.read(old)[0];mid=row["id"]
    if not wait(lambda:status.get(mid) is not None,5,"queued status"):return False
    def submit():
        result=inject_pending_channel_messages(None,cursor[0],services=injection,terminal_available=False)
        cursor[0]=result
        return status.get(mid).get("status")=="submitted"
    if not wait(submit,15,"SSE socket submitted"):return False
    if not wait(lambda:wake(mid,lifecycle.prompt(mid))=="completed",40,"SSE transcript replied"):return False
    lifecycle.complete(mid)
    checks[marker+"_once"]=sum("claude_session_socket_injected" in row["text"] for row in logs)==len(EVENTS)
    return status.get(mid)["status"]=="replied"

try:
    launch=RUN/"interactive.cmd"
    lines=["@echo off"]
    for prefix in PREFIXES:lines.append("for /f "+chr(34)+"delims=="+chr(34)+" %%v in ("+chr(39)+"set "+prefix+" 2^>nul"+chr(39)+") do set "+chr(34)+"%%v="+chr(34))
    for k,v in BASE.items():
        if k.upper().startswith(PREFIXES) or k in ("HOME","USERPROFILE","APPDATA","LOCALAPPDATA","TEMP","TMP","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","DISABLE_AUTOUPDATER","DISABLE_TELEMETRY"):
            lines.append("set "+chr(34)+k+"="+v+chr(34))
    launch_cmd=[str(CLI),"--dangerously-skip-permissions","--model","fake","--settings",json.dumps(dict(crossSessionInbound="accept")),"--messaging-socket-path",pipe]
    if os.environ.get("HANDOFF_LAUNCHER") == "1":
        launch_cmd=[sys.executable,str(REPO/"ciel_runtime.py"),"cli","--ca-runtime","claude","--ca-no-update-check","--ca-no-self-update-check","--messaging-socket-path",pipe,"--dangerously-skip-permissions"]
    lines += ["cd /d "+chr(34)+str(WS)+chr(34),subprocess.list2cmdline(launch_cmd)]
    launch.write_text(chr(10).join(lines)+chr(10),encoding="utf-8")
    subprocess.Popen(["wt.exe","-w","new","--title",TITLE,"--suppressApplicationTitle","cmd","/k",str(launch)])
    def findcmd():
        global cmdpid
        for r in procs():
            if r.get("Name","").lower()=="cmd.exe" and str(launch) in (r.get("CommandLine") or ""):
                cmdpid=r["ProcessId"];return True
        return False
    if not wait(findcmd,30,"owned cmd"):raise RuntimeError("no console")
    OWNED.append(cmdpid)
    wait(lambda:bool(owned_window()),15,"owned window");hwnd=owned_window()
    if hwnd:USER32.ShowWindow(hwnd,6)
    agent=subprocess.Popen([sys.executable,"-I",str(NAV/"console_agent.py"),str(cmdpid),str(RUN)],creationflags=8);OWNED.append(agent.pid)
    def screen():
        try:return (RUN/"screen-latest.txt").read_text(encoding="utf-8")
        except OSError:return ""
    def command(text=None,key=None):
        (RUN/"command-sent.json").unlink(missing_ok=True)
        (RUN/"console-command.tmp").write_text(json.dumps(dict(key=key) if key else dict(text=text)),encoding="utf-8")
        (RUN/"console-command.tmp").replace(RUN/"console-command.json")
        if not wait(lambda:(RUN/"command-sent.json").exists(),10,"console command"):raise RuntimeError("console timeout")
    def screenshot(label):
        (RUN/("screen-"+label+".txt")).write_text(screen(),encoding="utf-8")
        if hwnd:USER32.ShowWindow(hwnd,4)
        r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(HELP/"shot_title.ps1"),TITLE,str(RUN/(label+".png"))],capture_output=True,text=True,timeout=90)
        (RUN/(label+"-shot.txt")).write_text(r.stdout+r.stderr,encoding="utf-8")
        if hwnd:USER32.ShowWindow(hwnd,7)
    if not wait(lambda:"shift+tab" in screen() or "? for shortcuts" in screen(),50,"ready"):raise RuntimeError("not ready")
    command("HANDOFF-SEED reply briefly without tools")
    checks["seed_replied"]=wait(lambda:"HANDOFF-SEED-READY" in screen(),45,"seed reply")
    time.sleep(2)
    original=next(r for r in sessions() if r.get("messagingSocketPath")==pipe)
    RESULT["original"]={k:original.get(k) for k in ("pid","sessionId","kind","procStart","messagingSocketPath")}
    checks["before_socket_sent"]=send_event("HANDOFF-BEFORE")
    checks["before_socket_replied"]=wait(lambda:any(r["before"] for r in ROWS),40,"original socket reply")
    time.sleep(3);screenshot("01-before-background")
    command(key="left")
    checks["native_parked"]=wait(lambda:any(r.get("parkedJobId") for r in sessions()),40,"native parked")
    if not checks["native_parked"]:raise RuntimeError("native background not reached")
    checks["bg_record"]=wait(lambda:any(r.get("kind")=="bg" for r in sessions()),40,"bg record")
    bg=next(r for r in sessions() if r.get("kind")=="bg")
    RESULT["bg"]={k:bg.get(k) for k in ("pid","sessionId","jobId","kind","procStart","messagingSocketPath")}
    checks["binding_followed"]=wait(lambda:binding.scope() is not None and binding.scope().session_id==bg["sessionId"],30,"binding successor")
    checks["socket_followed"]=client.target==bg["messagingSocketPath"]
    checks["after_socket_sent"]=send_event("HANDOFF-AFTER")
    checks["after_reached_bg"]=wait(lambda:any(r["after"] and r["sid"]==bg["sessionId"] for r in ROWS),45,"after reached background")
    checks["after_not_original"]=not any(r["after"] and r["sid"]==original["sessionId"] for r in ROWS)
    if os.environ.get("HANDOFF_LAUNCHER") == "1":
        from pathlib import Path as _Path
        def launcher_scope():
            for scopefile in CFG.rglob("compaction-scope.json"):
                try:
                    if json.loads(scopefile.read_text(encoding="utf-8")).get("session_id")==bg["sessionId"]:return True
                except (OSError,ValueError):pass
            return False
        checks["production_launcher_scope_followed"]=wait(launcher_scope,15,"launcher successor scope")
    checks["transcript_followed"]=wait(lambda:binding.transcript_path() is not None and binding.transcript_path().stem==bg["sessionId"],15,"successor transcript")
    time.sleep(2);screenshot("02-background-injected")
except BaseException as exc:
    RESULT["error"]=type(exc).__name__+": "+str(exc)
    try:screenshot("error")
    except Exception:pass
finally:
    orig=RESULT.get("original",{}).get("pid")
    for r in procs():
        cl=r.get("CommandLine") or ""
        if r["ProcessId"] not in old_daemons and "daemon run" in cl and str(orig) in cl and str(WS) in cl.replace(chr(92)*2,chr(92)):
            OWNED.append(r["ProcessId"])
    (RUN/"stop-console").write_text("1")
    for pid in reversed(OWNED):
        try:stop(pid)
        except Exception as exc:log(str(exc))
    STATE["stop_events"]=True
    receiver.stop()
    RESULT["transitions"]=transitions
    for server in SERVERS:server.shutdown()
    AFTER=health();dump("live-after.json",AFTER)
    checks["live_unchanged"]=BEFORE==AFTER
    RESULT.update(checks=checks,requests=ROWS,logs=logs,egress=EGRESS,run=str(RUN),live_before=BEFORE,live_after=AFTER)
    dump("result.json",RESULT)
    print(json.dumps(RESULT,ensure_ascii=False,indent=1),flush=True)
    if not checks or not all(checks.values()) or RESULT.get("error"):raise SystemExit(1)
