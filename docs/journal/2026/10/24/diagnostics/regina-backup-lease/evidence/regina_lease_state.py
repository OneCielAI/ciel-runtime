# Read-only snapshot of Regina's router lease state. Writes nothing.
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
home = Path.home()
red = lambda s: re.sub(r"(sk-|key=|token=)[A-Za-z0-9._-]+", r"\1<redacted>", s)
pkg = home / ".npm-global/lib/node_modules/@one-ciel-ai/ciel-runtime"
meta = json.loads((pkg / "package.json").read_text())
sel = pkg / "ciel_runtime_support/workspace_router_selection.py"
print("NOW", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
print("PACKAGE", meta.get("version"), meta.get("gitHead"))
print("FILE workspace_router_selection.py sha256", hashlib.sha256(sel.read_bytes()).hexdigest())
cfg = home / ".config/ciel-runtime"
reg_path = cfg / "workspace-router-ports.json"
st = reg_path.stat()
print("REGISTRY", reg_path, "mtime", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(st.st_mtime)))
reg = json.loads(reg_path.read_text())
def proc(pid):
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        cwd = os.readlink(f"/proc/{pid}/cwd")
        start = os.stat(f"/proc/{pid}").st_mtime
        return {"alive": True, "args": red(args)[:220], "cwd": cwd,
                "proc_mtime": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start))}
    except FileNotFoundError:
        return {"alive": False}
    except PermissionError as exc:
        return {"alive": True, "error": str(exc)}
for key, rec in (reg.get("workspaces") or {}).items():
    lease = rec.get("launch_lease") or {}
    port = rec.get("port")
    health = None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            h = json.loads(r.read())
            health = {"pid": h.get("pid"), "version": h.get("version"), "workspace": h.get("workspace")}
    except Exception as exc:
        health = f"unreachable: {type(exc).__name__}"
    print("WORKSPACE", key, json.dumps({k: rec.get(k) for k in ("workspace", "port", "updated_at", "last_used_at") if k in rec}))
    print("  LEASE", json.dumps(lease))
    if lease.get("pid"):
        print("  LEASE_PROC", json.dumps(proc(lease["pid"])))
    print("  HEALTH", json.dumps(health))
print("PS ciel-runtime processes")
for d in Path("/proc").iterdir():
    if not d.name.isdigit():
        continue
    try:
        if d.stat().st_uid != os.getuid():
            continue
        args = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except Exception:
        continue
    if "ciel" in args and ("ciel_runtime" in args or "ciel-runtime" in args):
        p = proc(int(d.name))
        print(" ", d.name, json.dumps(p))
for name in ("session-backups.json", "session-backup-state.json", "session-backup.json"):
    p = cfg / name
    if p.exists():
        print("BACKUP_STATE_FILE", name, "mtime", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(p.stat().st_mtime)))
