"""Retain only selected fixture evidence from one completed installed CLI run."""
from pathlib import Path
import hashlib
import json
import sys
from PIL import Image
ROOT = Path(__file__).resolve().parents[8]
OUT = Path(__file__).resolve().parent
run = Path(sys.argv[1]).resolve()
allowed = ROOT / "docs/journal/2026/10/29/features/sse-gate-control/evidence/scripts"
assert run.is_relative_to(allowed) and run.name.startswith("run-installed-gate-")
record = json.loads((run / "result.json").read_text(encoding="utf-8"))
assert record["status"] == "gate_slash_local_verified"
assert record["live_unchanged"] and record["foreground"]["owned_hits"] == 0
assert len(record["requests"]) == 1
selected = OUT / "installed-cli"
selected.mkdir()
for name in ("result.json", "gate-off.png", "gate-status.png", "gate-on.png", "screen-gate-off.txt", "screen-gate-status.txt", "screen-gate-on.txt"):
    path = run / name
    assert path.exists(), name
    (selected / name).write_bytes(path.read_bytes())
shots = []
for path in selected.glob("*.png"):
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        shots.append({"file": path.name, "width": image.width, "height": image.height, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(OUT / "installed-screenshots.json").write_text(json.dumps(shots, indent=2), encoding="utf-8")
print(json.dumps({"status": record["status"], "upstream_requests": len(record["requests"]), "foreground": record["foreground"], "screenshots": shots}, indent=2))
