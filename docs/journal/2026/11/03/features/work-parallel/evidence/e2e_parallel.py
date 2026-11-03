"""Owned isolated packaged-mod E2E using actual Python dispatcher."""
import sys, json
import tempfile
from pathlib import Path
PACKAGE=Path(__file__).resolve().parents[8]
# Reuse the proven loopback SSE transport, replacing its setup and scenario.
source=(Path(__file__).parent/'run_fork_probe.py').read_text(encoding='utf-8')
source=source.replace('fork-probe-', 'parallel-e2e-')
source=source.replace('WS = RUN / "workspace"; WS.mkdir()', 'WS = Path(tempfile.mkdtemp(prefix="wp-"))')
source=source.replace('ROWS=[]; COUNTS={}', r"""
for argv in [('init',), ('config','user.name','fixture'), ('config','user.email','fixture@example.invalid')]:
 subprocess.run(['git','-C',str(WS),*argv],check=True,capture_output=True)
(WS/'fixture.txt').write_text('owned fixture')
subprocess.run(['git','-C',str(WS),'add','fixture.txt'],check=True,capture_output=True)
subprocess.run(['git','-C',str(WS),'commit','-m','owned fixture'],check=True,capture_output=True)
SESSION=str(uuid.uuid4()); IDENTITY='claude:'+SESSION
TOKEN=uuid.uuid4().hex+uuid.uuid4().hex
HOME=RUN/'home';HOME.mkdir()
removed=[k for k in os.environ if k.upper().startswith(('CIEL_RUNTIME_','CLAUDE','ANTHROPIC','CODEX','OPENAI'))]
for k in removed:os.environ.pop(k,None)
os.environ.update(HOME=str(HOME),USERPROFILE=str(HOME),APPDATA=str(HOME/'AppData/Roaming'),LOCALAPPDATA=str(HOME/'AppData/Local'),CIEL_RUNTIME_CONFIG_DIR=str(RUN/'runtime-cfg'))
sys.path.insert(0,str(PACKAGE))
from ciel_runtime_support import runtime_paths, work_parallel_mcp
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool,work_tool_schemas
from ciel_runtime_support.work_plan_store import session_work_plan_store
assert runtime_paths.CONFIG_DIR.resolve()==(RUN/'runtime-cfg').resolve()
CONFIG={'work_mode':True,'work_auto_continue':False,'work_parallel_host':{'token':TOKEN,'capability_ready':True}}
MCP_ROWS=[]
def respond(i,text,error=False):return {'content':[{'type':'text','text':text}],'isError':error}
def dispatch(name,args):
 if args.get('session_id')!=IDENTITY:
  out=respond(None,'exact session mismatch',True)
  MCP_ROWS.append({'name':name,'args':args,'result':out,'rejected_before_dispatch':True})
  (RUN/'mcp.json').write_text(json.dumps(MCP_ROWS,indent=2),encoding='utf-8')
  return out
 out=dispatch_work_tool(None,name,args,load_config=lambda:CONFIG,state_dir=RUN/'state',respond=respond,save_config=lambda d:CONFIG.update(d))
 safe=json.loads(json.dumps(args))
 if '_host' in safe:safe['_host']['token']='[redacted]'
 MCP_ROWS.append({'name':name,'args':safe,'result':out})
 (RUN/'mcp.json').write_text(json.dumps(MCP_ROWS,indent=2),encoding='utf-8')
 return out
dispatch('work_plan_set',{'session_id':IDENTITY,'goal':'Owned fixture','children':[{'title':'Phase','children':[{'title':'Proof A'},{'title':'Proof B'}]}]})
dispatch('work_execute',{'session_id':IDENTITY,'user_confirmed':True})
board=session_work_plan_store(RUN/'state',IDENTITY).read()
(RUN/'board.json').write_text(json.dumps(board,indent=2))
ITEMS=[n['id'] for n in board['nodes'] if n['title'].startswith('Proof')]
assert board['phase']=='executing' and len(ITEMS)==2
assert not work_parallel_mcp.ledger().read()['policy']['enabled']
TOOLS=work_tool_schemas();known={t['name'] for t in TOOLS}
TOOLS += [t for t in work_parallel_mcp.schemas() if t['name'] not in known]
class MCP(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.send_error(405)
 def do_POST(self):
  b=json.loads(self.rfile.read(int(self.headers.get('content-length',0))));i=b.get('id');m=b.get('method')
  if i is None:self.send_response(202);self.end_headers();return
  if m=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'owned-dispatcher','version':'1'}}
  elif m=='tools/list':r={'tools':TOOLS}
  elif m=='tools/call':r=dispatch(b['params']['name'],b['params'].get('arguments',{}))
  else:r={}
  raw=json.dumps({'jsonrpc':'2.0','id':i,'result':r}).encode();self.send_response(200);self.send_header('content-type','application/json');self.send_header('content-length',str(len(raw)));self.end_headers();self.wfile.write(raw)
mcpserver=ThreadingHTTPServer(('127.0.0.1',0),MCP);threading.Thread(target=mcpserver.serve_forever,daemon=True).start()
PLUGIN=PACKAGE/'ciel_runtime_support/claude_mods/ciel-work'
ROWS=[]; COUNTS={}
""")
source=source.replace("child='CHILD-FORK-ONLY' in encoded", "child=any(m.get('role')=='user' and any(x.get('type')=='text' and 'Execute only approved Work item' in x.get('text','') for x in (m.get('content') if isinstance(m.get('content'),list) else [])) for m in messages)")
a=source.index("        if tag=='main' and n==0:")
b=source.index("        msg=",a)
source=source[:a]+r"""        (RUN/('request-'+str(len(ROWS))+'.json')).write_text(json.dumps(b,indent=2),encoding='utf-8')
        state=work_parallel_mcp.ledger().read();v=state['version']
        def tool(name,args):return {'type':'tool_use','id':'toolu_'+uuid.uuid4().hex[:12],'name':'mcp__ciel-runtime-router__'+name,'input':args}
        if child:
            lease=next(iter(state['leases'].values()),None)
            if n==0:c=tool('work_parallel_status',{})
            elif n==1 and lease:c=tool('work_parallel_event',{'event':'result','lease_id':lease['id'],'plan_version':lease['plan_version'],'expected_version':v,'artifact':'a'*64,'evidence':'owned child result'})
            else:c={'type':'text','text':'OWNED-CHILD-RESULT'}
        elif n==0:c=tool('work_parallel_configure',{'changes':{'enabled':True,'max_active':1,'p1_reserved':1},'expected_version':v})
        elif n in (1,3):c=tool('work_parallel_run',{'item_id':ITEMS[0 if n==1 else 1],'priority':'P1','role':'worker','expected_version':v})
        elif n in (2,4):
            tickets=[]
            for message in messages:
                if not isinstance(message.get('content'),list):continue
                for block in message['content']:
                    if block.get('type')!='tool_result':continue
                    try:
                        value=json.loads(block.get('content',''))
                        if value.get('dispatch')=='native_agent_required':tickets.append(value)
                    except (ValueError,TypeError):pass
            ticket=tickets[-1]
            c={'type':'tool_use','id':'toolu_'+uuid.uuid4().hex[:12],'name':'Agent','input':{k:ticket[k] for k in ('subagent_type','name','description','prompt')}}
        else:c={'type':'text','text':'OWNED-MAIN-END'}
        reason='tool_use' if c['type']=='tool_use' else 'end_turn'
"""+source[b:]
source=source.replace("env.update({", "env.update({'HOME':str(HOME),'USERPROFILE':str(HOME),'CIEL_RUNTIME_WORK_PARALLEL_HOST_TOKEN':TOKEN,")
source=source.replace("cli,'-p'", "cli,'--session-id',SESSION,'--debug-file',str(RUN/'cli-debug.log'),'-p'")
source=source.replace("'PROBE-MAIN: spawn the requested fork then reply.'", "'User requests enabling Work parallel cap 1; run both approved fixtures. Do not edit files.'")
source=source.replace(repr(json.dumps({"mcpServers":{}},separators=(",",":"))), "json.dumps({'mcpServers':{'ciel-runtime-router':{'type':'http','url':'http://127.0.0.1:'+str(mcpserver.server_port)+'/mcp'}}})")
source=source.replace("finally:server.shutdown()", "finally:server.shutdown();mcpserver.shutdown()")
exec(compile(source,str(__file__),'exec'))

# Persist the actual integration verdict, not the CLI's exit-code-only success.
state=work_parallel_mcp.ledger().read()
(RUN/'ledger-after.json').write_text(json.dumps(state,indent=2),encoding='utf-8')
checks={'default_off':True,'configure':False,'reserve':False,'bind':False,'child_result':False,'hard_cap_rejection':False}
for row in MCP_ROWS:
 if row.get('rejected_before_dispatch'):continue
 if row['name']=='work_parallel_configure' and not row['result']['isError']:checks['configure']=True
 for e,key in [('reserve','reserve'),('bind','bind'),('result','child_result')]:
  if row['args'].get('event')==e and not row['result']['isError']:checks[key]=True
 if row['args'].get('event')=='reserve' and row['result']['isError'] and 'cap' in json.dumps(row['result']).lower():checks['hard_cap_rejection']=True
errors=[line for line in (RUN/'cli-debug.log').read_text(encoding='utf-8').splitlines() if 'hooks module' in line and ('failed' in line or 'did not load' in line)]
(RUN/'verification.json').write_text(json.dumps({'success':all(checks.values()),'checks':checks,'session':IDENTITY,'removed_env_names':removed,'config_dir':str(runtime_paths.CONFIG_DIR),'errors':errors,'native_spawn':result['spawn'],'api_endpoint':'loopback fake gateway only'},indent=2),encoding='utf-8')
print(json.dumps({'checks':checks,'errors':errors},indent=2))
