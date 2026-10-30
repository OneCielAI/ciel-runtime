"""Retain one owned successful delayed-catalog CLI run."""
from pathlib import Path
import hashlib
import json
import sys
from PIL import Image
OUT = Path(__file__).resolve().parent
run = Path(sys.argv[1]).resolve()
label = sys.argv[2]
assert label in ("source-cli", "installed-cli")
assert run.is_relative_to(OUT / "scripts")
record = json.loads((run / "result.json").read_text(encoding="utf-8"))
assert record["status"] == "gate_slash_local_verified"
assert record["live_unchanged"] and record["foreground"]["owned_hits"] == 0
assert len(record["requests"]) == 1
catalog = record["catalog"]
assert len(catalog) >= 2, catalog
assert any(row.get("response_at", 0) - row["started"] >= 10.9 for row in catalog), catalog
selected = OUT / label
selected.mkdir()
for name in ("result.json", "01-gate-default-on.png", "gate-off.png", "gate-status.png", "gate-on.png", "screen-gate-off.txt", "screen-gate-status.txt", "screen-gate-on.txt"):
    path = run / name
    assert path.exists(), name
    (selected / name).write_bytes(path.read_bytes())
shots = []
for path in selected.glob("*.png"):
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        shots.append({"file": path.name, "width": image.width, "height": image.height, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(OUT / (label + "-screenshots.json")).write_text(json.dumps(shots, indent=2), encoding="utf-8")
print(json.dumps({"status": record["status"], "catalog_requests": len(catalog), "upstream_requests": len(record["requests"]), "foreground": record["foreground"]}, indent=2))
