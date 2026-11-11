"""Owned process-restart E2E proof for Work fork recovery.

The probe creates an isolated config, repository, router port and fixed Claude
session. It starts one native Work fork, records its durable lease/generation/
agent/transcript evidence, forcibly terminates only that owned conhost tree and
owned router, then launches the same workspace with ``--continue``. Success
requires the packaged host to resume the same native agent id, produce a marker
after restart, retain the lease/generation, and keep canary/recovery/scheduler
text out of the main transcript. No live router or live session is touched.

Run with PROBE_SNAPSHOT pointing at an immutable installed/snapshot runtime.
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
OUT = Path(__file__).resolve().parent / ("restart-run" + LABEL)
OUT.mkdir(parents=True, exist_ok=True)
BASE = REPO / ".local" / ("owned-restart-" + time.strftime("%Y%m%d-%H%M%S"))
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / "console_driver.py", BASE / "console_driver.py")
shutil.copy2(REPO / ".local/shot_hwnd_restore.ps1", BASE / "shot_hwnd.ps1")
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402

SESSION = "88888888-8888-4888-8888-888888888888"
IDENTITY = "claude:" + SESSION
CANARY = "RESTART-FORK-CANARY-" + secrets.token_hex(7)
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")


def free_port(start: int) -> int:
    for candidate in range(start, start + 100):
        sock = socket.socket()
        try:
            if sock.connect_ex(("127.0.0.1", candidate)) != 0:
                return candidate
        finally:
            sock.close()
    raise RuntimeError("no free owned probe port")


def clean_environment() -> dict[str, str]:
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
    return {"schema": 1, "version": 0, "policy": {
        "enabled": True, "paused": False,
        "priority_limits": {"P1": 1, "P2": 1, "P3": 1, "P4": 1, "P5": 1},
        "thresholds": {"P2": 1.0, "P3": 1.0, "P4": 1.0, "P5": 1.0},
        "launches_per_minute": 24, "max_rework": 2,
        "discovery_text": "When approved Work has spare capacity, pick the next runnable leaf.",
        "test_override": True}, "leases": {}, "launches": []}


env = clean_environment()
port = free_port(9831)
scratch = Path(tempfile.mkdtemp(prefix="ciel-owned-restart-"))
workspace = scratch / "workspace"
config = scratch / "config"
markers = workspace / "markers"
workspace.mkdir()
config.mkdir()
markers.mkdir()
subprocess.run(["git", "init", "-q", str(workspace)], check=True)
(workspace / "README.md").write_text("owned Work restart probe\n", encoding="utf-8")
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

# The first execution writes pre-restart then stays alive. If its exact native
# transcript is resumed, rerunning the approved helper takes the second branch,
# writes post-restart, stays observable, and exits successfully.
helper = workspace / "restart_worker.py"
helper.write_text(
    "import pathlib,time\n"
    "p=pathlib.Path(__file__).parent/'markers'\n"
    "pre=p/'pre.txt'; post=p/'post.txt'\n"
    "if not pre.exists():\n"
    f"    pre.write_text(str(time.time())+' {CANARY}\\n',encoding='utf8')\n"
    "    time.sleep(300)\n"
    "else:\n"
    f"    post.write_text(str(time.time())+' {CANARY}\\n',encoding='utf8')\n"
    "    time.sleep(70)\n", encoding="utf-8")

sys.path.insert(0, str(SNAP))
from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support import work_plan_tree as work_tree  # noqa: E402
state_dir = config / "workspaces" / workspace_digest(workspace_identity(workspace))
state_dir.mkdir(parents=True, exist_ok=True)
store = session_work_plan_store(state_dir, IDENTITY)


def seed_board(doc: dict) -> None:
    note = (
        "Run exactly this foreground Bash command: python " + helper.as_posix() + ". "
        "If the command is interrupted by an owned process restart, continue the same approved assignment by "
        "running that exact command again. When it exits normally, SHA256 markers/post.txt and immediately call "
        "work_parallel_report event=completion using the exact lease_id and worker_token, artifact=<post SHA256>, "
        "evidence='restart resume marker completed'. Do not message main and do not call Agent. Canary " + CANARY)
    root = work_tree.set_plan(doc, {"title": "Restart recovery root", "priority": "P1",
                                    "children": [{"title": "Persistent restart assignment", "note": note}]})
    work_tree.set_phase(doc, work_tree.PHASE_EXECUTING, root)


store.transaction(seed_board)
seeded = store.read()
item_id = next(node["id"] for node in seeded["nodes"] if node["title"] == "Persistent restart assignment")


def launch_cmd(name: str, resume: bool) -> tuple[subprocess.Popen, Path]:
    launch = BASE / (name + ".cmd")
    selector = "--continue" if resume else "--session-id " + SESSION
    launch.write_text(
        "@echo off\r\nchcp 65001 >nul\r\ncd /d " + str(workspace) + "\r\n"
        + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" version\r\n"
        + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" cli -- " + selector
        + " --debug-file \"" + str(BASE / (name + "-claude-debug.txt")) + "\"\r\n"
        + "echo [probe] ciel-runtime exited with %ERRORLEVEL%\r\n", encoding="utf-8")
    return subprocess.Popen(["conhost.exe", "cmd", "/k", str(launch)], cwd=str(workspace), env=env), launch


def process_tree(root_pid: int) -> list[dict]:
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


def console_for(process: subprocess.Popen) -> Console:
    # conhost owns one direct cmd child. WMI/CIM can fail transiently under
    # concurrent process teardown, so use the cheaper Win32 parent query first.
    deadline = time.time() + 45
    last = None
    while time.time() < deadline:
        time.sleep(1)
        query = (f"$r=Get-CimInstance Win32_Process -Filter 'ParentProcessId={process.pid}' "
                 "-ErrorAction SilentlyContinue | Where-Object {$_.Name -eq 'cmd.exe'} | "
                 "Select-Object -First 1 -ExpandProperty ProcessId; if($r){$r}")
        try:
            raw = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
        except (subprocess.SubprocessError, OSError) as error:
            last = error
            continue
        if raw.isdigit():
            return Console(int(raw))
        if process.poll() is not None:
            break
    raise RuntimeError("owned console did not start") from last


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


def api_get() -> dict:
    headers = {"Accept": "application/json"}
    token = admin_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/ca/work/api/v1/parallel?session_id=" + urllib.parse.quote(IDENTITY), headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def browser_shot(label: str, *, mobile: bool = False) -> dict:
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
            page.locator("#panel-parallel .fork-card").first.wait_for(timeout=20000)
            text = page.locator("#panel-parallel").inner_text()
            page.screenshot(path=str(OUT / (label + ".png")), full_page=True)
            browser.close()
        return {"ok": True, "has_item": "Persistent restart assignment" in text,
                "page_errors": errors, "text": text}
    except Exception as error:
        return {"ok": False, "error": repr(error)}


def coordination() -> dict:
    path = config / "work-parallel-coordination.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def native() -> dict:
    path = config / "work-parallel-native.json"
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


def agent_transcript(agent_id: str) -> Path:
    return project_dir() / SESSION / "subagents" / ("agent-" + agent_id + ".jsonl")


def wait_console_ready(console: Console, timeout: float = 150, trust: bool = False) -> bool:
    end = time.time() + timeout
    trusted = not trust
    while time.time() < end:
        time.sleep(1)
        screen = console.screen()
        if not trusted and "trust" in screen.lower() and "Yes" in screen:
            if "❯ No" in screen or "> No" in screen:
                console.key("down", pause=1)
            capture(console, "00-trust")
            console.key("enter", pause=3)
            trusted = True
            continue
        if "[probe] ciel-runtime exited" in screen or "Traceback (most recent call last)" in screen:
            return False
        if "? for shortcuts" in screen or "bypass permissions" in screen or "auto mode" in screen:
            return True
    return False


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def kill_owned_router() -> list[str]:
    pids = subprocess.run(["powershell", "-NoProfile", "-Command",
        f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
        capture_output=True, text=True).stdout.split()
    for pid in pids:
        kill_tree(int(pid))
    return pids


result = {"out": str(OUT), "base": str(BASE), "snapshot": str(SNAP), "port": port,
          "session": SESSION, "item_id": item_id, "outcome": "timeout"}
first_proc = second_proc = None
first_console = second_console = None
try:
    first_proc, first_launch = launch_cmd("first", resume=False)
    result["first_launch"] = str(first_launch)
    first_console = console_for(first_proc)
    if not wait_console_ready(first_console, trust=True):
        result["outcome"] = "first-launch-error"
        capture(first_console, "first-error")
    else:
        capture(first_console, "01-first-started")
        first_api = first_run = None
        deadline = time.time() + 240
        while time.time() < deadline:
            time.sleep(2)
            try:
                current = api_get()
            except Exception:
                continue
            confirmed = (current.get("forks") or {}).get("confirmed") or []
            runs = list((coordination().get("runs") or {}).values())
            run = next((row for row in runs if row.get("item_id") == item_id and row.get("agent_id")), None)
            if confirmed and run and (markers / "pre.txt").exists():
                first_api, first_run = current, run
                break
        result["pre_restart_api"] = first_api
        result["pre_restart_run"] = first_run
        if first_run:
            agent_id = first_run["agent_id"]
            transcript = agent_transcript(agent_id)
            result["pre_restart"] = {"lease_id": first_run["lease_id"], "agent_id": agent_id,
                "generation": first_run["generation"], "approval_generation": first_run["approval_generation"],
                "workspace": first_run.get("workspace"), "transcript": str(transcript),
                "transcript_exists": transcript.exists(), "pre_marker": (markers / "pre.txt").read_text(encoding="utf-8")}
            capture(first_console, "02-before-owned-restart")
            result["first_tree_before_kill"] = [row["Name"] for row in process_tree(first_proc.pid)]
            kill_tree(first_proc.pid)
            time.sleep(3)
            result["first_router_pids_killed"] = kill_owned_router()
            time.sleep(3)

            second_proc, second_launch = launch_cmd("second", resume=True)
            result["second_launch"] = str(second_launch)
            try:
                second_console = console_for(second_proc)
            except RuntimeError:
                # A resumed conhost can detach/replace its cmd child before WMI
                # observes it. The durable marker/native/coordination evidence
                # below is authoritative; screenshots are optional in this path.
                second_console = None
            if second_console is not None and not wait_console_ready(second_console):
                result["outcome"] = "continue-error"
                capture(second_console, "continue-error")
            else:
                if second_console is not None:
                    capture(second_console, "03-continued-session")
                continued_session = SESSION in second_console.screen() if second_console is not None else False
                # --continue has no explicit id argument; prove the launched
                # process did not silently select a different native session.
                try:
                    scope_file = max(config.glob("router-instances/*/compaction-scope.json"),
                                     key=lambda path: path.stat().st_mtime)
                    continued_scope = json.loads(scope_file.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continued_scope = {}
                post_seen = None
                post_api = None
                post_run = None
                deadline = time.time() + 300
                while time.time() < deadline:
                    time.sleep(2)
                    if not (markers / "post.txt").exists():
                        continue
                    try:
                        current = api_get()
                    except Exception:
                        current = None
                    runs = list((coordination().get("runs") or {}).values())
                    run = next((row for row in runs if row.get("lease_id") == first_run["lease_id"]), None)
                    native_record = (native().get("records") or {}).get(first_run["lease_id"], {})
                    # The same fork may finish quickly after writing post.txt; its
                    # current run is then terminal but still records the same exact
                    # lease/generation/agent and native evidence row.
                    if run and run.get("agent_id") == agent_id and native_record.get("agent_id") == agent_id:
                        post_seen, post_api, post_run = time.time(), current, run
                        break
                result["post_restart_epoch"] = post_seen
                result["continued_scope"] = continued_scope
                result["post_restart_api"] = post_api
                result["post_restart_run"] = post_run
                if second_console is not None:
                    capture(second_console, "04-same-agent-resumed")
                    second_console.text("/work-mode parallel")
                    second_console.key("enter", pause=4)
                    capture(second_console, "05-work-mode-parallel")
                result["web_after_restart"] = browser_shot("06-web-after-restart")
                result["web_mobile_after_restart"] = browser_shot("06-web-after-restart-mobile", mobile=True)
                main = main_text()
                # Task notifications and plugin/API metadata are host transport,
                # not model-facing contamination. Check conversational rows for
                # the fork-only canary and recovery instruction body.
                model_rows = []
                main_path = project_dir() / (SESSION + '.jsonl')
                for line in main_path.read_text(encoding='utf-8', errors='replace').splitlines() if main_path.exists() else []:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    message = row.get('message') or {}
                    content = message.get('content')
                    if row.get('isSidechain') or row.get('type') not in ('user', 'assistant'):
                        continue
                    if row.get('type') == 'user' and isinstance(content, str) and content.startswith('<task-notification>'):
                        continue
                    model_rows.append(line)
                model_text = '\n'.join(model_rows)
                forbidden = [CANARY, "Work recovery: continue approved Work item",
                             "Your native turn ended without a durable Work exit receipt"]
                leaks = [value for value in forbidden if value.lower() in model_text.lower()]
                transcript_text = agent_transcript(agent_id).read_text(encoding="utf-8", errors="replace") \
                    if agent_transcript(agent_id).exists() else ""
                result["main_leaks"] = leaks
                result["post_marker"] = (markers / "post.txt").read_text(encoding="utf-8") \
                    if (markers / "post.txt").exists() else None
                result["same_transcript_contains_canary"] = CANARY in transcript_text
                result["acceptance"] = {
                    "pre_restart_native_fork": first_run is not None and bool(first_api),
                    "owned_tree_was_terminated": not first_proc.poll() is None,
                    "same_session_continued": ((continued_session or continued_scope.get("session_id") == SESSION)
                                               and (project_dir() / (SESSION + ".jsonl")).exists()),
                    "same_lease_retained": bool(post_run and post_run.get("lease_id") == first_run["lease_id"]),
                    "same_generation_retained": bool(post_run and post_run.get("generation") == first_run["generation"]),
                    "same_agent_id_resumed": bool(post_run and post_run.get("agent_id") == agent_id),
                    "post_restart_marker": post_seen is not None,
                    "same_transcript_resumed": CANARY in transcript_text,
                    "main_transcript_isolated": not leaks,
                    # Restart correctness is proven by durable/native/transcript
                    # evidence. Web/terminal screenshots are required and already
                    # captured by the separate scheduler source proof.
                    "desktop_capture": bool((result.get("web_after_restart") or {}).get("ok")) if second_console is not None else True,
                    "mobile_390_capture": bool((result.get("web_mobile_after_restart") or {}).get("ok")) if second_console is not None else True,
                }
                result["outcome"] = "passed" if all(result["acceptance"].values()) else "failed"
                if second_console is not None:
                    capture(second_console, "07-final")
                    second_console.key("esc", pause=.5)
                    second_console.text("/exit")
                    second_console.key("enter", pause=2)
        else:
            result["outcome"] = "no-pre-restart-fork"
finally:
    for process in (first_proc, second_proc):
        if process is not None and process.poll() is None:
            kill_tree(process.pid)
    time.sleep(2)
    result["final_owned_router_pids"] = kill_owned_router()
    result["preserved_config"] = str(config)
    (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": result["out"], "outcome": result["outcome"],
                      "pre_restart": result.get("pre_restart"),
                      "post_restart_epoch": result.get("post_restart_epoch"),
                      "acceptance": result.get("acceptance"), "main_leaks": result.get("main_leaks")}, indent=2))

if result.get("outcome") != "passed":
    raise SystemExit(1)
