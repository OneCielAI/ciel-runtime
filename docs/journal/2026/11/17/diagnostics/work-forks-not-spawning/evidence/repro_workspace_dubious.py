"""Reproduce Celly's stuck Work reservations: workspace preparation fails with
git's 'detected dubious ownership' (the error Celly's sandbox account got for
G:/ciel-bridge-service), for the P0 lane and the ordinary lane.

  python repro_workspace_dubious.py <tree>
"""
import json, sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, str(Path(sys.argv[1]) / "tests"))
import importlib
m = importlib.import_module("ciel_runtime_support.work_parallel_mcp")
parallel = importlib.import_module("ciel_runtime_support.work_parallel")
p0 = importlib.import_module("ciel_runtime_support.work_parallel_p0")
ws = importlib.import_module("ciel_runtime_support.work_parallel_workspace")

SECRET = "p0-event-secret-" * 3
SCOPE = "claude:celly-repro"
DUBIOUS = ("Git verification failed: fatal: detected dubious ownership in repository at "
           "'G:/ciel-bridge-service'")


class Board:
    def __init__(self, approved):
        self.doc = {"version": 1, "phase": "executing" if approved else "planning", "active_root": "root",
                    "focus_ids": [], "nodes": [
            {"id": "root", "parent_id": None, "title": "goal", "status": "pending", "note": "", "progress": "",
             "evidence": "", "waiting_on": "", "ref": "", "urgent": False, "updated_at": 1.0, "created_at": 1.0,
             "phase": "planning", "priority": "P1"},
            {"id": "item", "parent_id": "root", "title": "item", "status": "pending", "note": "", "progress": "",
             "evidence": "", "waiting_on": "", "ref": "", "urgent": False, "updated_at": 1.0, "created_at": 1.0}]}

    def read(self):
        return self.doc


def run(lane):
    with TemporaryDirectory() as raw:
        tmp = Path(raw)
        store = parallel.WorkParallelStore(tmp / "work-parallel.json")
        board = Board(approved=False)
        config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        p0.p0_store(store).activate_root(SCOPE, "root", 1.0, set_by="user", expected_version=0, now=1.0)
        errors = []

        def call(event, host, **args):
            args.setdefault("expected_version", store.read()["version"])
            args["event"] = event
            args["_host"] = {"token": SECRET, **host}
            with patch.object(m, "ledger", return_value=store):
                return json.loads(m.run("work_parallel_event", args, board, SCOPE, config, None))

        def failing_prepare(*_a, **_k):
            raise ws.WorkspaceError(DUBIOUS)

        attempts = 3
        with patch.object(ws, "prepare", side_effect=failing_prepare):
            for _ in range(attempts):
                try:
                    call("p0_reserve_next", {"fork": True, "workspace_repo": str(tmp)}, role="worker")
                except Exception as error:
                    errors.append(str(error)[:160])
        leases = store.read()["leases"]
        runs = m.coordination(store).read()["runs"]
        return {"lane": lane, "attempts": attempts, "errors": errors,
                "leases": {k[:8]: v["state"] for k, v in leases.items()},
                "coordination_runs": {k[:8]: v["state"] for k, v in runs.items()}}


print(json.dumps(run("p0"), indent=1))


def run_ordinary():
    tests = importlib.import_module("test_work_parallel_mcp")
    with TemporaryDirectory() as raw:
        tmp = Path(raw)
        store, board, config, run_tool = tests._make_env(tmp)

        def failing_prepare(*_a, **_k):
            raise ws.WorkspaceError(DUBIOUS)

        errors = []
        attempts = 3
        with patch.object(ws, "prepare", side_effect=failing_prepare):
            for _ in range(attempts):
                try:
                    run_tool("event", {"fork": True, "workspace_repo": str(tmp)}, event="reserve_next")
                except Exception as error:
                    errors.append(str(error)[:200])
        leases = store.read()["leases"]
        runs = m.coordination(store).read()["runs"]
        return {"lane": "ordinary", "attempts": attempts, "errors": errors,
                "leases": {k[:8]: v["state"] for k, v in leases.items()},
                "coordination_runs": {k[:8]: v["state"] for k, v in runs.items()}}


print(json.dumps(run_ordinary(), indent=1))
