"""Measure wait-boost sidecar and receipt compatibility in a real 57dbfb3 process.

The child imports only the supplied immutable 57dbfb3 extract. All files are
created under a new temporary directory. The result contains structural facts
and hashes only; no tokens, host identities, or released wait text are emitted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

beta_root = Path(sys.argv[1]).resolve()
out_path = Path(sys.argv[2]).resolve()
work = Path(tempfile.mkdtemp(prefix="wait-boost-old-roundtrip-"))
config = work / "config"
config.mkdir()

DRIVER = r'''
from __future__ import annotations
import hashlib, json, sys
from pathlib import Path

root, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from ciel_runtime_support import work_plan_tree as tree
from ciel_runtime_support.work_plan_store import WorkPlanStore
from ciel_runtime_support import work_parallel as parallel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate(row, version):
    return {"item_id": row["item_id"], "priority": row["priority"],
            "created_at": row["created_at"], "approved": True,
            "focused": row["focused"], "dependencies_done": True,
            "plan_version": version, "usage": 0.05, "role": "worker", "mode": "fork"}


def root_and_leaf(doc):
    root_row = next(row for row in doc["nodes"] if row["parent_id"] is None)
    leaf_row = next(row for row in doc["nodes"] if row["parent_id"] == root_row["id"])
    return root_row, leaf_row


scope = "claude:wait-boost-probe"
receipt_id = "a" * 64
wait_digest = "b" * 64
result = {"beta_root": str(root), "checks": {}, "observations": {}}

# Scenario A: new writer has committed an exact release receipt and boost sidecar.
board_path = work / "work-plan.json"
board = WorkPlanStore(board_path)
doc = board.read()
doc, root_id = board.transaction(lambda d: tree.set_plan(d, {
    "title": "Boost root", "priority": "P2", "children": [
        {"title": "Released item", "waiting_on": "", "children": []}
    ]}, None, now=1000.0), expected_version=doc["version"])
root_row, leaf_row = root_and_leaf(doc)
recovery = {"item_id": leaf_row["id"], "root_id": root_id,
            "root_created_at": root_row["created_at"],
            "release_receipt": receipt_id, "release_at": 1001.0,
            "wait_digest": wait_digest, "parked_lease_id": None}

def add_receipt(d):
    d.setdefault("side_decisions", {})[receipt_id] = {
        "decision_digest": "c" * 64,
        "applied_at": 1001.0,
        "item_id": leaf_row["id"],
        "recap": "bounded probe",
        "result": {"action": "update", "item_id": leaf_row["id"],
                   "wait_boost": dict(recovery)},
    }

doc, _ = board.transaction(add_receipt, expected_version=doc["version"])
sidecar = work / "work-parallel-wait-boost-v1.json"
key = scope + "\\u0000" + leaf_row["id"]
boost_row = {"scope": scope, "item_id": leaf_row["id"], "root_id": root_id,
             "root_created_at": root_row["created_at"],
             "release_receipt": receipt_id, "release_at": 1001.0,
             "wait_digest": wait_digest, "state": "boosted",
             "parked_lease_id": None, "attempt_id": None, "attempt_kind": None,
             "ledger_expected_version": None, "lease_id": None,
             "outcome": None, "consumed_at": None, "updated_at": 1001.0}
sidecar.write_bytes(json.dumps({"schema": 1, "version": 1, "rows": {key: boost_row}},
                               sort_keys=True, separators=(",", ":")).encode())
initial_sidecar_hash = sha(sidecar)
hashes = {"initial": initial_sidecar_hash}

# Real old board reads/writes preserve arbitrary result fields exactly.
read_receipt = board.read()["side_decisions"][receipt_id]["result"]["wait_boost"]
doc, _ = board.transaction(lambda d: tree.update_node(d, leaf_row["id"], progress="old progress", now=1002.0),
                           expected_version=doc["version"])
hashes["board_progress"] = sha(sidecar)
doc, _ = board.transaction(lambda d: tree.update_node(d, root_id, priority="P3", now=1003.0),
                           expected_version=doc["version"])
hashes["board_priority"] = sha(sidecar)
doc, _ = board.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, root_id),
                           expected_version=doc["version"])
hashes["board_execute"] = sha(sidecar)
written_receipt = board.read()["side_decisions"][receipt_id]["result"]["wait_boost"]
result["observations"]["receipt"] = {
    "read_equal": read_receipt == recovery,
    "after_old_writes_equal": written_receipt == recovery,
    "keys": sorted(written_receipt),
}

# Real old candidate ordering and reserve dispatch the boosted item. A second
# same-scope/item reserve is fenced, leaving one exact active match.
ledger_path = work / "work-parallel.json"
ledger = parallel.WorkParallelStore(ledger_path)
runnable = tree.runnable_leaves(board.read())
state, lease_id = ledger.reserve([candidate(row, board.read()["version"]) for row in runnable],
                                 scope=scope, expected_version=0, now=1004.0)
hashes["old_boost_reserve"] = sha(sidecar)
try:
    ledger.reserve([candidate(row, board.read()["version"]) for row in runnable],
                   scope=scope, expected_version=state["version"], now=1005.0)
    second = "unexpected_success"
except Exception as error:
    second = type(error).__name__ + ": " + str(error)
hashes["old_duplicate_attempt"] = sha(sidecar)
active = [row for row in ledger.read()["leases"].values()
          if row["scope"] == scope and row["item_id"] == leaf_row["id"]
          and row["state"] in ("reserved", "running", "waiting_input", "submitted", "orphaned")]
# This is the new reconciler's exact uniqueness predicate projected over old data:
# board root identity still matches the receipt and exactly one active execution-family lease matches scope/item.
board_now = board.read()
by_id = {row["id"]: row for row in board_now["nodes"]}
identity_ok = (leaf_row["id"] in by_id and root_id in by_id
               and by_id[root_id]["created_at"] == recovery["root_created_at"])
result["observations"]["old_dispatch_first"] = {
    "reserved_item_is_boost": ledger.read()["leases"][lease_id]["item_id"] == leaf_row["id"],
    "second_outcome": second,
    "exact_active_matches": len(active) if identity_ok else 0,
    "reconcile_outcome": "consumed" if identity_ok and len(active) == 1 else "fail_closed",
}

# Old ordinary ledger operations do not touch the new file.
state, _ = ledger.configure({"paused": True}, capability_ready=True,
                            expected_version=ledger.read()["version"])
hashes["ledger_pause_true"] = sha(sidecar)
state, _ = ledger.configure({"paused": False}, capability_ready=True,
                            expected_version=state["version"])
hashes["ledger_pause_false"] = sha(sidecar)
state, ordinary_id = ledger.reserve([{"item_id": "ordinary-separate", "priority": "P4",
    "created_at": 2000.0, "approved": True, "focused": False, "dependencies_done": True,
    "plan_version": 1, "usage": 0.05, "role": "worker", "mode": "fork"}],
    scope="claude:ordinary", expected_version=state["version"], now=2000.0)
hashes["ledger_ordinary_reserve"] = sha(sidecar)
state, _ = ledger.release_unbound(ordinary_id, proof="bounded compatibility probe",
                                  expected_version=state["version"])
hashes["ledger_ordinary_release"] = sha(sidecar)

# Scenario B: the old scheduler ignores the sidecar. Its own runnable order has a
# newer ordinary leaf first, so it reserves ordinary work before the boosted row.
race_board = WorkPlanStore(work / "race-work-plan.json")
race = race_board.read()
race, race_root = race_board.transaction(lambda d: tree.set_plan(d, {
    "title": "Race root", "priority": "P2", "children": [
        {"title": "Older boosted", "waiting_on": "", "children": []}
    ]}, None, now=3000.0), expected_version=race["version"])
boosted_id = next(row["id"] for row in race["nodes"] if row["parent_id"] == race_root)
race, ordinary_id = race_board.transaction(
    lambda d: tree.add_node(d, race_root, "Newer ordinary", now=3001.0, within_scope=True),
    expected_version=race["version"])
race, _ = race_board.transaction(lambda d: tree.set_phase(d, tree.PHASE_EXECUTING, race_root),
                                 expected_version=race["version"])
old_order = tree.runnable_leaves(race)
race_ledger = parallel.WorkParallelStore(work / "race-work-parallel.json")
race_state, race_lease = race_ledger.reserve([candidate(row, race["version"]) for row in old_order],
                                             scope=scope, expected_version=0, now=3002.0)
hashes["old_ordinary_first"] = sha(sidecar)
reserved_race_item = race_state["leases"][race_lease]["item_id"]
result["observations"]["mixed_version_order"] = {
    "old_runnable_order": [row["item_id"] for row in old_order],
    "boosted_item": boosted_id,
    "ordinary_item": ordinary_id,
    "old_reserved_ordinary_first": reserved_race_item == ordinary_id,
}

result["sidecar_hashes"] = hashes
result["checks"] = {
    "receipt_preserved_exactly": read_receipt == recovery and written_receipt == recovery,
    "sidecar_byte_identical": all(value == initial_sidecar_hash for value in hashes.values()),
    "old_dispatch_first_unique_match": result["observations"]["old_dispatch_first"]["exact_active_matches"] == 1,
    "old_dispatch_first_converges_consumed": result["observations"]["old_dispatch_first"]["reconcile_outcome"] == "consumed",
    "old_duplicate_fence_no_second_lease": len(active) == 1 and second != "unexpected_success",
    "mixed_version_can_reserve_ordinary_first": result["observations"]["mixed_version_order"]["old_reserved_ordinary_first"],
}
print(json.dumps(result, sort_keys=True))
'''

env = {key: value for key, value in os.environ.items()
       if not key.upper().startswith(("CIEL_RUNTIME_", "CLAUDE", "ANTHROPIC", "CODEX", "OPENAI", "MAX_MCP"))}
env.update({"CIEL_RUNTIME_CONFIG_DIR": str(config), "CIEL_RUNTIME_TEST_ISOLATED": "1",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
proc = subprocess.run([sys.executable, "-I", "-B", "-c", DRIVER, str(beta_root), str(work)],
                      env=env, cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=180)
outer = {"beta": "57dbfb3", "returncode": proc.returncode,
         "stderr_tail": proc.stderr[-2000:], "temp_dir": str(work)}
if proc.returncode == 0:
    outer["measured"] = json.loads(proc.stdout.strip().splitlines()[-1])
out_path.write_text(json.dumps(outer, indent=2, sort_keys=True), encoding="utf-8")
print(json.dumps({"returncode": proc.returncode,
                  "checks": outer.get("measured", {}).get("checks", {}),
                  "observations": outer.get("measured", {}).get("observations", {})},
                 indent=2, sort_keys=True))
if proc.returncode:
    raise SystemExit(proc.returncode)
