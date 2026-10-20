"""Every chunk a snapshot names exists in a local target's blob store (existence only, no reading).

usage: python check_chunks.py TARGET_DIR SNAPSHOT_JSON [PREVIOUS_SNAPSHOT_JSON]
With a previous snapshot: also counts entries whose chunks are the same in both.
"""
import json
import sys
from pathlib import Path

target, path = Path(sys.argv[1]), Path(sys.argv[2])
manifest = json.loads(path.read_text(encoding="utf-8"))
digests = {digest for entry in manifest["entries"] for digest in entry.get("chunks") or []}
missing = [digest for digest in digests if not (target / "blobs" / digest[:2] / digest).is_file()]
print(f"{path.name}: {len(manifest['entries'])} entries, {len(digests)} distinct chunks, missing {len(missing)}")
print("stats", manifest.get("stats"))
if len(sys.argv) > 3:
    old = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
    before = {(e["root"], e["path"]): e.get("chunks") for e in old["entries"]}
    same = sum(1 for e in manifest["entries"] if before.get((e["root"], e["path"])) == e.get("chunks"))
    print(f"entries with the same chunks as {Path(sys.argv[3]).name}: {same} of {len(manifest['entries'])}")
