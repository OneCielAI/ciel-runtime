"""Can a transcript written by a plain (embedded) Codex TUI continue on an app-server, and back?

A: codex 0.156.1 plain TUI writes a conversation (legacy).
B: codex 0.160.0 app-server on the same CODEX_HOME: thread/list, thread/read, thread/resume, turn/start.
C: codex 0.160.0 `--remote ws://.. resume <id>` TUI shows history; sidecar turn/start shows in it.
D: plain TUI 0.160.0 `codex resume <id>` after the server stopped continues the same thread.
E: rollout copied into a fresh CODEX_HOME without a state DB (migrated transcript): app-server thread/resume.
"""
import json, os, shutil, socket, sqlite3, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = Path(r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\01\research\codex-input-transport\evidence")
sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
from ciel_runtime_support.windows_conpty import WindowsConPtySession, _visible_terminal_text  # noqa: E402
from ciel_runtime_support.codex_session_repository import CodexSessionRepository  # noqa: E402

shared = (EV / "remote_tui_exp.py").read_text(encoding="utf-8")
ns: dict = {"__builtins__": __builtins__}
ns.update(json=json, time=time, BaseHTTPRequestHandler=__import__("http.server").server.BaseHTTPRequestHandler,
          socket=socket, os=os, struct=__import__("struct"), base64=__import__("base64"))
exec(shared[shared.index("SEEN = []"):shared.index("up = ThreadingHTTPServer")], ns)
exec(shared[shared.index("class WsClient:"):shared.index("def pane():")], ns)
Up, WsClient = ns["Up"], ns["WsClient"]

HISTORY = []  # every user text the upstream received, per request
_orig_post = Up.do_POST
def do_post(self):
    raw = self.rfile.read(int(self.headers.get("content-length") or 0))
    body = json.loads(raw or b"{}")
    HISTORY.append([c.get("text", "")[:40] for it in body.get("input", []) if isinstance(it, dict) and it.get("role") == "user"
                    for c in (it.get("content") or []) if isinstance(c, dict) and "<" not in c.get("text", "")[:1]])
    import io
    self.rfile = io.BytesIO(raw)
    _orig_post(self)
Up.do_POST = do_post

def bin_for(v):
    return str(HERE / f"codex-{v}/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
OLD, NEW = bin_for("01561"), bin_for("01600")
ROOT = Path(os.environ.get("COMPAT_ROOT") or HERE / "compat-exp"); shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir()
HOME, WSDIR = ROOT / "codex-home", ROOT / "ws"; HOME.mkdir(); WSDIR.mkdir()
WSPORT = 47833
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

up = ThreadingHTTPServer(("127.0.0.1", 0), Up); UP = up.server_address[1]
threading.Thread(target=up.serve_forever, daemon=True).start()
def write_config(home):
    (home / "config.toml").write_text("\n".join([
        'model = "stub-model"', 'model_provider = "stub"', "check_for_update_on_startup = false",
        "[model_providers.stub]", 'name = "stub"',
        f'base_url = "http://127.0.0.1:{UP}/v1"', 'wire_api = "responses"', "requires_openai_auth = false",
        f"[projects.'{WSDIR}']", 'trust_level = "trusted"', ""]), encoding="utf-8")
write_config(HOME)
base_env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CIEL_"))}
env = dict(base_env, CODEX_HOME=str(HOME))
os.chdir(WSDIR)

def tui(cmd, label):
    t = WindowsConPtySession(cmd, env, log=lambda lvl, msg: None, mirror_output=False, forward_stdin=False)
    log(f"{label} TUI pid {t.pid}: {' '.join(cmd[1:])}")
    return t
def screen(t): return _visible_terminal_text(t.output_tail())
def wait_screen(t, text, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if text in screen(t):
            return True
        time.sleep(0.5)
    return False
def dump(t, label):
    s = screen(t); (ROOT / f"screen-{label}.txt").write_text(s, encoding="utf-8")
    print(f"===== screen {label}\n{s[-900:]}", flush=True)
def threads_rows(home):
    db = sorted(home.glob("state_*.sqlite"))
    if not db:
        return "no state db"
    with sqlite3.connect(db[-1]) as c:
        return c.execute("select id, source, cwd, rollout_path, model_provider from threads").fetchall()
def rollouts(home):
    return sorted(str(p.relative_to(home)) for p in (home / "sessions").rglob("*.jsonl")) if (home / "sessions").is_dir() else []
def type_prompt(t, text):
    t.write(text.encode()); time.sleep(0.8); t.write(b"\r")
def start_server(home):
    srv = subprocess.Popen([NEW, "app-server", "--listen", f"ws://127.0.0.1:{WSPORT}"], env=dict(base_env, CODEX_HOME=str(home)),
                           cwd=WSDIR, stdout=open(ROOT / f"server-{home.name}.out", "ab"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", WSPORT), 0.5).close(); break
        except OSError:
            time.sleep(0.25)
    c = WsClient(WSPORT)
    r = c.request("initialize", {"clientInfo": {"name": "ciel-compat-exp", "title": "exp", "version": "0"},
                                 "capabilities": {"experimentalApi": True}})
    c.send({"method": "initialized"})
    log(f"app-server up pid {srv.pid} initialize ok={'result' in r}")
    return srv, c
def wait_turn_completed(c, secs=40):
    end = time.time() + secs
    while time.time() < end:
        for m in list(c.inbox):
            if m.get("method") == "turn/completed":
                c.inbox.clear(); return True
        m = c.recv(1.0)
        if m is not None:
            c.inbox.append(m)
    return False

# A ---------------------------------------------------------------------------
t = tui([OLD], "A plain 0.156.1")
time.sleep(8); type_prompt(t, "LEGACY-ONE first prompt")
log(f"A reply shown: {wait_screen(t, 'STUB-REPLY-1')}"); dump(t, "A"); t.close(); time.sleep(2)
rows = threads_rows(HOME); log(f"A threads: {rows}"); log(f"A rollouts: {rollouts(HOME)}")
tid = rows[0][0]

# B ---------------------------------------------------------------------------
srv, c = start_server(HOME)
r = c.request("thread/list", {}); log("B thread/list: " + json.dumps(r)[:600])
r = c.request("thread/read", {"threadId": tid, "includeTurns": True})
items = [i.get("type") + ":" + json.dumps(i.get("content") or i.get("text") or "")[:50]
         for turn in ((r.get("result") or {}).get("thread") or {}).get("turns") or [] for i in turn.get("items") or []]
log(f"B thread/read ok={'result' in r} items={items} err={r.get('error')}")
r = c.request("thread/resume", {"threadId": tid}); log(f"B thread/resume ok={'result' in r} err={r.get('error')}")
r = c.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "APPSERVER-TWO via turn/start"}]})
log(f"B turn/start ok={'result' in r} completed={wait_turn_completed(c)}")
log(f"B upstream user history of that request: {HISTORY[-1]}")
log(f"B rollouts: {rollouts(HOME)}"); log(f"B threads: {threads_rows(HOME)}")

# C ---------------------------------------------------------------------------
t = tui([NEW, "--remote", f"ws://127.0.0.1:{WSPORT}", "resume", tid], "C remote resume")
time.sleep(10)
log(f"C TUI shows LEGACY-ONE={wait_screen(t, 'LEGACY-ONE', 5)} APPSERVER-TWO={wait_screen(t, 'APPSERVER-TWO', 5)}")
r = c.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "REMOTE-THREE sidecar"}]})
log(f"C sidecar turn/start ok={'result' in r} completed={wait_turn_completed(c)}")
log(f"C TUI shows REMOTE-THREE={wait_screen(t, 'REMOTE-THREE', 20)} reply={wait_screen(t, 'to [REMOTE-THREE', 20)}")
log(f"C upstream history: {HISTORY[-1]}")
dump(t, "C"); t.close(); time.sleep(1); srv.terminate(); srv.wait(10); time.sleep(1)
log(f"C rollouts: {rollouts(HOME)}"); log(f"C threads: {threads_rows(HOME)}")
log("C ciel resumable(cwd=ws): " + str([x["id"] for x in CodexSessionRepository(sorted(HOME.glob('state_*.sqlite'))[-1], lambda a, b: None).resumable(cwd=WSDIR)]))

# D ---------------------------------------------------------------------------
t = tui([NEW, *os.environ.get("COMPAT_D_ARGS", "").split(), "resume", tid], "D plain resume")
time.sleep(10); log(f"D TUI shows REMOTE-THREE={wait_screen(t, 'REMOTE-THREE', 5)}")
type_prompt(t, "PLAIN-FOUR after server")
log(f"D reply shown={wait_screen(t, 'to [PLAIN-FOUR', 30)}"); log(f"D upstream history: {HISTORY[-1]}")
dump(t, "D"); t.close(); time.sleep(2)
log(f"D rollouts: {rollouts(HOME)}"); log(f"D threads: {threads_rows(HOME)}")

# E ---------------------------------------------------------------------------
HOME2 = ROOT / "codex-home2"; HOME2.mkdir(); write_config(HOME2)
for p in (HOME / "sessions").rglob("*.jsonl"):
    dst = HOME2 / p.relative_to(HOME); dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dst)
srv, c = start_server(HOME2)
r = c.request("thread/resume", {"threadId": tid}); log(f"E thread/resume (no state db) ok={'result' in r} err={r.get('error')}")
r = c.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "MIGRATED-FIVE"}]})
log(f"E turn/start ok={'result' in r} completed={wait_turn_completed(c)} history={HISTORY[-1]}")
srv.terminate(); srv.wait(10)
log(f"E threads: {threads_rows(HOME2)}")
log("done")
