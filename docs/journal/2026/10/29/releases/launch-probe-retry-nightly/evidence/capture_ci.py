"""Capture exact-commit workflow identity and concise test counts."""
from pathlib import Path
import json
import subprocess
import sys
OUT = Path(__file__).resolve().parent
commit, run_id, name = sys.argv[1:4]
assert name in ("ci", "publish")
data = subprocess.check_output(["gh", "run", "view", run_id, "--repo", "OneCielAI/ciel-runtime", "--json", "status,conclusion,headSha,jobs,url"], encoding="utf-8")
record = json.loads(data)
assert record["headSha"] == commit
(OUT / (name + ".json")).write_text(data, encoding="utf-8")
print(name, record["status"], record["conclusion"])
assert record["status"] == "completed" and record["conclusion"] == "success", record
log = subprocess.check_output(["gh", "run", "view", run_id, "--repo", "OneCielAI/ciel-runtime", "--log"], encoding="utf-8")
lines = [line for line in log.splitlines() if any(key in line for key in ("Ran ", "OK (", "FAILED (", "Nightly version:"))]
(OUT / (name + "-summary.txt")).write_text(chr(10).join(lines), encoding="utf-8")
print(chr(10).join(lines))
