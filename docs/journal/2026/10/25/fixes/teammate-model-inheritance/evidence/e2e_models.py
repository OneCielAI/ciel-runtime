"""Real CLI + source-generated environment; loopback Messages stub, metadata only."""
import json, os, socket, subprocess, sys, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[7]
HELPERS=REPO/'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
CASE=sys.argv[1]
RUN=HERE/('e2e-'+CASE+'-'+uuid.uuid4().hex[:6]); RUN.mkdir()
HOME,CFG,CCFG,WS=(RUN/x for x in ('home','cfg','ccfg','ws'))
for d in (HOME/'AppData/Roaming',HOME/'AppData/Local/Temp',CFG,CCFG,WS):d.mkdir(parents=True,exist_ok=True)
PREFIXES=('CIEL_RUNTIME_','CLAUDE','ANTHROPIC','CODEX','OPENAI')
STRIPPED=sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED:os.environ.pop(k)
UID=uuid.uuid4().hex[:8]; TITLE='CIELE2E-MODELS-'+UID
MODEL_A='fake-parent-a'; MODEL_B='fake-parent-b'; HAIKU='fake-haiku'; SONNET='fake-sonnet'
ROWS=[]; EGRESS=[]; STATE={}
CLI=Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
def dump(name,obj): (RUN/name).write_text(json.dumps(obj,indent=1,ensure_ascii=False),encoding='utf-8')
def log(s):
 print(s,flush=True)
 with (RUN/'driver.txt').open('a',encoding='utf-8') as f:f.write(s+'\n')
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def http(url):
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=6) as r:return json.loads(r.read())
 except Exception as e:return {'error':type(e).__name__}
def health():
 return {str(p):{k:v for k,v in http(f'http://127.0.0.1:{p}/health').items() if k in ('ok','pid','version','error')} for p in (9385,9464,9489,9505,9524)}
def ps(s):return subprocess.run(['powershell','-NoProfile','-Command',s],capture_output=True,text=True).stdout
def procs():
 try:return json.loads(ps('Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'))
 except Exception:return []
def wait(pred,secs,label):
 end=time.time()+secs
 while time.time()<end:
  if pred():log(label+': yes');return True
  time.sleep(.3)
 log(label+': NO');return False
class Sentinel(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def deny(self):
  EGRESS.append({'method':self.command,'host':self.path.split('/')[0] if self.command=='CONNECT' else __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(self.path).netloc});self.send_response(403);self.send_header('content-length','0');self.end_headers()
 do_CONNECT=do_GET=do_POST=do_HEAD=deny
class Fake(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def sendj(self,obj):
  b=json.dumps(obj).encode();self.send_response(200);self.send_header('content-type','application/json');self.send_header('content-length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  return self.sendj({'object':'list','data':[{'id':m,'object':'model','context_window':128000,'max_tokens':4096,'capabilities':{'tool_calling':True,'thinking':True}} for m in (MODEL_A,MODEL_B,HAIKU,SONNET)]})
 def do_POST(self):
  raw=self.rfile.read(int(self.headers.get('content-length') or 0));b=json.loads(raw or b'{}')
  if 'count_tokens' in self.path:return self.sendj({'input_tokens':10})
  msgs=b.get('messages') or []; tools={t.get('name'):t for t in b.get('tools') or [] if isinstance(t,dict)}
  users=[m for m in msgs if m.get('role')=='user']; first=json.dumps(users[0].get('content')) if users else ''
  latest=json.dumps(users[-1].get('content')) if users else ''
  tag=__import__('re').search(r'E2ECHILD-([A-Za-z0-9_-]+)',first)
  parent_tags=__import__('re').findall(r'E2EPARENT-([A-Za-z0-9_-]+)', ' '.join(m['content'] if isinstance(m.get('content'),str) else ' '.join(x.get('text','') for x in m.get('content',[]) if isinstance(x,dict) and x.get('type')=='text') for m in users))
  parent=__import__('re').search(r'([A-Za-z0-9_-]+)',parent_tags[-1]) if parent_tags and not tag else None
  err=any(x.get('is_error') for m in users if isinstance(m.get('content'),list) for x in m['content'] if isinstance(x,dict))
  props=tools.get('Agent',{}).get('input_schema',{}).get('properties') or {}
  row={'n':len(ROWS)+1,'path':self.path.split('?')[0],'model':b.get('model'),'role':'child' if tag else 'parent' if parent else 'aux','tag':tag.group(1) if tag else parent.group(1) if parent else None,'ua':self.headers.get('User-Agent'),'agent_schema_model':'model' in props,'agent_schema_keys':list(props),'prior_tool_error':err}
  content=[{'type':'text','text':'E2E-AUX-OK'}];stop='end_turn'
  if tag:content=[{'type':'text','text':'CHILD-OK-'+tag.group(1)}]
  elif parent and 'Agent' in tools:
   key=parent.group(1); stage=STATE.get(key,0);STATE[key]=stage+1
   if stage==0 or (stage==1 and err):
    inp={'description':'verify child model','prompt':'E2ECHILD-'+key+' Return CHILD-OK-'+key+'.','subagent_type':'haiku-def' if 'definition' in key else 'general-purpose'}
    if 'named' in key:inp['name']='e2e-mate-'+('new-' if 'switched' in key else '')+UID
    else:inp['run_in_background']=False
    if ('haiku' in key or 'sonnet' in key) and stage==0:inp['model']='sonnet' if 'sonnet' in key else 'haiku'
    content=[{'type':'tool_use','id':'toolu_'+key+str(stage),'name':'Agent','input':inp}];stop='tool_use';row['served_tool_model']=inp.get('model');row['served_name']=inp.get('name');row['retry_without_model']=stage==1
   else:content=[{'type':'text','text':'PARENT-OK-'+key}]
  ROWS.append(row)
  with (RUN/'requests.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
  msg={'id':'msg_'+uuid.uuid4().hex[:10],'type':'message','role':'assistant','model':b.get('model'),'content':content,'stop_reason':stop,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':5}}
  if not b.get('stream'):return self.sendj(msg)
  events=[('message_start',{'type':'message_start','message':{**msg,'content':[],'stop_reason':None}})]
  for i,c in enumerate(content):
   empty={'type':'tool_use','id':c['id'],'name':c['name'],'input':{}} if c['type']=='tool_use' else {'type':'text','text':''}
   delta={'type':'input_json_delta','partial_json':json.dumps(c['input'])} if c['type']=='tool_use' else {'type':'text_delta','text':c['text']}
   events += [('content_block_start',{'type':'content_block_start','index':i,'content_block':empty}),('content_block_delta',{'type':'content_block_delta','index':i,'delta':delta}),('content_block_stop',{'type':'content_block_stop','index':i})]
  events += [('message_delta',{'type':'message_delta','delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':5}}),('message_stop',{'type':'message_stop'})]
  self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('connection','close');self.end_headers()
  try:
   for k,v in events:self.wfile.write(('event: '+k+'\ndata: '+json.dumps(v)+'\n\n').encode());self.wfile.flush();time.sleep(.02)
  except OSError:pass
  self.close_connection=True
GATE,SENT=port(),port();SERVERS=[]
for p,h in ((GATE,Fake),(SENT,Sentinel)):
 s=ThreadingHTTPServer(('127.0.0.1',p),h);threading.Thread(target=s.serve_forever,daemon=True).start();SERVERS.append(s)
# Source imports occur only after all inherited live routing state is removed.
ROUTER=port()
BASE=os.environ.copy()
BASE.update({'HOME':str(HOME),'USERPROFILE':str(HOME),'APPDATA':str(HOME/'AppData/Roaming'),'LOCALAPPDATA':str(HOME/'AppData/Local'),'TEMP':str(HOME/'AppData/Local/Temp'),'TMP':str(HOME/'AppData/Local/Temp'),'XDG_CONFIG_HOME':str(HOME/'.config'),'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_ROUTER_PORT':str(ROUTER),'CLAUDE_CONFIG_DIR':str(CCFG),'CODEX_HOME':str(RUN/'codex'),'HTTP_PROXY':f'http://127.0.0.1:{SENT}','HTTPS_PROXY':f'http://127.0.0.1:{SENT}','ALL_PROXY':f'http://127.0.0.1:{SENT}','NO_PROXY':'127.0.0.1,localhost,::1','DISABLE_AUTOUPDATER':'1','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_TELEMETRY':'1','CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS':'1'})
os.environ.clear();os.environ.update(BASE)
os.chdir(WS);sys.path.insert(0,str(REPO))
import ciel_runtime as cr
from ciel_runtime_support.launch_data_path import LaunchDataPath
pcfg={'base_url':f'http://127.0.0.1:{GATE}','api_key':'e2e-key','current_model':MODEL_A,'custom_models':[MODEL_A,MODEL_B,HAIKU,SONNET],'context_window':128000,'max_output_tokens':4096}
if CASE!='old':pcfg['subagent_model']=HAIKU
cfg={'current_provider':'cielairouter','language':'en','providers':{'cielairouter':pcfg}}
(CFG/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
(CCFG/'.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True,'lastOnboardingVersion':'2.1.310','numStartups':3,'bypassPermissionsModeAccepted':True,'projects':{k:{'hasTrustDialogAccepted':True,'allowedTools':[]} for k in (str(WS),WS.as_posix(),str(WS).lower(),WS.as_posix().lower())}}),encoding='utf-8')
(WS/'.claude/agents').mkdir(parents=True)
(WS/'.claude/agents/haiku-def.md').write_text('---\nname: haiku-def\ndescription: Synthetic E2E definition\nmodel: haiku\n---\nReturn the provided marker.\n',encoding='utf-8')
direct=CASE not in ('routed','routedswitch')
path=LaunchDataPath(configured='direct',active='direct',endpoint=f'http://127.0.0.1:{GATE}',api_key='e2e-key',model=MODEL_A) if direct else None
cr.write_model_list_cache('cielairouter',pcfg,[MODEL_A,MODEL_B,HAIKU,SONNET])
projection=cr.env_vars(cfg,data_path=path)
if CASE=='old':
 projection['CLAUDE_CODE_SUBAGENT_MODEL']=projection['ANTHROPIC_MODEL'];projection.pop('CLAUDE_CODE_SUBAGENT_MODEL_FORCE',None)
ENV={**BASE,**projection}
keep=lambda d:{k:v for k,v in d.items() if ('MODEL' in k or k=='ANTHROPIC_BASE_URL') and 'TOKEN' not in k}
dump('projection.json',{'source':str(REPO/'ciel_runtime_support/claude_environment.py'),'case':CASE,'baseline_exact_old_env_only':CASE=='old','configured_subagent_model':pcfg.get('subagent_model'),'projection':keep(projection),'direct':direct,'stripped_names':STRIPPED})
BEFORE=health();dump('live-before.json',BEFORE)
router=None; owned=[]; results=[]
try:
 if not direct:
  router=subprocess.Popen([sys.executable,'-I','-c',"import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')",str(REPO)],env=BASE,cwd=WS,stdout=(RUN/'router-stdout.txt').open('wb'),stderr=(RUN/'router-stderr.txt').open('wb'))
  owned.append(router.pid);wait(lambda:http(f'http://127.0.0.1:{ROUTER}/health').get('ok'),40,'isolated router')
 tags=[] if CASE in ('switch','routedswitch') else ['plain','haiku','sonnet','definition','modelarg']
 for tag in tags:
  args=[str(CLI),'-p','E2EPARENT-'+tag,'--dangerously-skip-permissions','--output-format','json','--max-turns','6']
  if tag=='modelarg':args+=['--model',MODEL_B if direct else 'ciel-runtime-cielairouter-'+MODEL_B]
  log('case '+CASE+' tag '+tag)
  p=subprocess.Popen(args,env=ENV,cwd=WS,stdout=subprocess.PIPE,stderr=subprocess.PIPE);owned.append(p.pid)
  try:out,err=p.communicate(timeout=65)
  except subprocess.TimeoutExpired:
   subprocess.run(['taskkill','/T','/F','/PID',str(p.pid)],capture_output=True);out,err=p.communicate();results.append({'tag':tag,'timeout':True})
  facts={'tag':tag,'rc':p.returncode,'stdout_bytes':len(out),'stderr_bytes':len(err),'child_ok':('CHILD-OK-'+tag).encode() in out,'parent_ok':('PARENT-OK-'+tag).encode() in out}
  try:
   o=json.loads(out);facts.update({k:o[k] for k in ('type','subtype','is_error','num_turns','result') if k in o})
  except Exception:facts['non_json_tail']=out.decode('utf-8','replace')[-500:];facts['stderr']=err.decode('utf-8','replace')[-500:]
  results.append(facts);dump('print-results.json',results)
 # Interactive named teammate plus /model followed by a fresh named child.
 if CASE in ('fixed','old','routed','switch','routedswitch'):
  launch=RUN/'interactive.cmd'
  lines=['@echo off']
  for prefix in PREFIXES:
   lines.append('for /f "delims==" %%v in ('+chr(39)+'set '+prefix+' 2^>nul'+chr(39)+') do set "%%v="')
  lines += ['set "'+k+'='+str(v)+'"' for k,v in ENV.items() if k.upper().startswith(PREFIXES) or k in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','XDG_CONFIG_HOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','DISABLE_AUTOUPDATER','DISABLE_TELEMETRY')]
  lines += ['cd /d "'+str(WS)+'"','"'+str(CLI)+'" --dangerously-skip-permissions --settings "{'+chr(92)+'"teammateMode'+chr(92)+'":'+chr(92)+'"in-process'+chr(92)+'"}"']
  launch.write_text(chr(10).join(lines)+chr(10),encoding='utf-8')
  subprocess.Popen(['wt.exe','-w','new','--title',TITLE,'--suppressApplicationTitle','cmd','/k',str(launch)])
  cmdpid=0
  def findcmd():
   global cmdpid
   for x in procs():
    if str(x.get('Name','')).lower()=='cmd.exe' and str(launch) in (x.get('CommandLine') or ''):cmdpid=x['ProcessId'];return True
   return False
  wait(findcmd,25,'interactive owned cmd')
  if cmdpid:
   owned.append(cmdpid)
   agent=subprocess.Popen([sys.executable,'-I',str(HERE/'console_agent.py'),str(cmdpid),str(RUN)],creationflags=8);owned.append(agent.pid)
   def screen():
    try:return (RUN/'screen-latest.txt').read_text(encoding='utf-8')
    except OSError:return ''
   def command(text):
    (RUN/'command-sent.json').unlink(missing_ok=True);(RUN/'console-command.tmp').write_text(json.dumps({'text':text}),encoding='utf-8');(RUN/'console-command.tmp').replace(RUN/'console-command.json');wait(lambda:(RUN/'command-sent.json').exists(),10,'console command')
   wait(lambda:'? for shortcuts' in screen() or 'shift+tab' in screen(),50,'interactive ready');time.sleep(3)
   command('E2EPARENT-named')
   wait(lambda:any(r['role']=='child' and r['tag']=='named' for r in ROWS),65,'named child request')
   time.sleep(4)
   shot=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HELPERS/'shot_title.ps1'),TITLE,str(RUN/'named.png')],capture_output=True,text=True)
   (RUN/'screenshot-log.txt').write_text(shot.stdout+shot.stderr)
   command('/model '+(MODEL_B if direct else 'ciel-runtime-cielairouter-'+MODEL_B));time.sleep(12)
   command('E2EPARENT-named-switched');time.sleep(12)
   if not any(r['role']=='child' and r['tag']=='named-switched' for r in ROWS):command('E2EPARENT-named-switched')
   wait(lambda:any(r['role']=='child' and r['tag']=='named-switched' for r in ROWS),60,'switched named child request')
   time.sleep(3)
   shot=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HELPERS/'shot_title.ps1'),TITLE,str(RUN/'named-switched.png')],capture_output=True,text=True)
   with (RUN/'screenshot-log.txt').open('a') as f:f.write(shot.stdout+shot.stderr)
   (RUN/'stop-console').write_text('stop');time.sleep(.6)
   for pid in (cmdpid,agent.pid):subprocess.run(['taskkill','/T','/F','/PID',str(pid)],capture_output=True)
finally:
 if router and router.poll() is None:subprocess.run(['taskkill','/T','/F','/PID',str(router.pid)],capture_output=True)
 for s in SERVERS:s.shutdown()
 AFTER=health();dump('live-after.json',AFTER)
 dump('result.json',{'case':CASE,'cli_version':subprocess.check_output([str(CLI),'--version'],env=ENV,text=True).strip(),'requests':ROWS,'print_results':results,'egress':EGRESS,'owned_pids':owned,'live_pid_unchanged':all(BEFORE[k].get('pid') and BEFORE[k].get('pid')==AFTER[k].get('pid') for k in BEFORE),'inherit_on_wire':any(r.get('model')=='inherit' for r in ROWS)})
 log('evidence '+str(RUN))
