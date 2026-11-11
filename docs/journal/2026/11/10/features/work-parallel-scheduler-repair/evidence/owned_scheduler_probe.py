"""Owned real-console E2E proof for the host-owned Work scheduler.

This probe is intentionally destructive only to processes and files it creates:
an isolated config, router port, git repository, fixed Claude session, and conhost
process tree. It never restarts or reads state from a live Ciel session. Provider
configuration is copied, but bearer/API values are never emitted to evidence.

Run with PROBE_SNAPSHOT pointing at an immutable installed/snapshot runtime.
The probe seeds the Work board before session.start so the packaged host scheduler
must fill P1=2, P2=1 and P3=1 without a model dispatch call. One P1 fork exits
first; the fifth runnable P1 item must then start automatically. Acceptance is
computed from native evidence, coordination state, marker timestamps, transcript
isolation, the public API, and desktop/mobile/terminal captures.
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
import time
import urllib.parse
import urllib.request
import winreg
from pathlib import Path

REPO = Path(__file__).resolve().parents[8]
TOOLS = REPO / "docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui"
SNAP = Path(os.environ["PROBE_SNAPSHOT"]).resolve()
MAIN_MODEL = os.environ.get("PROBE_MAIN_MODEL", "_Walkie_Opus55-Sol61-Fast")
LABEL = os.environ.get("PROBE_LABEL", "")
OUT = Path(__file__).resolve().parent / ("scheduler-run" + LABEL)
OUT.mkdir(parents=True, exist_ok=True)
BASE = REPO / ".local" / ("owned-scheduler-" + time.strftime("%Y%m%d-%H%M%S"))
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / "console_driver.py", BASE / "console_driver.py")
shutil.copy2(REPO / ".local/shot_hwnd_restore.ps1", BASE / "shot_hwnd.ps1")
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

SESSION = "77777777-7777-4777-8777-777777777777"
IDENTITY = "claude:" + SESSION
PRIORITY_LIMITS = {"P1": 2, "P2": 1, "P3": 1, "P4": 1, "P5": 1}
INITIAL_TITLES = ("P1 first release", "P1 automatic refill", "P2 long", "P3 long")
NEXT_TITLE = "P1 long"
CANARIES = {name: "SCHED-" + name.upper().replace(" ", "-") + "-" + secrets.token_hex(5)
            for name in (*INITIAL_TITLES, NEXT_TITLE)}
DURATIONS = {"P1 first release": 68, "P1 automatic refill": 150,
             "P1 long": 82, "P2 long": 150, "P3 long": 150}
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")


def free_port(start: int) -> int:
    for port in range(start, start + 100):
        sock = socket.socket()
        try:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
        finally:
            sock.close()
    raise RuntimeError("no free owned probe port")


def clean_environment() -> dict[str, str]:
    """Match the proven safe probe: strip inherited routing/provider variables."""
    result = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        index = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                break
            index += 1
            if name.upper().startswith(PREFIXES) and name.upper() != "CIEL_RUNTIME_HOME":
                result[name] = str(value)
    return result


def initial_ledger() -> dict:
    """Compatible terminal/stale rows prove they consume neither slots nor rate."""
    now = time.time()
    policy = {
        "enabled": True, "paused": False, "priority_limits": dict(PRIORITY_LIMITS),
        "thresholds": {"P2": 1.0, "P3": 1.0, "P4": 1.0, "P5": 1.0},
        "launches_per_minute": 24, "max_rework": 2,
        "discovery_text": "When approved Work has spare capacity, pick the next runnable leaf.",
        "test_override": True,
    }
    leases = {}
    for index in range(24):
        ident = f"probe-terminal-{index:02d}"
        proof = "owned scheduler probe retained historical row"
        leases[ident] = {
            "id": ident, "scope": "claude:probe-history", "item_id": f"old-{index}",
            "priority": ("P1", "P2", "P3")[index % 3], "created_at": now - 7200 - index,
            "role": "worker", "mode": "fork", "plan_version": 1, "state": "done",
            "host_id": f"old-agent-{index}", "slot": False, "usage": 0,
            "artifact": hashlib.sha256(ident.encode()).hexdigest(), "rework": 0,
            "evidence": proof, "idle_proof": proof, "cleanup_proof": proof,
        }
    return {"schema": 1, "version": 1, "policy": policy, "leases": leases,
            "launches": [now - 180 - index for index in range(24)]}


def marker_note(title: str) -> str:
    marker = (markers / (slug(title) + ".txt")).as_posix()
    seconds = DURATIONS[title]
    canary = CANARIES[title]
    report = (
        "immediately call work_parallel_report event=question with the exact lease_id and worker_token "
        "from your assignment, evidence='owned scheduler slot release question', and detail with question, "
        "blocking_todos, and decision_needed. This deliberately parks the item and releases its slot"
        if title == "P1 first release" else
        "compute the marker SHA256 and immediately call work_parallel_report event=completion with the exact "
        "lease_id and worker_token from your assignment, artifact=<marker SHA256>, "
        "evidence='owned scheduler marker completed'"
    )
    return (
        "Execute this assignment without changing the Work board. Run exactly one foreground Bash command: "
        "python -c \"import time; p=r'" + marker + "'; "
        "[(open(p,'a').write(str(time.time())+' " + canary + "\\n'),time.sleep(2)) "
        "for _ in range(" + str(seconds // 2) + ")]\". After it exits, " + report + ". "
        "Do not send any message to main and do not call Agent."
    )


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


env = clean_environment()
port = free_port(9731)
scratch = Path(tempfile.mkdtemp(prefix="ciel-owned-scheduler-"))
workspace = scratch / "workspace"
config = scratch / "config"
markers = workspace / "markers"
workspace.mkdir()
config.mkdir()
markers.mkdir()
subprocess.run(["git", "init", "-q", str(workspace)], check=True)
(workspace / "README.md").write_text("owned Work scheduler probe\n", encoding="utf-8")
(workspace / ".gitignore").write_text("markers/\n", encoding="utf-8")
subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(workspace), "-c", "user.name=probe",
                "-c", "user.email=probe@localhost", "commit", "-q", "-m", "probe workspace"], check=True)

real_config = Path(os.environ["APPDATA"]) / "ciel-runtime"
for filename in ("config.json", "model-registry.json"):
    shutil.copy2(real_config / filename, config / filename)
copied = json.loads((config / "config.json").read_text(encoding="utf-8"))
assert copied.get("providers", {}).get("cielairouter"), "configured fixture provider missing"
for section in ("external_event_receivers", "remote_bridge", "remote_instructions", "remote_memory",
                "remote_provision", "work_parallel"):
    copied.pop(section, None)
copied["providers"]["cielairouter"]["current_model"] = MAIN_MODEL
copied.update(current_provider="cielairouter", work_mode=True, work_auto_continue=False,
              work_side_intake={"enabled": False})
(config / "config.json").write_text(json.dumps(copied, indent=2), encoding="utf-8")
(config / "work-parallel.json").write_text(json.dumps(initial_ledger(), indent=2), encoding="utf-8")

env.update(CIEL_RUNTIME_HOME_OVERRIDE=str(SNAP), CIEL_RUNTIME_CONFIG_DIR=str(config),
           CIEL_RUNTIME_LAUNCH_CWD=str(workspace), CIEL_RUNTIME_ROUTER_PORT=str(port),
           CIEL_RUNTIME_SKIP_MENU="1", CIEL_RUNTIME_SKIP_SELF_UPDATE="1")

# Seed multiple independently approved roots before session.start. No model turn is
# needed to expose the board-version transition to the scheduler.
sys.path.insert(0, str(SNAP))
from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support import work_plan_tree as work_tree  # noqa: E402
state_dir = config / "workspaces" / workspace_digest(workspace_identity(workspace))
state_dir.mkdir(parents=True, exist_ok=True)
store = session_work_plan_store(state_dir, IDENTITY)


def seed_board(doc: dict) -> None:
    # Runnable leaves use newest-first order within a priority. Create the long
    # continuation leaf first (oldest), then the two initially admitted P1 leaves,
    # so the long leaf is the measured slot waiter.
    roots = [
        ("P1 capacity root", "P1", ["P1 first release", "P1 automatic refill"]),
        ("P1 refill root", "P1", [NEXT_TITLE]),
        ("P2 root", "P2", ["P2 long"]),
        ("P3 root", "P3", ["P3 long"]),
    ]
    for root_title, priority, leaves in roots:
        root_id = work_tree.set_plan(doc, {"title": root_title, "priority": priority,
            "children": [{"title": title, "note": marker_note(title)} for title in leaves]})
        work_tree.set_phase(doc, work_tree.PHASE_EXECUTING, root_id)


store.transaction(seed_board)
seeded = store.read()
item_ids = {node["title"]: node["id"] for node in seeded["nodes"] if node["title"] in CANARIES}
assert set(item_ids) == set(CANARIES), item_ids

launch = BASE / "launch.cmd"
launch.write_text(
    "@echo off\r\nchcp 65001 >nul\r\ncd /d " + str(workspace) + "\r\n"
    + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" version\r\n"
    + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" cli -- --session-id " + SESSION
    + " --debug-file \"" + str(BASE / "claude-debug.txt") + "\"\r\n"
    + "echo [probe] ciel-runtime exited with %ERRORLEVEL%\r\n", encoding="utf-8")
proc = subprocess.Popen(["conhost.exe", "cmd", "/k", str(launch)], cwd=str(workspace), env=env)


def process_tree(root_pid: int | None = None) -> list[dict]:
    root_pid = proc.pid if root_pid is None else root_pid
    script = (f"$all=Get-CimInstance Win32_Process; $ids=@({root_pid}); do {{$new=@($all | Where-Object "
              "{$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | Select-Object -ExpandProperty ProcessId); "
              "$ids += $new} while ($new.Count); $all | Where-Object {$_.ProcessId -in $ids} | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json")
    for attempt in range(5):
        try:
            raw = subprocess.check_output(["powershell", "-NoProfile", "-Command", script], text=True).strip()
            rows = json.loads(raw) if raw else []
            return rows if isinstance(rows, list) else [rows]
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            if attempt == 4:
                raise
            time.sleep(1)
    return []


def capture(console: Console, label: str) -> str:
    text = console.screen()
    (OUT / (label + ".txt")).write_text(text, encoding="utf-8")
    shot = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(BASE / "shot_hwnd.ps1"), str(int(console.hwnd)), str(OUT / (label + ".png"))],
                          capture_output=True, text=True, creationflags=0x08000000)
    (OUT / (label + "-capture.log")).write_text(shot.stdout + shot.stderr, encoding="utf-8")
    return text


def admin_token() -> str:
    path = config / "router-external.token"
    return path.read_text(encoding="ascii").strip() if path.exists() else ""


def api_get(path: str) -> dict:
    headers = {"Accept": "application/json"}
    token = admin_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def browser_shot(label: str, *, mobile: bool = False, cards: int = 4) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        return {"ok": False, "error": repr(error)}
    token = admin_token()
    chrome = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
            page = browser.new_page(viewport={"width": 390 if mobile else 1440, "height": 844 if mobile else 1000},
                                    extra_http_headers={"Authorization": "Bearer " + token} if token else {})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}/ca/work/ui", wait_until="domcontentloaded")
            page.get_by_role("tab", name="Parallel").click()
            page.locator("#panel-parallel .parallel-section[data-group='confirmed'] .fork-card").nth(cards - 1).wait_for(timeout=20000)
            text = page.locator("#panel-parallel").inner_text()
            page.screenshot(path=str(OUT / (label + ".png")), full_page=True)
            browser.close()
        return {"ok": True, "confirmed_cards": text.count("confirmed by native agent list"),
                "titles": {title: title in text for title in CANARIES}, "page_errors": errors, "text": text}
    except Exception as error:
        return {"ok": False, "error": repr(error)}


def native_state() -> dict:
    path = config / "work-parallel-native.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def coordination_state() -> dict:
    path = config / "work-parallel-coordination.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def project_dir() -> Path:
    return Path.home() / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workspace))


def main_text() -> str:
    path = project_dir() / (SESSION + ".jsonl")
    if not path.exists():
        return ""
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get("isSidechain"):
            rows.append(line)
    return "\n".join(rows)


def fork_transcripts() -> list[dict]:
    rows = []
    for meta in (project_dir() / SESSION / "subagents").glob("*.meta.json"):
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        body = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        text = body.read_text(encoding="utf-8", errors="replace") if body.exists() else ""
        rows.append({"file": body.name, "agent_id": body.stem.removeprefix("agent-"),
                     "type": info.get("agentType"), "description": str(info.get("description"))[:160],
                     "canaries": [title for title, canary in CANARIES.items() if canary in text]})
    return rows


def marker_window(title: str) -> tuple[float, float, int] | None:
    path = markers / (slug(title) + ".txt")
    if not path.exists():
        return None
    stamps = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            stamps.append(float(line.split()[0]))
        except (ValueError, IndexError):
            pass
    return (min(stamps), max(stamps), len(stamps)) if stamps else None


def idle(console: Console) -> bool:
    text = console.screen()
    return "? for shortcuts" in text and "esc to interrupt" not in text


def kill_owned_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


result = {"out": str(OUT), "base": str(BASE), "snapshot": str(SNAP), "port": port,
          "session": SESSION, "main_model": MAIN_MODEL, "seed": {"version": seeded["version"],
          "items": item_ids, "terminal_rows": 24, "stale_launch_rows": 24}, "outcome": "timeout"}
console = None
try:
    time.sleep(12)
    cmd_pid = int(next(row["ProcessId"] for row in process_tree() if row["Name"] == "cmd.exe"))
    console = Console(cmd_pid)
    deadline = time.time() + 150
    trusted = False
    while time.time() < deadline:
        time.sleep(1)
        screen = console.screen()
        if "[probe] ciel-runtime exited" in screen or "Traceback (most recent call last)" in screen:
            capture(console, "error")
            result["outcome"] = "error"
            break
        if not trusted and "trust" in screen.lower() and "Yes" in screen:
            if "❯ No" in screen or "> No" in screen:
                console.key("down", pause=1)
            capture(console, "00-trust")
            console.key("enter", pause=3)
            trusted = True
            continue
        if not ("? for shortcuts" in screen or "bypass permissions" in screen or "auto mode" in screen):
            continue
        capture(console, "01-started-idle")
        initial_api = None
        initial_seen_at = None
        deadline_four = time.time() + 240
        while time.time() < deadline_four:
            time.sleep(2)
            try:
                current = api_get("/ca/work/api/v1/parallel?session_id=" + urllib.parse.quote(IDENTITY))
            except Exception:
                continue
            confirmed = (current.get("forks") or {}).get("confirmed") or []
            priorities = sorted(row.get("priority") for row in confirmed)
            ids = {row.get("agent_id") for row in confirmed if row.get("agent_id")}
            if len(ids) >= 4 and priorities.count("P1") >= 2 and priorities.count("P2") >= 1 and priorities.count("P3") >= 1:
                initial_api = current
                initial_seen_at = time.time()
                break
        result["initial_four_epoch"] = initial_seen_at
        result["api_initial_four"] = initial_api
        if initial_api:
            capture(console, "02-four-forks-terminal")
            result["web_initial_four"] = browser_shot("03-web-four-forks")
            result["web_mobile_initial_four"] = browser_shot("03-web-four-forks-mobile", mobile=True)
            console.text("/work-mode parallel")
            console.key("enter", pause=4)
            capture(console, "04-work-mode-parallel")
        initial_rows_by_item = {row.get("item_id"): row for row in ((initial_api or {}).get("forks") or {}).get("confirmed", [])}
        initial_ids = {row.get("agent_id") for row in initial_rows_by_item.values()}
        initial_lease_ids = {row.get("lease_id") for row in initial_rows_by_item.values()}
        refill = None
        deadline_refill = time.time() + 240
        while time.time() < deadline_refill:
            time.sleep(2)
            native = native_state()
            records = list((native.get("records") or {}).values())
            next_runs = {run_id for run_id, run in (coordination_state().get("runs") or {}).items()
                         if run.get("item_id") == item_ids[NEXT_TITLE] and run.get("role") == "worker"}
            next_rows = [row for row in records if row.get("scope") == IDENTITY and row.get("lease_id") in next_runs]
            next_ids = {row.get("agent_id") for row in next_rows if row.get("agent_id")}
            new_ids = next_ids - initial_ids
            new_leases = {row.get("lease_id") for row in next_rows} - initial_lease_ids
            if new_ids and new_leases:
                refill = {"at": time.time(), "agent_ids": sorted(new_ids), "lease_ids": sorted(new_leases), "rows": next_rows}
                break
        result["automatic_refill"] = refill
        if refill:
            capture(console, "05-automatic-refill")
        windows = {title: marker_window(title) for title in CANARIES}
        result["marker_windows"] = windows
        # Native confirmation is the authoritative concurrency proof. Marker
        # overlap is supporting execution evidence and may be missing when the
        # deliberately short question worker exits before running its command.
        initial_windows = [windows[title] for title in INITIAL_TITLES if windows.get(title)]
        if len(initial_windows) >= 3:
            overlap_start = max(window[0] for window in initial_windows)
            overlap_end = min(window[1] for window in initial_windows)
            result["initial_marker_overlap_seconds"] = round(max(0, overlap_end - overlap_start), 1)
        else:
            result["initial_marker_overlap_seconds"] = 0
        result["native_final"] = native_state()
        result["coordination_final"] = coordination_state()
        transcripts = fork_transcripts()
        result["fork_transcripts"] = transcripts
        main = main_text()
        initial_rows = ((initial_api or {}).get("forks") or {}).get("confirmed") or []
        # Main may contain ordinary Work titles, but never fork-only payloads,
        # scheduler/recovery traffic, a native evidence row, or native agent ids.
        # Only model-facing conversational rows are contamination evidence.
        # Native task notifications and model-authored status reads legitimately
        # carry agent ids; API blobs/queue-operation/debug metadata are excluded.
        model_rows = []
        main_path = project_dir() / (SESSION + '.jsonl')
        for line in main_path.read_text(encoding='utf-8', errors='replace').splitlines() if main_path.exists() else []:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            message = row.get('message') or {}
            if row.get('isSidechain') or row.get('type') not in ('user', 'assistant'):
                continue
            content = message.get('content')
            if row.get('type') == 'user' and isinstance(content, str) and content.startswith('<task-notification>'):
                continue
            model_rows.append(line)
        model_text = '\n'.join(model_rows)
        forbidden = list(CANARIES.values()) + ["Your native turn ended without a durable Work exit receipt"]
        result["main_leaks"] = [value for value in forbidden if value.lower() in model_text.lower()]
        initial_priority = {priority: sum(row.get("priority") == priority for row in initial_rows)
                            for priority in ("P1", "P2", "P3")}
        transcript_agents = {row["agent_id"] for row in transcripts if row["canaries"]}
        refill_ids = set((refill or {}).get("agent_ids") or [])
        result["acceptance"] = {
            "host_started_four_without_main_prompt": initial_seen_at is not None,
            "four_distinct_native_ids": len(initial_ids) >= 4,
            "priority_use_2_1_1": initial_priority == {"P1": 2, "P2": 1, "P3": 1},
            "initial_four_overlap": initial_seen_at is not None and len(initial_ids) >= 4,
            "marker_overlap_support": result["initial_marker_overlap_seconds"] > 5,
            "automatic_next_started": bool(refill_ids and refill_ids.isdisjoint(initial_ids)),
            "fork_transcripts_cover_observed_ids": initial_ids.issubset(transcript_agents),
            "main_transcript_isolated": not result["main_leaks"],
            "desktop_capture": bool((result.get("web_initial_four") or {}).get("ok")),
            "mobile_390_capture": bool((result.get("web_mobile_initial_four") or {}).get("ok")),
            "seeded_history_did_not_block": initial_seen_at is not None,
        }
        result["outcome"] = "passed" if all(result["acceptance"].values()) else "failed"
        capture(console, "06-final")
        console.key("esc", pause=.5)
        console.text("/exit")
        console.key("enter", pause=2)
        break
    else:
        if console:
            capture(console, "timeout")
finally:
    try:
        result["tree_before_kill"] = [row["Name"] for row in process_tree()]
    except Exception as error:
        result["tree_error"] = repr(error)
    kill_owned_tree(proc.pid)
    time.sleep(2)
    owner_pids = subprocess.run(["powershell", "-NoProfile", "-Command",
        f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
        capture_output=True, text=True).stdout.split()
    result["owned_router_pids"] = owner_pids
    for owner_pid in owner_pids:
        kill_owned_tree(int(owner_pid))
    result["preserved_config"] = str(config)
    (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": result["out"], "outcome": result["outcome"],
                      "acceptance": result.get("acceptance"),
                      "initial_marker_overlap_seconds": result.get("initial_marker_overlap_seconds"),
                      "automatic_refill": result.get("automatic_refill"),
                      "main_leaks": result.get("main_leaks")}, indent=2))

if result.get("outcome") != "passed":
    raise SystemExit(1)
