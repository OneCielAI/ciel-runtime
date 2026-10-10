"""E2E: every session-backup operation driven through the menu only (18. Session backup panel).

The menu runs in its own classic console window (conhost) with the e2e-3 layout (cfg2/ws2/codex2/home2).
e2e_menu_steps.py, started detached, attaches to that console and types real key events, waiting for each
prompt on screen: add a local-folder target 'nas' (connection test, selected), deselect the built-in
'local', test 'nas' from its row, create a backup key file, back up twice (the second shows the incremental
upload), list, verify, restore (confirmed), prune. Nothing goes through the desktop IME or the clipboard.
If a key ever lands on Launch, the routed upstream is a closed local port, so nothing leaves this PC.
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
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
BASE = Path(os.environ["E2E_SCRATCH"]) / "e2e-3-migrate-triggers"
CFG, WS, CODEX, HOME = BASE / "cfg2", BASE / "ws2", BASE / "codex2", BASE / "home2"
NAS = BASE / "nas-target"
KEYFILE = BASE / "keys" / "backup.key"
RUN = HERE / "e2e-5-menu-targets"
shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir(parents=True)
shutil.rmtree(NAS, ignore_errors=True)
shutil.rmtree(KEYFILE.parent, ignore_errors=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def settings() -> dict:
    return json.loads((CFG / "session-backup.json").read_text(encoding="utf-8"))


# Start from no extra targets and no key file (an earlier attempt left 'nas' in this scratch config).
current = settings()
(CFG / "session-backup.json").write_text(json.dumps({"targets": {}, "default_targets": [], "schedule": current.get("schedule", {})}, indent=2),
                                         encoding="utf-8")
log(f"settings before: {json.dumps({k: v for k, v in settings().items() if k != 'schedule'})}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch-menu.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CODEX_HOME={CODEX}"\n'
    f'set "USERPROFILE={HOME}"\nset "HOME={HOME}"\nset "CLAUDE_CONFIG_DIR={HOME / ".claude"}"\n'
    'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:9/backend-api/codex"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["conhost.exe", "cmd", "/k", str(script)])


def menu_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*--ca-menu*' } | "
                          "% { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[-1]) if out else 0


pid = 0
end = time.time() + 60
while time.time() < end and not pid:
    time.sleep(1)
    pid = menu_pid()
log(f"menu python pid: {pid}")
steps = subprocess.run([sys.executable, str(HERE / "e2e_menu_steps.py"), str(pid), str(RUN), str(NAS), str(KEYFILE)],
                       creationflags=0x00000008, timeout=1500)  # DETACHED_PROCESS: no console of its own
log(f"steps exit: {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
after = settings()
log(f"settings after: {json.dumps({k: v for k, v in after.items() if k != 'schedule'})}")
log(f"key file exists: {KEYFILE.is_file()} ({len(KEYFILE.read_text().strip()) if KEYFILE.is_file() else 0} hex chars)")
log(f"nas target snapshots: {sorted(p.stem for p in (NAS / 'snapshots').rglob('*.json')) if NAS.exists() else []}")
blob_text = b""
import zlib  # noqa: E402

for p in (NAS / "blobs").rglob("*") if NAS.exists() else []:
    if p.is_file():
        blob_text += zlib.decompress(p.read_bytes())
log(f"backup key in nas chunks: {KEYFILE.read_bytes().strip() in blob_text if KEYFILE.is_file() else 'n/a'}")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and $_.CommandLine -like '*"
                    + str(RUN) + "*' } | % { taskkill /T /F /PID $_.ProcessId | Out-Null; \"killed $($_.ProcessId)\" }"],
                   capture_output=True, text=True)
log("teardown: " + r.stdout.strip().replace("\n", " "))
log("done")
