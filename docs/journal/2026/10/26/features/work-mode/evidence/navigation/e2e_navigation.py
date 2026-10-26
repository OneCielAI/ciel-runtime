"""Real CLI, owned loopback gateway/router, isolated environment. No product patches."""
import json,os,re,socket,subprocess,sys,threading,time,uuid,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
HERE=Path(__file__).resolve().parent
SOURCE=next(p for p in HERE.parents if (p/'ciel_runtime.py').exists())
REPO=Path(os.environ.get('WORK_PROOF_REPO') or SOURCE)
OLD=SOURCE/'docs/journal/2026/10/25/fixes/teammate-model-inheritance/evidence'
HELP=SOURCE/'docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e'
RUN=HERE/('run-'+uuid.uuid4().hex[:8]);RUN.mkdir()
PREFIXES=('CIEL_RUNTIME_','CLAUDE','ANTHROPIC','CODEX','OPENAI')
STRIPPED=sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
for k in STRIPPED:os.environ.pop(k)
HOME,CFG,CCFG,WS=[RUN/x for x in ('home','cfg','ccfg','ws')]
for d in (HOME/'AppData/Roaming',HOME/'AppData/Local/Temp',CFG,CCFG,WS):d.mkdir(parents=True,exist_ok=True)
TARGET=WS/'subject.txt';TARGET.write_text('BEFORE\n',encoding='utf-8')
CLI=Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
ROWS=[];EGRESS=[];STATE={};OWNED=[];ACTIVE=[];RESULT={};LEAF=None;DEADLINE=0
TITLE='CIELE2E-WORK-'+RUN.name

def dump(n,o):(RUN/n).write_text(json.dumps(o,ensure_ascii=False,indent=1),encoding='utf-8')
def log(s):
 print(s,flush=True)
 with (RUN/'driver.txt').open('a',encoding='utf-8') as f:f.write(s+'\n')
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def http(url,body=None):
 try:
  req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json'})
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=3) as r:return json.loads(r.read())
 except Exception as e:return {'error':str(e)}
def health():return {str(p):http(f'http://127.0.0.1:{p}/health') for p in (9385,9464,9489,9505,9524)}
def ps(s):return subprocess.run(['powershell','-NoProfile','-Command',s],capture_output=True,text=True).stdout
def procs():
 try:return json.loads(ps('Get-CimInstance Win32_Process | Select ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress'))
 except Exception:return []
def wait(pred,secs,label):
 end=min(time.time()+secs,DEADLINE or time.time()+secs)
 while time.time()<end:
  if pred():log(label+': yes');return True
  time.sleep(.25)
 log(label+': NO');return False
def mcp(name,args):return http(f'http://127.0.0.1:{ROUTER}/ca/mcp',{'jsonrpc':'2.0','id':uuid.uuid4().hex,'method':'tools/call','params':{'name':name,'arguments':args}})
def snap(label):
 r=mcp('work_status',{'format':'json','session_id':'claude:'+SESSION});dump('snapshot-'+label+'.json',r)
 try:return json.loads(r['result']['content'][0]['text'])
 except Exception:return {}
def stop(pid):
 if pid not in OWNED:raise RuntimeError('Unowned termination refused '+str(pid))
 r=subprocess.run(['taskkill','/T','/F','/PID',str(pid)],capture_output=True,text=True);log('owned stop '+str(pid)+' '+r.stdout.strip())
class Sentinel(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def deny(self):
  EGRESS.append({'method':self.command,'path':self.path});self.send_response(403);self.send_header('content-length','0');self.end_headers()
 do_CONNECT=do_GET=do_POST=do_HEAD=deny
class Fake(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def sendj(self,o):
  b=json.dumps(o).encode();self.send_response(200);self.send_header('content-type','application/json');self.send_header('content-length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):return self.sendj({'object':'list','data':[{'id':'fake-work','object':'model','context_window':128000,'max_tokens':4096,'capabilities':{'tool_calling':True}}]})
 def do_POST(self):
  global LEAF
  b=json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or '{}')
  if 'count_tokens' in self.path:return self.sendj({'input_tokens':100})
  tools={t.get('name'):t for t in b.get('tools') or [] if isinstance(t,dict)}
  users=[m for m in b.get('messages',[]) if m.get('role')=='user']
  texts=' '.join(m['content'] if isinstance(m.get('content'),str) else ' '.join(x.get('text','') for x in m.get('content',[]) if isinstance(x,dict) and x.get('type')=='text') for m in users)
  ask_tags=re.findall(r'E2EWORK-ASK-(deny|approve)',texts)
  tag='ask-'+ask_tags[-1] if ask_tags else 'continue' if 'E2EWORK-CONTINUE' in texts else 'main' if 'E2EWORK-MAIN' in texts else 'aux'
  n=STATE.get(tag,0);STATE[tag]=n+1;number=len(ROWS)+1
  dump(f'request-{number:02}.json',b)
  prior=[x for m in users if isinstance(m.get('content'),list) for x in m['content'] if isinstance(x,dict) and x.get('type')=='tool_result']
  row={'n':number,'tag':tag,'stage':n,'path':self.path,'model':b.get('model'),'tool_names':list(tools),'work_instructions':'Ciel Work mode is on.' in json.dumps(b),'prior_results':prior[-2:]}
  content=[{'type':'text','text':'E2EWORK-AUX-OK'}];reason='end_turn'
  def tool(name,inp):
   nonlocal content,reason
   matches=[k for k in tools if k==name or k.endswith('__'+name)]
   if len(matches)!=1:
    row['schema_error']={'requested':name,'matches':matches};content=[{'type':'text','text':'E2EWORK-SCHEMA-MISSING-'+name}];return
   actual=matches[0];row['served_tool']={'name':actual,'input':inp};content=[{'type':'tool_use','id':f'toolu_w{number}','name':actual,'input':inp}];reason='tool_use'
  if number>25:content=[{'type':'text','text':'E2EWORK-CALL-BOUND-REACHED'}]
  elif tag=='main':
   if n==0:tool('Read',{'file_path':str(TARGET)})
   elif n==1:tool('Edit',{'file_path':str(TARGET),'old_string':'BEFORE','new_string':'ILLEGAL-PLANNING'})
   elif n==2:tool('Bash',{'command':'printf SHELL-PLANNING > shell-planning.txt','description':'Probe Work planning shell guard'})
   elif n==3:tool('work_plan_set',{'goal':'Verify real Work mode','children':[{'title':'Exercise guarded mutation','children':[{'title':'Edit and verify subject','children':[{'title':'Apply verified edit'},{'title':'Continue persisted plan'}]}]}, {'title':'B booking calendar','note':'Capacity and FIFO policies','children':[{'title':'Verify half-open intervals'}]}, {'title':'C inventory stream','note':'First accepted event wins','children':[{'title':'Verify stock and undo'}]}]})
   elif n==4:tool('work_execute',{})
   elif n==5:tool('Edit',{'file_path':str(TARGET),'old_string':'BEFORE','new_string':'AFTER'})
   elif n==6:tool('work_status',{'format':'json'})
   elif n==7:
    for p in CFG.rglob('work-plan.json'):
     d=json.loads(p.read_text());leafs=[v for v in d.get('nodes',[]) if v.get('title')=='Apply verified edit'];LEAF=leafs[0]['id'] if leafs else None
    if LEAF:tool('work_node_update',{'id':LEAF,'status':'in_progress'})
    else:content=[{'type':'text','text':'E2EWORK-LEAF-NOT-FOUND'}]
   elif n==8:tool('Bash',{'command':'cat subject.txt','description':'Verify edited file contents'})
   elif n==9:tool('work_node_update',{'id':LEAF,'status':'done','evidence':'Actual Edit returned success and Bash cat subject.txt returned AFTER.'})
   else:content=[{'type':'text','text':'E2EWORK-MAIN-DONE'}]
  elif tag.startswith('ask-'):
   if n==0:tool('work_execute',{})
   else:content=[{'type':'text','text':'E2EWORK-'+tag.upper()+'-DONE'}]
  elif tag=='continue':
   if n==0:tool('work_status',{'format':'json'})
   else:content=[{'type':'text','text':'E2EWORK-CONTINUE-DONE'}]
  row['response']=content;ROWS.append(row);dump('requests-summary.json',ROWS)
  msg={'id':'msg_'+uuid.uuid4().hex[:10],'type':'message','role':'assistant','model':b.get('model'),'content':content,'stop_reason':reason,'stop_sequence':None,'usage':{'input_tokens':100,'output_tokens':10}}
  if not b.get('stream'):return self.sendj(msg)
  events=[('message_start',{'type':'message_start','message':{**msg,'content':[],'stop_reason':None}})]
  for i,c in enumerate(content):
   empty={'type':'tool_use','id':c['id'],'name':c['name'],'input':{}} if c['type']=='tool_use' else {'type':'text','text':''}
   delta={'type':'input_json_delta','partial_json':json.dumps(c['input'])} if c['type']=='tool_use' else {'type':'text_delta','text':c['text']}
   events.extend([('content_block_start',{'type':'content_block_start','index':i,'content_block':empty}),('content_block_delta',{'type':'content_block_delta','index':i,'delta':delta}),('content_block_stop',{'type':'content_block_stop','index':i})])
  events.extend([('message_delta',{'type':'message_delta','delta':{'stop_reason':reason,'stop_sequence':None},'usage':{'output_tokens':10}}),('message_stop',{'type':'message_stop'})])
  self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('connection','close');self.end_headers()
  try:
   for k,v in events:self.wfile.write(('event: '+k+'\ndata: '+json.dumps(v)+'\n\n').encode());self.wfile.flush();time.sleep(.025)
  except OSError:pass
  self.close_connection=True

import tempfile,shutil
SESSION=str(uuid.uuid4())
GATE,SENT,ROUTER=port(),port(),port()
SERVERS=[]
for number,handler in ((GATE,Fake),(SENT,Sentinel)):
 server=ThreadingHTTPServer(('127.0.0.1',number),handler)
 threading.Thread(target=server.serve_forever,daemon=True).start();SERVERS.append(server)
BASE=os.environ.copy()
BASE.update({'HOME':str(HOME),'USERPROFILE':str(HOME),'APPDATA':str(HOME/'AppData/Roaming'),'LOCALAPPDATA':str(HOME/'AppData/Local'),'TEMP':str(HOME/'AppData/Local/Temp'),'TMP':str(HOME/'AppData/Local/Temp'),'XDG_CONFIG_HOME':str(HOME/'.config'),'CIEL_RUNTIME_CONFIG_DIR':str(CFG),'CIEL_RUNTIME_TEST_ISOLATED':'1','CIEL_RUNTIME_ROUTER_PORT':str(ROUTER),'CLAUDE_CONFIG_DIR':str(CCFG),'CODEX_HOME':str(RUN/'codex'),'HTTP_PROXY':f'http://127.0.0.1:{SENT}','HTTPS_PROXY':f'http://127.0.0.1:{SENT}','ALL_PROXY':f'http://127.0.0.1:{SENT}','NO_PROXY':'127.0.0.1,localhost,::1','DISABLE_AUTOUPDATER':'1','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_TELEMETRY':'1','ENABLE_TOOL_SEARCH':'false'})
os.environ.clear();os.environ.update(BASE)
CLI=Path(shutil.which('claude') or str(CLI))
# Prefer native executable; cmd wrappers can reparse JSON.
if CLI.suffix.lower()=='.cmd':
 native=Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe')
 if native.exists():CLI=native
pcfg={'base_url':f'http://127.0.0.1:{GATE}','api_key':'e2e-key','current_model':'fake-work','context_window':128000,'max_output_tokens':4096}
cfg={'current_provider':'cielairouter','language':'en','work_mode':True,'providers':{'cielairouter':pcfg}}
(CFG/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
(CCFG/'.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True,'lastOnboardingVersion':'2.1.310','numStartups':3,'bypassPermissionsModeAccepted':True,'projects':{k:{'hasTrustDialogAccepted':True,'allowedTools':[]} for k in (str(WS),WS.as_posix(),str(WS).lower(),WS.as_posix().lower())}}),encoding='utf-8')
ENV={**BASE,'CIEL_RUNTIME_WORKSPACE_STATE_DIR':str(CFG),'ANTHROPIC_BASE_URL':f'http://127.0.0.1:{GATE}','ANTHROPIC_API_KEY':'','ANTHROPIC_AUTH_TOKEN':'e2e-key','ANTHROPIC_MODEL':'fake-work','ANTHROPIC_DEFAULT_SONNET_MODEL':'fake-work','ANTHROPIC_DEFAULT_HAIKU_MODEL':'fake-work','ANTHROPIC_DEFAULT_OPUS_MODEL':'fake-work'}
MCP=RUN/'mcp.json';MCP.write_text(json.dumps({'mcpServers':{'ciel-runtime-router':{'type':'http','url':f'http://127.0.0.1:{ROUTER}/ca/mcp'}}}),encoding='utf-8')
MOD=REPO/'ciel_runtime_support/claude_mods/ciel-work'
sys.path.insert(0,str(REPO))
from ciel_runtime_support.statusline_script import STATUSLINE_SCRIPT
STATUS_SCRIPT=RUN/'statusline.py';STATUS_SCRIPT.write_text(STATUSLINE_SCRIPT,encoding='utf-8')
SETTINGS=RUN/'settings.json';SETTINGS.write_text(json.dumps({'statusLine':{'type':'command','command':subprocess.list2cmdline([sys.executable,str(STATUS_SCRIPT)])}}),encoding='utf-8')
BEFORE=health();dump('live-before.json',BEFORE)
router=None
try:
 router=subprocess.Popen([sys.executable,'-I','-c',"import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[1]+'/ciel_runtime.py','serve'];runpy.run_path(sys.argv[0],run_name='__main__')",str(REPO)],env=BASE,cwd=WS,stdout=(RUN/'router-out.txt').open('wb'),stderr=subprocess.STDOUT);OWNED.append(router.pid)
 if not wait(lambda:http(f'http://127.0.0.1:{ROUTER}/health').get('ok'),40,'owned router'):raise RuntimeError('router unavailable')
 common=[str(CLI),'--settings',str(SETTINGS),'--dangerously-skip-permissions','--plugin-dir',str(MOD),'--mcp-config',str(MCP),'--strict-mcp-config']
 p=subprocess.Popen([*common,'--session-id',SESSION,'-p','E2EWORK-MAIN verify planning guard then execute.','--output-format','json','--max-turns','16'],env=ENV,cwd=WS,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE);OWNED.append(p.pid)
 try:out,err=p.communicate(timeout=130)
 except subprocess.TimeoutExpired:stop(p.pid);out,err=p.communicate();RESULT['print_timeout']=True
 (RUN/'print-out.json').write_bytes(out);(RUN/'print-err.txt').write_bytes(err)
 RESULT.update(print_exit=p.returncode,target=TARGET.read_text(),shell_planning_file=(WS/'shell-planning.txt').exists(),session_id=SESSION)
 RESULT['main_snapshot']=snap('main')
 p=subprocess.Popen([*common,'--resume',SESSION,'-p','E2EWORK-CONTINUE verify persisted plan.','--output-format','json','--max-turns','3'],env=ENV,cwd=WS,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE);OWNED.append(p.pid)
 try:out,err=p.communicate(timeout=55)
 except subprocess.TimeoutExpired:stop(p.pid);out,err=p.communicate();RESULT['resume_timeout']=True
 (RUN/'resume-out.json').write_bytes(out);(RUN/'resume-err.txt').write_bytes(err);RESULT['resume_exit']=p.returncode;RESULT['resume_snapshot']=snap('resume')

 # Exercise the real interactive approval dialog against the persisted conversation.
 cfg['work_plan_approval_ask']=True;(CFG/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
 mcp('work_replan',{'session_id':'claude:'+SESSION})
 launch=RUN/'interactive.cmd'
 lines=['@echo off']
 for prefix in PREFIXES:lines.append('for /f "delims==" %%v in ('+chr(39)+'set '+prefix+' 2^>nul'+chr(39)+') do set "%%v="')
 lines += ['set "'+k+'='+str(v)+'"' for k,v in ENV.items() if k.upper().startswith(PREFIXES) or k in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP','XDG_CONFIG_HOME','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','DISABLE_AUTOUPDATER','DISABLE_TELEMETRY','ENABLE_TOOL_SEARCH')]
 lines += ['cd /d "'+str(WS)+'"',subprocess.list2cmdline([*common,'--resume',SESSION])]
 launch.write_text(chr(10).join(lines)+chr(10),encoding='utf-8')
 subprocess.Popen(['wt.exe','-w','new','--title',TITLE,'--suppressApplicationTitle','cmd','/k',str(launch)])
 cmdpid=0
 def findcmd():
  global cmdpid
  for x in procs():
   if str(x.get('Name','')).lower()=='cmd.exe' and str(launch) in (x.get('CommandLine') or ''):cmdpid=x['ProcessId'];return True
  return False
 if not wait(findcmd,25,'interactive owned cmd'):raise RuntimeError('no owned console')
 OWNED.append(cmdpid)
 agent=subprocess.Popen([sys.executable,'-I',str(HERE/'console_agent.py'),str(cmdpid),str(RUN)],creationflags=8);OWNED.append(agent.pid)
 def screen():
  try:return (RUN/'screen-latest.txt').read_text(encoding='utf-8')
  except OSError:return ''
 def command(text=None,key=None):
  q={'key':key} if key else {'text':text}
  (RUN/'command-sent.json').unlink(missing_ok=True)
  (RUN/'console-command.tmp').write_text(json.dumps(q),encoding='utf-8');(RUN/'console-command.tmp').replace(RUN/'console-command.json')
  if not wait(lambda:(RUN/'command-sent.json').exists(),10,'console command'):raise RuntimeError('console not responding')
 def screenshot(label):
  (RUN/('screen-'+label+'.txt')).write_text(screen(),encoding='utf-8')
  shot=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(HELP/'shot_title.ps1'),TITLE,str(RUN/(label+'.png'))],capture_output=True,text=True,timeout=30)
  with (RUN/'screenshot-log.txt').open('a',encoding='utf-8') as f:f.write(label+chr(10)+shot.stdout+shot.stderr)
 if not wait(lambda:'shift+tab' in screen() or '? for shortcuts' in screen(),45,'interactive ready'):raise RuntimeError('interactive not ready')
 command('/work-mode status')
 if not wait(lambda:'B booking calendar' in screen() and 'Ctrl+X Tab' in screen(),20,'vertical items ready'):raise RuntimeError('vertical list unavailable')
 screenshot('01-vertical-items')
 command(key='ctrl-x');command(key='tab');command(key='down');command(key='enter')
 if not wait(lambda:'Capacity and FIFO' in screen(),15,'keyboard B details'):
  screenshot('failed-keyboard-detail');raise RuntimeError('keyboard branch detail missing')
 screenshot('02-keyboard-B-detail')
 command(key='ctrl-x');command(key='tab')
 command(key='esc');command(key='esc')
 command('/work-mode view')
 if not wait(lambda:'Work details' in screen(),15,'view command'):raise RuntimeError('view inspector missing')
 screenshot('03-keyboard-tree')
 command(key='esc');command(key='esc')
 RESULT['keyboard_inspection']=True
 command('/work-mode tree');wait(lambda:'Continue persisted plan' in screen(),15,'tree command');screenshot('01-persisted-tree')
 command('E2EWORK-ASK-deny verify user approval refusal.')
 if not wait(lambda:'Approve this Work plan?' in screen(),35,'ask deny dialog'):raise RuntimeError('missing ask dialog')
 screenshot('02-approval-dialog-deny');command(key='down');command(key='enter')
 wait(lambda:any(x['tag']=='ask-deny' and x['stage']==1 for x in ROWS),30,'deny response')
 RESULT['denied_snapshot']=snap('denied');screenshot('03-denied-planning')
 command('E2EWORK-ASK-approve verify user approval acceptance.')
 if not wait(lambda:'Approve this Work plan?' in screen(),30,'ask approve dialog'):raise RuntimeError('missing second dialog')
 screenshot('04-approval-dialog-approve');command(key='enter')
 wait(lambda:any(x['tag']=='ask-approve' and x['stage']==1 for x in ROWS),30,'approve response')
 RESULT['approved_snapshot']=snap('approved');screenshot('05-approved-executing')
 (RUN/'stop-console').write_text('stop');time.sleep(.5)
 stop(cmdpid)
 if agent.poll() is None:stop(agent.pid)
 RESULT['work_instructions']=any(x['work_instructions'] for x in ROWS)
 RESULT['rows']=ROWS;RESULT['egress']=EGRESS
except Exception as e:
 RESULT['error']=type(e).__name__+': '+str(e)
 for pid in [x for x in OWNED if not router or x!=router.pid]:
  try:stop(pid)
  except Exception:pass
finally:
 if router and router.poll() is None:stop(router.pid)
 for server in SERVERS:server.shutdown()
 AFTER=health();dump('live-after.json',AFTER);RESULT['live_before']=BEFORE;RESULT['live_after']=AFTER
 dump('result.json',RESULT);log('evidence '+str(RUN))
 print(json.dumps({k:v for k,v in RESULT.items() if k not in ('rows','live_before','live_after')},indent=1))
