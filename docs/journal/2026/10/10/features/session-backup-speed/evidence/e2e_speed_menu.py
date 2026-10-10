"""E2E: Parallel transfers set in the menu, timed backups of a 1.1 GB work folder (G:\\ciel-e2e\\bench\\ws,
made by bench_backup.py) to the built-in local target, isolated config. Steps (key events, e2e_speed_menu_steps.py):
  1 Parallel transfers -> 1, Back up now (first backup)      -> the driver deletes the stored backups
  2 Parallel transfers -> 8, Back up now (first backup again)
  3 Back up now (incremental)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
WS = Path(r"G:\ciel-e2e\bench\ws")
BASE = Path(r"G:\ciel-e2e\speed")
shutil.rmtree(BASE, ignore_errors=True)
CFG, HOME = BASE / "cfg", BASE / "home"
RUN = HERE / "e2e-1-menu"
shutil.rmtree(RUN, ignore_errors=True)
for d in (CFG, HOME, RUN):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


assert WS.is_dir(), "run bench_backup.py first"
log(f"work folder {sum(p.stat().st_size for p in WS.rglob('*') if p.is_file()) / 1048576:.0f} MB")
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


def reset_backups() -> None:
    while not (RUN / "reset").exists():
        time.sleep(0.5)
    shutil.rmtree(CFG / "backups", ignore_errors=True)
    (CFG / "session-backup-state.json").unlink(missing_ok=True)
    (RUN / "reset").unlink()
    (RUN / "reset-done").write_text("1")
    log("stored backups removed for the second first-backup")


threading.Thread(target=reset_backups, daemon=True).start()
steps = subprocess.run([sys.executable, str(HERE / "e2e_speed_menu_steps.py"), str(pid), str(RUN)], creationflags=0x00000008, timeout=1800)
log(f"steps exit {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
for manifest_path in sorted((CFG / "backups" / "snapshots").rglob("*.json")):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    log(f"snapshot {manifest['id']}: stats {manifest.get('stats')}")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*"
                    + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*') } | % { taskkill /T /F /PID $_.ProcessId | Out-Null; \"killed $($_.ProcessId)\" }"],
                   capture_output=True, text=True)
log("teardown: " + r.stdout.strip().replace("\n", " "))
shutil.rmtree(BASE, ignore_errors=True)
log("done")
