"""Wait for exact registry identity; acquire digest-verified package in a fresh directory."""
from pathlib import Path
import json
import subprocess
import tempfile
import threading
import time
import urllib.request
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
COMMIT = "037c55fc252641c41d8058db66fa055e9f43dfdf"
end = time.monotonic() + 1000
while True:
    with urllib.request.urlopen("https://registry.npmjs.org/-/package/@one-ciel-ai%2Fciel-runtime/dist-tags", timeout=60) as response:
        version = json.load(response)["nightly"]
    if version.endswith(".037c55f"):
        break
    if time.monotonic() >= end:
        raise RuntimeError("exact nightly tag unavailable; no installation attempted")
    threading.Event().wait(15)
payload = Path(tempfile.mkdtemp(prefix="ciel-published-037c55f-"))
subprocess.run(["python", "-I", str(ROOT / "docs/journal/2026/10/27/releases/work-mode-switch-nightly/evidence/scripts/acquire_published.py"), COMMIT, str(payload), version], check=True)
(OUT / "published.json").write_bytes((payload / "published.json").read_bytes())
(OUT / "payload-location.json").write_text(json.dumps({"path": str(payload)}, indent=2), encoding="utf-8")
print("VERIFIED PAYLOAD", payload, flush=True)
