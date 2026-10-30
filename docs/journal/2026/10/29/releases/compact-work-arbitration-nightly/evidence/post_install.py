"""Read-only health and unrelated-work preservation proof after installation."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request
repo, out = map(Path, sys.argv[1:3])
before = json.loads((out / "live-before-install.json").read_text(encoding="utf-8"))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
after = {}
for port in before:
    with opener.open("http://127.0.0.1:" + port + "/health", timeout=10) as response:
        health = json.load(response)
    after[port] = {key: health.get(key) for key in ("pid", "version", "source_fingerprint")}
digests = json.loads((out / "preserved-dirty-digests.json").read_text(encoding="utf-8"))
changed = [name for name, digest in digests.items()
           if hashlib.sha256((repo / name).read_bytes()).hexdigest() != digest]
record = {"live_before": before, "live_after": after, "live_unchanged": before == after,
          "unrelated_files_compared": len(digests), "unrelated_changed": changed}
(out / "post-install.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(0 if before == after and not changed else 1)
