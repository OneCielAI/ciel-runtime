"""Reproduce: Ciel codex-routed + Codex 0.162.1, '/model' switch, and which model each request carries.

usage: python astra_repro.py
Isolated: scratch CIEL_RUNTIME_CONFIG_DIR and CODEX_HOME on G:, Codex 0.162.1 from G:\\ciel-e2e\\astra\\codex162,
the routed upstream moved to a local stub (CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM). The stub answers like the
ChatGPT Codex backend: GET /models with the 0.162.1 bundled list, POST /responses rejects gpt-6-astra with the
remote's 400 text and streams 'OK' for any other model. Fake ChatGPT auth (unsigned JWTs, nothing real).
A detached driver (astra_drive.py) types into the new terminal window and keeps each screen.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\djlov\ciel-runtime")
SCRATCH = Path(r"G:\ciel-e2e\astra")
RUN = HERE / ("run-" + (sys.argv[1] if len(sys.argv) > 1 else "1"))
RUNTIME = sys.argv[2] if len(sys.argv) > 2 else "codex"
shutil.rmtree(RUN, ignore_errors=True)
RUN.mkdir(parents=True)
CFG, WS, CODEX_HOME = SCRATCH / "cfg", SCRATCH / "ws", SCRATCH / "codex-home"
for d in (CFG, WS, CODEX_HOME):
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
CODEX_BIN = SCRATCH / "codex162" / "node_modules" / ".bin"
MODELS = json.loads((SCRATCH / "models-0.162.1.json").read_text(encoding="utf-8"))
REJECT = "gpt-6-astra"
REQS: list[dict] = []
LOCK = threading.Lock()


def log(text: str) -> None:
    line = time.strftime("%H:%M:%S ") + text
    print(line, flush=True)
    with open(RUN / "harness.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def record(entry: dict) -> None:
    with LOCK:
        entry["t"] = time.strftime("%H:%M:%S")
        REQS.append(entry)
        (RUN / "stub-requests.json").write_text(json.dumps(REQS, indent=1, ensure_ascii=False), encoding="utf-8")


def last_user_text(body: dict) -> str:
    for item in reversed(body.get("input") or []):
        if isinstance(item, dict) and item.get("role") == "user":
            parts = item.get("content") or []
            if isinstance(parts, str):
                return parts[-120:]
            return " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))[-120:]
    return ""


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        record({"method": "GET", "path": self.path, "auth": bool(self.headers.get("authorization"))})
        if self.path.split("?")[0].endswith("/models"):
            self._send(200, json.dumps(MODELS).encode())
        else:
            self._send(404, b'{"detail":"not found"}')

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        model = str(body.get("model") or "")
        path = self.path.split("?")[0]
        record({"method": "POST", "path": path, "model": model, "input_items": len(body.get("input") or []),
                "last_user": last_user_text(body), "auth": bool(self.headers.get("authorization"))})
        if model == REJECT:
            detail = f"The '{REJECT}' model is not supported when using Codex with a ChatGPT account."
            self._send(400, json.dumps({"detail": detail}).encode())
            return
        if path.endswith("/compact"):
            self._send(200, json.dumps({"output": []}).encode())
            return
        rid, text = f"resp_{len(REQS)}", f"OK from {model}"
        msg = {"id": f"msg_{len(REQS)}", "type": "message", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": text, "annotations": []}]}
        usage = {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 3,
                 "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 13}
        events = [
            ("response.created", {"type": "response.created", "response": {"id": rid, "status": "in_progress", "model": model}}),
            ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                            "item": {**msg, "status": "in_progress", "content": []}}),
            ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": msg["id"], "output_index": 0,
                                            "content_index": 0, "delta": text}),
            ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": msg}),
            ("response.completed", {"type": "response.completed",
                                    "response": {"id": rid, "status": "completed", "model": model, "output": [msg], "usage": usage}}),
        ]
        data = "".join(f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events).encode()
        self._send(200, data, "text/event-stream")


def jwt(claims: dict) -> str:
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(claims)}.e2e"


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
STUB = stub.server_address[1]
threading.Thread(target=stub.serve_forever, daemon=True).start()
ROUTER, APPSRV = free_port(), free_port()

exp = int(time.time()) + 10 * 86400
auth_claims = {"chatgpt_account_id": "acct-e2e", "chatgpt_plan_type": "pro", "chatgpt_user_id": "user-e2e", "user_id": "user-e2e"}
# Codex 0.162.1 reads the account from chatgpt_base_url (https only) at startup, so a fake token cannot
# start it. REAL_AUTH=1 copies this PC's own Codex sign-in; model requests still reach only the stub
# (Ciel routed upstream). The copy is removed afterwards, and a refreshed token is written back.
REAL_AUTH = os.environ.get("REAL_AUTH") == "1"
OWN_AUTH = Path.home() / ".codex" / "auth.json"
if REAL_AUTH:
    shutil.copyfile(OWN_AUTH, CODEX_HOME / "auth.json")
else:
  (CODEX_HOME / "auth.json").write_text(json.dumps({
    "auth_mode": "chatgpt", "OPENAI_API_KEY": None,
    "tokens": {"id_token": jwt({"email": "e2e@example.invalid", "exp": exp, "https://api.openai.com/auth": auth_claims}),
               "access_token": jwt({"exp": exp, "https://api.openai.com/auth": auth_claims}),
               "refresh_token": "e2e-refresh", "account_id": "acct-e2e"},
    "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())}), encoding="utf-8")
(CODEX_HOME / "config.toml").write_text(f'[projects.\'{WS}\']\ntrust_level = "trusted"\n', encoding="utf-8")
(CFG / "config.json").write_text(json.dumps({
    "current_provider": "codex", "language": "en",
    "providers": {"codex": {"route_through_router": True, "current_model": ""}},
}), encoding="utf-8")

title = f"CIELE2E-ASTRA-{int(time.time())}"
clear = "".join(f'for /f "delims==" %%v in (\'set {p} 2^>nul\') do set "%%v="\n' for p in ("CIEL_RUNTIME_", "CLAUDE_", "CODEX_"))
script = RUN / "launch.cmd"
script.write_text(
    "@echo off\n" + clear + "set CLAUDECODE=\n"
    f'set "CIEL_RUNTIME_CONFIG_DIR={CFG}"\nset "CIEL_RUNTIME_ROUTER_PORT={ROUTER}"\nset "CIEL_RUNTIME_TEST_ISOLATED=1"\n'
    f'set "CODEX_HOME={CODEX_HOME}"\nset "CIEL_RUNTIME_CODEX_ROUTED_UPSTREAM=http://127.0.0.1:{STUB}"\n'
    f'set "CIEL_RUNTIME_STARTUP_AUTO_ANSWER=1"\nset "PATH={CODEX_BIN};%PATH%"\n'
    f'set "CIEL_RUNTIME_CODEX_APP_SERVER_LISTEN=ws://127.0.0.1:{APPSRV}"\n'
    f'cd /d "{WS}"\ncall codex --version\n'
    f'python "{REPO / "ciel_runtime.py"}" cli --ca-runtime {RUNTIME} --ca-no-update-check --ca-no-self-update-check\n'
    "echo LAUNCHER-EXIT=%ERRORLEVEL%\n", encoding="utf-8")
log(f"runtime={RUNTIME} stub={STUB} router={ROUTER} appserver={APPSRV} title={title}")
subprocess.Popen(["wt.exe", "-w", "new", "--title", title, "--suppressApplicationTitle", "cmd", "/k", str(script)])


def launcher_pid() -> int:
    command = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ? { $_.CommandLine -like '*ciel_runtime.py*cli*' -and "
               "$_.ParentProcessId -in @(Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | ? { $_.CommandLine -like '*"
               + str(script) + "*' } | % { $_.ProcessId }) } | % { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else 0


pid = 0
end = time.time() + 60
while not pid and time.time() < end:
    pid = launcher_pid()
    time.sleep(1)
log(f"launcher pid {pid}")
try:
    if pid:
        driver = subprocess.Popen([sys.executable, str(HERE / "astra_drive.py"), str(pid), title, str(RUN), str(ROUTER)], creationflags=0x00000008)
        driver.wait(timeout=600)
    log("requests at the stub:")
    for r in REQS:
        log("  " + json.dumps(r, ensure_ascii=False))
    router_logs = list(CFG.rglob("router*.log"))
    lines = []
    for p in router_logs:
        lines += [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                  if any(k in l for k in ("codex_responses", "model=", "startup_prompt", "400"))]
    (RUN / "router-lines.txt").write_text("\n".join(lines[-300:]), encoding="utf-8")
finally:
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and $_.CommandLine -like '*" + str(script) + "*' } | % { taskkill /T /F /PID $_.ProcessId | Out-Null }"])
    if REAL_AUTH:
        copy = CODEX_HOME / "auth.json"
        own_refresh = json.loads(OWN_AUTH.read_text(encoding="utf-8"))["tokens"]["refresh_token"]
        copy_refresh = json.loads(copy.read_text(encoding="utf-8"))["tokens"]["refresh_token"]
        if copy_refresh != own_refresh:
            shutil.copyfile(copy, OWN_AUTH)
            log("the copy was refreshed: written back to ~/.codex/auth.json")
        else:
            log("token not refreshed during the run")
        copy.unlink()
    log("done")
