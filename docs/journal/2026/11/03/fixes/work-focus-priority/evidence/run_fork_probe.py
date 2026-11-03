"""Owned loopback Claude fork capability probe; no live router or provider."""
import json, os, subprocess, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parent
RUN = ROOT / ("fork-probe-" + uuid.uuid4().hex[:8]); RUN.mkdir()
CFG = RUN / "ccfg"; CFG.mkdir()
WS = RUN / "workspace"; WS.mkdir()
PLUGIN = RUN / "plugin"; (PLUGIN / ".claude-plugin").mkdir(parents=True); (PLUGIN / "hooks").mkdir()
(PLUGIN / ".claude-plugin/plugin.json").write_text(json.dumps({"name":"fork-probe","version":"0.1.0"}))
(PLUGIN / "hooks/hooks.json").write_text('{"modules":["./register.js"]}')
(PLUGIN / "hooks/register.js").write_text("export function register(on) { on('agent.spawn', async ($, e, next) => { await $.fs.write('spawn-observed.json', JSON.stringify({fork:e.fork,type:e.subagentType,parent:e.parentAgentId})); return next(e) }); }",encoding="utf-8")
ROWS=[]; COUNTS={}
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        b=json.loads(self.rfile.read(int(self.headers.get('content-length',0))))
        if 'count_tokens' in self.path:
            raw=json.dumps({'input_tokens':100}).encode();self.send_response(200);self.send_header('content-length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        messages=b.get('messages',[])
        encoded=json.dumps(messages)
        child='CHILD-FORK-ONLY' in encoded
        tag='child' if child else 'main';n=COUNTS.get(tag,0);COUNTS[tag]=n+1
        tools=[t.get('name') for t in b.get('tools',[])]
        ROWS.append({'tag':tag,'n':n,'tools':tools})
        if tag=='main' and n==0:
            c={'type':'tool_use','id':'toolu_probe','name':'Agent','input':{'subagent_type':'fork','description':'Owned fork probe','prompt':'CHILD-FORK-ONLY: reply FORK-PROBE-OK.'}};reason='tool_use'
        else:c={'type':'text','text':'FORK-PROBE-OK' if child else 'MAIN-PROBE-OK'};reason='end_turn'
        msg={'id':'msg_probe'+str(len(ROWS)),'type':'message','role':'assistant','model':b.get('model'),'content':[c],'stop_reason':reason,'usage':{'input_tokens':100,'output_tokens':10}}
        if not b.get('stream'):
            raw=json.dumps(msg).encode();self.send_response(200);self.send_header('content-length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        ev=[('message_start',{'type':'message_start','message':{**msg,'content':[],'stop_reason':None}})]
        empty={'type':'tool_use','id':c['id'],'name':c['name'],'input':{}} if reason=='tool_use' else {'type':'text','text':''}
        delta={'type':'input_json_delta','partial_json':json.dumps(c['input'])} if reason=='tool_use' else {'type':'text_delta','text':c['text']}
        ev += [('content_block_start',{'type':'content_block_start','index':0,'content_block':empty}),('content_block_delta',{'type':'content_block_delta','index':0,'delta':delta}),('content_block_stop',{'type':'content_block_stop','index':0}),('message_delta',{'type':'message_delta','delta':{'stop_reason':reason,'stop_sequence':None},'usage':{'output_tokens':10}}),('message_stop',{'type':'message_stop'})]
        self.send_response(200);self.send_header('content-type','text/event-stream');self.send_header('connection','close');self.end_headers()
        for name,data in ev:
            self.wfile.write(('event: '+name+chr(10)+'data: '+json.dumps(data)+chr(10)*2).encode());self.wfile.flush()
        self.close_connection=True
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
env={k:v for k,v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_','CLAUDE','ANTHROPIC','CODEX','OPENAI'))}
env.update({'CLAUDE_CONFIG_DIR':str(CFG),'ANTHROPIC_BASE_URL':'http://127.0.0.1:'+str(server.server_port),'ANTHROPIC_AUTH_TOKEN':'fixture','ANTHROPIC_API_KEY':'','ANTHROPIC_MODEL':'fixture','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1','ENABLE_TOOL_SEARCH':'false'})
cli='C:/Users/djlov/AppData/Roaming/npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'
try:
    r=subprocess.run([cli,'-p','PROBE-MAIN: spawn the requested fork then reply.','--dangerously-skip-permissions','--plugin-dir',str(PLUGIN),'--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--no-session-persistence','--output-format','stream-json','--verbose'],env=env,cwd=WS,capture_output=True,text=True,encoding='utf-8',timeout=90)
    (RUN/'stdout.jsonl').write_text(r.stdout,encoding='utf-8');(RUN/'stderr.txt').write_text(r.stderr,encoding='utf-8')
    result={'exit':r.returncode,'requests':ROWS,'spawn':json.loads((WS/'spawn-observed.json').read_text()) if (WS/'spawn-observed.json').exists() else None}
    (RUN/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result));print(str(RUN))
finally:server.shutdown()
