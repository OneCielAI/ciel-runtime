"""Menu E2E: the real prelaunch menu of this worktree, isolated, driven by key events.

Opens ``ciel_runtime.py cli`` in a Windows Terminal window with its own config
directory and router port (inherited CIEL_RUNTIME_/CLAUDE/ANTHROPIC/CODEX_/
OPENAI_ variables are cleared in the launch script), then a detached driver
(menu_driver.py) attaches to that console, walks to "External event inputs",
sets the resend attempt limit, and saves each screen.  PNG shots come from
shot_title.ps1.  Only processes whose command line names this run directory
are stopped at the end.

Usage: python menu_e2e.py <worktree-root> <evidence-dir>
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
EVIDENCE = Path(sys.argv[2]).resolve()
HERE = Path(__file__).resolve().parent
SHOT = ROOT / "docs/journal/2026/10/16/features/cielairouter-direct-path/evidence/e2e/shot_title.ps1"
RUN = Path(r"G:\ciel-e2e\sse-resend") / f"menu-{int(time.time())}"
TITLE = f"CIEL-RESEND-MENU-{RUN.name}"
SHOTS = EVIDENCE / "menu-shots"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def shot(label: str) -> str:
    out = SHOTS / f"{label}.png"
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SHOT), TITLE, str(out)],
                          capture_output=True, text=True)
    return (done.stdout or done.stderr).strip()[:200]


def launcher_pid() -> int:
    ps = ("Get-CimInstance Win32_Process | ? { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*"
          + str(RUN) + "*' } | Select-Object -First 1 -ExpandProperty ProcessId")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else 0


def stop_run_processes() -> str:
    ps = ("$pids = @(Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.ProcessId -ne " + str(os.getpid())
          + " -and $_.Name -ne 'powershell.exe' -and -not ($_.CommandLine -like '*menu_e2e.py*') -and $_.CommandLine -like '*"
          + str(RUN) + "*' } | % { $_.ProcessId }); "
          "$pids | Sort-Object -Unique | % { taskkill /T /F /PID $_ | Out-Null; \"stopped $_\" }")
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout.strip()


def main() -> None:
    for path in (RUN / "cfg", RUN / "ws", RUN / "claude-config", RUN / "codex-home", SHOTS):
        path.mkdir(parents=True, exist_ok=True)
    (RUN / "cfg" / "config.json").write_text(json.dumps({"language": "en"}), encoding="utf-8")
    script = RUN / "launch.cmd"
    script.write_text("\r\n".join([
        "@echo off",
        *[f"for /f \"delims==\" %%v in ('set {prefix} 2^>nul') do set \"%%v=\"" for prefix in
          ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE", "CODEX_", "OPENAI_")],
        f"set \"CIEL_RUNTIME_CONFIG_DIR={RUN / 'cfg'}\"",
        f"set \"CIEL_RUNTIME_ROUTER_PORT={free_port()}\"",
        "set \"CIEL_RUNTIME_TEST_ISOLATED=1\"",
        f"set \"CLAUDE_CONFIG_DIR={RUN / 'claude-config'}\"",
        f"set \"CODEX_HOME={RUN / 'codex-home'}\"",
        f"cd /d \"{RUN / 'ws'}\"",
        f"python \"{ROOT / 'ciel_runtime.py'}\" cli --ca-menu --ca-no-update-check --ca-no-self-update-check",
        "echo LAUNCHER-EXIT=%ERRORLEVEL%",
    ]) + "\r\n", encoding="utf-8")
    result: dict = {"run": str(RUN)}
    try:
        subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])
        pid = 0
        end = time.time() + 60
        while time.time() < end and not pid:
            pid = launcher_pid()
            time.sleep(0.5)
        result["launcher_pid"] = pid
        driver = subprocess.Popen([sys.executable, "-X", "utf8", str(HERE / "menu_driver.py"), str(pid), str(RUN), str(EVIDENCE)],
                                  creationflags=0x00000008)
        steps = {"main": "main-menu", "panel": "external-events", "set": "limit-set"}
        seen: set[str] = set()
        end = time.time() + 180
        while time.time() < end and driver.poll() is None:
            for flag, label in steps.items():
                if flag not in seen and (RUN / f"ready-{flag}").exists():
                    seen.add(flag)
                    time.sleep(0.6)
                    result[f"shot_{label}"] = shot(label)
                    (RUN / f"shot-{flag}").write_text("1")
            time.sleep(0.3)
        result["driver_exit"] = driver.poll()
        config = json.loads((RUN / "cfg" / "config.json").read_text(encoding="utf-8"))
        result["saved_runtime_input_retry"] = config.get("runtime_input_retry")
    finally:
        result["cleanup"] = stop_run_processes()
    (EVIDENCE / "menu-e2e.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
