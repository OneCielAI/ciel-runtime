"""Synthetic loopback Anthropic fault fixture. Stores metadata, never incoming headers/bodies."""
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

REQS=[]
RUN=None
WORKSPACE=None
LOCK=threading.Lock()
ATTEMPTS={}
MARKERS=('OWNED-BASELINE','OWNED-TEXT-CLOSED','OWNED-TEXT-OPEN','OWNED-PARTIAL-TOOL','OWNED-COMPLETE-TOOL','OWNED-RECOVERY',
         'OWNED-TEXT-CHAIN','OWNED-TEXT-MANUAL','OWNED-MANUAL-FOLLOWUP')
# Production stream recovery input (claude_stream_recovery_runtime.RECOVERY_MARKER), not typed by the harness.
AUTO_MARKER='계속-응답중단복구'
AUTO_CASE='OWNED-AUTO-RECOVERY'
# Set when OWNED-TEXT-CHAIN faults: the next automatic continuation faults once more.
CHAIN_FAULT_NEXT_AUTO=False
# Run 202918 repeated one sentence 16 times; the runaway-output guard cut that
# stream before the fault, so the fault was never exercised. Every line differs.
WORDS=('amber','basalt','cedar','delta','ember','fjord','garnet','harbor','iris','juniper','kestrel','lagoon',
       'meadow','nickel','orchid','pewter','quartz','raven','saffron','tundra','umber','violet','willow','xenon',
       'yarrow','zephyr','alder','birch','cobalt','dune','estuary','flint','glacier','hazel','indigo','jasper')
FAIL_MODE='eof'

def varied(label,lines):
    out=[]
    for i in range(lines):
        words=' '.join(WORDS[(i*7+j*5)%len(WORDS)] for j in range(9))
        out.append(f'{label} line {i:02d} ({i*7919%10007}): {words}.')
    return ' '.join(out)+' '

def text(m):
    c=m.get('content')
    if isinstance(c,str):return c
    return ' '.join(str(x.get('text','')) for x in (c or []) if isinstance(x,dict))

def save(row):
    with LOCK:
        (RUN/'requests.json').write_text(json.dumps(REQS,indent=2),encoding='utf-8')

def blocks(messages,kind):
    return [b for m in messages for b in (m.get('content') if isinstance(m.get('content'),list) else []) if isinstance(b,dict) and b.get('type')==kind]

class Stub(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def out_json(self,obj):
        data=json.dumps(obj).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        self.out_json({'object':'list','data':[{'id':'owned-stream-model','object':'model','owned_by':'owned-fixture',
            'max_model_len':128000,'context_length':128000,'max_output_tokens':4096,
            'capabilities':{'tool_calling':True,'reasoning':True,'thinking':True,'supportsThinking':True}}]})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        msgs=body.get('messages') or []; users=[text(m) for m in msgs if m.get('role')=='user']
        latest=next((u for u in reversed(users) if any(m in u for m in MARKERS)),'')
        last=msgs[-1] if msgs else {}
        last_blocks=last.get('content') if isinstance(last.get('content'),list) else []
        tool_result_turn=last.get('role')=='user' and bool(last_blocks) and all(isinstance(b,dict) and b.get('type')=='tool_result' for b in last_blocks)
        # The cielairouter route can append its own user message after the prompt,
        # so the turn is every user message after the last assistant message.
        trailing=[]
        for m in reversed(msgs):
            if m.get('role')!='user':break
            trailing.append(text(m))
        last_text=' '.join(reversed(trailing))
        if AUTO_MARKER in last_text: case=AUTO_CASE
        elif tool_result_turn: case=next((m for m in MARKERS if m in latest),'side')
        else: case=next((m for m in MARKERS if m in last_text),'side')
        tools=sorted(str((t.get('function') or {}).get('name') or t.get('name')) for t in body.get('tools') or [])
        # Title/suggestion/helper queries do not end in a marker prompt or tool result, so they cannot consume a fault budget.
        main=bool(tools) and case!='side'
        with LOCK:
            n=len(REQS)+1
            if main: ATTEMPTS[case]=ATTEMPTS.get(case,0)+1
            attempt=ATTEMPTS.get(case,0)
            uses=blocks(msgs,'tool_use');results=blocks(msgs,'tool_result')
            row={'n':n,'at':time.time(),'path':self.path,'case':case,'main':main,'attempt':attempt,
                 'stream':bool(body.get('stream')),'tool_result_turn':tool_result_turn,'last_role':last.get('role'),'messages':len(msgs),'users':len(users),'tool_count':len(tools),
                 'latest_user_sha256':hashlib.sha256(latest.encode()).hexdigest(),'latest_user_len':len(latest),
                 'history_markers':[m for m in MARKERS if any(m in u for u in users)],
                 'assistant_message_ids':[m.get('id') for m in msgs if m.get('role')=='assistant' and m.get('id')],
                 'tool_uses':[{'id':t.get('id'),'name':t.get('name'),'input_keys':list((t.get('input') or {}).keys())} for t in uses],
                 'tool_results':[{'tool_use_id':t.get('tool_use_id'),'is_error':t.get('is_error'),
                                  'content_length':len(str(t.get('content',''))),
                                  'contains_owned_once':'OWNED-TOOL-EXECUTED-ONCE' in str(t.get('content',''))} for t in results],
                 'header_metadata':{k:self.headers.get(k) for k in ('x-claude-code-session-id','x-client-request-id','request-id','x-request-id') if self.headers.get(k)},
                 'response_message_id':f'msg_owned_{n}', 'fixture_response_request_id':f'req_owned_{n}', 'emitted':[]}
            REQS.append(row)
        save(row)
        msg={'id':row['response_message_id'],'type':'message','role':'assistant','model':'owned-stream-model','content':[],
             'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':20,'output_tokens':0}}
        if self.path.split('?')[0].endswith('/count_tokens'):
            return self.out_json({'input_tokens':20})
        if not body.get('stream'):
            row['action']='nonstream_fallback' if main else 'probe';row['done_at']=time.time();save(row)
            return self.out_json({**msg,'content':[{'type':'text','text':f'OWNED-FALLBACK-OK-{case}-{n}'}],'stop_reason':'end_turn','usage':{'input_tokens':20,'output_tokens':12}})
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('Connection','close');self.send_header('request-id',row['fixture_response_request_id']);self.end_headers();self.close_connection=True
        def emit(kind,**fields):
            event={'type':kind,**fields};self.wfile.write(f'event: {kind}\ndata: {json.dumps(event)}\n\n'.encode());self.wfile.flush()
            row['emitted'].append({'type':kind,'at':time.time(), **({'index':fields['index']} if 'index' in fields else {})});save(row)
        def txt(index,content,close=True):
            emit('content_block_start',index=index,content_block={'type':'text','text':''})
            for i in range(0,len(content),128):
                emit('content_block_delta',index=index,delta={'type':'text_delta','text':content[i:i+128]});time.sleep(.025)
            if close:emit('content_block_stop',index=index)
        def end(reason='end_turn'):
            emit('message_delta',delta={'stop_reason':reason,'stop_sequence':None},usage={'output_tokens':160});emit('message_stop')
        def fail():
            # Delay makes partial text observable before the fault. 'eof' ends the
            # response without message_stop, like the Walkie gateway EOF
            # (eof_before_terminal); 'error' sends an in-stream error event.
            time.sleep(2)
            row['fail_mode']=FAIL_MODE
            if FAIL_MODE=='error':
                emit('error',error={'type':'api_error','message':'Owned fixture server error after visible output'})
        try:
            emit('message_start',message=msg)
            global CHAIN_FAULT_NEXT_AUTO
            fault=main and attempt==1 and case not in ('OWNED-BASELINE','OWNED-RECOVERY','OWNED-MANUAL-FOLLOWUP',AUTO_CASE)
            chain_auto_fault=main and case==AUTO_CASE and CHAIN_FAULT_NEXT_AUTO
            if chain_auto_fault: CHAIN_FAULT_NEXT_AUTO=False
            if chain_auto_fault:
                row['action']='auto_recovery_closed_text_then_error'
                txt(0,varied('VISIBLE-OWNED-CHAIN-AGAIN',24));fail()
            elif not fault:
                row['action']='retry_stream_success' if main and attempt>1 else 'complete'
                txt(0,f'OWNED-REPLY-OK-{case}-{n}');end()
            elif case in ('OWNED-TEXT-CLOSED','OWNED-TEXT-OPEN','OWNED-TEXT-CHAIN','OWNED-TEXT-MANUAL'):
                closed=case!='OWNED-TEXT-OPEN'
                row['action']='closed_text_then_error' if closed else 'open_text_then_error'
                if case=='OWNED-TEXT-CHAIN': CHAIN_FAULT_NEXT_AUTO=True
                txt(0,varied('VISIBLE-OWNED-PARTIAL-'+case[6:],24),close=closed);fail()
            elif case=='OWNED-PARTIAL-TOOL':
                row['action']='partial_tool_then_error'
                txt(0,varied('VISIBLE-OWNED-PARTIAL-TOOL',18))
                emit('content_block_start',index=1,content_block={'type':'tool_use','id':'toolu_owned_partial','name':'Read','input':{}})
                emit('content_block_delta',index=1,delta={'type':'input_json_delta','partial_json':'{"file_path": "'+(WORKSPACE/'notes.txt').as_posix()+'", "lim'})
                fail()
            elif case=='OWNED-COMPLETE-TOOL':
                row['action']='complete_tool_then_error'
                txt(0,varied('VISIBLE-OWNED-COMPLETE-TOOL',18))
                command='printf "OWNED-TOOL-EXECUTED-ONCE\n" >> owned-tool-count.txt; printf "OWNED-TOOL-EXECUTED-ONCE\n"'
                emit('content_block_start',index=1,content_block={'type':'tool_use','id':'toolu_owned_once','name':'Bash','input':{}})
                emit('content_block_delta',index=1,delta={'type':'input_json_delta','partial_json':json.dumps({'command':command,'description':'Append one marker in the owned workspace'})})
                emit('content_block_stop',index=1);fail()
            row['done_at']=time.time();save(row)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError) as exc:
            row['write_error']=type(exc).__name__;row['done_at']=time.time();save(row)
