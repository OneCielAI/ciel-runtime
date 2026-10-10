"""E2E: back up a running Ciel Codex session, lose its state, restore it, continue the same conversation.

Usage: python e2e_backup.py <label> <mode>      (mode: codex-remote | codex)
Env: E2E_SCRATCH (scratch root), E2E_MODELS_CACHE (models_cache.json to copy), E2E_REPO (Ciel source).

Isolated: scratch CIEL_RUNTIME_CONFIG_DIR, router port, CODEX_HOME with a test-only API-key auth.json,
workspace outside git, local stub upstream recording every /responses body. Inherited CIEL_RUNTIME_*,
CODEX_*, OPENAI_*, ANTHROPIC_*, CLAUDE_* are cleared. Input only via /ca/chat/notify.

Steps: launch 1 -> turn A ("remember ALPHA-7731") -> `backup create` while running -> turn B ->
stop -> wipe CODEX_HOME sessions/state, the Ciel workspace state and the work file -> `backup restore`
-> launch 2 with --continue -> turn C. Pass when turn C's request carries turn A's text (history restored).
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
LABEL, MODE = sys.argv[1], sys.argv[2]
RUN = HERE / LABEL
shutil.rmtree(RUN, ignore_errors=True)
SCRATCH = Path(os.environ["E2E_SCRATCH"]) / LABEL
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, HOME, SHOTS = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home", RUN / "shots"
for d in (RUN, CFG, WS, HOME, SHOTS):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "a", encoding="utf-8")
KEY = "e2e-backup-passphrase-test-only"
SECRET_MARK = "sk-e2e-local-test-only"


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


def sse(n: int, text: str) -> bytes:
    rid = f"resp_{n}"
    events = [
        {"type": "response.created", "response": {"id": rid}},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "message", "role": "assistant", "id": f"m{n}", "content": [{"type": "output_text", "text": text}]}},
        {"type": "response.completed",
         "response": {"id": rid, "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}},
    ]
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}"
        body = json.loads(raw)
        n = len(BODIES) + 1
        text = json.dumps(body.get("input") or body.get("messages") or [], ensure_ascii=False)
        row = {"n": n, "path": self.path, "launch": STATE["launch"], "has_alpha": "ALPHA-7731" in text,
               "has_beta": "BETA-4410" in text, "has_gamma": "GAMMA-5528" in text,
               "user_texts": [t for t in _user_texts(body)][-4:]}
        BODIES.append(row)
        with open(RUN / "stub-bodies.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        data = sse(n, f"STUB-REPLY-{n}")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _user_texts(body: dict):
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("role") == "user":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("text"):
                    yield part["text"][:120]


STATE = {"launch": 0}
stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER, APPSRV = free_port(), free_port()

(CFG / "config.json").write_text(json.dumps({"current_provider": "codex", "language": "en",
                                             "providers": {"codex": {"route_through_router": True, "current_model": "gpt-6.1-sol"}}}, indent=2))
(CFG / "log-level").write_text("INFO")
ws_key = str(WS).replace("\\", "\\\\")
(HOME / "config.toml").write_text(f'model_reasoning_effort = "medium"\n[projects."{ws_key}"]\ntrust_level = "trusted"\n', encoding="utf-8")
(HOME / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": SECRET_MARK}))
shutil.copy(os.environ["E2E_MODELS_CACHE"], HOME / "models_cache.json")
(WS / "work-notes.md").write_text("work file v1 ALPHA\n", encoding="utf-8")
log(f"run={RUN.name} mode={MODE} repo={REPO} stub={STUB} router={ROUTER}")


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))}
    env.update({"CIEL_RUNTIME_CONFIG_DIR": str(CFG), "CIEL_RUNTIME_ROUTER_PORT": str(ROUTER), "CODEX_HOME": str(HOME)})
    env.pop("CLAUDECODE", None)
    return env


clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))


def launch(n: int, extra: str) -> str:
    title = f"CIELE2E-BACKUP-{LABEL}-L{n}"
    script = RUN / f"launch{n}.cmd"
    script.write_text(
        "@echo off\n" + clear + "set CLAUDECODE=\n"
        f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CODEX_HOME={HOME}"\n'
        f'set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
        f'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}/backend-api/codex"\n'
        f'cd /d "{WS}"\n'
        f'python "{REPO / "ciel_runtime.py"}" cli --ca-runtime {MODE} --ca-no-update-check --ca-no-self-update-check {extra}\n'
        "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
    subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])
    return title


def shot(title: str, label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"), title, str(out)],
                       capture_output=True, text=True)
    log(f"shot {label}: {r.stdout.strip() or r.stderr.strip()[:200]}")


def http(method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
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
        return {"raw": raw[:300]}


def wait(pred, secs: float) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(1)
    return False


def turn(text: str, title: str, label: str) -> bool:
    before = len(BODIES)
    r = http("POST", "/ca/chat/notify", {"message": text, "channel": "e2e", "sender_id": "e2e-driver"})
    log(f"notify '{text[:40]}' -> {json.dumps(r)[:160]}")
    ok = wait(lambda: len(BODIES) > before, 120)
    time.sleep(6)
    shot(title, label)
    log(f"turn {label}: model request seen={ok} new_requests={len(BODIES) - before}")
    return ok


def ciel(*args: str) -> subprocess.CompletedProcess:
    env = clean_env()
    env["CIEL_RUNTIME_BACKUP_KEY"] = KEY
    # Never let the backup command see the real user home (~/.claude sign-in, ~/.claude.json).
    fake_home = SCRATCH / "user-home"
    (fake_home / ".claude").mkdir(parents=True, exist_ok=True)
    env.update({"USERPROFILE": str(fake_home), "HOME": str(fake_home), "CLAUDE_CONFIG_DIR": str(fake_home / ".claude")})
    r = subprocess.run([sys.executable, str(REPO / "ciel_runtime.py"), "cli", *args], cwd=str(WS), env=env,
                       capture_output=True, text=True, timeout=600)
    log(f"$ ciel-runtime {' '.join(args)}  (exit {r.returncode})\n{(r.stdout + r.stderr).strip()[:3000]}")
    return r


def teardown(tag: str) -> None:
    ps = ("$ports = @(" + str(ROUTER) + "," + str(APPSRV) + "); "
          "$pids = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ? { $ports -contains $_.LocalPort } | % { $_.OwningProcess }); "
          "$pids += @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid()) + " -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*" + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*' -or $_.CommandLine -like '*" + str(HOME) + "*') } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"killed $_\" }")
    rr = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    log(f"teardown {tag}: " + (rr.stdout.strip().replace("\n", " ") or rr.stderr.strip()[:200]))
    time.sleep(3)


def tree(path: Path) -> list[str]:
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()) if path.exists() else []


# ---- launch 1: conversation + backup while running ------------------------------------------
STATE["launch"] = 1
t1 = launch(1, "")
log(f"router up: {wait(lambda: 'raw' not in http('GET', '/health', timeout=2), 90)}")
time.sleep(25)
shot(t1, "L1-ready")
turn("Remember this code word: ALPHA-7731. Reply with one word.", t1, "L1-turnA")
created = ciel("backup", "create", "--label", "e2e-mid-session", "--json")
snapshot_id = json.loads(created.stdout)["id"] if created.returncode == 0 else ""
turn("Second message BETA-4410, still running after the backup.", t1, "L1-turnB-after-backup")
ciel("backup", "list")
ciel("backup", "verify", snapshot_id)
teardown("launch1")
blob_text = b""
import zlib  # noqa: E402

for p in (CFG / "backups" / "blobs").rglob("*"):
    if p.is_file():
        blob_text += zlib.decompress(p.read_bytes())
log(f"secret test key in plain backup chunks: {SECRET_MARK.encode() in blob_text}; passphrase in backup files: "
    f"{any(KEY.encode() in p.read_bytes() for p in (CFG / 'backups').rglob('*') if p.is_file())}")

# ---- loss ---------------------------------------------------------------------------------------
before = {"codex-home": tree(HOME), "ws": tree(WS)}
for item in ("sessions", "state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm", "auth.json", "history.jsonl"):
    target = HOME / item
    shutil.rmtree(target, ignore_errors=True) if target.is_dir() else target.unlink(missing_ok=True)
for ws_state in (CFG / "workspaces").glob("*"):
    shutil.rmtree(ws_state, ignore_errors=True)
(WS / "work-notes.md").write_text("damaged\n", encoding="utf-8")
log(f"after wipe: codex-home files={tree(HOME)} ws files={tree(WS)} workspaces={tree(CFG / 'workspaces')}")

# ---- restore + launch 2 --------------------------------------------------------------------------
ciel("backup", "restore", snapshot_id, "--json")
after = {"codex-home": tree(HOME), "ws": tree(WS)}
log(f"restored work-notes: {(WS / 'work-notes.md').read_text(encoding='utf-8').strip()!r}; auth.json back: {(HOME / 'auth.json').is_file()}")
log(f"codex-home files before loss vs after restore: missing={sorted(set(before['codex-home']) - set(after['codex-home']))}")
STATE["launch"] = 2
t2 = launch(2, "--continue")
log(f"router up (2): {wait(lambda: 'raw' not in http('GET', '/health', timeout=2), 90)}")
time.sleep(25)
shot(t2, "L2-ready-continued")
turn("Third message GAMMA-5528 after restore.", t2, "L2-turnC")
for b in BODIES:
    log(f"stub body n={b['n']} launch={b['launch']} has_alpha={b['has_alpha']} has_beta={b['has_beta']} has_gamma={b['has_gamma']} users={json.dumps(b['user_texts'], ensure_ascii=False)[:400]}")
# Codex also sends a title-generation request with only the newest message, so judge the
# conversation request: the launch-2 body that carries turn C's text and the channel history.
final = [b for b in BODIES if b["launch"] == 2 and b["has_gamma"] and any("external channel message" in t for t in b["user_texts"])]
log(f"RESULT history restored: {bool(final) and final[-1]['has_alpha']} (turn C request carries turn A); "
    f"turn B after backup present: {bool(final) and final[-1]['has_beta']} (expected False: it came after the snapshot)")
for f in CFG.rglob("router.log"):
    shutil.copy(f, RUN / f"router-{f.parent.name}.log")
teardown("launch2")
log("done")
