"""Conversation-scoped projection over the device-wide Work execution ledger."""
from __future__ import annotations
import copy
import hashlib
import json
import hmac
import secrets
import time
from pathlib import Path
from . import work_plan_tree as tree
from .work_parallel import (WorkParallelStore, WorkParallelConflict, WorkParallelError,
                            active_by_priority, digest, hash_worker_token, integer, require,
                            text, worker_token_matches)
from .workspace_router_selection import _process_identity as process_identity

TOOL_NAMES = ("work_parallel_status", "work_parallel_configure", "work_parallel_run",
              "work_parallel_event", "work_parallel_report", "work_parallel_item_review",
              "work_discovery_pickup")


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
        "detail_digest": {"type": "string"}, "detail": {"type": "object"},
        "related_lease_id": {"type": "string"},
        "worker_token": {"type": "string"}, "operation": {"type": "string"},
        "preview_digest": {"type": "string"}, "observation": {"type": "object"},
        "source": {"type": "string"}, "scope": {"type": "string"}, "results": {"type": "array", "items": {"type": "object"}},
        "rows": {"type": "array", "items": {"type": "object"}}, "complete": {"type": "boolean"},
        "expected_evidence_version": {"type": "integer", "minimum": 0},
        "expected_finalization_version": {"type": "integer", "minimum": 0},
        "decision": {"type": "string", "enum": ["approve", "rework"]},
        "decision_id": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "rework_instructions": {"type": "string"},
        "attempt_id": {"type": "string"}, "dispatch_name": {"type": "string"},
        "generation": {"type": "integer", "minimum": 1}, "agent_id": {"type": "string"},
        "state": {"type": "string"}, "proof": {"type": "string"},
    }
    descriptions = {
        "work_discovery_pickup": "Register caller-verified external open search items by priority/latest, with durable dedup and cooldown; never approves execution. Requires source, scope, results. Each result: source,item_id,priority,created_at,title,link,status,scope.",
        "work_parallel_status": "Show Work 병렬 실행 settings, device capacity and this conversation's leases. Read before changing limits.",
        "work_parallel_configure": "On the user's natural-language request, persist Work 병렬 실행 limits (concurrent executing fork sessions, reserved P1 slot, launch rate, rework limit) or pause. This never limits registering or queueing Work items. Pass changes and expected_version from work_parallel_status. Does not approve a plan.",
        "work_parallel_run": "Dispatch an approved Work item: the packaged host starts the fork itself and returns the started lease/agent. You do not call the Agent tool or supply a name for this dispatch. Requires item_id, priority, role and expected_version. Codex is unsupported until its native adapter passes capability tests.",
        "work_parallel_event": "Coordinator lifecycle and authenticated host observations for Work forks. Workers report through work_parallel_report.",
        "work_parallel_report": "Worker-only question/completion/handoff/review report. Requires the exact lease_id and one-use worker_token. Completion needs artifact+evidence; handoff needs a host-stored detail_digest. A successful report releases the fork slot; no session_id or host token.",
        "work_parallel_item_review": "Main-session only decision for a bounded pending completed item. Requires lease_id, decision (approve|rework), decision_id, evidence and expected_finalization_version. The host authenticates main; workers and reviewer forks are denied. Rework returns a host dispatch intent and never exposes a worker token to main.",
    }
    schemas = []
    for name in TOOL_NAMES:
        schema = {"type": "object", "properties": copy.deepcopy(props)}
        if name == "work_parallel_report":
            schema["required"] = ["lease_id", "worker_token", "event"]
        elif name == "work_parallel_item_review":
            schema["required"] = ["lease_id", "decision", "decision_id", "evidence",
                                  "expected_finalization_version"]
        schemas.append({"name": name, "description": descriptions[name], "inputSchema": schema})
    return schemas


def ledger():
    from .runtime_paths import CONFIG_DIR
    return WorkParallelStore(Path(CONFIG_DIR) / "work-parallel.json")


def native_evidence(store=None):
    """The native fork evidence file beside the given (or device) ledger."""
    from .work_parallel_native import NativeForkEvidence
    base = store.path if store is not None else Path(ledger().path)
    return NativeForkEvidence(base.with_name("work-parallel-native.json"))


def coordination(store=None):
    """Host-owned assignment/receipt state kept out of the legacy lease schema."""
    from .work_parallel_receipts import coordination_store
    return coordination_store(store or ledger())


def finalization(store=None):
    """Main-review journal kept outside the old-reader execution ledger."""
    from .work_parallel_finalization import finalization_store
    return finalization_store(store or ledger())


def _root_for(board, item_id):
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    current = nodes.get(item_id)
    if current is None:
        raise WorkParallelError("unknown Work item")
    while current.get("parent_id") is not None:
        current = nodes[current["parent_id"]]
    return current


def _root_scope_digest(board, root_id):
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    rows = []
    stack = [root_id]
    while stack:
        ident = stack.pop()
        node = nodes.get(ident)
        if node is None:
            raise WorkParallelError("unknown Work root")
        rows.append({key: node.get(key, "") for key in ("id", "parent_id", "title", "note", "ref")})
        stack.extend(reversed([row["id"] for row in board["nodes"] if row.get("parent_id") == ident]))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _reconcile_root(coord, board, root_id, identity):
    """CAS-reconcile one root's semantic approval generation."""
    root = _root_for(board, root_id)
    phase = tree.root_phase(board, root["id"])
    row = {"root_id": root["id"], "phase": phase,
           "scope_digest": _root_scope_digest(board, root["id"])}
    for attempt in range(3):
        current = coord.read()
        try:
            state, generations = coord.reconcile_roots(identity, [row], expected_version=current["version"])
            saved = state["root_generations"][identity + "\0" + root["id"]]
            return {"approved": saved["phase"] == "executing",
                    "generation": generations[root["id"]], "fingerprint": saved["scope_digest"]}
        except WorkParallelError as error:
            if attempt == 2 or "changed; reread" not in str(error):
                raise


def _item_details(board):
    """Item display metadata from the board: id -> title/ref/goal/path.

    Titles are display data only; they never influence admission or identity.
    A stale or unreadable board yields no details rather than failing status.
    """

    details = {}
    try:
        nodes = {n["id"]: n for n in board["nodes"]}
    except (TypeError, KeyError):
        return details
    for node_id, node in nodes.items():
        chain = []
        current = node
        seen = set()
        while isinstance(current, dict) and current.get("id") not in seen:
            seen.add(current.get("id"))
            chain.append(current)
            parent = current.get("parent_id")
            current = nodes.get(parent) if parent is not None else None
        chain.reverse()
        titles = [str(n.get("title") or "") for n in chain]
        details[node_id] = {
            "item_title": titles[-1] if titles else "",
            "item_ref": str(node.get("ref") or ""),
            "goal_id": str(chain[0].get("id") or "") if chain else "",
            "goal_title": titles[0] if titles else "",
            "item_path": titles,
            "item_note": str(node.get("note") or ""),
        }
    return details


def _clip(value, limit):
    value = str(value or "")
    return value if len(value) <= limit else value[:limit - 1] + "…"


EVIDENCE_GROUPS = ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended")
MODEL_LEASE_LIMIT = 24
MODEL_ENDED_LIMIT = 8
MODEL_TEXT_LIMIT = 180

EVIDENCE_RULE = ("A fork counts as processing only when the native agent list showed it pending or running "
                 "within the last 30 seconds. A reserved slot, a running lease state or a bound host id alone "
                 "is not evidence. The main session is the coordinator and is never a fork; side actors are "
                 "counted separately.")


def _unbound_rework_replacements(store, identity):
    """Replacement lease ids whose launch name is host-only until bound."""
    try:
        reviews = finalization(store).read()["reviews"].values()
    except Exception:
        # Unknown launch state fails closed: hide every resume lease's name.
        try:
            leases = store.read()["leases"]
        except Exception:
            return frozenset()
        return frozenset(key for key, lease in leases.items()
                         if lease.get("scope") == identity and lease.get("role") == "resume")
    return frozenset(row["replacement_lease_id"] for row in reviews
                     if row["scope"] == identity and row["decision"] == "rework"
                     and row["replacement_lease_id"] is not None and row["launch"]["state"] != "bound")


def _fork_projection(store, identity, leases, details, hidden=frozenset()):
    """Evidence-classified, allow-listed view of this conversation's leases.

    Only ids, titles, priorities, states, a fixed native status word and
    timestamps leave the host: never owner pids, workspaces, proofs, tokens
    or anything a fork wrote.
    """
    from .work_parallel_native import FRESH_SECONDS, classify
    try:
        evidence = native_evidence(store).read()
        available = True
    except Exception:
        evidence, available = {"version": None, "records": {}, "scans": {}}, False
    now = time.time()
    groups = {name: [] for name in EVIDENCE_GROUPS}
    for lease in sorted(leases.values(), key=lambda r: (r["priority"], r.get("created_at") or 0, r["id"])):
        record = evidence["records"].get(lease["id"])
        if record is not None and record.get("scope") != identity:
            record = None
        group = classify(lease, record, evidence["scans"].get(identity), now=now)
        item = details.get(lease["item_id"], {})
        row = {"lease_id": lease["id"], "item_id": lease["item_id"],
               "item_title": _clip(item.get("item_title"), 300), "item_ref": _clip(item.get("item_ref"), 100),
               "goal_title": _clip(item.get("goal_title"), 300),
               "item_path": [_clip(t, 120) for t in (item.get("item_path") or [])[:8]],
               "priority": lease["priority"], "role": lease["role"], "state": lease["state"],
               "slot": lease["slot"],
               "agent_name": None if lease["id"] in hidden else (lease.get("agent_name") or lease.get("dispatch_name")),
               "agent_id": lease.get("host_id"), "rework": lease["rework"],
               # Lifecycle timestamps live in the coordination sidecar so this
               # shared lease file stays readable by old live builds.
               "reserved_at": lease.get("created_at"), "bound_at": None,
               "state_changed_at": None,
               "native": None if record is None else {k: record[k] for k in (
                   "status", "first_seen_at", "last_seen_at", "status_changed_at", "missing_since", "terminal_at")},
               "evidence": group}
        if lease.get("terminal"):
            row["terminal_reason"] = _clip(lease["terminal"].get("reason"), 100)
        groups[group].append(row)
    counts = {name: len(rows) for name, rows in groups.items()}
    for name in groups:
        groups[name] = groups[name][-(MODEL_ENDED_LIMIT if name == "ended" else MODEL_LEASE_LIMIT):]
    return {"schema": "native-fork-evidence/1", "rule": EVIDENCE_RULE, "available": available,
            "evidence_version": evidence["version"], "fresh_seconds": FRESH_SECONDS,
            "observed_at": evidence["scans"].get(identity),
            "confirmed_count": counts["confirmed"], "counts": counts, **groups}


def _device_projection(d, identity):
    counts = active_by_priority(d["leases"])
    mine = active_by_priority({k: v for k, v in d["leases"].items() if v["scope"] == identity})
    limits = d["policy"]["priority_limits"]
    return {"active_slots": sum(counts.values()), "capacity": sum(limits.values()),
            "priority_limits": dict(limits), "active_by_priority": counts,
            "this_session_by_priority": mine,
            "other_sessions_by_priority": {k: counts[k] - mine[k] for k in counts},
            "paused": d["policy"]["paused"], "enabled": d["policy"]["enabled"],
            "note": "Device-wide lease slots across every session; a slot is not proof that a fork is running."}


def project(store, identity, board=None, *, host_view=False):
    d = store.read()
    all_mine = {k: v for k, v in d["leases"].items() if v["scope"] == identity}
    # An unbound rework launch name is a host recovery fact, not model context.
    # The authenticated mod keeps the lease fields it has always reconciled with.
    hidden = frozenset() if host_view else _unbound_rework_replacements(store, identity)
    # The scheduler/model default projection is actionable state, not an audit
    # dump. Terminal rows remain available through the coordination archive/UI.
    open_rows = [(k, v) for k, v in all_mine.items() if v["state"] in (
        "reserved", "running", "waiting_input", "submitted", "verifying",
        "reviewing", "accepted", "cleanup_pending")]
    open_rows.sort(key=lambda pair: (pair[1]["priority"], pair[1]["created_at"], pair[0]))
    details = _item_details(board) if board is not None and open_rows else {}
    leases = {}
    allow = ("id", "item_id", "priority", "role", "state", "slot", "host_id",
             "agent_name", "dispatch_name", "plan_version", "artifact", "rework")
    for ident, source in open_rows[:MODEL_LEASE_LIMIT]:
        row = {key: source.get(key) for key in allow if source.get(key) is not None}
        if ident in hidden:
            row.pop("dispatch_name", None)
            row.pop("agent_name", None)
        item = details.get(source["item_id"], {})
        row.update(item_title=_clip(item.get("item_title"), MODEL_TEXT_LIMIT),
                   item_ref=_clip(item.get("item_ref"), 100),
                   goal_id=item.get("goal_id", ""),
                   goal_title=_clip(item.get("goal_title"), MODEL_TEXT_LIMIT),
                   item_path=[_clip(value, 100) for value in (item.get("item_path") or [])[:6]])
        leases[ident] = row
    forks = _fork_projection(store, identity, all_mine, details, hidden)
    state_counts = {}
    for row in all_mine.values():
        state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1
    try:
        receipts = coordination(store).projection(identity, limit=MODEL_LEASE_LIMIT)
        coordination_summary = {"version": receipts["version"],
            "runs": receipts["runs"], "receipts": receipts["receipts"],
            "truncated": receipts["truncated"]}
    except Exception:
        coordination_summary = {"version": None, "assignments": [], "runs": [], "receipts": [], "truncated": False}
    try:
        review_summary = finalization(store).projection(identity, limit=MODEL_LEASE_LIMIT)
        review_details = _item_details(board) if board is not None and review_summary["pending"] else {}
        for row in review_summary["pending"]:
            item = review_details.get(row["item_id"], {})
            row.update(item_title=_clip(item.get("item_title"), MODEL_TEXT_LIMIT),
                       item_ref=_clip(item.get("item_ref"), 100),
                       goal_title=_clip(item.get("goal_title"), MODEL_TEXT_LIMIT),
                       item_path=[_clip(value, 100) for value in (item.get("item_path") or [])[:6]])
            # Saga identities and the repo root stay host-side; main inspects
            # the recorded worktree path, branch, HEAD and bounded diff only.
            ws = row["workspace"]
            row["workspace"] = {"path": ws["path"], "branch": ws["branch"], "head": ws["head"],
                                "dirty": ws["dirty"], "changed_count": ws["changed_files_total"],
                                "changed_files": [_clip(value, 200) for value in ws["changed_files"][:20]],
                                "diff_stat": _clip(ws["diff_stat"], 2000)}
            row["coverage_count"] = len(row["coverage"])
            row["coverage"] = row["coverage"][:32]
            row["stages"] = None if row["decision"] is None else row["stages"]
            # Main sees only durable wake and launch state, never host attempt,
            # agent identity, dispatch name, proof or capability.
            row["wake"] = {"state": row["wake"]["state"], "attempts": row["wake"]["attempts"]}
            launch_view = {"state": row["launch"]["state"], "attempt": row["launch"]["attempt"]}
            if row["launch"]["launch_ref"] is not None:
                # Opaque and not a capability: a manual request with it still
                # needs the person's confirmation, and the host checks absence.
                launch_view["launch_ref"] = row["launch"]["launch_ref"]
            row["launch"] = launch_view
            row["blocked"] = None if row["blocked"] is None else {
                "code": row["blocked"]["code"], "message": _clip(row["blocked"]["message"], 300)}
    except Exception:
        review_summary = {"version": None, "pending": [], "truncated": False}
    return {"version": d["version"], "policy": d["policy"],
            "active_device": sum(r["slot"] for r in d["leases"].values()),
            "active_by_priority": active_by_priority(d["leases"]),
            "leases": leases, "lease_counts": state_counts,
            "leases_truncated": len(open_rows) > MODEL_LEASE_LIMIT,
            "forks": forks, "coordination": coordination_summary, "review": review_summary,
            "device": _device_projection(d, identity),
            "runtime": "claude" if identity.startswith("claude:") else "codex",
            "codex_adapter": "unsupported",
            "limitations": ["dispatch is limited by concurrent fork slots and the launch rate, not by provider usage observations",
                            "external discovery requires a configured authenticated source connector"]}


def public_projection(projected, identity):
    """Browser-safe /parallel: evidence groups and aggregates, no raw leases."""
    return {"session_id": identity, "version": projected["version"],
            "forks": projected["forks"], "device": projected["device"],
            "policy": {k: projected["policy"][k] for k in ("enabled", "paused", "priority_limits",
                                                          "launches_per_minute", "max_rework")},
            "runtime": projected["runtime"], "limitations": projected["limitations"]}


def _approval_current(store, board, lease):
    """Validate only the assigned root generation, not unrelated board edits."""
    coord = coordination(store)
    assignment = coord.read().get("runs", {}).get(lease["id"])
    if assignment is None:
        # Compatibility for leases minted by an older live build.
        return board["version"] == lease["plan_version"] and candidate(
            board, lease["item_id"], lease["priority"], lease["role"])["approved"]
    root = _root_for(board, lease["item_id"])
    if root["id"] != assignment["root_id"]:
        return False
    approval = _reconcile_root(coord, board, root["id"], lease["scope"])
    return (approval["approved"] and approval["generation"] == assignment["approval_generation"]
            and approval["fingerprint"] == assignment["fingerprint"])


def _worker_event(store, board_store, lease, args):
    event = args.get("event")
    if event not in ("question", "result", "completion", "handoff", "review"):
        raise WorkParallelError("worker may only report question, completion, handoff or review")
    if event == "review" or lease["role"] == "reviewer":
        raise WorkParallelError("reviewer reports are retired; main reviews completed results")
    if lease["state"] != "running" or not lease["slot"]:
        raise WorkParallelError("worker lease is not active")
    board = board_store.read()
    if not _approval_current(store, board, lease):
        raise WorkParallelError("Work approval scope changed; reconcile before continuing")
    evidence = args.get("evidence")
    artifact = args.get("artifact")
    coord = coordination(store)
    # The authenticated capability proves the ledger binding. Complete a
    # coordination bind lost between the two stores before any receipt below
    # moves the run past "reserved", where it could never be bound again.
    _repair_lost_coordination_bind(store, lease)
    assignment = coord.read().get("runs", {}).get(lease["id"])
    if event in ("result", "completion", "review"):
        if event != "review" and not digest(artifact):
            raise WorkParallelError("completion artifact SHA256 required")
        kind = "review" if event == "review" else "completion"
        if assignment is not None:
            detail = args.get("detail")
            if not isinstance(detail, dict):
                detail = ({"summary": evidence, "coverage": [{"item_id": lease["item_id"], "verified": True}],
                           "verification": evidence, "workspace": None} if kind == "completion" else
                          {"receipt_id": args.get("related_lease_id") or "legacy", "pass": True,
                           "coverage": [lease["item_id"]], "findings": [], "verification": evidence})
            cstate = coord.read()
            coord.persist_receipt(lease["id"], kind, detail, expected_version=cstate["version"])
        d, _ = store.report_completion(lease["id"], plan_version=lease["plan_version"],
                                       artifact=artifact, evidence=evidence,
                                       expected_version=store.read()["version"])
    elif event == "question":
        if assignment is not None:
            detail = args.get("detail") if isinstance(args.get("detail"), dict) else {
                "question": evidence, "blocking_todos": [lease["item_id"]], "decision_needed": evidence}
            cstate = coord.read()
            coord.persist_receipt(lease["id"], "question", detail, expected_version=cstate["version"])
        d, _ = store.report_question(lease["id"], plan_version=lease["plan_version"], evidence=evidence,
                                     expected_version=store.read()["version"])
    else:
        receipt_digest = args.get("detail_digest")
        if assignment is not None:
            detail = args.get("detail")
            if not isinstance(detail, dict):
                raise WorkParallelError("structured handoff detail required")
            cstate = coord.read()
            _, receipt_digest = coord.persist_receipt(lease["id"], "handoff", detail,
                                                      expected_version=cstate["version"])
        d, _ = store.report_handoff(lease["id"], plan_version=lease["plan_version"],
                                    receipt_digest=receipt_digest, proof=evidence,
                                    expected_version=store.read()["version"])
    return {"lease_id": lease["id"], "version": d["version"], "item_id": lease["item_id"],
            "state": d["leases"][lease["id"]]["state"], "slot_released": True}


def report(args, board_store, config):
    """Capability-scoped worker report; no conversation or host identity needed."""
    del config
    store = ledger()
    d = store.read()
    lease = d["leases"].get(args.get("lease_id"))
    if not lease or not worker_token_matches(lease, args.get("worker_token")):
        raise WorkParallelError("invalid or revoked worker capability")
    return json.dumps(_worker_event(store, board_store, lease, args), ensure_ascii=False)


def _authenticated_host_read(args, config):
    if not isinstance(args.get("_host"), dict):
        return False
    try:
        host, _ = host_context(args, config)
    except (ValueError, OSError):
        return False
    return host.get("agent_id") is None


def _internal_host(host):
    return host.get("agent_id") is None and host.get("internal") is True


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


def _board_or_none(board_store):
    try:
        return board_store.read()
    except Exception:
        return None


def _owner_from_host(host, conversation):
    """The launcher process that owns this conversation's leases, when provable.

    The router's bound scope names the launcher of its current conversation;
    it is used only when that conversation is the one reserving, so a lease is
    never attributed to a different session's process.
    """
    pid = host.get("session_pid")
    identity = host.get("session_process_identity")
    if type(pid) is not int or pid <= 0:
        try:
            from .runtime_paths import ROUTER_INSTANCE_DIR
            scope = json.loads((Path(ROUTER_INSTANCE_DIR) / "compaction-scope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(scope, dict) or str(scope.get("runtime")) + ":" + str(scope.get("session_id")) != conversation:
            return None
        pid = scope.get("launcher_pid")
        if type(pid) is not int or pid <= 0:
            return None
        identity = process_identity(pid)
    return {"session_pid": pid, "identity": identity if isinstance(identity, str) else None}


def _reserve_assignment(store, board_store, identity, host, version, *, item_id=None, role="worker"):
    board = board_store.read()
    if item_id is None:
        rows = tree.runnable_leaves(board)
        if not rows:
            raise WorkParallelError("no eligible candidate")
        candidates = [candidate(board, row["item_id"], row["priority"], role) for row in rows]
    else:
        details = _item_details(board).get(item_id)
        if details is None:
            raise WorkParallelError("unknown Work item")
        nodes = {node["id"]: node for node in board["nodes"]}
        current = nodes[item_id]
        while current["parent_id"] is not None:
            current = nodes[current["parent_id"]]
        priority = current.get("priority") if current.get("priority") in tree.PRIORITIES else "P5"
        candidates = [candidate(board, item_id, priority, role)]
        refusal = dispatch_refusal(board, item_id)
        if refusal:
            raise WorkParallelError(refusal)
    token = secrets.token_hex(32)
    d, ident = store.reserve(
        candidates, scope=identity, expected_version=version,
        dispatch_name=host.get("dispatch_name"), report_hash=hash_worker_token(token),
        owner=_owner_from_host(host, identity),
    )
    lease = d["leases"][ident]
    item = _item_details(board).get(lease["item_id"], {})
    # Pin semantic approval independently of the board's global CAS version.
    # If sidecar persistence fails, release the still-unbound reservation: an
    # execution must never start without a durable recovery/validation record.
    coord = coordination(store)
    try:
        root = _root_for(board, lease["item_id"])
        approval = _reconcile_root(coord, board, root["id"], identity)
        cstate = coord.read()
        workspace_manifest = None
        coord.record_assignment(identity, ident, {
            "item_id": lease["item_id"], "root_id": root["id"],
            "priority": lease["priority"], "role": lease["role"], "generation": 1,
            "approval_generation": approval["generation"], "assigned_todos": [lease["item_id"]],
            "fingerprint": approval["fingerprint"], "workspace": workspace_manifest,
            "created_at": time.time(),
        }, expected_version=cstate["version"])
    except Exception as error:
        current = store.read()
        if current["leases"].get(ident, {}).get("host_id") is None:
            store.release_unbound(ident, proof="coordination persistence failed: " + type(error).__name__,
                                  expected_version=current["version"])
        raise WorkParallelError("coordination persistence failed; reservation released") from error
    workspace = None
    if host.get("workspace_repo"):
        from .work_parallel_workspace import prepare
        try:
            workspace = prepare(host["workspace_repo"], ident)
            d, _ = store.attach_workspace(ident, workspace, expected_version=d["version"])
            cstate = coord.read()
            coord.attach_workspace(ident, {key: workspace[key] for key in ("path", "branch", "head")},
                                   expected_version=cstate["version"])
        except Exception as error:
            current = store.read()
            if current["leases"].get(ident, {}).get("host_id", 0) is None:
                store.release_unbound(ident, proof="workspace preparation failed: " + type(error).__name__,
                                      expected_version=current["version"])
            raise WorkParallelError("workspace preparation failed; reservation released: "
                                    + (str(error) or type(error).__name__)[:300]) from error
    return {"lease_id": ident, "version": d["version"], "plan_version": board["version"],
            "workspace": workspace, "worker_token": token, "priority": lease["priority"],
            "item_id": lease["item_id"], **item}


def _review_facts(store, identity, lease):
    """Require durable completion plus a fresh positive idle workspace snapshot."""
    coord = coordination(store)
    cstate = coord.read()
    run_row = cstate.get("runs", {}).get(lease["id"])
    if run_row is None or run_row.get("exit", {}).get("kind") != "completion":
        raise WorkParallelError("durable completion receipt required before main review")
    if (lease["state"] != "submitted" or lease["slot"] or lease.get("idle_proof") is None
            or not lease.get("workspace")):
        raise WorkParallelError("positive idle workspace observation required before main review")
    detail = coord.detail(run_row["exit"]["digest"])
    from .work_parallel_workspace import review_observe
    observed = review_observe(lease["workspace"]["repo"], lease["id"],
                              generation=str(run_row["generation"]),
                              artifact=lease["artifact"], completion_receipt=run_row["exit"]["digest"],
                              host_id=lease["host_id"], idle_proof=lease["idle_proof"])
    receipt_workspace = detail.get("workspace")
    if isinstance(receipt_workspace, dict):
        for key in ("path", "branch", "head", "dirty"):
            if receipt_workspace.get(key) != observed.get(key):
                raise WorkParallelError("stale completion workspace snapshot; result retained")
    return {"lease_id": lease["id"], "scope": identity, "item_id": lease["item_id"],
            "root_id": run_row["root_id"], "priority": lease["priority"],
            "plan_version": lease["plan_version"], "generation": run_row["generation"],
            "artifact": lease["artifact"], "completion_receipt": run_row["exit"]["digest"],
            "fingerprint": run_row["fingerprint"], "summary": detail["summary"],
            "coverage": copy.deepcopy(detail["coverage"]), "verification": detail["verification"],
            "workspace": observed}


def _revalidate_review(store, board_store, row):
    """Artifact, approval fingerprint and workspace snapshot must be unchanged."""
    lease = store.read()["leases"].get(row["lease_id"])
    if (lease is None or lease["state"] != "submitted" or lease["slot"]
            or lease["artifact"] != row["artifact"] or lease["plan_version"] != row["plan_version"]):
        raise WorkParallelError("stale artifact: reviewed result is no longer the submitted result")
    run_row = coordination(store).read().get("runs", {}).get(row["lease_id"])
    if (run_row is None or (run_row.get("exit") or {}).get("digest") != row["completion_receipt"]
            or run_row["fingerprint"] != row["fingerprint"]):
        raise WorkParallelError("stale completion receipt or approval fingerprint")
    if not _approval_current(store, board_store.read(), lease):
        raise WorkParallelError("stale approval fingerprint: Work approval scope changed")
    from .work_parallel_workspace import review_observe
    observed = review_observe(lease["workspace"]["repo"], lease["id"],
                              generation=str(row["generation"]), artifact=row["artifact"],
                              completion_receipt=row["completion_receipt"],
                              host_id=lease["host_id"], idle_proof=lease["idle_proof"])
    if observed["snapshot_digest"] != row["workspace"]["snapshot_digest"]:
        raise WorkParallelError("stale workspace: HEAD or tree changed since review projection")


def _publish_pending_review(store, identity, lease):
    facts = _review_facts(store, identity, lease)
    reviews = finalization(store)
    state = reviews.read()
    reviews.pending(facts, expected_version=state["version"])


def _observe_idle_and_publish_review(store, identity, lease, proof):
    """Converge ledger/workspace idle proof, then idempotently publish review.

    The ledger and workspace are separate CAS domains. Another host observation
    may change the ledger proof between their writes. Re-read after every
    workspace write and retry with the ledger's current proof until they match.
    This also repairs an already-published review whose proofs diverged; pending
    is idempotent and never creates a second row.
    """
    require(lease["state"] == "submitted" and lease.get("workspace") is not None
            and text(proof), "submitted idle review observation required")
    if lease.get("idle_proof") != proof:
        d, _ = store.reconcile(lease["id"], host_state="idle", proof=proof,
                               expected_version=store.read()["version"])
        lease = d["leases"][lease["id"]]
    from .work_parallel_workspace import set_activity
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        current = store.read()["leases"].get(lease["id"])
        require(current is not None and current["state"] == "submitted"
                and current.get("workspace") is not None and text(current.get("idle_proof")),
                "submitted idle review observation changed")
        observed_proof = current["idle_proof"]
        set_activity(current["workspace"]["repo"], current["id"], current["host_id"],
                     idle=True, proof=observed_proof)
        latest = store.read()["leases"].get(current["id"])
        if latest is not None and latest.get("idle_proof") == observed_proof:
            _publish_pending_review(store, identity, latest)
            return latest
    raise WorkParallelConflict("parallel idle proof changed; reread")


def _archive_retry(coord, lease_id, summary, *, completion_receipt=None):
    for _ in range(4):
        state = coord.read()
        prior = next((row for row in reversed(state["archive"])
                      if row["run"]["lease_id"] == lease_id), None)
        if prior is not None:
            require(prior["summary"] == summary, "coordination archive outcome changed")
            return prior
        run = state["runs"].get(lease_id)
        try:
            if completion_receipt is not None and run is not None and run["state"] not in (
                    "terminal", "handed_off", "superseded"):
                # An older build could reopen a completed run. Only the exact
                # receipt the main review approved may restore it before archive.
                coord.restore_completion_terminal(lease_id, completion_receipt,
                                                  expected_version=state["version"])
                continue
            _, archived = coord.archive_terminal(lease_id, summary,
                                                 expected_version=state["version"])
            return archived
        except WorkParallelConflict:
            continue
    raise WorkParallelConflict("Work coordination changed; reread")


def _board_accept(board_store, item_id, artifact, evidence):
    """Idempotent board completion, independently CAS-protected by its store."""
    from . import work_plan_tree as plan_tree
    for _ in range(4):
        board = board_store.read()
        node = next((row for row in board["nodes"] if row["id"] == item_id), None)
        if node is None:
            raise WorkParallelError("review item no longer exists")
        if node["status"] == "done":
            require(artifact in str(node.get("evidence") or ""), "board item completed by another result")
            return board
        try:
            updated, _ = board_store.transaction(
                lambda doc: plan_tree.update_node(doc, item_id, status="done",
                    evidence=(evidence + " [artifact " + artifact + "]")[:plan_tree.MAX_TEXT_CHARS]),
                expected_version=board["version"])
            return updated
        except Exception as error:
            if "plan changed" not in str(error) and "version" not in str(error):
                raise
    raise WorkParallelConflict("Work plan changed; reread")


def request_rework_launch_manual(store, identity, lease_id, launch_ref, *, confirmed, expected_version):
    """Record a user-confirmed request for one exact launch. It never asserts absence.

    ``lease_id`` is the reviewed worker lease (the review row key the person
    sees); ``launch_ref`` is that row's current opaque launch reference. The
    host mod executes the request at its next pump boundary: it checks its own
    agent list, then sends rework_launch_manual_resolve or rework_launch_present.
    """
    from .work_parallel_finalization import launch_ref_valid
    if not (isinstance(lease_id, str) and 0 < len(lease_id) <= 200):
        raise WorkParallelError("exact rework lease_id is required")
    if not launch_ref_valid(launch_ref):
        raise WorkParallelError("exact rework launch reference required")
    if confirmed is not True:
        raise WorkParallelError("manual launch request requires the person's explicit confirmation")
    if type(expected_version) is not int or expected_version < 0:
        raise WorkParallelError("expected_finalization_version must be a nonnegative integer")
    state, launch = finalization(store).launch_manual_request(identity, lease_id, launch_ref, confirmed=True,
                                                              expected_version=expected_version)
    return {"lease_id": lease_id, "state": launch["state"], "attempt": launch["attempt"],
            "launch_ref": launch["launch_ref"], "finalization_version": state["version"]}


def _main_review(store, board_store, identity, host, args):
    """Run or resume the cross-store main review saga without a spanning lock."""
    if host.get("agent_id") is not None:
        raise WorkParallelError("main-session item review required")
    reviews = finalization(store)
    lease_id = args.get("lease_id")
    decision = args.get("decision")
    decision_id = args.get("decision_id")
    evidence = args.get("evidence")
    fstate = reviews.read()
    row = fstate["reviews"].get(lease_id)
    if row is None:
        lease = store.read()["leases"].get(lease_id)
        if not lease or lease["scope"] != identity:
            raise WorkParallelError("unknown conversation lease")
        _publish_pending_review(store, identity, lease)
        fstate = reviews.read()
        row = fstate["reviews"][lease_id]
    expected = args.get("expected_finalization_version")
    if row["decision"] is None:
        if expected != fstate["version"]:
            raise WorkParallelConflict("Work finalization changed; reread")
        if decision not in ("approve", "rework"):
            raise WorkParallelError("decision must be approve or rework")
        # Every fact main reviewed is re-proven before the decision is durable.
        # A stale fact leaves the result pending; nothing is committed.
        _revalidate_review(store, board_store, row)
        if decision == "rework":
            # A limit that can never clear is refused before the decision is
            # durable. Paused/capacity/rate/bound commit the decision and wait.
            from .work_parallel import REWORK_HARD_REFUSALS, rework_admission
            code, refusal = rework_admission(store.read(), lease_id, now=time.time())
            if code in REWORK_HARD_REFUSALS:
                raise WorkParallelError(refusal)

    replacement_id = row.get("replacement_lease_id")
    if decision == "rework" and replacement_id is None:
        replacement_id = hashlib.sha256((lease_id + chr(0) + str(decision_id)).encode()).hexdigest()[:32]
    from .work_parallel_finalization import decision_digest
    body_digest = decision_digest(lease_id, decision, evidence,
                                  args.get("findings"), args.get("rework_instructions"))
    fstate, row = reviews.decide(lease_id, decision, decision_id, evidence,
                                 decision_body_digest=body_digest,
                                 expected_version=fstate["version"],
                                 replacement_lease_id=replacement_id)
    return _roll_forward(store, board_store, identity, host, lease_id)


def _roll_forward(store, board_store, identity, host, lease_id):
    """Advance a durable decision from the journal alone.

    No caller argument reaches a stage: decision, evidence and replacement come
    from the stored row. A non-crash stage failure is recorded as ``blocked`` so
    the state is never silently stuck; the host pump calls this again.
    """
    reviews = finalization(store)
    fstate = reviews.read()
    row = fstate["reviews"].get(lease_id)
    require(row is not None and row["scope"] == identity and row["decision"] is not None,
            "no durable review decision to roll forward")
    try:
        return _roll_forward_stages(store, board_store, identity, host, lease_id)
    except ValueError as error:
        fstate = reviews.read()
        row = fstate["reviews"][lease_id]
        if row["state"] in ("finalized", "rework_ready"):
            raise
        fstate, row = reviews.block(lease_id, "stage_failed", str(error)[:300] or type(error).__name__,
                                    expected_version=fstate["version"])
        return {"decision": row["decision"], "lease_id": lease_id, "item_id": row["item_id"],
                "state": row["state"], "finalization_version": fstate["version"],
                "retry": "host_pump", "reason": row["blocked"]["message"]}


def _roll_forward_stages(store, board_store, identity, host, lease_id):
    reviews = finalization(store)
    fstate = reviews.read()
    row = fstate["reviews"][lease_id]
    decision = row["decision"]
    # Every replay uses the journaled immutable body, never retry arguments.
    evidence = row["evidence"]
    coord = coordination(store)

    if decision == "approve":
        if not row["stages"]["accepted"]:
            current = store.read()
            store.main_accept(lease_id, plan_version=row["plan_version"], artifact=row["artifact"],
                              evidence=evidence, expected_version=current["version"])
            fstate, row = reviews.mark(lease_id, "accepted", expected_version=reviews.read()["version"])
        if not row["stages"]["workspace"]:
            # The result is retained by default. Cleanup remains a separately
            # verified policy; retention is safe and is itself the proof.
            cleanup = "retained verified workspace " + row["workspace"]["path"] + " at " + row["workspace"]["head"]
            fstate, row = reviews.mark(lease_id, "workspace", expected_version=reviews.read()["version"])
        else:
            cleanup = "retained verified workspace " + row["workspace"]["path"] + " at " + row["workspace"]["head"]
        if not row["stages"]["board"]:
            _board_accept(board_store, row["item_id"], row["artifact"], evidence)
            fstate, row = reviews.mark(lease_id, "board", expected_version=reviews.read()["version"])
        if not row["stages"]["lease"]:
            current = store.read()
            store.finalize_accepted(lease_id, plan_version=row["plan_version"], artifact=row["artifact"],
                                    evidence=evidence, cleanup_proof=cleanup,
                                    expected_version=current["version"])
            fstate, row = reviews.mark(lease_id, "lease", expected_version=reviews.read()["version"])
        if not row["stages"]["archive"]:
            _archive_retry(coord, lease_id, {"state": "done", "outcome": "main accepted",
                                             "receipt_digest": row["completion_receipt"],
                                             "artifact_digest": row["artifact"], "published": False},
                           completion_receipt=row["completion_receipt"])
            fstate, row = reviews.mark(lease_id, "archive", state_value="finalized",
                                       expected_version=reviews.read()["version"])
        return {"decision": "approve", "lease_id": lease_id, "item_id": row["item_id"],
                "state": "finalized", "version": store.read()["version"],
                "finalization_version": fstate["version"]}

    if decision != "rework":
        raise WorkParallelError("decision must be approve or rework")
    if not row["stages"]["accepted"]:
        from .work_parallel import rework_admission
        current = store.read()
        if row["replacement_lease_id"] not in current["leases"]:
            code, refusal = rework_admission(current, lease_id, now=time.time())
            if code:
                # Admission is not available after the durable decision. The
                # submitted predecessor stays the duplicate fence; the host pump
                # retries through review_roll_forward. Never silently stuck.
                fstate, row = reviews.await_capacity(lease_id, code, refusal,
                                                     expected_version=reviews.read()["version"])
                return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                        "state": "awaiting_capacity", "finalization_version": fstate["version"],
                        "retry": "host_pump", "reason": refusal[:300]}
        store.reserve_rework(lease_id, row["replacement_lease_id"],
                             owner=_owner_from_host(host, identity), expected_version=current["version"])
        fstate, row = reviews.mark(lease_id, "accepted", expected_version=reviews.read()["version"])
    if not row["stages"]["workspace"]:
        old = store.read()["leases"][lease_id]
        from .work_parallel_workspace import handoff_rebind
        ws = row["workspace"]
        rebound = handoff_rebind(old["workspace"]["repo"], lease_id, row["replacement_lease_id"],
                                 prior_generation=str(row["generation"]),
                                 replacement_generation=str(row["replacement_generation"]),
                                 receipt_digest=ws["receipt_digest"], expected_path=ws["path"],
                                 expected_branch=ws["branch"], expected_head=ws["head"],
                                 expected_dirty=ws["dirty"], prior_terminal=True, prior_slot=False)
        current = store.read()
        store.attach_workspace(row["replacement_lease_id"], rebound, expected_version=current["version"])
        fstate, row = reviews.mark(lease_id, "workspace", expected_version=reviews.read()["version"])
    if not row["stages"]["board"]:
        current = coord.read()
        replacement_workspace = store.read()["leases"][row["replacement_lease_id"]]["workspace"]
        if row["replacement_lease_id"] not in current["runs"]:
            old_run = current["runs"].get(lease_id)
            require(old_run is not None and old_run.get("exit", {}).get("kind") == "completion",
                    "completion coordination record unavailable")
            coord.record_assignment(identity, row["replacement_lease_id"], {
                "item_id": row["item_id"], "root_id": row["root_id"],
                "priority": row["priority"], "role": "resume",
                "generation": row["replacement_generation"],
                "approval_generation": old_run["approval_generation"],
                "assigned_todos": copy.deepcopy(old_run["assigned_todos"]),
                "fingerprint": row["fingerprint"],
                "workspace": {key: replacement_workspace[key] for key in ("path", "branch", "head")},
                "created_at": time.time(),
            }, expected_version=current["version"])
        fstate, row = reviews.mark(lease_id, "board", expected_version=reviews.read()["version"])
    if not row["stages"]["lease"]:
        current = coord.read()
        if lease_id in current["runs"]:
            coord.supersede(lease_id, row["replacement_lease_id"], expected_version=current["version"])
        else:
            archived = next((entry for entry in reversed(current["archive"])
                             if entry["run"]["lease_id"] == lease_id), None)
            require(archived is not None and archived["run"].get("replaced_by") == row["replacement_lease_id"],
                    "replacement coordination outcome unknown")
        current_ledger = store.read()
        old_lease = current_ledger["leases"].get(lease_id)
        proof = "workspace rebound to resume lease " + row["replacement_lease_id"]
        if old_lease is not None and old_lease["state"] != "done":
            store.retire_reworked(lease_id, row["replacement_lease_id"], proof=proof,
                                  expected_version=current_ledger["version"])
        fstate, row = reviews.mark(lease_id, "lease", expected_version=reviews.read()["version"])
    if not row["stages"]["archive"]:
        fstate, row = reviews.mark(lease_id, "archive", state_value="rework_ready",
                                   expected_version=reviews.read()["version"])
    # Capability is minted only when authenticated host consumes dispatch intent.
    # Main receives neither token nor hash.
    return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
            "state": "rework_ready", "finalization_version": fstate["version"],
            "dispatch": {"kind": "native_resume", "lease_id": row["replacement_lease_id"],
                         "role": "resume", "generation": row["replacement_generation"],
                         "workspace": row["workspace"]["path"]}}


COORDINATION_BIND_ATTEMPTS = 8


def _run_bind_refusal(cstate, ident, agent):
    """True when an existing coordination run cannot take this agent."""
    run_row = cstate.get("runs", {}).get(ident)
    if run_row is None:
        return False
    bindable = run_row["state"] == "reserved" and run_row["agent_id"] is None
    taken = any(other["agent_id"] == agent and key != ident
                and other["state"] not in ("terminal", "superseded", "handed_off")
                for key, other in cstate["runs"].items())
    return not (bindable or run_row["agent_id"] == agent) or taken


def _bind_coordination_run(store, ident, agent, agent_name):
    """Bind the coordination half of a ledger-bound lease.

    The coordination file is device-wide, so any unrelated write (a native
    observation, receipt or diagnostic from any session) can move its version
    between our read and the CAS. Bindability is re-read on every attempt. An
    already-bound run for this agent and a missing (legacy) run are success.
    """
    coord = coordination(store)
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        cstate = coord.read()
        run_row = cstate.get("runs", {}).get(ident)
        if run_row is None or run_row["agent_id"] == agent:
            return
        if _run_bind_refusal(cstate, ident, agent):
            raise WorkParallelError("run is not bindable")
        try:
            coord.bind_run(ident, agent, agent_name or agent, expected_version=cstate["version"])
            return
        except WorkParallelConflict:
            continue
    # The ledger half is durable. A host retry of this bind takes the
    # idempotent branch, which completes the run; the mod retries this wording.
    raise WorkParallelConflict("Work coordination state changed; reread")


MAX_NATIVE_SKIPPED = 64
NATIVE_ROW_FIELDS = frozenset(("lease_id", "agent_id", "agent_name", "status"))


def _partition_native_rows(rows, leases):
    """Split host rows into ones naming this conversation's bound fork and the rest."""
    from .work_parallel_native import MAX_ROWS_PER_OBSERVATION, NATIVE_STATUSES
    if len(rows) > MAX_ROWS_PER_OBSERVATION:
        raise WorkParallelError("native rows")
    valid, skipped, seen = [], [], set()
    for row in rows:
        lease_id = row.get("lease_id") if type(row) is dict else None
        if type(row) is not dict or not set(row) <= NATIVE_ROW_FIELDS or not (
                {"lease_id", "agent_id", "status"} <= set(row)):
            reason = "native row fields"
        elif lease_id not in leases:
            reason = "native row names no lease of this conversation"
        elif leases[lease_id]["host_id"] is None or row["agent_id"] != leases[lease_id]["host_id"]:
            reason = "native row is not this lease's bound fork"
        elif row["status"] not in NATIVE_STATUSES:
            reason = "native status"
        elif row.get("agent_name") is not None and not (
                isinstance(row["agent_name"], str) and 0 < len(row["agent_name"]) <= 200
                and chr(0) not in row["agent_name"]):
            reason = "native agent name"
        elif lease_id in seen:
            reason = "duplicate native row"
        else:
            seen.add(lease_id)
            valid.append(row)
            continue
        skipped.append({"lease_id": lease_id[:200] if isinstance(lease_id, str) else None,
                        "reason": reason})
    return valid, skipped


def _observe_coordination_row(store, identity, lease, row):
    """Apply one observed row to coordination; never raise for one bad row.

    Returns "" when applied, "observed_terminal" for a run that already ended
    without its bind recorded, or a bounded reason for a skipped row.
    """
    coord = coordination(store)
    try:
        run_row = coord.read().get("runs", {}).get(lease["id"])
        if run_row is None:
            return ""
        if run_row["agent_id"] is None and run_row["state"] != "reserved":
            # The run left "reserved" (a receipt landed) before its bind was
            # recorded, so it can no longer be bound. The ledger and native list
            # agree on the agent; the run's own exit receipt is authoritative.
            return "observed_terminal"
        # A ledger bind whose coordination half was lost is completed from this
        # positive observation of the same durable agent.
        _bind_coordination_run(store, lease["id"], lease["host_id"],
                               lease.get("agent_name") or row.get("agent_name"))
        for attempt in range(3):
            try:
                coord.update_native(lease["id"], row["status"], expected_version=coord.read()["version"])
                return ""
            except WorkParallelConflict:
                if attempt == 2:
                    raise
    except WorkParallelError as error:
        _native_diagnostic(coord, identity, lease["priority"])
        return (str(error) or type(error).__name__)[:200]
    return ""


def _native_diagnostic(coord, identity, priority):
    """Best effort, bounded (capped list, consecutive repeats collapse)."""
    for _ in range(3):
        try:
            coord.diagnostic(identity, priority, "native_uncertain", expected_version=coord.read()["version"])
            return
        except WorkParallelConflict:
            continue
        except WorkParallelError:
            return


def _repair_lost_coordination_bind(store, lease):
    """Bind a still-reserved coordination run to the lease's ledger agent."""
    if lease.get("host_id") is None:
        return
    run_row = coordination(store).read().get("runs", {}).get(lease["id"])
    if run_row is not None and run_row["state"] == "reserved" and run_row["agent_id"] is None:
        _bind_coordination_run(store, lease["id"], lease["host_id"], lease.get("agent_name"))


RESTART_TERMINAL_PROOF = "Submitted completion fork absent from agent.list after restart"


def _restart_submitted_terminal(store, identity, lease_id, generation, receipt_digest,
                                absent_agent_id, absent_from_complete_list):
    """Publish a review after the current host proves this exact fork absent."""
    lease = store.read()["leases"].get(lease_id)
    require(lease is not None and lease["scope"] == identity and lease["state"] == "submitted"
            and lease["slot"] is False and lease.get("host_id") is not None
            and lease.get("workspace") is not None, "submitted restart lease required")
    require(absent_from_complete_list is True,
            "host must assert absence from its complete current agent list")
    require(absent_agent_id == lease["host_id"], "host must assert this exact submitted fork absent")
    require(integer(generation, 1) and digest(receipt_digest),
            "exact generation and completion receipt required")
    coord = coordination(store)

    def exact_run():
        run_row = coord.read().get("runs", {}).get(lease_id)
        require(run_row is not None and run_row["agent_id"] == absent_agent_id
                and run_row["generation"] == generation
                and (run_row["exit"] or {}) == {"kind": "completion", "digest": receipt_digest},
                "exact durable completion receipt required before a restart terminal observation")
        return run_row

    result = {"lease_id": lease_id, "generation": generation,
              "completion_receipt": receipt_digest, "observed_terminal": True}
    # A review with a recorded decision is no longer "pending" (validated
    # invariant: pending <=> decision is None). Its saga owns every later write:
    # approve restores a reopened run before archive, and rework may already
    # have rebound this workspace, which the old manifest then refuses. The
    # exact receipt is still checked; nothing is written.
    review = finalization(store).read()["reviews"].get(lease_id)
    if review is not None and review["decision"] is not None:
        exact_run()
        return {**result, "review_pending": False}
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        if exact_run()["state"] == "terminal":
            break
        try:
            coord.update_native(lease_id, "missing", expected_version=coord.read()["version"])
        except WorkParallelConflict:
            continue
    else:
        raise WorkParallelConflict("Work coordination changed; reread")
    # Enter the converging helper. With no review, this restart is the idle
    # observation. With a pending review, the ledger proof is kept and only the
    # workspace is converged to it: pending is idempotent, so a proof mismatch
    # is repaired without a second row or wake, and matched proofs write nothing.
    current = store.read()["leases"][lease_id]
    reviewed = lease_id in finalization(store).read()["reviews"]
    _observe_idle_and_publish_review(store, identity, current,
                                     current["idle_proof"] if reviewed else RESTART_TERMINAL_PROOF)
    return {**result, "review_pending": lease_id in finalization(store).read()["reviews"]}


def _launch_view(launch):
    """Host-facing launch result: the nonce is echoed only as a match flag."""
    view = {key: launch[key] for key in ("state", "dispatch_name", "attempt", "generation", "agent_id",
                                         "proof", "launch_ref", "updated_at")}
    view["audit"] = [{key: row[key] for key in ("kind", "dispatch_name", "attempt", "at", "detail")}
                     for row in launch["audit"][-5:]]
    return view


def _rework_review_for(store, lease):
    if lease["role"] != "resume":
        return None
    return next((row for row in finalization(store).read()["reviews"].values()
                 if row.get("replacement_lease_id") == lease["id"]), None)


def _check_rework_launch(store, lease, host):
    """Refuse a ledger bind before it commits unless the exact launch was observed."""
    row = _rework_review_for(store, lease)
    if row is None:
        return
    launch = row["launch"]
    agent = host.get("host_id")
    if not ((launch["state"] in ("spawn_observed", "bound") and launch["agent_id"] == agent)
            or (launch["state"] == "launch_intent" and host.get("agent_name") is not None
                and launch["dispatch_name"] == host.get("agent_name"))):
        raise WorkParallelError("rework launch intent was not observed for this agent")


def _bind_rework_launch(store, lease, host):
    """Mark a main-review rework launch bound; legacy resume leases are untouched.

    Finalization is a separate CAS domain. Any unrelated review/wake write can
    move its version after the read, so re-read on every bounded attempt. An
    identical replay for an already-bound launch is idempotent.
    """
    if lease["role"] != "resume":
        return
    reviews = finalization(store)
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        fstate = reviews.read()
        if not any(row.get("replacement_lease_id") == lease["id"]
                   for row in fstate["reviews"].values()):
            return
        try:
            reviews.launch_bound(lease["id"], host.get("host_id"), host.get("agent_name"),
                                 expected_version=fstate["version"])
            return
        except WorkParallelConflict:
            continue
    # The ledger and coordination halves may already be durable. This exact
    # wording is retried by parallelEvent; a later idempotent bind repairs the
    # finalization and workspace halves too.
    raise WorkParallelConflict("Work finalization state changed; reread")


def _orphan_preview(store):
    d = store.read()
    rows = []
    for lease in d.get("leases", {}).values():
        if not lease["slot"]:
            continue
        owner = lease.get("owner")
        if owner is None:
            state, observed = "legacy_no_owner", None
        elif owner.get("identity") is None:
            # A PID without its birth identity cannot prove either liveness or
            # death: the PID may have been reused or identity observation failed.
            state, observed = "unknown", process_identity(owner["session_pid"])
        else:
            observed = process_identity(owner["session_pid"])
            state = "unknown" if observed is None else ("dead" if observed == "" else
                    "live" if observed == owner["identity"] else "dead")
        rows.append({"lease_id": lease["id"], "scope": lease["scope"], "item_id": lease["item_id"],
                     "priority": lease["priority"], "state": lease["state"],
                     "owner_state": state, "observed_identity": observed})
    body = {"version": d.get("version", 0), "leases": rows}
    body["digest"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return body


def run(name, args, board_store, identity, config, save_config):
    store = ledger()
    if name == "work_parallel_status":
        # A model tool call carries no host token. Only the authenticated mod's
        # own read, with no worker identity, gets the host lease fields.
        return json.dumps(project(store, identity, _board_or_none(board_store),
                                  host_view=_authenticated_host_read(args, config)), ensure_ascii=False)
    host, ready = host_context(args, config)
    if not identity.startswith("claude:"):
        raise WorkParallelError("native adapter unsupported")
    actor = host.get("agent_id")
    version = args.get("expected_version")
    if name == "work_parallel_item_review":
        return json.dumps(_main_review(store, board_store, identity, host, args), ensure_ascii=False)
    if name == "work_parallel_configure":
        if actor is not None:
            raise WorkParallelError("workers cannot configure parallel policy")
        d, _ = store.configure(args.get("changes"), capability_ready=ready, expected_version=version)
        if save_config is not None:
            updated = dict(config)
            updated["work_parallel"] = d["policy"]
            save_config(updated)
        return json.dumps(project(store, identity, _board_or_none(board_store), host_view=_internal_host(host)),
                          ensure_ascii=False)
    if name == "work_discovery_pickup":
        if actor is not None:
            raise WorkParallelError("coordinator pickup required")
        from dataclasses import asdict
        from .work_discovery import discovery_hint
        from .work_discovery_pickup import ExternalSearchItem, ingest_external_search_results
        if not discovery_hint(config, tree.snapshot(board_store.read(), approval_ask=False)).get("enabled"):
            raise WorkParallelError("discovery disabled/paused")
        raw = args.get("results")
        if not isinstance(raw, list) or len(raw) > 100:
            raise WorkParallelError("bounded search results required")
        try:
            items = [ExternalSearchItem(**item) for item in raw]
        except (TypeError, ValueError) as error:
            raise WorkParallelError("invalid external search result schema") from error
        return json.dumps(asdict(ingest_external_search_results(board_store, source=args.get("source"),
                           scope=args.get("scope"), results=items)), ensure_ascii=False)
    if name != "work_parallel_event":
        raise WorkParallelError("dispatch requires packaged host interception")
    event = args.get("event")
    if event == "review_roll_forward":
        # Host pump retries a durable decision (awaiting_capacity or a blocked
        # stage) from the journal alone. Main never needs to resend its body.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal review host required")
        return json.dumps(_roll_forward(store, board_store, identity, host, args.get("lease_id")),
                          ensure_ascii=False)
    if event == "review_wake":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal review host required")
        reviews = finalization(store)
        operation = args.get("operation")
        current = reviews.read()
        if operation == "prepare":
            state, wake = reviews.wake_prepare(args.get("lease_id"), expected_version=current["version"])
        elif operation == "record":
            state, wake = reviews.wake_record(args.get("lease_id"), args.get("attempt_id"),
                                              args.get("state"), expected_version=current["version"])
        else:
            raise WorkParallelError("review wake operation must be prepare or record")
        return json.dumps({"lease_id": args.get("lease_id"), **wake,
                           "finalization_version": state["version"]}, ensure_ascii=False)
    if event in ("rework_launch_status", "rework_launch_manual_request"):
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal launch host required")
        if event == "rework_launch_status":
            return json.dumps(finalization(store).launch_status(identity, host.get("launch_nonce")),
                              ensure_ascii=False)
        return json.dumps(request_rework_launch_manual(
            store, identity, args.get("lease_id"), args.get("launch_ref"),
            confirmed=host.get("user_confirmed") is True,
            expected_version=finalization(store).read()["version"]), ensure_ascii=False)
    if event in ("rework_launch_intent", "rework_launch_denied", "rework_spawn_observed",
                 "rework_launch_absent", "rework_launch_manual_resolve", "rework_launch_present"):
        # The mod forwards a main-model work_parallel_event with only
        # _host.agent_id=null, which is also actor None. Launch facts change
        # whether a fresh worker capability may be minted, so they are accepted
        # only from the mod's own internal calls, which carry _host.internal.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal launch host required")
        reviews = finalization(store)
        current = reviews.read()
        replacement = store.read()["leases"].get(args.get("lease_id"))
        if replacement is None or replacement["scope"] != identity:
            raise WorkParallelError("unknown rework replacement")
        if event in ("rework_launch_absent", "rework_launch_manual_resolve"):
            asserted = host.get("absent_dispatch_name")
            if asserted is None or asserted != args.get("dispatch_name"):
                raise WorkParallelError("host must assert this exact launch name absent from its agent list")
            if event == "rework_launch_absent":
                state, launch = reviews.launch_absent(args.get("lease_id"), asserted, host.get("launch_nonce"),
                                                      absent_agent_id=host.get("absent_agent_id"),
                                                      expected_version=current["version"])
            else:
                state, launch = reviews.launch_manual(args.get("lease_id"), asserted, host.get("launch_nonce"),
                                                      absent_agent_id=host.get("absent_agent_id"),
                                                      launch_ref=args.get("launch_ref"),
                                                      expected_version=current["version"])
            return json.dumps({"lease_id": args.get("lease_id"), **_launch_view(launch),
                               "finalization_version": state["version"]}, ensure_ascii=False)
        if event == "rework_launch_present":
            asserted = host.get("present_dispatch_name")
            if asserted is None or asserted != args.get("dispatch_name"):
                raise WorkParallelError("host must assert this exact launch name present in its agent list")
            if host.get("present_agent_id") is None:
                raise WorkParallelError("present launch agent required")
            state, launch = reviews.launch_present(args.get("lease_id"), asserted, host.get("present_agent_id"),
                                                   launch_ref=args.get("launch_ref"),
                                                   expected_version=current["version"])
            return json.dumps({"lease_id": args.get("lease_id"), **_launch_view(launch),
                               "finalization_version": state["version"]}, ensure_ascii=False)
        if event == "rework_launch_intent":
            state, launch = reviews.launch_intent(args.get("lease_id"), args.get("dispatch_name"),
                                                  args.get("generation"), host_nonce=host.get("launch_nonce"),
                                                  expected_version=current["version"])
            # The exact name is durable on the reserved lease before any spawn.
            ledger_now = store.read()
            if ledger_now["leases"][args.get("lease_id")].get("dispatch_name") != launch["dispatch_name"]:
                store.name_reserved_resume(args.get("lease_id"), launch["dispatch_name"],
                                           expected_version=ledger_now["version"])
        elif event == "rework_launch_denied":
            state, launch = reviews.launch_denied(args.get("lease_id"), args.get("dispatch_name"),
                                                  host.get("proof") or args.get("proof"),
                                                  expected_version=current["version"])
        else:
            state, launch = reviews.spawn_observed(args.get("lease_id"), args.get("dispatch_name"),
                                                   args.get("agent_id"), expected_version=current["version"])
        return json.dumps({"lease_id": args.get("lease_id"), **_launch_view(launch),
                           "finalization_version": state["version"]}, ensure_ascii=False)
    if event == "rework_dispatch":
        # Host consumes a previously committed rework intent. Main never sees
        # this response: register.js intercepts it and injects the capability
        # only into the native resume assignment.
        if actor is not None or host.get("fork") is not True or not ready:
            raise WorkParallelError("verified coordinator fork capability required")
        replacement_id = args.get("lease_id")
        current = store.read()
        replacement = current["leases"].get(replacement_id)
        require(replacement is not None and replacement["scope"] == identity
                and replacement["role"] == "resume" and replacement["state"] == "reserved"
                and replacement["host_id"] is None, "unknown rework dispatch intent")
        review_state = finalization(store).read()
        source = next((row for row in review_state["reviews"].values()
                       if row.get("replacement_lease_id") == replacement_id
                       and row["state"] == "rework_ready"), None)
        require(source is not None, "rework finalization is not dispatch-ready")
        require(source["launch"]["state"] in ("none", "spawn_denied"),
                "rework launch intent outcome is unresolved; reconcile its dispatch name first")
        token = secrets.token_hex(32)
        d, _ = store.rotate_reserved_capability(replacement_id, hash_worker_token(token),
                                                expected_version=version)
        return json.dumps({"lease_id": replacement_id, "worker_token": token,
                           "plan_version": replacement["plan_version"], "item_id": replacement["item_id"],
                           "priority": replacement["priority"], "role": "resume",
                           "generation": source["replacement_generation"],
                           "workspace": replacement.get("workspace")}, ensure_ascii=False)
    if event in ("reserve", "reserve_next"):
        if actor is not None or host.get("fork") is not True or not ready:
            raise WorkParallelError("verified coordinator fork capability required")
        assignment = _reserve_assignment(
            store, board_store, identity, host, version,
            item_id=args.get("item_id") if event == "reserve" else None,
            role=args.get("role", "worker"),
        )
        return json.dumps(assignment, ensure_ascii=False)
    if event == "restart_submitted_terminal":
        # Host-only. After a process restart, a submitted lease whose worker
        # already committed its completion receipt, and whose exact bound fork
        # the new process cannot list, is observed terminal: forks live in the
        # process that spawned them. No slot is released (submission did that).
        # The idle workspace fact the review projection requires is recorded
        # and the review is published, so the result is never left unreviewable.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal restart host required")
        return json.dumps(_restart_submitted_terminal(
            store, identity, args.get("lease_id"), args.get("generation"), args.get("receipt_digest"),
            host.get("absent_agent_id"), host.get("absent_from_complete_list")), ensure_ascii=False)
    if event == "native_observation":
        # Host-only: the mod reports what $.agent.list() showed, outside any
        # model turn. Rows are checked against this conversation's own bound
        # leases, so neither main nor a side actor can be recorded as a fork.
        if actor is not None:
            raise WorkParallelError("coordinator host observation required")
        leases = {k: v for k, v in store.read()["leases"].items() if v["scope"] == identity}
        evidence = native_evidence(store)
        expected = args.get("expected_evidence_version")
        if type(expected) is not int:
            raise WorkParallelError("expected_evidence_version required")
        observed_rows = args.get("rows") or []
        if type(observed_rows) is not list:
            raise WorkParallelError("native rows")
        # Each row is judged alone. A row that does not name this
        # conversation's bound fork is never recorded, but it no longer
        # discards the healthy rows observed with it. A batch with no valid
        # row at all is still refused and writes nothing.
        valid, skipped = _partition_native_rows(observed_rows, leases)
        if skipped and not valid:
            raise WorkParallelError("native observation names no bound fork of this conversation: "
                                    + skipped[0]["reason"])
        # A skipped row hides its lease from this list, so it is not complete.
        state, changed = evidence.observe(identity, leases, valid,
                                          complete=args.get("complete") is True and not skipped,
                                          expected_version=expected)
        observed_terminal = []
        for row in valid:
            outcome = _observe_coordination_row(store, identity, leases[row["lease_id"]], row)
            if outcome == "observed_terminal":
                observed_terminal.append(row["lease_id"])
            elif outcome:
                skipped.append({"lease_id": row["lease_id"], "reason": outcome})
        return json.dumps({"evidence_version": state["version"], "changed": changed,
                           "skipped": skipped[:MAX_NATIVE_SKIPPED],
                           "observed_terminal": observed_terminal[:MAX_NATIVE_SKIPPED]}, ensure_ascii=False)
    if event == "host_salvage":
        if actor is not None:
            raise WorkParallelError("coordinator host salvage required")
        d = store.read()
        lease = d["leases"].get(args.get("lease_id"))
        if not lease or lease["scope"] != identity or not lease.get("workspace"):
            raise WorkParallelError("unknown salvage workspace")
        from .work_parallel_workspace import salvage_observe
        coord = coordination(store)
        _repair_lost_coordination_bind(store, lease)
        run_row = coord.read().get("runs", {}).get(lease["id"])
        if run_row is None:
            raise WorkParallelError("missing salvage coordination record")
        # Native terminal status is positive exit evidence; the lease may still
        # hold its slot until this salvage receipt is committed.
        observed = salvage_observe(lease["workspace"]["repo"], lease["id"],
                                   generation=str(run_row["generation"]), terminal=True,
                                   slot=False, config_dir=None)
        workspace = {"path": observed["path"], "branch": observed["branch"],
                     "head": observed["head"], "dirty": observed["dirty"],
                     "changed_files": observed["changed_files"], "diff_stat": observed["diff_stat"] or "no tracked diff"}
        detail = {"done": [], "remaining": list(run_row.get("assigned_todos") or [lease["item_id"]]),
                  "blockers": [str(args.get("reason") or "native fork ended without report")[:2000]],
                  "workspace": workspace, "next_step": "Resume the recorded assignment from this verified workspace."}
        cstate = coord.read()
        _, receipt = coord.persist_receipt(lease["id"], "host_salvage", detail,
                                           expected_version=cstate["version"])
        return json.dumps({"lease_id": lease["id"], "receipt_digest": receipt,
                           "salvaged": True}, ensure_ascii=False)
    if event == "reconcile_orphans":
        if actor is not None:
            raise WorkParallelError("coordinator required")
        preview = _orphan_preview(store)
        if args.get("operation") in (None, "preview"):
            return json.dumps(preview, ensure_ascii=False)
        if args.get("operation") != "apply" or args.get("preview_digest") != preview["digest"]:
            raise WorkParallelError("stale or invalid orphan preview")
        row = next((item for item in preview["leases"] if item["lease_id"] == args.get("lease_id")), None)
        if row is None:
            raise WorkParallelError("unknown preview lease")
        observation = dict(args.get("observation") or {})
        observation.setdefault("owner_state", row["owner_state"])
        observation.setdefault("observed_identity", row["observed_identity"])
        d, _ = store.orphan_release(row["lease_id"], proof=host.get("proof"), observation=observation,
                                    expected_version=version)
        current = d["leases"].get(row["lease_id"])
        return json.dumps({"lease_id": row["lease_id"], "version": d["version"],
                           "state": current["state"] if current else "released",
                           "released": current is None}, ensure_ascii=False)
    d = store.read()
    lease = d["leases"].get(args.get("lease_id"))
    if not lease or lease["scope"] != identity:
        raise WorkParallelError("unknown conversation lease")
    ident = lease["id"]
    if event in ("bind", "reconcile", "release", "wait", "reacquire", "unreported"):
        if actor is not None:
            raise WorkParallelError("coordinator host observation required")
        if event == "unreported":
            d, _ = store.terminate_unreported(ident, proof=host.get("proof"), expected_version=version)
            return json.dumps({"lease_id": ident, "version": d["version"], "state": "unreported",
                               "slot_released": True, "item_kept": True}, ensure_ascii=False)
        if event == "wait":
            d, _ = store.wait(ident, proof=host.get("proof"), expected_version=version)
            return json.dumps({"lease_id": ident, "version": d["version"], "waiting": True,
                               "slot_released": True, "item_id": lease["item_id"], "item_kept": True}, ensure_ascii=False)
        if event == "reacquire":
            d, _ = store.reacquire(ident, expected_version=version)
            return json.dumps({"lease_id": ident, "version": d["version"], "slot_reacquired": True,
                               "item_id": lease["item_id"]}, ensure_ascii=False)
        if event == "release":
            d, _ = store.release_unbound(ident, proof=host.get("proof"), expected_version=version)
            coord = coordination(store)
            if ident in coord.read().get("runs", {}):
                cstate = coord.read()
                coord.cancel_reserved(ident, outcome="native launch never started",
                                      expected_version=cstate["version"])
            return json.dumps({"lease_id": ident, "version": d["version"], "released": True,
                               "item_id": lease["item_id"], "item_kept": True}, ensure_ascii=False)
        if event == "bind" and lease["host_id"] is not None and lease["host_id"] == host.get("host_id"):
            # Idempotent replay after a lost response. It also completes a
            # coordination run left reserved by an interrupted earlier bind.
            _bind_coordination_run(store, ident, lease["host_id"],
                                   host.get("agent_name") or lease.get("agent_name"))
            _bind_rework_launch(store, lease, host)
        elif event == "bind":
            _check_rework_launch(store, lease, host)
            agent = host.get("host_id")
            # Check the coordination run before the ledger commit, so a conflict
            # never leaves the ledger bound and the run unbound. A lease minted by
            # an older build has no run at all; like release, it binds ledger-only.
            if _run_bind_refusal(coordination(store).read(), ident, agent):
                raise WorkParallelError("run is not bindable")
            d, _ = store.bind(ident, agent, expected_version=version,
                              agent_name=host.get("agent_name"))
            _bind_coordination_run(store, ident, agent, host.get("agent_name"))
            _bind_rework_launch(store, lease, host)
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
            if lease["role"] == "reviewer":
                raise WorkParallelError("reviewer reports are retired; main reviews completed results")
        elif event in ("question", "result"):
            raise WorkParallelError("worker host identity required")
        board = board_store.read()
        if not _approval_current(store, board, lease):
            raise WorkParallelError("Work approval scope changed; reconcile before continuing")
        if event == "accept" and lease["role"] != "reviewer":
            reviewer = d["leases"].get(args.get("reviewer_lease_id"))
            if (not reviewer or reviewer["scope"] != identity or reviewer["role"] != "reviewer"
                    or reviewer["host_id"] == lease["host_id"] or reviewer["item_id"] != lease["item_id"]
                    or reviewer["plan_version"] != lease["plan_version"] or reviewer["state"] != "submitted"):
                raise WorkParallelError("independent reviewer result required before acceptance")
            d, _ = store.accept_with_reviewer(ident, reviewer["id"], plan_version=args.get("plan_version"),
                                               artifact=args.get("artifact"), evidence=args.get("evidence"),
                                               expected_version=version)
            return json.dumps(project(store, identity, _board_or_none(board_store),
                                      host_view=_internal_host(host)), ensure_ascii=False)
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
    current = d["leases"].get(ident, {})
    workspace = current.get("workspace")
    if workspace and event in ("bind", "reconcile"):
        from .work_parallel_workspace import bind, set_activity
        if event == "bind":
            bind(workspace["repo"], ident, current["host_id"])
        elif host.get("state") == "idle":
            set_activity(workspace["repo"], ident, current["host_id"], idle=True, proof=current["idle_proof"])
    projected = None
    if event == "reconcile" and host.get("state") == "idle" and current.get("state") == "submitted":
        # The idle workspace write above is the positive observation that the
        # review projection requires. The reconcile itself is already durable;
        # an incomplete review fact is reported, then retried on the next idle
        # observation or by the main review call. The restart observation uses
        # this same readiness path (_observe_idle_and_publish_review).
        try:
            _observe_idle_and_publish_review(store, identity, current, current["idle_proof"])
        except ValueError as error:
            projected = project(store, identity, _board_or_none(board_store), host_view=_internal_host(host))
            projected["review_publication_error"] = str(error)[:300]
    if projected is None:
        projected = project(store, identity, _board_or_none(board_store), host_view=_internal_host(host))
    return json.dumps(projected, ensure_ascii=False)


def dispatch_refusal(board, item_id):
    """Explain structured gates; never interpret wait prose as permission."""
    nodes = {n["id"]: n for n in board["nodes"]}
    current = nodes[item_id]
    while True:
        if current["status"] in ("done", "blocked"):
            return f"Work item {item_id} cannot dispatch: node {current['id']} is {current['status']}"
        if current.get("waiting_on"):
            return (f"Work item {item_id} cannot dispatch: node {current['id']} has waiting_on: "
                    f"{current['waiting_on']}. Clear a resolved wait explicitly; keep restrictions "
                    "in notes or on the specific waiting child, not on a runnable branch.")
        if current["parent_id"] is None:
            if tree.root_phase(board, current["id"]) != "executing":
                return f"Work item {item_id} cannot dispatch: goal {current['id']} is not approved for execution"
            return ""
        current = nodes[current["parent_id"]]


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
    if lease.get("workspace"):
        from .work_parallel_workspace import observe, remove
        binding = lease["workspace"]
        if target.resolve() != Path(binding["path"]).resolve():
            raise WorkParallelError("workspace is not owned by this lease")
        record = observe(binding["repo"], lease["id"])
        return remove(binding["repo"], lease["id"], host_id=lease["host_id"], active=lease["slot"],
                      idle_proof=lease["idle_proof"], expected_head=record["head"],
                      approved_target=host.get("approved_target"),
                      remote_ref=remote_ref or "refs/heads/" + record["branch"])
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
