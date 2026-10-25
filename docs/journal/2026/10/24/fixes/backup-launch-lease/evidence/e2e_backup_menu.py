"""Real prelaunch-menu E2E: item 18 "Back up this session now" in Windows Terminal.

python -I e2e_backup_menu.py <repo-root> <run-name>

Starts `py -3 <repo>/ciel_runtime.py cli --ca-menu` (the same launcher form as the local
ciel-runtime.cmd) in a new Windows Terminal window with a scratch HOME, APPDATA,
config, Claude config and workspace, every inherited CIEL_RUNTIME_*/CLAUDE*/
ANTHROPIC*/CODEX*/OPENAI* removed, no explicit router port (automatic selection,
as on Regina), and HTTP(S) egress pointed at a closed loopback port. A detached
agent presses only the keys that reach the backup item. Window captures use
PrintWindow on this window only. Owned processes are killed by tree at the end;
live routers are compared before and after.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(sys.argv[1]).resolve()
RUN = HERE / sys.argv[2]
RUN.mkdir(parents=True, exist_ok=True)
SHOTS = RUN / "shots"
SHOTS.mkdir(exist_ok=True)
SHOT_PS1 = HERE.parents[3] / "16/features/cielairouter-direct-path/evidence/e2e/shot_title.ps1"
LIVE_PORTS = (9385, 9464, 9489, 9505, 9524)
TITLE = f"ciel-backup-e2e-{sys.argv[2]}-{int(time.time())}"


def log(message: str) -> None:
    with open(RUN / "driver.log", "a", encoding="utf-8") as stream:
        stream.write(f"{time.strftime('%H:%M:%S')} {message}\n")


def live_health() -> dict:
    out = {}
    for port in LIVE_PORTS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                data = json.load(response)
            out[str(port)] = {"pid": data.get("pid"), "version": data.get("version")}
        except Exception as error:
            out[str(port)] = {"error": type(error).__name__}
    return out


def processes() -> list[dict]:
    raw = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return json.loads(raw or "[]")


def descendants(root: int, table: list[dict]) -> list[dict]:
    out, frontier = [], {root}
    while frontier:
        children = [p for p in table if p["ParentProcessId"] in frontier and p not in out]
        out += children
        frontier = {p["ProcessId"] for p in children}
    return out


def wait(predicate, seconds: float, label: str) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            log(f"ok: {label}")
            return True
        time.sleep(0.5)
    log(f"TIMEOUT: {label}")
    return False


def shot(label: str) -> None:
    out = SHOTS / f"{len(list(SHOTS.iterdir())) + 1:02d}-{label}.png"
    restored = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "restore_title.ps1"), TITLE],
                              capture_output=True, text=True)
    log(f"restore {label}: {(restored.stdout or restored.stderr).strip()[:200]}")
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHOT_PS1), TITLE, str(out)],
                            capture_output=True, text=True)
    log(f"shot {label}: {(result.stdout or result.stderr).strip()[:200]}")


scratch = Path(tempfile.mkdtemp(prefix="ciel-backup-e2e-"))
home, ws, cfg = scratch / "home", scratch / "ws", scratch / "home" / ".config" / "ciel-runtime"
for path in (home / "AppData/Roaming", home / "AppData/Local/Temp", ws, cfg):
    path.mkdir(parents=True, exist_ok=True)
(ws / "notes.txt").write_text("backup e2e work file\n", encoding="utf-8")
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    dead_proxy = sock.getsockname()[1]
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear
    + f'set "HOME={home}"\nset "USERPROFILE={home}"\nset "HOMEDRIVE={home.drive}"\nset "HOMEPATH={str(home)[len(home.drive):]}"\n'
    + f'set "APPDATA={home / "AppData/Roaming"}"\nset "LOCALAPPDATA={home / "AppData/Local"}"\n'
    + f'set "TEMP={home / "AppData/Local/Temp"}"\nset "TMP={home / "AppData/Local/Temp"}"\nset "XDG_CONFIG_HOME={home / ".config"}"\n'
    + f'set "HTTP_PROXY=http://127.0.0.1:{dead_proxy}"\nset "HTTPS_PROXY=http://127.0.0.1:{dead_proxy}"\n'
    + f'set "ALL_PROXY=http://127.0.0.1:{dead_proxy}"\nset "NO_PROXY=127.0.0.1,localhost,::1"\n'
    + f'set "CIEL_RUNTIME_CONFIG_DIR={cfg}"\nset "CLAUDE_CONFIG_DIR={home / ".claude"}"\nset "CODEX_HOME={home / ".codex"}"\n'
    + f'(set CIEL_RUNTIME_ & set CLAUDE & set ANTHROPIC & set CODEX & set OPENAI & set HOME & set APPDATA & set HTTP) > "{RUN / "launch-env.txt"}" 2>&1\n'
    + f'cd /d "{ws}"\npy -3 "{REPO / "ciel_runtime.py"}" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
    + "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
result: dict = {"repo": str(REPO), "scratch": str(scratch), "title": TITLE, "live_health_before": live_health()}
cmd_pid = console_pid = 0
try:
    subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])

    def find() -> bool:
        global cmd_pid, console_pid
        table = processes()
        cmds = [p for p in table if (p["Name"] or "").lower() == "cmd.exe" and str(script) in (p.get("CommandLine") or "")]
        if cmds:
            cmd_pid = cmds[0]["ProcessId"]
            kids = [p for p in descendants(cmd_pid, table) if "ciel_runtime.py" in (p.get("CommandLine") or "")]
            console_pid = kids[0]["ProcessId"] if kids else 0
        return bool(cmd_pid and console_pid)

    wait(find, 60, "cmd + menu process")
    log(f"cmd {cmd_pid} menu {console_pid}")
    subprocess.Popen([sys.executable, "-I", str(HERE / "e2e_backup_agent.py"), str(console_pid), str(RUN)], creationflags=0x00000008)
    if wait(lambda: (RUN / "marker-menu").exists(), 150, "main menu drawn"):
        shot("menu")
    if wait(lambda: (RUN / "marker-submenu").exists(), 90, "backup submenu with 'Back up this session now' selected"):
        shot("submenu")
    if wait(lambda: (RUN / "marker-result").exists(), 300, "backup result text"):
        time.sleep(0.5)
        shot("result")
    try:
        result["outcome"] = json.loads((RUN / "outcome.json").read_text(encoding="utf-8"))["outcome"]
    except OSError:
        result["outcome"] = "agent-ended-without-outcome"
    table = processes()
    tree = descendants(cmd_pid, table) if cmd_pid else []
    result["owned_processes"] = [{"pid": p["ProcessId"], "ppid": p["ParentProcessId"], "name": p["Name"],
                                  "cmd": (p.get("CommandLine") or "")[:240]} for p in tree]
    registry = cfg / "workspace-router-ports.json"
    if registry.exists():
        result["registry_after"] = json.loads(registry.read_text(encoding="utf-8"))
    state = cfg / "session-backup-state.json"
    if state.exists():
        result["backup_state"] = json.loads(state.read_text(encoding="utf-8"))
finally:
    (RUN / "stop").write_text("stop", encoding="utf-8")
    time.sleep(1.0)
    if cmd_pid:
        subprocess.run(["taskkill", "/PID", str(cmd_pid), "/T", "/F"], capture_output=True)
    result["live_health_after"] = live_health()
    result["live_unchanged"] = all(result["live_health_after"].get(k, {}).get("pid") == v.get("pid")
                                   for k, v in result["live_health_before"].items() if v.get("pid"))
    (RUN / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ("outcome", "live_unchanged", "title")}, indent=2))
