"""Owned probe: codex in a new console with only stdout redirected (stdin stays the console)."""
import os, subprocess, sys, tempfile, time
from pathlib import Path
CODEX = sys.argv[1]
home = Path(tempfile.mkdtemp(prefix="codex-tty2-", dir="C:/Windows/Temp/conhost-probe/codex-tty"))
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
env.update(CODEX_HOME=str(home), HOME=str(home), USERPROFILE=str(home), HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1", NO_PROXY="127.0.0.1,localhost")
si = subprocess.STARTUPINFO(); si.dwFlags = subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 7
line = f'"{CODEX}" > "{home / "out.txt"}" 2> "{home / "err.txt"}"'
p = subprocess.Popen("cmd.exe /d /s /c \"" + line + "\"", env=env, cwd=str(home), creationflags=subprocess.CREATE_NEW_CONSOLE,
                     startupinfo=si, stdin=None, stdout=None, stderr=None, close_fds=True)
try:
    p.wait(timeout=20)
except subprocess.TimeoutExpired:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    print("TIMEOUT killed")
print("exit", p.returncode, "out", (home / "out.txt").read_bytes()[:200], "err", (home / "err.txt").read_bytes()[:300])
