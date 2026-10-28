"""Replace only one live router with a verified snapshot (explicitly approved scope).

Usage: router_handover_launch.py <new_home> <port> <old_pid> <owner_pid> <out_json>
Copies the old router's environment (values never printed), swaps only home/script
paths to the new snapshot, starts router_handover_serve.py detached, and records
before/after identities. Clients, registry, queues and histories are not touched.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import psutil

new_home, port, old_pid, owner_pid, out = Path(sys.argv[1]).resolve(), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(p):
    try:
        with opener.open(f"http://127.0.0.1:{p}/health", timeout=5) as response:
            h = json.load(response)
        return {k: h.get(k) for k in ("ok", "pid", "version", "source_fingerprint", "active_client_pids", "config_dir", "workspace")}
    except Exception as exc:
        return {"error": type(exc).__name__}


old = psutil.Process(old_pid)
cmd = old.cmdline()
assert cmd[-1] == "serve" and cmd[-2].endswith("ciel_runtime.py"), "unexpected old router command"
old_home = str(Path(cmd[-2]).parent)
env = old.environ()
before = {"target": health(port), "other": health(9505), "owner_running": psutil.pid_exists(owner_pid)}
assert before["target"].get("pid") == old_pid and before["target"].get("active_client_pids") == [owner_pid]
swapped = []
for key in ("CIEL_RUNTIME_HOME", "CIEL_RUNTIME_SELECTED_HOME", "CIEL_RUNTIME_REGISTERED_HOME", "CIEL_RUNTIME_SCRIPT"):
    if env.get(key) in (old_home, str(Path(old_home) / "ciel_runtime.py")):
        env[key] = env[key].replace(old_home, str(new_home))
        swapped.append(key)
instance = Path(before["target"]["config_dir"])
registry = instance.parent.parent / "workspace-router-ports.json"
log = Path(os.environ["TEMP"]) / f"router-handover-{port}-{int(time.time())}.log"
flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
with log.open("ab", buffering=0) as stream:
    child = subprocess.Popen([old.exe(), str(Path(__file__).with_name("router_handover_serve.py")), str(new_home), str(port),
                              str(old_pid), str(owner_pid), str(instance), str(registry)],
                             cwd=old.cwd(), env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, creationflags=flags)
after = None
deadline = time.time() + 60
while time.time() < deadline:
    time.sleep(0.5)
    h = health(port)
    if h.get("pid") == child.pid and h.get("ok"):
        after = h
        break
    if child.poll() is not None:
        break
record = {"new_home": str(new_home), "old_home": old_home, "swapped_env_keys": swapped, "env_key_count": len(env),
          "before": before, "child_pid": child.pid, "child_exit": child.poll(), "after": after,
          "after_other": health(9505), "owner_running_after": psutil.pid_exists(owner_pid),
          "old_running_after": psutil.pid_exists(old_pid), "log": str(log)}
out.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if after else 1)
