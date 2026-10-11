"""Launch Claude Code or Codex through Ciel on CielAiRouter (isolated config) and capture the bottom of the screen."""
from __future__ import annotations

import json, os, shutil, socket, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVID = Path(r"C:\Users\djlov\ciel-runtime\docs\journal\2026\10\10\features\session-restart\evidence")
sys.path.insert(0, str(EVID))
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
MODE, MODEL, TAG = sys.argv[1], sys.argv[2], sys.argv[3]
WAIT = float(os.environ.get("E2E_WAIT") or 35)
RUN = Path(os.environ.get("E2E_OUT") or HERE / "runs") / f"{MODE}-{TAG}"
shutil.rmtree(RUN, ignore_errors=True); RUN.mkdir(parents=True)
SCRATCH = Path(r"G:\ciel-e2e\model-name") / f"{MODE}-{TAG}"
shutil.rmtree(SCRATCH, ignore_errors=True)
CFG, WS, CODEX_HOME = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home"
for d in (CFG, WS, CODEX_HOME):
    d.mkdir(parents=True, exist_ok=True)
(WS / "README.md").write_text("model name e2e workspace\n")
TITLE = f"CIELE2E-MODELNAME-{MODE}-{TAG}"

def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

src = json.load(open(Path(os.environ["APPDATA"]) / "ciel-runtime" / "config.json", encoding="utf-8"))["providers"]["cielairouter"]
prov = {k: src[k] for k in ("api_key", "base_url", "native_compat", "request_timeout_ms", "stream_enabled", "stream_word_chunking") if k in src}
prov["current_model"] = MODEL
(CFG / "config.json").write_text(json.dumps({"current_provider": "cielairouter", "language": "en",
                                              "providers": {"cielairouter": prov}}, indent=2), encoding="utf-8")
(CFG / "log-level").write_text("INFO")
(CODEX_HOME / "config.toml").write_text(f'[projects."{str(WS).replace(chr(92), chr(92) * 2)}"]\ntrust_level = "trusted"\n', encoding="utf-8")
ROUTER, APPSRV = free_port(), free_port()
EXTRA = ""
if MODE == "claude" and os.environ.get("E2E_OWN_STATUSLINE"):
    # Claude Code reads the user's ~/.claude statusLine (the installed script); point it at the one
    # this checkout writes into the isolated config instead.
    (SCRATCH / "statusline-settings.json").write_text(json.dumps({"statusLine": {
        "type": "command", "command": f'"{sys.executable}" "{CFG / "ciel-runtime-statusline.py"}"', "padding": 0,
        "refreshInterval": 1000}}), encoding="utf-8")
    EXTRA = f' --settings "{SCRATCH / "statusline-settings.json"}"'
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "ANTHROPIC_", "CLAUDE_", "CODEX_", "OPENAI_"))
script = SCRATCH / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    f'cd /d "{WS}"\npython "{REPO / "ciel_runtime.py"}" cli --ca-runtime {MODE} --ca-no-update-check --ca-no-self-update-check{EXTRA}\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
subprocess.Popen(["wt.exe", "-w", "new", "--title", TITLE, "--suppressApplicationTitle", "cmd", "/k", str(script)])

def launcher_pid() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*' -and "
                          "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
                          + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0

deadline = time.time() + 60
pid = 0
while time.time() < deadline and not pid:
    pid = launcher_pid(); time.sleep(1)
print("launcher pid", pid, "router", ROUTER, flush=True)
watcher = subprocess.Popen([sys.executable, str(HERE / "watch.py"), str(pid), str(RUN)], creationflags=0x00000008)
time.sleep(WAIT)
out = RUN / "screen.png"
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EVID / "shot_title.ps1"), TITLE, str(out)],
                   capture_output=True, text=True)
print(r.stdout.strip(), r.stderr.strip()[:300])
(RUN / "stop").write_text("")
time.sleep(1)
print("title", TITLE, "scratch", SCRATCH, "run", RUN)
