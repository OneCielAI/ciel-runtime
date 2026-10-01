"""Windows variant: `codex --remote` TUI hosted in ciel's ConPTY wrapper; a second client sends turn/start and turn/steer."""
import json, os, shutil, socket, subprocess, sys, threading, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, r"C:\Users\djlov\ciel-runtime")
from ciel_runtime_support.windows_conpty import WindowsConPtySession, _visible_terminal_text  # noqa: E402

# Reuse the stub upstream and the websocket client from the Linux experiment without running it.
shared = (HERE / "remote_tui_exp.py").read_text(encoding="utf-8")
ns: dict = {"__builtins__": __builtins__}
exec(shared[shared.index("SEEN = []"):shared.index("up = ThreadingHTTPServer")], ns.update(json=json, time=time, BaseHTTPRequestHandler=__import__("http.server").server.BaseHTTPRequestHandler) or ns)  #

exec(shared[shared.index("class WsClient:"):shared.index("def pane():")], ns.update(socket=socket, os=os, struct=__import__("struct"), base64=__import__("base64")) or ns)  #

Up, SEEN, WsClient = ns["Up"], ns["SEEN"], ns["WsClient"]
from http.server import ThreadingHTTPServer  # noqa: E402

ROOT = HERE / "cxexp-win"; shutil.rmtree(ROOT, ignore_errors=True); ROOT.mkdir()
HOME, WSDIR = ROOT / "codex-home", ROOT / "ws"; HOME.mkdir(); WSDIR.mkdir()
CODEX = str(HERE / "codex1593/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
WSPORT = 47812
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

up = ThreadingHTTPServer(("127.0.0.1", 0), Up); UP = up.server_address[1]
threading.Thread(target=up.serve_forever, daemon=True).start()
(HOME / "config.toml").write_text("\n".join([
    'model = "stub-model"', 'model_provider = "stub"', "[model_providers.stub]", 'name = "stub"',
    f'base_url = "http://127.0.0.1:{UP}/v1"', 'wire_api = "responses"', "requires_openai_auth = false",
    f"[projects.'{WSDIR}']", 'trust_level = "trusted"', ""]), encoding="utf-8")
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

os.chdir(WSDIR)
TUI = WindowsConPtySession([CODEX, "--remote", f"ws://127.0.0.1:{WSPORT}"], env, log=lambda lvl, msg: None,
                           mirror_output=False, forward_stdin=False)
log(f"TUI in ConPTY pid {TUI.pid}")
def screen(): return _visible_terminal_text(TUI.output_tail())
def wait_screen(text, secs=40):
    end = time.time() + secs
    while time.time() < end:
        if text in screen():
            return True
        time.sleep(0.5)
    return False
def shot(label):
    s = screen(); print(f"===== TUI screen tail: {label}\n{s[-700:]}", flush=True)

time.sleep(8); shot("started")
TUI.write(b"hello from the tui keyboard"); time.sleep(0.8); TUI.write(b"\r")
log(f"TUI first turn answered: {wait_screen('STUB-REPLY-1')}")

side = WsClient(WSPORT)
init = side.request("initialize", {"clientInfo": {"name": "ciel-runtime-exp", "title": "exp", "version": "0"},
                                   "capabilities": {"experimentalApi": True}})
log("sidecar initialize ok: " + str("result" in init)); side.send({"method": "initialized"})
loaded = side.request("thread/loaded/list", {}); log("thread/loaded/list: " + json.dumps(loaded)[:240])
tid = ((loaded.get("result") or {}).get("data") or [None])[0]
log("thread/resume ok: " + str("result" in side.request("thread/resume", {"threadId": tid, "excludeTurns": True})))
r = side.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "SIDECAR-TURN-START message"}]})
log("turn/start ok: " + str("result" in r))
log(f"TUI shows sidecar user text: {wait_screen('SIDECAR-TURN-START')}")
log(f"TUI shows reply to it: {wait_screen('to [SIDECAR-TURN-START')}")
r = side.request("turn/start", {"threadId": tid, "input": [{"type": "text", "text": "SLOW sidecar turn"}]})
turn_id = ((r.get("result") or {}).get("turn") or {}).get("id"); time.sleep(2)
r = side.request("turn/steer", {"threadId": tid, "expectedTurnId": turn_id,
                                "input": [{"type": "text", "text": "SIDECAR-STEER while busy"}]})
log("turn/steer -> " + json.dumps(r)[:160])
log(f"TUI shows steered text: {wait_screen('SIDECAR-STEER', 25)}")
log(f"TUI shows reply to steer: {wait_screen('to [SIDECAR-STEER', 25)}")
shot("final")
log("upstream saw user texts: " + json.dumps(SEEN))
TUI.close(); server.terminate()
