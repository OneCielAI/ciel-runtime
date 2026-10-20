"""Menu E2E (Walkie 53255): 11. Remote instructions -> "Reload session when the company prompt changes" off, then on.

usage: python menu_e2e.py
Isolated CIEL_RUNTIME_CONFIG_DIR (stub vllm provider, no real key) and work folder on G:; a detached driver
(menu_drive.py) presses the keys and keeps each screen as text and PNG under e2e-menu/.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
run = here / "e2e-menu"
shutil.rmtree(run, ignore_errors=True)
run.mkdir(parents=True)
scratch = Path(r"G:\ciel-e2e\company-prompt\menu")
shutil.rmtree(scratch, ignore_errors=True)
cfg, ws = scratch / "cfg", scratch / "ws"
cfg.mkdir(parents=True)
ws.mkdir(parents=True)
(cfg / "config.json").write_text(json.dumps({
    "current_provider": "vllm", "language": "en",
    "providers": {"vllm": {"base_url": "http://127.0.0.1:9", "current_model": "stub-model", "api_key": "dummy"}},
    "remote_instructions": {"enabled": True, "codex_url": "http://127.0.0.1:9/instructions"},
}), encoding="utf-8")
# The workspace already holds company prompt v8 (written by an earlier launch): the row shows it.
(ws / "AGENTS.md").write_text("<!-- company-prompt version=8 sha256=c344e8a6" + "0" * 56 + " source=latest -->\n# prompt\n",
                              encoding="utf-8")
title = f"CIELE2E-CP-MENU-{int(time.time())}"
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "CLAUDE_", "CODEX_"))
script = run / "launch.cmd"
script.write_text("@echo off\n" + clear + "set CLAUDECODE=\n"
                  f'set "CIEL_RUNTIME_CONFIG_DIR={cfg}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
                  f'cd /d "{ws}"\npython "C:\\Users\\djlov\\ciel-runtime\\ciel_runtime.py" cli --ca-menu --ca-no-update-check --ca-no-self-update-check\n'
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
    driver = subprocess.Popen([sys.executable, str(here / "menu_drive.py"), str(pid), title, str(run)], creationflags=0x00000008)
    driver.wait(timeout=180)
result = {"remote_instructions": json.loads((cfg / "config.json").read_text(encoding="utf-8")).get("remote_instructions")}
(run / "result.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
print(json.dumps(result, indent=1, ensure_ascii=False))
subprocess.run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*" + str(script) + "*' } | % { taskkill /T /F /PID $_.ProcessId | Out-Null }"])
