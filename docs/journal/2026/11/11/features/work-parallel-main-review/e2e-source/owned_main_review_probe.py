"""Owned real-console E2E for Work main review and rework recovery.

Creates only owned state: an immutable snapshot of the frozen source, a temp
CIEL_RUNTIME_CONFIG_DIR, one git repository, one router port, fixed Claude
session, conhost process trees and evidence under this folder. It never touches
live router/session state and kills only command lines/ports it owns.

The real Claude main session and native forks make every Work decision. This
runner observes durable state and captures evidence; it does not replace or
script model decisions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import threading
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[8]
FREEZE = SOURCE / ".local/main-review-ui/FREEZE-SHA256.txt"
TOOLS = SOURCE / "docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui"
LIVE_PIDS = (23728, 39984, 21912, 42764, 42924, 36344, 40616, 45652, 45428,
             46232, 46508, 2272, 9764, 12240, 48104, 9008, 44500, 20744)
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")
MAIN_MODEL = "_Walkie_Opus55-Sol61-Fast"
ROUTER_BIND = "127.0.0.1"
GATE_HOST = "127.0.0.2"
SESSION = "99999999-9999-4999-8999-999999999991"
IDENTITY = "claude:" + SESSION
STAMP = time.strftime("%Y%m%d-%H%M%S")
OUT = Path(__file__).resolve().parent / ("run-" + STAMP)
OUT.mkdir(parents=True)
# Recovery record first: another interruption must be able to find this exact runner.
(OUT / "probe-process.json").write_text(json.dumps({
    "pid": os.getpid(), "ppid": os.getppid(), "argv": sys.argv, "executable": sys.executable,
    "started_at": time.time()}, indent=2), encoding="utf-8")
BASE = SOURCE / ".local" / ("owned-main-review-" + STAMP)
SNAP = SOURCE / ".local" / ("owned-main-review-snapshot-" + STAMP)
BASE.mkdir(parents=True)
shutil.copy2(TOOLS / "console_driver.py", BASE / "console_driver.py")
shutil.copy2(SOURCE / ".local/shot_hwnd_restore.ps1", BASE / "shot_hwnd.ps1")
sys.path.insert(0, str(BASE))
from console_driver import Console  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_freeze() -> dict[str, str]:
    rows = {}
    for line in FREEZE.read_text(encoding="utf-8-sig").splitlines()[1:]:
        if "  " not in line:
            continue
        digest, name = line.split("  ", 1)
        name = name.strip().replace("\\", "/")
        if name.startswith("./"):
            name = name[2:]
        rows[name] = digest
    if len(rows) != 29:
        raise RuntimeError(f"expected 29 freeze entries, got {len(rows)}")
    return rows


def build_snapshot(freeze: dict[str, str]) -> dict:
    if SNAP.exists():
        raise RuntimeError("snapshot already exists")
    SNAP.mkdir()
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache")
    for name in ("ciel_runtime_support", "scripts", "tests", "npm-bin"):
        shutil.copytree(SOURCE / name, SNAP / name, ignore=ignored)
    for path in SOURCE.iterdir():
        if path.is_file() and path.name != ".git":
            shutil.copy2(path, SNAP / path.name)
    for rel in freeze:
        target = SNAP / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / rel, target)
    shutil.copy2(FREEZE, SNAP / "FREEZE-SHA256.txt")
    source_bad, snapshot_bad = {}, {}
    for rel, expected in freeze.items():
        actual_source, actual_snap = sha(SOURCE / rel), sha(SNAP / rel)
        if actual_source != expected:
            source_bad[rel] = actual_source
        if actual_snap != expected:
            snapshot_bad[rel] = actual_snap
    if source_bad or snapshot_bad:
        raise RuntimeError(f"freeze mismatch source={list(source_bad)} snapshot={list(snapshot_bad)}")
    # The runtime must not write bytecode; read-only files make accidental source
    # mutation fail closed. Directories remain writable only for OS traversal.
    for path in SNAP.rglob("*"):
        if path.is_file():
            try:
                path.chmod(stat.S_IREAD)
            except OSError:
                pass
    return {"entries": len(freeze), "source_mismatch": source_bad, "snapshot_mismatch": snapshot_bad,
            "snapshot": str(SNAP)}


def clean_environment() -> tuple[dict[str, str], list[str]]:
    inherited = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
    return env, inherited


def live_rows() -> list[dict]:
    command = ("Get-CimInstance Win32_Process | Where-Object {$_.ProcessId -in @(" + ",".join(map(str, LIVE_PIDS))
               + ")} | Select-Object ProcessId,ParentProcessId,Name,@{n='CreationDateUtc';e={$_.CreationDate.ToUniversalTime().ToString('o')}},CommandLine | ConvertTo-Json")
    raw = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True,
                         text=True, timeout=30).stdout.strip()
    value = json.loads(raw) if raw else []
    return value if isinstance(value, list) else [value]


def free_port(start: int = 9931) -> int:
    for candidate in range(start, start + 100):
        clear = True
        for host in ("127.0.0.1", "127.0.0.2"):
            sock = socket.socket()
            try:
                if sock.connect_ex((host, candidate)) == 0:
                    clear = False
                    break
            finally:
                sock.close()
        if clear:
            return candidate
    raise RuntimeError("no free owned port")


def process_tree(root_pid: int) -> list[dict]:
    script = (f"$all=Get-CimInstance Win32_Process; $ids=@({root_pid}); do {{$new=@($all | Where-Object "
              "{$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | Select-Object -ExpandProperty ProcessId); "
              "$ids += $new} while ($new.Count); $all | Where-Object {$_.ProcessId -in $ids} | "
              "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json")
    raw = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                         text=True, timeout=30).stdout.strip()
    value = json.loads(raw) if raw else []
    return value if isinstance(value, list) else [value]


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30)


def kill_owned_commands(markers: list[str]) -> list[int]:
    quoted = ",".join("'" + value.replace("'", "''") + "'" for value in markers)
    script = ("$m=@(" + quoted + "); Get-CimInstance Win32_Process | Where-Object { $c=$_.CommandLine; "
              "$c -and $_.ProcessId -ne $PID -and ($m | Where-Object {$c.Contains($_)}) } | "
              "Select-Object -ExpandProperty ProcessId")
    raw = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                         text=True, timeout=30).stdout.split()
    pids = [int(value) for value in raw if value.isdigit() and int(value) != os.getpid()]
    for pid in pids:
        kill_tree(pid)
    return pids


freeze = parse_freeze()
snapshot_proof = build_snapshot(freeze)
env, stripped = clean_environment()
live_before = live_rows()
if {row.get("ProcessId") for row in live_before} != set(LIVE_PIDS):
    raise RuntimeError("one or more protected live PIDs were absent before the probe")

port = free_port()
scratch = Path(tempfile.mkdtemp(prefix="ciel-owned-main-review-"))
workspace = scratch / "workspace"
config = scratch / "config"
# CIEL_RUNTIME_TEST_ISOLATED redirects the launcher's CLI assets (settings,
# slash commands, statusline) to CONFIG_DIR/.claude instead of the user's real
# profile; Claude reads the same directory through CLAUDE_CONFIG_DIR.
claude_config = config / ".claude"
workspace.mkdir()
config.mkdir()
claude_config.mkdir()
subprocess.run(["git", "init", "-q", str(workspace)], check=True)
(workspace / "README.md").write_text("owned main review E2E\n", encoding="utf-8")
subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
subprocess.run(["git", "-C", str(workspace), "-c", "user.name=probe",
                "-c", "user.email=probe@localhost", "commit", "-q", "-m", "probe base"], check=True)

# Minimal provider config: only the cielairouter provider entry (its key is the
# one credential needed) plus non-secret behavior settings. Never written to evidence.
real_config = Path(os.environ["APPDATA"]) / "ciel-runtime"
shutil.copy2(real_config / "model-registry.json", config / "model-registry.json")
live_config = json.loads((real_config / "config.json").read_text(encoding="utf-8"))
provider_entry = dict((live_config.get("providers") or {}).get("cielairouter") or {})
if not provider_entry.get("api_key"):
    raise RuntimeError("owned provider config is unavailable")
provider_entry["current_model"] = MAIN_MODEL
copied = {key: live_config[key] for key in ("claude_code", "claude_stream_recovery", "language", "web_search",
                                            "work_plan_approval_ask") if key in live_config}
copied.update(providers={"cielairouter": provider_entry}, current_provider="cielairouter",
              work_mode=True, work_auto_continue=False, work_side_intake={"enabled": False})
(config / "config.json").write_text(json.dumps(copied, indent=2), encoding="utf-8")
del live_config, provider_entry
config_proof = {"top_level_keys": sorted(copied), "providers": sorted(copied["providers"]),
                "provider_keys": sorted(copied["providers"]["cielairouter"])}

# Keep Claude's session history, teams, settings and trust database inside this
# owned run. The provider login is supplied by the owned router, so no Claude
# credential or token is copied. Minimal onboarding state avoids any write to
# the user's real ~/.claude.json or ~/.claude tree.
(claude_config / "settings.json").write_text(json.dumps({
    "skipDangerousModePermissionPrompt": True,
    "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
}, indent=2), encoding="utf-8")
(claude_config / ".claude.json").write_text(json.dumps({
    "hasCompletedOnboarding": True,
    "lastOnboardingVersion": "2.1.322",
    "numStartups": 3,
    "bypassPermissionsModeAccepted": True,
    "autoUpdates": False,
}, indent=2), encoding="utf-8")

policy = {"enabled": True, "paused": False,
          "priority_limits": {"P1": 2, "P2": 1, "P3": 1, "P4": 1, "P5": 1},
          "thresholds": {"P2": 1.0, "P3": 1.0, "P4": 1.0, "P5": 1.0},
          "launches_per_minute": 24, "max_rework": 2,
          "discovery_text": "When approved Work has capacity, start the next runnable leaf.",
          "test_override": True}
(config / "work-parallel.json").write_text(json.dumps({"schema": 1, "version": 0, "policy": policy,
                                                        "leases": {}, "launches": []}, indent=2),
                                             encoding="utf-8")

env.update(CIEL_RUNTIME_HOME_OVERRIDE=str(SNAP), CIEL_RUNTIME_CONFIG_DIR=str(config),
           CIEL_RUNTIME_LAUNCH_CWD=str(workspace), CIEL_RUNTIME_ROUTER_PORT=str(port),
           CIEL_RUNTIME_SKIP_MENU="1", CIEL_RUNTIME_SKIP_SELF_UPDATE="1",
           CIEL_RUNTIME_TEST_ISOLATED="1", CLAUDE_CONFIG_DIR=str(claude_config),
           # Router listens on 127.0.0.1; the owned Claude (model + MCP) uses
           # 127.0.0.2, where this runner's response-boundary gate forwards it.
           CIEL_RUNTIME_ROUTER_BIND_HOST=ROUTER_BIND, CIEL_RUNTIME_ROUTER_CLIENT_HOST=GATE_HOST,
           DISABLE_AUTOUPDATER="1",
           PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
# The runner's own children (git, powershell, browser) must not see live values either.
for _name in [k for k in os.environ if k.upper().startswith(PREFIXES)]:
    os.environ.pop(_name, None)

sys.path.insert(0, str(SNAP))
from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
from ciel_runtime_support import work_plan_tree as work_tree  # noqa: E402
# Under CIEL_RUNTIME_TEST_ISOLATED the workspace state dir is CONFIG_DIR itself
# (runtime_paths.WORKSPACE_STATE_DIR); the digest is kept for the record.
workspace_state_digest = workspace_digest(workspace_identity(workspace))
state_dir = config
board_store = session_work_plan_store(state_dir, IDENTITY)
# Fail fast on a seed the product rejects (attempt run-20261111-104734 seeded
# P2-P5=0, which validate_policy refuses: every Work call then failed).
from ciel_runtime_support.work_parallel import WorkParallelStore  # noqa: E402
seed_policy_check = WorkParallelStore(config / "work-parallel.json").read()["policy"]
if seed_policy_check["priority_limits"]["P1"] != 2:
    raise RuntimeError("seeded P1 worker limit was not accepted")

PASS_TITLE = "Passing implementation"
FAIL_TITLE = "Failing implementation then rework"
PASS_NOTE = """Create pass_solution.py with function add(a,b) returning a+b. Create test_pass.py using unittest and asserting add(2,3)==5. Run exactly `python -m unittest -v test_pass.py`. Do not change the test after running it. If it passes, compute SHA256 of pass_solution.py and immediately call work_parallel_report event=completion with the exact lease_id and worker_token, artifact=<that SHA256>, evidence='provided passing test passed', detail={summary:'pass_solution implements addition and the provided test passes',coverage:[{item_id:<assigned item id>,verified:true}],verification:'python -m unittest -v test_pass.py: PASS'}. Do not call Agent or message main."""
FAIL_NOTE = """Use fail_solution.py and test_fail.py. If this assignment role is worker / generation 1: create fail_solution.py with value() returning 1; create test_fail.py using unittest and asserting value()==2; run exactly `python -m unittest -v test_fail.py`; deliberately do not fix the implementation and do not alter the test. Then compute SHA256 of fail_solution.py and call work_parallel_report event=completion with the exact lease_id and worker_token, artifact=<that SHA256>, evidence='provided failing test failed', detail={summary:'the provided test fails because value returned 1 instead of 2',coverage:[{item_id:<assigned item id>,verified:false}],verification:'python -m unittest -v test_fail.py: FAIL expected 2 got 1'}. If this assignment role is resume / generation 2: retain test_fail.py unchanged, fix fail_solution.py so value() returns 2, run the same unittest until it passes, then report completion with the new SHA256, evidence='provided failing test fixed and passed', summary='rework fixed value and the unchanged provided test passes', verified:true, verification='python -m unittest -v test_fail.py: PASS'. Do not call Agent or message main."""


def seed(doc: dict) -> None:
    for title, note in ((PASS_TITLE, PASS_NOTE), (FAIL_TITLE, FAIL_NOTE)):
        root = work_tree.set_plan(doc, {"title": title + " root", "priority": "P1",
                                        "children": [{"title": title, "note": note}]})
        work_tree.set_phase(doc, work_tree.PHASE_EXECUTING, root)


board_store.transaction(seed)
seeded = board_store.read()
items = {node["title"]: node["id"] for node in seeded["nodes"] if node["title"] in (PASS_TITLE, FAIL_TITLE)}
if set(items) != {PASS_TITLE, FAIL_TITLE}:
    raise RuntimeError("board seed failed")

launchers: list[tuple[subprocess.Popen, Path]] = []


def launch(name: str, resume: bool) -> tuple[subprocess.Popen, Path]:
    path = BASE / (name + ".cmd")
    selector = "--continue" if resume else "--session-id " + SESSION
    path.write_text("@echo off\r\nchcp 65001 >nul\r\ncd /d " + str(workspace) + "\r\n"
                    + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" version\r\n"
                    + "py -3 \"" + str(SNAP / "ciel_runtime.py") + "\" cli -- " + selector
                    + " --debug-file \"" + str(BASE / (name + "-claude-debug.txt")) + "\"\r\n"
                    + "echo [probe] ciel-runtime exited with %ERRORLEVEL%\r\n", encoding="utf-8")
    proc = subprocess.Popen(["conhost.exe", "cmd", "/k", str(path)], cwd=str(workspace), env=env)
    launchers.append((proc, path))
    return proc, path


def console_for(proc: subprocess.Popen) -> Console:
    end = time.time() + 45
    while time.time() < end:
        time.sleep(.5)
        query = (f"$r=Get-CimInstance Win32_Process -Filter 'ParentProcessId={proc.pid}' -ErrorAction SilentlyContinue "
                 "| Where-Object {$_.Name -eq 'cmd.exe'} | Select-Object -First 1 -ExpandProperty ProcessId; if($r){$r}")
        raw = subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True,
                             text=True, timeout=15).stdout.strip()
        if raw.isdigit():
            return Console(int(raw))
        if proc.poll() is not None:
            break
    raise RuntimeError("owned console did not start")


def capture(console: Console | None, label: str) -> str:
    if console is None:
        return ""
    text = console.screen()
    (OUT / (label + ".txt")).write_text(text, encoding="utf-8")
    shot = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(BASE / "shot_hwnd.ps1"), str(int(console.hwnd)), str(OUT / (label + ".png"))],
                          capture_output=True, text=True, timeout=45, creationflags=0x08000000)
    (OUT / (label + "-capture.log")).write_text(shot.stdout + shot.stderr, encoding="utf-8")
    return text


def console_checkpoint(console: Console | None, label: str) -> None:
    if console is not None:
        (OUT / (label + ".txt")).write_text(console.screen(), encoding="utf-8")


def wait_ready(console: Console, trust: bool, timeout: float = 180) -> bool:
    end, trusted = time.time() + timeout, not trust
    trust_moves = 0
    while time.time() < end:
        time.sleep(.5)
        screen = console.screen()
        if not trusted and "trust" in screen.lower() and "Yes" in screen:
            yes_selected = ("❯ Yes" in screen or "> Yes" in screen or
                            "❯   Yes" in screen or ">   Yes" in screen)
            no_selected = "❯ No" in screen or "> No" in screen
            if not yes_selected:
                if trust_moves >= 4:
                    capture(console, "00-trust-unresolved")
                    return False
                console.key("down" if no_selected else "up", pause=1)
                trust_moves += 1
                continue
            capture(console, "00-trust")
            console.key("enter", pause=3)
            trusted = True
            continue
        if "[probe] ciel-runtime exited" in screen or "Traceback (most recent call last)" in screen:
            return False
        if "? for shortcuts" in screen or "bypass permissions" in screen or "auto mode" in screen:
            return True
    return False


from ciel_runtime_support.durable_file_lock import exclusive_file_lock  # noqa: E402


def read_json(path: Path) -> dict:
    # Read under the store's own advisory lock: an unlocked open handle on
    # Windows makes the router's os.replace fail (measured PermissionError 13).
    for _ in range(10):
        try:
            if not path.exists():
                return {}
            with exclusive_file_lock(path):
                return json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            time.sleep(.05)
    return {}


class ResponseGate:
    """Owned HTTP gate between the owned Claude and the owned router.

    Every byte is forwarded unchanged except one armed JSON-RPC tools/call: that
    request is held and never reaches the router, so its result is never
    committed and no response returns. The runner then confirms durable state
    and kills the owned processes. Only non-secret request fields are logged.
    """

    def __init__(self, listen: tuple[str, int], upstream: tuple[str, int], log_path: Path):
        self.upstream, self.log_path = upstream, log_path
        self.rules: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.held: list[socket.socket] = []
        self.server = socket.socket()
        self.server.bind(listen)
        self.server.listen(128)
        threading.Thread(target=self._accept, daemon=True).start()

    def log(self, row: dict) -> None:
        row = {"at": time.time(), **row}
        with self.lock, self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def arm(self, name: str, predicate) -> threading.Event:
        event = threading.Event()
        with self.lock:
            self.rules[name] = {"predicate": predicate, "event": event, "hit": None, "active": True,
                                "held_requests": []}
        self.log({"gate": name, "armed": True})
        return event

    def hit(self, name: str) -> dict | None:
        return self.rules.get(name, {}).get("hit")

    def release_held(self) -> int:
        with self.lock:
            held, self.held = self.held, []
        for sock in held:
            try:
                sock.close()
            except OSError:
                pass
        return len(held)

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    @staticmethod
    def _pipe(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        for sock in (source, target):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _match(self, head: bytes, body: bytes) -> str | None:
        if not head.startswith(b"POST /ca/mcp"):
            return None
        try:
            request = json.loads(body.decode("utf-8"))
        except ValueError:
            return None
        params = request.get("params") if isinstance(request, dict) else None
        if not isinstance(params, dict) or request.get("method") != "tools/call":
            return None
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        with self.lock:
            rules = [(name, rule) for name, rule in self.rules.items() if rule["active"]]
        for name, rule in rules:
            # Every later matching request is held too until disarm, so a host
            # retry can never commit the gated step before the owned kill.
            if rule["predicate"](str(params.get("name") or ""), args):
                seen = {"at": time.time(), "tool": params.get("name"), "event": args.get("event"),
                        "operation": args.get("operation"), "lease_id": args.get("lease_id")}
                with self.lock:
                    if rule["hit"] is None:
                        rule["hit"] = seen
                    rule["held_requests"].append(seen)
                return name
        return None

    def disarm(self, name: str) -> None:
        with self.lock:
            if name in self.rules:
                self.rules[name]["active"] = False
        self.log({"gate": name, "disarmed": True})

    def _serve(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self.upstream, timeout=10)
            upstream.settimeout(None)
        except OSError:
            client.close()
            return
        threading.Thread(target=self._pipe, args=(upstream, client), daemon=True).start()
        buffer = b""
        try:
            while True:
                while b"\r\n\r\n" not in buffer:
                    data = client.recv(65536)
                    if not data:
                        raise EOFError
                    buffer += data
                head, buffer = buffer.split(b"\r\n\r\n", 1)
                head += b"\r\n\r\n"
                lowered = head.lower()
                if b"transfer-encoding: chunked" in lowered:
                    # Not a JSON-RPC body we gate: forward the rest verbatim.
                    upstream.sendall(head + buffer)
                    self._pipe(client, upstream)
                    return
                match = re.search(rb"\r\ncontent-length:\s*(\d+)", lowered)
                length = int(match.group(1)) if match else 0
                while len(buffer) < length:
                    data = client.recv(65536)
                    if not data:
                        raise EOFError
                    buffer += data
                body, buffer = buffer[:length], buffer[length:]
                name = self._match(head, body)
                if name:
                    rule = self.rules[name]
                    self.log({"gate": name, "held": rule["held_requests"][-1]})
                    with self.lock:
                        self.held += [client, upstream]
                    rule["event"].set()
                    return  # never forwarded; the owner kills the owned processes
                upstream.sendall(head + body)
        except (EOFError, OSError):
            pass
        for sock in (client, upstream):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass



ledger_path = config / "work-parallel.json"
final_path = config / "work-parallel-finalization.json"
coord_path = config / "work-parallel-coordination.json"
native_path = config / "work-parallel-native.json"

def ledger() -> dict: return read_json(ledger_path)
def finalization() -> dict: return read_json(final_path)
def coordination() -> dict: return read_json(coord_path)
def native() -> dict: return read_json(native_path)


def wait_until(predicate, timeout: float, description: str, interval: float = .25):
    end, last = time.time() + timeout, None
    while time.time() < end:
        try:
            value = predicate()
            if value:
                return value
            last = value
        except Exception as error:
            last = repr(error)
        time.sleep(interval)
    raise TimeoutError(description + "; last=" + repr(last)[:500])


def admin_token() -> str:
    path = config / "router-external.token"
    return path.read_text(encoding="ascii").strip() if path.exists() else ""


NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_get(path: str) -> dict:
    headers = {"Accept": "application/json"}
    if admin_token():
        headers["Authorization"] = "Bearer " + admin_token()
    request = urllib.request.Request(f"http://{ROUTER_BIND}:{port}{path}", headers=headers)
    with NO_PROXY.open(request, timeout=30) as response:
        return json.loads(response.read())


wake_observations: dict[str, list] = {}


def observe_wakes() -> dict:
    """Record every distinct durable wake state per review (append vs prompt evidence)."""
    rows = finalization().get("reviews") or {}
    for key, row in rows.items():
        wake = row.get("wake") or {}
        mark = [wake.get("state"), wake.get("attempts"), row.get("decision")]
        seen = wake_observations.setdefault(key, [])
        if not seen or seen[-1][:3] != mark:
            seen.append(mark + [time.time()])
    return rows


def browser_shot(label: str, mobile: bool, expected: str | None = None, expand_ended: bool = False) -> dict:
    from playwright.sync_api import sync_playwright
    chrome = Path.home() / "AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe"
    target = OUT / (label + ("-mobile" if mobile else "-desktop") + ".png")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(chrome))
        page = browser.new_page(viewport={"width": 390 if mobile else 1440, "height": 844 if mobile else 1000},
                                extra_http_headers={"Authorization": "Bearer " + admin_token()} if admin_token() else {})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://{ROUTER_BIND}:{port}/ca/work/ui", wait_until="domcontentloaded")
        page.get_by_role("tab", name="Parallel").click()
        page.locator("#panel-parallel").wait_for(timeout=20000)
        if expected:
            page.locator("#panel-parallel").get_by_text(expected, exact=False).first.wait_for(timeout=30000)
        if expand_ended:
            page.locator("#panel-parallel details.ended-forks > summary").first.click()
            page.wait_for_timeout(500)
        text = page.locator("#panel-parallel").inner_text()
        page.screenshot(path=str(target), full_page=True)
        browser.close()
    return {"ok": not errors, "page_errors": errors, "text": text[:4000], "path": str(target),
            "viewport": [390 if mobile else 1440, 844 if mobile else 1000]}


def both_browser(label: str, expected: str | None = None, expand_ended: bool = False) -> dict:
    return {"desktop": browser_shot(label, False, expected, expand_ended),
            "mobile": browser_shot(label, True, expected, expand_ended)}


def project_dir() -> Path:
    return claude_config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workspace))


def transcript_rows() -> list[dict]:
    path = project_dir() / (SESSION + ".jsonl")
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def main_tool_inputs() -> list[dict]:
    found = []
    for row in transcript_rows():
        if row.get("isSidechain"):
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                found.append({"name": block.get("name"), "input": block.get("input"),
                              "timestamp": row.get("timestamp")})
    return found


def fork_rows() -> list[dict]:
    rows = []
    folder = project_dir() / SESSION / "subagents"
    for meta in folder.glob("*.meta.json") if folder.exists() else []:
        try:
            info = json.loads(meta.read_text(encoding="utf-8"))
        except ValueError:
            continue
        body = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        rows.append({"agent_id": body.stem.removeprefix("agent-"), "description": info.get("description"),
                     "name": info.get("name"), "type": info.get("agentType"), "finished": info.get("finished"),
                     "file": str(body)})
    return rows


def board_status(title: str) -> str | None:
    item = items[title]
    row = next((node for node in board_store.read()["nodes"] if node["id"] == item), None)
    return row.get("status") if row else None


def durable_snapshot(label: str) -> dict:
    value = {"at": time.time(), "board": board_store.read(), "ledger": ledger(),
             "finalization": finalization(), "coordination": coordination(), "native": native()}
    (OUT / (label + ".json")).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return value


def record_tree(label: str, root: int) -> list[dict]:
    tree = process_tree(root)
    (OUT / (label + ".json")).write_text(json.dumps({"at": time.time(), "root": root, "processes": tree},
                                                    indent=2), encoding="utf-8")
    return tree


def stop_owned(proc: subprocess.Popen, label: str, console: Console | None) -> dict:
    console_checkpoint(console, label + "-terminal")
    before = record_tree(label + "-process-tree-before-kill", proc.pid)
    killed_at = time.time()
    kill_tree(proc.pid)
    time.sleep(2)
    routers = kill_owned_router(port)
    return {"process_names": [row.get("Name") for row in before], "routers": routers,
            "killed_at": killed_at, "proc_exited": proc.poll() is not None}


def show_parallel(console: Console, expected: str, timeout: float = 90) -> str:
    console.text("/work-mode parallel")
    console.key("enter", pause=3)
    return wait_until(lambda: console.screen() if expected in console.screen() else None,
                      timeout, "terminal never showed " + expected, interval=.5)


def kill_owned_router(port: int) -> list[int]:
    # The listener is the owned router on ROUTER_BIND; never this runner (the gate).
    raw = subprocess.run(["powershell", "-NoProfile", "-Command",
        f"(Get-NetTCPConnection -LocalAddress {ROUTER_BIND} -LocalPort {port} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
        capture_output=True, text=True, timeout=30).stdout.split()
    pids = [int(value) for value in raw if value.isdigit() and int(value) != os.getpid()]
    for pid in pids:
        kill_tree(pid)
    return pids


owned_prefixed = sorted(k for k in env if k.upper().startswith(PREFIXES))
allowed_owned = {"CIEL_RUNTIME_HOME_OVERRIDE", "CIEL_RUNTIME_CONFIG_DIR", "CIEL_RUNTIME_LAUNCH_CWD",
                 "CIEL_RUNTIME_ROUTER_PORT", "CIEL_RUNTIME_SKIP_MENU", "CIEL_RUNTIME_SKIP_SELF_UPDATE",
                 "CIEL_RUNTIME_TEST_ISOLATED", "CIEL_RUNTIME_ROUTER_BIND_HOST",
                 "CIEL_RUNTIME_ROUTER_CLIENT_HOST", "CLAUDE_CONFIG_DIR"}
result = {"out": str(OUT), "base": str(BASE), "snapshot": snapshot_proof, "session": SESSION,
          "port": port, "router_bind": ROUTER_BIND, "gate_host": GATE_HOST, "items": items,
          "workspace_state_digest": workspace_state_digest, "provider_config_proof": config_proof,
          "stripped_inherited_names": stripped, "owned_prefix_names_after_strip": owned_prefixed,
          "unexpected_inherited_prefix_names_after_strip": sorted(set(owned_prefixed) - allowed_owned),
          "owned_prefix_values_are_owned": all(
              str(scratch) in env[k] or str(SNAP) in env[k] or env[k] in ("1", str(port), ROUTER_BIND, GATE_HOST)
              for k in owned_prefixed),
          "live_before": live_before, "outcome": "timeout", "checkpoints": {}, "screenshots": {}}
current_proc = None
current_console = None
gate = ResponseGate((GATE_HOST, port), (ROUTER_BIND, port), OUT / "response-gate.jsonl")
record_tree("probe-process-tree-initial", os.getpid())

try:
    # Checkpoint A (deterministic): the first review_wake prepare is withheld, so the
    # completion is durable as a pending review with wake pending/attempts 0.
    wake_gate = gate.arm("wake-prepare", lambda tool, args:
                         tool == "work_parallel_event" and args.get("event") == "review_wake"
                         and args.get("operation") == "prepare")
    current_proc, _ = launch("first", False)
    record_tree("first-process-tree-launch", current_proc.pid)
    current_console = console_for(current_proc)
    if not wait_ready(current_console, True):
        raise RuntimeError("first Claude console did not become ready")
    capture(current_console, "01-started")
    record_tree("first-process-tree-ready", current_proc.pid)

    def two_workers():
        value = api_get("/ca/work/api/v1/parallel?session_id=" + urllib.parse.quote(IDENTITY))
        return value if len(((value.get("forks") or {}).get("confirmed") or [])) >= 2 else None
    initial = wait_until(two_workers, 360, "two worker forks did not fill", interval=1)
    confirmed = ((initial.get("forks") or {}).get("confirmed") or [])
    result["initial_workers"] = confirmed
    durable_snapshot("02-workers-running-state")
    capture(current_console, "02-workers-running")
    result["screenshots"]["workers_web"] = both_browser("02-workers-running")

    if not wake_gate.wait(480):
        raise TimeoutError("first review wake prepare did not reach the deterministic gate")
    hit_a = gate.hit("wake-prepare")
    pending_key = hit_a["lease_id"]

    def a_durable():
        row = (finalization().get("reviews") or {}).get(pending_key) or {}
        wake = row.get("wake") or {}
        return row if row.get("state") == "pending" and row.get("decision") is None \
            and wake.get("state") == "pending" and wake.get("attempts") == 0 else None
    pending = wait_until(a_durable, 30, "completion was not durable as pending/unwoken")
    result["checkpoint_a"] = {"recorded_at": time.time(), "gate_hit": hit_a, "review_lease": pending_key,
                              "item_id": pending["item_id"], "state": pending["state"],
                              "decision": pending["decision"], "wake": pending["wake"]}
    result["screenshots"]["pending_web"] = both_browser("03-awaiting-main-review", "Awaiting main review")
    capture(current_console, "03-completion-before-review-terminal")
    result["checkpoints"]["a_before_kill"] = {"file": "03-completion-before-review.json",
                                              "at": durable_snapshot("03-completion-before-review")["at"]}
    still = (finalization().get("reviews") or {}).get(pending_key) or {}
    result["checkpoint_a"]["wake_at_kill"] = still.get("wake")
    result["checkpoints"]["a_kill"] = stop_owned(current_proc, "03-awaiting-main-review", current_console)
    current_proc = None
    current_console = None
    gate.disarm("wake-prepare")
    result["checkpoint_a"]["held_requests"] = gate.rules["wake-prepare"]["held_requests"]
    gate.release_held()

    current_proc, _ = launch("second", True)
    record_tree("second-process-tree-launch", current_proc.pid)
    current_console = console_for(current_proc)
    if not wait_ready(current_console, False):
        raise RuntimeError("first resume console did not become ready")
    record_tree("second-process-tree-ready", current_proc.pid)

    # Arm checkpoint B before the decision: the first bind of the item's
    # replacement (role resume) lease is withheld. launch_intent and
    # spawn_observed are durable before the mod calls bind.
    def resume_bind(tool, args):
        if tool != "work_parallel_event" or args.get("event") != "bind":
            return False
        lease = (ledger().get("leases") or {}).get(args.get("lease_id")) or {}
        return lease.get("role") == "resume" and lease.get("item_id") == items[FAIL_TITLE]
    bind_gate = gate.arm("rework-bind", resume_bind)

    deadline, decided = time.time() + 600, None
    while time.time() < deadline and decided is None:
        rows = observe_wakes()
        if "pending_terminal" not in result["screenshots"]:
            woken = [k for k, r in rows.items() if r.get("decision") is None
                     and (r.get("wake") or {}).get("state") in ("queued", "appended")]
            if woken:
                capture(current_console, "04-pending-review")
                result["screenshots"]["pending_terminal"] = {"review": woken[0], "wake": rows[woken[0]]["wake"],
                                                             "decision_after_capture": ((finalization().get("reviews") or {}).get(woken[0]) or {}).get("decision")}
        approved = [(k, r) for k, r in rows.items() if r.get("decision") == "approve"]
        reworked = [(k, r) for k, r in rows.items() if r.get("decision") == "rework"
                    and r.get("item_id") == items[FAIL_TITLE]]
        if approved and reworked:
            decided = (approved, reworked)
            break
        time.sleep(.5)
    if decided is None:
        raise TimeoutError("main did not produce approve + rework")
    approved_rows, reworked_rows = decided
    result["first_decisions"] = {"approved": [key for key, _ in approved_rows],
                                 "reworked": [key for key, _ in reworked_rows]}
    durable_snapshot("05-main-decisions-state")
    capture(current_console, "05-main-decisions")
    b_review_key, b_review = reworked_rows[0]
    replacement_id = b_review["replacement_lease_id"]

    if not bind_gate.wait(420):
        raise TimeoutError("replacement bind did not reach the deterministic gate")
    hit_b = gate.hit("rework-bind")

    def b_durable():
        row = (finalization().get("reviews") or {}).get(b_review_key) or {}
        lease = (ledger().get("leases") or {}).get(replacement_id) or {}
        return (row, lease) if (row.get("launch") or {}).get("state") in ("launch_intent", "spawn_observed") \
            and lease and lease.get("host_id") is None else None
    b_row, b_lease = wait_until(b_durable, 30, "rework launch was not durable before bind")
    result["checkpoint_b"] = {"recorded_at": time.time(), "gate_hit": hit_b, "review_lease": b_review_key,
                              "replacement_lease": replacement_id, "review_state": b_row["state"],
                              "launch_state": b_row["launch"]["state"], "launch_attempt": b_row["launch"]["attempt"],
                              "launch_updated_at": b_row["launch"]["updated_at"],
                              "dispatch_name": b_row["launch"]["dispatch_name"],
                              "observed_agent_id": b_row["launch"]["agent_id"],
                              "ledger_state": b_lease.get("state"), "ledger_host_id": b_lease.get("host_id")}
    result["screenshots"]["decision_web"] = both_browser("05-main-decisions", "Rework")
    capture(current_console, "05-rework-before-bind-terminal")
    result["checkpoints"]["b_before_kill"] = {"file": "05-rework-before-bind.json",
                                              "at": durable_snapshot("05-rework-before-bind")["at"]}
    result["checkpoints"]["b_kill"] = stop_owned(current_proc, "05-rework-before-bind", current_console)
    current_proc = None
    current_console = None
    gate.disarm("rework-bind")
    result["checkpoint_b"]["held_requests"] = gate.rules["rework-bind"]["held_requests"]
    gate.release_held()
    after_b_kill = {"launch": ((finalization().get("reviews") or {}).get(b_review_key) or {}).get("launch"),
                    "ledger_host_id": ((ledger().get("leases") or {}).get(replacement_id) or {}).get("host_id")}
    result["checkpoint_b"]["after_kill"] = after_b_kill

    current_proc, _ = launch("third", True)
    record_tree("third-process-tree-launch", current_proc.pid)
    current_console = console_for(current_proc)
    if not wait_ready(current_console, False):
        raise RuntimeError("second resume console did not become ready")
    record_tree("third-process-tree-ready", current_proc.pid)

    bound = wait_until(lambda: (ledger().get("leases") or {}).get(replacement_id)
                       if ((ledger().get("leases") or {}).get(replacement_id) or {}).get("host_id") else None,
                       420, "replacement fork never bound after restart", interval=.5)
    result["replacement_bound"] = {"host_id": bound.get("host_id"), "state": bound.get("state"),
                                   "agent_name": bound.get("agent_name"), "dispatch_name": bound.get("dispatch_name")}
    durable_snapshot("06-rework-running-state")
    wait_until(lambda: "work-rework-" in current_console.screen(), 60,
               "terminal agent list never showed the rework fork", interval=.5)
    capture(current_console, "06-rework-running")
    result["screenshots"]["rework_web"] = both_browser("06-rework-running", "Rework")

    def all_done():
        reviews = observe_wakes()
        fail_rows = [(k, r) for k, r in reviews.items() if r.get("item_id") == items[FAIL_TITLE]]
        fail_final = [(k, r) for k, r in fail_rows if r.get("decision") == "approve" and r.get("state") == "finalized"]
        pass_final = [(k, r) for k, r in reviews.items() if r.get("item_id") == items[PASS_TITLE]
                      and r.get("decision") == "approve" and r.get("state") == "finalized"]
        return (fail_rows, fail_final, pass_final) if fail_final and pass_final \
            and board_status(PASS_TITLE) == "done" and board_status(FAIL_TITLE) == "done" else None
    fail_rows, fail_final, pass_final = wait_until(all_done, 720, "both items did not reach approved finalized/done", interval=1)
    final_state = durable_snapshot("07-final-state")
    # Every model decision is already durable; only now is a local status
    # command typed, so it cannot steer any review.
    wait_until(lambda: "? for shortcuts" in current_console.screen() and "esc to interrupt" not in current_console.screen(),
               240, "main did not become idle after finalization", interval=1)
    capture(current_console, "07-before-status")
    show_parallel(current_console, "DEVICE: slots")
    capture(current_console, "07-finalized")
    result["screenshots"]["final_web"] = both_browser("07-finalized", "Ended", expand_ended=True)

    tools = main_tool_inputs()
    tool_text = json.dumps(tools, ensure_ascii=False)
    all_leases = list((final_state["ledger"].get("leases") or {}).values())
    all_runs = list((final_state["coordination"].get("runs") or {}).values())
    archive_runs = [entry.get("run") or {} for entry in (final_state["coordination"].get("archive") or [])]
    native_records = list((final_state["native"].get("records") or {}).values())
    fork_meta = fork_rows()
    reviewers = [row for row in all_leases + all_runs + archive_runs if row.get("role") == "reviewer"]
    resume_leases = [row for row in all_leases if row.get("role") == "resume" and row.get("item_id") == items[FAIL_TITLE]]
    rework_forks = [row for row in fork_meta if str(row.get("name") or "").startswith("work-rework-")]
    rework_row = next(row for _, row in fail_rows if row.get("decision") == "rework")
    second_review = next(row for _, row in fail_rows if row.get("decision") == "approve")
    wake_attempts = {key: (row.get("wake") or {}).get("attempts")
                     for key, row in (final_state["finalization"].get("reviews") or {}).items()}
    review_calls = [row for row in tools if "work_parallel_item_review" in str(row.get("name"))]
    resume_native = [row for row in native_records if row.get("lease_id") == replacement_id]
    result["final"] = {"board_status": {PASS_TITLE: board_status(PASS_TITLE), FAIL_TITLE: board_status(FAIL_TITLE)},
                       "reviewers": reviewers, "resume_lease_ids": [r["id"] for r in resume_leases],
                       "rework_forks": rework_forks, "wake_attempts": wake_attempts,
                       "review_states": {key: {"item": row.get("item_id"), "state": row.get("state"),
                                               "decision": row.get("decision"), "wake": row.get("wake"),
                                               "launch": {k: (row.get("launch") or {}).get(k) for k in
                                                          ("state", "attempt", "dispatch_name", "agent_id", "audit")}}
                                         for key, row in (final_state["finalization"].get("reviews") or {}).items()},
                       "review_calls": review_calls, "resume_native_records": resume_native,
                       "forks": fork_meta}
    result["acceptance"] = {
        "snapshot_29_hashes_verified": not snapshot_proof["source_mismatch"] and not snapshot_proof["snapshot_mismatch"],
        "minimal_provider_config_only": config_proof["providers"] == ["cielairouter"],
        "isolated_claude_config": (project_dir() / (SESSION + ".jsonl")).exists()
                                  and not (Path.home() / ".claude/projects" / project_dir().name).exists(),
        "inherited_sensitive_env_stripped": not result["unexpected_inherited_prefix_names_after_strip"]
                                            and result["owned_prefix_values_are_owned"],
        "workers_filled_policy": len({row.get("agent_id") for row in confirmed if row.get("agent_id")}) == 2,
        "no_reviewer_forks": not reviewers and all(row.get("type") == "fork"
                                                   and "review" not in str(row.get("name") or "").lower()
                                                   for row in fork_meta),
        "checkpoint_a_completion_before_review": result["checkpoint_a"]["wake_at_kill"].get("attempts") == 0
                                                 and result["checkpoints"]["a_kill"]["proc_exited"],
        "exactly_one_first_review_wake": wake_attempts.get(pending_key) == 1,
        "main_read_tests_and_decided": "test_pass.py" in tool_text and "test_fail.py" in tool_text
                                       and len(review_calls) >= 3,
        "pass_item_approved_finalized_done": bool(pass_final) and board_status(PASS_TITLE) == "done",
        "failed_test_reworked": rework_row.get("decision") == "rework" and rework_row.get("state") == "rework_ready",
        "checkpoint_b_intent_or_observed_before_bind": result["checkpoints"]["b_kill"]["proc_exited"]
                                                       and result["checkpoint_b"]["ledger_host_id"] is None
                                                       and after_b_kill["ledger_host_id"] is None,
        "one_rework_resume_lease": len(resume_leases) == 1,
        "one_rework_native_fork": len(rework_forks) == 1 and len({r.get("agent_id") for r in resume_native}) <= 1,
        "replacement_second_review_approved": second_review.get("state") == "finalized",
        "failed_item_finalized_done": board_status(FAIL_TITLE) == "done",
        "each_review_woken_once_by_append_or_prompt": all(
            wake_attempts.get(key) == 1 and any(mark[0] in ("queued", "appended") for mark in wake_observations.get(key, []))
            for key in wake_attempts),
        "terminal_captures": all((OUT / name).exists() for name in
                                 ("04-pending-review.png", "05-main-decisions.png",
                                  "06-rework-running.png", "07-finalized.png")),
        "web_1440_and_390_captures": all((OUT / (name + suffix)).exists() for name in
                                         ("03-awaiting-main-review", "05-main-decisions", "06-rework-running",
                                          "07-finalized") for suffix in ("-desktop.png", "-mobile.png")),
    }
    result["wake_observations"] = wake_observations
    result["outcome"] = "passed" if all(result["acceptance"].values()) else "failed"
except Exception as error:
    result["outcome"] = "error"
    result["error"] = repr(error)
    result["error_type"] = type(error).__name__
    try:
        if current_console is not None:
            capture(current_console, "99-error")
        durable_snapshot("99-error-state")
    except Exception as capture_error:
        result["error_capture"] = repr(capture_error)
finally:
    if current_proc is not None and current_proc.poll() is None:
        kill_tree(current_proc.pid)
    gate.release_held()
    try:
        gate.server.close()
    except OSError:
        pass
    time.sleep(2)
    result["final_owned_command_pids"] = kill_owned_commands([str(BASE), str(scratch), str(SNAP)])
    result["final_owned_router_pids"] = kill_owned_router(port)
    result["live_after"] = live_rows()
    before_by_id = {row.get("ProcessId"): row for row in live_before}
    after_by_id = {row.get("ProcessId"): row for row in result["live_after"]}
    result["protected_pids_unchanged"] = all(before_by_id.get(pid) == after_by_id.get(pid) for pid in LIVE_PIDS)
    if result.get("acceptance") is not None:
        result["acceptance"]["protected_pids_unchanged"] = result["protected_pids_unchanged"]
        result["outcome"] = "passed" if all(result["acceptance"].values()) else "failed"
    result["preserved_config"] = str(config)
    result["preserved_workspace"] = str(workspace)
    result["preserved_claude_config"] = str(claude_config)
    result["wake_observations"] = wake_observations
    try:
        record_tree("probe-process-tree-final", os.getpid())
    except Exception:
        pass
    (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "outcome": result["outcome"], "error": result.get("error"),
                      "acceptance": result.get("acceptance"),
                      "protected_pids_unchanged": result["protected_pids_unchanged"]}, indent=2, ensure_ascii=False))

if result["outcome"] != "passed":
    raise SystemExit(1)
