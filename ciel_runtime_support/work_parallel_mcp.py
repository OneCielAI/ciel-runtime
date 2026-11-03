"""Conversation-scoped projection over the device-wide Work execution ledger."""
from __future__ import annotations
import copy
import json
import hmac
from pathlib import Path
from . import work_plan_tree as tree
from .work_parallel import WorkParallelStore, WorkParallelError

TOOL_NAMES = ("work_parallel_status", "work_parallel_configure", "work_parallel_run", "work_parallel_event")


def schemas():
    props = {
        "item_id": {"type": "string"}, "lease_id": {"type": "string"},
        "priority": {"type": "string", "enum": ["P1", "P2", "P3", "P4", "P5"]},
        "role": {"type": "string", "enum": ["worker", "reviewer", "resume"]},
        "expected_version": {"type": "integer", "minimum": 0},
        "changes": {"type": "object"}, "event": {"type": "string"},
        "artifact": {"type": "string"}, "evidence": {"type": "string"},
        "plan_version": {"type": "integer", "minimum": 0},
        "cleanup_proof": {"type": "string"},
        "workspace": {"type": "string"}, "remote_ref": {"type": "string"},
        "reviewer_lease_id": {"type": "string"},
    }
    descriptions = {
        "work_parallel_status": "Show Work 병렬 실행 settings, device capacity and this conversation's leases. Read before changing limits.",
        "work_parallel_configure": "On the user's natural-language request, persist Work 병렬 실행 limits, P2-P5 usage thresholds or pause. Pass changes and expected_version from work_parallel_status. Thresholds are fractions 0-1. Does not approve a plan.",
        "work_parallel_run": "Dispatch an approved Work item through the Claude host adapter. P1 requires a fork; all workers use fork. Requires item_id, priority, role and expected_version. Codex is unsupported until its native adapter passes capability tests.",
        "work_parallel_event": "Report a bound worker's question/result or coordinator validation/review/cleanup evidence. Never mark a submitted result done without validation and cleanup. Host supplies worker identity; do not impersonate an agent.",
    }
    return [{"name": name, "description": descriptions[name],
             "inputSchema": {"type": "object", "properties": copy.deepcopy(props)}} for name in TOOL_NAMES]


def ledger():
    from .runtime_paths import CONFIG_DIR
    return WorkParallelStore(Path(CONFIG_DIR) / "work-parallel.json")


def project(store, identity):
    d = store.read()
    return {"version": d["version"], "policy": d["policy"],
            "active_device": sum(r["slot"] for r in d["leases"].values()),
            "leases": {k: v for k, v in d["leases"].items() if v["scope"] == identity},
            "runtime": "claude" if identity.startswith("claude:") else "codex",
            "codex_adapter": "unsupported",
            "limitations": ["usage requires provider rate-limit observations; unknown blocks P2-P5",
                            "workspace isolation and automatic review dispatch not yet verified"]}


def host_context(args, config):
    """Authenticate the packaged host bridge, NOT model-provided identity flags.

    Deployment must provision a random host token out of band to the router and
    mod's api.hostToken. No token or failed capability check means fail closed.
    This is not a shell/filesystem sandbox: a process able to read that secret
    can impersonate the bridge. Never put the token in a schema or model result.
    """
    private = config.get("work_parallel_host", {})
    host = args.get("_host")
    secret = private.get("token")
    if secret is None:
        from .work_parallel_host import host_token
        secret = host_token()
    if (not isinstance(host, dict) or not isinstance(secret, str) or len(secret) < 32
            or not isinstance(host.get("token"), str)
            or not hmac.compare_digest(secret, host["token"])):
        raise WorkParallelError("authenticated parallel host required")
    return host, private.get("capability_ready", True) is True


def run(name, args, board_store, identity, config, save_config):
    store = ledger()
    if name == "work_parallel_status":
        return json.dumps(project(store, identity), ensure_ascii=False)
    host, ready = host_context(args, config)
    if not identity.startswith("claude:"):
        raise WorkParallelError("native adapter unsupported")
    actor = host.get("agent_id")
    version = args.get("expected_version")
    if name == "work_parallel_configure":
        if actor is not None:
            raise WorkParallelError("workers cannot configure parallel policy")
        d, _ = store.configure(args.get("changes"), capability_ready=ready, expected_version=version)
        if save_config is not None:
            updated = dict(config)
            updated["work_parallel"] = d["policy"]
            save_config(updated)
        return json.dumps(project(store, identity), ensure_ascii=False)
    if name != "work_parallel_event":
        raise WorkParallelError("dispatch requires packaged host interception")
    event = args.get("event")
    if event == "reserve":
        if actor is not None or host.get("fork") is not True or not ready:
            raise WorkParallelError("verified coordinator fork capability required")
        board = board_store.read()
        c = candidate(board, args.get("item_id"), args.get("priority", "P1"), args.get("role", "worker"))
        observed, _ = store.observe_usage(host.get("measured_usage"), expected_version=version)
        d, ident = store.reserve([c], scope=identity, measured_usage=host.get("measured_usage"), expected_version=observed["version"])
        return json.dumps({"lease_id": ident, "version": d["version"], "plan_version": board["version"]})
    d = store.read()
    lease = d["leases"].get(args.get("lease_id"))
    if not lease or lease["scope"] != identity:
        raise WorkParallelError("unknown conversation lease")
    ident = lease["id"]
    if event in ("bind", "reconcile"):
        if actor is not None:
            raise WorkParallelError("coordinator host observation required")
        if event == "bind" and lease["host_id"] is not None and lease["host_id"] == host.get("host_id"):
            pass  # Idempotent: the same observed native agent was already bound.
        elif event == "bind":
            d, _ = store.bind(ident, host.get("host_id"), expected_version=version)
        else:
            d, _ = store.reconcile(ident, host_state=host.get("state", "unknown"),
                                   proof=host.get("proof"), expected_version=version)
    else:
        targets = {"question": "waiting_input", "result": "submitted", "resume": "running",
                   "verify": "verifying", "review": "reviewing", "accept": "accepted",
                   "cleanup": "cleanup_pending", "done": "done", "rework": "running"}
        if event not in targets:
            raise WorkParallelError("unsupported parallel event")
        if actor is not None:
            if actor != lease["host_id"] or event not in ("question", "result"):
                raise WorkParallelError("worker may only report its own question/result")
        elif event in ("question", "result"):
            raise WorkParallelError("worker host identity required")
        board = board_store.read()
        c = candidate(board, lease["item_id"], lease["priority"], lease["role"])
        if board["version"] != lease["plan_version"] or not c["approved"] or not c["focused"]:
            raise WorkParallelError("Work approval/focus/version changed; reconcile before continuing")
        if event == "accept" and lease["role"] != "reviewer":
            reviewer = d["leases"].get(args.get("reviewer_lease_id"))
            if (not reviewer or reviewer["scope"] != identity or reviewer["role"] != "reviewer"
                    or reviewer["host_id"] == lease["host_id"] or reviewer["item_id"] != lease["item_id"]
                    or reviewer["plan_version"] != lease["plan_version"] or reviewer["state"] not in ("submitted", "verifying", "reviewing", "accepted", "cleanup_pending", "done")):
                raise WorkParallelError("independent reviewer result required before acceptance")
        if event == "done" and not isinstance(args.get("cleanup_proof"), str) and not str(lease.get("evidence") or "").startswith('{"status": "removed"'):
            raise WorkParallelError("verified cleanup or explicit retained-workspace proof required")
        evidence = args.get("evidence")
        cleanup_action = None
        if event == "cleanup" and args.get("workspace"):
            # Removal needs a verified remote push of HEAD or the user's explicit approval of
            # this exact path; approval comes only from the host's own prompt, never model args.
            def cleanup_action():
                return _remove_workspace(args["workspace"], lease, host, args.get("remote_ref", ""))
        cleanup_proof = args.get("cleanup_proof")
        if event == "done" and cleanup_proof is None and str(lease.get("evidence") or "").startswith('{"status": "removed"'):
            cleanup_proof = lease["evidence"]
        d, _ = store.transition(ident, targets[event], plan_version=args.get("plan_version"),
                                 expected_version=version, artifact=args.get("artifact"),
                                 evidence=evidence, cleanup_proof=cleanup_proof, cleanup_action=cleanup_action)
    return json.dumps(project(store, identity), ensure_ascii=False)


def candidate(board, item_id, priority, role):
    nodes = {n["id"]: n for n in board["nodes"]}
    if item_id not in nodes:
        raise WorkParallelError("unknown Work item")
    n = nodes[item_id]
    if role not in ("worker", "reviewer", "resume"):
        raise WorkParallelError("invalid execution role")
    ancestors = [n]
    while ancestors[-1]["parent_id"] is not None:
        ancestors.append(nodes[ancestors[-1]["parent_id"]])
    root = ancestors[-1]
    focus = board.get("focus_ids", [])
    return dict(item_id=item_id, priority=priority, created_at=n.get("created_at", n["updated_at"]),
                approved=tree.root_phase(board, root["id"]) == "executing",
                focused=not focus or any(x["id"] in focus for x in ancestors),
                dependencies_done=all(x["status"] not in ("done", "blocked") and not x.get("waiting_on") for x in ancestors),
                plan_version=board["version"], usage=.05, role=role, mode="fork")


def _remove_workspace(workspace, lease, host, remote_ref):
    import subprocess
    from .worktree_cleanup import cleanup_worktree
    target = Path(workspace)
    # A model-selected path or root containment alone is not an ownership proof.
    # Until the native adapter supplies an observed workspace binding, retain it.
    observed = host.get("workspace_binding")
    if (not isinstance(observed, dict) or observed.get("host_id") != lease["host_id"]
            or observed.get("lease_id") != lease["id"] or observed.get("idle") is not True
            or not isinstance(observed.get("path"), str)
            or Path(observed["path"]).resolve() != target.resolve()):
        raise WorkParallelError("verified native workspace ownership and idle binding unavailable; worktree retained")
    def git(*args):
        done = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=30, check=False)
        if done.returncode:
            raise WorkParallelError("workspace is not a readable Git worktree; retained")
        return done.stdout.strip()
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    repo = common.parent
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    ref = remote_ref or ("refs/heads/" + branch if branch != "HEAD" else "")
    try:
        return cleanup_worktree(repo, target, owned_root=repo / ".claude" / "worktrees",
                                expected_head=git("rev-parse", "HEAD"), active=lease["slot"],
                                approved_target=host.get("approved_target"), remote_ref=ref)
    except ValueError as error:
        raise WorkParallelError(str(error)) from error
