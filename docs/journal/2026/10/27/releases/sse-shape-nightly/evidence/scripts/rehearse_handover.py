"""Rehearse router_handover_launch.py on an owned isolated router before touching a live one.

Usage: rehearse_handover.py <old_home> <new_home> <out_dir>
Owner process (stands in for the launcher) spawns the old router as its child with
an isolated config/workspace/port, registers itself as the router client, then the
real handover script replaces only that router. Egress goes to a refusing sentinel.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

old_home, new_home, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]).resolve()
out.mkdir(parents=True, exist_ok=False)
here = Path(__file__).resolve().parent
scratch = Path(os.environ["TEMP"]) / f"handover-rehearsal-{os.getpid()}"
cfg, ws, home = scratch / "cfg", scratch / "ws", scratch / "home"
for d in (cfg, ws, home / "AppData/Roaming", home / "AppData/Local/Temp"):
    d.mkdir(parents=True, exist_ok=True)
egress = []


class Sentinel(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def deny(self):
        egress.append(self.command + " " + self.path)
        self.send_response(403)
        self.send_header("content-length", "0")
        self.end_headers()
    do_CONNECT = do_GET = do_POST = deny


def free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


sentinel = ThreadingHTTPServer(("127.0.0.1", free()), Sentinel)
threading.Thread(target=sentinel.serve_forever, daemon=True).start()
port = free()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI"))}
proxy = f"http://127.0.0.1:{sentinel.server_port}"
env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData/Roaming"), "LOCALAPPDATA": str(home / "AppData/Local"),
            "TEMP": str(home / "AppData/Local/Temp"), "TMP": str(home / "AppData/Local/Temp"), "CIEL_RUNTIME_CONFIG_DIR": str(cfg),
            "HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy, "NO_PROXY": "127.0.0.1,localhost",
            "CIEL_RUNTIME_LOG_LEVEL": "INFO"})
(cfg / "config.json").write_text(json.dumps({"current_provider": "cielairouter", "language": "en", "work_mode": False,
                                             "providers": {"cielairouter": {"base_url": proxy, "api_key": "owned-dummy", "gateway_selection": "off"}}}), encoding="utf-8")
python = r"C:\Users\djlov\AppData\Local\Python\pythoncore-3.14-64\python.exe"  # the live router interpreter
owner_code = ("import os,subprocess,sys,time;e=dict(os.environ);e['CIEL_RUNTIME_MANAGED_ROUTER']='1';e['CIEL_RUNTIME_ROUTER_OWNER_PID']=str(os.getpid());"
              "e['CIEL_RUNTIME_ROUTER_PORT']=sys.argv[2];e['CIEL_RUNTIME_LAUNCH_CWD']=os.getcwd();e['CIEL_RUNTIME_HOME']=sys.argv[1];"
              "e['CIEL_RUNTIME_SCRIPT']=os.path.join(sys.argv[1],'ciel_runtime.py');"
              "subprocess.Popen([sys.executable,os.path.join(sys.argv[1],'ciel_runtime.py'),'serve'],env=e,stdout=open(sys.argv[3],'ab'),stderr=subprocess.STDOUT);time.sleep(3600)")
owner = subprocess.Popen([python, "-c", owner_code, str(old_home), str(port), str(out / "old-router-out.txt")], cwd=ws, env=env)
record = {"port": port, "owner": owner.pid, "scratch": str(scratch)}
try:
    deadline = time.time() + 40
    while time.time() < deadline and not (health() or {}).get("ok"):
        time.sleep(0.5)
    h = health()
    assert h and h.get("ok"), "old router did not start"
    old_pid = h["pid"]
    clients = Path(h["config_dir"]) / "router-clients"
    clients.mkdir(exist_ok=True)
    (clients / f"{owner.pid}.json").write_text(json.dumps({"pid": owner.pid, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                         "router_port": port, "workspace": str(ws)}), encoding="utf-8")
    deadline = time.time() + 10
    while time.time() < deadline and (health() or {}).get("active_client_pids") != [owner.pid]:
        time.sleep(0.5)
    record["before"] = {k: health().get(k) for k in ("pid", "version", "source_fingerprint", "active_client_pids")}
    run = subprocess.run([sys.executable, "-X", "utf8", "-I", str(here / "router_handover_launch.py"), str(new_home), str(port), str(old_pid),
                          str(owner.pid), str(out / "handover.json")], capture_output=True, text=True, encoding="utf-8", timeout=120)
    record["handover_exit"] = run.returncode
    record["handover"] = json.loads((out / "handover.json").read_text(encoding="utf-8")) if (out / "handover.json").exists() else run.stderr[-2000:]
    time.sleep(8)  # survives beyond the launcher; owner-held client keeps it alive
    a = health() or {}
    record["after_8s"] = {k: a.get(k) for k in ("pid", "version", "source_fingerprint", "active_client_pids")}
    record["old_running"] = psutil.pid_exists(old_pid)
    record["checks"] = {"handover_ok": run.returncode == 0, "new_pid": a.get("pid") not in (None, old_pid),
                        "new_version": a.get("version") != record["before"]["version"], "client_kept": a.get("active_client_pids") == [owner.pid],
                        "old_gone": not record["old_running"], "single_listener": len([c for c in psutil.net_connections("tcp") if c.laddr and c.laddr.port == port and c.status == "LISTEN"]) == 1}
finally:
    h = health()
    owner.kill()
    for pid in {(h or {}).get("pid"), record.get("handover", {}).get("child_pid") if isinstance(record.get("handover"), dict) else None}:
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    sentinel.shutdown()
    record["egress"] = egress
    (out / "result.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
raise SystemExit(0 if all(record.get("checks", {"x": False}).values()) else 1)
