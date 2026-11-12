"""Real Work backend for work_mod_real_backend_harness.mjs.

One JSON request per stdin line, one JSON reply per stdout line. Every
work_parallel_* call goes through the real work_parallel_mcp.run with the
real ledger, coordination and finalization stores under the temporary
CIEL_RUNTIME_CONFIG_DIR. Nothing here scripts a backend answer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")
sys.path.insert(0, os.environ["WORK_SOURCE"])
from ciel_runtime_support import work_parallel_mcp as parallel  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.work_parallel_host import host_token  # noqa: E402
from ciel_runtime_support.work_plan_store import WorkPlanStore  # noqa: E402

IDENTITY = "claude:S1"
ROOT = Path(os.environ["WORK_REAL_ROOT"])
ROOT.mkdir(parents=True, exist_ok=True)
BOARD = WorkPlanStore(ROOT / "work-plan.json")


def config():
    return {"work_mode": True, "work_parallel_host": {"token": host_token(), "capability_ready": True}}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def run(tool, args, host=None):
    payload = dict(args)
    payload["_host"] = {"token": host_token(), **(payload.get("_host") or {}), **(host or {})}
    payload.setdefault("session_id", IDENTITY)
    if tool == "work_parallel_event" and "expected_version" not in payload:
        payload["expected_version"] = parallel.ledger().read()["version"]
    return json.loads(parallel.run(tool, payload, BOARD, IDENTITY, config(), None))


def backend(tool, args):
    if tool == "work_status":
        doc = BOARD.read()
        value = tree.snapshot(doc, enabled=True, approval_ask=False)
        value["auto_continue"] = False
        if args.get("detail"):
            value.update(nodes=doc["nodes"], root_id=doc.get("active_root"))
        return value
    if tool == "work_side_status":
        return {"enabled": False, "side_inbox_receipts": []}
    if tool == "work_side_claim":
        return {"claim": None, "settings": {}, "handoff_replies": [], "main_wakes": []}
    if tool == "work_live_settings":
        return {"settings": [], "provider": "fixture"}
    if tool.startswith("work_parallel_"):
        # The mod passes its own _host (token, fork, host_id, agent_name, proof).
        return json.loads(parallel.run(tool, dict(args), BOARD, IDENTITY, config(), None))
    raise ValueError("unsupported bridge tool " + tool)


def init():
    repo = ROOT / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.name", "fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    run("work_parallel_configure", {"changes": {"enabled": True, "paused": False},
                                    "expected_version": parallel.ledger().read()["version"]})
    return {"token": host_token(), "repo": str(repo)}


def seed(title):
    """A goal whose single item has a durable completion and a positive idle."""
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": title, "note": "", "priority": "P1",
        "children": [{"title": title + " item", "note": "", "ref": "", "waiting_on": "", "children": []}]},
        None), expected_version=doc["version"])
    item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    reserve = run("work_parallel_event", {"event": "reserve", "item_id": item, "priority": "P1"},
                  {"fork": True, "workspace_repo": str(ROOT / "repo")})
    run("work_parallel_event", {"event": "bind", "lease_id": reserve["lease_id"]},
        {"host_id": "seed-worker-" + reserve["lease_id"][:8]})
    workspace = Path(reserve["workspace"]["path"])
    (workspace / "result.txt").write_text("result", encoding="utf-8")
    parallel.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                     "event": "completion", "plan_version": BOARD.read()["version"],
                     "artifact": "a" * 64, "evidence": "tests pass",
                     "detail": {"summary": "done", "coverage": [{"item_id": item, "verified": True}],
                                "verification": "tests pass"}}, BOARD, config())
    run("work_parallel_event", {"event": "reconcile", "lease_id": reserve["lease_id"]},
        {"state": "idle", "proof": "native completed"})
    return {"lease_id": reserve["lease_id"], "item_id": item, "workspace": str(workspace)}


def _native_rows(leases, status):
    evidence = parallel.native_evidence(parallel.ledger()).read()["version"]
    return run("work_parallel_event", {"event": "native_observation", "complete": True,
                                       "expected_evidence_version": evidence,
                                       "rows": [{"lease_id": row["lease_id"], "agent_id": row["agent_id"],
                                                 "agent_name": row["agent_id"], "status": status}
                                                for row in leases]})


def seed_restart_pair():
    """Two workers, in the order run-20261111-105012 recorded, up to checkpoint A.

    Worker 1 reports; a native observation still lists both forks running;
    worker 2 reports; worker 1's own turn end reconciles it idle and publishes
    its review; both are then listed idle. The process dies before any wake.
    """
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": "Restart pair", "note": "", "priority": "P1",
        "children": [
            {"title": "Passing result", "note": "", "ref": "", "waiting_on": "", "children": []},
            {"title": "Other result", "note": "", "ref": "", "waiting_on": "", "children": []},
        ]}, None), expected_version=doc["version"])
    items = [n["id"] for n in doc["nodes"] if n["parent_id"] == root]
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    leases = []
    for index, item in enumerate(items):
        reserve = run("work_parallel_event", {"event": "reserve", "item_id": item, "priority": "P1"},
                      {"fork": True, "workspace_repo": str(ROOT / "repo")})
        agent = "restart-worker-" + str(index + 1)
        run("work_parallel_event", {"event": "bind", "lease_id": reserve["lease_id"]},
            {"host_id": agent, "agent_name": agent})
        workspace = Path(reserve["workspace"]["path"])
        (workspace / ("result-" + str(index + 1) + ".txt")).write_text("result", encoding="utf-8")
        leases.append({"lease_id": reserve["lease_id"], "item_id": item, "agent_id": agent,
                       "token": reserve["worker_token"]})

    def complete(row, index):
        parallel.report({"lease_id": row["lease_id"], "worker_token": row["token"],
                         "event": "completion", "plan_version": BOARD.read()["version"],
                         "artifact": str(index + 1) * 64, "evidence": "tests pass",
                         "detail": {"summary": "result " + str(index + 1),
                                    "coverage": [{"item_id": row["item_id"], "verified": True}],
                                    "verification": "tests pass"}}, BOARD, config())
    complete(leases[0], 0)
    _native_rows(leases, "running")
    complete(leases[1], 1)
    run("work_parallel_event", {"event": "reconcile", "lease_id": leases[0]["lease_id"]},
        {"state": "idle", "proof": "Native agent.list status: idle"})
    _native_rows(leases, "idle")
    for row in leases:
        row.pop("token")
    return {"leases": leases, "checkpoint": inspect()}


def seed_running():
    """A bound worker that never reported: no completion receipt exists."""
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": "No receipt", "note": "", "priority": "P1",
        "children": [{"title": "Unreported", "note": "", "ref": "", "waiting_on": "", "children": []}]},
        None), expected_version=doc["version"])
    item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    reserve = run("work_parallel_event", {"event": "reserve", "item_id": item, "priority": "P1"},
                  {"fork": True, "workspace_repo": str(ROOT / "repo")})
    run("work_parallel_event", {"event": "bind", "lease_id": reserve["lease_id"]},
        {"host_id": "unreported-worker", "agent_name": "unreported-worker"})
    return {"lease_id": reserve["lease_id"], "item_id": item, "agent_id": "unreported-worker"}


def _workspace_proof(lease_id):
    from ciel_runtime_support import work_parallel_workspace as ws
    lease = parallel.ledger().read()["leases"][lease_id]
    _, _, manifest = ws._identity(lease["workspace"]["repo"], lease_id, None)
    return json.loads(manifest.read_text(encoding="utf-8"))["idle_proof"]


def seed_proof_mismatch():
    """One submitted, reviewed, terminal result whose two idle proofs disagree.

    The review is published by an ordinary idle reconcile; the workspace proof is
    then durably overwritten with the restart proof. That is the backend state
    review-restart-terminal forced: review exists, run terminal, proofs differ.
    """
    from ciel_runtime_support import work_parallel_workspace as ws
    seeded = seed("Proof mismatch")
    lease_id = seeded["lease_id"]
    lease = parallel.ledger().read()["leases"][lease_id]
    ws.set_activity(lease["workspace"]["repo"], lease_id, lease["host_id"], idle=True,
                    proof=parallel.RESTART_TERMINAL_PROOF)
    return {**seeded, "agent_id": lease["host_id"], "proofs": proofs(lease_id)}


def proofs(lease_id):
    return {"ledger": parallel.ledger().read()["leases"][lease_id]["idle_proof"],
            "workspace": _workspace_proof(lease_id)}


def stores(lease_id=None):
    """Version and SHA256 of the on-disk bytes of every durable Work store."""
    import hashlib

    def raw(path):
        path = Path(path)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    store = parallel.ledger()
    coord = parallel.coordination(store)
    coord_state = coord.read()
    final = parallel.finalization(store)
    native = parallel.native_evidence(store)
    value = {
        "board": {"version": BOARD.read()["version"], "bytes": raw(BOARD.path)},
        "parallel": {"version": store.read()["version"], "bytes": raw(store.path)},
        "coordination": {"version": coord_state["version"], "bytes": raw(coord.path)},
        "receipts": hashlib.sha256(json.dumps(coord_state["receipts"], sort_keys=True).encode()).hexdigest(),
        "finalization": {"version": final.read()["version"], "bytes": raw(final.path)},
        "native": {"version": native.read()["version"], "bytes": raw(native.path)},
    }
    from ciel_runtime_support import work_parallel_workspace as ws
    # Every lease's workspace ownership manifest, so a rebind's replacement
    # manifest is covered as well as the predecessor's.
    manifests = {}
    for key, lease in store.read()["leases"].items():
        if lease.get("workspace") is not None:
            _, _, manifest = ws._identity(lease["workspace"]["repo"], key, None)
            manifests[key] = raw(manifest)
    value["workspaces"] = manifests
    if lease_id in manifests:
        value["workspace"] = manifests[lease_id]
    return value


def measured_tool(tool, args):
    """One bridge call with every store read immediately before and after it.

    The server handles one request at a time, so no other mod call can land
    between the two reads.
    """
    lease_id = args.get("lease_id")
    before = stores(lease_id)
    try:
        value, error = backend(tool, args), None
    except Exception as exc:  # Returned to the mod as the router error.
        value, error = None, str(exc)
    return {"value": value, "error": error, "before": before, "after": stores(lease_id)}


RESUME_ASSIGNMENT_FAULT = {"message": None}


def fault_resume_assignment(message):
    """Make the rework saga's board stage fail after its workspace rebind.

    That stage is the first to record the resume run's coordination assignment.
    A non-crash ValueError there is recorded as ``blocked`` with the
    predecessor still ``submitted`` and its workspace manifest ``rebound``.
    ``None`` clears the fault.
    """
    RESUME_ASSIGNMENT_FAULT["message"] = message
    return {"fault": message}


def _install_resume_assignment_fault():
    from ciel_runtime_support.work_parallel_receipts import WorkCoordinationStore
    original = WorkCoordinationStore.record_assignment

    def record_assignment(self, scope, lease_id, manifest, *, expected_version):
        if RESUME_ASSIGNMENT_FAULT["message"] and manifest.get("role") == "resume":
            raise parallel.WorkParallelError(RESUME_ASSIGNMENT_FAULT["message"])
        return original(self, scope, lease_id, manifest, expected_version=expected_version)
    WorkCoordinationStore.record_assignment = record_assignment


_install_resume_assignment_fault()


def workspace_status(lease_id):
    from ciel_runtime_support import work_parallel_workspace as ws
    lease = parallel.ledger().read()["leases"][lease_id]
    _, _, manifest = ws._identity(lease["workspace"]["repo"], lease_id, None)
    return json.loads(manifest.read_text(encoding="utf-8"))["status"]


def workspace_manifest(lease_id):
    """The ownership manifest fields that show whether a host bound it."""
    from ciel_runtime_support import work_parallel_workspace as ws
    lease = parallel.ledger().read()["leases"][lease_id]
    _, _, manifest = ws._identity(lease["workspace"]["repo"], lease_id, None)
    record = json.loads(manifest.read_text(encoding="utf-8"))
    return {key: record.get(key) for key in ("status", "host_id", "idle", "idle_proof")}


# One concurrent finalization write between _bind_rework_launch's read() and
# its launch_bound(expected_version=...) call. launch_bound is entered only
# after that read, so writing first and then calling the real method is exactly
# the window. The write changes only the source review's updated_at, through the
# real finalization CAS writer, and the resulting file validates.
LAUNCH_BOUND_RACE = {"remaining": 0, "fired": [], "calls": []}


def arm_launch_bound_race(attempts=1):
    attempts = int(attempts)
    if attempts < 0 or attempts > 100:
        raise ValueError("launch_bound race attempts must be 0..100 (0 disarms)")
    LAUNCH_BOUND_RACE.update(remaining=attempts, fired=[], calls=[])
    return {"remaining": attempts}


def _install_launch_bound_race():
    import time
    from ciel_runtime_support.work_parallel_finalization import WorkParallelFinalizationStore
    original = WorkParallelFinalizationStore.launch_bound

    def launch_bound(self, replacement_lease_id, agent_id, agent_name=None, *, expected_version):
        LAUNCH_BOUND_RACE["calls"].append({"lease_id": replacement_lease_id, "expected_version": expected_version})
        if LAUNCH_BOUND_RACE["remaining"]:
            LAUNCH_BOUND_RACE["remaining"] -= 1
            current = self.read()
            source = next(row for row in current["reviews"].values()
                          if row.get("replacement_lease_id") == replacement_lease_id)
            def concurrent_write(state):
                state["reviews"][source["lease_id"]]["updated_at"] = time.time()
                return source["lease_id"]
            self._change(concurrent_write, expected_version=current["version"])
            LAUNCH_BOUND_RACE["fired"].append({"source": source["lease_id"],
                                               "read_version": expected_version,
                                               "after_version": self.read()["version"]})
        return original(self, replacement_lease_id, agent_id, agent_name, expected_version=expected_version)
    WorkParallelFinalizationStore.launch_bound = launch_bound


_install_launch_bound_race()


def force_launch_state(lease_id, state):
    """Rewrite one review's launch as a valid none or spawn_denied row.

    Through the real finalization CAS writer, so the validator must accept it.
    The ledger lease (its host_id) is left untouched: an older build's bind, or
    a denial recorded for a lease the ledger had already bound.
    """
    import time
    reviews = parallel.finalization(parallel.ledger())
    current = reviews.read()
    def apply(doc):
        launch = doc["reviews"][lease_id]["launch"]
        stamp = time.time()
        if state == "none":
            launch.update(state="none", dispatch_name=None, attempt=0, generation=None, agent_id=None,
                          proof=None, host_nonce=None, manual_from=None, launch_ref=None, audit=[],
                          updated_at=stamp)
        elif state == "spawn_denied":
            launch.update(state="spawn_denied", agent_id=None, proof="forced denial for the test",
                          manual_from=None, launch_ref=None, updated_at=stamp)
            launch["audit"] = launch["audit"] + [{
                "kind": "denied", "dispatch_name": launch["dispatch_name"], "attempt": launch["attempt"],
                "observer_nonce": None, "at": stamp, "detail": "forced denial for the test"}]
        else:
            raise ValueError("unsupported forced launch state " + str(state))
        return state
    reviews._change(apply, expected_version=current["version"])
    return {"state": reviews.read()["reviews"][lease_id]["launch"]["state"]}


def report_completion(lease_id, worker_token, label):
    """The resume fork's own completion report, with the token its prompt carried."""
    lease = parallel.ledger().read()["leases"][lease_id]
    return json.loads(parallel.report({
        "lease_id": lease_id, "worker_token": worker_token, "event": "completion",
        "plan_version": lease["plan_version"], "artifact": "c" * 64, "evidence": "fixed " + label,
        "detail": {"summary": "fixed " + label, "coverage": [{"item_id": lease["item_id"], "verified": True}],
                   "verification": "fixed " + label}}, BOARD, config()))


def pause(paused):
    run("work_parallel_configure", {"changes": {"paused": bool(paused)},
                                    "expected_version": parallel.ledger().read()["version"]})
    return {"paused": bool(paused)}


def web(op, body):
    """The real Work REST controller, as the browser console calls it."""
    from ciel_runtime_support.http_response import HttpResponseAdapter
    from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
    controller = WorkHttpController(WorkHttpServices(
        ROOT / "state", config, lambda: IDENTITY, lambda identity: {"session_id": identity, "last_observed": None},
        HttpResponseAdapter.write_json, "fixture",
        parallel_status=lambda identity, board: parallel.project(parallel.ledger(), identity, board),
        parallel_store=parallel.ledger))
    if op == "launches":
        return controller._public_launches(IDENTITY)
    if op == "parallel":
        # The browser's Parallel tab payload, as /parallel builds it.
        return controller._public_parallel(IDENTITY, BOARD.read())
    if op == "resolve":
        return controller._parallel_launch_resolve(IDENTITY, body)
    raise ValueError("unknown web op " + str(op))


def _nonce_digest(value):
    import hashlib
    return None if value is None else hashlib.sha256(str(value).encode()).hexdigest()


def _launch_view(launch):
    """The launch row with every host nonce replaced by its digest."""
    view = dict(launch)
    view["host_nonce"] = _nonce_digest(launch.get("host_nonce"))
    view["audit"] = [dict(row, observer_nonce=_nonce_digest(row.get("observer_nonce")))
                     for row in launch.get("audit") or []]
    return view


def clear_intent_nonce(lease_id):
    """Rewrite one review's launch intent as a legacy (pre-nonce) row."""
    reviews = parallel.finalization(parallel.ledger())
    path = reviews.path
    state = json.loads(path.read_text(encoding="utf-8"))
    launch = state["reviews"][lease_id]["launch"]
    assert launch["state"] == "launch_intent", launch["state"]
    launch["host_nonce"] = None
    tmp = path.with_name(path.name + ".legacy.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)
    reviews.read()
    return {"cleared": True}


def inspect():
    store = parallel.ledger()
    final = parallel.finalization(store).read()
    reviews = final["reviews"]
    leases = store.read()["leases"]
    return {
        "finalization_version": final["version"],
        "reviews": {key: {**{k: row.get(k) for k in ("state", "decision", "wake", "blocked",
                                                     "replacement_lease_id", "stages")},
                          "launch": _launch_view(row.get("launch") or {})}
                    for key, row in reviews.items()},
        "leases": {key: {k: row.get(k) for k in ("state", "role", "host_id", "dispatch_name", "slot",
                                                 "item_id", "report_hash")}
                   for key, row in leases.items()},
        "runs": {key: {k: row.get(k) for k in ("state", "agent_id", "native_status", "exit")}
                 for key, row in parallel.coordination(store).read()["runs"].items()},
        "coordination_archive": [row["run"]["lease_id"]
                                 for row in parallel.coordination(store).read()["archive"]],
        "nodes": {node["id"]: node["status"] for node in BOARD.read()["nodes"]},
    }


def dispatch(request):
    action = request["action"]
    if action == "init":
        return init()
    if action == "seed":
        return seed(request["title"])
    if action == "seed_restart_pair":
        return seed_restart_pair()
    if action == "seed_running":
        return seed_running()
    if action == "seed_proof_mismatch":
        return seed_proof_mismatch()
    if action == "proofs":
        return proofs(request["lease_id"])
    if action == "stores":
        return stores(request.get("lease_id"))
    if action == "measured_tool":
        return measured_tool(request["tool"], request["args"])
    if action == "fault_resume_assignment":
        return fault_resume_assignment(request.get("message"))
    if action == "workspace_status":
        return workspace_status(request["lease_id"])
    if action == "workspace_manifest":
        return workspace_manifest(request["lease_id"])
    if action == "arm_launch_bound_race":
        return arm_launch_bound_race(request.get("attempts", 1))
    if action == "force_launch_state":
        return force_launch_state(request["lease_id"], request["state"])
    if action == "launch_bound_race":
        return {"remaining": LAUNCH_BOUND_RACE["remaining"], "fired": LAUNCH_BOUND_RACE["fired"],
                "calls": LAUNCH_BOUND_RACE["calls"]}
    if action == "clear_intent_nonce":
        return clear_intent_nonce(request["lease_id"])
    if action == "pause":
        return pause(request["paused"])
    if action == "report_completion":
        return report_completion(request["lease_id"], request["worker_token"], request.get("label", "resume"))
    if action == "tool":
        return backend(request["tool"], request["args"])
    if action == "inspect":
        return inspect()
    if action == "web":
        return web(request["op"], request.get("body") or {})
    raise ValueError("unknown action " + str(action))


# The ledger path comes from the temporary CIEL_RUNTIME_CONFIG_DIR at import.
with patch.object(parallel, "ledger", wraps=parallel.ledger):
    for line in sys.stdin:
        try:
            reply = {"ok": True, "value": dispatch(json.loads(line))}
        except Exception as error:  # Reported to the harness, never swallowed.
            reply = {"ok": False, "error": str(error), "trace": traceback.format_exc()}
        print(json.dumps(reply, ensure_ascii=False), flush=True)
