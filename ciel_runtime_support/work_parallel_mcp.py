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
from .work_parallel import (WorkParallelStore, WorkParallelError, active_by_priority, digest,
                            hash_worker_token, worker_token_matches)
from .workspace_router_selection import _process_identity as process_identity

TOOL_NAMES = ("work_parallel_status", "work_parallel_configure", "work_parallel_run",
              "work_parallel_event", "work_parallel_report", "work_discovery_pickup")


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
    }
    descriptions = {
        "work_discovery_pickup": "Register caller-verified external open search items by priority/latest, with durable dedup and cooldown; never approves execution. Requires source, scope, results. Each result: source,item_id,priority,created_at,title,link,status,scope.",
        "work_parallel_status": "Show Work 병렬 실행 settings, device capacity and this conversation's leases. Read before changing limits.",
        "work_parallel_configure": "On the user's natural-language request, persist Work 병렬 실행 limits (concurrent executing fork sessions, reserved P1 slot, launch rate, rework limit) or pause. This never limits registering or queueing Work items. Pass changes and expected_version from work_parallel_status. Does not approve a plan.",
        "work_parallel_run": "Dispatch an approved Work item: the packaged host starts the fork itself and returns the started lease/agent. You do not call the Agent tool or supply a name for this dispatch. Requires item_id, priority, role and expected_version. Codex is unsupported until its native adapter passes capability tests.",
        "work_parallel_event": "Coordinator lifecycle and authenticated host observations for Work forks. Workers report through work_parallel_report.",
        "work_parallel_report": "Worker-only question/completion/handoff/review report. Requires the exact lease_id and one-use worker_token. Completion needs artifact+evidence; handoff needs a host-stored detail_digest. A successful report releases the fork slot; no session_id or host token.",
    }
    schemas = []
    for name in TOOL_NAMES:
        schema = {"type": "object", "properties": copy.deepcopy(props)}
        if name == "work_parallel_report":
            schema["required"] = ["lease_id", "worker_token", "event"]
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


def _fork_projection(store, identity, leases, details):
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
               "slot": lease["slot"], "agent_name": lease.get("agent_name") or lease.get("dispatch_name"),
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


def project(store, identity, board=None):
    d = store.read()
    all_mine = {k: v for k, v in d["leases"].items() if v["scope"] == identity}
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
        item = details.get(source["item_id"], {})
        row.update(item_title=_clip(item.get("item_title"), MODEL_TEXT_LIMIT),
                   item_ref=_clip(item.get("item_ref"), 100),
                   goal_id=item.get("goal_id", ""),
                   goal_title=_clip(item.get("goal_title"), MODEL_TEXT_LIMIT),
                   item_path=[_clip(value, 100) for value in (item.get("item_path") or [])[:6]])
        leases[ident] = row
    forks = _fork_projection(store, identity, all_mine, details)
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
    return {"version": d["version"], "policy": d["policy"],
            "active_device": sum(r["slot"] for r in d["leases"].values()),
            "active_by_priority": active_by_priority(d["leases"]),
            "leases": leases, "lease_counts": state_counts,
            "leases_truncated": len(open_rows) > MODEL_LEASE_LIMIT,
            "forks": forks, "coordination": coordination_summary,
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
    if lease["state"] != "running" or not lease["slot"]:
        raise WorkParallelError("worker lease is not active")
    board = board_store.read()
    if not _approval_current(store, board, lease):
        raise WorkParallelError("Work approval scope changed; reconcile before continuing")
    evidence = args.get("evidence")
    artifact = args.get("artifact")
    coord = coordination(store)
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
        return json.dumps(project(store, identity, _board_or_none(board_store)), ensure_ascii=False)
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
        return json.dumps(project(store, identity, _board_or_none(board_store)), ensure_ascii=False)
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
    if event in ("reserve", "reserve_next"):
        if actor is not None or host.get("fork") is not True or not ready:
            raise WorkParallelError("verified coordinator fork capability required")
        assignment = _reserve_assignment(
            store, board_store, identity, host, version,
            item_id=args.get("item_id") if event == "reserve" else None,
            role=args.get("role", "worker"),
        )
        return json.dumps(assignment, ensure_ascii=False)
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
        state, changed = evidence.observe(identity, leases, observed_rows,
                                          complete=args.get("complete") is True, expected_version=expected)
        coord = coordination(store)
        for row in observed_rows:
            if row.get("lease_id") not in coord.read().get("runs", {}):
                continue
            for attempt in range(3):
                cstate = coord.read()
                try:
                    coord.update_native(row["lease_id"], row["status"], expected_version=cstate["version"])
                    break
                except WorkParallelError as error:
                    if attempt == 2 or "changed; reread" not in str(error):
                        raise
        return json.dumps({"evidence_version": state["version"], "changed": changed}, ensure_ascii=False)
    if event == "host_salvage":
        if actor is not None:
            raise WorkParallelError("coordinator host salvage required")
        d = store.read()
        lease = d["leases"].get(args.get("lease_id"))
        if not lease or lease["scope"] != identity or not lease.get("workspace"):
            raise WorkParallelError("unknown salvage workspace")
        from .work_parallel_workspace import salvage_observe
        coord = coordination(store)
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
            pass  # Idempotent: the same observed native agent was already bound.
        elif event == "bind":
            d, _ = store.bind(ident, host.get("host_id"), expected_version=version,
                              agent_name=host.get("agent_name"))
            coord = coordination(store)
            cstate = coord.read()
            try:
                coord.bind_run(ident, host.get("host_id"), host.get("agent_name") or host.get("host_id"),
                               expected_version=cstate["version"])
            except WorkParallelError:
                # An identical bind may have committed before a lost response.
                row = coord.read().get("runs", {}).get(ident)
                if not row or row.get("native_id") != host.get("host_id"):
                    raise
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
            return json.dumps(project(store, identity, _board_or_none(board_store)), ensure_ascii=False)
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
    return json.dumps(project(store, identity, _board_or_none(board_store)), ensure_ascii=False)


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
