"""E2E: a Synology target added and used through the menu only, against a local DSM stand-in.

The stand-in (127.0.0.1, plain HTTP) answers the File Station API calls the target makes
(SYNO.API.Info query, SYNO.API.Auth login/logout, SYNO.FileStation.Upload/Download/List/Delete)
and records them. Isolated config/home/work folder on G:. The test password is a value made
up for this stand-in. e2e_synology_menu_steps.py (detached) types into the menu console.

  1 Add a target… -> synology, address http://127.0.0.1:PORT, account, folder, password (hidden), test, use
  2 local is switched off, so the NAS is the only target
  3 Back up now (first) -> Back up now (second: nothing new uploaded)
  4 Verify the newest snapshot; Restore the first one after notes.md was changed
  5 nas-e2e row -> password (changed on the stand-in too) -> test
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = Path(os.environ.get("E2E_REPO") or r"C:\Users\djlov\ciel-runtime")
BASE = Path(r"G:\ciel-e2e\synology")
shutil.rmtree(BASE, ignore_errors=True)
CFG, WS, HOME = BASE / "cfg", BASE / "ws", BASE / "home"
RUN = HERE / "e2e-1-menu"
shutil.rmtree(RUN, ignore_errors=True)
for d in (CFG, WS, HOME, RUN):
    d.mkdir(parents=True, exist_ok=True)
LOG = open(RUN / "driver.log", "w", encoding="utf-8")
STATE = {"password": "e2e-stand-in-pass-1", "files": {}, "sessions": set(), "calls": []}


def log(m: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


API_INFO = {name: {"minVersion": 2 if name == "SYNO.FileStation.Upload" else 1, "maxVersion": 7 if name == "SYNO.API.Auth" else 2,
                   "path": "entry.cgi"}
            for name in ("SYNO.API.Auth", "SYNO.FileStation.List", "SYNO.FileStation.Download", "SYNO.FileStation.Upload",
                         "SYNO.FileStation.Delete", "SYNO.FileStation.CreateFolder")}


class Dsm(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, data: bytes, ctype: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, data=None):
        self._send(json.dumps({"success": True, "data": data or {}}).encode())

    def _fail(self, code):
        self._send(json.dumps({"success": False, "error": {"code": code}}).encode())

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/webapi/query.cgi":
            return self._send(json.dumps({"success": True, "data": API_INFO}).encode())
        self.handle_api(dict(urllib.parse.parse_qsl(query)))

    def do_POST(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            message = BytesParser(policy=HTTP).parsebytes(b"content-type: " + ctype.encode() + b"\r\n\r\n" + body)
            for part in message.iter_parts():
                if part.get_filename():
                    params["_upload"] = (part.get_filename(), part.get_payload(decode=True))
                else:
                    params[part.get_param("name", header="content-disposition")] = part.get_content().strip()
        else:
            params.update(dict(urllib.parse.parse_qsl(body.decode())))
        self.handle_api(params)

    def handle_api(self, p):
        api, method = p.get("api"), p.get("method")
        STATE["calls"].append(f"{api}.{method}")
        if api == "SYNO.API.Auth" and method == "login":
            if p.get("account") != "ciel-backup" or p.get("passwd") != STATE["password"]:
                return self._fail(400)
            sid = f"sid{len(STATE['calls'])}"
            STATE["sessions"].add(sid)
            return self._ok({"sid": sid})
        if p.get("_sid") not in STATE["sessions"]:
            return self._fail(119)
        files = STATE["files"]
        if api == "SYNO.API.Auth":
            STATE["sessions"].discard(p["_sid"])
            return self._ok()
        if api == "SYNO.FileStation.Upload":
            name, data = p["_upload"]
            files[f"{p['path']}/{name}"] = data
            return self._ok()
        if api == "SYNO.FileStation.Download":
            path = json.loads(p["path"])[0]
            return self._send(files[path], "application/octet-stream") if path in files else self._fail(408)
        if api == "SYNO.FileStation.List":
            folder = p["folder_path"].rstrip("/") + "/"
            names = {}
            for path in files:
                if path.startswith(folder):
                    head, slash, _ = path[len(folder):].partition("/")
                    names[head] = names.get(head, False) or bool(slash)
            if not names:
                return self._fail(408)
            rows = [{"name": n, "isdir": d} for n, d in sorted(names.items())]
            offset = int(p.get("offset", 0))
            return self._ok({"files": rows[offset:offset + 1000], "total": len(rows)})
        if api == "SYNO.FileStation.Delete":
            path = json.loads(p["path"])[0]
            if path not in files:
                return self._fail(408)
            del files[path]
            return self._ok()
        return self._fail(102)


server = ThreadingHTTPServer(("127.0.0.1", 0), Dsm)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
(WS / "notes.md").write_text("plan v1\n", encoding="utf-8")
(WS / "src").mkdir()
(WS / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
(CFG / "config.json").write_text(json.dumps({"current_provider": "vllm", "language": "en", "providers": {
    "vllm": {"base_url": "http://127.0.0.1:9", "api_key": "dummy", "current_model": "m"}}}), encoding="utf-8")
(RUN / "port.txt").write_text(str(PORT))
log(f"DSM stand-in http://127.0.0.1:{PORT}")

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


def change_password() -> None:
    # Step 5: the NAS password changes; the steps then store the new one from the menu.
    while not (RUN / "change-password").exists():
        time.sleep(0.5)
    STATE["password"] = "e2e-stand-in-pass-2"
    log("stand-in password changed")


threading.Thread(target=change_password, daemon=True).start()
steps = subprocess.run([sys.executable, str(HERE / "e2e_synology_menu_steps.py"), str(pid), str(RUN)], creationflags=0x00000008, timeout=900)
log(f"steps exit {steps.returncode}")
log("steps log:\n" + (RUN / "steps.log").read_text(encoding="utf-8"))
settings = json.loads((CFG / "session-backup.json").read_text(encoding="utf-8"))
log(f"session-backup.json: {json.dumps(settings)}")
log(f"password in session-backup.json: {'e2e-stand-in-pass' in (CFG / 'session-backup.json').read_text(encoding='utf-8')}")
for f in (CFG / "backup-credentials").glob("*"):
    log(f"credential file {f.name}: {f.stat().st_size} bytes, plaintext inside: {b'e2e-stand-in-pass' in f.read_bytes()}")
log(f"NAS files: {len(STATE['files'])}; snapshots: {sorted(k for k in STATE['files'] if '/snapshots/' in k)}")
from collections import Counter  # noqa: E402
log(f"NAS calls: {dict(Counter(STATE['calls']))}")
log(f"notes.md after restore: {(WS / 'notes.md').read_text(encoding='utf-8')!r}")
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.ProcessId -ne $PID -and $_.Name -ne 'powershell.exe' -and ($_.CommandLine -like '*"
                    + str(RUN) + "*' -or $_.CommandLine -like '*" + str(CFG) + "*') } | % { taskkill /T /F /PID $_.ProcessId | Out-Null; \"killed $($_.ProcessId)\" }"],
                   capture_output=True, text=True)
log("teardown: " + r.stdout.strip().replace("\n", " "))
log("done")
