"""Wait for exact acquisition + successful jobs, then verify local install and actual CLI."""
from pathlib import Path
import json
import subprocess
import sys
import threading
import time
OUT = Path(__file__).resolve().parent
COMMIT, CI_RUN, PUBLISH_RUN = sys.argv[1:4]
assert len(COMMIT) == 40 and all(c in "0123456789abcdef" for c in COMMIT)
end = time.monotonic() + 1100
while True:
    ready = (OUT / "payload-location.json").exists()
    results = []
    for run in (CI_RUN, PUBLISH_RUN):
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
for run, name in ((CI_RUN, "ci"), (PUBLISH_RUN, "publish")):
    subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "capture_ci.py"), COMMIT, run, name], check=True)
subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "finish_install.py"), COMMIT], check=True)
subprocess.run([sys.executable, "-I", "-X", "utf8", str(OUT / "installed_cli.py"), COMMIT], check=True)
print("EXACT INSTALLED LAUNCH CLI VERIFIED", flush=True)
