"""run_backup with a child whose summary is ~70 KB: old code stalls until its time limit, new code returns.

usage: python run_backup_big_summary.py CODE_DIR SCRATCH_DIR
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

code = Path(sys.argv[1])
sys.path.insert(0, str(code))
from ciel_runtime_support.session_backup_service import run_backup  # noqa: E402

script = ("import json, sys; sys.stdout.write(json.dumps({'id': 'snap-big', 'targets': ['local'], "
          "'left_out': {'folders': ['x' * 100] * 700}}))")  # a ~70 KB summary


def popen(command, **kwargs):
    return subprocess.Popen([sys.executable, "-c", script], stdout=kwargs["stdout"], stderr=kwargs["stderr"], text=True)


with tempfile.TemporaryDirectory(dir=sys.argv[2]) as directory:
    config, cwd = Path(directory) / "ciel", Path(directory) / "work"
    config.mkdir()
    cwd.mkdir()
    started = time.time()
    result = run_backup(config, cwd, "menu", popen=popen, poll_seconds=0.1, timeout=15)
    print(json.dumps({"code": str(code), "ok": result["ok"], "id": result.get("id"),
                      "output": str(result.get("output") or "")[:80], "seconds": round(time.time() - started, 1)}))
