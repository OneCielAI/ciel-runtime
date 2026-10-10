"""E2E (rerun of e2e-3 steps 6-7 alone): session-end backup and the Session backup menu panel.

Uses the e2e-3 layout (cfg2/ws2/codex2/home2) left by e2e_migrate_triggers.py; that run's last steps were
disturbed by a stale driver of an earlier, stopped attempt that tore down the same paths.
"""
from __future__ import annotations

import json
import os
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
BASE = Path(os.environ["E2E_SCRATCH"]) / "e2e-3-migrate-triggers"
CFG, WS, CODEX, HOME = BASE / "cfg2", BASE / "ws2", BASE / "codex2", BASE / "home2"
RUN = HERE / "e2e-4-session-end-menu"
RUN.mkdir(exist_ok=True)
SHOTS = RUN / "shots"
SHOTS.mkdir(exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
KEY = "e2e-backup-passphrase-test-only"
TITLE, MENU = "CIELE2E-BACKUP-e2e-4-session", "CIELE2E-BACKUP-e2e-4-menu"


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


SEEN: list[int] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        n = len(SEEN) + 1
        SEEN.append(n)
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


def env() -> dict[str, str]:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))}
    e.update({"CIEL_RUNTIME_CONFIG_DIR": str(CFG), "CIEL_RUNTIME_ROUTER_PORT": str(ROUTER), "CODEX_HOME": str(CODEX),
              "CIEL_RUNTIME_BACKUP_KEY": KEY, "USERPROFILE": str(HOME), "HOME": str(HOME), "CLAUDE_CONFIG_DIR": str(HOME / ".claude")})
    return e


def ciel(*args: str) -> subprocess.CompletedProcess:
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", *args], cwd=str(WS), env=env(),
                       capture_output=True, text=True, timeout=600)
    log(f"$ ciel-runtime {' '.join(args)}  (exit {r.returncode})\n{(r.stdout + r.stderr).strip()[:1200]}")
    return r


def triggers() -> list[str]:
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", "backup", "list", "--json"], cwd=str(WS), env=env(),
                       capture_output=True, text=True, timeout=300)
    try:
        return [row.get("trigger") for row in json.loads(r.stdout)]
    except ValueError:
        return []


def http(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return {"raw": f"{type(e).__name__}: {e}"}


def wait(pred, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(1)
    return False


def shot(label: str, title: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), title, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def keys(text: str, title: str) -> None:
    ps = f"$w = New-Object -ComObject WScript.Shell; if ($w.AppActivate('{title}')) {{ Start-Sleep -Milliseconds 500; $w.SendKeys('{text}') ; 'sent' }} else {{ 'no window' }}"
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"keys {text!r} -> {title}: {r.stdout.strip()}")


clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))


def launch(name: str, title: str, args: str) -> None:
    script = RUN / name
    script.write_text(
        "@echo off\n" + clear + "set CLAUDECODE=\n"
        f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CODEX_HOME={CODEX}"\n'
        f'set "CIEL_RUNTIME_BACKUP_KEY={KEY}"\nset "USERPROFILE={HOME}"\nset "HOME={HOME}"\nset "CLAUDE_CONFIG_DIR={HOME / ".claude"}"\n'
        f'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}/backend-api/codex"\n'
        f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli {args}\necho LAUNCHER-EXIT=%ERRORLEVEL%\n', encoding="utf-8")
    subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def teardown() -> None:
    ps = ("$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $_.LocalPort -eq " + str(ROUTER) + " } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*' -or $_.CommandLine -like '*" + str(CODEX) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    rr = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log("teardown: " + (rr.stdout.strip().replace("\n", " ") or rr.stderr.strip()[:200]))


before = triggers()
log(f"snapshots before: {before}")
MENU_ONLY = bool(os.environ.get("E2E_MENU_ONLY"))
if not MENU_ONLY:
    launch("launch-session.cmd", TITLE, "--ca-runtime codex --ca-no-update-check --ca-no-self-update-check --continue")
    log(f"router up: {wait(lambda: 'raw' not in http('GET', '/health'), 90)}")
    time.sleep(25)
    r = http("POST", "/ca/chat/notify", {"message": "Message EPSILON-1209 before quitting.", "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify -> {json.dumps(r)[:120]}; model request: {wait(lambda: len(SEEN) > 0, 120)}")
    time.sleep(8)
    shot("before-quit", TITLE)
    # Typed text goes through the desktop's IME (Korean here turned "/quit" into Hangul), so quit with Ctrl+C twice.
    keys("^c", TITLE)
    time.sleep(1)
    keys("^c", TITLE)
    log(f"session-end snapshot: {wait(lambda: triggers().count('session-end') > before.count('session-end'), 300)}")
    time.sleep(3)
    shot("after-quit", TITLE)
    log(f"snapshots after: {triggers()}")
    teardown()

launch("launch-menu.cmd", MENU, "--ca-menu --ca-no-update-check --ca-no-self-update-check")
time.sleep(15)
shot("menu", MENU)
keys("{DOWN 9}", MENU)  # the menu opens on 9. Launch; row 18 is nine rows down
time.sleep(1)
shot("menu-row-18", MENU)
keys("{ENTER}", MENU)
time.sleep(3)
shot("backup-panel", MENU)
keys("{DOWN}", MENU)
time.sleep(1)
keys("{ENTER}", MENU)
time.sleep(3)
shot("panel-schedule-toggled", MENU)
log("schedule after the menu change: " + json.dumps(json.loads(ciel("backup", "schedule").stdout)["schedule"]))
teardown()
log("done")
