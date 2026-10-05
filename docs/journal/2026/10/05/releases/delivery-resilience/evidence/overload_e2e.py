"""E2E: a real ciel router (Codex routed) in front of an upstream that answers 'high demand'."""
import json, os, shutil, socket, subprocess, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(r'C:\Users\djlov\ciel-runtime')
root = Path(__file__).parent / 'overload-e2e'; shutil.rmtree(root, ignore_errors=True); root.mkdir()
HIGH = json.dumps({'error': {'message': 'We\u2019re currently experiencing high demand, which may cause temporary errors.'}}).encode()
OK = (b'event: response.created\ndata: {"type":"response.created","response":{"id":"r1"}}\n\n'
      b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"done"}\n\n'
      b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"r1","status":"completed"}}\n\n')
plan = []
seen = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('content-length') or 0))
        seen.append(time.monotonic())
        status = plan.pop(0) if plan else 200
        body = HIGH if status != 200 else OK
        self.send_response(status)
        self.send_header('content-type', 'application/json' if status != 200 else 'text/event-stream')
        self.send_header('content-length', str(len(body)))
        self.end_headers(); self.wfile.write(body)


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


stub_port, router_port = free_port(), free_port()
stub = ThreadingHTTPServer(('127.0.0.1', stub_port), Stub); threading.Thread(target=stub.serve_forever, daemon=True).start()
cfg = root / 'cfg'; cfg.mkdir(); ws = root / 'ws'; ws.mkdir()
(cfg / 'config.json').write_text(json.dumps({'current_provider': 'codex', 'language': 'en', 'providers': {'codex': {'route_through_router': True}}}))
(cfg / 'log-level').write_text('INFO')
env = {k: v for k, v in os.environ.items() if not k.startswith(('CIEL_RUNTIME_', 'OPENAI_', 'CODEX_'))}
env.update(CIEL_RUNTIME_CONFIG_DIR=str(cfg), CIEL_RUNTIME_LAUNCH_CWD=str(ws), CIEL_RUNTIME_ROUTER_PORT=str(router_port),
           CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=f'http://127.0.0.1:{stub_port}/backend-api/codex',
           CIEL_RUNTIME_CODEX_OVERLOAD_RETRY_SECONDS='20', PYTHONIOENCODING='utf-8')
log = open(root / 'router.out', 'wb')
router = subprocess.Popen([sys.executable, str(REPO / 'ciel_runtime.py'), 'serve'], env=env, cwd=str(ws), stdout=log, stderr=subprocess.STDOUT)


def responses():
    req = urllib.request.Request(f'http://127.0.0.1:{router_port}/backend-api/codex/responses', method='POST',
                                 data=json.dumps({'model': 'gpt-test', 'stream': True, 'input': 'hi'}).encode(),
                                 headers={'content-type': 'application/json', 'authorization': 'Bearer local-test', 'accept': 'text/event-stream'})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode('utf-8', 'replace'), round(time.monotonic() - started, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace'), round(time.monotonic() - started, 1)


out = {}
try:
    for _ in range(80):
        try:
            if urllib.request.urlopen(f'http://127.0.0.1:{router_port}/health', timeout=1).status == 200: break
        except Exception: time.sleep(0.5)
    plan[:] = [500, 500]
    status, body, secs = responses()
    out['recovers'] = {'status': status, 'seconds': secs, 'upstream_calls': len(seen), 'got_done': '"delta":"done"' in body}
    seen.clear(); plan[:] = [500] * 20
    status, body, secs = responses()
    out['gives_up'] = {'status': status, 'seconds': secs, 'upstream_calls': len(seen), 'body': body[:400]}
finally:
    router.terminate(); router.wait(10); stub.shutdown(); log.close()
logs = []
for p in (cfg / 'router-instances').rglob('router.log'):
    logs += [line for line in p.read_text(encoding='utf-8', errors='replace').splitlines() if 'codex_overload' in line]
out['router_log'] = logs
print(json.dumps(out, indent=1, ensure_ascii=False))
