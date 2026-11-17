"""Behavior probe for Celly's Work fork failure, runnable on any build.

Uses only APIs present before and after the fix. Git is told every repository
is owned by another account (GIT_TEST_ASSUME_DIFFERENT_OWNER=1), the router's
launch folder is the repository, and three reservations are attempted on the
ordinary lane and on the P0 lane. It prints what each build leaves behind.

    python foreign_owner_probe.py <tree> [launched-elsewhere]

With ``launched-elsewhere`` the router's launch folder is another folder, so
Git's refusal stands on every build; the probe then shows what each build
leaves reserved after the refusal.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from ciel_runtime_support import runtime_paths  # noqa: E402
from ciel_runtime_support import work_parallel_mcp as m  # noqa: E402
from ciel_runtime_support.work_parallel import WorkParallelStore  # noqa: E402
from ciel_runtime_support.work_parallel_p0 import p0_store  # noqa: E402

SECRET = "probe-host-secret-" * 3
SCOPE = "claude:probe"
ELSEWHERE = sys.argv[2:] == ["launched-elsewhere"]


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def board(phase):
    nodes = [{"id": "root", "parent_id": None, "title": "goal", "status": "pending", "note": "", "progress": "",
              "evidence": "", "waiting_on": "", "ref": "", "urgent": False, "updated_at": 1.0,
              "created_at": 1.0, "phase": phase, "priority": "P1"},
             {"id": "item", "parent_id": "root", "title": "item", "status": "pending", "note": "", "progress": "",
              "evidence": "", "waiting_on": "", "ref": "", "urgent": False, "updated_at": 1.0, "created_at": 1.0}]
    doc = {"version": 1, "phase": phase, "active_root": "root", "focus_ids": [], "nodes": nodes}
    return type("Board", (), {"read": lambda self: doc})()


def lane(name):
    with TemporaryDirectory() as raw:
        tmp = Path(raw).resolve()
        repo = tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "-c", "user.name=f", "-c", "user.email=f@example.invalid", "commit", "-q",
            "--allow-empty", "-m", "base")
        store = WorkParallelStore(tmp / "work-parallel.json")
        config = {"work_parallel_host": {"token": SECRET, "capability_ready": True}}
        plan = board("planning" if name == "p0" else "executing")

        def call(tool, args):
            args = {**args, "expected_version": store.read()["version"]}
            with patch.object(m, "ledger", return_value=store):
                return json.loads(m.run(tool, args, plan, SCOPE, config, None))
        if name == "p0":
            p0_store(store).activate_root(SCOPE, "root", 1.0, set_by="user", expected_version=0, now=1.0)
        else:
            call("work_parallel_configure", {"changes": {"enabled": True}, "_host": {"token": SECRET}})
        event = "p0_reserve_next" if name == "p0" else "reserve"
        errors, started = [], 0
        with patch.object(runtime_paths, "CONFIG_DIR", tmp / "cfg"), \
                patch.object(runtime_paths, "ROUTER_WORKSPACE", str(tmp / "elsewhere" if ELSEWHERE else repo)), \
                patch.dict(os.environ, {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}):
            for _ in range(3):
                try:
                    reserved = call("work_parallel_event", {
                        "event": event, "item_id": "item", "priority": "P1", "role": "worker",
                        "_host": {"token": SECRET, "fork": True, "workspace_repo": str(repo)}})
                    started += 1
                    path = (reserved.get("workspace") or {}).get("path")
                    errors.append("reserved with workspace " + ("present" if path and Path(path).is_dir()
                                                                else "missing"))
                    break
                except Exception as error:  # noqa: BLE001 - the probe reports every outcome
                    text = str(error).replace(str(tmp), "<tmp>").replace(tmp.as_posix(), "<tmp>")
                    errors.append(text[:160])
        intents = p0_store(store).read()["intents"].values()
        return {"lane": name, "attempts": len(errors), "outcomes": errors,
                "leases": sorted(v["state"] for v in store.read()["leases"].values()),
                "coordination_runs": sorted(v["state"] for v in m.coordination(store).read()["runs"].values()),
                "p0_intents": sorted(row["state"] for row in intents)}


for name in ("ordinary", "p0"):
    print(json.dumps(lane(name), indent=1))
