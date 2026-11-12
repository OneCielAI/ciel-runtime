"""Offline: apply the worktree's WorkParallelFinalizationStore.projection to the
final finalization state recorded by run 213818 (temp copy; read-only on evidence)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore  # noqa: E402

run = Path(sys.argv[2])
fin = json.loads((run / "07-final-state.json").read_text(encoding="utf-8"))["finalization"]
path = Path(tempfile.mkdtemp(prefix="projection-213818-")) / "work-parallel-finalization.json"
path.write_text(json.dumps(fin), encoding="utf-8")
store = WorkParallelFinalizationStore(path)
for scope in sorted({row["scope"] for row in fin["reviews"].values()}):
    out = store.projection(scope)
    print("scope", scope, "pending rows", [(r["item_id"], r["lease_id"], r["state"]) for r in out["pending"]])
