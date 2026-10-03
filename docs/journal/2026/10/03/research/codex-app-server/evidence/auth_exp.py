"""codex 0.160.0 app-server with --ws-auth capability-token; --remote TUI with --remote-auth-token-env;
approval_policy=never + danger-full-access on the server only; what a second client sees when the TUI resumes."""
import base64, json, os, secrets, socket, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = Path(r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\01\research\codex-input-transport\evidence")
sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
from ciel_runtime_support.windows_conpty import WindowsConPtySession, _visible_terminal_text  # noqa: E402

shared = (EV / "remote_tui_exp.py").read_text(encoding="utf-8")
ns: dict = {"__builtins__": __builtins__, "json": json, "time": time, "socket": socket, "os": os,
            "struct": __import__("struct"), "base64": base64}
exec(shared[shared.index("class WsClient:"):shared.index("def pane():")], ns)
WsClient = ns["WsClient"]

class WsAuth(WsClient):
    def __init__(self, port, token=None):
        self.s = socket.create_connection(("127.0.0.1", port), 10)
        key = base64.b64encode(os.urandom(16)).decode()
        auth = f"Authorization: Bearer {token}\r\n" if token else ""
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{auth}\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(4096)
            if not chunk:
                break
            resp += chunk
        head, _, self.buf = resp.partition(b"\r\n\r\n")
        self.status = head.split(b"\r\n")[0].decode(errors="replace")
        self.next_id = 0; self.inbox = []

REQ = []
class Up(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self): self.send_response(404); self.end_headers()
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        items = body.get("input") or []; last = items[-1] if items else {}
        tools = [t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)]
        users = [c.get("text", "") for it in items if isinstance(it, dict) and it.get("role") == "user"
                 for c in (it.get("content") or []) if isinstance(c, dict)]
        n = len(REQ) + 1; REQ.append({"last_type": last.get("type"), "tools": tools[:12], "last_user": (users or [""])[-1][:40],
                                       "tool_output": str(last.get("output"))[:200] if last.get("type") == "function_call_output" else None})
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        if users and "RUN-CMD" in users[-1] and last.get("type") == "message" and "exec_command" in tools:
            item = {"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "exec_command",
                    "arguments": json.dumps({"cmd": "echo RAN-OK-123"})}
        elif users and "RUN-CMD" in users[-1] and last.get("type") == "message" and "shell_command" in tools:
            item = {"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "shell_command",
                    "arguments": json.dumps({"command": "echo RAN-OK-123"})}
        else:
            item = {"type": "message", "id": f"msg_{n}", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": f"AUTH-REPLY-{n} to [{(users or [''])[-1][:24]}]", "annotations": []}]}
        for ev in ({"type": "response.created", "response": {"id": f"r{n}"}},
                   {"type": "response.output_item.done", "output_index": 0, "item": item},
                   {"type": "response.completed", "response": {"id": f"r{n}", "usage": {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 2}}}):
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()); self.wfile.flush()

up = ThreadingHTTPServer(("127.0.0.1", 0), Up); UP = up.server_address[1]
threading.Thread(target=up.serve_forever, daemon=True).start()
ROOT = Path(r"C:\cxe"); HOME, WSDIR = ROOT / "codex-home", ROOT / "ws"
cfg = (HOME / "config.toml").read_text(encoding="utf-8")
import re
(HOME / "config.toml").write_text(re.sub(r'base_url = "http://127.0.0.1:\d+/v1"', f'base_url = "http://127.0.0.1:{UP}/v1"', cfg), encoding="utf-8")
TID = "01a10351-25bd-7bd3-a3fb-0ca0b505217d"
NEW = str(HERE / "codex-01600/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
PORT = 47834
TOKEN = secrets.token_hex(24); (ROOT / "ws-token").write_text(TOKEN, encoding="utf-8")
env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CIEL_"))}
env.update(CODEX_HOME=str(HOME), CXTOK=TOKEN)
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
os.chdir(WSDIR)

server = subprocess.Popen([NEW, "app-server", "-c", 'approval_policy="never"', "-c", 'sandbox_mode="danger-full-access"',
                           "--listen", f"ws://127.0.0.1:{PORT}", "--ws-auth", "capability-token",
                           "--ws-token-file", str(ROOT / "ws-token")],
                          env=env, cwd=WSDIR, stdout=open(ROOT / "auth-server.out", "wb"), stderr=subprocess.STDOUT)
for _ in range(80):
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.5).close(); break
    except OSError:
        time.sleep(0.25)
for path in ("/readyz", "/healthz"):
    try:
        code = urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5).status
    except Exception as exc:
        code = getattr(exc, "code", repr(exc))
    log(f"GET {path} without token -> {code}")
log(f"ws without token -> {WsAuth(PORT).status}")
log(f"ws with wrong token -> {WsAuth(PORT, 'nope').status}")
side = WsAuth(PORT, TOKEN); log(f"ws with token -> {side.status}")
r = side.request("initialize", {"clientInfo": {"name": "ciel-auth-exp", "title": "exp", "version": "0"}, "capabilities": {"experimentalApi": True}})
side.send({"method": "initialized"}); log(f"initialize ok={'result' in r}")

tui = WindowsConPtySession([NEW, "--remote", f"ws://127.0.0.1:{PORT}", "--remote-auth-token-env", "CXTOK", "resume", TID],
                           env, log=lambda a, b: None, mirror_output=False, forward_stdin=False)
log(f"TUI pid {tui.pid}")
def screen(): return _visible_terminal_text(tui.output_tail())
def wait_screen(text, secs=30):
    end = time.time() + secs
    while time.time() < end:
        if text in screen():
            return True
        time.sleep(0.5)
    return False
log(f"TUI shows history (PLAIN-FOUR)={wait_screen('PLAIN-FOUR', 20)}")
seen = []
end = time.time() + 4
while time.time() < end:
    m = side.recv(0.5)
    if m is not None:
        side.inbox.append(m)
for m in side.inbox:
    p = m.get("params") or {}
    seen.append((m.get("method"), (p.get("thread") or {}).get("id") or p.get("threadId")))
log(f"notifications to the second client after the TUI resumed: {seen}")
r = side.request("thread/loaded/list", {}); log("thread/loaded/list: " + json.dumps(r.get("result"))[:300])
side.inbox.clear()
log(f"thread/resume ok={'result' in side.request('thread/resume', {'threadId': TID})}")
r = side.request("turn/start", {"threadId": TID, "input": [{"type": "text", "text": "RUN-CMD please"}]})
log(f"turn/start ok={'result' in r} err={r.get('error')}")
done, requests_from_server, cmd_items = False, [], []
end = time.time() + 40
while time.time() < end and not done:
    m = side.recv(1.0)
    if m is None:
        continue
    if "id" in m and "method" in m:
        requests_from_server.append(m.get("method"))
    if m.get("method") == "item/completed":
        it = (m.get("params") or {}).get("item") or {}
        if it.get("type") == "commandExecution":
            cmd_items.append({k: it.get(k) for k in ("command", "status", "exitCode", "aggregatedOutput")})
    if m.get("method") == "turn/completed":
        done = True
log(f"turn completed={done} server->client requests={requests_from_server}")
log(f"commandExecution items={cmd_items}")
log(f"upstream requests={REQ}")
log(f"TUI shows RAN-OK-123={wait_screen('RAN-OK-123', 10)} reply={wait_screen('AUTH-REPLY', 10)}")
s = screen(); (ROOT / "screen-auth.txt").write_text(s, encoding="utf-8"); print("===== screen\n" + s[-900:], flush=True)
tui.close(); server.terminate(); server.wait(10)
log("done")
