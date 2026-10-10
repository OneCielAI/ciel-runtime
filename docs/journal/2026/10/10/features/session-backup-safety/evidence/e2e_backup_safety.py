"""E2E: session backup on a nearly full disk and on a large work folder, through the menu only.

Scratch on G: (C: has ~20 MB free on this PC, which is the condition under test). Isolated Ciel config.
Work folder: src/ (a script and a 1.1 GB file over the file limit) and bigdata/ (12 x 256 MB). Targets: 'cdrive' (a folder on C:) and the built-in 'local'
(in the scratch config on G:). Steps, by real key events into the menu's console (console_driver):
  1 select only 'cdrive' -> Back up now -> refused before reading: "not enough space on target cdrive ..."
  2 select only 'local'  -> Back up now -> progress line while reading bigdata -> Esc -> "Backup not saved: cancelled"
  3 Exclude from the work folder -> sizes listed (bigdata 3 GB) -> leave out 'bigdata'
  4 Back up now -> saved, incremental line, "Not in this backup: 1 file(s)" naming src/huge-model.bin;
    the snapshot has src/main.py only
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
BASE = Path(r"G:\ciel-e2e\backup-safety")
shutil.rmtree(BASE, ignore_errors=True)
CFG, WS = BASE / "cfg", BASE / "ws"
CDRIVE = Path(os.environ["LOCALAPPDATA"]) / "Temp" / "ciel-e2e-cdrive"
RUN = HERE / "e2e-1-menu"
shutil.rmtree(RUN, ignore_errors=True)
for d in (CFG, WS / "src", WS / "bigdata", RUN):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


(WS / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
block = os.urandom(1024 * 1024)
for index in range(12):  # 12 x 256 MB: long enough to watch progress and cancel
    with open(WS / "bigdata" / f"part-{index:02d}.bin", "wb") as stream:
        for _ in range(256):
            stream.write(block[:-index - 1] + os.urandom(index + 1))
with open(WS / "src" / "huge-model.bin", "wb") as stream:  # over the 1024 MB file limit: listed, not stored
    for _ in range(1100):
        stream.write(block)
(CFG / "config.json").write_text(json.dumps({"current_provider": "vllm", "language": "en", "providers": {
    "vllm": {"base_url": "http://127.0.0.1:9", "api_key": "dummy", "current_model": "m"}}}), encoding="utf-8")
(CFG / "session-backup.json").write_text(json.dumps({
    "targets": {"cdrive": {"type": "local", "path": str(CDRIVE)}}, "default_targets": ["cdrive"]}), encoding="utf-8")
log(f"C free MB: {shutil.disk_usage('C:/').free / 1048576:.1f}; G free GB: {shutil.disk_usage('G:/').free / 1024 ** 3:.1f}")

clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch-menu.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\nset "CODEX_HOME={BASE / "codex-home"}"\n'
    f'set "USERPROFILE={BASE / "home"}"\nset "HOME={BASE / "home"}"\nset "CLAUDE_CONFIG_DIR={BASE / "home" / ".claude"}"\n'
    'set "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:9/backend-api/codex"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["conhost.exe", "cmd", "/k", str(script)])
pid = 0
end = time.time() + 60
while time.time() < end and not pid:
    time.sleep(1)
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*--ca-menu*' } | % { $_.ProcessId }"],
                         capture_output=True, text=True).stdout.split()
    pid = int(out[-1]) if out else 0
log(f"menu pid {pid}")
steps = subprocess.run([sys.executable, str(HERE / "e2e_backup_safety_steps.py"), str(pid), str(RUN)], creationflags=0x00000008, timeout=1500)
log(f"steps exit {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
snapshots = sorted((CFG / "backups" / "snapshots").rglob("*.json")) if (CFG / "backups").exists() else []
for manifest_path in snapshots:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["entries"] if entry["root"] == "cwd"]
    log(f"snapshot {manifest['id']}: cwd files {paths}; stats {manifest.get('stats')}")
log(f"cdrive target written: {sorted(p.name for p in CDRIVE.rglob('*'))[:5] if CDRIVE.exists() else 'nothing'}")
log(f"session-backup.json: {json.dumps(json.loads((CFG / 'session-backup.json').read_text(encoding='utf-8')))}")
log(f"state: {(CFG / 'session-backup-state.json').read_text(encoding='utf-8') if (CFG / 'session-backup-state.json').exists() else '-'}")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*"
                    + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*') } | % { taskkill /T /F /PID $_.ProcessId | Out-Null; \"killed $($_.ProcessId)\" }"],
                   capture_output=True, text=True)
log("teardown: " + r.stdout.strip().replace("\n", " "))
shutil.rmtree(CDRIVE, ignore_errors=True)
log("done")
