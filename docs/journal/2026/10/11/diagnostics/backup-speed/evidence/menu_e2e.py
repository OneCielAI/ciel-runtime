"""Open the Ciel menu from this checkout in a new Windows Terminal window at a work folder and run a backup from it.

usage: python menu_e2e.py WORK_FOLDER RUN_DIR "ROW TEXT"
The window starts with every CIEL_RUNTIME_*/CLAUDE_*/CODEX_* variable cleared, the user's own config.
"""
import subprocess
import sys
import time
from pathlib import Path

work, run, row_text = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
run.mkdir(parents=True, exist_ok=True)
here = Path(__file__).resolve().parent
title = f"CIELE2E-BACKUP-MENU-{int(time.time())}"
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "CLAUDE_", "CODEX_"))
script = run / "launch.cmd"
script.write_text("@echo off\n" + clear + "set CLAUDECODE=\n"
                  f'cd /d "{work}"\npython "C:\\Users\\djlov\\ciel-runtime\\ciel_runtime.py" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
                  "echo MENU-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def menu_pid() -> int:
    command = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*--ca-menu*' -and "
               "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
               + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


pid = 0
deadline = time.time() + 60
while not pid and time.time() < deadline:
    pid = menu_pid()
    time.sleep(1)
print("menu pid", pid, "title", title, flush=True)
if pid:
    subprocess.Popen([sys.executable, str(here / "menu_drive.py"), str(pid), title, str(run), row_text], creationflags=0x00000008)
