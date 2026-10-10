"""E2E: restore e2e-1's snapshot to a different layout ("another machine/user"), continue it there,
then exercise the automatic triggers and the menu.

Usage: python e2e_migrate_triggers.py <snapshot id>
Env: E2E_SCRATCH, E2E_MODELS_CACHE, E2E_REPO.

New layout under <scratch>/e2e-3-migrate: cfg2 (Ciel config), ws2 (work folder), codex2 (CODEX_HOME),
home2 (user home). Source snapshot: e2e-1's local target (<scratch>/e2e-1-.../cfg/backups).
Steps:
 1 restore with --to-cwd/--codex-home/--ciel-dir/--home/--claude-dir (no live session there)
 2 schedule: on_turn_end (min 0), before_restart, on_session_end on
 3 launch `cli --ca-runtime codex --continue` in ws2 -> turn C: request must carry turn A (ALPHA) from
   the original folder's conversation -> a turn-end snapshot appears
 4 router MCP tools/call session_backup create -> an mcp snapshot
 5 `ciel-runtime restart-session` -> a pre-restart snapshot, Codex comes back, turn D works
 6 /quit typed into the TUI -> a session-end snapshot
 7 menu: row 18 and the Session backup panel (screenshots)
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
SNAPSHOT = sys.argv[1]
SCRATCH = Path(os.environ["E2E_SCRATCH"])
SOURCE_BACKUPS = SCRATCH / "e2e-1-remote-backup-restore" / "cfg" / "backups"
LABEL = "e2e-3-migrate-triggers"
RUN = HERE / LABEL
shutil.rmtree(RUN, ignore_errors=True)
BASE = SCRATCH / LABEL
shutil.rmtree(BASE, ignore_errors=True)
CFG, WS, CODEX, HOME, SHOTS = BASE / "cfg2", BASE / "ws2", BASE / "codex2", BASE / "home2", RUN / "shots"
for d in (RUN, CFG, WS, CODEX, HOME / ".claude", SHOTS):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "a", encoding="utf-8")
KEY = "e2e-backup-passphrase-test-only"


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


BODIES: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        n = len(BODIES) + 1
        text = json.dumps(body.get("input") or [], ensure_ascii=False)
        row = {"n": n, "alpha": "ALPHA-7731" in text, "gamma": "GAMMA-9001" in text, "delta": "DELTA-3302" in text,
               "conversation": "external channel message" in text}
        BODIES.append(row)
        with open(RUN / "stub-bodies.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        rid = f"resp_{n}"
        events = [{"type": "response.created", "response": {"id": rid}},
                  {"type": "response.output_item.done", "output_index": 0,
                   "item": {"type": "message", "role": "assistant", "id": f"m{n}", "content": [{"type": "output_text", "text": f"STUB-REPLY-{n}"}]}},
                  {"type": "response.completed", "response": {"id": rid, "status": "completed",
                                                              "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}]
        data = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER = free_port()
(CFG / "log-level").write_text("INFO")
TITLE = "CIELE2E-BACKUP-e2e-3-migrate"


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))}
    e.update({"CIEL_RUNTIME_CONFIG_DIR": str(CFG), "CIEL_RUNTIME_ROUTER_PORT": str(ROUTER), "CODEX_HOME": str(CODEX),
              "CIEL_RUNTIME_BACKUP_KEY": KEY, "USERPROFILE": str(HOME), "HOME": str(HOME),
              "CLAUDE_CONFIG_DIR": str(HOME / ".claude")})
    e.pop("CLAUDECODE", None)
    return e


def ciel(*args: str, show: int = 1200) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", *args], cwd=str(WS), env=env(),
                       capture_output=True, text=True, timeout=900)
    log(f"$ ciel-runtime {' '.join(args)}  (exit {r.returncode})\n{(r.stdout + r.stderr).strip()[:show]}")
    return r


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:
        return {"raw": f"{type(e).__name__}: {e}"}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw[:500]}


def wait(pred, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(1)
    return False


def shot(label: str, title: str = TITLE) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), title, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def keys(text: str, title: str = TITLE) -> None:
    ps = f"$w = New-Object -ComObject WScript.Shell; if ($w.AppActivate('{title}')) {{ Start-Sleep -Milliseconds 400; $w.SendKeys('{text}') ; 'sent' }} else {{ 'no window' }}"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"keys {text!r}: {r.stdout.strip()}")


def snapshots() -> list[dict]:
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", "backup", "list", "--json"], cwd=str(WS), env=env(),
                       capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return []


def triggers() -> list[str]:
    return [row.get("trigger") for row in snapshots()]


def turn(text: str, label: str) -> None:
    before = len(BODIES)
    r = http("POST", "/ca/chat/notify", {"message": text, "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify '{text[:40]}' -> {json.dumps(r)[:140]}")
    log(f"turn {label}: model request seen={wait(lambda: len(BODIES) > before, 120)}")
    time.sleep(6)
    shot(label)


clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))


def launch(script_name: str, title: str, args: str) -> None:
    script = RUN / script_name
    script.write_text(
        "@echo off\n" + clear + "set CLAUDECODE=\n"
        f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CODEX_HOME={CODEX}"\n'
        f'set "CIEL_RUNTIME_BACKUP_KEY={KEY}"\nset "USERPROFILE={HOME}"\nset "HOME={HOME}"\nset "CLAUDE_CONFIG_DIR={HOME / ".claude"}"\n'
        f'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}/backend-api/codex"\n'
        f'cd /d "{WS}"\n'
        f'python "{REPO / "ciel_runtime.py"}" cli {args}\n'
        "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
    subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def teardown() -> None:
    ps = ("$ports = @(" + str(ROUTER) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*' -or $_.CommandLine -like '*" + str(CODEX) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    rr = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("teardown: " + (rr.stdout.strip().replace("\n", " ") or rr.stderr.strip()[:200]))


# 1 restore into the new layout -----------------------------------------------------------------
ciel("backup", "restore", SNAPSHOT, "--target", str(SOURCE_BACKUPS), "--json", "--to-cwd", str(WS), "--codex-home", str(CODEX),
     "--ciel-dir", str(CFG), "--home", str(HOME), "--claude-dir", str(HOME / ".claude"), show=2500)
(CODEX / "models_cache.json").exists() or shutil.copy(os.environ["E2E_MODELS_CACHE"], CODEX / "models_cache.json")
cfg = json.loads((CFG / "config.json").read_text(encoding="utf-8"))
log(f"restored config.json current_provider={cfg.get('current_provider')}; work file: {(WS / 'work-notes.md').read_text(encoding='utf-8').strip()!r}")
log("codex config.toml after remap:\n" + (CODEX / "config.toml").read_text(encoding="utf-8").replace(str(BASE), "<base>"))
import sqlite3  # noqa: E402

db = sqlite3.connect(CODEX / "state_5.sqlite")
for row in db.execute("SELECT id, cwd, rollout_path FROM threads"):
    # Codex stores cwd in Windows extended-length form (\\?\C:\...).
    plain = row[1][4:] if row[1].startswith("\\\\?\\") else row[1]
    log(f"threads row: id={row[0]} cwd={row[1].replace(str(BASE), '<base>')} cwd_is_ws2={Path(plain).resolve() == WS.resolve()} "
        f"rollout_exists={Path(row[2][4:] if row[2].startswith(chr(92) * 2 + '?') else row[2]).is_file()}")
db.close()

# 2 triggers on --------------------------------------------------------------------------------
ciel("backup", "schedule", "set", "on_turn_end=on", "min_interval_minutes=0", "before_restart=on", "on_session_end=on")

# 3 continue there -----------------------------------------------------------------------------
launch("launch-continue.cmd", TITLE, "--ca-runtime codex --ca-no-update-check --ca-no-self-update-check --continue")
log(f"router up: {wait(lambda: 'raw' not in http('GET', '/health', timeout=2), 90)}")
time.sleep(25)
shot("continued-ready")
turn("Message GAMMA-9001 on the new machine.", "turnC")
conversation = [b for b in BODIES if b["gamma"] and b["conversation"]]
log(f"RESULT continued conversation carries ALPHA from the original folder: {bool(conversation) and conversation[-1]['alpha']}")
log(f"turn-end snapshot: {wait(lambda: 'turn-end' in triggers(), 120)} triggers={triggers()}")

# 4 MCP tool --------------------------------------------------------------------------------------
mcp = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                               "params": {"name": "session_backup", "arguments": {"action": "create"}}}, timeout=900)
log(f"mcp tools/call session_backup create -> {json.dumps(mcp)[:700]}")
status = http("POST", "/ca/mcp", {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                  "params": {"name": "session_backup", "arguments": {"action": "status"}}}, timeout=60)
log(f"mcp status -> {json.dumps(status)[:900]}")

# 5 restart ----------------------------------------------------------------------------------------
ciel("restart-session", "--reason", "e2e backup before restart")
log(f"pre-restart snapshot: {wait(lambda: 'pre-restart' in triggers(), 240)}")
time.sleep(25)
shot("after-restart")
turn("Message DELTA-3302 after the restart.", "turnD")

# 6 session end ------------------------------------------------------------------------------------
keys("/quit{ENTER}")
log(f"session-end snapshot: {wait(lambda: 'session-end' in triggers(), 240)}")
time.sleep(3)
shot("after-quit")
log("snapshots: " + json.dumps([{k: r.get(k) for k in ("id", "trigger", "files", "secrets")} for r in snapshots()]))
teardown()

# 7 menu --------------------------------------------------------------------------------------------
MENU = "CIELE2E-BACKUP-e2e-3-menu"
launch("launch-menu.cmd", MENU, "--ca-no-update-check --ca-no-self-update-check")
time.sleep(12)
shot("menu", MENU)
keys("{UP}", MENU)
keys("{UP}", MENU)
time.sleep(1)
shot("menu-row-18", MENU)
keys("{ENTER}", MENU)
time.sleep(2)
shot("backup-panel", MENU)
keys("{DOWN}", MENU)
keys("{DOWN}", MENU)
keys("{ENTER}", MENU)
time.sleep(2)
shot("panel-interval-changed", MENU)
log(f"schedule after the menu change: {json.dumps(json.loads(ciel('backup', 'schedule').stdout)['schedule'])}")
teardown()
log("done")
