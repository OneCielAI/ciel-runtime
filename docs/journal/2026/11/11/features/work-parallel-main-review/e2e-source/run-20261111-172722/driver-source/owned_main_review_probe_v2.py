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
TOOLS = SOURCE / "docs/journal/2026/11/03/features/work-parallel/evidence/menu-ui"
# Current integrated tree named by the team lead (SSE freeze 378f6d73 + restart freeze).
KEY_HASHES = {
    "ciel_runtime_support/claude_mods/ciel-work/hooks/register.js":
        "0d1b4afcff4c4d44436209d750411f36825a22486e519e7f4de89242a5d968c8",
    "ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js":
        "194fa123ca8e88ed7ddf0f12d6b1d7e5424efa827415fbaa8f1f0da7ffb721a4",
    "ciel_runtime_support/work_parallel_mcp.py":
        "3521486981a1d99891094c56bf63e36cf4a958d5504cc2cef3c7a146ac17f717",
}
# Live Ciel launchers and their cmd parents. Every current descendant of the
# launchers (python routers, claude.exe, node.exe/MCP children) is protected too.
LIVE_ROOTS = (23728, 39984, 21912)
LIVE_PARENTS = (9008, 44500, 20744)
CORE_PIDS = (23728, 39984, 21912, 42764, 42924, 36344, 40616, 45652, 45428,
             46232, 46508, 2272, 9764, 12240, 48104, 9008, 44500, 20744)
PREFIXES = ("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP")
NO_WINDOW = 0x08000000
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
# Exact executed sources, preserved in the run folder before anything launches.
DRIVER_DIR = OUT / "driver-source"
DRIVER_DIR.mkdir()
DRIVER_SOURCES = {"owned_main_review_probe_v2.py": Path(__file__).resolve(),
                  "console_driver.py": BASE / "console_driver.py",
                  "shot_hwnd.ps1": BASE / "shot_hwnd.ps1"}
DRIVER_HASHES = {}
for _name, _path in DRIVER_SOURCES.items():
    shutil.copy2(_path, DRIVER_DIR / _name)
    DRIVER_HASHES[_name] = hashlib.sha256((DRIVER_DIR / _name).read_bytes()).hexdigest()
    if DRIVER_HASHES[_name] != hashlib.sha256(_path.read_bytes()).hexdigest():
        raise RuntimeError("driver source copy mismatch: " + _name)
(DRIVER_DIR / "SHA256SUMS").write_text("".join(f"{v} *{k}\n" for k, v in DRIVER_HASHES.items()), encoding="utf-8")
sys.path.insert(0, str(BASE))
from console_driver import Console, kernel32  # noqa: E402

diagnostics: list[dict] = []


def diag(row: dict) -> None:
    row = {"at": time.time(), **row}
    diagnostics.append(row)
    with (OUT / "diagnostics.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


SNAP_DIRS = ("ciel_runtime_support", "scripts", "tests", "npm-bin")
IGNORED = ("__pycache__", ".pytest_cache", ".ruff_cache")


ENGINE_TYPES = "ciel_runtime_support/claude_mods/ciel-work/.claude-plugin/types/"


def engine_generated(rel: str) -> bool:
    """Claude Code writes these git-ignored plugin types at load (debug: wrote .claude-plugin/types/...)."""
    return rel.startswith(ENGINE_TYPES) and rel not in (ENGINE_TYPES + ".gitignore", ENGINE_TYPES + "tsconfig.json")


def tree_manifest(root: Path) -> dict[str, str]:
    rows = {}
    for name in SNAP_DIRS:
        for path in sorted((root / name).rglob("*")):
            if path.is_file() and not any(part in IGNORED for part in path.parts) and path.suffix != ".pyc":
                rows[path.relative_to(root).as_posix()] = sha(path)
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix != ".pyc":
            rows[path.name] = sha(path)
    return rows


def build_snapshot() -> dict:
    """Per-run byte copy of the current worktree, verified file by file."""
    if SNAP.exists():
        raise RuntimeError("snapshot already exists")
    source_before = tree_manifest(SOURCE)
    key_source = {rel: source_before.get(rel, "") for rel in KEY_HASHES}
    if key_source != KEY_HASHES:
        raise RuntimeError(f"current worktree key hashes differ: {key_source}")
    SNAP.mkdir()
    ignored = shutil.ignore_patterns(*IGNORED, "*.pyc")
    for name in SNAP_DIRS:
        shutil.copytree(SOURCE / name, SNAP / name, ignore=ignored)
    for path in SOURCE.iterdir():
        if path.is_file() and path.suffix != ".pyc":
            shutil.copy2(path, SNAP / path.name)
    snap = tree_manifest(SNAP)
    source_after = tree_manifest(SOURCE)
    if source_after != source_before:
        changed = sorted(k for k in set(source_before) | set(source_after)
                         if source_before.get(k) != source_after.get(k))
        raise RuntimeError(f"worktree changed while snapshotting: {changed[:20]}")
    mismatch = sorted(k for k in set(snap) | set(source_before) if snap.get(k) != source_before.get(k))
    if mismatch:
        raise RuntimeError(f"snapshot differs from worktree: {mismatch[:20]}")
    (OUT / "snapshot-manifest.json").write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")
    for path in SNAP.rglob("*"):
        if path.is_file():
            try:
                path.chmod(stat.S_IREAD)
            except OSError:
                pass
    digest = hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()
    return {"files": len(snap), "manifest_sha256": digest, "key_hashes": {k: snap[k] for k in KEY_HASHES},
            "snapshot": str(SNAP), "mismatch": mismatch}


def clean_environment() -> tuple[dict[str, str], list[str]]:
    inherited = sorted(k for k in os.environ if k.upper().startswith(PREFIXES))
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(PREFIXES)}
    return env, inherited


class MeasurementError(RuntimeError):
    pass


def ps(script: str, label: str, timeout: float = 45) -> str:
    """PowerShell with its own hidden console; every failing call's rc/stderr is kept."""
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL)
    if done.returncode != 0 or done.stderr.strip():
        diag({"ps": label, "rc": done.returncode, "stderr": done.stderr.strip()[:600]})
    if done.returncode != 0:
        raise MeasurementError(f"{label}: powershell rc={done.returncode}")
    return done.stdout.strip()


def ps_rows(script: str, label: str) -> list[dict]:
    raw = ps(script + " | ConvertTo-Json -Depth 3", label)
    value = json.loads(raw) if raw else []
    return value if isinstance(value, list) else [value]


ROW = ("Select-Object ProcessId,ParentProcessId,Name,"
       "@{n='CreationDateUtc';e={$_.CreationDate.ToUniversalTime().ToString('o')}},CommandLine")


def identity(row: dict) -> tuple[int, str]:
    return int(row["ProcessId"]), str(row["CreationDateUtc"])


def all_process_rows() -> list[dict]:
    return ps_rows("Get-CimInstance Win32_Process | " + ROW, "all_process_rows")


def strict_tree(rows: list[dict], roots: list[tuple[int, str]]) -> list[dict]:
    """Descendants whose creation is strictly later than their accepted parent.

    PID-only parentage is unsafe after reuse. A child created before the process
    currently holding its recorded ParentProcessId is not that process's child.
    """
    by_pid = {int(row["ProcessId"]): row for row in rows}
    accepted: dict[int, dict] = {}
    for pid, created in roots:
        row = by_pid.get(pid)
        if row is not None and identity(row) == (pid, created):
            accepted[pid] = row
    while True:
        added = 0
        for row in rows:
            pid, parent = int(row["ProcessId"]), int(row["ParentProcessId"])
            if pid in accepted or parent not in accepted:
                continue
            if str(row["CreationDateUtc"]) > str(accepted[parent]["CreationDateUtc"]):
                accepted[pid] = row
                added += 1
        if not added:
            break
    return list(accepted.values())


def current_identity(pid: int) -> tuple[int, str] | None:
    rows = ps_rows(f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | " + ROW,
                   "current_identity")
    return identity(rows[0]) if rows else None


owned_roots: dict[int, tuple[int, str]] = {}


def register_owned_root(pid: int, label: str) -> tuple[int, str]:
    current = current_identity(pid)
    if current is None:
        raise MeasurementError(f"{label}: owned root {pid} vanished before registration")
    if pid in PROTECTED_PIDS:
        raise RuntimeError(f"{label}: root PID {pid} appears in protected snapshot")
    owned_roots[pid] = current
    diag({"owned_root_registered": label, "identity": current})
    return current


def process_tree(root_pid: int) -> list[dict]:
    root = owned_roots.get(root_pid) or current_identity(root_pid)
    return [] if root is None else strict_tree(all_process_rows(), [root])


def protected_rows() -> list[dict]:
    rows = all_process_rows()
    roots = []
    for pid in LIVE_ROOTS + LIVE_PARENTS:
        row = next((item for item in rows if item.get("ProcessId") == pid), None)
        if row is not None:
            roots.append(identity(row))
    found = strict_tree(rows, roots)
    if not {r.get("ProcessId") for r in found} >= set(LIVE_ROOTS):
        raise MeasurementError("protected launcher roots missing from process query")
    return found


def self_check(label: str) -> None:
    current = current_identity(os.getpid())
    if current is None:
        raise MeasurementError(label + ": process query did not return this runner")


kill_log: list[dict] = []


def write_kill(row: dict) -> None:
    kill_log.append(row)
    with (OUT / "kills.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


import ctypes as _ct
from ctypes import wintypes as _wt

_k32 = _ct.WinDLL("kernel32", use_last_error=True)
_ntdll = _ct.WinDLL("ntdll")
_k32.OpenProcess.restype = _wt.HANDLE
_k32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
_k32.GetProcessTimes.argtypes = [_wt.HANDLE] + [_ct.POINTER(_wt.FILETIME)] * 4
_k32.WaitForSingleObject.argtypes = [_wt.HANDLE, _wt.DWORD]
_k32.CloseHandle.argtypes = [_wt.HANDLE]
_ntdll.NtSuspendProcess.argtypes = [_wt.HANDLE]
_ACCESS = 0x0800 | 0x1000 | 0x00100000  # SUSPEND_RESUME | QUERY_LIMITED_INFORMATION | SYNCHRONIZE


def _filetime_iso(ft: _wt.FILETIME) -> str:
    ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    seconds, fraction = divmod(ticks, 10_000_000)
    stamp = time.gmtime(seconds - 11644473600)
    # Win32_Process.CreationDate is microsecond precision (7th digit always 0;
    # measured: handle .9444429 vs CIM .9444420), so compare at that precision.
    return time.strftime("%Y-%m-%dT%H:%M:%S", stamp) + ".%06d0Z" % (fraction // 10)


class Pinned:
    """An open handle to one exact process identity. While it is open, Windows
    cannot reuse the PID, so taskkill /PID n reaches this same process object."""

    def __init__(self, pid: int, created: str):
        self.pid, self.created, self.handle = pid, created, None
        handle = _k32.OpenProcess(_ACCESS, False, pid)
        if not handle:
            return
        times = [_wt.FILETIME() for _ in range(4)]
        if not _k32.GetProcessTimes(handle, *[_ct.byref(t) for t in times]) or _filetime_iso(times[0]) != created:
            _k32.CloseHandle(handle)
            return
        self.handle = handle

    def alive(self) -> bool:
        return bool(self.handle) and _k32.WaitForSingleObject(self.handle, 0) == 0x102  # WAIT_TIMEOUT

    def suspend(self) -> bool:
        return bool(self.handle) and _ntdll.NtSuspendProcess(self.handle) == 0

    def wait_dead(self, ms: int) -> bool:
        return bool(self.handle) and _k32.WaitForSingleObject(self.handle, ms) == 0

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def handle_identity_check() -> dict:
    """Preflight: the handle creation time must equal CIM's string for this runner."""
    cim = current_identity(os.getpid())
    pinned = Pinned(os.getpid(), cim[1]) if cim else None
    ok = bool(pinned and pinned.handle)
    if pinned:
        pinned.close()
    if not ok:
        raise MeasurementError("handle creation time does not match CIM creation time")
    return {"pid": os.getpid(), "cim_creation": cim[1], "handle_matches": ok}


def explicit_kill(pinned: Pinned, row: dict, label: str, round_number: int, depth: int) -> dict:
    """Kill one pinned identity by explicit PID (no /T) and log everything."""
    record = {"at": time.time(), "label": label, "round": round_number, "depth": depth,
              "target": {"pid": pinned.pid, "creation": pinned.created, "parent": row.get("ParentProcessId"),
                         "name": row.get("Name"), "command_line": row.get("CommandLine")}}
    if pinned.pid in PROTECTED_PIDS or (pinned.pid, pinned.created) in PROTECTED_IDENTITIES:
        record.update(refused=True, reason="PID appears in protected snapshot")
        write_kill(record)
        raise RuntimeError(f"refused protected PID {pinned.pid}")
    if not pinned.alive():
        record.update(skipped="exited before kill")
        write_kill(record)
        return record
    command = ["taskkill", "/PID", str(pinned.pid), "/F"]
    done = subprocess.run(command, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL)
    record.update(command=command, rc=done.returncode, stdout=done.stdout, stderr=done.stderr,
                  dead=pinned.wait_dead(5000))
    write_kill(record)
    return record


def kill_tree(pid: int, label: str) -> dict:
    """Freeze, then kill a creation-fenced owned tree leaves-first by exact identity."""
    root = owned_roots.get(pid)
    if root is None:
        raise RuntimeError(f"{label}: owned root {pid} was never registered")
    pinned: dict[tuple[int, str], Pinned] = {}
    rows_by_id: dict[tuple[int, str], dict] = {}
    suspended, refused_pins = [], []
    try:
        # Phase 1: pin and suspend until a fresh enumeration finds nothing new.
        # Known identities stay roots, so a child whose parent exited is still found.
        for sweep in range(60):
            rows = all_process_rows()
            roots = [key for key in list(pinned) if pinned[key].alive()] or [root]
            found = strict_tree(rows, roots)
            new = [row for row in found if identity(row) not in pinned]
            if not new and sweep:
                break
            for row in sorted(new, key=lambda r: str(r["CreationDateUtc"])):
                key = identity(row)
                if key[0] in PROTECTED_PIDS or key in PROTECTED_IDENTITIES:
                    refused_pins.append(key)
                    diag({"kill_refused_protected": label, "identity": key})
                    continue
                pin = Pinned(*key)
                if not pin.handle:
                    continue  # exited, or the PID now names a different process
                pinned[key], rows_by_id[key] = pin, row
                if pin.suspend():
                    suspended.append(key)
        initial = sorted(pinned)
        if refused_pins:
            raise RuntimeError(f"{label}: protected identity inside owned tree: {refused_pins}")
        # Phase 2: frozen tree, leaves first.
        killed, rounds = [], 0
        while rounds < 2000:
            live = {key: pin for key, pin in pinned.items() if pin.alive()}
            if not live:
                break
            rounds += 1
            pids = {key[0] for key in live}
            parents = {int(rows_by_id[key]["ParentProcessId"]) for key in live}
            leaves = [key for key in live if key[0] not in parents & pids] or list(live)

            def depth(key):
                count, parent = 0, int(rows_by_id[key]["ParentProcessId"])
                by_pid = {k[0]: k for k in live}
                while parent in by_pid and count < 64:
                    count += 1
                    parent = int(rows_by_id[by_pid[parent]]["ParentProcessId"])
                return count
            for key in sorted(leaves, key=depth, reverse=True):
                record = explicit_kill(live[key], rows_by_id[key], label, rounds, depth(key))
                if record.get("dead"):
                    killed.append(key)
        # Phase 3: any descendant created during phase 1/2 escapes only if its
        # ancestor was not yet suspended; one more fenced sweep proves none remain.
        rows = all_process_rows()
        live_ids = {identity(row) for row in rows}
        late = [identity(row) for row in strict_tree(rows, [k for k in pinned if k in live_ids] or [root])
                if identity(row) not in pinned]
        remaining = sorted(key for key in pinned if key in live_ids) + late
        summary = {"at": time.time(), "label": label, "root": root, "initial": initial, "suspended": len(suspended),
                   "killed": killed, "rounds": rounds, "remaining": remaining}
        with (OUT / "kill-summaries.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary) + "\n")
        if remaining:
            raise RuntimeError(f"{label}: owned processes remain: {remaining}")
        return summary
    finally:
        for pin in pinned.values():
            pin.close()


def register_discovered_owned(row: dict, label: str, markers: list[str]) -> None:
    command = str(row.get("CommandLine") or "")
    if not any(marker in command for marker in markers):
        raise RuntimeError(f"{label}: process command line has no owned marker")
    pid = int(row["ProcessId"])
    if pid in PROTECTED_PIDS:
        raise RuntimeError(f"{label}: discovered PID {pid} appears in protected snapshot")
    owned_roots[pid] = identity(row)
    diag({"owned_root_discovered": label, "identity": identity(row)})


def kill_owned_commands(markers: list[str]) -> list[int]:
    quoted = ",".join("'" + value.replace("'", "''") + "'" for value in markers)
    rows = ps_rows("$m=@(" + quoted + "); Get-CimInstance Win32_Process | Where-Object { $c=$_.CommandLine; "
                   "$c -and $_.ProcessId -ne $PID -and ($m | Where-Object {$c.Contains($_)}) } | " + ROW,
                   "owned_commands")
    pids = []
    # Roots first; a later root may disappear when an earlier ancestor is killed.
    row_pids = {int(row["ProcessId"]) for row in rows}
    roots = [row for row in rows if int(row["ParentProcessId"]) not in row_pids]
    for row in roots:
        pid = int(row["ProcessId"])
        try:
            register_discovered_owned(row, "final-owned-command", markers)
            kill_tree(pid, "final-owned-command")
            pids.append(pid)
        except RuntimeError as error:
            diag({"final_kill_skipped": pid, "error": str(error)})
    return pids


LIVE_PROFILE = [Path.home() / ".claude/settings.json",
                *sorted((Path.home() / ".claude/commands").glob("*")),
                *sorted((Path(os.environ["LOCALAPPDATA"]) / "ciel-runtime/bin").glob("*"))]


def live_profile() -> dict:
    hashes = {str(p).replace(str(Path.home()), "~"): sha(p) for p in LIVE_PROFILE if p.is_file()}
    try:
        projects = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8")).get("projects") or {}
    except (OSError, ValueError):
        projects = None
    owned = sorted(k for k in (projects or {}) if "ciel-owned-main-review" in k)
    return {"hashes": hashes, "claude_json_owned_project_entries": owned,
            "claude_json_projects": None if projects is None else len(projects)}


snapshot_proof = build_snapshot()
env, stripped = clean_environment()
PROTECTED_PIDS: set[int] = set(CORE_PIDS)
self_check("preflight")
live_before = protected_rows()
PROTECTED_PIDS.update(int(row["ProcessId"]) for row in live_before)
PROTECTED_IDENTITIES = {identity(row) for row in live_before}
core_missing = sorted(set(CORE_PIDS) - {r.get("ProcessId") for r in live_before})
if core_missing:
    raise RuntimeError(f"protected core PIDs absent before the probe: {core_missing}")
profile_before = live_profile()
if profile_before["claude_json_owned_project_entries"]:
    raise RuntimeError("live ~/.claude.json already has an owned project entry")
(OUT / "live-before.json").write_text(json.dumps({"protected": live_before, "profile": profile_before},
                                                 indent=1), encoding="utf-8")


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

markers = scratch / "markers"
markers.mkdir()


def marker_step(slug: str, seconds: int) -> str:
    path = (markers / (slug + ".txt")).as_posix()
    return ("First run exactly one foreground Bash command and wait for it to exit: python -c \"import time; p=r'"
            + path + "'; [(open(p,'a').write(str(time.time())+chr(10)),time.sleep(2)) for _ in range("
            + str(seconds // 2) + ")]\". ")


def report_step(solution: str, test: str, summary: str) -> str:
    return ("If it passes, compute SHA256 of " + solution + " and immediately call work_parallel_report "
            "event=completion with the exact lease_id and worker_token, artifact=<that SHA256>, "
            "evidence='provided passing test passed', detail={summary:'" + summary + "',coverage:[{item_id:"
            "<assigned item id>,verified:true}],verification:'python -m unittest -v " + test + ": PASS'}. "
            "Do not call Agent or message main.")


PASS_TITLE = "Passing implementation"
FAIL_TITLE = "Failing implementation then rework"
P2_TITLE = "P2 passing multiply"
P3_TITLE = "P3 passing subtract"
PASS_NOTE = (marker_step("p1-pass", 60) + "Then create pass_solution.py with function add(a,b) returning a+b. "
             "Create test_pass.py using unittest and asserting add(2,3)==5. Run exactly "
             "`python -m unittest -v test_pass.py`. Do not change the test after running it. "
             + report_step("pass_solution.py", "test_pass.py",
                           "pass_solution implements addition and the provided test passes"))
P2_NOTE = (marker_step("p2-pass", 60) + "Then create p2_solution.py with function mul(a,b) returning a*b. "
           "Create test_p2.py using unittest and asserting mul(2,3)==6. Run exactly "
           "`python -m unittest -v test_p2.py`. Do not change the test after running it. "
           + report_step("p2_solution.py", "test_p2.py",
                         "p2_solution implements multiplication and the provided test passes"))
P3_NOTE = (marker_step("p3-pass", 60) + "Then create p3_solution.py with function sub(a,b) returning a-b. "
           "Create test_p3.py using unittest and asserting sub(5,3)==2. Run exactly "
           "`python -m unittest -v test_p3.py`. Do not change the test after running it. "
           + report_step("p3_solution.py", "test_p3.py",
                         "p3_solution implements subtraction and the provided test passes"))
FAIL_NOTE = ("Use fail_solution.py and test_fail.py. If this assignment role is worker / generation 1: "
             + marker_step("p1-fail", 60)
             + "Then create fail_solution.py with value() returning 1; create test_fail.py using unittest and "
             "asserting value()==2; run exactly `python -m unittest -v test_fail.py`; deliberately do not fix the "
             "implementation and do not alter the test. Then compute SHA256 of fail_solution.py and call "
             "work_parallel_report event=completion with the exact lease_id and worker_token, artifact=<that "
             "SHA256>, evidence='provided failing test failed', detail={summary:'the provided test fails because "
             "value returned 1 instead of 2',coverage:[{item_id:<assigned item id>,verified:false}],verification:"
             "'python -m unittest -v test_fail.py: FAIL expected 2 got 1'}. If this assignment role is resume / "
             "generation 2: do not run the timing command; retain test_fail.py unchanged, fix fail_solution.py so "
             "value() returns 2, run the same unittest until it passes, then report completion with the new SHA256, "
             "evidence='provided failing test fixed and passed', summary='rework fixed value and the unchanged "
             "provided test passes', verified:true, verification='python -m unittest -v test_fail.py: PASS'. "
             "Do not call Agent or message main.")
SEED = ((PASS_TITLE, "P1", PASS_NOTE), (FAIL_TITLE, "P1", FAIL_NOTE), (P2_TITLE, "P2", P2_NOTE),
        (P3_TITLE, "P3", P3_NOTE))
TITLES = tuple(title for title, _, _ in SEED)


def seed(doc: dict) -> None:
    for title, priority, note in SEED:
        root = work_tree.set_plan(doc, {"title": title + " root", "priority": priority,
                                        "children": [{"title": title, "note": note}]})
        work_tree.set_phase(doc, work_tree.PHASE_EXECUTING, root)


board_store.transaction(seed)
seeded = board_store.read()
items = {node["title"]: node["id"] for node in seeded["nodes"] if node["title"] in TITLES}
if set(items) != set(TITLES):
    raise RuntimeError("board seed failed")
item_priority = {items[title]: priority for title, priority, _ in SEED}

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
    register_owned_root(proc.pid, "launch-" + name)
    launchers.append((proc, path))
    (OUT / (name + "-owned-root.json")).write_text(json.dumps({"at": time.time(), "conhost_pid": proc.pid,
                                                               "cmd": str(path)}), encoding="utf-8")
    return proc, path


def console_for(proc: subprocess.Popen) -> Console:
    end = time.time() + 60
    while time.time() < end:
        time.sleep(.5)
        rows = ps_rows(f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={proc.pid}' | "
                       "Where-Object {$_.Name -eq 'cmd.exe'} | " + ROW, "console_for")
        if rows:
            return Console(int(rows[0]["ProcessId"]))
        if proc.poll() is not None:
            break
    self_check("console_for timeout")
    raise RuntimeError("owned console did not start")


def detach_console() -> None:
    """Leave an owned console before it is killed; later queries must not inherit it."""
    kernel32.FreeConsole()


def capture(console: Console | None, label: str) -> str:
    if console is None:
        return ""
    text = console.screen()
    (OUT / (label + ".txt")).write_text(text, encoding="utf-8")
    shot = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(BASE / "shot_hwnd.ps1"), str(int(console.hwnd)), str(OUT / (label + ".png"))],
                          capture_output=True, text=True, timeout=45, creationflags=NO_WINDOW,
                          stdin=subprocess.DEVNULL)
    (OUT / (label + "-capture.log")).write_text(shot.stdout + shot.stderr, encoding="utf-8")
    return text


def console_checkpoint(console: Console | None, label: str) -> None:
    if console is not None:
        (OUT / (label + ".txt")).write_text(console.screen(), encoding="utf-8")


def trust_selection(screen: str) -> str | None:
    for line in screen.splitlines():
        stripped = line.strip()
        if stripped.startswith(("❯", ">")):
            body = stripped.lstrip("❯>").strip()
            if body.startswith("Yes"):
                return "yes"
            if body.startswith("No"):
                return "no"
    return None


keypress_log: list[dict] = []


def press(console: Console, key: str, screen_before: str, reason: str, pause: float) -> None:
    keypress_log.append({"at": time.time(), "key": key, "reason": reason,
                         "screen_tail": screen_before.strip().splitlines()[-12:]})
    console.key(key, pause=pause)


def wait_ready(console: Console, trust: bool, timeout: float = 180) -> bool:
    end, trusted = time.time() + timeout, not trust
    trust_moves = 0
    while time.time() < end:
        time.sleep(.5)
        screen = console.screen()
        if not trusted and "trust" in screen.lower() and "Yes" in screen:
            selected = trust_selection(screen)
            if selected != "yes":
                if trust_moves >= 4:
                    capture(console, "00-trust-unresolved")
                    return False
                press(console, "down" if selected == "no" else "up", screen, "move trust selection to Yes", 1)
                trust_moves += 1
                continue
            capture(console, "00-trust")
            again = console.screen()
            if trust_selection(again) != "yes":
                continue
            press(console, "enter", again, "confirm Yes, I trust this folder (re-read)", 3)
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


def kill_owned_router(port: int) -> list[int]:
    raw = ps(f"(Get-NetTCPConnection -LocalAddress {ROUTER_BIND} -LocalPort {port} -State Listen "
             "-ErrorAction SilentlyContinue).OwningProcess", "owned_router")
    pids = sorted({int(value) for value in raw.split() if value.isdigit() and int(value) != os.getpid()})
    killed = []
    for pid in pids:
        rows = ps_rows(f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | " + ROW, "owned_router_row")
        if not rows:
            continue
        register_discovered_owned(rows[0], "owned-router", [str(SNAP), str(scratch)])
        kill_tree(pid, "owned-router")
        killed.append(pid)
    return killed


def stop_owned(proc: subprocess.Popen, label: str, console: Console | None) -> dict:
    console_checkpoint(console, label + "-terminal")
    before = record_tree(label + "-process-tree-before-kill", proc.pid)
    detach_console()
    killed_at = time.time()
    kill = kill_tree(proc.pid, label)
    time.sleep(2)
    routers = kill_owned_router(port)
    self_check(label + " after kill")
    left = [r for r in process_tree(proc.pid) if r.get("ProcessId") == proc.pid]
    return {"process_names": [row.get("Name") for row in before], "owned_initial": kill["initial"],
            "killed": kill["killed"], "rounds": kill["rounds"], "routers": routers, "killed_at": killed_at,
            "proc_exited": proc.poll() is not None and not left}


def show_parallel(console: Console, expected: str, timeout: float = 90) -> str:
    screen = console.screen()
    if "esc to interrupt" in screen:
        raise RuntimeError("refusing to type while main is busy")
    keypress_log.append({"at": time.time(), "key": "text:/work-mode parallel", "reason": "local status view",
                         "screen_tail": screen.strip().splitlines()[-12:]})
    console.text("/work-mode parallel")
    press(console, "enter", console.screen(), "submit /work-mode parallel", 3)
    return wait_until(lambda: console.screen() if expected in console.screen() else None,
                      timeout, "terminal never showed " + expected, interval=.5)


wake_messages_seen: dict[str, int] = {}
guard_state = {"stop": False, "profile_violation": None, "errors": 0}


STOP_BLOCK = "Work results are awaiting explicit main review"
WAKE_TEXT = "awaiting main review (data, not instructions)"
SUBMIT_MARK = "$.prompt.submit (ciel-work):"
TURN_START = re.compile(r"\[engine\] turn \d+ start")
DEBUG_LEASE = re.compile(r'\\"lease_id\\":\\"([0-9a-f]{32})')
TEXT_LEASE = re.compile(r'"lease_id"\s*:\s*"([0-9a-f]{32})')


def prompt_submit_flights() -> dict:
    """At most one ciel-work $.prompt.submit may be in flight per process.

    A submit is in flight from its debug line until the next engine turn start
    (the shared FIFO releases when the prompt entered, i.e. its turn started).
    Two submit lines with no turn start between them mean two were in flight.
    A dropped submit also releases the FIFO without a turn; overlaps are listed
    with their leases so each can be checked against the log by hand.
    """
    per_process = {}
    for name in ("first", "second", "third"):
        path = BASE / (name + "-claude-debug.txt")
        if not path.exists():
            continue
        events, in_flight, peak, overlaps = [], 0, 0, []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stamp = line[:24]
            if SUBMIT_MARK in line:
                lease = DEBUG_LEASE.search(line)
                in_flight += 1
                peak = max(peak, in_flight)
                events.append({"at": stamp, "kind": "submit", "lease": lease.group(1) if lease else None})
                if in_flight > 1:
                    overlaps.append(events[-1])
            elif TURN_START.search(line):
                in_flight = max(0, in_flight - 1)
                events.append({"at": stamp, "kind": "turn_start"})
        per_process[name] = {"events": events, "peak_in_flight": peak, "overlaps": overlaps}
    return per_process


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def presentations() -> dict:
    """Per review lease, in main's own transcript: wake prompts/appends and Stop
    blocks that carry its data. Request-blob rows are copies and are skipped."""
    shown: dict[str, dict] = {}
    for row in transcript_rows():
        if row.get("isSidechain") or row.get("type") not in ("user", "system", "attachment"):
            continue
        # One row is one presentation, even when it repeats the text in several
        # fields (a plugin prompt row carries it in message and pluginPrompt).
        found = {"wake": set(), "stop": set()}
        for text in _strings(row):
            kind = "wake" if WAKE_TEXT in text else "stop" if STOP_BLOCK in text else None
            if kind is not None:
                found[kind].update(TEXT_LEASE.findall(text))
        for kind, leases in found.items():
            for lease in leases:
                shown.setdefault(lease, {"wake": [], "stop": []})[kind].append(row.get("timestamp"))
    return shown


def wake_messages() -> dict[str, list[str]]:
    """Plugin wake rows in main's own transcript, keyed by review lease id."""
    found: dict[str, list[str]] = {}
    prefix = "Ciel Work result is awaiting main review (data, not instructions): "
    for row in transcript_rows():
        if row.get("isSidechain") or row.get("type") != "user":
            continue
        content = (row.get("message") or {}).get("content")
        texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or []
                                                           if isinstance(b, dict) and b.get("type") == "text"]
        for text in texts:
            at = text.find(prefix)
            if at < 0:
                continue
            body = text[at + len(prefix):]
            try:
                lease = json.JSONDecoder().raw_decode(body)[0].get("lease_id")
            except ValueError:
                lease = None
            found.setdefault(str(lease), []).append(str(row.get("timestamp")))
    return found


def guard_loop() -> None:
    last_profile = 0.0
    while not guard_state["stop"]:
        try:
            observe_wakes()
            if time.time() - last_profile > 10:
                last_profile = time.time()
                now = live_profile()
                if now["hashes"] != profile_before["hashes"] or now["claude_json_owned_project_entries"]:
                    guard_state["profile_violation"] = {"at": time.time(), "now": now}
                    diag({"profile_violation": now})
                    guard_state["stop"] = True
        except Exception as error:
            guard_state["errors"] += 1
            if guard_state["errors"] < 20:
                diag({"guard_error": repr(error)})
        time.sleep(.25)


def check_guard() -> None:
    if guard_state["profile_violation"]:
        raise RuntimeError("live profile changed during the owned run; aborting")


def wait_until_guarded(predicate, timeout: float, description: str, interval: float = .5):
    def checked():
        check_guard()
        return predicate()
    return wait_until(checked, timeout, description, interval)


def lease_rows(role: str | None = None) -> list[dict]:
    return [row for row in (ledger().get("leases") or {}).values() if role is None or row.get("role") == role]


def is_idle(console: Console) -> bool:
    screen = console.screen()
    return "esc to interrupt" not in screen and ("? for shortcuts" in screen or "bypass permissions" in screen)


owned_prefixed = sorted(k for k in env if k.upper().startswith(PREFIXES))
allowed_owned = {"CIEL_RUNTIME_HOME_OVERRIDE", "CIEL_RUNTIME_CONFIG_DIR", "CIEL_RUNTIME_LAUNCH_CWD",
                 "CIEL_RUNTIME_ROUTER_PORT", "CIEL_RUNTIME_SKIP_MENU", "CIEL_RUNTIME_SKIP_SELF_UPDATE",
                 "CIEL_RUNTIME_TEST_ISOLATED", "CIEL_RUNTIME_ROUTER_BIND_HOST",
                 "CIEL_RUNTIME_ROUTER_CLIENT_HOST", "CLAUDE_CONFIG_DIR"}
result = {"out": str(OUT), "base": str(BASE), "driver_hashes": DRIVER_HASHES, "snapshot": snapshot_proof, "session": SESSION,
          "port": port, "router_bind": ROUTER_BIND, "gate_host": GATE_HOST, "items": items,
          "item_priority": item_priority, "policy": policy,
          "workspace_state_digest": workspace_state_digest, "provider_config_proof": config_proof,
          "stripped_inherited_names": stripped, "owned_prefix_names_after_strip": owned_prefixed,
          "unexpected_inherited_prefix_names_after_strip": sorted(set(owned_prefixed) - allowed_owned),
          "owned_prefix_values_are_owned": all(
              str(scratch) in env[k] or str(SNAP) in env[k] or env[k] in ("1", str(port), ROUTER_BIND, GATE_HOST)
              for k in owned_prefixed),
          "outcome": "timeout", "checkpoints": {}, "screenshots": {}}
current_proc = None
current_console = None
gate = ResponseGate((GATE_HOST, port), (ROUTER_BIND, port), OUT / "response-gate.jsonl")
record_tree("probe-process-tree-initial", os.getpid())
guard_thread = threading.Thread(target=guard_loop, daemon=True)
guard_thread.start()
API = "/ca/work/api/v1/parallel?session_id=" + urllib.parse.quote(IDENTITY)


def console_kill_drill() -> dict:
    """Owned-only drill: attach, detach, then kill a live owned tree leaves-first
    by exact (PID, CreationDate), never taskkill /T, while it keeps spawning."""
    spawner = scratch / "drill_spawner.py"
    spawner.write_text(
        "import subprocess, sys, time\n"
        "kids = []\n"
        "end = time.time() + 120\n"
        "while time.time() < end:\n"
        "    kids.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']))\n"
        "    time.sleep(0.07)\n", encoding="utf-8")
    # A .cmd file, as real launches use: an argv string for cmd /k gets
    # backslash-escaped quotes that cmd does not parse (preflight 164715).
    script = scratch / "drill.cmd"
    script.write_text("@echo off\r\necho owned-drill " + STAMP + "\r\n\"" + sys.executable + "\" \""
                      + str(spawner) + "\"\r\n", encoding="utf-8")
    proc = subprocess.Popen(["conhost.exe", "cmd", "/k", str(script)], cwd=str(scratch), env=env)
    root = register_owned_root(proc.pid, "preflight-console-drill")
    console = console_for(proc)
    seen = wait_until(lambda: "owned-drill" in console.screen(), 20, "drill console never drew")

    def spawning():
        rows = process_tree(proc.pid)
        sleepers = [r for r in rows if "time.sleep(120)" in str(r.get("CommandLine") or "")]
        return rows if len(sleepers) >= 5 else None
    before = wait_until(spawning, 30, "drill spawner never produced owned children")
    detach_console()
    summary = kill_tree(proc.pid, "preflight-console-drill")
    self_check("after console drill kill")
    killed = set(map(tuple, summary["killed"]))
    initial = set(map(tuple, summary["initial"]))
    drill_rows = [row for row in kill_log if row.get("label") == "preflight-console-drill"]
    all_rows = all_process_rows()
    alive = {identity(row) for row in all_rows}
    protected_hit = sorted(killed & PROTECTED_IDENTITIES)
    return {"screen_read": bool(seen), "root": root, "tree_before": [identity(r) for r in before],
            "initial_count": len(initial), "killed_count": len(killed), "rounds": summary["rounds"],
            "spawned_during_kill": len(killed - initial), "remaining": summary["remaining"],
            "killed_still_alive": sorted(killed & alive), "protected_killed": protected_hit,
            "any_taskkill_with_tree_flag": any("/T" in (row.get("command") or []) for row in drill_rows),
            "taskkill_commands": sorted({" ".join(row.get("command") or []).split(" /PID ")[0] + " /PID <n> /F" for row in drill_rows if row.get("command")}),
            "taskkill_rc_nonzero": [row["target"] for row in drill_rows if row.get("rc") not in (0, None)],
            "refusals": [row for row in drill_rows if row.get("refused")],
            "self_visible_after_kill": current_identity(os.getpid()) is not None}


def reuse_adoption_check() -> dict:
    """Synthetic rows: a process older than the current holder of its recorded
    parent PID must not join the owned tree (PID reuse cannot adopt it)."""
    rows = [
        {"ProcessId": 100, "ParentProcessId": 1, "Name": "owned-root", "CreationDateUtc": "2026-11-11T10:00:00.0000000Z"},
        {"ProcessId": 101, "ParentProcessId": 100, "Name": "owned-child", "CreationDateUtc": "2026-11-11T10:00:01.0000000Z"},
        {"ProcessId": 900, "ParentProcessId": 100, "Name": "foreign-older", "CreationDateUtc": "2026-11-11T09:00:00.0000000Z"},
        {"ProcessId": 901, "ParentProcessId": 900, "Name": "foreign-grandchild", "CreationDateUtc": "2026-11-11T10:30:00.0000000Z"},
        {"ProcessId": 102, "ParentProcessId": 101, "Name": "owned-grandchild", "CreationDateUtc": "2026-11-11T10:00:02.0000000Z"},
    ]
    accepted = sorted(int(r["ProcessId"]) for r in strict_tree(rows, [(100, "2026-11-11T10:00:00.0000000Z")]))
    stale_root = strict_tree(rows, [(100, "2026-11-11T08:00:00.0000000Z")])
    return {"accepted": accepted, "foreign_rejected": 900 not in accepted and 901 not in accepted,
            "owned_accepted": accepted == [100, 101, 102], "stale_root_identity_rejected": stale_root == []}


result["preflight"] = {"reuse_adoption_check": reuse_adoption_check(),
                       "handle_identity_check": handle_identity_check(),
                       "console_kill_drill": console_kill_drill(), "gate_bound": [GATE_HOST, port],
                       "isolated_env": {k: env[k] for k in ("CIEL_RUNTIME_TEST_ISOLATED", "CLAUDE_CONFIG_DIR",
                                                            "CIEL_RUNTIME_CONFIG_DIR")},
                       "protected_rows_before": len(live_before), "core_pids": list(CORE_PIDS)}
if os.environ.get("PROBE_PREFLIGHT_ONLY") == "1":
    guard_state["stop"] = True
    gate.server.close()
    result["outcome"] = "preflight_only"
    result["live_profile_after_preflight"] = live_profile()["hashes"] == profile_before["hashes"]
    (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "driver_hashes": DRIVER_HASHES, "preflight": result["preflight"],
                      "snapshot": {k: snapshot_proof[k] for k in ("files", "mismatch")},
                      "unexpected_env": result["unexpected_inherited_prefix_names_after_strip"],
                      "owned_env_values": result["owned_prefix_values_are_owned"],
                      "profile_unchanged": result["live_profile_after_preflight"]}, indent=1))
    raise SystemExit(0)

try:
    # Checkpoint A (deterministic): every review_wake prepare is withheld while
    # armed, so each published completion stays pending with wake attempts 0.
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

    def policy_fill():
        value = api_get(API)
        confirmed = (value.get("forks") or {}).get("confirmed") or []
        use = {p: sum(r.get("priority") == p for r in confirmed) for p in ("P1", "P2", "P3")}
        ids = {r.get("agent_id") for r in confirmed if r.get("agent_id")}
        return (value, use) if use == {"P1": 2, "P2": 1, "P3": 1} and len(ids) == 4 else None
    initial, fill_use = wait_until_guarded(policy_fill, 420, "P1=2/P2=1/P3=1 fill was not confirmed", interval=1)
    confirmed = (initial.get("forks") or {}).get("confirmed") or []
    result["initial_workers"] = confirmed
    result["fill_use"] = fill_use
    result["fill_seen_at"] = time.time()
    durable_snapshot("02-workers-running-state")
    capture(current_console, "02-workers-running")
    result["screenshots"]["workers_web"] = both_browser("02-workers-running")

    if not wake_gate.wait(600):
        raise TimeoutError("first review wake prepare did not reach the deterministic gate")
    hit_a = gate.hit("wake-prepare")

    def all_submitted():
        workers = [r for r in lease_rows("worker") if r.get("item_id") in items.values()]
        return workers if len(workers) == 4 and all(r.get("state") == "submitted" for r in workers) else None
    try:
        wait_until_guarded(all_submitted, 300, "not every worker submitted before checkpoint A")
        a_all_submitted = True
    except TimeoutError:
        a_all_submitted = False

    def a_state():
        reviews = finalization().get("reviews") or {}
        return {k: {"item": r.get("item_id"), "state": r.get("state"), "decision": r.get("decision"),
                    "wake": r.get("wake")} for k, r in reviews.items()}
    a_reviews = a_state()
    result["checkpoint_a"] = {"recorded_at": time.time(), "gate_hit": hit_a, "all_workers_submitted": a_all_submitted,
                              "leases": {r["id"]: {"item": r.get("item_id"), "state": r.get("state"),
                                                   "slot": r.get("slot")} for r in lease_rows()},
                              "reviews": a_reviews}
    result["screenshots"]["pending_web"] = both_browser("03-awaiting-main-review", "Awaiting main review")
    capture(current_console, "03-completion-before-review-terminal")
    result["checkpoints"]["a_before_kill"] = {"file": "03-completion-before-review.json",
                                              "at": durable_snapshot("03-completion-before-review")["at"]}
    result["checkpoint_a"]["reviews_at_kill"] = a_state()
    result["checkpoints"]["a_kill"] = stop_owned(current_proc, "03-awaiting-main-review", current_console)
    current_proc = None
    current_console = None
    gate.disarm("wake-prepare")
    result["checkpoint_a"]["held_requests"] = gate.rules["wake-prepare"]["held_requests"]
    gate.release_held()
    result["checkpoint_a"]["reviews_after_kill"] = a_state()

    # Checkpoint B gate, armed before the second process starts: the first bind of
    # the failing item's replacement (role resume) lease is withheld.
    def resume_bind(tool, args):
        if tool != "work_parallel_event" or args.get("event") != "bind":
            return False
        lease = (ledger().get("leases") or {}).get(args.get("lease_id")) or {}
        return lease.get("role") == "resume" and lease.get("item_id") == items[FAIL_TITLE]
    bind_gate = gate.arm("rework-bind", resume_bind)

    current_proc, _ = launch("second", True)
    record_tree("second-process-tree-launch", current_proc.pid)
    current_console = console_for(current_proc)
    if not wait_ready(current_console, False):
        raise RuntimeError("first resume console did not become ready")
    record_tree("second-process-tree-ready", current_proc.pid)

    def woken_undecided():
        rows = finalization().get("reviews") or {}
        woken = [k for k, r in rows.items() if r.get("decision") is None
                 and (r.get("wake") or {}).get("state") in ("queued", "appended")]
        return woken if woken and wake_messages().keys() & set(woken) else None
    try:
        woken = wait_until_guarded(woken_undecided, 240, "no delivered wake observed before a decision", .25)
        capture(current_console, "04-pending-review")
        result["screenshots"]["pending_terminal"] = {"reviews": woken, "at": time.time()}
    except TimeoutError as error:
        result["screenshots"]["pending_terminal"] = {"missed": str(error)}

    def rework_decided():
        rows = finalization().get("reviews") or {}
        reworked = [(k, r) for k, r in rows.items() if r.get("decision") == "rework"
                    and r.get("item_id") == items[FAIL_TITLE]]
        return reworked or None
    reworked_rows = wait_until_guarded(rework_decided, 900, "main did not choose rework for the failing item")
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
    b_row, b_lease = wait_until_guarded(b_durable, 30, "rework launch was not durable before bind")
    result["screenshots"]["decision_web"] = both_browser("05-main-decisions", "Rework")

    # Kill only after every other first-round review is decided and main is idle,
    # so the restart tests the rework launch, not an undelivered review.
    def others_decided_idle():
        rows = finalization().get("reviews") or {}
        others = [r for r in rows.values() if r.get("item_id") != items[FAIL_TITLE]]
        return others if len(others) == 3 and all(r.get("decision") for r in others) \
            and is_idle(current_console) else None
    try:
        wait_until_guarded(others_decided_idle, 900, "other reviews undecided or main busy at checkpoint B")
        b_others_decided = True
    except TimeoutError:
        b_others_decided = False
    b_row = (finalization().get("reviews") or {}).get(b_review_key) or {}
    b_lease = (ledger().get("leases") or {}).get(replacement_id) or {}
    result["checkpoint_b"] = {"recorded_at": time.time(), "gate_hit": hit_b, "review_lease": b_review_key,
                              "replacement_lease": replacement_id, "review_state": b_row.get("state"),
                              "launch_state": b_row["launch"]["state"], "launch_attempt": b_row["launch"]["attempt"],
                              "launch_updated_at": b_row["launch"]["updated_at"],
                              "dispatch_name": b_row["launch"]["dispatch_name"],
                              "observed_agent_id": b_row["launch"]["agent_id"],
                              "ledger_state": b_lease.get("state"), "ledger_host_id": b_lease.get("host_id"),
                              "token_hash_at_kill": b_lease.get("report_hash"),
                              "workspace_at_kill": b_lease.get("workspace"),
                              "other_reviews_decided": b_others_decided}
    capture(current_console, "05-rework-before-bind-terminal")
    result["checkpoints"]["b_before_kill"] = {"file": "05-rework-before-bind.json",
                                              "at": durable_snapshot("05-rework-before-bind")["at"]}
    result["checkpoints"]["b_kill"] = stop_owned(current_proc, "05-rework-before-bind", current_console)
    current_proc = None
    current_console = None
    gate.disarm("rework-bind")
    result["checkpoint_b"]["held_requests"] = gate.rules["rework-bind"]["held_requests"]
    gate.release_held()
    result["checkpoint_b"]["after_kill"] = {
        "launch": ((finalization().get("reviews") or {}).get(b_review_key) or {}).get("launch"),
        "ledger_host_id": ((ledger().get("leases") or {}).get(replacement_id) or {}).get("host_id")}
    forks_before_third = {row["agent_id"] for row in fork_rows()}

    current_proc, _ = launch("third", True)
    record_tree("third-process-tree-launch", current_proc.pid)
    current_console = console_for(current_proc)
    if not wait_ready(current_console, False):
        raise RuntimeError("second resume console did not become ready")
    record_tree("third-process-tree-ready", current_proc.pid)

    bound = wait_until_guarded(lambda: (ledger().get("leases") or {}).get(replacement_id)
                               if ((ledger().get("leases") or {}).get(replacement_id) or {}).get("host_id") else None,
                               420, "replacement fork never bound after restart")
    result["replacement_bound"] = {"host_id": bound.get("host_id"), "state": bound.get("state"),
                                   "agent_name": bound.get("agent_name"), "dispatch_name": bound.get("dispatch_name"),
                                   "token_hash": bound.get("report_hash"), "workspace": bound.get("workspace")}
    durable_snapshot("06-rework-running-state")
    try:
        wait_until_guarded(lambda: "work-rework-" in current_console.screen(), 60,
                           "terminal agent list never showed the rework fork")
    except TimeoutError as error:
        result["screenshots"]["rework_terminal_note"] = str(error)
    capture(current_console, "06-rework-running")
    result["screenshots"]["rework_web"] = both_browser("06-rework-running", "Rework")

    def all_done():
        reviews = finalization().get("reviews") or {}
        finals = {title: [r for r in reviews.values() if r.get("item_id") == items[title]
                          and r.get("decision") == "approve" and r.get("state") == "finalized"] for title in TITLES}
        return finals if all(finals.values()) and all(board_status(t) == "done" for t in TITLES) else None
    wait_until_guarded(all_done, 900, "not every item reached approved finalized/done", interval=1)
    final_state = durable_snapshot("07-final-state")
    wait_until_guarded(lambda: is_idle(current_console), 300, "main did not become idle after finalization", 1)
    capture(current_console, "07-before-status")
    show_parallel(current_console, "DEVICE: slots")
    capture(current_console, "07-finalized")
    result["screenshots"]["final_web"] = both_browser("07-finalized", "Ended", expand_ended=True)

    tools = main_tool_inputs()
    tool_text = json.dumps(tools, ensure_ascii=False)
    reviews = final_state["finalization"].get("reviews") or {}
    all_leases = list((final_state["ledger"].get("leases") or {}).values())
    all_runs = list((final_state["coordination"].get("runs") or {}).values())
    archive_runs = [entry.get("run") or {} for entry in (final_state["coordination"].get("archive") or [])]
    fork_meta = fork_rows()
    reviewers = [row for row in all_leases + all_runs + archive_runs if row.get("role") == "reviewer"]
    worker_leases = [row for row in all_leases if row.get("role") == "worker"]
    resume_leases = [row for row in all_leases if row.get("role") == "resume"]
    fail_rows = [r for r in reviews.values() if r.get("item_id") == items[FAIL_TITLE]]
    rework_row = next(r for r in fail_rows if r.get("decision") == "rework")
    second_review = next(r for r in fail_rows if r.get("decision") == "approve")
    launch = rework_row.get("launch") or {}
    audit = launch.get("audit") or []
    rework_forks = [row for row in fork_meta if str(row.get("name") or "").startswith("work-rework-")]
    third_forks = [row for row in fork_meta if row["agent_id"] not in forks_before_third]
    third_rework = [row for row in third_forks if str(row.get("name") or "").startswith("work-rework-")]
    original_worker = next(r for r in worker_leases if r.get("item_id") == items[FAIL_TITLE])
    wake_attempts = {key: (row.get("wake") or {}).get("attempts") for key, row in reviews.items()}
    messages = wake_messages()
    decisions = {key: {"item": row.get("item_id"), "decision": row.get("decision"), "state": row.get("state")}
                 for key, row in reviews.items()}
    flights = prompt_submit_flights()
    shown = presentations()
    stuck_submitting = sorted(key for key, marks in wake_observations.items()
                              if any(m[0] == "submitting" for m in marks)
                              and not any(m[0] in ("queued", "appended") for m in marks))
    stop_before_own_wake = sorted(lease for lease, seen in shown.items()
                                  if seen["wake"] and any(stop and stop < max(seen["wake"]) for stop in seen["stop"]))
    result["fifo"] = {"prompt_submit_flights": flights, "presentations": shown,
                      "stuck_submitting": stuck_submitting, "stop_before_own_wake": stop_before_own_wake}
    result["final"] = {"board_status": {t: board_status(t) for t in TITLES}, "reviewers": reviewers,
                       "resume_lease_ids": [r["id"] for r in resume_leases], "rework_forks": rework_forks,
                       "third_process_forks": third_forks, "wake_attempts": wake_attempts,
                       "wake_messages": messages, "decisions": decisions,
                       "launch": {k: launch.get(k) for k in ("state", "attempt", "dispatch_name", "agent_id", "audit")},
                       "original_worker_workspace": original_worker.get("workspace"),
                       "replacement_workspace": bound.get("workspace"), "forks": fork_meta,
                       "review_calls": [row for row in tools if "work_parallel_item_review" in str(row.get("name"))]}
    absent_rows = [row for row in audit if row.get("kind") == "absent"]
    result["acceptance"] = {
        "snapshot_current_bytes_verified": not snapshot_proof["mismatch"]
                                           and snapshot_proof["key_hashes"] == KEY_HASHES,
        "minimal_provider_config_only": config_proof["providers"] == ["cielairouter"],
        "isolated_claude_config": (project_dir() / (SESSION + ".jsonl")).exists()
                                  and not (Path.home() / ".claude/projects" / project_dir().name).exists(),
        "inherited_sensitive_env_stripped": not result["unexpected_inherited_prefix_names_after_strip"]
                                            and result["owned_prefix_values_are_owned"],
        "workers_filled_p1_2_p2_1_p3_1": fill_use == {"P1": 2, "P2": 1, "P3": 1},
        "no_reviewer_forks": not reviewers and all(row.get("type") == "fork"
                                                   and str(row.get("name") or "").startswith(("work-auto-", "work-rework-"))
                                                   for row in fork_meta),
        "checkpoint_a_completion_before_review": a_all_submitted
            and all(v["decision"] is None and (v["wake"] or {}).get("attempts") == 0
                    for v in result["checkpoint_a"]["reviews_at_kill"].values())
            and result["checkpoints"]["a_kill"]["proc_exited"],
        "five_reviews_each_woken_exactly_once": len(reviews) == 5 and all(a == 1 for a in wake_attempts.values()),
        "wake_delivery_append_or_prompt": all(any(mark[0] in ("queued", "appended") for mark in
                                                  wake_observations.get(key, [])) for key in reviews),
        "no_duplicate_wake_messages": all(len(v) <= 1 for v in messages.values()),
        "no_wake_left_submitting": not stuck_submitting,
        "no_stop_data_for_pending_prompt": not stop_before_own_wake
                                           and all(len(seen["wake"]) <= 1 for seen in shown.values()),
        "at_most_one_prompt_submit_in_flight": all(row["peak_in_flight"] <= 1 for row in flights.values()),
        "main_read_tests_and_decided": all(name in tool_text for name in ("test_pass.py", "test_fail.py"))
                                       and len([r for r in reviews.values() if r.get("decision")]) == 5,
        "approved_items_finalized_done": all(any(r.get("decision") == "approve" and r.get("state") == "finalized"
                                                 for r in reviews.values() if r.get("item_id") == items[t])
                                             and board_status(t) == "done" for t in TITLES),
        "failed_test_reworked": rework_row.get("state") == "rework_ready",
        "checkpoint_b_launch_durable_before_bind": result["checkpoints"]["b_kill"]["proc_exited"]
            and result["checkpoint_b"]["launch_state"] in ("launch_intent", "spawn_observed")
            and result["checkpoint_b"]["ledger_host_id"] is None
            and result["checkpoint_b"]["after_kill"]["ledger_host_id"] is None
            and result["checkpoint_b"]["other_reviews_decided"],
        "one_replacement_lease": len(resume_leases) == 1 and resume_leases[0]["id"] == replacement_id,
        "exactly_one_rework_fork_after_restart": len(third_rework) == 1
            and third_rework[0]["agent_id"] == bound.get("host_id"),
        "killed_attempt_resolved_absent_once": len(absent_rows) == 1
            and absent_rows[0].get("dispatch_name") == result["checkpoint_b"]["dispatch_name"],
        "token_rotated_once_for_new_attempt": bound.get("report_hash") not in (None, result["checkpoint_b"]["token_hash_at_kill"])
            and launch.get("attempt") == result["checkpoint_b"]["launch_attempt"] + 1,
        "rework_same_retained_workspace": (original_worker.get("workspace") or {}).get("path")
                                          == (bound.get("workspace") or {}).get("path") is not None,
        "replacement_second_review_approved_finalized": second_review.get("state") == "finalized",
        "terminal_captures": all((OUT / name).exists() for name in
                                 ("04-pending-review.png", "05-main-decisions.png",
                                  "06-rework-running.png", "07-finalized.png")),
        "web_1440_and_390_captures": all((OUT / (name + suffix)).exists() for name in
                                         ("03-awaiting-main-review", "05-main-decisions", "06-rework-running",
                                          "07-finalized") for suffix in ("-desktop.png", "-mobile.png")),
    }
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
    guard_state["stop"] = True
    if current_proc is not None:
        try:
            detach_console()
            kill_tree(current_proc.pid, "final-current")
        except Exception as error:
            diag({"final_current_kill": repr(error)})
    gate.release_held()
    try:
        gate.server.close()
    except OSError:
        pass
    time.sleep(2)
    for label, action in (("final_owned_command_pids", lambda: kill_owned_commands([str(BASE), str(scratch), str(SNAP)])),
                          ("final_owned_router_pids", lambda: kill_owned_router(port))):
        try:
            result[label] = action()
        except Exception as error:
            result[label] = "error: " + repr(error)
    try:
        self_check("final")
        live_after = protected_rows()
        before_by_id = {row["ProcessId"]: row for row in live_before}
        after_by_id = {row["ProcessId"]: row for row in live_after}
        core_changed = [pid for pid in CORE_PIDS
                        if (identity(before_by_id[pid]) if pid in before_by_id else None)
                        != (identity(after_by_id[pid]) if pid in after_by_id else None)]
        killed_identities = {(row["target"]["pid"], row["target"]["creation"]) for row in kill_log
                             if row.get("target") and not row.get("refused")}
        before_identities = {identity(row) for row in live_before}
        after_identities = {identity(row) for row in live_after}
        gone = sorted(before_identities - after_identities)
        result["protected"] = {"before": len(before_identities), "after": len(after_identities),
                               "core_changed": core_changed,
                               "gone_descendants": [row for row in live_before if identity(row) in gone],
                               "killed_by_probe_identity_overlap": sorted(killed_identities & before_identities),
                               "kill_refusals": [r for r in kill_log if r.get("refused")]}
        (OUT / "live-after.json").write_text(json.dumps({"protected": live_after}, indent=1), encoding="utf-8")
        result["protected_pids_unchanged"] = (not core_changed
                                              and not result["protected"]["killed_by_probe_identity_overlap"])
    except Exception as error:
        result["protected_pids_unchanged"] = False
        result["protected_error"] = repr(error)
    profile_after = live_profile()
    result["live_profile"] = {"before": profile_before, "after": profile_after,
                              "unchanged": profile_after["hashes"] == profile_before["hashes"]
                                           and not profile_after["claude_json_owned_project_entries"],
                              "violation": guard_state["profile_violation"]}
    snapshot_before = json.loads((OUT / "snapshot-manifest.json").read_text(encoding="utf-8"))
    snapshot_after = tree_manifest(SNAP)
    changed_snapshot = sorted(k for k in set(snapshot_before) | set(snapshot_after)
                              if snapshot_before.get(k) != snapshot_after.get(k))
    engine_changes = [k for k in changed_snapshot if engine_generated(k)]
    product_changes = [k for k in changed_snapshot if not engine_generated(k)]
    result["snapshot_changes"] = {"engine_generated_excluded": engine_changes, "product": product_changes}
    result["snapshot_unchanged_after_run"] = not product_changes
    if result.get("acceptance") is not None:
        result["acceptance"]["protected_pids_unchanged"] = result["protected_pids_unchanged"]
        result["acceptance"]["live_profile_unchanged"] = result["live_profile"]["unchanged"]
        result["acceptance"]["snapshot_unchanged_after_run"] = result["snapshot_unchanged_after_run"]
        result["outcome"] = "passed" if all(result["acceptance"].values()) else "failed"
    result["preserved_config"] = str(config)
    result["preserved_workspace"] = str(workspace)
    result["preserved_claude_config"] = str(claude_config)
    result["wake_observations"] = wake_observations
    result["keypresses"] = keypress_log
    result["kills"] = kill_log
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
