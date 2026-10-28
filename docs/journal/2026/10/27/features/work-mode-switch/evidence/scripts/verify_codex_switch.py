"""Real Codex app-server (approvalPolicy never) with Work mode OFF: the model calls work_mode_set(true).
'preapproved' uses Ciel's launch args (switch pre-approved); 'negative' omits them as a control.
Isolated HOME/config/router per case; never touches live routers. Run: python -I <absolute path>.
Set WORK_SWITCH_PROOF_REPO to test an installed snapshot."""
import json, os, queue, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = next(p for p in HERE.parents if (p / 'ciel_runtime.py').exists())
REPO = Path(os.environ.get('WORK_SWITCH_PROOF_REPO') or SOURCE)
OUT = HERE.parent / ('codex-' + ('installed-' if os.environ.get('WORK_SWITCH_PROOF_REPO') else 'source-') + time.strftime('%H%M%S')); OUT.mkdir()
CX = Path('C:/Users/djlov/AppData/Roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
sys.path.insert(0, str(REPO))
from ciel_runtime_support.work_mode_launch import codex_work_mode_args  # noqa: E402
BASE = {k: v for k, v in os.environ.items() if not k.upper().startswith(('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI'))}


def health():
    out = {}
    for p in (9505, 9524):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{p}/health', timeout=3) as r: h = json.load(r)
        except Exception as e: h = {'error': str(e)}
        out[str(p)] = {k: h.get(k) for k in ('pid', 'version', 'error')}
    return out


def case(name, args):
    run = OUT / name; run.mkdir()
    root = Path(tempfile.mkdtemp(prefix='codex-switch-')); cfg, ws = root / 'runtime', root / 'workspace'
    for p in (cfg, ws, root / 'home', root / 'appdata', root / 'localappdata', root / 'codex'): p.mkdir()
    env = dict(BASE, HOME=str(root / 'home'), USERPROFILE=str(root / 'home'), APPDATA=str(root / 'appdata'),
               LOCALAPPDATA=str(root / 'localappdata'), CODEX_HOME=str(root / 'codex'), NO_PROXY='127.0.0.1,localhost')
    summary = {'case': name, 'temp_root': str(root), 'codex_args': args}
    save = lambda n, d: (run / n).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding='utf-8')  # noqa: E731
    requests, mcp_calls, events, errors = [], [], [], []
    with socket.socket() as s: s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = json.dumps({'object': 'list', 'data': [{'id': 'switch-probe', 'object': 'model'}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            try:
                raw = self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/mcp-forward':
                    obj = json.loads(raw)
                    if obj.get('method') == 'tools/call': mcp_calls.append(obj); save('mcp-wire-calls.json', mcp_calls)
                    forwarded = urllib.request.Request(f'http://127.0.0.1:{port}/ca/mcp', data=raw, headers={'Content-Type': 'application/json'})
                    with opener.open(forwarded, timeout=15) as res:
                        body = res.read(); self.send_response(res.status); self.send_header('Content-Type', res.headers.get('Content-Type', 'application/json')); self.end_headers(); self.wfile.write(body)
                    return
                req = json.loads(raw); requests.append(req); save('gateway-requests.json', requests)
                names = [x.get('name', '') for x in req.get('tools', []) if x.get('type') == 'function']
                spaces = {f['name']: t['name'] for t in req.get('tools', []) if t.get('type') == 'namespace' for f in t.get('tools', [])}
                names += list(spaces)
                if len(requests) == 1:
                    match = next((x for x in names if x == 'work_mode_set' or x.endswith('__work_mode_set')), None)
                    if not match: raise RuntimeError(f'work_mode_set not offered among {names}')
                    item = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'name': match, 'arguments': json.dumps({'enabled': True}), 'status': 'completed'}
                    if match in spaces: item['namespace'] = spaces[match]
                    output = [item]
                else:
                    output = [{'type': 'message', 'id': 'msg_final', 'role': 'assistant', 'status': 'completed', 'content': [{'type': 'output_text', 'text': 'CODEX_SWITCH_DONE', 'annotations': []}]}]
                resp = {'id': f'resp_{len(requests)}', 'object': 'response', 'created_at': int(time.time()), 'status': 'completed', 'model': 'switch-probe', 'output': output,
                        'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}}
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Connection', 'close'); self.end_headers()
                def event(typ, **kw): self.wfile.write(('event: ' + typ + chr(10) + 'data: ' + json.dumps({'type': typ, **kw}) + chr(10) * 2).encode()); self.wfile.flush()
                event('response.created', response={**resp, 'status': 'in_progress', 'output': []})
                for i, item in enumerate(output):
                    event('response.output_item.added', output_index=i, item=item); event('response.output_item.done', output_index=i, item=item)
                event('response.completed', response=resp)
            except Exception as e:
                errors.append(repr(e)); save('gateway-errors.json', errors)
                try: self.send_error(500, str(e))
                except Exception: pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway); threading.Thread(target=server.serve_forever, daemon=True).start()
    (cfg / 'config.json').write_text(json.dumps({'current_provider': 'vllm', 'language': 'en', 'work_mode': False, 'providers': {'vllm': {
        'base_url': f'http://127.0.0.1:{server.server_port}', 'current_model': 'switch-probe', 'api_key': 'dummy', 'context_window': 128000, 'max_output_tokens': 4096}}}), encoding='utf-8')
    renv = {**env, 'CIEL_RUNTIME_CONFIG_DIR': str(cfg), 'CIEL_RUNTIME_ROUTER_PORT': str(port), 'CIEL_RUNTIME_TEST_ISOLATED': '1', 'CIEL_RUNTIME_LAUNCH_CWD': str(ws)}
    def work_mode(): return json.loads((cfg / 'config.json').read_text(encoding='utf-8')).get('work_mode', '<unset>')
    def mcp(method, params=None):
        data = {'jsonrpc': '2.0', 'id': int(time.time() * 1000), 'method': method, 'params': params or {}}
        with opener.open(urllib.request.Request(f'http://127.0.0.1:{port}/ca/mcp', data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'}), timeout=5) as r: return json.load(r)
    router = app = None
    rf = (run / 'router-console.txt').open('w', encoding='utf-8'); ef = (run / 'app-server-stderr.txt').open('w', encoding='utf-8')
    try:
        router = subprocess.Popen([sys.executable, str(REPO / 'ciel_runtime.py'), 'serve'], env=renv, cwd=ws, stdout=rf, stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            try: listed = mcp('tools/list'); break
            except Exception:
                if router.poll() is not None: raise RuntimeError('isolated router exited')
                time.sleep(.25)
        else: raise RuntimeError('isolated router unavailable')
        summary['config_before'] = work_mode()
        summary['router_work_tools_while_off'] = sorted(x['name'] for x in listed['result']['tools'] if x['name'].startswith('work_'))
        cli = [str(CX), *args, '-c', 'model="switch-probe"', '-c', 'model_provider="switchproof"', '-c', 'model_providers.switchproof.name="Switch proof"',
               '-c', f'model_providers.switchproof.base_url="http://127.0.0.1:{server.server_port}/v1"', '-c', 'model_providers.switchproof.wire_api="responses"',
               '-c', 'model_providers.switchproof.requires_openai_auth=false', '-c', f'mcp_servers.ciel-runtime-router.url="http://127.0.0.1:{server.server_port}/mcp-forward"', 'app-server']
        summary['version'] = subprocess.check_output([str(CX), '--version'], env=env, cwd=ws, text=True).strip()
        app = subprocess.Popen(cli, cwd=ws, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=ef, text=True, encoding='utf-8')
        q = queue.Queue()
        def reader():
            for line in app.stdout:
                try: obj = json.loads(line); events.append(obj); q.put(obj)
                except Exception: events.append({'raw': line})
        threading.Thread(target=reader, daemon=True).start()
        def rpc(i, method, params):
            app.stdin.write(json.dumps({'id': i, 'method': method, 'params': params}) + chr(10)); app.stdin.flush()
            end = time.time() + 60
            while time.time() < end:
                obj = q.get(timeout=max(.1, end - time.time()))
                if obj.get('id') == i:
                    if 'error' in obj: raise RuntimeError(json.dumps(obj))
                    return obj['result']
                if 'id' in obj and 'method' in obj: raise RuntimeError('unexpected server request ' + json.dumps(obj))
            raise TimeoutError(method)
        rpc(1, 'initialize', {'clientInfo': {'name': 'switch-proof', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
        app.stdin.write(json.dumps({'method': 'initialized'}) + chr(10)); app.stdin.flush()
        tid = rpc(2, 'thread/start', {'cwd': str(ws), 'approvalPolicy': 'never', 'sandbox': 'read-only', 'ephemeral': True, 'model': 'switch-probe'})['thread']['id']
        rpc(3, 'turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': 'Turn Ciel Work mode on yourself.'}]})
        end = time.time() + 90
        while time.time() < end:
            obj = q.get(timeout=max(.1, end - time.time()))
            if obj.get('method') == 'turn/completed': summary['turn_status'] = obj.get('params', {}).get('turn', {}).get('status'); break
            if 'id' in obj and 'method' in obj: summary['server_request'] = obj; raise RuntimeError('unexpected server request ' + obj.get('method', ''))
        else: raise TimeoutError('turn/completed')
        summary['config_after'] = work_mode()
        summary['gateway_requests'] = len(requests)
        summary['tools_offered'] = sorted({x.get('name', '') for r in requests[:1] for t in r.get('tools', []) for x in ([t] if t.get('type') == 'function' else t.get('tools', [])) if 'work_' in x.get('name', '')})
        summary['mcp_switch_calls'] = [c['params'].get('arguments') for c in mcp_calls if c['params'].get('name') == 'work_mode_set']
        last = requests[-1] if len(requests) > 1 else {}
        outputs = [x for x in last.get('input', []) if x.get('type') == 'function_call_output']
        summary['tool_output_seen_by_model'] = json.dumps(outputs[-1].get('output'), ensure_ascii=False)[:600] if outputs else None
    except Exception as e: summary['error'] = repr(e)
    finally:
        save('app-server-events.json', events)
        for p in (app, router):
            if p and p.poll() is None:
                p.terminate()
                try: p.wait(timeout=10)
                except subprocess.TimeoutExpired: p.kill(); p.wait(timeout=5)
        server.shutdown(); rf.close(); ef.close()
        for f in cfg.rglob('router.log'): shutil.copyfile(f, run / 'isolated-router.log')
        save('summary.json', summary)
    return summary


before = health()
pre = case('preapproved', codex_work_mode_args({'work_mode': False}, native=False, router_mcp_attached=True))
neg = case('negative', [])
after = health()
result = {'repo': str(REPO), 'preapproved': pre, 'negative': neg, 'live_before': before, 'live_after': after, 'checks': {
    'launch_args_preapprove_only_switch': pre['codex_args'] == ['-c', 'mcp_servers.ciel-runtime-router.tools.work_mode_set.approval_mode="approve"'],
    'router_lists_only_switch_while_off': pre.get('router_work_tools_while_off') == ['work_mode_set'],
    'preapproved_call_reached_router': pre.get('mcp_switch_calls') == [{'enabled': True}],
    'preapproved_config_turned_on': pre.get('config_before') is False and pre.get('config_after') is True,
    'preapproved_model_saw_now_on': 'now on' in (pre.get('tool_output_seen_by_model') or ''),
    'negative_call_never_reached_router': neg.get('mcp_switch_calls') == [],
    'negative_config_unchanged': neg.get('config_after') is False,
    'no_error': 'error' not in pre and 'error' not in neg,
    'live_unchanged': before == after,
}}
(OUT / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(result, indent=2, ensure_ascii=False))
raise SystemExit(0 if all(result['checks'].values()) else 1)
