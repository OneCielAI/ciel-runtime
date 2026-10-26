"""Reproduce: a child of a lease-holding process cannot import runtime_paths.

Usage: python probe.py <repo-root> <out-dir>
Temp HOME/APPDATA/config/workspace; no live router or config is touched
(only read-only GET /health on localhost ports during the scan).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for key in list(os.environ):
    if key.startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI")):
        os.environ.pop(key)
temp = Path(tempfile.mkdtemp(prefix="lease-probe-"))
for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
    os.environ[name] = str(temp)
os.environ["CIEL_RUNTIME_CONFIG_DIR"] = str(temp / "runtime")
ws = temp / "ws"
ws.mkdir()
os.chdir(ws)
sys.path.insert(0, str(root))
report = {"repo": str(root), "workspace": str(ws)}

# Parent = the menu process: importing runtime_paths selects a port and,
# with no router alive for this workspace, records a launch lease for itself.
from ciel_runtime_support import runtime_paths  # noqa: E402
registry = json.loads(runtime_paths.WORKSPACE_ROUTER_REGISTRY_PATH.read_text(encoding="utf-8"))
record = next(iter(registry["workspaces"].values()))
report["parent_pid"] = os.getpid()
report["parent_port"] = runtime_paths.ROUTER_PORT
report["lease_pid"] = record.get("launch_lease", {}).get("pid")

# 1) Minimal child: a fresh interpreter importing runtime_paths.
child = subprocess.run([sys.executable, "-X", "utf8", "-c",
                        "import sys;sys.path.insert(0,sys.argv[1]);"
                        "from ciel_runtime_support import runtime_paths as r;print(r.ROUTER_PORT)", str(root)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ws)
report["child_import"] = {"exit": child.returncode, "stdout": child.stdout.strip(),
                          "stderr_tail": child.stderr.strip().splitlines()[-1:] }

# 2) The real menu backup path: session_backup_service.run_backup spawns
#    `ciel_runtime.py cli backup create` exactly as menu item 18 does.
from ciel_runtime_support.session_backup_service import run_backup  # noqa: E402
result = run_backup(runtime_paths.CONFIG_DIR, ws, "menu", timeout=120)
report["run_backup"] = {"ok": result.get("ok"), "output_tail": str(result.get("output") or "")[-400:]}
(out / "probe-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
