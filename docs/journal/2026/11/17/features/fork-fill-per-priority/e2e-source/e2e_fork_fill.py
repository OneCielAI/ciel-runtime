"""E2E: every priority fills to its own limit, a parked worker resumes when its wait clears,
the periodic pump works during a long main tool call, and the gate check runs while Work
forks run with side intake off.

Usage: python e2e_fork_fill.py <source snapshot> <out dir>

Real ciel-runtime launcher (`ciel_runtime.py cli`) and real claude.exe. The model is a LOCAL
STUB in this process (Anthropic /v1/messages), scripted by the Work assignment each fork
receives. No real configuration, provider or API key is read, copied or used: the scratch
config is written from nothing and its only provider points at the stub with a dummy key.
Isolated: scratch under D:\\Temp (screenshots carry no account path), HOME/USERPROFILE =
scratch home, CIEL_RUNTIME_TEST_ISOLATED=1 with a scratch CIEL_RUNTIME_CONFIG_DIR, every
inherited CIEL_RUNTIME_/ANTHROPIC_/CLAUDE_/CODEX_/OPENAI_ variable cleared, own router port.
Live sessions are never touched. Every process naming the scratch folder is stopped at the
end, and the scratch folder is deleted.

Board (seeded before launch, all goals executing), limits P1 3, P2 2, P3 1, P4 1, P5 1:
  P1: "P1 park" (asks a question, parks), "P1 long a", "P1 long b", "P1 refill" (oldest, waits)
  P2: "P2 long a", "P2 long b"; P3, P4, P5: one long item each.
Steps:
  1 8 Work forks run at once (3+2+1+1+1); the 9th item waits.
  2 the gate check's host round is recorded while forks run, side intake off.
  3 "P1 park" parks -> "P1 refill" takes the P1 slot and finishes.
  4 waiting_on is recorded on "P1 park" while parked (REST).
  5 main starts one long Bash call; the wait is cleared during it, so no turn boundary and no
    idle refresh can wake the pump -> the same fork (same agent id, same history) resumes.
  6 per-priority diagnosis on REST, ctl, web, /work-mode parallel and the pane.
  P0: with every slot full a new P0 goal gets its own fork, and main is told it started.
Scenario p0-blocked (E2E_SCENARIO=p0-blocked): creating the Work worktree fails with git's
ownership wording (a post-checkout hook simulates it), so a P0 goal cannot get its workspace;
main must be told that failure and nothing may claim a fork is starting.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.dont_write_bytecode = True
SOURCE = Path(sys.argv[1]).resolve()
RUN = Path(sys.argv[2]).resolve()
RUN.mkdir(parents=True, exist_ok=False)
TEMP_ROOT = Path(os.environ.get("E2E_TEMP_ROOT") or r"D:\Temp")
SCRATCH = Path(tempfile.mkdtemp(prefix="ciel-e2e-fork-fill-", dir=str(TEMP_ROOT)))
CFG, HOME, WS, SHOTS = SCRATCH / "cfg", SCRATCH / "home", SCRATCH / "workspace", RUN / "shots"
MARKERS = SCRATCH / "markers"
for folder in (CFG, HOME, WS, SHOTS, MARKERS):
    folder.mkdir(parents=True, exist_ok=True)
(RUN / "scratch.txt").write_text(str(SCRATCH), encoding="utf-8")  # local cleanup aid; never committed
# This process imports the board store: it stays inside the scratch profile too.
for name in [key for key in os.environ if key.upper().startswith("CIEL_RUNTIME_")]:
    os.environ.pop(name, None)
os.environ.update(CIEL_RUNTIME_TEST_ISOLATED="1", CIEL_RUNTIME_CONFIG_DIR=str(CFG), TEMP=str(TEMP_ROOT),
                  TMP=str(TEMP_ROOT), PYTHONDONTWRITEBYTECODE="1")
tempfile.tempdir = str(TEMP_ROOT)
SESSION = "88888888-8888-4888-8888-" + secrets.token_hex(6)
IDENTITY = "claude:" + SESSION
TITLE = "CIELE2E-FORK-FILL-" + RUN.name
LIMITS = {"P1": 3, "P2": 2, "P3": 1, "P4": 1, "P5": 1}
LONG = int(os.environ.get("E2E_LONG_SECONDS") or 330)
PARK_SECONDS, REFILL_SECONDS, MAIN_LONG_SECONDS = 45, 20, 150
MAIN_PROMPT = "E2E-MAIN-LONG run the long command"
SCENARIO = os.environ.get("E2E_SCENARIO") or "fill"
P0_GOAL, P0_LEAF, P0_SECONDS = "P0 goal", "P0 leaf", 60
P0_NOTICE = "Ciel Work P0 outcome"
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(message: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {message}".replace(str(SCRATCH), "<scratch>")
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


ITEMS = {  # title -> (root title, priority, seconds)
    "P1 refill": ("P1 refill root", "P1", REFILL_SECONDS),
    "P1 park": ("P1 root", "P1", PARK_SECONDS),
    "P1 long a": ("P1 root", "P1", LONG),
    "P1 long b": ("P1 root", "P1", LONG),
    "P2 long a": ("P2 root", "P2", LONG),
    "P2 long b": ("P2 root", "P2", LONG),
    "P3 long": ("P3 root", "P3", LONG),
    "P4 long": ("P4 root", "P4", LONG),
    "P5 long": ("P5 root", "P5", LONG),
} if SCENARIO == "fill" else {}
P0_ITEMS: set[str] = set()


def marker(title: str) -> Path:
    return MARKERS / (slug(title) + ".txt")


TICK = SCRATCH / "tick.py"
TICK.write_text("import sys, time\npath, seconds, word = sys.argv[1], int(sys.argv[2]), sys.argv[3]\n"
                "end = time.time() + seconds\n"
                "while True:\n    open(path, 'a').write(str(time.time()) + ' ' + word + '\\n')\n"
                "    if time.time() >= end: break\n    time.sleep(2)\nprint('TICK-DONE', word)\n", encoding="utf-8")


def tick_input(path: Path, seconds: int, word: str) -> dict:
    return {"command": f'python "{TICK.as_posix()}" "{path.as_posix()}" {seconds} {word}',
            "description": "e2e marker loop", "timeout": min(600000, (seconds + 60) * 1000)}


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


# ---- workspace repository and scratch home ----
subprocess.run(["git", "init", "-q", "-b", "main", str(WS)], check=True)
(WS / "README.md").write_text("fork fill e2e\n", encoding="utf-8")
subprocess.run(["git", "-C", str(WS), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(WS), "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid",
                "commit", "-q", "-m", "base"], check=True)
if SCENARIO == "p0-blocked":
    # Only the Work worktree creation fails, with git's ownership wording: an environment
    # switch also blinds Claude Code's own repository check, and then no workspace is tried.
    (WS / ".git" / "hooks").mkdir(exist_ok=True)
    (WS / ".git" / "hooks" / "post-checkout").write_text(
        "#!/bin/sh\necho 'fatal: detected dubious ownership in repository (simulated by the E2E)' >&2\nexit 1\n",
        encoding="utf-8", newline="\n")
(HOME / ".claude").mkdir()
(HOME / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Bash"], "deny": []},
                                                            "skipDangerousModePermissionPrompt": True}), encoding="utf-8")
spellings = {spelling for raw in (str(WS), str(WS.resolve()), os.path.realpath(WS))
             for slash in (raw, raw.replace("\\", "/"))
             for spelling in (slash, slash.lower(), slash[:1].upper() + slash[1:].lower())}
(HOME / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True, "theme": "dark",
                                               "projects": {key: {"hasTrustDialogAccepted": True} for key in spellings}}),
                                   encoding="utf-8")
(RUN / "answer-trust").write_text("1")


# ---- the stub model: scripted by the assignment a fork received ----
REQS: list[dict] = []
REQ_LOCK = threading.Lock()
REPORT_TOOL = "mcp__ciel-runtime-router__work_parallel_report"
RESUME_WORDS = "is cleared on the board"
FIELD = re.compile(r'"(item_id|lease_id|worker_token)"\s*:\s*"([^"]+)"')
PLAN_VERSION = re.compile(r'"plan_version"\s*:\s*(\d+)')


def blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [block for block in content or [] if isinstance(block, dict)]


def user_text(message: dict) -> str:
    return " ".join(str(block.get("text") or "") for block in blocks(message) if block.get("type") == "text")


def tool_uses(messages: list[dict]) -> list[str]:
    return [str(block.get("name")) for message in messages if message.get("role") == "assistant"
            for block in blocks(message) if block.get("type") == "tool_use"]


def last_result(messages: list[dict]) -> str:
    for message in reversed(messages):
        for block in reversed(blocks(message)):
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
                return (("ERROR " if block.get("is_error") else "") + str(content))[:400]
    return ""


def plan(body: dict) -> dict:
    """What this request is and what the stub answers, from its messages only."""
    messages = body.get("messages") or []
    users = [(index, user_text(m)) for index, m in enumerate(messages) if m.get("role") == "user"]
    assignment = next(((i, t) for i, t in reversed(users) if "Assignment JSON:" in t and "worker_token" in t), None)
    names = {tool.get("name"): tool for tool in body.get("tools") or [] if isinstance(tool, dict)}
    shell = "Bash" if "Bash" in names or "PowerShell" not in names else "PowerShell"
    if assignment is None:
        index = next((i for i, t in reversed(users) if MAIN_PROMPT in t), None)
        seen = {"p0_notices": [t[t.index(P0_NOTICE):][:900] for _, t in users if P0_NOTICE in t],
                "parked_notices": [t[t.index("Ciel Work parked worker (host notice"):][:500] for _, t in users
                                   if "Ciel Work parked worker (host notice" in t],
                "fork_starting": any("fork starting" in t for _, t in users)}
        if index is not None and not tool_uses(messages[index:]):
            return {"kind": "main", "tool": (shell, tick_input(MARKERS / "main-long.txt", MAIN_LONG_SECONDS, "MAIN")),
                    "tag": "main-long", **seen}
        return {"kind": "main", "tool": None, "tag": "main-text", **seen}
    fields = dict(FIELD.findall(assignment[1]))
    title = TITLE_OF.get(fields.get("item_id", ""), fields.get("item_id", "?"))
    resume = next((i for i, t in reversed(users) if RESUME_WORDS in t and i > assignment[0]), None)
    start = resume if resume is not None else assignment[0]
    used = tool_uses(messages[start:])
    out = {"kind": "fork", "title": title, "lease": fields.get("lease_id"), "resumed": resume is not None,
           "history_kept": resume is not None and bool(tool_uses(messages[assignment[0]:resume])),
           "last_result": last_result(messages[start:])}
    if shell not in used:
        word = "RESUMED" if resume is not None else "RUN"
        seconds = 1 if resume is not None else P0_SECONDS if title == P0_LEAF else ITEMS.get(title, ("", "", 5))[2]
        return {**out, "tool": (shell, tick_input(marker(title), seconds, word)), "tag": "shell"}
    if not any(name.endswith("work_parallel_report") for name in used):
        loaded = REPORT_TOOL in names and not names[REPORT_TOOL].get("defer_loading")
        if not loaded and "ToolSearch" in names and "ToolSearch" not in used:
            return {**out, "tool": ("ToolSearch", {"query": "select:" + REPORT_TOOL, "max_results": 1}), "tag": "search"}
        report = {"lease_id": fields.get("lease_id"), "worker_token": fields.get("worker_token")}
        if title == "P1 park" and resume is None:
            report.update(event="question", evidence="fork-fill park question")
            return {**out, "tool": (REPORT_TOOL, report), "tag": "question"}
        if not marker(title).exists():
            # The shell call returned without running the marker loop: try it again, then stop.
            if used.count(shell) < 3:
                word = "RESUMED" if resume is not None else "RUN"
                seconds = 1 if resume is not None else P0_SECONDS if title == P0_LEAF else ITEMS.get(title, ("", "", 5))[2]
                return {**out, "tool": (shell, tick_input(marker(title), seconds, word)), "tag": "shell-retry"}
            return {**out, "tool": None, "tag": "no-marker"}
        digest = hashlib.sha256(marker(title).read_bytes()).hexdigest()
        report.update(event="completion", artifact=digest, evidence="fork-fill marker completed")
        return {**out, "tool": (REPORT_TOOL, report), "tag": "completion"}
    return {**out, "tool": None, "tag": "fork-end"}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, value, code=200):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "stub-model", "object": "model", "owned_by": "stub",
                                                "max_model_len": 128000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        path = self.path.split("?")[0]
        if path.endswith("/count_tokens"):
            self._json({"input_tokens": 100})
            return
        if not path.endswith("/v1/messages"):
            choice = {"index": 0, "message": {"role": "assistant", "content": "STUB"}, "finish_reason": "stop"}
            self._json({"id": "c", "object": "chat.completion", "created": int(time.time()), "model": "stub-model",
                        "choices": [choice], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        try:
            step = plan(body)
        except Exception as error:  # noqa: BLE001 - shown in the evidence, answered as text
            step = {"kind": "error", "tool": None, "tag": "stub-error", "error": repr(error)[:300]}
        tool = step.pop("tool")
        with REQ_LOCK:
            REQS.append({"at": time.time(), "tool": tool[0] if tool else None, **step})
            number = len(REQS)
        message = {"id": f"msg_{number}", "type": "message", "role": "assistant", "model": body.get("model") or "stub-model",
                   "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
        if tool:
            block = {"type": "tool_use", "id": f"toolu_e2e_{number}", "name": tool[0]}
            final, stop = [{**block, "input": tool[1]}], "tool_use"
            events = [{"type": "content_block_start", "index": 0, "content_block": {**block, "input": {}}},
                      {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "input_json_delta", "partial_json": json.dumps(tool[1])}},
                      {"type": "content_block_stop", "index": 0}]
        else:
            reply = f"STUB-{step['tag']}"
            final, stop = [{"type": "text", "text": reply}], "end_turn"
            events = [{"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                      {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": reply}},
                      {"type": "content_block_stop", "index": 0}]
        if not body.get("stream"):
            self._json({**message, "content": final, "stop_reason": stop})
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in ([{"type": "message_start", "message": message}] + events
                      + [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                          "usage": {"output_tokens": 2}}, {"type": "message_stop"}]):
            self.wfile.write(("event: " + event["type"] + "\n" + "data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()

# ---- scratch config, written from nothing: the stub is the only provider ----
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "vllm", "language": "en", "work_mode": True, "work_auto_continue": False,
    "work_side_intake": {"enabled": False}, "work_gate_check": {"minutes": 1},
    "providers": {"vllm": {"base_url": f"http://127.0.0.1:{stub.server_address[1]}", "current_model": "stub-model",
                           "api_key": "dummy", "context_window": 128000, "max_output_tokens": 4096}},
}, indent=2), encoding="utf-8")
policy = {"enabled": True, "paused": False, "priority_limits": dict(LIMITS),
          "thresholds": {"P2": 1.0, "P3": 1.0, "P4": 1.0, "P5": 1.0}, "launches_per_minute": 24, "max_rework": 2,
          "discovery_text": "When approved Work has spare capacity, pick the next runnable leaf.", "test_override": True}
(CFG / "work-parallel.json").write_text(json.dumps({"schema": 1, "version": 1, "policy": policy, "leases": {},
                                                     "launches": []}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")

# ---- seed the board before session.start (TEST_ISOLATED: the state dir is the config dir) ----
sys.path.insert(0, str(SOURCE))
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support import work_plan_tree as work_tree  # noqa: E402
store = session_work_plan_store(CFG, IDENTITY)


def seed(doc: dict) -> None:
    # The dispatcher orders newest first within a priority (work_plan_tree.runnable_leaves),
    # so "P1 refill" is created first, 100 s older than the rest: it is the item that waits.
    roots: dict[str, list[str]] = {}
    for title, (root, _priority, _seconds) in ITEMS.items():
        roots.setdefault(root, []).append(title)
    base = time.time() - 600
    for index, (root, titles) in enumerate(roots.items()):
        root_id = work_tree.set_plan(doc, {"title": root, "priority": ITEMS[titles[0]][1],
                                            "children": [{"title": t, "note": "stub-scripted e2e item"} for t in titles]},
                                     now=base + (0 if index == 0 else 100 + index))
        work_tree.set_phase(doc, work_tree.PHASE_EXECUTING, root_id)


if ITEMS:
    store.transaction(seed)
ITEM_ID = {node["title"]: node["id"] for node in store.read()["nodes"] if node["title"] in ITEMS}
assert set(ITEM_ID) == set(ITEMS), ITEM_ID
TITLE_OF = {value: key for key, value in ITEM_ID.items()}

ROUTER = free_port()
clear = "".join(f'for /f "delims==" %%v in (\'set {prefix} 2^>nul\') do set "%%v="\n'
                for prefix in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\nchcp 65001 >nul\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "USERPROFILE={HOME}"\nset "HOME={HOME}"\nset "TEMP={TEMP_ROOT}"\nset "TMP={TEMP_ROOT}"\n'
    'set "PYTHONDONTWRITEBYTECODE=1"\nset "DISABLE_AUTOUPDATER=1"\n'
    f'cd /d "{WS}"\npython "{SOURCE / "ciel_runtime.py"}" cli --ca-runtime claude --ca-no-update-check '
    f'--ca-no-self-update-check -- --session-id {SESSION} --debug-file "{SCRATCH / "claude-debug.txt"}"\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
log(f"source=<snapshot> stub={stub.server_address[1]} router={ROUTER} scratch={SCRATCH} session={SESSION}")
subprocess.Popen(["wt.exe", "-w", "new", "--size", "150,62", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def token() -> str:
    for path in (CFG / "router-external.token", ):
        if path.exists():
            return path.read_text(encoding="ascii").strip()
    return ""


def http(method: str, path: str, body: dict | None = None, timeout: float = 20) -> dict:
    headers = {"content-type": "application/json", "accept": "application/json"}
    if token():
        headers["authorization"] = "Bearer " + token()
    request = urllib.request.Request(f"http://127.0.0.1:{ROUTER}{path}", method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001
        return {"raw": f"{type(error).__name__}: {error}"}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:500]}


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.glob('*.png'))) + 1:02d}-{label}.png"
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot_title.ps1"),
                           TITLE, str(out)], capture_output=True, text=True)
    log(f"shot {label}: {(done.stdout.strip() or done.stderr.strip())[:120]}")


def ledger() -> dict:
    try:
        return json.loads((CFG / "work-parallel.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"leases": {}}


def mine(doc: dict) -> list[dict]:
    return [row for row in doc.get("leases", {}).values() if row.get("scope") == IDENTITY]


def running_by_priority(doc: dict) -> dict[str, int]:
    counts = dict.fromkeys(LIMITS, 0)
    for row in mine(doc):
        if row.get("state") == "running" and row.get("slot") and row.get("item_id") not in P0_ITEMS:
            counts[row["priority"]] = counts.get(row["priority"], 0) + 1
    return counts


def lease_for(title: str, doc: dict | None = None) -> dict | None:
    rows = [row for row in mine(doc or ledger()) if row.get("item_id") == ITEM_ID[title]]
    return max(rows, key=lambda row: row.get("created_at") or 0) if rows else None


def marker_live(title: str, within: float = 6.0) -> bool:
    path = marker(title)
    return path.exists() and time.time() - path.stat().st_mtime < within


def wait(predicate, seconds: float, label: str) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if predicate():
                log(f"wait {label}: True")
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    log(f"wait {label}: False")
    return False


SAMPLES: list[dict] = []
STOP = threading.Event()


def sampler() -> None:
    while not STOP.is_set():
        doc = ledger()
        SAMPLES.append({"at": time.time(), "running": running_by_priority(doc),
                        "live_markers": sorted(t for t in ITEMS if marker_live(t)),
                        "states": {TITLE_OF.get(r["item_id"], r["item_id"]): [r["state"], bool(r.get("slot")),
                                   r.get("host_id")] for r in mine(doc)}})
        STOP.wait(2)


def node_update(item: str, waiting_on: str) -> dict:
    version = store.read()["version"]
    return http("POST", "/ca/work/api/v1/commands/node-update",
                {"session_id": IDENTITY, "expected_version": version, "id": ITEM_ID[item], "waiting_on": waiting_on,
                 "operation_id": secrets.token_hex(16)})


def gate_record() -> dict:
    for path in CFG.rglob("gate-check.json"):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def ctl_capacity() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC"))}
    env.update(CIEL_RUNTIME_CONFIG_DIR=str(CFG), CIEL_RUNTIME_TEST_ISOLATED="1", TEMP=str(TEMP_ROOT), TMP=str(TEMP_ROOT),
               CIEL_RUNTIME_LAUNCH_CWD=str(WS))
    done = subprocess.run([sys.executable, str(SOURCE / "ciel_runtime.py"), "work", "capacity", "--session", IDENTITY,
                           "--json"], capture_output=True, text=True, env=env, cwd=str(WS), timeout=120)
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {"raw": (done.stdout + done.stderr)[-800:]}


def web_shot(label: str) -> dict:
    from playwright.sync_api import sync_playwright
    chrome = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright/chromium-1243/chrome-win64/chrome.exe"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
        page = browser.new_page(viewport={"width": 1440, "height": 1100},
                                extra_http_headers={"Authorization": "Bearer " + token()} if token() else {})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{ROUTER}/ca/work/ui", wait_until="domcontentloaded")
        page.get_by_role("tab", name="Parallel").click()
        page.wait_for_timeout(4000)
        text = page.locator("#panel-parallel").inner_text()
        page.screenshot(path=str(SHOTS / f"{len(list(SHOTS.glob('*.png'))) + 1:02d}-{label}.png"), full_page=True)
        browser.close()
    return {"page_errors": errors, "has_dispatch": "P1" in text and ("Dispatch" in text or "dispatch" in text),
            "excerpt": text[:1500]}


def requests_of(**match) -> list[dict]:
    with REQ_LOCK:
        return [row for row in REQS if all(row.get(key) == value for key, value in match.items())]


def screen_files(label: str) -> list[Path]:
    return sorted(RUN.glob(f"screen-*-{label}.txt"))


def powershell(command: str) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True).stdout.strip()


def launcher_pids() -> list[str]:
    return powershell("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*"
                      + SESSION + "*' } | % { $_.ProcessId }").split()


def create_p0_goal() -> dict:
    answer = http("POST", "/ca/work/api/v1/commands/plan-set",
                  {"session_id": IDENTITY, "expected_version": store.read()["version"], "goal": P0_GOAL, "priority": "P0",
                   "children": [{"title": P0_LEAF}], "operation_id": secrets.token_hex(16)})
    for node in store.read()["nodes"]:
        if node["title"] == P0_LEAF:
            ITEM_ID[P0_LEAF] = node["id"]
            TITLE_OF[node["id"]] = P0_LEAF
            P0_ITEMS.add(node["id"])
    return answer


def p0_goal_row() -> dict:
    rest = http("GET", "/ca/work/api/v1/parallel?session_id=" + IDENTITY)
    row = next((r for r in (rest.get("priority_diagnosis") or {}).get("rows", []) if r.get("priority") == "P0"), {})
    return {"state": row.get("state"), "reason": row.get("reason"), "goals": row.get("goals")}


def main_notices() -> list[str]:
    return sorted({text for row in requests_of(kind="main") for text in row.get("p0_notices", [])})


def transcript_has(word: str) -> bool:
    for path in (HOME / ".claude" / "projects").rglob("*.jsonl"):
        try:
            if word in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def remove_tree(path: Path) -> None:
    def writable(function, name, _error):
        os.chmod(name, 0o700)
        function(name)
    shutil.rmtree(path, onerror=writable)


def run_blocked(router_up: bool) -> None:
    """A P0 goal whose workspace git refuses: main hears that, not "fork starting"."""
    time.sleep(8)
    result["plan_set"] = {k: v for k, v in create_p0_goal().items() if k in ("ok", "operation", "error", "raw")}
    told = wait(lambda: any("no P0 fork (workspace_" in text for text in main_notices()), 150,
                "main was told the workspace failure")
    row = p0_goal_row()
    result["p0_row"] = row
    result["main_notices"] = main_notices()
    shot("p0-blocked")
    goal = (row.get("goals") or [{}])[0]
    item = ITEM_ID.get(P0_LEAF)
    result["leases_on_item"] = [{k: lease.get(k) for k in ("state", "host_id")} for lease in mine(ledger())
                                if lease.get("item_id") == item]
    result["checks"] = {
        "router_up": router_up,
        "p0_goal_created": bool(item),
        "main_told_workspace_failure": told,
        "notice_has_gits_reason": any("dubious ownership" in text for text in main_notices()),
        "diagnosis_names_it": goal.get("state") == "blocked" and str(goal.get("code")).startswith("workspace_"),
        "no_fork_on_the_item": not any(lease["host_id"] for lease in result["leases_on_item"]),
        "nothing_said_fork_starting": not any(row.get("fork_starting") for row in requests_of(kind="main")),
        "notice_said_once": len(main_notices()) == 1,
        "no_stub_errors": not requests_of(kind="error"),
    }
    result["passed"] = all(result["checks"].values())


result: dict = {"scenario": SCENARIO, "limits": LIMITS, "model": "local stub; no real provider or key"}
launcher = 0
threading.Thread(target=sampler, daemon=True).start()
try:
    router_up = wait(lambda: "raw" not in http("GET", "/health", timeout=3), 120, "router up")
    wait(lambda: bool(launcher_pids()), 30, "launcher visible")
    pids = launcher_pids()
    launcher = int(pids[0]) if pids else 0
    if launcher:
        subprocess.Popen([sys.executable, str(HERE / "e2e_console.py"), str(launcher), str(RUN)], creationflags=0x00000008)
    if SCENARIO == "p0-blocked":
        run_blocked(router_up)
        raise SystemExit(0)
    # 1 eight forks at once
    full = wait(lambda: running_by_priority(ledger()) == LIMITS, 240, "every priority at its limit (8 running)")
    result["running_at_limit"] = running_by_priority(ledger())
    eight_live = wait(lambda: sum(marker_live(t, 8.0) for t in ITEMS if t != "P1 refill") >= 8, 90, "8 markers written at once")
    time.sleep(3)
    shot("eight-forks")
    # While the list is short, so the DISPATCH block is on the visible screen.
    (RUN / "type.txt").write_text("/work-mode parallel", encoding="utf-8")
    text_seen = wait(lambda: bool(screen_files("dispatch")) or transcript_has("DISPATCH: "), 25,
                     "/work-mode parallel shows DISPATCH")
    shot("work-mode-parallel")
    peak = max((sum(s["running"].values()) for s in SAMPLES), default=0)
    result["live_markers_at_peak"] = max((len(s["live_markers"]) for s in SAMPLES), default=0)
    # P0 with every slot full: its own fork, beyond the per-priority limits
    p0_asked = time.time()
    result["p0_plan_set"] = {k: v for k, v in create_p0_goal().items() if k in ("ok", "operation", "error", "raw")}
    p0_running = wait(lambda: (lease_for(P0_LEAF) or {}).get("state") == "running" and bool(lease_for(P0_LEAF).get("host_id")),
                      90, "the P0 goal has a running fork")
    result["p0_fork_seconds"] = round(time.time() - p0_asked, 1) if p0_running else None
    result["p0_with_full_slots"] = running_by_priority(ledger())
    result["p0_row_running"] = p0_goal_row()
    shot("p0-fork")
    # 2 gate check host round while forks run, side intake off
    wait(lambda: (gate_record().get("host_round") or {}).get("forks_running", 0) > 0, 180,
         "gate check host round recorded while forks run")
    result["gate_record"] = {k: gate_record().get(k) for k in ("host_round", "notified_digest")}
    result["refill_waited"] = all(s["states"].get("P1 refill", ["none"])[0] != "running"
                                  for s in SAMPLES if sum(s["running"].values()) == 8
                                  and s["states"].get("P1 park", ["none"])[0] == "running")
    # 3 park -> refill
    wait(lambda: (lease_for("P1 park") or {}).get("state") == "waiting_input" and not lease_for("P1 park").get("slot"),
         180, "P1 park parked (waiting_input, slot given back)")
    park = dict(lease_for("P1 park") or {})
    result["park_lease"] = {k: park.get(k) for k in ("id", "state", "slot", "host_id", "agent_name")}
    refill_started = wait(lambda: (lease_for("P1 refill") or {}).get("state") not in (None, "reserved"), 120,
                          "P1 refill took the freed P1 slot")
    shot("park-refill")
    # 4 record a wait while parked
    result["wait_set"] = node_update("P1 park", "E2E external approval")
    seen = wait(lambda: park.get("id") in json.loads((CFG / "work-parallel-parked-waits.json").read_text(encoding="utf-8")).get(
        "leases", {}), 120, "host recorded the wait while parked")
    wait(lambda: (lease_for("P1 refill") or {}).get("state") not in (None, "reserved", "running"), 120,
         "P1 refill finished (P1 slot free)")
    # 5 main in one long tool call; the wait clears during it
    time.sleep(5)
    (RUN / "type.txt").write_text(MAIN_PROMPT, encoding="utf-8")
    main_busy = wait(lambda: marker_live("main-long", 6.0), 60, "main is inside its long Bash call")
    result["wait_cleared_request"] = node_update("P1 park", "")
    cleared_at = time.time()
    resumed = wait(lambda: "RESUMED" in marker("P1 park").read_text(encoding="utf-8"), 110,
                   "parked worker resumed and wrote RESUMED")
    result["resume_latency_seconds"] = round(time.time() - cleared_at, 1) if resumed else None
    result["main_still_in_tool_at_resume"] = marker_live("main-long", 6.0)
    submitted = wait(lambda: (lease_for("P1 park") or {}).get("state") == "submitted", 60, "resumed worker reported completion")
    after = lease_for("P1 park") or {}
    result["park_after"] = {k: after.get(k) for k in ("id", "state", "slot", "host_id", "agent_name")}
    resumed_rows = [row for row in requests_of(kind="fork", title="P1 park") if row.get("resumed")]
    result["resumed_requests"] = [{k: row.get(k) for k in ("tag", "history_kept", "lease")} for row in resumed_rows]
    result["shell_retries"] = [{k: row.get(k) for k in ("title", "tag", "last_result")} for row in requests_of(kind="fork")
                               if row.get("tag") in ("shell-retry", "no-marker")][:8]
    result["parked_notice_to_main"] = sorted({text[:400] for row in requests_of(kind="main")
                                              for text in row.get("parked_notices", [])})
    shot("parked-resumed")
    # 6 diagnosis on every surface
    rest = http("GET", "/ca/work/api/v1/parallel?session_id=" + IDENTITY)
    result["rest_priority_diagnosis"] = rest.get("priority_diagnosis") or {"missing": str(rest)[:300]}
    result["ctl_priority_diagnosis"] = ctl_capacity().get("priority_diagnosis")
    try:
        result["web"] = web_shot("web-parallel")
    except Exception as error:  # noqa: BLE001
        result["web"] = {"error": repr(error)[:300]}
    (RUN / "type.txt").write_text("/work-mode view", encoding="utf-8")
    time.sleep(6)
    shot("pane-dispatch")
    debug = SCRATCH / "claude-debug.txt"
    debug_text = debug.read_text(encoding="utf-8", errors="replace") if debug.exists() else ""
    hook_bound = [row for row in mine(ledger()) if row.get("host_id")]
    result["mod_load"] = {"debug_file_bytes": len(debug_text),
                          "load_failure_lines": [line[:300] for line in debug_text.splitlines()
                                                 if "ciel-work" in line and re.search(r"did not load|failed to load", line, re.I)][:10],
                          "leases_bound_by_the_hook": len(hook_bound), "stub_fork_requests": len(requests_of(kind="fork"))}
    result["stub_errors"] = requests_of(kind="error")[:5]
    agent_ok = bool(after.get("host_id")) and after.get("host_id") == park.get("host_id")
    round_row = result["gate_record"].get("host_round") or {}
    peak = max((sum(s["running"].values()) for s in SAMPLES), default=0)
    result["peak_running"] = peak
    result["fork_last_results"] = [{k: row.get(k) for k in ("title", "tag", "last_result")}
                                   for row in requests_of(kind="fork") if "ERROR" in str(row.get("last_result"))][:8]
    result["main_notices"] = main_notices()
    result["parked_log"] = [line.split("$.ui.log: ", 1)[1][:240] for line in debug_text.splitlines()
                            if "$.ui.log: Work: " in line and "parked" in line][:20]
    result["checks"] = {
        "router_up": router_up,
        "mod_loaded_hooks_observed": len(hook_bound) >= 8 and not result["mod_load"]["load_failure_lines"],
        "eight_forks_at_once": bool(full) and peak == sum(LIMITS.values()) and eight_live,
        "per_priority_never_over_limit": all(all(s["running"][p] <= LIMITS[p] for p in LIMITS) for s in SAMPLES),
        "ninth_item_waited_while_full": result["refill_waited"],
        "refill_took_parked_slot": bool(refill_started),
        "host_saw_recorded_wait": bool(seen),
        "main_in_long_tool_when_cleared": bool(main_busy),
        "parked_worker_resumed_same_agent": bool(resumed) and agent_ok and bool(submitted),
        "resumed_with_its_history": any(row.get("history_kept") for row in resumed_rows),
        "resumed_during_the_main_tool_call": bool(resumed) and result["main_still_in_tool_at_resume"],
        "gate_check_ran_while_forks_run_side_off": (round_row.get("forks_running") or 0) > 0 and round_row.get("side_intake") is False,
        "gate_round_repumped": round_row.get("repumped") is True,
        "diagnosis_on_rest": bool((result["rest_priority_diagnosis"] or {}).get("rows")),
        "diagnosis_on_ctl": bool((result["ctl_priority_diagnosis"] or {}).get("rows")),
        "diagnosis_on_web": bool(result["web"].get("has_dispatch")) and not result["web"].get("page_errors"),
        "diagnosis_in_work_mode_text": bool(text_seen),
        "no_stub_errors": not result["stub_errors"],
        "p0_fork_with_every_slot_full": bool(p0_running) and result["p0_with_full_slots"] == LIMITS,
        "p0_diagnosis_shows_the_fork": any(goal.get("state") == "running" for goal in result["p0_row_running"].get("goals") or []),
        "main_told_p0_fork_started": any("P0 fork started" in text for text in result["main_notices"]),
        "nothing_said_fork_starting": not any(row.get("fork_starting") for row in requests_of(kind="main")),
    }
    # Auto-resume is reported on its own: when it did not run, the safe outcome is required.
    resume_keys = ("parked_worker_resumed_same_agent", "resumed_with_its_history", "resumed_during_the_main_tool_call")
    result["auto_resume_worked"] = all(result["checks"][key] for key in resume_keys)
    result["resume_safe_failure"] = {"lease_parked_without_slot": after.get("state") == "waiting_input" and not after.get("slot"),
                                     "main_told_once": len(result["parked_notice_to_main"]) == 1}
    others = [value for key, value in result["checks"].items() if key not in resume_keys]
    result["passed"] = all(others) and (result["auto_resume_worked"] or all(result["resume_safe_failure"].values()))
finally:
    STOP.set()
    (RUN / "stop").write_text("1")
    time.sleep(1)
    (RUN / "samples.json").write_text(json.dumps(SAMPLES, indent=1), encoding="utf-8")
    (RUN / "stub-requests.json").write_text(json.dumps(REQS, indent=1), encoding="utf-8")
    for path in CFG.rglob("router.log"):
        shutil.copy2(path, RUN / ("router-" + path.parent.name + ".log"))
    if (SCRATCH / "claude-debug.txt").exists():
        shutil.copy2(SCRATCH / "claude-debug.txt", RUN / "claude-debug.txt")
    if launcher:
        subprocess.run(["taskkill", "/PID", str(launcher), "/T", "/F"], capture_output=True)
    powershell("Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*" + str(script)
               + "*' } | % { Stop-Process -Id $_.ProcessId -Force }")
    needles = ("($_.CommandLine -like '*" + str(SCRATCH) + "*' -or $_.CommandLine -like '*" + SCRATCH.as_posix()
               + "*' -or $_.CommandLine -like '*" + SESSION + "*' -or $_.CommandLine -like '*" + str(RUN) + "*')")
    others = " -and $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid())
    owned = powershell("Get-CimInstance Win32_Process | ? { " + needles + others
                       + " } | % { $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }").split()
    time.sleep(3)
    survivors = powershell("@(Get-CimInstance Win32_Process | ? { " + needles + others + " }).Count")
    result["owned_killed_at_end"] = len(owned)
    result["owned_survivors"] = int(survivors or 0)
    for _attempt in range(10):
        try:
            remove_tree(SCRATCH)
        except OSError:
            pass
        if not SCRATCH.exists():
            break
        time.sleep(2)
    result["scratch_removed"] = not SCRATCH.exists()
    text = json.dumps(result, indent=1, ensure_ascii=False).replace(str(SCRATCH), "<scratch>").replace(
        SCRATCH.as_posix(), "<scratch>")
    (RUN / "result.json").write_text(text, encoding="utf-8")
    log("result: " + json.dumps({"passed": result.get("passed"), "checks": result.get("checks"),
                                 "peak": result.get("peak_running"), "survivors": result.get("owned_survivors"),
                                 "scratch_removed": result.get("scratch_removed")}, ensure_ascii=False))
