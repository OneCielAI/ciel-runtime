"""Read and compare exact installed global package against verified registry bytes."""
from pathlib import Path
import hashlib
import json
import sys

package, global_dir, output = map(Path, sys.argv[1:4])
assert (global_dir / "package.json").is_file()
actual = json.loads((global_dir / "package.json").read_text(encoding="utf-8"))
expected = json.loads((package / "package.json").read_text(encoding="utf-8"))
assert actual["name"] == expected["name"] and actual["version"] == expected["version"]
mismatch = []
count = 0
for p in package.rglob("*"):
    if not p.is_file():
        continue
    rel = p.relative_to(package)
    count += 1
    target = global_dir / rel
    if not target.is_file() or hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
        mismatch.append(str(rel))
record = {"files": count, "mismatch": mismatch, "version": actual["version"]}
output.write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
raise SystemExit(1 if mismatch else 0)
