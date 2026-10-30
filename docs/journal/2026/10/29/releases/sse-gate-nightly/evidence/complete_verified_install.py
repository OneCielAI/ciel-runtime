"""Wait for exact acquisition + successful jobs, then verify local install and actual CLI."""
from pathlib import Path
import json
import subprocess
import sys
import threading
import time
OUT = Path(__file__).resolve().parent
COMMIT = "907726516449764c26169a54c5d441cb0dd9be94"
end = time.monotonic() + 1100
while True:
    ready = (OUT / "payload-location.json").exists()
    results = []
    for run in ("40987470637", "40987470633"):
        value = json.loads(subprocess.check_output(["gh", "run", "view", run, "--repo", "OneCielAI/ciel-runtime", "--json", "status,conclusion,headSha"], encoding="utf-8"))
        assert value["headSha"] == COMMIT
        if value["status"] == "completed" and value["conclusion"] != "success":
            raise RuntimeError("release job failed: " + run + " " + value["conclusion"])
        results.append(value["status"] == "completed" and value["conclusion"] == "success")
    if ready and all(results):
        break
    if time.monotonic() >= end:
        raise RuntimeError("release prerequisites timed out; installation not attempted")
    threading.Event().wait(20)
for run, name in (("40987470637", "ci"), ("40987470633", "publish")):
    subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "capture_ci.py"), COMMIT, run, name], check=True)
subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "finish_install.py"), COMMIT], check=True)
subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "installed_cli.py"), COMMIT], check=True)
print("SSE EXACT INSTALLED CLI VERIFIED", flush=True)
