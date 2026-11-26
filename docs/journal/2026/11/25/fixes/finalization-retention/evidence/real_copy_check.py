"""Read-only real-file check of finalized-row retention (2026-11-25).

Copies a host's work-parallel-finalization.json and work-parallel-stranded-v1.json
into a temp directory, then adds one synthetic pending review through the
tree-under-test store. Prints the size before and after, rows removed by state,
and that every undecided/rework row and every row named as a replacement is
still present. The live files are only read (copied), never written.

usage: python real_copy_check.py <tree under test> <host config dir>
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

tree = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2])
sys.path.insert(0, str(tree))
from ciel_runtime_support import work_parallel_finalization as fin  # noqa: E402

scratch = Path(tempfile.mkdtemp(prefix="fin-copy-"))
try:
    for name in ("work-parallel-finalization.json", "work-parallel-stranded-v1.json"):
        if (source / name).exists():
            shutil.copyfile(source / name, scratch / name)
    before_raw = (scratch / "work-parallel-finalization.json").read_bytes()
    before = json.loads(before_raw)
    store = fin.finalization_store(type("Ledger", (), {"path": scratch / "work-parallel.json"})())
    template = next(row for row in before["reviews"].values() if row["workspace"] is None) \
        if any(row["workspace"] is None for row in before["reviews"].values()) else None
    facts = {"lease_id": "real-copy-check", "scope": "claude:real-copy-check", "item_id": "n0", "root_id": "n0",
             "priority": "P1", "plan_version": 1, "generation": 1, "artifact": "a" * 64,
             "completion_receipt": "b" * 64, "fingerprint": "c" * 64, "summary": "s" * 2000,
             "coverage": [], "verification": "v" * 2000, "workspace": None}
    error = None
    try:
        store.pending(facts, expected_version=store.read()["version"])
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        error = type(exc).__name__ + ": " + str(exc)
    after_raw = (scratch / "work-parallel-finalization.json").read_bytes()
    after = json.loads(after_raw)
    removed = [key for key in before["reviews"] if key not in after["reviews"]]
    kept_live = all(key in after["reviews"] for key, row in before["reviews"].items() if row["state"] != "finalized")
    named = {row["replacement_lease_id"] for row in before["reviews"].values() if row["replacement_lease_id"]}
    stranded = fin._stranded_replacements(scratch / "work-parallel-stranded-v1.json") or set()
    kept_named = all(key in after["reviews"] for key in (named | stranded) if key in before["reviews"])
    newest_removed = max((before["reviews"][key]["updated_at"] for key in removed), default=None)
    oldest_kept = min((row["updated_at"] for row in after["reviews"].values() if row["state"] == "finalized"),
                      default=None)
    stamp = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None
    print(json.dumps({
        "error": error, "before_bytes": len(before_raw), "after_bytes": len(after_raw),
        "max_bytes": fin.MAX_BYTES, "trim_target": fin.HISTORY_TRIM_TARGET,
        "rows_before": len(before["reviews"]), "rows_after": len(after["reviews"]),
        "removed": len(removed), "removed_states": sorted({before["reviews"][k]["state"] for k in removed}),
        "new_row_written": "real-copy-check" in after["reviews"],
        "all_non_finalized_kept": kept_live, "all_replacements_kept": kept_named,
        "newest_removed_updated_at": stamp(newest_removed), "oldest_kept_finalized_updated_at": stamp(oldest_kept),
        "version": [before["version"], after["version"]],
    }, indent=1))
finally:
    shutil.rmtree(scratch, ignore_errors=True)
