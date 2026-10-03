"""Which way makes channel turns and typed turns of a resumed thread run without approval prompts on a
`codex --remote` TUI (codex 0.160.0)?

V3: plain-TUI thread (no --yolo); the channel client resumes it with approvalPolicy/sandbox overrides
    BEFORE the TUI attaches; then the TUI resumes it.
V4: plain-TUI thread (no --yolo); TUI resumes first; channel turn/start carries approvalPolicy/sandboxPolicy.
V5: thread created by a plain TUI with --yolo (what the sandboxes have); no overrides anywhere.
Each: channel turn runs `echo RAN-OK-123`; then the same is typed in the TUI. Approval requests are declined.
"""
import json, os, re, secrets, shutil, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
src = (HERE / "auth_exp.py").read_text(encoding="utf-8")
g: dict = {"__file__": str(HERE / "auth_exp.py"), "__name__": "x"}
exec(src[:src.index("up = ThreadingHTTPServer")], g)
WsAuth, Up, REQ = g["WsAuth"], g["Up"], g["REQ"]
WindowsConPtySession, _vis = g["WindowsConPtySession"], g["_visible_terminal_text"]
up = ThreadingHTTPServer(("127.0.0.1", 0), Up); UP = up.server_address[1]
threading.Thread(target=up.serve_forever, daemon=True).start()
def binp(v): return str(HERE / f"codex-{v}/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
OLD, NEW = binp("01561"), binp("01600")
def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
base_env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CIEL_"))}

def screen(t): return _vis(t.output_tail())
def wait_screen(t, text, secs=20):
    end = time.time() + secs
    while time.time() < end:
        if text in screen(t):
            return True
        time.sleep(0.5)
    return False

def pump(side, secs, until_completed=True):
    """Collect messages; decline approval requests; return (completed, approvals, command outputs)."""
    approvals, outputs, completed = [], [], False
    end = time.time() + secs
    while time.time() < end:
        m = side.recv(0.5)
        if m is None:
            continue
        if "id" in m and "method" in m:
            approvals.append(m["method"])
            side.send({"id": m["id"], "result": {"decision": "decline"}})
        if m.get("method") == "item/completed":
            it = (m.get("params") or {}).get("item") or {}
            if it.get("type") == "commandExecution":
                outputs.append((it.get("status"), (it.get("aggregatedOutput") or "").strip()[:40]))
        if m.get("method") == "turn/completed":
            completed = True
            if until_completed:
                break
    return completed, approvals, outputs

def variant(label, *, legacy_yolo, resume_first, turn_override, port):
    root = Path(rf"C:\cxa\{label}"); shutil.rmtree(root, ignore_errors=True); root.mkdir(parents=True)
    home, ws = root / "h", root / "ws"; home.mkdir(); ws.mkdir()
    (home / "config.toml").write_text("\n".join([
        'model = "stub-model"', 'model_provider = "stub"', "check_for_update_on_startup = false",
        "[model_providers.stub]", 'name = "stub"', f'base_url = "http://127.0.0.1:{UP}/v1"',
        'wire_api = "responses"', "requires_openai_auth = false", f"[projects.'{ws}']", 'trust_level = "trusted"', ""]),
        encoding="utf-8")
    token = secrets.token_hex(16); (root / "tok").write_text(token, encoding="utf-8")
    env = dict(base_env, CODEX_HOME=str(home), CXTOK=token)
    os.chdir(ws)
    t = WindowsConPtySession([OLD, *(["--yolo"] if legacy_yolo else [])], env, log=lambda a, b: None, mirror_output=False, forward_stdin=False)
    time.sleep(8); t.write(b"LEGACY first"); time.sleep(0.8); t.write(b"\r")
    ok = wait_screen(t, "to [LEGACY first", 20); t.close(); time.sleep(2)
    import sqlite3
    with sqlite3.connect(sorted(home.glob("state_*.sqlite"))[-1]) as c:
        tid, sandbox_policy, approval_mode = c.execute("select id, sandbox_policy, approval_mode from threads").fetchone()
    log(f"{label} legacy thread {tid} replied={ok} state sandbox_policy={sandbox_policy} approval_mode={approval_mode}")
    server = subprocess.Popen([NEW, "app-server", "--listen", f"ws://127.0.0.1:{port}", "--ws-auth", "capability-token",
                               "--ws-token-file", str(root / "tok")], env=env, cwd=ws,
                              stdout=open(root / "server.out", "wb"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close(); break
        except OSError:
            time.sleep(0.25)
    side = WsAuth(port, token)
    side.request("initialize", {"clientInfo": {"name": "ciel-approval-exp", "title": "exp", "version": "0"}, "capabilities": {"experimentalApi": True}})
    side.send({"method": "initialized"})
    overrides = {"approvalPolicy": "never", "sandbox": "danger-full-access"}
    if resume_first:
        r = side.request("thread/resume", dict({"threadId": tid}, **overrides))
        res = r.get("result") or {}
        log(f"{label} channel resume first ok={'result' in r} approvalPolicy={res.get('approvalPolicy')} sandbox={json.dumps(res.get('sandbox'))[:60]}")
    tui = WindowsConPtySession([NEW, "--remote", f"ws://127.0.0.1:{port}", "--remote-auth-token-env", "CXTOK", "resume", tid],
                               env, log=lambda a, b: None, mirror_output=False, forward_stdin=False)
    log(f"{label} TUI shows history={wait_screen(tui, 'LEGACY first', 20)} perm-line={re.findall(r'permissions: [A-Za-z ]{1,24}', screen(tui))[:1]}")
    if not resume_first:
        r = side.request("thread/resume", {"threadId": tid})
        log(f"{label} channel resume after TUI ok={'result' in r} approvalPolicy={(r.get('result') or {}).get('approvalPolicy')}")
    params = {"threadId": tid, "input": [{"type": "text", "text": "RUN-CMD channel"}]}
    if turn_override:
        params.update(approvalPolicy="never", sandboxPolicy={"type": "dangerFullAccess"})
    r = side.request("turn/start", params); log(f"{label} channel turn/start ok={'result' in r} err={r.get('error')}")
    completed, approvals, outputs = pump(side, 30)
    log(f"{label} CHANNEL turn: completed={completed} approval_requests={approvals} commands={outputs}")
    time.sleep(2)
    tui.write(b"RUN-CMD typed"); time.sleep(0.8); tui.write(b"\r")
    completed, approvals, outputs = pump(side, 30)
    log(f"{label} TYPED turn: completed={completed} approval_requests={approvals} commands={outputs} tui_prompt_shown={'Would you like to run' in screen(tui)[-1500:]}")
    (root / "screen.txt").write_text(screen(tui), encoding="utf-8")
    tui.close(); server.terminate(); server.wait(10)

variant("V3", legacy_yolo=False, resume_first=True, turn_override=False, port=47841)
variant("V4", legacy_yolo=False, resume_first=False, turn_override=True, port=47842)
variant("V5", legacy_yolo=True, resume_first=False, turn_override=False, port=47843)
log("done")
