"""E2E: the real Codex CLI (codex exec) through a real ciel-runtime router with stored tokens.

Codex runs with an isolated CODEX_HOME (fake ChatGPT auth), a model provider pointing at the
router's /backend-api/codex, and the router forwards to a fake upstream. The first upstream
answer is a tool call, so the CLI sends a second request inside the same turn.
"""
import base64, json, os, shutil, subprocess, sys, threading, time, urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(r"C:\Users\djlov\ciel-runtime")
CODEX = r"C:\Users\djlov\AppData\Roaming\npm\codex.cmd"
ROOT = Path(sys.argv[1]); shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
CFG, WS, CH = ROOT / "cfg", ROOT / "ws", ROOT / "codex-home"; CFG.mkdir(); WS.mkdir(); CH.mkdir()
ROUTER_PORT = 19968
LOG = open(ROOT / "e2e.log", "w", encoding="utf-8"); T0 = time.time(); RESULTS = []
def log(m):
    line = f"{time.time()-T0:7.2f} {m}"; print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def check(name, ok, detail=""):
    RESULTS.append(ok); log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")
def jwt(claims):
    p = lambda v: base64.urlsafe_b64encode(json.dumps(v).encode()).rstrip(b"=").decode()
    return f"{p({'alg':'none'})}.{p(claims)}.sig"
def access(name, account):
    return jwt({"exp": int(time.time() + 86400), "sub": name, "https://api.openai.com/auth": {"chatgpt_account_id": account, "chatgpt_plan_type": "pro"}})

SEEN = []  # (token, account, session_id header, prompt_cache_key, last input type)
USAGE = {"A": 96.0, "B": 5.0}
class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(404); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b"{}")
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        token = self.headers.get("authorization", "").split(" ", 1)[-1]
        payload = token.split(".")[1] if token.count(".") == 2 else ""
        name = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"] if payload else token
        items = body.get("input") or []
        last = items[-1].get("type") if items and isinstance(items[-1], dict) else None
        SEEN.append((name, self.headers.get("chatgpt-account-id"), self.headers.get("session_id"), body.get("prompt_cache_key"), last))
        self.send_response(200); self.send_header("content-type", "text/event-stream")
        self.send_header("x-codex-primary-used-percent", str(USAGE.get(name, 0)))
        self.send_header("x-codex-primary-reset-at", str(int(time.time() + 3600)))
        self.end_headers()
        rid = f"resp_{len(SEEN)}"
        if last == "message":  # first request of a turn: ask for a tool call
            item = {"type": "function_call", "id": f"fc_{len(SEEN)}", "call_id": f"call_{len(SEEN)}", "name": "shell",
                    "arguments": json.dumps({"command": ["cmd", "/c", "echo", "tool-ran"]})}
        else:
            item = {"type": "message", "id": f"msg_{len(SEEN)}", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": f"DONE via {name}", "annotations": []}]}
        events = [
            {"type": "response.created", "response": {"id": rid}},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {"id": rid, "usage": {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}}},
        ]
        for event in events:
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()); self.wfile.flush()

upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream); UP = upstream.server_address[1]
threading.Thread(target=upstream.serve_forever, daemon=True).start()

(CFG / "config.json").write_text(json.dumps({"current_provider": "codex", "providers": {"codex": {"route_through_router": True}}}), encoding="utf-8")
base_env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_", "CODEX_", "OPENAI_"))}
env = dict(base_env, CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_ROUTER_PORT=str(ROUTER_PORT), CIEL_RUNTIME_LAUNCH_CWD=str(WS),
           CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=f"http://127.0.0.1:{UP}/backend-api/codex", CIEL_RUNTIME_SKIP_MENU="1",
           CIEL_RUNTIME_UPDATE_CHECK="0", CIEL_RUNTIME_SELF_UPDATE_CHECK="0", PYTHONIOENCODING="utf-8")
def ciel(*args):
    return subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", *args], env=env, cwd=WS, capture_output=True, text=True, timeout=60)
for name in ("A", "B"):
    path = ROOT / f"{name}.json"
    path.write_text(json.dumps({"tokens": {"access_token": access(name, f"acct-{name}"), "refresh_token": f"r-{name}", "account_id": f"acct-{name}"}}))
    ciel("tokens", "import", "codex", "--from", str(path), "--label", name)
ciel("log-level", "INFO")
router = subprocess.Popen([sys.executable, str(REPO / "ciel_runtime.py"), "serve"], env=env, cwd=WS, stdout=open(ROOT / "router.out", "wb"), stderr=subprocess.STDOUT)
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{ROUTER_PORT}/health", timeout=1).read(); break
    except Exception:
        time.sleep(0.5)

# The CLI's own (fake) ChatGPT sign-in: the router must replace it.
(CH / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": None, "tokens": {"id_token": jwt({"email": "cli@example.com", "https://api.openai.com/auth": {"chatgpt_account_id": "cli-acct", "chatgpt_plan_type": "pro"}}),
                                          "access_token": access("CLI", "cli-acct"), "refresh_token": "cli-r", "account_id": "cli-acct"},
                                          "last_refresh": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}))
codex_env = dict(base_env, CODEX_HOME=str(CH))
provider = 'model_providers.ciel-e2e={name="ciel-e2e",base_url="http://127.0.0.1:%d/backend-api/codex",wire_api="responses",requires_openai_auth=true,supports_websockets=false}' % ROUTER_PORT
def codex_exec(prompt):
    cmd = [CODEX, "exec", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-c", 'model_provider="ciel-e2e"', "-c", provider, "-m", "gpt-5.5", prompt]
    result = subprocess.run(cmd, env=codex_env, cwd=WS, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    log(f"codex exec exit={result.returncode} stdout_tail={result.stdout.strip()[-160:]!r} stderr_tail={result.stderr.strip()[-300:]!r}")
    return result

try:
    first = codex_exec("run a tool then answer")
    turn1 = list(SEEN)
    log(f"turn 1 requests: {turn1}")
    check("real Codex turn: first request used stored A, not the CLI token", len(turn1) >= 1 and turn1[0][0] == "A" and turn1[0][1] == "acct-A", str(turn1[:1]))
    check("real Codex tool-loop request stayed on draining A", len(turn1) >= 2 and turn1[1][0] == "A" and turn1[1][4] != "message", str(turn1[1:2]))
    check("real Codex output came back", "DONE via A" in first.stdout)
    second = codex_exec("a new conversation")
    turn2 = SEEN[len(turn1):]
    log(f"conversation 2 requests: {turn2}")
    check("new conversation avoided draining A", bool(turn2) and turn2[0][0] == "B", str(turn2[:1]))
    check("CLI token never reached the upstream", all(r[0] != "CLI" for r in SEEN))
finally:
    router.terminate(); router.wait(10); upstream.shutdown()
    for line in (CFG / "router-instances").rglob("*.log"):
        for text in line.read_text(encoding="utf-8", errors="replace").splitlines():
            if "oauth_token" in text:
                log("  " + text[-200:])
    log(f"RESULT {sum(RESULTS)}/{len(RESULTS)} passed")
