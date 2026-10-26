import os,sys,json,time,subprocess,tempfile,socket,urllib.request,uuid,traceback,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
if "--detached" not in sys.argv:
 p=subprocess.Popen([sys.executable,"-I",str(Path(__file__).resolve()),"--detached"],creationflags=8)
 print("evaluation driver pid",p.pid,flush=True)
 raise SystemExit(p.wait(timeout=1900))
SOURCE=next(p for p in HERE.parents if (p/"ciel_runtime.py").exists())
DEPLOY=json.loads((HERE.parent/"deployment/snapshot.json").read_text())["snapshot"]
RUN=HERE/("run-"+uuid.uuid4().hex[:8]);RUN.mkdir()
SCRATCH=Path(tempfile.mkdtemp(prefix="ciel-work-coding-eval-"))
HOME=SCRATCH/"home";CFG=SCRATCH/"cfg";CCFG=HOME/".claude";WS=SCRATCH/"workspace"
for p in [HOME,CFG,CCFG,WS,HOME/"AppData/Roaming",HOME/"AppData/Local/Temp"]:p.mkdir(parents=True,exist_ok=True)
(HERE/"latest-run.txt").write_text(str(RUN),encoding="utf-8")
def dump(n,d):(RUN/n).write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding="utf-8")
def log(s):
 with (RUN/"driver.txt").open("a",encoding="utf-8") as f:f.write(time.strftime("%H:%M:%S")+" "+s+chr(10))
def http(port,path,body=None,timeout=6):
 req=urllib.request.Request(f"http://127.0.0.1:{port}"+path,data=json.dumps(body).encode() if body is not None else None,headers={"Content-Type":"application/json"})
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as r:return json.load(r)
def live():
 d={}
 for p in [9385,9464,9489,9505,9524]:
  try:d[str(p)]={k:v for k,v in http(p,"/health").items() if k in ["pid","version","ok"]}
  except Exception as e:d[str(p)]={"error":type(e).__name__}
 return d
with socket.socket() as sock:sock.bind(("127.0.0.1",0));PORT=sock.getsockname()[1]
GLOBAL=json.loads(Path("C:/Users/djlov/AppData/Roaming/ciel-runtime/config.json").read_text(encoding="utf-8"))
pc=dict(GLOBAL["providers"]["cielairouter"]);pc.update(current_model="_Walkie_Opus55-Sol61-Fast",gateway_data_path="router",context_window=200000,max_output_tokens=16000,request_timeout_ms=180000)
cfg={"current_provider":"cielairouter","language":"en","work_mode":True,"providers":{"cielairouter":pc},"web_backend":{"enabled":False},"remote_instructions":{"enabled":False},"remote_memory":{"enabled":False}}
(CFG/"config.json").write_text(json.dumps(cfg),encoding="utf-8");(CFG/"log-level").write_text("INFO")
(CCFG/".claude.json").write_text(json.dumps({"hasCompletedOnboarding":True,"lastOnboardingVersion":"2.1.310","numStartups":3,"bypassPermissionsModeAccepted":True,"projects":{k:{"hasTrustDialogAccepted":True,"allowedTools":[]} for k in [str(WS),WS.as_posix(),str(WS).lower(),WS.as_posix().lower()]}}),encoding="utf-8")
(WS/"CLAUDE.md").write_text("---"+chr(10)+"type: EvaluationWorkspace"+chr(10)+"---"+chr(10)+"Isolated evaluation. Only edit this workspace. Standard library only. Preserve all incoming requests. Use Work hierarchical plan. No external services or session control actions. Do not finalize until REQ-FINAL arrives.",encoding="utf-8")
blocked=("CIEL_RUNTIME_","CLAUDE","ANTHROPIC","CODEX","OPENAI","OLLAMA","GEMINI","GOOGLE","AWS","AZURE","OPENCODE")
env={k:v for k,v in os.environ.items() if not k.upper().startswith(blocked) and not any(s in k.upper() for s in ["TOKEN","SECRET","PASSWORD","API_KEY","CREDENTIAL","PROXY"])}
env.update(HOME=str(HOME),USERPROFILE=str(HOME),APPDATA=str(HOME/"AppData/Roaming"),LOCALAPPDATA=str(HOME/"AppData/Local"),TEMP=str(HOME/"AppData/Local/Temp"),TMP=str(HOME/"AppData/Local/Temp"),XDG_CONFIG_HOME=str(HOME/".config"),CLAUDE_CONFIG_DIR=str(CCFG),CODEX_HOME=str(SCRATCH/"codex"),CIEL_RUNTIME_CONFIG_DIR=str(CFG),CIEL_RUNTIME_TEST_ISOLATED="1",CIEL_RUNTIME_ROUTER_PORT=str(PORT),CIEL_RUNTIME_LAUNCH_CWD=str(WS),CIEL_RUNTIME_STARTUP_AUTO_ANSWER="1",DISABLE_AUTOUPDATER="1",CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",DISABLE_TELEMETRY="1",PYTHONIOENCODING="utf-8",ENABLE_TOOL_SEARCH="false",NO_PROXY="127.0.0.1,localhost,::1")
meta={"provider":"cielairouter","model":pc["current_model"],"snapshot":DEPLOY,"port":PORT,"workspace":str(WS),"scratch":str(SCRATCH),"transport":"actual MCP submit_input/session_socket","live_before":live(),"injections":[],"screens":[]}
process=None;agent=None;launcher=0
def screen():
 try:return (RUN/"screen-latest.txt").read_text(encoding="utf-8")
 except:return ""
def command(text):
 (RUN/"command-sent.json").unlink(missing_ok=True)
 (RUN/"console-command.tmp").write_text(json.dumps({"text":text}),encoding="utf-8");(RUN/"console-command.tmp").replace(RUN/"console-command.json")
 end=time.monotonic()+10
 while time.monotonic()<end and not (RUN/"command-sent.json").exists():time.sleep(.1)
def transcript():
 files=list((CCFG/"projects").glob("*/*.jsonl"))
 return max(files,key=lambda p:p.stat().st_mtime) if files else None
def activity():
 try:return json.loads((CFG/"router-activity.json").read_text(encoding="utf-8"))
 except:return {}
def tstate():
 p=transcript();d={"bytes":p.stat().st_size if p else 0,"last_type":None,"last_tools":[]}
 if p:
  with p.open("rb") as f:f.seek(max(0,p.stat().st_size-250000));lines=f.read().splitlines()[1:]
  for raw in lines[-70:]:
   try:o=json.loads(raw)
   except:continue
   if o.get("type") in ["assistant","user"]:
    d["last_type"]=o["type"];d["timestamp"]=o.get("timestamp")
    if o["type"]=="assistant":d["last_tools"]=[b.get("name") for b in o.get("message",{}).get("content",[]) if isinstance(b,dict) and b.get("type")=="tool_use"]
 return d
def mcp(name,args):return http(PORT,"/ca/mcp",{"jsonrpc":"2.0","id":uuid.uuid4().hex,"method":"tools/call","params":{"name":name,"arguments":args}},timeout=15)
def shot(label):
 (RUN/(label+".txt")).write_text(screen(),encoding="utf-8")
 helper=SOURCE/"docs/journal/2026/10/26/features/work-mode/evidence/menu/shot_hwnd.ps1"
 h=RUN/"console-hwnd.txt"
 if h.exists():
  result=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(helper),h.read_text(),str(RUN/(label+".png"))],capture_output=True,text=True,timeout=25)
  meta["screens"].append({"label":label,"exit":result.returncode,"bytes":(RUN/(label+".png")).stat().st_size if (RUN/(label+".png")).exists() else 0})
try:
 process=subprocess.Popen([os.path.join(os.environ.get("SystemRoot","C:/Windows"),"System32/conhost.exe"),sys.executable,"-I",str(HERE/"owned_bootstrap.py"),DEPLOY,str(RUN)],env=env,cwd=WS)
 deadline=time.monotonic()+100
 while time.monotonic()<deadline and not (RUN/"launcher.pid").exists():
  if process.poll() is not None:raise RuntimeError("owned launcher exited")
  time.sleep(.3)
 launcher=int((RUN/"launcher.pid").read_text());meta["owned_launcher_pid"]=launcher;meta["console_host_pid"]=process.pid
 agent=subprocess.Popen([sys.executable,"-I",str(SOURCE/"docs/journal/2026/10/25/fixes/teammate-model-inheritance/evidence/console_agent.py"),str(launcher),str(RUN)],creationflags=8);meta["owned_console_agent_pid"]=agent.pid
 end=time.monotonic()+120
 while time.monotonic()<end:
  if "shift+tab" in screen() or "? for shortcuts" in screen():break
  time.sleep(.5)
 else:raise RuntimeError("CLI startup timeout: "+screen()[-800:])
 helper=RUN/"hwnd.py"
 helper.write_text("import ctypes,sys"+chr(10)+"from pathlib import Path"+chr(10)+"k=ctypes.WinDLL('kernel32');k.GetConsoleWindow.restype=ctypes.c_void_p;k.FreeConsole();k.AttachConsole(int(sys.argv[1]));Path(sys.argv[2]).write_text(str(k.GetConsoleWindow()))",encoding="utf-8")
 subprocess.run([sys.executable,"-I",str(helper),str(launcher),str(RUN/"console-hwnd.txt")],creationflags=8,timeout=10)
 shot("00-ready")
 requests=json.loads((HERE/"requests.json").read_text(encoding="utf-8"))
 (WS/"initial_request.txt").write_text(requests[0]["text"],encoding="utf-8")
 command("Read initial_request.txt completely and carry out REQ-A0. Further requests will arrive while you work.")
 meta["injections"].append({"id":"A0","at":time.time(),"transport":"short console instruction -> complete request file"})
 last_bytes=0
 first_deadline=time.monotonic()+100
 while time.monotonic()<first_deadline:
  tp=transcript()
  if tp and "REQ-A0:" in tp.read_text(encoding="utf-8") and "TTL expiration is inclusive" in tp.read_text(encoding="utf-8"):break
  time.sleep(1)
 else:raise RuntimeError("complete initial request not observed in transcript")
 meta["initial_full_request_verified"]=True
 for request in requests[1:]:
  end=time.monotonic()+150;active=False
  while time.monotonic()<end:
   state=tstate();act=activity()
   if state["bytes"]>last_bytes+800 and ((state.get("last_type")=="assistant" and state.get("last_tools")) or (act.get("event") in ["request","retry"] and time.time()-float(act.get("updated_at") or 0)<20)):active=True;break
   time.sleep(1)
  if not active:log("no active-state proof before "+request["id"])
  if request["id"] not in ["B0","C0"]:time.sleep(6)
  before=tstate();at=time.time();resp=mcp("submit_input",{"message":request["text"],"channel":"eval:"+request["task"],"sender_id":"coding-evaluator","thread_id":"work-eval-"+request["task"],"input_transport":"session_socket","response_mode":"mcp"})
  meta["injections"].append({"id":request["id"],"at":at,"activity_observed":active,"before":before,"router_activity":{k:v for k,v in activity().items() if k in ["event","time","tokens","attempt"]},"response":resp});dump("progress.json",meta);log("injected "+request["id"]);last_bytes=before["bytes"];shot("inject-"+request["id"]);time.sleep(10)
 end=time.monotonic()+1000
 while time.monotonic()<end:
  if process.poll() is not None:raise RuntimeError("owned console exited during evaluation")
  if "503 Ciel Router deferred wake" in screen():raise RuntimeError("transport rejected input; coding retention not evaluated")
  current=transcript();finished=False
  if current and (WS/"completion.json").exists():
   with current.open("rb") as f:f.seek(max(0,current.stat().st_size-120000));tail=f.read().splitlines()[1:]
   for raw in tail:
    try:entry=json.loads(raw)
    except:continue
    if entry.get("type")=="assistant":
     blocks=entry.get("message",{}).get("content",[])
     if any(b.get("type")=="text" and "EVAL-COMPLETE" in b.get("text","") for b in blocks if isinstance(b,dict)) and entry.get("message",{}).get("stop_reason")=="end_turn":finished=True
  if finished:break
  time.sleep(3)
 else:meta["timeout"]=True
 shot("99-final");meta["final_screen"]=screen()[-5000:]
 p=transcript()
 if p:
  shutil.copyfile(p,RUN/"transcript.jsonl");meta["session_id"]=p.stem
  meta["work_status"]=mcp("work_status",{"format":"json","session_id":"claude:"+p.stem})
  for plan in CFG.rglob("work-plan.json"):shutil.copyfile(plan,RUN/"work-plan.json")
 meta["produced_files"]=[p.relative_to(WS).as_posix() for p in WS.rglob("*") if p.is_file() and "__pycache__" not in p.parts];meta["status"]="run finished; independent verification pending"
 shutil.copytree(WS,RUN/"workspace",ignore=shutil.ignore_patterns("__pycache__"),dirs_exist_ok=True)
except BaseException:
 meta["error"]=traceback.format_exc();log(meta["error"])
finally:
 for name in ["router.log","runtime-inputs.jsonl","runtime-input-status.jsonl"]:
  if (CFG/name).exists():shutil.copyfile(CFG/name,RUN/name)
 (RUN/"stop-console").write_text("stop")
 if launcher:subprocess.run(["taskkill","/T","/F","/PID",str(launcher)],capture_output=True)
 if agent and agent.poll() is None:agent.terminate();agent.wait(timeout=10)
 if process and process.poll() is None:process.terminate();process.wait(timeout=10)
 try:
  pid=int((CFG/"router.pid").read_text());subprocess.run(["taskkill","/T","/F","/PID",str(pid)],capture_output=True)
 except:pass
 meta["live_after"]=live();meta["live_pids_unchanged"]=all(v.get("pid")==meta["live_after"][k].get("pid") for k,v in meta["live_before"].items())
 dump("result.json",meta);log("finished "+str(RUN));(CFG/"config.json").unlink(missing_ok=True)
