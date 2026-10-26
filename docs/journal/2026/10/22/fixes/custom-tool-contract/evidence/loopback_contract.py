"""Real loopback upstream replay; no model or production API calls."""
import http.server
import io
import json
import sys
import threading
import urllib.request
from types import SimpleNamespace
from unittest import mock

RUNTIME = sys.argv[1]
sys.path.insert(0, RUNTIME)
from ciel_runtime_support.provider_responses_passthrough import ProviderResponsesPassthrough, ProviderResponsesPassthroughPorts

SCRIPT = 'text(await tools.clock__curr_time({}));'
ITEM = {"type":"function_call","id":"fc_exec","call_id":"owned_call","name":"exec","arguments":json.dumps({"input":SCRIPT})}
rows = [{"type":"response.output_item.done","output_index":0,"item":ITEM},{"type":"response.completed","response":{"id":"resp_owned","status":"completed","output":[ITEM]}}]
nl = chr(10)
payload = ''.join('event: '+r['type']+nl+'data:'+json.dumps(r)+nl+nl for r in rows).encode()
requests = []
class Upstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        requests.append(body)
        self.send_response(200)
        self.send_header('content-type','text/event-stream')
        self.send_header('content-length',str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self,*args): pass
server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
threading.Thread(target=server.serve_forever,daemon=True).start()
base = 'http://127.0.0.1:'+str(server.server_port)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
service = ProviderResponsesPassthrough(ProviderResponsesPassthroughPorts(
    project_channel_context=lambda b:(b,{}),begin_channel_delivery=mock.Mock(),
    normalize_model=lambda p,c,m:m,normalize_request=lambda p,c,b:b,
    upstream_base=lambda p,c:base,join_url=lambda b,p:b+p,headers=lambda *a:{},
    urlopen=lambda req,**kw:opener.open(req,timeout=2),timeout_seconds=lambda c:2,
    copy_response_headers=lambda *a:None))
handler=SimpleNamespace(headers={},wfile=io.BytesIO(),send_response=mock.Mock(),send_header=mock.Mock(),end_headers=mock.Mock())
body={"model":"owned-fixture","stream":True,"input":[{"type":"additional_tools","tools":[{"type":"namespace","name":"functions","tools":[{"type":"custom","name":"exec"}]}]}]}
try:
    service.forward(handler,'cielairouter',{},body)
    wire=handler.wfile.getvalue()
    output=[json.loads(line[5:]) for line in wire.splitlines() if line.startswith(b'data:')]
    result=output[-1]['response']['output'][0]
    assert result['type']=='custom_tool_call',result
    assert result['namespace']=='functions' and result['name']=='exec',result
    assert result['input']==SCRIPT and result['call_id']=='owned_call',result
    assert len(requests)==1
    print(json.dumps({"runtime":RUNTIME,"requests":1,"received_type":ITEM['type'],"delivered_type":result['type'],"namespace":result['namespace'],"raw_script_preserved":True,"outbound_flag":"absent","status":"PASS"},indent=2))
finally:
    server.shutdown();server.server_close()
