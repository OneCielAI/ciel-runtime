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
BRIDGE_CALLS = []


def bind_router():
    """The routed main session this backend serves (backend mcp 30314c14 onward).

    A rework or stranded recovery is reserved only for a proven live owner
    (work_parallel_mcp._proven_owner). In a real session the conversation's own
    router has a compaction-scope.json naming it and its live launcher; here the
    scope names IDENTITY and this server process, as tests/work_owner_fixture.py
    does for the backend suites. WORK_OWNER_OFFLINE=1 leaves the owner unproven.
    """
    if os.environ.get("WORK_OWNER_OFFLINE") == "1":
        return None
    from ciel_runtime_support import runtime_paths
    instance = ROOT / "router-instance"
    instance.mkdir(parents=True, exist_ok=True)
    runtime, session = IDENTITY.split(":", 1)
    (instance / "compaction-scope.json").write_text(json.dumps({
        "runtime": runtime, "session_id": session, "launcher_pid": os.getpid(), "epoch": "fixture"}),
        encoding="utf-8")
    runtime_paths.ROUTER_INSTANCE_DIR = instance
    return instance


bind_router()


def owner(bound):
    """Wait-deadlock audit: make the routed owner unprovable, or bind it again later
    (the OO case: the router becomes bound while main is idle, with no event)."""
    if bound:
        bind_router()
    else:
        from ciel_runtime_support import runtime_paths
        (Path(runtime_paths.ROUTER_INSTANCE_DIR) / "compaction-scope.json").unlink(missing_ok=True)
    return {"proven": parallel._proven_owner({}, IDENTITY) is not None}


CLOCK = {"offset": 0.0}


def advance_clock(seconds):
    """Time passes for the backend only (no file is written): every
    ``time.time()`` call in this process reads ``seconds`` later. The launch
    window (work_parallel.py launch_rate, 60 s) is time-based only."""
    import time as _time
    if "real" not in CLOCK:
        CLOCK["real"] = _time.time
        _time.time = lambda: CLOCK["real"]() + CLOCK["offset"]
    CLOCK["offset"] += float(seconds)
    return {"offset": CLOCK["offset"]}


def seed_leaf(title):
    """One runnable approved leaf under its own executing goal, never reserved."""
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": title, "note": "", "priority": "P1",
        "children": [{"title": title + " item", "note": "", "ref": "", "waiting_on": "", "children": []}]},
        None), expected_version=doc["version"])
    item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    return {"root_id": root, "item_id": item,
            "runnable": [row["item_id"] for row in tree.runnable_leaves(BOARD.read())]}


def limits(p1):
    """The P1 concurrent fork limit, through the real configure tool."""
    current = parallel.ledger().read()["policy"]["priority_limits"]
    run("work_parallel_configure", {"changes": {"priority_limits": {**current, "P1": int(p1)}},
                                    "expected_version": parallel.ledger().read()["version"]})
    return {"P1": parallel.ledger().read()["policy"]["priority_limits"]["P1"]}


def fingerprint():
    """sha256 of every file under the isolated config dir and the work root: a
    write anywhere (ledger, journals, sidecars, board, worktrees) changes it."""
    import hashlib
    from ciel_runtime_support import runtime_paths
    files = {}
    for label, base in (("config", Path(runtime_paths.CONFIG_DIR)), ("work", ROOT)):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                try:
                    files[label + "/" + path.relative_to(base).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError as error:
                    files[label + "/" + path.relative_to(base).as_posix()] = "unreadable:" + type(error).__name__
    return files


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
        BRIDGE_CALLS.append({"tool": tool, "args": dict(args)})
        return json.loads(parallel.run(tool, dict(args), BOARD, IDENTITY, config(), None))
    if tool == "work_priority_command":
        from ciel_runtime_support.work_mcp_tools import dispatch_work_tool
        BRIDGE_CALLS.append({"tool": tool, "args": dict(args)})
        answer = dispatch_work_tool(1, tool, dict(args), load_config=config, state_dir=ROOT,
                                    store=BOARD, respond=lambda _id, text, error=False: {"text": text, "error": error})
        if answer["error"]:
            raise ValueError(answer["text"])
        return json.loads(answer["text"])
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
    # R2 B2: in a real session the launcher that binds the router scope also sets
    # CIEL_RUNTIME_WORK_LAUNCHER_PID (claude_environment). Here the scope names
    # this server process (bind_router), so the mod's launcher is this pid too.
    return {"token": host_token(), "repo": str(repo), "launcher_pid": os.getpid()}


OWNER_CHILDREN = []


def seed_running_owned(kind):
    """A bound running worker whose lease owner is another process, for R2 B2.

    other_live: a live separate launcher (a child process kept running);
    gone: a launcher that was attached and then exited;
    unknown: an owner with no recorded identity (a legacy-shaped owner).
    """
    nonce = None
    if kind in ("other_live", "gone"):
        # The stand-in launcher lives exactly as long as this server holds its
        # stdin pipe: however this server ends, the pipe closes and it exits.
        # Its cwd is outside the test tree so it never holds that directory.
        import tempfile
        child = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 cwd=tempfile.gettempdir())
        OWNER_CHILDREN.append(child)
        nonce = ("a" if kind == "other_live" else "b") * 32
        run("work_parallel_event", {"event": "host_attach"},
            {"internal": True, "launcher_pid": child.pid, "launch_nonce": nonce})
    elif kind != "unknown":
        raise ValueError("unknown owner kind " + str(kind))
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": "Owner " + kind, "note": "", "priority": "P1",
        "children": [{"title": "Owned by " + kind, "note": "", "ref": "", "waiting_on": "", "children": []}]},
        None), expected_version=doc["version"])
    item = next(n["id"] for n in doc["nodes"] if n["parent_id"] == root)
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    host = {"fork": True, "workspace_repo": str(ROOT / "repo")}
    # unknown: a session_pid with no identity is not provable (lease_owner_class).
    host.update({"launch_nonce": nonce} if nonce else {"session_pid": 1})
    reserve = run("work_parallel_event", {"event": "reserve", "item_id": item, "priority": "P1"}, host)
    agent = "owner-" + kind.replace("_", "-") + "-worker"
    run("work_parallel_event", {"event": "bind", "lease_id": reserve["lease_id"]},
        {"host_id": agent, "agent_name": agent})
    if kind == "gone":
        child.kill()
        child.wait(timeout=30)
    return {"lease_id": reserve["lease_id"], "item_id": item, "agent_id": agent,
            "owner": parallel.ledger().read()["leases"][reserve["lease_id"]].get("owner") is not None}


def seed(title, agent_name=None):
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
        {"host_id": "seed-worker-" + reserve["lease_id"][:8],
         **({"agent_name": agent_name} if agent_name else {})})
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


def seed_reported_pair():
    """Two bound workers that both reported; neither has been reconciled idle yet."""
    doc = BOARD.read()
    doc, root = BOARD.transaction(lambda d: tree.set_plan(d, {
        "title": "Reported pair", "note": "", "priority": "P1",
        "children": [
            {"title": "Stranded result", "note": "", "ref": "", "waiting_on": "", "children": []},
            {"title": "Healthy result", "note": "", "ref": "", "waiting_on": "", "children": []},
        ]}, None), expected_version=doc["version"])
    items = [n["id"] for n in doc["nodes"] if n["parent_id"] == root]
    BOARD.transaction(lambda d: tree.set_phase(d, "executing", root), expected_version=doc["version"])
    leases = []
    for index, item in enumerate(items):
        reserve = run("work_parallel_event", {"event": "reserve", "item_id": item, "priority": "P1"},
                      {"fork": True, "workspace_repo": str(ROOT / "repo")})
        agent = "pair-worker-" + str(index + 1)
        run("work_parallel_event", {"event": "bind", "lease_id": reserve["lease_id"]},
            {"host_id": agent, "agent_name": agent})
        workspace = Path(reserve["workspace"]["path"])
        (workspace / ("result-" + str(index + 1) + ".txt")).write_text("result", encoding="utf-8")
        parallel.report({"lease_id": reserve["lease_id"], "worker_token": reserve["worker_token"],
                         "event": "completion", "plan_version": BOARD.read()["version"],
                         "artifact": str(index + 1) * 64, "evidence": "tests pass",
                         "detail": {"summary": "result " + str(index + 1),
                                    "coverage": [{"item_id": item, "verified": True}],
                                    "verification": "tests pass"}}, BOARD, config())
        leases.append({"lease_id": reserve["lease_id"], "item_id": item, "agent_id": agent,
                       "workspace": str(workspace)})
    return {"leases": leases}


def break_worktree(lease_id):
    """Delete one lease's retained worktree directory, as a person or a tool could."""
    import shutil
    import stat
    lease = parallel.ledger().read()["leases"][lease_id]
    path = Path(lease["workspace"]["path"])

    def writable(function, target, _info):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onerror=writable)
    return {"path": str(path), "exists": path.exists()}


def seed_running(agent_id="unreported-worker"):
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
        {"host_id": agent_id, "agent_name": agent_id})
    return {"lease_id": reserve["lease_id"], "item_id": item, "agent_id": agent_id,
            "worker_token": reserve["worker_token"], "workspace": reserve["workspace"]["path"]}


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

    def record_assignment(self, scope, lease_id, manifest, *, expected_version, **options):
        if RESUME_ASSIGNMENT_FAULT["message"] and manifest.get("role") == "resume":
            raise parallel.WorkParallelError(RESUME_ASSIGNMENT_FAULT["message"])
        return original(self, scope, lease_id, manifest, expected_version=expected_version, **options)
    WorkCoordinationStore.record_assignment = record_assignment


_install_resume_assignment_fault()


def workspace_status(lease_id):
    from ciel_runtime_support import work_parallel_workspace as ws
    lease = parallel.ledger().read()["leases"][lease_id]
    _, _, manifest = ws._identity(lease["workspace"]["repo"], lease_id, None)
    return json.loads(manifest.read_text(encoding="utf-8"))["status"]


def lease_workspace(lease_id):
    """The retained worktree path the ledger records for one lease, and whether it exists."""
    lease = parallel.ledger().read()["leases"].get(lease_id)
    path = (lease or {}).get("workspace", {}).get("path") if lease else None
    return {"path": path, "exists": bool(path) and Path(path).exists()}


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

    def launch_bound(self, replacement_lease_id, agent_id, agent_name=None, *, handoff_drift="unknown",
                     expected_version):
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
        return original(self, replacement_lease_id, agent_id, agent_name,
                        handoff_drift=handoff_drift, expected_version=expected_version)
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


def policy(changes):
    """Set enabled, paused, launches_per_minute and/or max_rework through the real configure tool."""
    allowed = {key: bool(value) for key, value in dict(changes).items() if key in ("enabled", "paused")}
    for key in ("launches_per_minute", "max_rework"):
        if key in changes:
            allowed[key] = int(changes[key])
    if not allowed or len(allowed) != len(changes):
        raise ValueError("policy changes must be enabled, paused, launches_per_minute and/or max_rework")
    run("work_parallel_configure", {"changes": allowed,
                                    "expected_version": parallel.ledger().read()["version"]})
    current = parallel.ledger().read()["policy"]
    return {"enabled": current["enabled"], "paused": current["paused"],
            **({"launches_per_minute": current["launches_per_minute"]} if "launches_per_minute" in allowed else {})}


def _host_project(identity, board):
    """The projection production REST reads: the host view (review.ended) when the backend has one."""
    import inspect
    if "host_view" in inspect.signature(parallel.project).parameters:
        return parallel.project(parallel.ledger(), identity, board, host_view=True)
    return parallel.project(parallel.ledger(), identity, board)


def web(op, body):
    """The real Work REST controller, as the browser console calls it."""
    from ciel_runtime_support.http_response import HttpResponseAdapter
    from ciel_runtime_support.work_http_controller import WorkHttpController, WorkHttpServices
    controller = WorkHttpController(WorkHttpServices(
        ROOT / "state", config, lambda: IDENTITY, lambda identity: {"session_id": identity, "last_observed": None},
        HttpResponseAdapter.write_json, "fixture",
        parallel_status=lambda identity, board: _host_project(identity, board),
        parallel_store=parallel.ledger))
    if op == "launches":
        return controller._public_launches(IDENTITY)
    if op == "parallel":
        # The browser's Parallel tab payload, as /parallel builds it.
        return controller._public_parallel(IDENTITY, BOARD.read())
    if op == "resolve":
        return controller._parallel_launch_resolve(IDENTITY, body)
    # The operator effects the web, REST, menu and ctl share: a policy change
    # and a result decision, applied while main is idle (wait-deadlock audit).
    if op == "configure":
        return controller._operator_effect(IDENTITY, "parallel_configure", {
            "changes": body["changes"], "expected_version": parallel.ledger().read()["version"]})
    if op == "review":
        final = parallel.finalization(parallel.ledger()).read()
        # This fixture's BOARD is the session store used by every MCP call;
        # patch the controller accessor so this web call reaches the same bytes.
        with patch.object(controller, "_store", return_value=BOARD):
            return controller._parallel_item_review(IDENTITY, {**body, "expected_finalization_version": final["version"]})
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


def set_intent_owner(lease_id, kind):
    """Rewrite one review's launch intent owner for R2 B2 (as clear_intent_nonce does).

    unknown: no owner record (a pre-nonce intent); other_live: the nonce of a
    live separate launcher attached to this conversation.
    """
    if kind == "unknown":
        return clear_intent_nonce(lease_id)
    if kind != "other_live":
        raise ValueError("unknown intent owner kind " + str(kind))
    import tempfile
    child = subprocess.Popen([sys.executable, "-I", "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=tempfile.gettempdir())
    OWNER_CHILDREN.append(child)
    nonce = "c" * 32
    run("work_parallel_event", {"event": "host_attach"},
        {"internal": True, "launcher_pid": child.pid, "launch_nonce": nonce})
    reviews = parallel.finalization(parallel.ledger())
    path = reviews.path
    state = json.loads(path.read_text(encoding="utf-8"))
    launch = state["reviews"][lease_id]["launch"]
    assert launch["state"] == "launch_intent", launch["state"]
    launch["host_nonce"] = nonce
    tmp = path.with_name(path.name + ".owner.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)
    reviews.read()
    return {"owner": kind}


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


# A real 57dbfb3 process on these exact files (the same driver as
# tests/test_work_parallel_rework_worktree.py). WORK_BETA_ROOT is a private
# extract of that commit's ciel_runtime_support, prepared by the test.
BETA_DRIVER = r"""
import json, sys
from pathlib import Path
from unittest.mock import patch
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["root"])
import ciel_runtime_support.runtime_paths as runtime_paths
from ciel_runtime_support import work_parallel_mcp as m
from ciel_runtime_support.work_parallel import WorkParallelStore
from ciel_runtime_support.work_plan_store import WorkPlanStore
assert Path(m.__file__).resolve().is_relative_to(Path(spec["root"]).resolve()), m.__file__
out = []
with patch.object(runtime_paths, "CONFIG_DIR", Path(spec["config"])):
    store = WorkParallelStore(Path(spec["ledger"]))
    board = WorkPlanStore(Path(spec["board"]))
    for name, args in spec["calls"]:
        args = dict(args)
        args["_host"] = {"token": spec["token"], **args.pop("host", {})}
        args.setdefault("expected_version", store.read()["version"])
        try:
            with patch.object(m, "ledger", return_value=store):
                out.append({"ok": json.loads(m.run(name, args, board, spec["scope"], spec["config_obj"], None))})
        except Exception as error:
            out.append({"error": type(error).__name__, "message": str(error)})
print(json.dumps(out))
"""


def beta(calls):
    """Run work_parallel calls in a separate real 57dbfb3 process on these files."""
    from ciel_runtime_support import runtime_paths
    root = os.environ.get("WORK_BETA_ROOT")
    if not root:
        raise ValueError("WORK_BETA_ROOT is not set")
    spec = {"root": root, "config": str(runtime_paths.CONFIG_DIR), "ledger": str(parallel.ledger().path),
            "board": str(BOARD.path), "scope": IDENTITY, "token": host_token(), "config_obj": config(),
            "calls": calls}
    done = subprocess.run([sys.executable, "-I", "-B", "-c", BETA_DRIVER, json.dumps(spec)],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    if done.returncode:
        raise RuntimeError("beta process failed: " + done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def repo_path():
    return str(ROOT / "repo")


def failures():
    """This conversation's durable failure log, or None on a backend without one."""
    try:
        from ciel_runtime_support.work_parallel_failures import WorkParallelFailureLog
    except ImportError:
        return None
    return WorkParallelFailureLog.beside(parallel.ledger().path).public(IDENTITY)


def p0_view():
    side = parallel.p0_state(parallel.ledger()).read()
    return {"roots": side["roots"], "intents": side["intents"],
            "policy": parallel.ledger().read()["policy"], "board": BOARD.read()}


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
        "stranded": _stranded_view(store),
    }


def _stranded_view(store):
    """The stranded-v1 sidecar's rows (no binding or launch nonce), or None on a pre-sidecar backend."""
    try:
        from ciel_runtime_support.work_parallel_stranded import stranded_store
    except ImportError:
        return None
    state = stranded_store(store).read()
    return {"version": state["version"],
            "rows": {key: {k: row.get(k) for k in ("kind", "state", "generation", "replacement_lease_id",
                                                   "replacement_generation", "stages", "artifact")}
                     | {"wake": (row.get("wake") or {}).get("state"),
                        "launch": (row.get("launch") or {}).get("state"),
                        # checkpoint3 fields; absent (None) on an earlier sidecar.
                        "superseded_by": (row.get("superseded") or {}).get("lease_id"),
                        "legacy_review_removed": (row.get("legacy_review_removed") or {}).get("state")}
                     for key, row in state["rows"].items()}}


def dispatch(request):
    action = request["action"]
    if action == "init":
        return init()
    if action == "seed":
        return seed(request["title"], request.get("agent_name"))
    if action == "seed_leaf":
        return seed_leaf(request["title"])
    if action == "seed_restart_pair":
        return seed_restart_pair()
    if action == "seed_running":
        return seed_running(request.get("agent_id", "unreported-worker"))
    if action == "seed_running_owned":
        return seed_running_owned(request["kind"])
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
    if action == "lease_workspace":
        return lease_workspace(request["lease_id"])
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
    if action == "set_intent_owner":
        return set_intent_owner(request["lease_id"], request["kind"])
    if action == "pause":
        return pause(request["paused"])
    if action == "policy":
        return policy(request["changes"])
    if action == "seed_reported_pair":
        return seed_reported_pair()
    if action == "break_worktree":
        return break_worktree(request["lease_id"])
    if action == "report_completion":
        return report_completion(request["lease_id"], request["worker_token"], request.get("label", "resume"))
    if action == "tool":
        return backend(request["tool"], request["args"])
    if action == "bridge_calls":
        return list(BRIDGE_CALLS)
    if action == "inspect":
        return inspect()
    if action == "p0_view":
        return p0_view()
    if action == "beta":
        return beta(request["calls"])
    if action == "repo_path":
        return repo_path()
    if action == "failures":
        return failures()
    if action == "web":
        return web(request["op"], request.get("body") or {})
    if action == "owner":
        return owner(request["bound"])
    if action == "advance_clock":
        return advance_clock(request["seconds"])
    if action == "seed_leaf":
        return seed_leaf(request["title"])
    if action == "limits":
        return limits(request["p1"])
    if action == "end_owner_children":
        # The other live Work host's launcher exits (wait-deadlock audit).
        ended = 0
        for child in OWNER_CHILDREN:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=30)
                ended += 1
        return {"ended": ended}
    if action == "fingerprint":
        return fingerprint()
    raise ValueError("unknown action " + str(action))


# The ledger path comes from the temporary CIEL_RUNTIME_CONFIG_DIR at import.
try:
    with patch.object(parallel, "ledger", wraps=parallel.ledger):
        for line in sys.stdin:
            try:
                reply = {"ok": True, "value": dispatch(json.loads(line))}
            except Exception as error:  # Reported to the harness, never swallowed.
                reply = {"ok": False, "error": str(error), "trace": traceback.format_exc()}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
finally:
    # Only this server's own owner stand-ins (seed_running_owned).
    for child in OWNER_CHILDREN:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)
