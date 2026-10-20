"""E2E: choose first-level folders and the git worktree mode in the menu, then back up.

Work folder (a git repository) on G:: notes.md, data/ (left out from the menu), src/data/keep.txt
(same name deeper: must stay), a linked worktree inside (.claude/worktrees/feat) and one outside
(../outside-wt). Isolated config/home. Steps in e2e_folders_steps.py (key events into the menu console):
  1 Work folder section shows folders, worktree counts; Measure folder sizes
  2 data -> left out
  3 Back up now (worktrees off) -> result lists the left-out folder and worktree
  4 Git worktrees -> inside -> all; Back up now -> the outside worktree is in the snapshot
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
BASE = Path(r"G:\ciel-e2e\folders")
shutil.rmtree(BASE, ignore_errors=True)
CFG, WS, HOME, OUT = BASE / "cfg", BASE / "ws", BASE / "home", BASE / "outside-wt"
RUN = HERE / "e2e-1-menu"
shutil.rmtree(RUN, ignore_errors=True)
for d in (CFG, WS, HOME, RUN):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.name=e2e", "-c", "user.email=e2e@example.invalid", *args], cwd=WS, check=True,
                   capture_output=True, text=True)


(WS / "notes.md").write_text("plan\n", encoding="utf-8")
(WS / "data").mkdir()
(WS / "data" / "big.bin").write_bytes(os.urandom(3 * 1024 * 1024))
(WS / "src" / "data").mkdir(parents=True)
(WS / "src" / "data" / "keep.txt").write_text("nested data stays\n", encoding="utf-8")
git("init", "-q")
git("add", "notes.md", "src")
git("commit", "-q", "-m", "init")
git("worktree", "add", "-q", str(WS / ".claude" / "worktrees" / "feat"))
git("worktree", "add", "-q", str(OUT))
(WS / ".claude" / "worktrees" / "feat" / "inside-wip.txt").write_text("inside\n", encoding="utf-8")
(OUT / "outside-wip.txt").write_text("outside\n", encoding="utf-8")
(CFG / "config.json").write_text(json.dumps({"current_provider": "vllm", "language": "en", "providers": {
    "vllm": {"base_url": "http://127.0.0.1:9", "api_key": "dummy", "current_model": "m"}}}), encoding="utf-8")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch-menu.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\nset "CODEX_HOME={BASE / "codex-home"}"\n'
    f'set "USERPROFILE={HOME}"\nset "HOME={HOME}"\nset "CLAUDE_CONFIG_DIR={HOME / ".claude"}"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["conhost.exe", "cmd", "/k", str(script)])
pid = 0
end = time.time() + 60
while time.time() < end and not pid:
    time.sleep(1)
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*--ca-menu*' -and $_.CommandLine -notlike '*e2e_*' } | % { $_.ProcessId }"],
                         capture_output=True, text=True).stdout.split()
    pid = int(out[-1]) if out else 0
log(f"menu pid {pid}")
steps = subprocess.run([sys.executable, str(HERE / "e2e_folders_steps.py"), str(pid), str(RUN)], creationflags=0x00000008, timeout=900)
log(f"steps exit {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
log(f"session-backup.json folders: {json.loads((CFG / 'session-backup.json').read_text(encoding='utf-8')).get('folders')}")
for manifest_path in sorted((CFG / "backups" / "snapshots").rglob("*.json")):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = sorted(f"{e['root']}:{e['path']}" for e in manifest["entries"] if e["root"] not in ("ciel", "ciel_ws") and not e["path"].startswith(".git/"))
    skipped = sorted(f"{s['path']} ({s['reason']})" for s in manifest["skipped"] if s["reason"] in ("not selected", "worktree"))
    log(f"snapshot {manifest['id']}: roots {sorted(manifest['roots'])}\n   files {paths}\n   left out {skipped}")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*"
                    + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*') } | % { taskkill /T /F /PID $_.ProcessId | Out-Null; \"killed $($_.ProcessId)\" }"],
                   capture_output=True, text=True)
log("teardown: " + r.stdout.strip().replace("\n", " "))
log("done")
