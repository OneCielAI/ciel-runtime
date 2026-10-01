"""Does a second app-server client's turn/start or turn/steer show up in a `codex --remote` TUI?"""
import base64, json, os, socket, struct, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/var/tmp/cxexp"); subprocess.run(["rm", "-rf", str(ROOT)]); ROOT.mkdir()
HOME, WSDIR = ROOT / "codex-home", ROOT / "ws"; HOME.mkdir(); WSDIR.mkdir()
CODEX = "/var/tmp/cx/node_modules/.bin/codex"; WSPORT = 47811
T = ["tmux", "-L", "cxexp"]
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

SEEN = []
class Up(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self): self.send_response(404); self.end_headers()
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        users = [c.get("text", "") for it in body.get("input", []) if isinstance(it, dict) and it.get("role") == "user"
                 for c in (it.get("content") or []) if isinstance(c, dict)]
        last = users[-1] if users else ""
        SEEN.append(last[:40])
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
        if "SLOW" in last: time.sleep(8)
        rid = f"resp_{len(SEEN)}"
        item = {"type": "message", "id": f"msg_{len(SEEN)}", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": f"STUB-REPLY-{len(SEEN)} to [{last[:30]}]", "annotations": []}]}
        for ev in ({"type": "response.created", "response": {"id": rid}},
                   {"type": "response.output_item.done", "output_index": 0, "item": item},
                   {"type": "response.completed", "response": {"id": rid, "usage": {"input_tokens": 1, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 2}}}):
            self.wfile.write(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()); self.wfile.flush()

up = ThreadingHTTPServer(("127.0.0.1", 0), Up); UP = up.server_address[1]
threading.Thread(target=up.serve_forever, daemon=True).start()
(HOME / "config.toml").write_text("\n".join([
    'model = "stub-model"', 'model_provider = "stub"', "[model_providers.stub]", 'name = "stub"',
    f'base_url = "http://127.0.0.1:{UP}/v1"', 'wire_api = "responses"', "requires_openai_auth = false",
    f'[projects."{WSDIR}"]', 'trust_level = "trusted"', ""]))
env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CIEL_"))}
env.update(CODEX_HOME=str(HOME))
server = subprocess.Popen([CODEX, "app-server", "--listen", f"ws://127.0.0.1:{WSPORT}"], env=env, cwd=WSDIR,
                          stdout=open(ROOT / "server.out", "wb"), stderr=subprocess.STDOUT)
for _ in range(80):
    try:
        socket.create_connection(("127.0.0.1", WSPORT), 0.5).close(); break
    except OSError:
        time.sleep(0.25)
log(f"app-server up on ws://127.0.0.1:{WSPORT} (pid {server.pid})")


class WsClient:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), 10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(4096)
        head, self.buf = resp.split(b"\r\n\r\n", 1)
        assert b" 101 " in head.split(b"\r\n")[0], head
        self.next_id = 0; self.inbox = []

    def send(self, obj):
        data = json.dumps(obj).encode(); mask = os.urandom(4)
        if len(data) < 126:
            hdr = bytes([0x81, 0x80 | len(data)])
        elif len(data) < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + struct.pack(">H", len(data))
        else:
            hdr = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", len(data))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self, timeout):
        if not self.buf:
            self.s.settimeout(timeout)
            try:
                self.buf += self.s.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
        self.s.settimeout(30)
        b1, b2 = self._read(2)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        payload = self._read(n)
        return json.loads(payload) if b1 & 0x0F == 0x1 else None

    def request(self, method, params=None, timeout=30):
        self.next_id += 1; rid = self.next_id
        self.send({"id": rid, "method": method, "params": params or {}})
        end = time.time() + timeout
        while time.time() < end:
            m = self.recv(1.0)
            if m is None:
                continue
            if m.get("id") == rid and "method" not in m:
                return m
            self.inbox.append(m)
        raise TimeoutError(method)

    def drain(self, secs):
        end = time.time() + secs
        while time.time() < end:
            m = self.recv(0.5)
            if m:
                self.inbox.append(m)


def pane():
    return subprocess.run(T + ["capture-pane", "-p", "-J", "-t", "m"], capture_output=True, text=True).stdout
def shot(label, n=22):
    lines = [l for l in pane().splitlines() if l.strip()]
    print(f"===== TUI: {label}"); print("\n".join(lines[-n:]), flush=True)
def wait_pane(text, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if text in pane():
            return True
        time.sleep(0.5)
    return False


subprocess.run(T + ["kill-server"], capture_output=True)
subprocess.run(T + ["new-session", "-d", "-s", "m", "-x", "160", "-y", "45",
                    f"cd {WSDIR} && env CODEX_HOME={HOME} TERM=xterm-256color {CODEX} --remote ws://127.0.0.1:{WSPORT}; sleep 60"])
time.sleep(7); shot("TUI started with --remote", 14)
subprocess.run(T + ["send-keys", "-t", "m", "-l", "hello from the tui keyboard"]); time.sleep(0.5)
subprocess.run(T + ["send-keys", "-t", "m", "Enter"])
log(f"TUI first turn answered: {wait_pane('STUB-REPLY-1')}")

side = WsClient(WSPORT)
init = side.request("initialize", {"clientInfo": {"name": "ciel-runtime-exp", "title": "exp", "version": "0"},
                                   "capabilities": {"experimentalApi": True}})
log("sidecar initialize ok: " + str("result" in init))
side.send({"method": "initialized"})
loaded = side.request("thread/loaded/list", {})
log("thread/loaded/list: " + json.dumps(loaded)[:300])
data = (loaded.get("result") or {}).get("data") or []
tid = (data[0] if isinstance(data[0], str) else data[0].get("id")) if data else None
if not tid:
    lst = side.request("thread/list", {}); log("thread/list: " + json.dumps(lst)[:400])
    rows = (lst.get("result") or {}).get("data") or []
    tid = rows[0].get("id") if rows else None
log(f"thread id = {tid}")
r = side.request("thread/resume", {"threadId": tid, "excludeTurns": True}); log("thread/resume: " + json.dumps(r)[:200])

r = side.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "SIDECAR-TURN-START message"}]})
log("turn/start -> " + json.dumps(r)[:200])
log(f"TUI shows sidecar user text: {wait_pane('SIDECAR-TURN-START')}")
log(f"TUI shows reply to sidecar turn: {wait_pane('STUB-REPLY-2')}")
shot("after sidecar turn/start")

r = side.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "SLOW sidecar turn"}]})
turn_id = ((r.get("result") or {}).get("turn") or {}).get("id")
log(f"slow turn/start -> turn {turn_id}"); time.sleep(2)
r = side.request("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                "input": [{"type": "text", "text": "SIDECAR-STEER while busy"}]})
log("turn/steer -> " + json.dumps(r)[:240])
log(f"TUI shows steered text: {wait_pane('SIDECAR-STEER', 25)}")
side.drain(12)
shot("after steer", 26)
log("upstream saw user texts: " + json.dumps(SEEN))
kinds = sorted({m.get("method") for m in side.inbox if m.get("method")})
log("sidecar notifications: " + ", ".join(kinds)[:600])
subprocess.run(T + ["kill-server"], capture_output=True); server.terminate()
