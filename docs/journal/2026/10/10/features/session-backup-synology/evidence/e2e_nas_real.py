"""Real NAS test through the menu: a small work folder backed up to a Synology NAS over QuickConnect.

Isolated config/home on G: (the person's own Ciel settings are not touched). The person types the DSM
password into the menu window; the steps (e2e_nas_real_steps.py) do the rest by key events.
Usage: python e2e_nas_real.py <quickconnect id> <account> <nas folder>
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
QC_ID, ACCOUNT, FOLDER = sys.argv[1:4]
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
BASE = Path(r"G:\ciel-e2e\nas-real")
shutil.rmtree(BASE, ignore_errors=True)
CFG, WS, HOME = BASE / "cfg", BASE / "ws", BASE / "home"
RUN = HERE / "e2e-2-real-nas"
shutil.rmtree(RUN, ignore_errors=True)
for d in (CFG, WS / "src", HOME, RUN):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


(WS / "notes.md").write_text("plan v1\n", encoding="utf-8")
(WS / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
(WS / "random-64mb.bin").write_bytes(os.urandom(64 * 1024 * 1024))
(WS / "log.jsonl").write_text('{"type": "message", "text": "lorem ipsum"}\n' * 200000, encoding="utf-8")
(CFG / "config.json").write_text(json.dumps({"current_provider": "vllm", "language": "en", "providers": {
    "vllm": {"base_url": "http://127.0.0.1:9", "api_key": "dummy", "current_model": "m"}}}), encoding="utf-8")
log(f"work folder {sum(p.stat().st_size for p in WS.rglob('*') if p.is_file()) / 1048576:.1f} MB")
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n'
                for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = RUN / "launch-menu.cmd"
script.write_text(
    "@echo off\ntitle Ciel NAS test - type the DSM password when asked\n" + clear + "set CLAUDECODE=\n"
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
steps = subprocess.run([sys.executable, str(HERE / "e2e_nas_real_steps.py"), str(pid), str(RUN), QC_ID, ACCOUNT, FOLDER, str(WS)],
                       creationflags=0x00000008, timeout=7200)
log(f"steps exit {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
settings = (CFG / "session-backup.json").read_text(encoding="utf-8") if (CFG / "session-backup.json").exists() else "{}"
has_password = "passwd" in settings or '"password"' in settings
log(f"session-backup.json has a password field: {has_password}")
for f in (CFG / "backup-credentials").glob("*"):
    log(f"credential file {f.name}: {f.stat().st_size} bytes")
log("done; the menu window stays open")
