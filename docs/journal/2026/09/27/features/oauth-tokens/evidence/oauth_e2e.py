"""E2E: stored OAuth tokens through a real ciel-runtime router process.

A fake upstream plays chatgpt.com/backend-api/codex, api.anthropic.com and the
OAuth token endpoint. The router runs from this repo (`ciel_runtime.py serve`)
with an isolated config dir and a temporary workspace; tokens are added with
the real `ciel_runtime.py cli tokens import` command.
"""
import base64, json, os, shutil, subprocess, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(r"C:\Users\djlov\ciel-runtime")
ROOT = Path(sys.argv[1]); shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir(parents=True)
CFG, WS = ROOT / "cfg", ROOT / "ws"; CFG.mkdir(); WS.mkdir()
ROUTER_PORT = 19967
LOG = open(ROOT / "e2e.log", "w", encoding="utf-8")
T0 = time.time()
RESULTS = []
def log(m):
    line = f"{time.time()-T0:7.2f} {m}"; print(line, flush=True); LOG.write(line + "\n"); LOG.flush()
def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok))); log(f"{'PASS' if ok else 'FAIL'} {name} {detail}")

def jwt(claims):
    p = lambda v: base64.urlsafe_b64encode(json.dumps(v).encode()).rstrip(b"=").decode()
    return f"{p({'alg':'none'})}.{p(claims)}.sig"
def codex_access(name, exp):
    return jwt({"exp": int(exp), "sub": name, "https://api.openai.com/auth": {"chatgpt_account_id": f"acct-{name}"}})

# ---- fake upstream --------------------------------------------------------------------------------
REQUESTS = []           # (path, token, account, session, turn marker)
CODEX_USAGE = {"A": 50.0, "B": 10.0}
CODEX_LIMITED = set()   # tokens answering 429 usage_limit_reached
LIMIT_RESET = {}        # token -> epoch
CLAUDE_UTIL = {"C": 0.40, "D": 0.10}
CLAUDE_REJECT = set()
REFRESHES = []

def token_name(auth):
    token = auth.split(" ", 1)[-1]
    if token.count(".") == 2:
        payload = token.split(".")[1]; payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["sub"]
    return token.replace("sk-ant-oat-", "")

class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status); self.send_header("content-type", "application/json")
        for k, v in (headers or {}).items(): self.send_header(k, v)
        self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        if self.path.startswith("/oauth/token"):
            body = json.loads(raw) if raw.startswith(b"{") else dict(x.split("=", 1) for x in raw.decode().split("&"))
            REFRESHES.append(body.get("refresh_token"))
            return self._json(200, {"access_token": codex_access("E2", time.time() + 864000), "refresh_token": "r-E-2", "expires_in": 864000})
        auth = self.headers.get("authorization", "")
        name = token_name(auth)
        body = json.loads(raw or b"{}")
        if self.path.startswith("/backend-api/codex/responses"):
            last = (body.get("input") or [{}])[-1]
            REQUESTS.append(("codex", name, self.headers.get("chatgpt-account-id"), body.get("prompt_cache_key"), last.get("type")))
            if name in CODEX_LIMITED:
                return self._json(429, {"error": {"type": "usage_limit_reached", "message": "limit", "resets_at": int(LIMIT_RESET[name])}})
            usage = CODEX_USAGE.get(name, 0.0)
            self.send_response(200); self.send_header("content-type", "text/event-stream")
            self.send_header("x-codex-primary-used-percent", str(usage))
            self.send_header("x-codex-primary-reset-at", str(int(time.time() + 3600)))
            self.end_headers()
            events = [{"type": "response.created", "response": {"id": "r1", "status": "in_progress"}},
                      {"type": "response.output_text.delta", "delta": f"hello from {name}"},
                      {"type": "response.completed", "response": {"id": "r1", "status": "completed", "output": []}}]
            for event in events:
                self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()); self.wfile.flush()
            return
        if self.path.startswith("/v1/messages"):
            REQUESTS.append(("claude", name, self.headers.get("anthropic-beta"), self.headers.get("x-claude-code-session-id"), None))
            headers = {"anthropic-ratelimit-unified-5h-utilization": str(CLAUDE_UTIL.get(name, 0)),
                       "anthropic-ratelimit-unified-5h-reset": str(int(time.time() + 3600)),
                       "anthropic-ratelimit-unified-status": "allowed"}
            if name in CLAUDE_REJECT:
                headers["anthropic-ratelimit-unified-status"] = "rejected"
                headers["anthropic-ratelimit-unified-reset"] = str(int(LIMIT_RESET[name]))
                return self._json(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "limit reached"}}, headers)
            return self._json(200, {"id": "m1", "type": "message", "role": "assistant", "model": body.get("model"),
                                    "content": [{"type": "text", "text": f"hello from {name}"}], "stop_reason": "end_turn",
                                    "usage": {"input_tokens": 1, "output_tokens": 3}}, headers)
        self._json(404, {"error": "not found"})

upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream); UP = upstream.server_address[1]
threading.Thread(target=upstream.serve_forever, daemon=True).start()
log(f"fake upstream on {UP}")

# ---- isolated router ------------------------------------------------------------------------------
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "codex",
    "providers": {"codex": {"route_through_router": True},
                  "anthropic": {"route_through_router": True, "base_url": f"http://127.0.0.1:{UP}"}},
}), encoding="utf-8")
env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC_"))}
env.update({"CIEL_RUNTIME_CONFIG_DIR": str(CFG), "CIEL_RUNTIME_ROUTER_PORT": str(ROUTER_PORT), "CIEL_RUNTIME_LAUNCH_CWD": str(WS),
            "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM": f"http://127.0.0.1:{UP}/backend-api/codex",
            "CIEL_RUNTIME_OAUTH_TOKEN_URL_CODEX": f"http://127.0.0.1:{UP}/oauth/token",
            "CIEL_RUNTIME_OAUTH_TOKEN_URL_CLAUDE": f"http://127.0.0.1:{UP}/oauth/token",
            "CIEL_RUNTIME_SKIP_MENU": "1", "CIEL_RUNTIME_UPDATE_CHECK": "0", "CIEL_RUNTIME_SELF_UPDATE_CHECK": "0", "PYTHONIOENCODING": "utf-8"})

def cli(*args):
    result = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", "tokens", *args], env=env, cwd=WS,
                            capture_output=True, text=True, timeout=60)
    log(f"$ tokens {' '.join(args)} -> exit {result.returncode}\n{result.stdout.strip()}{(chr(10) + result.stderr.strip()) if result.stderr.strip() else ''}")
    return result

now = time.time()
for name, exp in (("A", now + 86400), ("B", now + 86400), ("E", now + 120)):
    path = ROOT / f"codex-{name}.json"
    path.write_text(json.dumps({"tokens": {"access_token": codex_access(name, exp), "refresh_token": f"r-{name}", "account_id": f"acct-{name}"}}))
    cli("import", "codex", "--from", str(path), "--label", name)
for name in ("C", "D"):
    path = ROOT / f"claude-{name}.json"
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": f"sk-ant-oat-{name}", "refreshToken": f"r-{name}", "expiresAt": int((now + 86400) * 1000)}}))
    cli("import", "claude", "--from", str(path), "--label", name)
# E is kept out of rotation until the refresh test so the first two phases use A and B only.
state = json.loads((CFG / "workspaces").glob("*/oauth-tokens.state.json").__next__().read_text())
ids = {t["label"]: t["token_id"] for t in state["tokens"]}
cli("disable", ids["E"])
vault_text = next((CFG / "workspaces").glob("*/oauth-tokens.vault.json")).read_text()
check("vault holds no plaintext token", "sk-ant-oat-C" not in vault_text and "r-A" not in vault_text)

subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", "log-level", "INFO"], env=env, cwd=WS, capture_output=True, timeout=60)
router = subprocess.Popen([sys.executable, str(REPO / "ciel_runtime.py"), "serve"], env=env, cwd=WS,
                          stdout=open(ROOT / "router.out", "wb"), stderr=subprocess.STDOUT)
for _ in range(60):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{ROUTER_PORT}/health", timeout=1).read(); break
    except Exception:
        time.sleep(0.5)
log(f"router pid {router.pid} up")

def codex(session, kind):
    user = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    call = {"type": "function_call", "call_id": "c1", "name": "shell", "arguments": "{}"}
    items = [user] if kind == "turn" else [user, call, {"type": "function_call_output", "call_id": "c1", "output": "ok"}]
    body = {"model": "gpt-5.5", "stream": True, "prompt_cache_key": session, "input": items}
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER_PORT}/backend-api/codex/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json", "authorization": "Bearer cli-own-token", "chatgpt-account-id": "cli-acct", "session_id": session})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")

def last_codex():
    return [r for r in REQUESTS if r[0] == "codex"][-1]

try:
    # 1. a new conversation fills the first token
    status, text = codex("conv1", "turn"); check("conv1 turn 1 served by A", status == 200 and last_codex()[1] == "A" and last_codex()[2] == "acct-A" and "hello from A" in text, str(last_codex()))
    # 2. A reports 96% -> draining, but a tool-loop request stays on A
    CODEX_USAGE["A"] = 96.0
    codex("conv1", "tool"); status, text = codex("conv1", "tool")
    check("conv1 tool loop stays on draining A", last_codex()[1] == "A", str(last_codex()))
    # 3. the next user turn moves to B
    status, text = codex("conv1", "turn"); check("conv1 next turn moves to B", status == 200 and last_codex()[1] == "B", str(last_codex()))
    # 4. a new conversation also avoids draining A
    codex("conv2", "turn"); check("conv2 starts on B", last_codex()[1] == "B", str(last_codex()))
    # 5. B hits its usage limit mid-turn -> the same request is retried on A before output
    CODEX_LIMITED.add("B"); LIMIT_RESET["B"] = time.time() + 12
    before = len(REQUESTS)
    status, text = codex("conv2", "tool")
    tail = [r[1] for r in REQUESTS[before:]]
    check("limit on B retried on A within one request", status == 200 and tail == ["B", "A"] and "hello from A" in text, str(tail))
    listing = cli("list").stdout
    check("tokens list shows B limited", "limited, back in" in listing)
    # 6. after B's reset it is back in service; A still draining so new conversations go to B
    CODEX_LIMITED.discard("B"); time.sleep(13)
    codex("conv3", "turn"); check("B restored after its reset", last_codex()[1] == "B", str(last_codex()))
    # 7. every token limited -> the CLI gets the upstream 429
    CODEX_LIMITED.update({"A", "B"}); LIMIT_RESET["A"] = LIMIT_RESET["B"] = time.time() + 600
    status, text = codex("conv4", "turn"); check("all limited relays 429", status == 429 and "usage_limit" in text, f"status={status}")
    CODEX_LIMITED.clear()
    # 8. the watcher refreshes an expiring token (E expires in 2 minutes, lead is 10 minutes)
    cli("enable", ids["E"])
    deadline = time.time() + 90
    while time.time() < deadline and "r-E" not in REFRESHES:
        time.sleep(2)
    after = json.loads(next((CFG / "workspaces").glob("*/oauth-tokens.state.json")).read_text())
    e_state = next(t for t in after["tokens"] if t["label"] == "E")
    check("watcher refreshed E with its refresh token", "r-E" in REFRESHES and e_state["expires_at"] > time.time() + 800000, f"refreshes={REFRESHES}")
    # 9. Anthropic routed: stored Claude token replaces the CLI bearer; rejected status rotates
    ws_cfg = next((CFG / "workspaces").glob("*/config.json"))
    cfg = json.loads(ws_cfg.read_text()); cfg["current_provider"] = "anthropic"; ws_cfg.write_text(json.dumps(cfg))
    def claude(session, content):
        body = {"model": "claude-sonnet-5", "max_tokens": 32, "stream": False, "messages": [{"role": "user", "content": content}]}
        req = urllib.request.Request(f"http://127.0.0.1:{ROUTER_PORT}/v1/messages", data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json", "authorization": "Bearer cli-oauth", "anthropic-version": "2023-06-01",
                                              "anthropic-beta": "claude-code-20250219", "x-claude-code-session-id": session})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()
    last_claude = lambda: ([r for r in REQUESTS if r[0] == "claude"] or [("claude", None, None, None, None)])[-1]
    status, text = claude("s1", "hi")
    log(f"claude first response {status} {text[:200]}")
    check("claude served by stored C with oauth beta", status == 200 and last_claude()[1] == "C" and "oauth-2025-04-20" in (last_claude()[2] or ""), f"{status} {last_claude()}")
    CLAUDE_REJECT.add("C"); LIMIT_RESET["C"] = time.time() + 600
    status, text = claude("s1", [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}])
    check("claude rejected C retried on D", status == 200 and last_claude()[1] == "D" and "hello from D" in text, f"{status} {last_claude()}")
    log("router log lines:")
    for line in (CFG / "router-instances").rglob("*.log"):
        for text_line in line.read_text(encoding="utf-8", errors="replace").splitlines():
            if "oauth_token" in text_line:
                log("  " + text_line[-220:])
finally:
    router.terminate(); router.wait(10); upstream.shutdown()
    passed = sum(1 for _n, ok in RESULTS if ok)
    log(f"RESULT {passed}/{len(RESULTS)} passed")
