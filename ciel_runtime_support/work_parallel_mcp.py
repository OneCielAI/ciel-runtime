"""Conversation-scoped projection over the device-wide Work execution ledger."""
from __future__ import annotations
import copy
import hashlib
import json
import hmac
import re
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
        "role": {"type": "string", "enum": ["worker", "resume"]},
        "expected_version": {"type": "integer", "minimum": 0},
        "projection": {"type": "string", "enum": ["version"],
                       "description": "Host only: version returns the ledger version alone."},
        "changes": {"type": "object"}, "event": {"type": "string"},
        "artifact": {"type": "string"}, "evidence": {"type": "string"},
        "plan_version": {"type": "integer", "minimum": 0},
        "cleanup_proof": {"type": "string"},
        "workspace": {"type": "string"}, "remote_ref": {"type": "string"},
        "detail_digest": {"type": "string"}, "detail": {"type": "object"},
        "related_lease_id": {"type": "string"},
        "worker_token": {"type": "string"}, "operation": {"type": "string"},
        "preview_digest": {"type": "string"}, "observation": {"type": "object"},
        "source": {"type": "string"}, "scope": {"type": "string"}, "results": {"type": "array", "items": {"type": "object"}},
        "rows": {"type": "array", "items": {"type": "object"}}, "complete": {"type": "boolean"},
        "expected_evidence_version": {"type": "integer", "minimum": 0},
        "expected_finalization_version": {"type": "integer", "minimum": 0},
        "expected_review_pin": {"type": "string", "pattern": "^rp1:[0-9a-f]{64}$"},
        "decision": {"type": "string", "enum": ["approve", "rework"]},
        "decision_id": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "rework_instructions": {"type": "string"},
        "attempt_id": {"type": "string"}, "dispatch_name": {"type": "string"},
        "generation": {"type": "integer", "minimum": 1}, "agent_id": {"type": "string"},
        "state": {"type": "string"}, "proof": {"type": "string"},
        "names": {"type": "array", "maxItems": 64,
                  "items": {"type": "string", "maxLength": 200}},
        "ids": {"type": "array", "maxItems": 64,
                "items": {"type": "string", "maxLength": 200}},
    }
    descriptions = {
        "work_discovery_pickup": "Register caller-verified external open search items by priority/latest, with durable dedup and cooldown; never approves execution. Requires source, scope, results. Each result: source,item_id,priority,created_at,title,link,status,scope.",
        "work_parallel_status": "Show Work 병렬 실행 settings, device capacity and this conversation's leases. Read before changing limits.",
        "work_parallel_configure": "On the user's natural-language request, persist Work 병렬 실행 limits (concurrent fork sessions per priority, launch rate, rework limit) or pause. This never limits registering or queueing Work items. Pass changes and expected_version from work_parallel_status. Does not approve a plan.",
        "work_parallel_run": "Dispatch an approved Work item: the packaged host starts the fork itself and returns the started lease/agent. You do not call the Agent tool or supply a name for this dispatch. Requires item_id, priority, role and expected_version. Codex is unsupported until its native adapter passes capability tests.",
        "work_parallel_event": "Coordinator lifecycle and authenticated host observations for Work forks. Main in Claude can request recover_submitted_review with the exact lease_id; the local host verifies native termination before publishing review. Workers report through work_parallel_report.",
        "work_parallel_report": "Worker-only question/completion/handoff report. Requires the exact lease_id and one-use worker_token. Completion needs artifact+evidence; handoff needs a host-stored detail_digest. A successful report releases the fork slot; no session_id or host token.",
        "work_parallel_item_review": "Main-session only decision for a bounded pending completed item. Requires lease_id, decision (approve|rework), decision_id, evidence and expected_finalization_version. The host authenticates main; workers and forks are denied. Rework returns a host dispatch intent and never exposes a worker token to main.",
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


def attachments(store=None):
    """Cross-process Work host ownership, in a sidecar 57dbfb3 never names."""
    from .work_parallel_attachments import WorkParallelAttachments
    return WorkParallelAttachments.beside((store or ledger()).path)


def coordination(store=None):
    """Host-owned assignment/receipt state kept out of the legacy lease schema."""
    from .work_parallel_receipts import coordination_store
    return coordination_store(store or ledger())


def finalization(store=None):
    """Main-review journal kept outside the old-reader execution ledger."""
    from .work_parallel_finalization import finalization_store
    return finalization_store(store or ledger())


def stranded(store=None):
    """Stranded-result journal that builds through 57dbfb3 never read."""
    from .work_parallel_stranded import stranded_store
    return stranded_store(store or ledger())


def p0_state(store=None):
    """P0 root/launch state ignored by builds through 57dbfb3."""
    from .work_parallel_p0 import p0_store
    return p0_store(store or ledger())


def _root_for(board, item_id):
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    current = nodes.get(item_id)
    if current is None:
        raise WorkParallelError("unknown Work item")
    while current.get("parent_id") is not None:
        current = nodes[current["parent_id"]]
    return current


def parked_wait_cleared(board, item_id):
    """True when nothing on the board holds a parked worker's item now (Stage 3).

    Clear: the item is unfinished, its goal's phase is executing, and neither
    the item nor any ancestor has waiting_on or the blocked status. Anything
    unknown is not clear, so the host never resumes on a guess. This alone is
    not "the wait was cleared": a worker question parks the lease without
    recording waiting_on (``_worker_event``), so see ``_parked_wait_marks``.
    """
    nodes = {node["id"]: node for node in (board or {}).get("nodes", [])}
    current = nodes.get(item_id)
    if current is None or current.get("status") == "done":
        return False
    for _ in range(tree.MAX_DEPTH + 1):
        if str(current.get("waiting_on") or "").strip() or current.get("status") == "blocked":
            return False
        parent = current.get("parent_id")
        if parent is None:
            return current.get("status") != "done" and tree.root_phase(board, current["id"]) == "executing"
        current = nodes.get(parent)
        if current is None:
            return False
    return False


PARKED_WAITS_NAME = "work-parallel-parked-waits.json"


def _parked_chain_wait(board, item_id):
    """The first recorded wait or block on a parked item's chain, or None."""
    nodes = {node["id"]: node for node in (board or {}).get("nodes", [])}
    current = nodes.get(item_id)
    for _ in range(tree.MAX_DEPTH + 1):
        if current is None:
            return None
        if str(current.get("waiting_on") or "").strip() or current.get("status") == "blocked":
            return {"node": current["id"], "waiting_on": str(current.get("waiting_on") or "blocked")[:200]}
        current = nodes.get(current.get("parent_id")) if current.get("parent_id") is not None else None
    return None


def _parked_wait_marks(store, board, parked):
    """Lease ids whose chain was seen with a recorded wait while parked.

    A worker that asked a question parks with no waiting_on on the board, and
    it waits for main's answer, not for the board. Only a wait the host has
    seen recorded on the item's chain while the lease was parked, and that is
    now gone, lets the host resume it on its own (Stage 3, 2026-11-17). The
    marks live beside the ledger and are pruned to the leases parked now.
    ``parked`` maps lease id -> item id. Any failure marks nothing.
    """
    from .durable_file_lock import exclusive_file_lock
    path = Path(store.path).with_name(PARKED_WAITS_NAME)
    with exclusive_file_lock(path):
        try:
            marks = json.loads(path.read_text(encoding="utf-8")).get("leases", {})
        except (OSError, ValueError, AttributeError):
            marks = {}
        marks = {key: value for key, value in marks.items() if key in parked and isinstance(value, dict)}
        changed = False
        for lease_id, item_id in parked.items():
            wait = _parked_chain_wait(board, item_id)
            if wait is not None and lease_id not in marks:
                marks[lease_id] = {**wait, "item_id": item_id, "seen_at": time.time()}
                changed = True
        try:
            stored = json.loads(path.read_text(encoding="utf-8")).get("leases", {})
        except (OSError, ValueError, AttributeError):
            stored = None
        if changed or stored != marks:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(json.dumps({"leases": marks}, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
    return set(marks)


def _parked_notice_claim(store, lease_id, *, release=False):
    """True for the one caller that may tell main this parked worker needs SendMessage."""
    from .durable_file_lock import exclusive_file_lock
    path = Path(store.path).with_name(PARKED_WAITS_NAME)
    with exclusive_file_lock(path):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            marks = doc.get("leases", {})
        except (OSError, ValueError, AttributeError):
            return False
        mark = marks.get(lease_id)
        if not isinstance(mark, dict) or (not release and mark.get("notified_at")):
            return False
        if release:
            mark.pop("notified_at", None)
        else:
            mark["notified_at"] = time.time()
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps({"leases": marks}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    return True


def workflow_gate(board, root_id):
    from .work_workflow import gate
    return gate(board, root_id)


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


def _p0_runnable_leaves(store, board, identity):
    """Planning-safe runnable leaves of active exact P0 roots, oldest first."""
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    children = {node_id: [] for node_id in nodes}
    for node in nodes.values():
        if node.get("parent_id") in children:
            children[node["parent_id"]].append(node["id"])
    try:
        side = p0_state(store)
        side_state = side.read()
    except Exception:
        # Corrupt P0 state is displayed by status and conservatively schedules P1.
        return []
    rows = []
    for root in board.get("nodes", []):
        if root.get("parent_id") is not None or root.get("status") == "done":
            continue
        if side.matching_active_marker(
                side_state, identity, root["id"], root.get("created_at")) is None:
            continue
        if board.get("workflows") and workflow_gate(board, root["id"]):
            # Approval workflow: a side-registered P0 also waits for the
            # proposal and the verified approval (spec 2, 5.1).
            continue
        stack = [root["id"]]
        while stack:
            node_id = stack.pop()
            child_ids = children.get(node_id, [])
            if child_ids:
                stack.extend(reversed(child_ids))
                continue
            node = nodes[node_id]
            ancestors = [node]
            while ancestors[-1].get("parent_id") is not None:
                ancestors.append(nodes[ancestors[-1]["parent_id"]])
            if all(row.get("status") not in ("done", "blocked")
                   and not row.get("waiting_on") for row in ancestors):
                rows.append({"item_id": node_id, "root_id": root["id"],
                             "created_at": node.get("created_at", node.get("updated_at", 0))})
    rows.sort(key=lambda row: (-float(row["created_at"]), row["item_id"]))
    return rows


def _p0_next_rows(store, board, identity, *, include_refused=True):
    """P0 leaves a reservation may take now, in dispatch order.

    A leaf that already holds an open lease is not a candidate, except the one
    launch a crash left unbound (reserved or spawning intent, no fork yet), which
    the next reserve replays. Before 2026-11-18 the newest P0 leaf stayed first
    while its fork ran or its result awaited review, so every later reserve of
    both lanes was refused and no other fork started. Goals whose last launch was
    refused go last, so one failing goal does not hold the others back, and the
    ordinary lane skips them (``include_refused=False``): its dispatch goes on.
    """
    from .work_parallel import ACTIVE_STATES
    from .work_parallel_p0_outcomes import P0Outcomes, outcome_key
    rows = [row for row in _p0_runnable_leaves(store, board, identity)
            if not _stranded_item_pending(store, identity, row["item_id"])]
    if not rows:
        return rows
    side_doc = p0_state(store).read()
    replayable = {row["dispatch_name"] for row in side_doc["intents"].values()
                  if row["scope"] == identity and row["state"] in ("intent", "reserved", "spawning")}
    fenced = {lease["item_id"] for lease in store.read()["leases"].values()
              if lease["scope"] == identity and lease["role"] in ("worker", "resume")
              and lease["state"] in ACTIVE_STATES
              and not (lease.get("dispatch_name") in replayable and lease.get("host_id") is None)}
    rows = [row for row in rows if row["item_id"] not in fenced]
    saved = P0Outcomes(store.path).read()["roots"]
    nodes = {node["id"]: node for node in board.get("nodes", [])}
    side = p0_state(store)

    def refused(row):
        root = nodes[row["root_id"]]
        marker = side.matching_active_marker(side_doc, identity, row["root_id"], root.get("created_at")) or {}
        key = outcome_key(identity, row["root_id"], marker.get("set_at") or 0)
        return (saved.get(key) or {}).get("refusal") is not None
    return sorted(rows, key=refused) if include_refused else [row for row in rows if not refused(row)]


def _p0_outcome_key(store, board, identity, item_or_root):
    """(outcome key, root id) of the active P0 marker over this item, or None."""
    from .work_parallel_p0_outcomes import outcome_key
    root = _root_for(board, item_or_root)
    side = p0_state(store)
    marker = side.matching_active_marker(side.read(), identity, root["id"], root.get("created_at"))
    return None if marker is None else (outcome_key(identity, root["id"], marker["set_at"]), root["id"])


def _p0_note_refusal(store, board, identity, item_id, code, detail):
    """Advisory: the exact reason a P0 launch did not happen. Never blocks the refusal."""
    from .work_parallel_p0_outcomes import P0Outcomes
    try:
        found = _p0_outcome_key(store, board, identity, item_id)
        if found is not None:
            P0Outcomes(store.path).note_refusal(found[0], item_id, code, detail)
    except Exception:  # noqa: BLE001 - the launch refusal itself still reaches the caller
        pass


def _p0_clear_refusal(store, board, identity, item_id):
    from .work_parallel_p0_outcomes import P0Outcomes
    try:
        found = _p0_outcome_key(store, board, identity, item_id)
        if found is not None:
            P0Outcomes(store.path).clear_refusal(found[0])
    except Exception:  # noqa: BLE001
        pass


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


EVIDENCE_GROUPS = ("confirmed", "waiting", "reservation", "uncertain", "validating", "ended", "legacy_reviewer")
# A reviewer lease minted by 4eebfdc/5c91ea8 is never current work: it is shown
# in its own group until a legacy accept consumes it (then it is ended).
GROUP_LABELS = {"legacy_reviewer": "Legacy reviewer (57dbfb3, read-only, draining)"}
MODEL_LEASE_LIMIT = 24
MODEL_ENDED_LIMIT = 8
MODEL_TEXT_LIMIT = 180
MAX_ENDED_REVIEWS = 20

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
    hidden = {row["replacement_lease_id"] for row in reviews
              if row["scope"] == identity and row["decision"] == "rework"
              and row["replacement_lease_id"] is not None and row["launch"]["state"] != "bound"}
    try:
        hidden |= {row["replacement_lease_id"] for row in stranded(store).read()["rows"].values()
                   if row["scope"] == identity and row["replacement_lease_id"] is not None
                   and row["launch"]["state"] != "bound"}
    except Exception:
        leases = store.read()["leases"]
        hidden |= {key for key, lease in leases.items()
                   if lease.get("scope") == identity and lease.get("role") == "resume"}
    return frozenset(hidden)


def _fork_projection(store, identity, leases, details, hidden=frozenset(), *, host_view=True):
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
    # A P0 lease keeps the goal's canonical priority (P1); P0 lives in the P0
    # sidecar. Rows say which one runs as P0 (Celly 2026-11-17 saw P0 as P1).
    from .work_parallel import _p0_active_lease_ids
    p0_leases = _p0_active_lease_ids(store.path)
    for lease in sorted(leases.values(), key=lambda r: (r["priority"], r.get("created_at") or 0, r["id"])):
        record = evidence["records"].get(lease["id"])
        if record is not None and record.get("scope") != identity:
            record = None
        group = classify(lease, record, evidence["scans"].get(identity), now=now)
        legacy_reviewer = lease["role"] == "reviewer"
        if legacy_reviewer and group != "ended":
            group = "legacy_reviewer"
        item = details.get(lease["item_id"], {})
        row = {"lease_id": lease["id"], "item_id": lease["item_id"],
               "item_title": _clip(item.get("item_title"), 300), "item_ref": _clip(item.get("item_ref"), 100),
               "goal_title": _clip(item.get("goal_title"), 300),
               "item_path": [_clip(t, 120) for t in (item.get("item_path") or [])[:8]],
               "priority": lease["priority"], "role": lease["role"], "state": lease["state"],
               "effective_priority": "P0" if lease["id"] in p0_leases else lease["priority"],
               "slot": lease["slot"],
               "agent_name": None if lease["id"] in hidden or (legacy_reviewer and not host_view)
               else (lease.get("agent_name") or lease.get("dispatch_name")),
               # Main never uses a legacy reviewer's fork identity.
               "agent_id": None if legacy_reviewer and not host_view else lease.get("host_id"),
               "rework": lease["rework"],
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
            "group_labels": dict(GROUP_LABELS),
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


# The gate's confinement read: a missing key is denied, None is unconfined.
_NO_WORKSPACE_PATH = object()


def _host_workspace_path(lease):
    """A lease's absolute worktree path, None when it has no workspace binding.

    The router admits a reservation without a workspace (no workspace_repo
    from the host): such a lease is unconfined. A recorded binding whose path
    is not an absolute path is no confinement fact; the row omits the key.
    """
    workspace = lease.get("workspace")
    if workspace is None:
        return None
    path = workspace.get("path") if isinstance(workspace, dict) else None
    if isinstance(path, str) and 0 < len(path) <= 2000 and chr(0) not in path and Path(path).is_absolute():
        return path
    return _NO_WORKSPACE_PATH


def project(store, identity, board=None, *, host_view=False, caller_nonce=None, config=None):
    from .work_parallel import MAX_LEASES
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
    p0_doc = None
    if host_view:
        try:
            p0_doc = p0_state(store).read()
        except Exception:
            p0_doc = None
    allow = ("id", "item_id", "priority", "role", "state", "slot", "host_id",
             "agent_name", "dispatch_name", "plan_version", "artifact", "rework")
    # The host view lists every open lease, so a bound fork beyond the model's
    # cap is never read as unbound by the host. Parked leases (waiting for an
    # answer or for review) are outside MAX_LEASES (Stage 3, 2026-11-17), so the
    # host list is not cut at MAX_LEASES either; the ledger's byte bound limits it.
    lease_limit = max(MAX_LEASES, len(open_rows)) if host_view else MODEL_LEASE_LIMIT
    for ident, source in open_rows[:lease_limit]:
        row = {key: source.get(key) for key in allow if source.get(key) is not None}
        if host_view:
            # Host-only: the Work fork gate confines a bound fork's file writes
            # to this path. The model's own status never carries it.
            workspace_path = _host_workspace_path(source)
            if workspace_path is not _NO_WORKSPACE_PATH:
                row["workspace_path"] = workspace_path
            # Host-only: a parked worker the mod may resume on its own lease
            # (wait_cleared below, once every row is projected).
            if source["state"] == "waiting_input" and not source.get("slot") and board is not None:
                try:
                    row["wait_clear_now"] = parked_wait_cleared(board, source["item_id"])
                except Exception:
                    row["wait_clear_now"] = False
            if p0_doc is not None and board is not None:
                intent_rows = [value for value in p0_doc["intents"].values()
                               if value["lease_id"] == ident
                               and value["state"] in ("reserved", "spawning", "bound")
                               and value["scope"] == identity
                               and value["item_id"] == source["item_id"]]
                if len(intent_rows) == 1:
                    intent = intent_rows[0]
                    try:
                        root = _root_for(board, source["item_id"])
                        marker = p0_state(store).matching_active_marker(
                            p0_doc, identity, root["id"], root.get("created_at"))
                        new_fork_name = (intent["continuation"] == "reacquire"
                                         or source.get("dispatch_name") == intent["dispatch_name"])
                        if (marker is not None and intent["root_id"] == root["id"]
                                and intent["root_created_at"] == root.get("created_at")
                                and new_fork_name):
                            row.update(effective_priority="P0",
                                       p0_intent_id=intent["intent_id"],
                                       p0_continuation=intent["continuation"],
                                       planning_write_authorized=True,
                                       authorization="p0_dispatch_without_approval")
                    except WorkParallelError:
                        pass
        if ident in hidden:
            row.pop("dispatch_name", None)
            row.pop("agent_name", None)
        if source["role"] == "reviewer" and not host_view:
            for key in ("host_id", "agent_name", "dispatch_name"):
                row.pop(key, None)
        item = details.get(source["item_id"], {})
        row.update(item_title=_clip(item.get("item_title"), MODEL_TEXT_LIMIT),
                   item_ref=_clip(item.get("item_ref"), 100),
                   goal_id=item.get("goal_id", ""),
                   goal_title=_clip(item.get("goal_title"), MODEL_TEXT_LIMIT),
                   item_path=[_clip(value, 100) for value in (item.get("item_path") or [])[:6]])
        leases[ident] = row
    if host_view and board is not None:
        parked = {ident: row["item_id"] for ident, row in leases.items() if "wait_clear_now" in row}
        try:
            seen = _parked_wait_marks(store, board, parked) if parked else set()
        except Exception:
            seen = set()
        for ident in parked:
            leases[ident]["wait_seen"] = ident in seen
            leases[ident]["wait_cleared"] = leases[ident]["wait_clear_now"] and ident in seen
    forks = _fork_projection(store, identity, all_mine, details, hidden, host_view=host_view)
    state_counts = {}
    for row in all_mine.values():
        state_counts[row["state"]] = state_counts.get(row["state"], 0) + 1
    try:
        receipts = coordination(store).projection(identity, limit=MODEL_LEASE_LIMIT, host_view=host_view)
        if not host_view:
            reviewers = {key for key, row in all_mine.items() if row["role"] == "reviewer"}
            for run in receipts["runs"]:
                if run.get("lease_id") in reviewers:
                    run["agent_id"] = None
                    run["agent_name"] = None
        coordination_summary = {"version": receipts["version"],
            "runs": receipts["runs"], "receipts": receipts["receipts"],
            "truncated": receipts["truncated"]}
    except Exception:
        coordination_summary = {"version": None, "assignments": [], "runs": [], "receipts": [], "truncated": False}
    try:
        reviews_store = finalization(store)
        review_summary = reviews_store.projection(identity, limit=MODEL_LEASE_LIMIT)
        raw_reviews = reviews_store.read()["reviews"]
        side_store = stranded(store)
        try:
            _observe_supersessions(store, identity)
        except WorkParallelConflict:
            pass  # read-time derivation below still applies
        side_state = side_store.read()
        side_rows = side_state["rows"]
        # One row per item for main: an active stranded-v1 row hides the
        # schema-5 row of the same lease, and a completed rework/recovery hides
        # once its replacement generation has a row of its own in either journal.
        ledger_now = store.read()
        superseding = {key: _superseding_lease(ledger_now, row) for key, row in side_rows.items()
                       if row["scope"] == identity and row["state"] in ("intent", "pending")}
        active_side = {key for key, row in side_rows.items()
                       if row["scope"] == identity and row["state"] not in ("done", "intent")}
        merged = []
        for row in review_summary["pending"]:
            side = side_rows.get(row["lease_id"])
            if side is not None and side["scope"] == identity and (
                    row["lease_id"] in active_side or side["decision_id"] is not None):
                # A stranded row (active, or decided and recovered) owns this
                # lease; the schema-5 row it was raised from is never decided.
                continue
            replacement = side_rows.get(row.get("replacement_lease_id") or "")
            if (row["state"] == "rework_ready" and replacement is not None
                    and replacement["generation"] == row.get("replacement_generation")):
                continue
            raw = raw_reviews[row["lease_id"]]
            row["updated_at"] = raw["updated_at"]
            if raw.get("decision") is None:
                from .work_parallel_finalization import review_pin
                row["review_pin"] = review_pin(raw)
            merged.append(row)
        ended = []
        for key in active_side:
            source = side_rows[key]
            if source["state"] == "recovered" and (source["replacement_lease_id"] in raw_reviews
                                                   or source["replacement_lease_id"] in side_rows):
                continue
            other = superseding.get(key)
            if source["state"] == "pending" and other is not None:
                source = {**source, "state": "superseded",
                          "superseded": {"lease_id": other["id"], "generation": 1,
                                         "observed_at": source["updated_at"],
                                         "digest": _supersede_digest(source, other)}}
            if source["state"] == "superseded":
                # Ended audit row: always listed (newest MAX_ENDED_REVIEWS), with
                # the superseding lease's current ledger state, whatever it is. It
                # is never decidable, so it never displaces a decidable row.
                winner = ledger_now["leases"].get(source["superseded"]["lease_id"])
                view = _stranded_view(source)
                view["stranded"]["superseding_state"] = winner["state"] if winner is not None else "absent"
                ended.append(view)
                continue
            merged.append(_stranded_view(source))
        merged.sort(key=lambda row: (row["updated_at"], row["lease_id"]), reverse=True)
        ended.sort(key=lambda row: (row["stranded"]["observed_at"], row["lease_id"]), reverse=True)
        review_summary = {"version": review_summary["version"], "stranded_version": side_state["version"],
                          "pending": merged[:MODEL_LEASE_LIMIT],
                          "truncated": review_summary["truncated"] or len(merged) > MODEL_LEASE_LIMIT,
                          # Main sees only how many ended rows exist (never decidable).
                          "ended_count": len(ended)}
        if host_view:
            # Ended/audit state is for the host/operator UI only. A model's own
            # work_parallel_status never receives this key or its rows.
            review_summary["ended"] = ended[:MAX_ENDED_REVIEWS]
        all_reviews = review_summary["pending"] + review_summary.get("ended", [])
        review_details = _item_details(board) if board is not None and all_reviews else {}
        approval_state = coordination(store).read() if review_summary["pending"] else {}
        for row in review_summary["pending"]:
            if row.get("state") != "superseded":
                # The goal's scope was edited after this result's assignment;
                # main decides it anyway (user 2026-11-19). Read-only.
                row["approval_changed"] = _approval_changed(approval_state, board, row["lease_id"])
        for row in all_reviews:
            row.pop("updated_at", None)
            item = review_details.get(row["item_id"], {})
            row.update(item_title=_clip(item.get("item_title"), MODEL_TEXT_LIMIT),
                       item_ref=_clip(item.get("item_ref"), 100),
                       goal_title=_clip(item.get("goal_title"), MODEL_TEXT_LIMIT),
                       item_path=[_clip(value, 100) for value in (item.get("item_path") or [])[:6]])
            if row["state"] == "superseded":
                # Ended audit row: no workspace, wake, launch, controls or reason.
                row["coverage_count"] = 0
                row["changed_before_bind"] = "not_applicable"
                continue
            # Saga identities and the repo root stay host-side; main inspects
            # the recorded worktree path, branch, HEAD and bounded diff only.
            ws = row["workspace"]
            if "stranded" not in row and ws is not None:
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
            own = raw_reviews.get(row["lease_id"]) or side_rows.get(row["lease_id"]) or {}
            predecessor = next((candidate for candidate in raw_reviews.values()
                                if candidate.get("replacement_lease_id") == row["lease_id"]), None)
            side_predecessor = next((candidate for candidate in side_rows.values()
                                     if candidate["replacement_lease_id"] == row["lease_id"]), None)
            if own.get("generation") == 1 or side_predecessor is not None:
                # First generation, or a recovery that starts in a fresh worktree:
                # there is no retained handoff to compare.
                row["changed_before_bind"] = "not_applicable"
            else:
                row["changed_before_bind"] = (_launch_handoff_drift(predecessor["launch"])
                                              if predecessor is not None else "unknown")
            row["blocked"] = None if row["blocked"] is None else {
                "code": row["blocked"]["code"], "message": _clip(row["blocked"]["message"], 300)}
    except Exception:
        review_summary = {"version": None, "pending": [], "truncated": False}
    view = {"version": d["version"], "policy": d["policy"],
            "active_device": sum(r["slot"] for r in d["leases"].values()),
            "active_by_priority": active_by_priority(d["leases"]),
            "leases": leases, "lease_counts": state_counts,
            "leases_truncated": len(open_rows) > lease_limit,
            "forks": forks, "coordination": coordination_summary, "review": review_summary,
            "legacy_reviews": _legacy_reviews(d, identity, details),
            "device": _device_projection(d, identity),
            "runtime": "claude" if identity.startswith("claude:") else "codex",
            "codex_adapter": "unsupported",
            "limitations": ["dispatch is limited by concurrent fork slots and the launch rate, not by provider usage observations",
                            "external discovery requires a configured authenticated source connector"],
            "wait_boost": _wait_boost_status(store, identity, board)}
    if host_view:
        # The Work pane and /work-mode parallel show the same durable rows and
        # server texts as web, REST and ctl. Never in a model's own status.
        try:
            from .work_parallel_failures import public_failures
            view["parallel_failures"] = public_failures(store.path, identity, limit=10)["failures"]
        except (OSError, ValueError):
            view["parallel_failures"] = None
    if board is not None:
        try:
            side = p0_state(store)
            p0_view = side.read_view()
            roots = []
            for root in board.get("nodes", []):
                if root.get("parent_id") is not None:
                    continue
                effective = p0_view.effective_root_priority(identity, root)
                roots.append({"root_id": root["id"], "title": _clip(root.get("title", ""), 200),
                              "canonical_priority": root.get("priority", ""),
                              "effective_priority": effective})
            view["root_priorities"] = roots
        except Exception:
            view["root_priorities"] = []
    try:
        p0_goals = []
        view["priority_diagnosis"] = priority_diagnosis(store, identity, board, ledger_document=d,
                                                        caller_nonce=caller_nonce, goals_out=p0_goals)
        if host_view:
            view["p0_goals"] = p0_goals
    except Exception:
        view["priority_diagnosis"] = None
    if config is not None:
        try:
            # Per-priority Work-fork and side-fork models (P0-P5, side), resolved
            # against the current provider's list; ciel-work-gate applies them at
            # each fork model request. Model ids only, no credentials.
            from .work_fork_models import projection as fork_model_projection
            view["fork_models"] = fork_model_projection(config)
        except Exception:
            view["fork_models"] = {"provider": "", "models": {}, "status": {}, "unavailable": True}
        if isinstance(board, dict) and board.get("workflows"):
            try:
                # Approval workflow escalation: a step main moved to another model.
                from .work_workflow_parallel import model_overrides
                view["fork_models"]["overrides"] = model_overrides(board, view["leases"], config)
            except Exception:
                pass
    if isinstance(board, dict) and board.get("workflows"):
        try:
            from .work_workflow import projection as workflow_projection
            summary = workflow_projection(board)
            view["workflow"] = {"counts": summary["counts"], "todo": summary["todo"][:10],
                                "awaiting_approval": summary["awaiting_approval"][:20]}
        except Exception:
            view["workflow"] = None
    if host_view:
        # G5, display only: the mod's last live probe of the Work fork gate.
        from .work_parallel_gate_presence import fork_gate_view
        view.update(fork_gate_view(store.path, identity))
        # R2 B2: host-only process-attachment summary and per-lease owner
        # classification. The model projection receives neither field.
        view["session_attachments"] = attachments(store).summary(identity)
        classes = _owner_classes(store, list(view["leases"]), caller_nonce)
        for lease_id, lease_view in view["leases"].items():
            # Same launcher (one claude.exe at a time) reads as own for display.
            owner_class = classes[lease_id]
            lease_view["owner_process"] = "own" if owner_class == "earlier_child" else owner_class
    return view


# A durable rework, or a stranded recovery, whose owner is not provable now:
# nothing is reserved until that conversation's own host rolls it forward.
OWNER_OFFLINE = "owner_offline"
OWNER_OFFLINE_TEXT = "rework queued: waiting for the conversation to resume"
STRANDED_WAIT_CODES = ("paused", "duplicate_assignment", "capacity", "lease_bound", OWNER_OFFLINE)


def _stranded_view(source):
    """The public review shape of a stranded-v1 row; no placeholder is a fact."""
    pin = None
    if source.get("decision_id") is None and source.get("state") != "superseded":
        from .work_parallel_stranded import review_pin
        pin = review_pin(source)
    if source["state"] == "superseded":
        outcome = source["superseded"]
        return {"lease_id": source["lease_id"], "item_id": source["item_id"], "root_id": source["root_id"],
                "priority": source["priority"], "artifact": None, "completion_receipt": None,
                "summary": "Stranded result superseded by another execution of this Work item.",
                "coverage": [], "verification": "Not decidable: another execution owns this item.",
                "state": "superseded", "decision": None, "evidence": None, "blocked": None,
                "updated_at": source["updated_at"],
                "stranded": {"kind": source["kind"], "phase": "superseded", "approve": "refused",
                             "rework": "fresh_workspace", "counts_against_rework_limit": False,
                             "superseding_lease_id": outcome["lease_id"],
                             "observed_at": outcome["observed_at"]}}
    no_report = source["kind"] == "no_report"
    blocked = source["blocked"]
    if source["state"] == "pending":
        state = "pending"
        blocked = {"code": source["kind"], "message": source["reason"]}
    elif source["state"] == "recovered":
        state = "rework_ready"
    elif blocked is not None and blocked["code"] in STRANDED_WAIT_CODES:
        state = "awaiting_capacity"
    else:
        state = "deciding"
    binding = source["binding"]
    return {"lease_id": source["lease_id"], "item_id": source["item_id"], "root_id": source["root_id"],
            "priority": source["priority"], **({"review_pin": pin} if pin is not None else {}),
            "artifact": None if no_report else source["artifact"],
            "completion_receipt": None if no_report else source["completion_receipt"],
            "fingerprint": source["fingerprint"], "summary": source["summary"],
            "coverage": copy.deepcopy(source["coverage"]), "verification": source["verification"],
            "workspace": {"path": binding["path"], "branch": binding["branch"], "head": binding["head"],
                          "unavailable": True, "reason": source["reason"]},
            "state": state, "decision_id": source["decision_id"],
            "decision": None if source["decision_id"] is None else "rework",
            "evidence": source["evidence"], "replacement_lease_id": source["replacement_lease_id"],
            "replacement_generation": source["replacement_generation"],
            "stages": copy.deepcopy(source["stages"]), "wake": copy.deepcopy(source["wake"]),
            "launch": copy.deepcopy(source["launch"]), "blocked": copy.deepcopy(blocked),
            "updated_at": source["updated_at"],
            "stranded": {"kind": source["kind"], "reason": source["reason"], "phase": source["state"],
                         "approve": "refused", "rework": "fresh_workspace",
                         "counts_against_rework_limit": False}}


def public_projection(projected, identity):
    """Browser-safe /parallel: evidence groups and aggregates, no raw leases."""
    return {"session_id": identity, "version": projected["version"],
            "forks": projected["forks"], "device": projected["device"],
            "policy": {k: projected["policy"][k] for k in ("enabled", "paused", "priority_limits",
                                                          "launches_per_minute", "max_rework")},
            "runtime": projected["runtime"], "limitations": projected["limitations"],
            "wait_boost": projected.get("wait_boost"),
            "fork_models": projected.get("fork_models"),
            "root_priorities": projected.get("root_priorities", []),
            "priority_diagnosis": projected.get("priority_diagnosis")}


def _wait_lane_order(store, board, identity, rows):
    """Boosted launches first and parked slot holds; sidecar trouble falls back
    to the ordinary order and never blocks scheduling."""
    try:
        from .work_parallel import _p0_active_lease_ids, active_by_priority
        from .work_wait_schedule import WaitLane, held_out
        lane = WaitLane(store.path)
        ledger_document = store.read()
        lane.reconcile(board, ledger_document, identity)
        ordered, holds, _ = lane.order(rows, identity, ledger_document, board)
        if not holds:
            return ordered, []
        counts = active_by_priority(ledger_document["leases"],
                                    excluded_lease_ids=_p0_active_lease_ids(store.path))
        return held_out(ordered, holds, counts, ledger_document["policy"]["priority_limits"])
    except Exception:
        return rows, []


def _wait_lane_consume(store, identity, lease, *, kind, outcome, ledger_before, ledger_after):
    """Consume the item's boost after the ledger commit that used it."""
    try:
        from .work_wait_schedule import WaitLane
        return WaitLane(store.path).consume(identity, lease["item_id"], lease, kind=kind, outcome=outcome,
                                            ledger_before=ledger_before, ledger_after=ledger_after)
    except Exception:
        return False


def priority_diagnosis(store, identity, board, *, ledger_document=None, now=None, caller_nonce=None,
                       goals_out=None):
    """Per-priority dispatch diagnosis shared by status, menu, web, REST and ctl.

    Read-only: the wait-lane holds are read without reconciling the sidecar.
    Any unreadable sidecar degrades to "no hold / no P0", never to a failure.
    ``goals_out`` receives the host-only P0 goal rows (titles, refusal detail).
    """
    from .work_parallel import (MAX_LEASES, _p0_active_lease_ids, bounded_open_leases, lease_bound_reached)
    from .work_parallel_diagnosis import diagnose
    from .work_parallel_p0_outcomes import P0Outcomes, goal_rows, public_row
    d = ledger_document if ledger_document is not None else store.read()
    p0_roots, p0_runnable, p0_markers = set(), set(), {}
    if board is not None:
        try:
            side = p0_state(store)
            side_state = side.read()
            for root in board.get("nodes", []):
                if root.get("parent_id") is None and root.get("status") != "done":
                    marker = side.matching_active_marker(side_state, identity, root["id"], root.get("created_at"))
                    if marker is not None:
                        p0_markers[root["id"]] = marker
            p0_roots = set(p0_markers)
            p0_runnable = {row["item_id"] for row in _p0_runnable_leaves(store, board, identity)}
        except Exception:
            p0_roots, p0_runnable, p0_markers = set(), set(), {}
    holds = {}
    if board is not None:
        try:
            from .work_wait_schedule import WaitLane
            _, holds, _ = WaitLane(store.path).order([], identity, d, board)
        except Exception:
            holds = {}
    try:
        stranded_items = {row["item_id"] for row in stranded(store).read()["rows"].values()
                          if row["scope"] == identity and row["decision_id"] is None
                          and row["state"] in ("intent", "pending")}
    except Exception:
        stranded_items = set()
    goals = []
    if p0_markers:
        try:
            mine = [key for key, row in d["leases"].items() if row.get("scope") == identity]
            goals = goal_rows(board, identity, p0_markers, d["leases"], runnable=p0_runnable,
                              stranded_items=stranded_items, outcomes=P0Outcomes(store.path).read(),
                              owner_classes=_owner_classes(store, mine, caller_nonce),
                              bound=lease_bound_reached(d["leases"]))
        except Exception:
            goals = []
    if goals_out is not None:
        goals_out.extend(goals)
    return diagnose(d, board, identity, p0_roots=p0_roots, p0_runnable=p0_runnable,
                    p0_active_ids=_p0_active_lease_ids(store.path), stranded_items=stranded_items,
                    holds=holds, now=time.time() if now is None else now, max_leases=MAX_LEASES,
                    bounded_open=bounded_open_leases(d["leases"]), p0_goals=[public_row(row) for row in goals])


def _wait_boost_status(store, identity, board=None):
    """One allowlisted wait-boost summary shared by menu, web, REST and ctl."""
    from .work_wait_surfaces import project_wait_status
    try:
        from .work_parallel_wait_boost import WaitBoostStore
        document = WaitBoostStore(
            Path(store.path).with_name("work-parallel-wait-boost-v1.json")).read()
    except Exception:
        return project_wait_status({"eligible": False, "reason": "unavailable"})
    mine = [row for row in document["rows"].values() if row["scope"] == identity]
    active = [row for row in mine if row["state"] in ("boosted", "consuming")]
    if not active:
        return project_wait_status({"eligible": False, "reason": "none"})
    try:
        from .work_wait_schedule import parked_lease
        ledger_document = store.read()
        parked = [row for row in active if row["state"] == "boosted"
                  and parked_lease(row, ledger_document) is not None]
    except Exception:
        ledger_document, parked = None, []
    # Parked reacquire ranks before other released waits (design: Scheduling order).
    pool = parked or active
    first = min(pool, key=lambda row: (-row["release_at"], row["release_receipt"], row["item_id"]))
    if first["state"] == "consuming":
        behavior = "consuming"
    elif parked:
        behavior = "parked_first_eligible"
    else:
        behavior = "launch_first_eligible"
    lease = parked_lease(first, ledger_document) if parked else None
    return project_wait_status({
        "eligible": True, "behavior": behavior, "reason": None,
        "ordinary_allowed": not parked, "item_id": first["item_id"],
        "title": next((node.get("title") for node in (board or {}).get("nodes", [])
                       if node.get("id") == first["item_id"]), None),
        "root_id": first["root_id"], "priority": lease["priority"] if lease else None,
    })


def _p0_key_required(state):
    """Any nonterminal intent or open send depends on the existing key."""
    from .work_parallel_p0 import OPEN_INTENT_STATES, SEND_OPEN_STATES
    return (any(row["state"] in OPEN_INTENT_STATES for row in state["intents"].values())
            or any(row["state"] in SEND_OPEN_STATES for row in state["sends"].values()))


def _p0_key_ready(store, action):
    """Read-only key validation; never creates or rotates."""
    from .work_p0_assignment_key import P0AssignmentKeyStore, P0AssignmentKeyUnavailable
    try:
        return P0AssignmentKeyStore.beside_ledger(store.path).read()
    except P0AssignmentKeyUnavailable as error:
        raise WorkParallelError(
            "P0 assignment key recovery required; " + action + " blocked (" + error.reason + ")") from error


def _p0_key_for_admission(store, p0):
    """Create only after a locked zero-dependent check; otherwise validate."""
    if p0.has_key_dependency():
        return _p0_key_ready(store, "admission")
    from .work_p0_assignment_key import P0AssignmentKeyStore
    # Key-store publication is also exclusive. A concurrent first admission may
    # publish the same one-time file; it never replaces an existing value.
    return P0AssignmentKeyStore.beside_ledger(store.path).ensure_before_first_intent()


def _end_predecessor_continuation(store, p0, identity, item_id, continuation, predecessor_lease_id):
    """End the intent that launched the result this continuation replaces.

    A rework of a result that was itself a rework (or a stranded recovery of a
    recovery) finds its predecessor's intent of the same continuation still
    bound: it ends only with the predecessor's lease, which is retired after
    this continuation is reserved. Matching it as the one open intent refused
    every such roll-forward as "open P0 intent facts changed" (Andy 2026-11-20,
    n2471 gen-3). The predecessor holds no slot, so its intents end here.
    """
    from .work_parallel_p0 import OPEN_INTENT_STATES
    predecessor = store.read()["leases"].get(predecessor_lease_id)
    if predecessor is None or predecessor["slot"]:
        return
    state = p0.read()
    if any(row["scope"] == identity and row["item_id"] == item_id
           and row["continuation"] == continuation and row["lease_id"] == predecessor_lease_id
           and row["state"] in OPEN_INTENT_STATES for row in state["intents"].values()):
        p0.close_lease_intents(predecessor_lease_id, expected_version=state["version"])


def _p0_continuation_intent(store, board_store, identity, item_id, continuation,
                            predecessor_lease_id):
    """Create/reuse a durable intent only while the exact root is effectively P0."""
    board = board_store.read()
    root = _root_for(board, item_id)
    p0 = p0_state(store)
    if p0.effective_priority(identity, root, root.get("priority", "")) != "P0":
        return None
    _p0_key_for_admission(store, p0)
    _end_predecessor_continuation(store, p0, identity, item_id, continuation, predecessor_lease_id)
    state, intent = p0.create_intent({
        "scope": identity, "root_id": root["id"],
        "root_created_at": root["created_at"], "item_id": item_id,
        "continuation": continuation,
        "predecessor_lease_id": predecessor_lease_id,
    }, expected_version=p0.read()["version"])
    del state
    return intent


def _p0_approval_intent(store, board, lease):
    """Exact sidecar authorization for an already-launched P0 generation."""
    intent = p0_state(store).approval_intent(
        lease["id"], scope=lease["scope"], item_id=lease["item_id"])
    if intent is None:
        return None
    try:
        root = _root_for(board, lease["item_id"])
    except WorkParallelError:
        return None
    if (root["id"] != intent["root_id"]
            or root.get("created_at") != intent["root_created_at"]):
        return None
    return intent


def _approval_current(store, board, lease):
    """Validate only the assigned root generation, not unrelated board edits."""
    if _p0_approval_intent(store, board, lease) is not None:
        return True
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


def _root_approved(store, board, lease):
    """Whether the lease's goal may run now: approved for execution, or an exact P0 launch.

    Unlike _approval_current it ignores scope edits made since the assignment
    (user 2026-11-19): an in-flight assignment is never stranded by them; main
    judges its result, and the review shows ``approval_changed``.
    """
    if _p0_approval_intent(store, board, lease) is not None:
        return True
    try:
        root = _root_for(board, lease["item_id"])
    except WorkParallelError:
        return False
    return tree.root_phase(board, root["id"]) == "executing"


def _approval_changed(cstate, board, lease_id):
    """Read-only: the goal's scope no longer matches the run's assignment fingerprint.

    Compared against the board itself, so it is current before any reconcile
    has moved the stored approval generation. A removed goal counts as changed.
    """
    if not board:
        return False
    run = (cstate.get("runs") or {}).get(lease_id)
    if run is None:
        run = next((entry["run"] for entry in reversed(cstate.get("archive") or [])
                    if entry.get("run", {}).get("lease_id") == lease_id), None)
    if run is None or not run.get("fingerprint"):
        return False
    try:
        return _root_scope_digest(board, run["root_id"]) != run["fingerprint"]
    except WorkParallelError:
        return True


def _worker_event(store, board_store, lease, args):
    event = args.get("event")
    if event == "review" or lease["role"] == "reviewer":
        raise WorkParallelError("reviewer reports are retired; main reviews completed results")
    if event not in ("question", "result", "completion", "handoff"):
        raise WorkParallelError("worker may only report question, completion or handoff")
    if lease["state"] != "running" or not lease["slot"]:
        raise WorkParallelError("worker lease is not active")
    # A report is never refused because the goal's scope was edited after the
    # assignment (user 2026-11-19; Celly lost 10 completions to it): main
    # reviews the result, which shows approval_changed.
    evidence = args.get("evidence")
    artifact = args.get("artifact")
    coord = coordination(store)
    # The authenticated capability proves the ledger binding. Complete a
    # coordination bind lost between the two stores before any receipt below
    # moves the run past "reserved", where it could never be bound again.
    _repair_lost_coordination_bind(store, lease)
    assignment = coord.read().get("runs", {}).get(lease["id"])
    workflow_report = None
    if event in ("result", "completion", "review"):
        if event != "review" and not digest(artifact):
            raise WorkParallelError("completion artifact SHA256 required")
        if event != "review":
            # Approval workflow (spec 4.6): a missing linked QA, a missing
            # evidence file or a wrong hash refuses the report before any write.
            from .work_workflow_parallel import report_check
            try:
                workflow_report = report_check(board_store, lease, args)
            except tree.WorkPlanError as error:
                raise WorkParallelError(str(error)) from None
        kind = "review" if event == "review" else "completion"
        if assignment is not None:
            detail = args.get("detail") if workflow_report is None else workflow_report["receipt_detail"]
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
        if workflow_report is not None:
            from .work_workflow_parallel import report_record
            usage = args.get("usage") if isinstance(args.get("usage"), dict) else {}
            report_record(board_store, workflow_report, lease,
                          {key: usage[key] for key in ("model", "main_model", "tokens", "seconds")
                           if type(usage.get(key)) in (str, int, float)})
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
    if event == "handoff":
        side = p0_state(store)
        side.close_lease_intents(lease["id"], expected_version=side.read()["version"])
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


def _p0_send_reported_after(store, send):
    """True when the worker reported on this lease after the P0 send child was made."""
    rows = coordination(store).read().get("receipts", {}).values()
    return any(row["lease_id"] == send["lease_id"] and row["kind"] in ("question", "completion", "handoff")
               and row["created_at"] > send["created_at"] for row in rows)


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


def parked_wait_projection(store, board_store, scope, released_items, ledger_version=None):
    """Bounded host pre-read for side-released waits; never an execution proof."""
    require(text(scope, 200) and type(released_items) is list
            and len(released_items) <= 16, "released wait projection")
    clean = []
    for value in released_items:
        require(type(value) is dict and set(value) == {"item_id", "root_id"}
                and text(value["item_id"], 200) and text(value["root_id"], 200),
                "released wait projection row")
        clean.append(value)
    ledger_state = store.read()
    if ledger_version is not None and ledger_version != ledger_state["version"]:
        return None
    board = board_store.read()
    nodes = {row["id"]: row for row in board.get("nodes", [])}
    rows = []
    for value in clean:
        node = nodes.get(value["item_id"])
        if node is None:
            continue
        root = node
        while root.get("parent_id") is not None:
            root = nodes.get(root["parent_id"])
            if root is None:
                break
        if root is None or root["id"] != value["root_id"]:
            continue
        matches = [lease for lease in ledger_state["leases"].values()
                   if lease["scope"] == scope and lease["item_id"] == value["item_id"]
                   and lease["role"] in ("worker", "resume")
                   and lease["state"] == "waiting_input" and lease["slot"] is False]
        if len(matches) != 1:
            continue
        lease = matches[0]
        rows.append({"item_id": value["item_id"], "root_id": value["root_id"],
                     "lease_id": lease["id"], "state": "waiting_input", "slot": False})
    return {"ledger_version": ledger_state["version"], "rows": rows}


def _board_or_none(board_store):
    try:
        return board_store.read()
    except Exception:
        return None


def _caller_attachment_owner(store, host, conversation):
    """This host's exactly attached launcher owner, or None (fallback stays below)."""
    return attachments(store).caller_owner(conversation, host.get("launch_nonce"))


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


def _proven_owner(host, conversation):
    """The live launcher that owns ``conversation``, proven exactly now, or None.

    Either the authenticated host names its session process (session_pid, and
    the birth identity it observed when it gives one), or the router's bound
    scope names exactly this conversation and its launcher. In both cases the
    process must be alive: its birth identity is observable now and, when the
    host stated one, equal to it. A request for another conversation (ctl or
    REST for a session this router is not bound to), an unbound router, or a
    dead or unobservable launcher proves nothing.
    """
    pid = host.get("session_pid")
    claimed = host.get("session_process_identity")
    if type(pid) is not int or pid <= 0:
        claimed = None
        try:
            from .runtime_paths import ROUTER_INSTANCE_DIR
            scope = json.loads((Path(ROUTER_INSTANCE_DIR) / "compaction-scope.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (not isinstance(scope, dict) or not scope.get("session_id")
                or str(scope.get("runtime")) + ":" + str(scope.get("session_id")) != conversation):
            return None
        pid = scope.get("launcher_pid")
        if type(pid) is not int or pid <= 0:
            return None
    observed = process_identity(pid)
    if not isinstance(observed, str) or not observed:
        return None
    if claimed is not None and claimed != observed:
        return None
    return {"session_pid": pid, "identity": observed}


def _workspace_failure(store, identity, code, item_id, log, detail):
    """Durable fixed code (workspace failure log) and Git's own text in the router log."""
    try:
        from .work_parallel_failures import WorkParallelFailureLog
        key = "ws:" + hashlib.sha256((code + "\0" + str(item_id or "-")).encode("utf-8")).hexdigest()[:32]
        WorkParallelFailureLog.workspace_beside(store.path).record(identity, code, dedup_key=key)
    except Exception:  # noqa: BLE001 - the record is advisory; the refusal below still happens
        pass
    if log is not None:
        try:
            log("WARN", "work_workspace_failed code=" + code + " item=" + str(item_id or "-")[:40]
                + " detail=" + json.dumps(str(detail)[:600], ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass


def _retry_conflict(write, attempts=3):
    for attempt in range(attempts):
        try:
            return write()
        except WorkParallelConflict:
            if attempt == attempts - 1:
                raise


def _release_failed_reservation(store, ident, p0_intent):
    """Undo one reservation that never launched: lease, coordination run, P0 intent.

    Each half is checked first and skipped when it is already gone (the orphan
    reconciliation may have taken it). Returns what could not be undone.
    """
    left = []
    try:
        def release():
            current = store.read()
            lease = current["leases"].get(ident)
            if lease is not None and lease["state"] == "reserved" and lease["host_id"] is None:
                store.release_unbound(ident, proof="workspace preparation failed before launch",
                                      expected_version=current["version"])
        _retry_conflict(release)
    except Exception:  # noqa: BLE001
        left.append("lease")
    coord = coordination(store)
    try:
        def cancel():
            current = coord.read()
            run_row = current["runs"].get(ident)
            if run_row is not None and run_row["state"] == "reserved" and run_row["agent_id"] is None:
                coord.cancel_reserved(ident, outcome="workspace preparation failed before launch",
                                      expected_version=current["version"])
        _retry_conflict(cancel)
    except Exception:  # noqa: BLE001
        left.append("coordination")
    if p0_intent is not None:
        side = p0_state(store)
        try:
            def unreserve():
                current = side.read()
                row = current["intents"].get(p0_intent["intent_id"])
                if row is not None and row["state"] == "reserved" and row["lease_id"] == ident:
                    side.release_reservation(row["intent_id"], ident, expected_version=current["version"])
            _retry_conflict(unreserve)
        except Exception:  # noqa: BLE001
            left.append("p0_intent")
    return left


ENDED_LEASE_STATES = ("done", "orphaned", "unreported")


def _close_ended_p0_intents(store, p0, identity, item_id):
    """Close this item's open P0 intents whose lease has ended (or is gone).

    An orphan sweep, a finalized review or a release ends a lease without the
    host event that closes its intent. create_intent then returned that stale
    bound intent, the reserve reused its old run, and every P0 launch of the
    item failed (Andy 2026-11-19: n2471, n2452 "coordination persistence failed").
    """
    leases = store.read()["leases"]
    for row in list(p0.read()["intents"].values()):
        if (row["scope"] != identity or row["item_id"] != item_id or row["lease_id"] is None
                or row["state"] not in ("reserved", "spawning", "bound")):
            continue
        lease = leases.get(row["lease_id"])
        if lease is None or lease["state"] in ENDED_LEASE_STATES:
            p0.close_lease_intents(row["lease_id"], expected_version=p0.read()["version"])


def _reserve_assignment(store, board_store, identity, host, version, *, item_id=None,
                        role="worker", p0_only=False, log=None):
    _repair_no_report_rows(store, identity)
    board = board_store.read()
    p0 = p0_state(store)
    p0_intent = None
    if item_id is None:
        p0_rows = _p0_next_rows(store, board, identity, include_refused=p0_only)
        if p0_rows:
            item_id = p0_rows[0]["item_id"]
        elif p0_only:
            raise WorkParallelError("no eligible candidate")
        else:
            rows = [row for row in tree.runnable_leaves(board)
                    if not _stranded_item_pending(store, identity, row["item_id"])]
            if not rows:
                raise WorkParallelError("no eligible candidate")
            rows, held = _wait_lane_order(store, board, identity, rows)
            if not rows:
                raise WorkParallelError(
                    "the next " + "/".join(held) + " slot is kept for a released parked worker; "
                    "main resumes it with SendMessage")
            candidates = [candidate(board, row["item_id"], row["priority"], role)
                          for row in rows]
    if item_id is not None:
        details = _item_details(board).get(item_id)
        if details is None:
            raise WorkParallelError("unknown Work item")
        nodes = {node["id"]: node for node in board["nodes"]}
        current = nodes[item_id]
        while current["parent_id"] is not None:
            current = nodes[current["parent_id"]]
        priority = current.get("priority") if current.get("priority") in tree.PRIORITIES else "P5"
        root = current
        while root["parent_id"] is not None:
            root = nodes[root["parent_id"]]
        if board.get("workflows") and workflow_gate(board, root["id"]):
            from .work_workflow import gate_text
            raise WorkParallelError(f"Work item {item_id} cannot dispatch: "
                                    + gate_text(root["id"], workflow_gate(board, root["id"])))
        effective = p0.effective_priority(identity, root, priority)
        if effective == "P0":
            # Key creation precedes the first durable launch intent. While any
            # dependent intent/send exists, a missing/unreadable key is
            # recovery_required and is never recreated or rotated.
            _p0_key_for_admission(store, p0)
            _close_ended_p0_intents(store, p0, identity, item_id)
            _, p0_intent = p0.create_intent({
                "scope": identity, "root_id": root["id"],
                "root_created_at": root["created_at"], "item_id": item_id,
                "continuation": "initial", "predecessor_lease_id": None,
            }, expected_version=p0.read()["version"])
            priority = "P1"
        elif p0_only:
            raise WorkParallelError("no eligible candidate")
        candidates = [candidate(board, item_id, priority, role)]
        refusal = dispatch_refusal(board, item_id)
        if refusal and p0_intent is None:
            raise WorkParallelError(refusal)
        if _stranded_item_pending(store, identity, item_id):
            raise WorkParallelError("Work item " + item_id + " has a stranded result awaiting main's "
                                    "decision; rework it from the review instead of dispatching again")
    token = None if p0_intent is not None else secrets.token_hex(32)
    dispatch = p0_intent["dispatch_name"] if p0_intent is not None else host.get("dispatch_name")
    ident = None
    if p0_intent is not None and p0_intent["lease_id"] is not None:
        d = store.read()
        existing = d["leases"].get(p0_intent["lease_id"])
        require(existing is not None and existing["scope"] == identity
                and existing["item_id"] == p0_intent["item_id"]
                and existing.get("dispatch_name") == p0_intent["dispatch_name"]
                and p0_intent["state"] in ("reserved", "spawning", "bound"),
                "P0 intent lease identity")
        ident = existing["id"]
    elif p0_intent is not None:
        # Crash recovery precedes a new reserve: adopt only the one exact legacy
        # reservation carrying this durable intent's derived dispatch name.
        _, adopted = p0.adopt_reserved(
            p0_intent["intent_id"], store.read()["leases"],
            expected_version=p0.read()["version"])
        if adopted is not None:
            p0_intent = adopted
            ident = adopted["lease_id"]
            d = store.read()
    if ident is None:
        d, ident = store.reserve(
            candidates, scope=identity, expected_version=version,
            dispatch_name=dispatch, report_hash=None if token is None else hash_worker_token(token),
            owner=_caller_attachment_owner(store, host, identity) or _owner_from_host(host, identity),
            admission_bypass="p0" if p0_intent is not None else None,
            p0_intent=p0_intent,
        )
        if p0_intent is not None:
            try:
                _, p0_intent = p0.adopt_reserved(
                    p0_intent["intent_id"], d["leases"],
                    expected_version=p0.read()["version"])
            except Exception as error:
                # The ledger reservation is durable but sidecar adoption is
                # ambiguous. Never guess native absence from host_id=None or
                # release it here; exact-dispatch replay/absence recovery owns it.
                raise WorkParallelError(
                    "P0 intent persistence outcome unknown; recovery required") from error
    lease = d["leases"][ident]
    item = _item_details(board).get(lease["item_id"], {})
    # Pin semantic approval independently of the board's global CAS version.
    # A known assignment write failure follows the accepted exact rollback:
    # release the still-unbound lease and cancel its intent.
    coord = coordination(store)
    try:
        root = _root_for(board, lease["item_id"])
        cstate = coord.read()
        existing_run = cstate["runs"].get(ident)
        if existing_run is not None and existing_run["state"] == "reserved" and existing_run.get("agent_id") is None:
            # A replay of this exact unbound reservation keeps the assignment it
            # was given; a scope edit since must not turn the replay into a
            # "different manifest" refusal that releases the reservation.
            require(existing_run["scope"] == identity and existing_run["item_id"] == lease["item_id"]
                    and existing_run["root_id"] == root["id"], "assignment belongs to another item")
        else:
            approval = _reconcile_root(coord, board, root["id"], identity)
            cstate = coord.read()
            created_at = (existing_run["reserved_at"] if existing_run is not None else time.time())
            coord.record_assignment(identity, ident, {
                "item_id": lease["item_id"], "root_id": root["id"],
                "priority": lease["priority"], "role": lease["role"], "generation": 1,
                "approval_generation": max(1, approval["generation"]),
                "assigned_todos": [lease["item_id"]],
                "fingerprint": approval["fingerprint"], "workspace": None,
                "created_at": created_at,
            }, expected_version=cstate["version"],
                dispatch_without_approval=p0_intent is not None, p0_intent=p0_intent)
    except Exception as error:
        current = store.read()
        if current["leases"].get(ident, {}).get("host_id") is None:
            store.release_unbound(ident, proof="coordination persistence failed: " + type(error).__name__,
                                  expected_version=current["version"])
        if p0_intent is not None:
            try:
                p0.transition_intent(p0_intent["intent_id"], "cancelled",
                                     expected_version=p0.read()["version"])
            except WorkParallelError:
                pass
        # The cause goes with the refusal (Andy and Celly 2026-11-19: the bare
        # message hid "assignment already exists with a different manifest").
        raise WorkParallelError("coordination persistence failed; reservation released: "
                                + _clip(type(error).__name__ + ": " + str(error), 300)) from error
    workspace = None
    if host.get("workspace_repo"):
        from .work_parallel_workspace import DUBIOUS_OWNERSHIP, WorkspaceError, prepare
        prepared = False
        try:
            workspace = prepare(host["workspace_repo"], ident)
            prepared = True
            # Device-wide files: an unrelated write may move either version
            # between the reads; a conflict is retried, never a failure.
            def attach_lease():
                return store.attach_workspace(ident, workspace, expected_version=store.read()["version"])
            d, _ = _retry_conflict(attach_lease)
            binding = {key: workspace[key] for key in ("path", "branch", "head")}
            _retry_conflict(lambda: coord.attach_workspace(ident, binding,
                                                           expected_version=coord.read()["version"]))
        except Exception as error:
            detail = str(error) or type(error).__name__
            clean = isinstance(error, WorkspaceError) and error.clean and not prepared
            code = ("workspace_prepare_retained" if not clean
                    else "workspace_git_untrusted" if DUBIOUS_OWNERSHIP in detail
                    else "workspace_prepare_failed")
            if p0_intent is not None and prepared:
                # The worktree is complete and verified; only attaching it
                # failed. The next P0 pump replays this exact lease, and prepare
                # returns the same worktree from its manifest.
                _workspace_failure(store, identity, code, lease["item_id"], log, detail)
                raise WorkParallelError(
                    "P0 workspace preparation outcome unknown; recovery required") from error
            # Nothing has launched: the native spawn starts only after this
            # returns (P0: after p0_launch_spawning). The lease, its coordination
            # run and a P0 intent are released, so no slot stays held and the
            # item stays queued. A worktree written before the failure is kept
            # on disk, never reused or removed. Celly 2026-11-17: these halves
            # stayed reserved, and P0 retried the same failing lease forever.
            left = _release_failed_reservation(store, ident, p0_intent)
            _workspace_failure(store, identity, code, lease["item_id"], log,
                               detail + (" · not undone: " + ", ".join(left) if left else ""))
            raise WorkParallelError(
                code + ": workspace preparation failed; reservation released"
                + (" except " + ", ".join(left) + " (orphan reconciliation retries it)" if left else "")
                + ", item stays queued: " + detail[:300]) from error
    if p0_intent is None:
        _wait_lane_consume(store, identity, d["leases"][ident], kind="reserve", outcome="native_bound",
                           ledger_before=version, ledger_after=d["version"])
    result = {"lease_id": ident, "version": d["version"], "plan_version": board["version"],
              "workspace": workspace, "priority": lease["priority"],
              "role": lease["role"], "generation": 1,
              "item_id": lease["item_id"], **item}
    if token is not None:
        result["worker_token"] = token
    return result


def _unavailable_review_facts(store, identity, lease, reason):
    """Stranded-v1 facts for a completion whose workspace cannot be verified."""
    coord = coordination(store)
    run_row = coord.read().get("runs", {}).get(lease["id"])
    if run_row is None or (run_row.get("exit") or {}).get("kind") != "completion":
        raise WorkParallelError("durable completion receipt required before main review")
    detail = coord.detail(run_row["exit"]["digest"])
    binding = _stranded_binding(lease.get("workspace"))
    d = store.read()
    known = _known_execution_ids(d, d["leases"][lease["id"]])
    return {"lease_id": lease["id"], "scope": identity, "item_id": lease["item_id"],
            "known_lease_ids": known, "capture_ledger_version": d["version"],
            "root_id": run_row["root_id"], "priority": lease["priority"],
            "plan_version": lease["plan_version"], "generation": run_row["generation"],
            "kind": "workspace_unavailable", "reason": _clip(reason, 300),
            "artifact": lease["artifact"], "completion_receipt": run_row["exit"]["digest"],
            "fingerprint": run_row["fingerprint"], "summary": detail["summary"],
            "coverage": copy.deepcopy(detail["coverage"]), "verification": detail["verification"],
            "binding": binding}


UNCONFINED_CLEANUP_PROOF = "no workspace: the fork ran without a git worktree; nothing to retain or remove"
NO_REPORT_SUMMARY = "No worker report: the native fork ended without a durable exit receipt."
NO_REPORT_VERIFICATION = "Not verified: no worker report and the retained workspace is unavailable."


def _known_execution_ids(d, lease):
    """Every same-scope+item worker/resume lease id at one ledger version.

    Captured while the stranded lease is still active: the active-lease fence
    means no other execution of the item can be reserved after that version
    without this lease first leaving ACTIVE_STATES. A later id therefore proves
    a later reservation (lead ruling 2026-11-13; no timestamp or UUID order).
    """
    from .work_parallel import ACTIVE_STATES
    from .work_parallel_stranded import MAX_KNOWN_LEASE_IDS
    require(lease["state"] in ACTIVE_STATES, "stranded lease is no longer active; intent cannot be captured")
    ids = sorted(key for key, row in d["leases"].items()
                 if row["scope"] == lease["scope"] and row["item_id"] == lease["item_id"]
                 and row["role"] in ("worker", "resume"))
    if len(ids) > MAX_KNOWN_LEASE_IDS:
        raise WorkParallelError("stranded known-lease bound exceeded: " + str(len(ids)) + "/"
                                + str(MAX_KNOWN_LEASE_IDS) + "; lease kept active")
    return ids


def _stranded_binding(binding):
    required = ("repo", "path", "branch", "head")
    require(isinstance(binding, dict) and all(text(binding.get(key), 2_000) for key in required),
            "recorded workspace identity unavailable")
    return {key: binding[key] for key in required}


def _no_report_review_facts(store, identity, lease, reason, d):
    """Stranded-v1 intent facts for a still-active lease that ended without a report."""
    run_row = coordination(store).read().get("runs", {}).get(lease["id"])
    require(run_row is not None and (run_row.get("exit") or {}).get("kind") == "host_salvage",
            "durable host salvage receipt required before a stranded review")
    receipt = run_row["exit"]["digest"]
    return {"lease_id": lease["id"], "scope": identity, "item_id": lease["item_id"],
            "root_id": run_row["root_id"], "priority": lease["priority"],
            "plan_version": lease["plan_version"], "generation": run_row["generation"],
            "known_lease_ids": _known_execution_ids(d, d["leases"][lease["id"]]),
            "capture_ledger_version": d["version"],
            "kind": "no_report", "reason": _clip(reason, 300),
            "artifact": None, "completion_receipt": receipt,
            "fingerprint": run_row["fingerprint"], "summary": NO_REPORT_SUMMARY,
            "coverage": [], "verification": NO_REPORT_VERIFICATION,
            "binding": _stranded_binding(lease.get("workspace"))}

def _review_facts(store, identity, lease):
    """Require durable completion plus a fresh positive idle workspace snapshot.

    A lease reserved with no workspace has no snapshot: its review carries
    workspace None, and the positive idle proof on the ledger is the observation.
    """
    coord = coordination(store)
    cstate = coord.read()
    run_row = cstate.get("runs", {}).get(lease["id"])
    if run_row is None or (run_row.get("exit") or {}).get("kind") != "completion":
        raise WorkParallelError("durable completion receipt required before main review")
    if lease["state"] != "submitted" or lease["slot"] or lease.get("idle_proof") is None:
        raise WorkParallelError("positive idle observation required before main review")
    if lease.get("workspace") is not None and not lease["workspace"]:
        raise WorkParallelError("positive idle workspace observation required before main review")
    detail = coord.detail(run_row["exit"]["digest"])
    observed = None
    if lease.get("workspace") is not None:
        from .work_parallel_workspace import review_observe
        observed = review_observe(lease["workspace"]["repo"], lease["id"],
                                  generation=str(run_row["generation"]),
                                  artifact=lease["artifact"], completion_receipt=run_row["exit"]["digest"],
                                  host_id=lease["host_id"], idle_proof=lease["idle_proof"])
    receipt_workspace = detail.get("workspace")
    if isinstance(receipt_workspace, dict):
        if observed is None:
            raise WorkParallelError("stale completion workspace snapshot; result retained")
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


def _replacement_p0_authorization(store, identity, replacement_id, item_id):
    """record_assignment arguments for a rework / stranded replacement lease.

    The initial P0 launch passes its pinned intent so an unapproved (planning)
    P0 goal still records its run. A replacement reserved under P0 adopted its
    own intent, so it carries the same authorization; without it the run was
    refused as "stale root approval" (Celly 2026-11-20, n1097 under P0 n1006).
    """
    intent = p0_state(store).approval_intent(replacement_id, scope=identity, item_id=item_id)
    if intent is None:
        return {}
    return {"dispatch_without_approval": True, "p0_intent": intent}


def _assignment_approval(coord, board_store, root_id, identity):
    """(generation, fingerprint) of the goal's current approval for a replacement run.

    A rework or stranded recovery copied the predecessor's approval, so after a
    goal scope edit the replacement was refused as "stale root approval" and
    the saga stayed blocked (Celly 2026-11-19: 4 stranded rows). An approved
    goal now gives its current approval; otherwise None keeps the old values
    (the exact-P0 path, unchanged).
    """
    try:
        approval = _reconcile_root(coord, board_store.read(), root_id, identity)
    except WorkParallelError:
        return None
    if not approval["approved"]:
        return None
    return max(1, approval["generation"]), approval["fingerprint"]


def _rework_needs_approved_goal(store, board_store, lease):
    """A rework runs again, so its goal must be approved now (or an exact P0 launch)."""
    if lease is not None and not _root_approved(store, board_store.read(), lease):
        raise WorkParallelError("Work item " + lease["item_id"] + "'s goal is not approved for execution: "
                                "approve the result, or approve the goal and then rework")


def _revalidate_review(store, board_store, row, decision=None):
    """Ordinary schema-5 review facts must remain verifiable.

    A goal scope edit since the assignment is not a refusal (user 2026-11-19):
    main decides the result, and the review shows ``approval_changed``.
    """
    lease = store.read()["leases"].get(row["lease_id"])
    if (lease is None or lease["state"] != "submitted" or lease["slot"]
            or lease["artifact"] != row["artifact"] or lease["plan_version"] != row["plan_version"]):
        raise WorkParallelError("stale artifact: reviewed result is no longer the submitted result")
    run_row = coordination(store).read().get("runs", {}).get(row["lease_id"])
    if (run_row is None or (run_row.get("exit") or {}).get("digest") != row["completion_receipt"]
            or run_row["fingerprint"] != row["fingerprint"]):
        raise WorkParallelError("stale completion receipt or approval fingerprint")
    if decision == "rework":
        _rework_needs_approved_goal(store, board_store, lease)
    if row["workspace"] is None or lease.get("workspace") is None:
        # A review without a snapshot stays valid only while the lease still
        # has no workspace; either side changing is a different result.
        if row["workspace"] is not None or lease.get("workspace") is not None:
            raise WorkParallelError("stale workspace: the reviewed result had a different workspace binding")
        return None
    from .work_parallel_workspace import WorkspaceError, review_observe
    try:
        observed = review_observe(lease["workspace"]["repo"], lease["id"],
                                  generation=str(row["generation"]), artifact=row["artifact"],
                                  completion_receipt=row["completion_receipt"],
                                  host_id=lease["host_id"], idle_proof=lease["idle_proof"])
    except WorkspaceError:
        reason = _workspace_unavailable_reason(lease)
        if reason is None:
            raise
        _record_unavailable_review(store, row["scope"], lease, reason, legacy_row=row)
        return "stranded"
    if observed["snapshot_digest"] != row["workspace"]["snapshot_digest"]:
        raise WorkParallelError("stale workspace: HEAD or tree changed since review projection")


def _revalidate_stranded(store, board_store, row):
    lease = store.read()["leases"].get(row["lease_id"])
    run_row = coordination(store).read().get("runs", {}).get(row["lease_id"])
    if row["kind"] == "no_report":
        if (lease is None or lease["state"] != "unreported" or lease["slot"]
                or lease.get("report_hash") is not None or run_row is None
                or (run_row.get("exit") or {}).get("kind") != "host_salvage"
                or (run_row.get("exit") or {}).get("digest") != row["completion_receipt"]
                or run_row["fingerprint"] != row["fingerprint"]):
            raise WorkParallelError("stale stranded result: terminal salvage receipt changed")
    else:
        if (lease is None or lease["state"] != "submitted" or lease["slot"]
                or lease["artifact"] != row["artifact"] or lease["plan_version"] != row["plan_version"]
                or run_row is None or (run_row.get("exit") or {}).get("kind") != "completion"
                or (run_row.get("exit") or {}).get("digest") != row["completion_receipt"]
                or run_row["fingerprint"] != row["fingerprint"]):
            raise WorkParallelError("stale stranded completion receipt changed")
    # A stranded result is only ever reworked: its goal must be approved now.
    _rework_needs_approved_goal(store, board_store, lease)


def _stranded_admission(d, identity, row):
    """(code, message) for fresh-workspace recovery; never rework_limit."""
    from .work_parallel import ACTIVE_STATES, lease_bound_reached
    p = d["policy"]
    if not p["enabled"] or p["paused"]:
        return "paused", "disabled/paused; stranded recovery waits for admission"
    others = [lease for lease in d["leases"].values()
              if lease["scope"] == identity and lease["item_id"] == row["item_id"]
              and lease["id"] != row["lease_id"] and lease["role"] in ("worker", "resume")
              and lease["state"] in ACTIVE_STATES]
    if others:
        return "duplicate_assignment", "item already has an active execution; recovery waits for it"
    counts = active_by_priority(d["leases"])
    if counts[row["priority"]] >= p["priority_limits"][row["priority"]]:
        return "capacity", "active slot capacity reached; stranded recovery waits for admission"
    if lease_bound_reached(d["leases"]):
        return "lease_bound", "lease bound reached; stranded recovery waits for admission"
    return "", ""


def _publish_stranded(store, facts):
    rows = stranded(store)
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        try:
            rows.pending(facts, expected_version=rows.read()["version"])
            return True
        except WorkParallelConflict:
            continue
    raise WorkParallelConflict("Work stranded state changed; reread")


def _salvage_unavailable_reason(store, lease_id):
    cstate = coordination(store).read()
    run_row = cstate.get("runs", {}).get(lease_id)
    exit_row = (run_row or {}).get("exit") or {}
    detail = cstate.get("details", {}).get(exit_row.get("digest"))
    if exit_row.get("kind") != "host_salvage" or detail is None or detail["body"]["workspace"] is None:
        return None
    diff = str(detail["body"]["workspace"]["diff_stat"])
    prefix = "workspace unavailable: "
    return diff[len(prefix):] if diff.startswith(prefix) else None


def _terminate_unreported_with_intent(store, identity, ident, proof):
    """Intent first, then the terminal ledger CAS at the captured version.

    Only a broken-worktree salvage writes an intent. A healthy salvage keeps
    today's behavior (no row; the item is dispatchable again).
    """
    rows = stranded(store)
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        d = store.read()
        lease = d["leases"][ident]
        side = rows.read()["rows"].get(ident)
        if lease["state"] == "unreported":
            # Another writer already made it terminal. Keep an intent that was
            # captured while active; never capture one now (no intent, no row).
            if side is not None and side["state"] == "intent":
                _finalization_write(rows, lambda version: rows.activate(ident, expected_version=version))
            return d, side is not None
        reason = _salvage_unavailable_reason(store, ident) if lease.get("workspace") else None
        if reason is not None:
            facts = _no_report_review_facts(store, identity, lease, reason, d)
            if side is None:
                _publish_stranded(store, facts)
            elif side["state"] == "intent" and side["capture_ledger_version"] != d["version"]:
                _finalization_write(rows, lambda version: rows.refresh_intent(
                    ident, facts["known_lease_ids"], d["version"], expected_version=version))
        try:
            d, _ = store.terminate_unreported(ident, proof=proof, expected_version=d["version"])
        except WorkParallelConflict:
            continue
        if reason is not None:
            _finalization_write(rows, lambda version: rows.activate(ident, expected_version=version))
        return d, reason is not None
    raise WorkParallelConflict("Work parallel state changed; reread")


def _stranded_item_pending(store, identity, item_id):
    return any(row["scope"] == identity and row["item_id"] == item_id and row["decision_id"] is None
               and row["state"] in ("intent", "pending") for row in stranded(store).read()["rows"].values())


def _repair_no_report_rows(store, identity):
    """Complete an intent whose terminal write landed; never capture after the fact."""
    rows = stranded(store)
    leases = store.read()["leases"]
    for row in list(rows.read()["rows"].values()):
        if row["scope"] == identity and row["state"] == "intent":
            lease = leases.get(row["lease_id"])
            if lease is not None and lease["state"] == "unreported":
                _finalization_write(rows, lambda version, key=row["lease_id"]: rows.activate(
                    key, expected_version=version))
    _observe_supersessions(store, identity)


def _superseding_lease(d, row):
    own = {row["lease_id"], row["replacement_lease_id"]}
    later = [lease for key, lease in d["leases"].items()
             if lease["scope"] == row["scope"] and lease["item_id"] == row["item_id"]
             and lease["role"] in ("worker", "resume") and key not in own
             and key not in row["known_lease_ids"]]
    return min(later, key=lambda lease: lease["id"]) if later else None


def _supersede_digest(row, lease):
    from .work_parallel_finalization import canonical_digest
    return canonical_digest(["work-parallel-stranded-superseded-v1", row["lease_id"], lease["id"],
                             lease["plan_version"]])


def _cancel_unbound_recovery(store, row, superseding_lease_id):
    """A decided recovery that never started is permanently cancelled before supersession."""
    replacement_id = row["replacement_lease_id"]
    if replacement_id is None:
        return
    current = store.read()
    recovery = current["leases"].get(replacement_id)
    if recovery is not None:
        if recovery["state"] != "reserved" or recovery["host_id"] is not None:
            # It already started: the existing active-lease fence makes this
            # execution authoritative. Never cancel a bound worker.
            return
        store.release_unbound(replacement_id,
                              proof="superseded before launch by lease " + superseding_lease_id,
                              expected_version=current["version"])
    coord = coordination(store)
    cstate = coord.read()
    run = cstate["runs"].get(replacement_id)
    if run is not None:
        require(run["state"] == "reserved" and run["agent_id"] is None,
                "superseded recovery coordination run already started")
        coord.cancel_reserved(replacement_id,
                              outcome="superseded before launch by lease " + superseding_lease_id,
                              expected_version=cstate["version"])


def _observe_supersessions(store, identity):
    """Persist read-time supersession by CAS; cancel an unbound recovery first."""
    rows = stranded(store)
    d = store.read()
    for row in list(rows.read()["rows"].values()):
        if row["scope"] != identity or row["state"] not in ("intent", "pending", "deciding"):
            continue
        other = _superseding_lease(d, row)
        if other is None:
            continue
        if row["state"] == "deciding":
            replacement = d["leases"].get(row["replacement_lease_id"])
            if replacement is not None and (replacement["state"] != "reserved" or replacement["host_id"] is not None):
                continue  # recovery already started and is authoritative
            _cancel_unbound_recovery(store, row, other["id"])
        _finalization_write(rows, lambda version, key=row["lease_id"], lease=other, source=row: rows.supersede(
            key, lease["id"], 1, _supersede_digest(source, lease), expected_version=version))


def _workspace_unavailable_reason(lease):
    """The ownership-verification failure of a lease's workspace, or None if it verifies."""
    from .work_parallel_workspace import WorkspaceError, observe
    try:
        observe(lease["workspace"]["repo"], lease["id"])
    except WorkspaceError as error:
        return str(error) or "workspace verification failed"
    return None


def _publish_pending_review(store, identity, lease):
    """Publish an ordinary schema-5 review, or a stranded-v1 row if unverifiable.

    A stranded row is never written to schema-5 finalization: builds through
    57dbfb3 would show it as an undecidable pending review and hold Stop on it.
    """
    from .work_parallel_workspace import WorkspaceError
    try:
        facts = _review_facts(store, identity, lease)
    except WorkspaceError:
        # Only a failed ownership verification becomes a stranded row. Any other
        # workspace refusal (an idle-proof race) is not a broken worktree.
        reason = _workspace_unavailable_reason(lease)
        if reason is None:
            raise
        _publish_stranded(store, _unavailable_review_facts(store, identity, lease, reason))
        _converge_legacy_review(store, lease["id"])
        _close_recovered_predecessor(store, lease["id"])
        return
    reviews = finalization(store)
    state = reviews.read()
    reviews.pending(facts, expected_version=state["version"])
    # A real, verifiable review replaces an undecided stranded row for this lease.
    _close_stranded(store, lease["id"], undecided_only=True)
    _close_recovered_predecessor(store, lease["id"])


def _close_stranded(store, lease_id, *, undecided_only):
    rows = stranded(store)
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        row = rows.read()["rows"].get(lease_id)
        if row is None or row["state"] == "done" or (undecided_only and row["decision_id"] is not None):
            return
        try:
            rows.done(lease_id, expected_version=rows.read()["version"])
            return
        except WorkParallelConflict:
            continue
    raise WorkParallelConflict("Work stranded state changed; reread")


def _close_recovered_predecessor(store, replacement_lease_id):
    """The recovery generation published its own review: its stranded source is done."""
    for row in list(stranded(store).read()["rows"].values()):
        if row["replacement_lease_id"] == replacement_lease_id and row["state"] == "recovered":
            _close_stranded(store, row["lease_id"], undecided_only=False)


def _record_unavailable_review(store, identity, lease, reason, legacy_row=None):
    _publish_stranded(store, _unavailable_review_facts(store, identity, lease, reason))
    if legacy_row is not None:
        _terminalize_legacy_review(store, lease, legacy_row)


def _converge_legacy_review(store, lease_id):
    """Replay an interrupted legacy-review move from durable state alone."""
    side = stranded(store).read()["rows"].get(lease_id)
    if side is None or side["kind"] != "workspace_unavailable" or side["state"] == "superseded":
        return
    legacy = finalization(store).read()["reviews"].get(lease_id)
    tombstone = side["legacy_review_removed"]
    if legacy is None:
        if tombstone is not None and tombstone["state"] == "prepared":
            rows = stranded(store)
            # Exact absence plus the prepared tombstone: the delete committed.
            _finalization_write(rows, lambda version: rows.legacy_review(
                lease_id, "removed", row_digest=tombstone["row_digest"],
                source_digest=tombstone["source_digest"], old_version=tombstone["old_version"],
                expected_version=version))
        return
    if legacy["decision"] is not None:
        return  # An ordinary decision saga owns it; never removed.
    lease = store.read()["leases"].get(lease_id)
    if lease is not None:
        _terminalize_legacy_review(store, lease, legacy)


def _terminalize_legacy_review(store, lease, captured):
    """Move one exact broken ordinary review to the sidecar, crash-safe.

    The sidecar row is durable first. Its prepared tombstone binds the exact
    schema-5 row digest + immutable source digest + old finalization version.
    The schema-5 row is then deleted by CAS. Replay accepts only that exact
    absence+tombstone; any changed row conflicts and is never deleted.
    """
    require(lease["state"] == "submitted" and not lease["slot"] and lease.get("idle_proof") is not None,
            "legacy review lease is not terminal and slot-free")
    require(captured["decision"] is None and captured["state"] == "pending"
            and not any(captured["stages"].values()), "ordinary decision saga is in flight")
    from .work_parallel_finalization import canonical_digest
    reviews = finalization(store)
    rows = stranded(store)
    row_digest = canonical_digest(captured)
    side = rows.read()["rows"].get(lease["id"])
    require(side is not None and side["kind"] == "workspace_unavailable", "stranded source row not durable")
    # The sidecar row must describe exactly this schema-5 row: same completion,
    # approval and recorded workspace identity. Anything else is not this row.
    same = all(side[key] == captured[key] for key in (
        "lease_id", "scope", "item_id", "root_id", "priority", "plan_version", "generation",
        "artifact", "completion_receipt", "fingerprint", "summary", "coverage", "verification"))
    same = same and all(side["binding"][key] == captured["workspace"][key]
                        for key in ("repo", "path", "branch", "head"))
    require(same, "stranded source facts do not match the legacy review")
    source_digest = side["source_digest"]
    prior = side["legacy_review_removed"]
    old_version = reviews.read()["version"] if prior is None else prior["old_version"]
    if prior is None:
        _finalization_write(rows, lambda version: rows.legacy_review(
            lease["id"], "prepared", row_digest=row_digest, source_digest=source_digest,
            old_version=old_version, expected_version=version))
    # Exact row or exact absence only. remove_undecided checks the row digest,
    # its pending state and the finalization CAS version in one store call.
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        state = reviews.read()
        current = state["reviews"].get(lease["id"])
        if current is None:
            break
        if canonical_digest(current) != row_digest:
            raise WorkParallelConflict("legacy review changed since capture; reread")
        try:
            reviews.remove_undecided(lease["id"], row_digest=row_digest, expected_version=state["version"])
            break
        except WorkParallelConflict:
            continue
    else:
        raise WorkParallelConflict("Work finalization state changed; reread")
    _finalization_write(rows, lambda version: rows.legacy_review(
        lease["id"], "removed", row_digest=row_digest, source_digest=source_digest,
        old_version=old_version, expected_version=version))


def _settle_idle_rework_launch(store, lease):
    """Complete the launch half of a bind that never landed, before the review publishes.

    A bind whose ``launch_bound`` write was lost leaves the ledger bound and the
    predecessor's launch unresolved. Once the replacement's review row exists the
    predecessor is no longer projected, so the mod's bind resend never sees that
    launch again. The ledger's own host and name are the evidence, exactly as in
    a replayed bind, and ``launch_bound`` applies the same exact-match rule.

    A launch that does not match the ledger's agent is left as it is. A fresh
    bind refuses such an agent before the ledger commits, so the product cannot
    reach that state, and a completed result must stay reviewable either way.
    """
    from .work_parallel_finalization import UNRESOLVED_LAUNCH_STATES
    row = _rework_review_for(store, lease)
    if row is None or row["launch"]["state"] not in UNRESOLVED_LAUNCH_STATES:
        return
    try:
        _bind_rework_launch(store, lease, {"host_id": lease["host_id"],
                                           "agent_name": lease.get("agent_name")})
    except WorkParallelConflict:
        raise
    except WorkParallelError:
        # Review publication must not be blocked by a launch record that cannot
        # be reconciled, but the refusal must not disappear either. The frozen
        # coordination schema has a bounded diagnostic channel (code only, no
        # free-text payload); use its existing native_uncertain code.
        _native_diagnostic(coordination(store), lease["scope"], lease["priority"])
        return


def _observe_idle_and_publish_review(store, identity, lease, proof):
    """Converge ledger/workspace idle proof, then idempotently publish review.

    The ledger and workspace are separate CAS domains. Another host observation
    may change the ledger proof between their writes. Re-read after every
    workspace write and retry with the ledger's current proof until they match.
    This also repairs an already-published review whose proofs diverged; pending
    is idempotent and never creates a second row.

    A lease reserved with no workspace (the session folder is not a git
    repository) has no worktree to converge: the ledger idle proof alone is
    its positive observation.
    """
    require(lease["state"] == "submitted" and text(proof), "submitted idle review observation required")
    if lease.get("idle_proof") != proof:
        d, _ = store.reconcile(lease["id"], host_state="idle", proof=proof,
                               expected_version=store.read()["version"])
        lease = d["leases"][lease["id"]]
    unconfined = lease.get("workspace") is None
    from .work_parallel_workspace import WorkspaceError, set_activity
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        current = store.read()["leases"].get(lease["id"])
        require(current is not None and current["state"] == "submitted"
                and (current.get("workspace") is None) == unconfined and text(current.get("idle_proof")),
                "submitted idle review observation changed")
        observed_proof = current["idle_proof"]
        try:
            if not unconfined:
                set_activity(current["workspace"]["repo"], current["id"], current["host_id"],
                             idle=True, proof=observed_proof)
        except WorkspaceError:
            # A broken worktree cannot record idle, but the submitted result must
            # not be stranded silently: publish the frozen-schema stranded row.
            if _workspace_unavailable_reason(current) is None:
                raise
        latest = store.read()["leases"].get(current["id"])
        if latest is not None and latest.get("idle_proof") == observed_proof:
            _settle_idle_rework_launch(store, latest)
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
    """Idempotent board completion, independently CAS-protected by its store.

    A node already completed (by main or another result) or removed from the
    board needs no change: the decision still finalizes. Refusing it blocked
    the review forever (G:\\ciel-Walkie 2026-11-15, n1909 "board item completed
    by another result").
    """
    from . import work_plan_tree as plan_tree
    for _ in range(4):
        board = board_store.read()
        node = next((row for row in board["nodes"] if row["id"] == item_id), None)
        if node is None or node["status"] == "done":
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


def request_rework_launch_manual(store, identity, lease_id, launch_ref, *, confirmed, expected_version,
                                 journal=None):
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
    journal = journal or finalization(store)
    state, launch = journal.launch_manual_request(identity, lease_id, launch_ref, confirmed=True,
                                                  expected_version=expected_version)
    return {"lease_id": lease_id, "state": launch["state"], "attempt": launch["attempt"],
            "launch_ref": launch["launch_ref"], "finalization_version": state["version"]}


def review_launch_rows(store, identity):
    """(journal, review lease, launch) of every rework/recovery launch of this conversation.

    Schema-5 finalization owns ordinary rework launches; stranded-v1 owns
    fresh-workspace recovery launches. Host use only: the caller redacts.
    """
    rows = []
    for row in finalization(store).read()["reviews"].values():
        if (row.get("scope") == identity and row.get("decision") == "rework"
                and (row.get("launch") or {}).get("state") not in (None, "none")):
            rows.append(("finalization", row["lease_id"], row["launch"]))
    for row in stranded(store).read()["rows"].values():
        if (row["scope"] == identity and row["decision_id"] is not None
                and row["launch"]["state"] != "none"):
            rows.append(("stranded", row["lease_id"], row["launch"]))
    return rows


def request_review_launch_resolve(store, identity, lease_id, launch_ref, *,
                                  expected_finalization_version=None, expected_stranded_version=None):
    """Operator request for one exact launch, in the journal that owns its review row.

    The version the caller pins is that journal's own: a recovery launch of a
    stranded row is pinned by the stranded-v1 version, never the schema-5 one.
    """
    if _active_stranded(store, identity, lease_id) is not None:
        if type(expected_stranded_version) is not int or expected_stranded_version < 0:
            raise WorkParallelError("expected_stranded_version must be a nonnegative integer for a stranded review")
        result = request_rework_launch_manual(store, identity, lease_id, launch_ref, confirmed=True,
                                              expected_version=expected_stranded_version,
                                              journal=stranded(store))
        version = result.pop("finalization_version")
        return {**result, "review_journal": "stranded", "stranded_version": version}
    if type(expected_finalization_version) is not int or expected_finalization_version < 0:
        raise WorkParallelError("expected_finalization_version must be a nonnegative integer")
    result = request_rework_launch_manual(store, identity, lease_id, launch_ref, confirmed=True,
                                          expected_version=expected_finalization_version)
    return {**result, "review_journal": "finalization"}


def _main_review(store, board_store, identity, host, args):
    """Run or resume the cross-store main review saga without a spanning lock.

    A matching expected_review_pin lets unrelated review/wake writes move the
    file version without making this decision stale. On each CAS retry every
    semantic fact and every cross-row admission/approval/workspace invariant is
    re-read before decide; the durable write still pins the fresh file version.
    """
    if host.get("agent_id") is not None and not _workflow_review_fork(store, board_store, host, args):
        raise WorkParallelError("main-session item review required")
    if isinstance(args.get("workflow_review_fork"), str) and host.get("agent_id") is None:
        # The host submits a delegated review fork's decision (spec 6.1); the
        # fork must be the goal's review fork and not the step's implementer.
        if not _workflow_review_fork(store, board_store, {"agent_id": args["workflow_review_fork"]}, args):
            raise WorkParallelError("review fork is not this goal's reviewer or implemented the step")
        host = {**host, "workflow_review_fork": args["workflow_review_fork"]}
    reviews = finalization(store)
    lease_id = args.get("lease_id")
    decision = args.get("decision")
    decision_id = args.get("decision_id")
    evidence = args.get("evidence")
    expected_version = args.get("expected_finalization_version")
    expected_pin = args.get("expected_review_pin")
    if expected_pin is not None:
        from .work_parallel_finalization import review_pin_valid
        if not review_pin_valid(expected_pin):
            raise WorkParallelError("expected_review_pin must be rp1: followed by 64 lowercase hex characters")

    # An existing stranded row owns the decision (including a schema-5 row a
    # host moved after main rendered it). Tombstone old_version is the exact
    # finalization version at which that captured row was removed.
    if _active_stranded(store, identity, lease_id) is not None:
        _converge_legacy_review(store, lease_id)
        return _stranded_review(store, board_store, identity, host, args, reviews.read()["version"])

    # With a semantic pin, retry only the finalization CAS. Each attempt reads
    # and revalidates every fact again. Without one, the legacy version rule is
    # one-shot and unchanged.
    attempts = COORDINATION_BIND_ATTEMPTS if expected_pin is not None else 1
    for attempt in range(attempts):
        fstate = reviews.read()
        row = fstate["reviews"].get(lease_id)
        if row is None:
            lease = store.read()["leases"].get(lease_id)
            if not lease or lease["scope"] != identity:
                raise WorkParallelError("unknown conversation lease")
            _publish_pending_review(store, identity, lease)
            fstate = reviews.read()
            if _active_stranded(store, identity, lease_id) is not None:
                return _stranded_review(store, board_store, identity, host, args, fstate["version"])
            row = fstate["reviews"][lease_id]
        workflow_root = None
        if row["decision"] is None:
            from .work_parallel_finalization import review_pin
            if expected_pin is not None:
                if review_pin(row) != expected_pin:
                    raise WorkParallelConflict("review changed; reread")
            elif expected_version != fstate["version"]:
                raise WorkParallelConflict("Work finalization changed; reread")
            if decision not in ("approve", "rework"):
                raise WorkParallelError("decision must be approve or rework")
            # Fresh on every CAS attempt: lease scope/item/generation/artifact and
            # receipt, approval generation/root fingerprint, and workspace HEAD.
            moved = _revalidate_review(store, board_store, row, decision)
            if moved == "stranded":
                if decision == "approve":
                    side = _active_stranded(store, identity, lease_id) or {}
                    reason = str(side.get("reason") or "workspace unavailable")[:300]
                    raise WorkParallelError("workspace unavailable: " + reason
                                            + "; approve refused; rework creates a fresh workspace")
                # The pin matched the exact ordinary row just moved. Revalidate
                # the new stranded row fresh, then decide it under its own CAS.
                return _stranded_review(store, board_store, identity, host, args, reviews.read()["version"],
                                        pin_prevalidated=True)
            if decision in ("approve", "rework"):
                # Approval workflow (spec 4.7): per-QA verdicts, a structured
                # rework, and escalation past the rework limit.
                from .work_workflow_parallel import review_apply, review_target
                lease_row = store.read()["leases"].get(lease_id)
                workflow_root = review_target(board_store, lease_row) if lease_row else None
                if workflow_root is not None:
                    try:
                        entry = review_apply(board_store, workflow_root, lease_row, decision, args,
                                             max_rework=store.read()["policy"]["max_rework"],
                                             reviewer=_workflow_reviewer(host, board_store, workflow_root),
                                             dry_run=True)
                    except tree.WorkPlanError as error:
                        raise WorkParallelError(str(error)) from None
                    if entry["decision"] == "escalated":
                        review_apply(board_store, workflow_root, lease_row, decision, args,
                                     max_rework=store.read()["policy"]["max_rework"],
                                     reviewer=_workflow_reviewer(host, board_store, workflow_root))
                        return {"lease_id": lease_id, "decision": "escalated", "root_id": workflow_root,
                                "state": "escalated", "detail": "rework limit reached; resolve the escalation "
                                "with work_workflow_resolve (escalate_model, escalate_main or cancel)"}
            if decision == "rework":
                # Fresh max_rework and current cross-row capacity/admission.
                from .work_parallel import REWORK_HARD_REFUSALS, rework_admission
                current = store.read()
                code, refusal = rework_admission(current, lease_id, now=time.time(),
                                                 grant=_rework_grant(store, current, lease_id))
                if code in REWORK_HARD_REFUSALS:
                    raise WorkParallelError(refusal)

        replacement_id = row.get("replacement_lease_id")
        if decision == "rework" and replacement_id is None:
            replacement_id = hashlib.sha256((lease_id + chr(0) + str(decision_id)).encode()).hexdigest()[:32]
        from .work_parallel_finalization import decision_digest
        body_digest = decision_digest(lease_id, decision, evidence,
                                      args.get("findings"), args.get("rework_instructions"))
        try:
            # Read before the roll-forward archives the run.
            changed = _approval_changed(coordination(store).read(), board_store.read(), lease_id)
            fstate, row = reviews.decide(
                lease_id, decision, decision_id, evidence, decision_body_digest=body_digest,
                expected_version=fstate["version"], replacement_lease_id=replacement_id,
                expected_review_pin=expected_pin if row["decision"] is None else None)
            if workflow_root is not None:
                from .work_workflow_parallel import review_apply
                review_apply(board_store, workflow_root, store.read()["leases"][lease_id], decision, args,
                             max_rework=store.read()["policy"]["max_rework"],
                             reviewer=_workflow_reviewer(host, board_store, workflow_root))
            rolled = _roll_forward(store, board_store, identity, host, lease_id)
            if workflow_root is not None:
                from .work_workflow_parallel import step_approved_after_board
                step_approved_after_board(board_store, workflow_root)
            return {**rolled, "approval_changed": changed}
        except WorkParallelConflict:
            if expected_pin is None or attempt + 1 == attempts:
                raise
    raise WorkParallelConflict("Work finalization state changed; reread")


def _board_snapshot(board_store):
    from .work_workflow_parallel import snapshot
    return snapshot(board_store)


def _rework_grant(store, ledger_document, lease_id):
    lease = ledger_document["leases"].get(lease_id) or {}
    from .work_rework_grants import grant
    return grant(store.path, lease.get("scope", ""), lease.get("item_id", "")) if lease else 0


def _workflow_review_fork(store, board_store, host, args):
    """A delegated review fork (spec 6.1) reviews only its goal's step, never one it implemented."""
    lease = store.read()["leases"].get(args.get("lease_id"))
    if not lease:
        return False
    from .work_workflow_parallel import review_target
    from .work_workflow import record
    root_id = review_target(board_store, lease)
    if root_id is None:
        return False
    delegation = (record(_board_snapshot(board_store), root_id) or {}).get("delegation") or {}
    agent = host.get("agent_id")
    implementers = {row.get("host_id") for row in store.read()["leases"].values()
                    if row.get("item_id") == lease["item_id"] and row.get("host_id")}
    return (delegation.get("role") == "review" and delegation.get("state") == "running"
            and delegation.get("agent_id") == agent and agent not in implementers)


def _workflow_reviewer(host, board_store, root_id):
    """Who decided a workflow review: main, or the goal's review fork (never its implementer)."""
    agent = (host or {}).get("agent_id") or (host or {}).get("workflow_review_fork")
    if agent is None:
        return {"kind": "main", "id": "main"}
    from .work_workflow import record
    delegation = (record(_board_snapshot(board_store), root_id) or {}).get("delegation") or {}
    if delegation.get("role") != "review" or delegation.get("agent_id") != agent:
        raise WorkParallelError("only main or this goal's review fork reviews it")
    return {"kind": "review_fork", "id": str(agent), "model": str(delegation.get("model") or "main")}


CLOSE_RESOLVED_LIMIT = 24


def close_resolved_reviews(store, board_store, identity, *, limit=CLOSE_RESOLVED_LIMIT):
    """End undecided reviews whose Work item is already resolved on the board.

    Main may complete an item itself, or remove it, while a fork result for it
    still waits in review; no review path could then close it, and "Awaiting
    main review" stayed forever (G:\\ciel-Walkie 2026-11-15: n1869/n1871/n1873
    done, n1536-n1538 removed). The result was not reviewed: the decision is
    ``close`` (never ``approve``), its evidence says so, the board is not
    changed and the workspace is retained. A stranded no-report row for such an
    item is closed too. Decided sagas of removed or done items close too
    (user decision 2026-11-21): the decision is never applied to the board.
    """
    from .work_parallel_finalization import decision_digest
    board = board_store.read()
    nodes = {node["id"]: node for node in board["nodes"]}
    closed = []
    reviews = finalization(store)
    for row in list(reviews.read()["reviews"].values()):
        if len(closed) >= limit:
            break
        if row["scope"] != identity or not (
                row["state"] == "pending" or row["decision"] == "rework"
                or (row["decision"] == "close" and row["state"] == "deciding")):
            continue
        node = nodes.get(row["item_id"])
        if node is not None and node.get("status") != "done":
            continue
        ledger_state = store.read()
        lease = ledger_state["leases"].get(row["lease_id"])
        if (lease is None or lease["state"] not in ("submitted", "accepted", "done")
                or lease["artifact"] != row["artifact"]):
            continue
        how = "removed from the board" if node is None else "completed on the board"
        ws = row["workspace"]
        kept = ("the fork ran with no workspace, so nothing is retained" if ws is None else
                f"the workspace is retained at {ws['path']} ({ws['head']})")
        evidence = (f"Closed unreviewed: Work item {row['item_id']} was {how}. "
                    f"The board was not changed; {kept}.")
        try:
            fstate = reviews.read()
            if fstate["reviews"].get(row["lease_id"]) != row:
                continue
            if row["decision"] == "rework":
                replacement = ledger_state["leases"].get(row["replacement_lease_id"])
                if replacement is not None and replacement["state"] not in ("done", "orphaned", "unreported"):
                    continue  # Do not cancel a possibly live replacement.
                evidence += (f" Prior rework decision {row['decision_id']} ({row['decision_digest']}); "
                             f"replacement {row['replacement_lease_id']}.")
                reviews.close_decided_rework(row["lease_id"], evidence[:2000], expected_version=fstate["version"])
            elif row["decision"] is None:
                reviews.decide(row["lease_id"], "close", "close-resolved:" + row["lease_id"], evidence[:2000],
                               decision_body_digest=decision_digest(row["lease_id"], "close", evidence[:2000]),
                               expected_version=fstate["version"])
            result = _roll_forward(store, board_store, identity, {"internal": True}, row["lease_id"])
        except (WorkParallelConflict, WorkParallelError):
            continue
        closed.append({"lease_id": row["lease_id"], "item_id": row["item_id"], "reason": how,
                       "state": result.get("state")})
    for row in list(stranded(store).read()["rows"].values()):
        if len(closed) >= limit:
            break
        if (row["scope"] != identity or row["state"] in ("done", "superseded", "recovered")
                or row["decision_id"] is not None):
            continue
        node = nodes.get(row["item_id"])
        if node is not None and node.get("status") != "done":
            continue
        try:
            _close_stranded(store, row["lease_id"], undecided_only=True)
        except WorkParallelConflict:
            continue
        closed.append({"lease_id": row["lease_id"], "item_id": row["item_id"],
                       "reason": "removed from the board" if node is None else "completed on the board",
                       "state": "done"})
    return closed


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


def _closed_rework_already_archived(coord_state, lease, row):
    """Recognize an archived rework even after its archive entry was pruned."""
    lease_id = row["lease_id"]
    archived = next((entry for entry in reversed(coord_state["archive"])
                     if entry["run"]["lease_id"] == lease_id), None)
    if archived is not None:
        return bool(archived["run"].get("replaced_by"))
    if lease_id in coord_state["runs"] or not coord_state["archive_dropped"] or not lease:
        return False
    receipt = coord_state["receipts"].get(row["completion_receipt"]) or {}
    return (lease["state"] == "done" and not lease["slot"]
            and all(lease[key] == row[key] for key in ("scope", "item_id", "plan_version", "artifact"))
            and receipt.get("lease_id") == lease_id and receipt.get("kind") == "completion"
            and re.fullmatch(r"(?:workspace rebound|reworked without a workspace) to resume lease [0-9a-f]{32}",
                             lease.get("cleanup_proof") or "") is not None)


def _roll_forward_stages(store, board_store, identity, host, lease_id):
    reviews = finalization(store)
    fstate = reviews.read()
    row = fstate["reviews"][lease_id]
    decision = row["decision"]
    # Every replay uses the journaled immutable body, never retry arguments.
    evidence = row["evidence"]
    coord = coordination(store)

    if decision in ("approve", "close"):
        if not row["stages"]["accepted"]:
            current = store.read()
            store.main_accept(lease_id, plan_version=row["plan_version"], artifact=row["artifact"],
                              evidence=evidence, expected_version=current["version"])
            fstate, row = reviews.mark(lease_id, "accepted", expected_version=reviews.read()["version"])
        # The result is retained by default. Cleanup remains a separately
        # verified policy; retention is safe and is itself the proof.
        cleanup = (UNCONFINED_CLEANUP_PROOF if row["workspace"] is None else
                   "retained verified workspace " + row["workspace"]["path"] + " at " + row["workspace"]["head"])
        if not row["stages"]["workspace"]:
            fstate, row = reviews.mark(lease_id, "workspace", expected_version=reviews.read()["version"])
        if not row["stages"]["board"]:
            if decision == "approve":
                _board_accept(board_store, row["item_id"], row["artifact"], evidence)
            fstate, row = reviews.mark(lease_id, "board", expected_version=reviews.read()["version"])
        if not row["stages"]["lease"]:
            current = store.read()
            store.finalize_accepted(lease_id, plan_version=row["plan_version"], artifact=row["artifact"],
                                    evidence=evidence, cleanup_proof=cleanup,
                                    expected_version=current["version"])
            fstate, row = reviews.mark(lease_id, "lease", expected_version=reviews.read()["version"])
        if not row["stages"]["archive"]:
            archived_rework = decision == "close" and _closed_rework_already_archived(
                coord.read(), store.read()["leases"].get(lease_id), row)
            if not archived_rework:
                _archive_retry(coord, lease_id, {"state": "done",
                                             "outcome": ("main accepted" if decision == "approve" else
                                                         "closed unreviewed: Work item resolved on the board"),
                                             "receipt_digest": row["completion_receipt"],
                                             "artifact_digest": row["artifact"], "published": False},
                           completion_receipt=row["completion_receipt"])
            fstate, row = reviews.mark(lease_id, "archive", state_value="finalized",
                                       expected_version=reviews.read()["version"])
        return {"decision": decision, "lease_id": lease_id, "item_id": row["item_id"],
                "state": "finalized", "version": store.read()["version"],
                "finalization_version": fstate["version"]}

    if decision != "rework":
        raise WorkParallelError("decision must be approve or rework")
    replacement_id = row["replacement_lease_id"]
    leases = store.read()["leases"]
    # Celly 2026-11-20 (n1108): a rework replacement lease can be released by
    # orphan sweep before its coordination run exists (stages.accepted=True,
    # stages.board=False), or left reserved without adopting its P0 intent.
    released = (row["stages"]["accepted"] and not row["stages"]["board"]
                and replacement_id not in leases
                and replacement_id not in coord.read()["runs"])
    unadopted_p0 = (not row["stages"]["board"] and replacement_id in leases
                    and leases[replacement_id]["state"] == "reserved"
                    and p0_state(store).approval_intent(
                        replacement_id, scope=identity, item_id=row["item_id"]) is None)
    if not row["stages"]["accepted"] or released or unadopted_p0:
        from .work_parallel import rework_admission
        current = store.read()
        owner = None
        p0_intent = _p0_continuation_intent(
            store, board_store, identity, row["item_id"], "rework", lease_id)
        if replacement_id not in current["leases"]:
            code, refusal = rework_admission(
                current, lease_id, now=time.time(),
                excluded_lease_ids=p0_state(store).active_lease_ids(), grant=_rework_grant(store, current, lease_id))
            if p0_intent is not None and code in ("paused", "capacity", "launch_rate"):
                code, refusal = "", ""
            if not code:
                # The replacement belongs to this conversation's launcher; with
                # no exact live proof (ctl or REST for another conversation, an
                # unbound router) it waits for that conversation's own host.
                owner = _proven_owner(host, identity)
                if owner is None:
                    code, refusal = OWNER_OFFLINE, OWNER_OFFLINE_TEXT
            if code:
                # Admission is not available after the durable decision. The
                # submitted predecessor stays the duplicate fence; the host pump
                # retries through review_roll_forward. Never silently stuck.
                fstate, row = reviews.await_capacity(lease_id, code, refusal,
                                                     expected_version=reviews.read()["version"])
                return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                        "state": "awaiting_capacity", "finalization_version": fstate["version"],
                        "retry": "host_pump", "reason": refusal[:300]}
        try:
            existing = current["leases"].get(replacement_id)
            if p0_intent is not None and existing is not None and p0_intent["lease_id"] is None:
                p0_state(store).adopt_reserved(
                    p0_intent["intent_id"], current["leases"],
                    expected_version=p0_state(store).read()["version"])
                p0_intent = p0_state(store).read()["intents"][p0_intent["intent_id"]]
            if existing is None:
                store.reserve_rework(
                    lease_id, replacement_id, owner=owner,
                    expected_version=current["version"],
                    admission_bypass="p0" if p0_intent is not None else None,
                    p0_intent=p0_intent)
                if p0_intent is not None:
                    p0_state(store).adopt_reserved(
                        p0_intent["intent_id"], store.read()["leases"],
                        expected_version=p0_state(store).read()["version"])
        except WorkParallelConflict:
            # Another host's roll-forward reserved first under the same CAS:
            # the deterministic replacement id makes its lease this one.
            if replacement_id not in store.read()["leases"]:
                raise
        if not row["stages"]["accepted"] or released:
            fstate, row = reviews.mark(lease_id, "accepted", expected_version=reviews.read()["version"])
    missing_workspace = (row["workspace"] is not None and not row["stages"]["board"]
                         and (store.read()["leases"].get(replacement_id) or {}).get("workspace") is None)
    if not row["stages"]["workspace"] or released or missing_workspace:
        # A result with no workspace has nothing to rebind: the replacement
        # resumes unconfined, like its predecessor.
        if row["workspace"] is not None:
            old = store.read()["leases"][lease_id]
            from .work_parallel_workspace import handoff_rebind
            ws = row["workspace"]
            rebound = handoff_rebind(old["workspace"]["repo"], lease_id, replacement_id,
                                     prior_generation=str(row["generation"]),
                                     replacement_generation=str(row["replacement_generation"]),
                                     receipt_digest=ws["receipt_digest"], expected_path=ws["path"],
                                     expected_branch=ws["branch"], expected_head=ws["head"],
                                     expected_dirty=ws["dirty"], prior_terminal=True, prior_slot=False)
            current = store.read()
            replacement = current["leases"].get(replacement_id)
            require(replacement is not None, "rework replacement lease unavailable")
            if (replacement.get("workspace") or {}).get("path") != rebound["path"]:
                store.attach_workspace(replacement_id, rebound, expected_version=current["version"])
        if not row["stages"]["workspace"] or released or missing_workspace:
            fstate, row = reviews.mark(lease_id, "workspace", expected_version=reviews.read()["version"])
    if not row["stages"]["board"]:
        approval = _assignment_approval(coord, board_store, row["root_id"], identity)
        current = coord.read()
        replacement_workspace = store.read()["leases"][row["replacement_lease_id"]].get("workspace")
        if row["replacement_lease_id"] not in current["runs"]:
            old_run = current["runs"].get(lease_id)
            require(old_run is not None and (old_run.get("exit") or {}).get("kind") == "completion",
                    "completion coordination record unavailable")
            coord.record_assignment(identity, row["replacement_lease_id"], {
                "item_id": row["item_id"], "root_id": row["root_id"],
                "priority": row["priority"], "role": "resume",
                "generation": row["replacement_generation"],
                "approval_generation": approval[0] if approval else old_run["approval_generation"],
                "assigned_todos": copy.deepcopy(old_run["assigned_todos"]),
                "fingerprint": approval[1] if approval else row["fingerprint"],
                "workspace": (None if replacement_workspace is None else
                              {key: replacement_workspace[key] for key in ("path", "branch", "head")}),
                "created_at": time.time(),
            }, expected_version=current["version"],
                **_replacement_p0_authorization(store, identity, row["replacement_lease_id"], row["item_id"]))
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
        if old_lease is not None and old_lease["state"] != "done":
            proof = (("reworked without a workspace" if row["workspace"] is None else "workspace rebound")
                     + " to resume lease " + row["replacement_lease_id"])
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
                         "workspace": None if row["workspace"] is None else row["workspace"]["path"]}}


def _active_stranded(store, identity, lease_id):
    row = stranded(store).read()["rows"].get(lease_id)
    if row is None or row["scope"] != identity or row["state"] == "done":
        return None
    return row


def _stranded_review(store, board_store, identity, host, args, finalization_version, *,
                     pin_prevalidated=False):
    """Main's decision on a stranded-v1 row: approve refused; rework recovers fresh.

    A matching stranded pin replaces the finalization-version check. A decision
    made on the exact earlier schema-5 projection is also valid when its version
    equals the tombstone old_version. A host restart drops its pin mapping and
    therefore uses this explicit version fallback.
    """
    rows = stranded(store)
    lease_id = args.get("lease_id")
    decision = args.get("decision")
    decision_id = args.get("decision_id")
    evidence = args.get("evidence")
    expected_pin = args.get("expected_review_pin")
    _observe_supersessions(store, identity)
    state = rows.read()
    row = state["rows"][lease_id]
    if row["state"] == "superseded":
        raise WorkParallelError("stranded result superseded by lease " + row["superseded"]["lease_id"]
                                + "; reread")
    if row["state"] == "intent":
        raise WorkParallelError("stranded result is not yet published; reread")
    pin_for_store = None
    if row["decision_id"] is None:
        if decision not in ("approve", "rework"):
            raise WorkParallelError("decision must be approve or rework")
        if decision == "approve":
            if row["kind"] == "no_report":
                raise WorkParallelError("no worker report: approve refused; rework creates a fresh workspace")
            raise WorkParallelError("workspace unavailable: " + row["reason"]
                                    + "; approve refused; rework creates a fresh workspace")
        if not pin_prevalidated:
            tombstone = row.get("legacy_review_removed") or {}
            if expected_pin is not None:
                from .work_parallel_finalization import review_pin_valid
                from .work_parallel_stranded import review_pin
                if not review_pin_valid(expected_pin):
                    raise WorkParallelError("expected_review_pin must be rp1: followed by 64 lowercase hex characters")
                if review_pin(row) == expected_pin:
                    pin_for_store = expected_pin
                elif args.get("expected_finalization_version") != tombstone.get("old_version"):
                    # An exact earlier schema-5 projection is carried across the
                    # frozen-schema tombstone by its old version. The pin itself
                    # is never persisted (R1); every stranded fact is rechecked.
                    raise WorkParallelConflict("review changed; reread")
            elif args.get("expected_finalization_version") not in (
                    finalization_version, tombstone.get("old_version")):
                raise WorkParallelConflict("Work finalization changed; reread")
        # Fresh terminal receipt, approval and supersession checks.
        _revalidate_stranded(store, board_store, row)
    elif decision != "rework":
        raise WorkParallelError("stranded review already decided differently")
    from .work_parallel_finalization import decision_digest
    body_digest = decision_digest(lease_id, "rework", evidence,
                                  args.get("findings"), args.get("rework_instructions"))
    replacement_id = row["replacement_lease_id"] or hashlib.sha256(
        (lease_id + chr(0) + str(decision_id)).encode()).hexdigest()[:32]
    _finalization_write(rows, lambda version: rows.decide(
        lease_id, decision_id, body_digest, evidence, replacement_id, expected_version=version,
        expected_review_pin=pin_for_store))
    return _stranded_roll_forward(store, board_store, identity, host, lease_id)


def _stranded_roll_forward(store, board_store, identity, host, lease_id):
    """Advance a durable stranded decision from the sidecar alone; never silently stuck."""
    rows = stranded(store)
    try:
        return _stranded_roll_forward_stages(store, board_store, identity, host, lease_id)
    except WorkParallelConflict:
        raise
    except ValueError as error:
        row = rows.read()["rows"][lease_id]
        if row["state"] in ("recovered", "done", "superseded"):
            raise
        message = str(error)[:300] or type(error).__name__
        _finalization_write(rows, lambda version: rows.block(
            lease_id, "stage_failed", message, expected_version=version))
        return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                "state": "deciding", "retry": "host_pump", "reason": message,
                "stranded_version": rows.read()["version"]}


def _stranded_roll_forward_stages(store, board_store, identity, host, lease_id):
    rows = stranded(store)
    # Supersession is checked before every replay: a recovery waiting for
    # admission must never start once another execution of the item exists.
    _observe_supersessions(store, identity)
    row = rows.read()["rows"][lease_id]
    if row["state"] == "superseded":
        return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                "state": "superseded", "superseding_lease_id": row["superseded"]["lease_id"],
                "stranded_version": rows.read()["version"]}
    require(row["scope"] == identity and row["decision_id"] is not None,
            "no durable stranded decision to roll forward")
    replacement_id = row["replacement_lease_id"]
    coord = coordination(store)

    def mark(stage, state_value=None):
        _finalization_write(rows, lambda version: rows.mark(
            lease_id, stage, state_value=state_value, expected_version=version))
        return rows.read()["rows"][lease_id]

    # Celly 2026-11-19/20: recovery leases can be released before their
    # coordination run exists, or left reserved without adopting their P0 intent
    # / attaching their workspace after a mid-stage failure. Recover all three.
    leases = store.read()["leases"]
    released = (row["stages"]["reserved"] and not row["stages"]["coordination"]
                and replacement_id not in leases
                and replacement_id not in coord.read()["runs"])
    unadopted_p0 = (not row["stages"]["coordination"] and replacement_id in leases
                    and leases[replacement_id]["state"] == "reserved"
                    and p0_state(store).approval_intent(
                        replacement_id, scope=identity, item_id=row["item_id"]) is None)
    if not row["stages"]["reserved"] or released or unadopted_p0:
        current = store.read()
        p0_intent = _p0_continuation_intent(
            store, board_store, identity, row["item_id"],
            "stranded_recovery", lease_id)
        existing = current["leases"].get(replacement_id)
        if existing is None:
            code, refusal = _stranded_admission(current, identity, row)
            if p0_intent is not None and code in ("paused", "capacity", "launch_rate"):
                code, refusal = "", ""
            owner = None
            if not code:
                owner = _proven_owner(host, identity)
                if owner is None:
                    code, refusal = OWNER_OFFLINE, OWNER_OFFLINE_TEXT
            if code:
                _finalization_write(rows, lambda version: rows.block(
                    lease_id, code, refusal, expected_version=version))
                return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                        "state": "awaiting_capacity", "retry": "host_pump", "reason": refusal[:300],
                        "stranded_version": rows.read()["version"]}
            # Recovery, not a rework attempt: the predecessor's counter is kept.
            try:
                store.reserve_stranded_recovery(
                    lease_id, replacement_id, owner=owner,
                    expected_version=current["version"],
                    admission_bypass="p0" if p0_intent is not None else None,
                    p0_intent=p0_intent)
                if p0_intent is not None:
                    p0_state(store).adopt_reserved(
                        p0_intent["intent_id"], store.read()["leases"],
                        expected_version=p0_state(store).read()["version"])
            except WorkParallelConflict:
                if replacement_id not in store.read()["leases"]:
                    raise
        elif p0_intent is not None and p0_intent["lease_id"] is None:
            p0_state(store).adopt_reserved(
                p0_intent["intent_id"], current["leases"],
                expected_version=p0_state(store).read()["version"])
        if not row["stages"]["reserved"] or released:
            row = mark("reserved")
    if replacement_id not in store.read()["leases"]:
        # The recovery lease was reserved and later released without starting
        # (its P0 intent was cancelled). Celly 2026-11-19: the lookups below then
        # raised KeyError on every pump, an uncaught router error each 16 s.
        # Nothing can roll forward from here; main decides the result again.
        return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"],
                "state": "recovery_lease_released", "retry": "main_review",
                "reason": "the recovery lease of this stranded result was released before it started; "
                          "decide the result again with work_parallel_item_review",
                "stranded_version": rows.read()["version"]}
    missing_workspace = (not row["stages"]["coordination"]
                         and (store.read()["leases"].get(replacement_id) or {}).get("workspace") is None)
    if not row["stages"]["workspace"] or released or missing_workspace:
        # The retained worktree cannot be verified: a fresh owned worktree.
        # The broken one stays retained, never reused or removed. A re-reserved
        # recovery gets the worktree its id already owns (prepare verifies it).
        from .work_parallel_workspace import prepare
        fresh = prepare(row["binding"]["repo"], replacement_id)
        current = store.read()
        if (current["leases"][replacement_id].get("workspace") or {}).get("path") != fresh["path"]:
            store.attach_workspace(replacement_id, fresh, expected_version=current["version"])
        if not row["stages"]["workspace"] or released or missing_workspace:
            row = mark("workspace")
    if not row["stages"]["coordination"]:
        approval = _assignment_approval(coord, board_store, row["root_id"], identity)
        cstate = coord.read()
        replacement_ws = (store.read()["leases"].get(replacement_id) or {}).get("workspace")
        require(replacement_ws is not None, "stranded replacement workspace unavailable")
        old_run = cstate["runs"].get(lease_id)
        if old_run is None:
            old_run = next((entry["run"] for entry in reversed(cstate["archive"])
                            if entry["run"]["lease_id"] == lease_id), None)
        require(old_run is not None and (old_run.get("exit") or {}).get("digest") == row["completion_receipt"],
                "stranded coordination record unavailable")
        if replacement_id not in cstate["runs"] and old_run.get("replaced_by") != replacement_id:
            coord.record_assignment(identity, replacement_id, {
                "item_id": row["item_id"], "root_id": row["root_id"], "priority": row["priority"],
                "role": "resume", "generation": row["replacement_generation"],
                "approval_generation": approval[0] if approval else old_run["approval_generation"],
                "assigned_todos": copy.deepcopy(old_run["assigned_todos"]),
                "fingerprint": approval[1] if approval else row["fingerprint"],
                "workspace": {key: replacement_ws[key] for key in ("path", "branch", "head")},
                "created_at": time.time()}, expected_version=cstate["version"],
                **_replacement_p0_authorization(store, identity, replacement_id, row["item_id"]))
        cstate = coord.read()
        if lease_id in cstate["runs"]:
            coord.supersede(lease_id, replacement_id, expected_version=cstate["version"])
        else:
            archived = next((entry for entry in reversed(cstate["archive"])
                             if entry["run"]["lease_id"] == lease_id), None)
            require(archived is not None and archived["run"].get("replaced_by") == replacement_id,
                    "replacement coordination outcome unknown")
        row = mark("coordination")
    if not row["stages"]["retired"]:
        current = store.read()
        old = current["leases"].get(lease_id)
        if old is not None and old["state"] == "submitted":
            store.retire_stranded(lease_id, replacement_id,
                                  proof="stranded result recovered in a fresh workspace by lease " + replacement_id,
                                  expected_version=current["version"])
        row = mark("retired", state_value="recovered")
    workspace = (store.read()["leases"].get(replacement_id) or {}).get("workspace") or {}
    return {"decision": "rework", "lease_id": lease_id, "item_id": row["item_id"], "state": "rework_ready",
            "stranded_version": rows.read()["version"],
            "dispatch": {"kind": "native_resume", "lease_id": replacement_id, "role": "resume",
                         "generation": row["replacement_generation"], "workspace": workspace.get("path"),
                         "recovery": row["kind"]}}


COORDINATION_BIND_ATTEMPTS = 8


def _finalization_write(reviews, write):
    """Run one host-observed finalization write from a version read just before it.

    Finalization is one device-wide CAS domain: an unrelated review, wake or
    launch write can move its version between a handler's read and its write.
    The handler supplies no version of its own and the store re-checks every
    precondition on the state it then reads, so the write is repeated from a
    fresh read. Only that version race is repeated; any other refusal is final.
    When the race persists, the wording is the one parallelEvent retries.
    """
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        try:
            return write(reviews.read()["version"])
        except WorkParallelConflict:
            continue
    raise WorkParallelConflict("Work finalization state changed; reread")


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


NATIVE_IDLE_STATUSES = frozenset(("idle", "completed", "failed", "killed"))


def _publish_native_idle_review(store, identity, row):
    """Publish a submitted result's review from a listed idle fork row.

    The row is the same positive host evidence a turn-end ``reconcile
    state=idle`` carries (the mod's own $.agent.list), so the review no longer
    waits for main's next turn end (Andy n2505, 2026-11-20). Only a submitted,
    slot-free lease whose run holds this agent's completion receipt qualifies;
    an absent row is never death and publishes nothing. Returns "published",
    "" when nothing applied, or a bounded reason for one row that failed.
    """
    if row["status"] not in NATIVE_IDLE_STATUSES or _has_review_row(store, row["lease_id"]):
        return ""
    lease = store.read()["leases"].get(row["lease_id"])
    run = coordination(store).read().get("runs", {}).get(row["lease_id"])
    if (lease is None or lease["state"] != "submitted" or lease["slot"]
            or lease["host_id"] != row["agent_id"] or run is None
            or run["agent_id"] != row["agent_id"] or (run.get("exit") or {}).get("kind") != "completion"):
        return ""
    try:
        _observe_idle_and_publish_review(store, identity, lease,
                                         "Native agent.list status: " + row["status"])
    except ValueError as error:
        return (str(error) or type(error).__name__)[:200]
    return "published" if _has_review_row(store, row["lease_id"]) else ""


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
                                absent_agent_id, absent_from_complete_list, *, caller_nonce=None):
    """Publish a review after the current host proves this exact fork absent."""
    lease = store.read()["leases"].get(lease_id)
    # A lease with no workspace (session folder not a git repository) is
    # published the same way; its review carries no snapshot.
    require(lease is not None and lease["scope"] == identity and lease["state"] == "submitted"
            and lease["slot"] is False and lease.get("host_id") is not None,
            "submitted restart lease required")
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
    side = stranded(store).read()["rows"].get(lease_id)
    if ((review is not None and review["decision"] is not None)
            or (side is not None and (side["decision_id"] is not None or side["state"] == "done"))):
        exact_run()
        return {**result, "review_pending": False}
    # R2 B2: every exact fact above is checked first (each refusal writes
    # nothing). A fork that another live Work host owns is never observed
    # terminal from this list. An unknown owner (no attach record: a legacy,
    # pre-deploy or unattached attempt; a new-build lease is always attached)
    # keeps the pre-B2 behaviour (lead ruling D): refusing it left the
    # submitted result with no resolving path.
    exact_run()
    from .work_parallel_attachments import OTHER_LIVE
    if _restart_owner_class(store, lease_id, caller_nonce) == OTHER_LIVE:
        _record_owner_wait(store, identity, lease_id, "owner_live", caller_nonce)
        from .work_parallel_failures import OWNER_LIVE_TEXT
        raise WorkParallelError(OWNER_LIVE_TEXT)
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
    reviewed = _has_review_row(store, lease_id)
    _observe_idle_and_publish_review(store, identity, current,
                                     current["idle_proof"] if reviewed else RESTART_TERMINAL_PROOF)
    return {**result, "review_pending": _has_review_row(store, lease_id)}


def _has_review_row(store, lease_id):
    return (lease_id in finalization(store).read()["reviews"]
            or lease_id in stranded(store).read()["rows"])


def _launch_handoff_drift(launch):
    match = re.search(r"(?:^|; )handoff_drift=(true|false|unknown|not_applicable)(?:;|$)",
                      str(launch.get("proof") or ""))
    if match is None or match.group(1) == "unknown":
        return "unknown"
    if match.group(1) == "not_applicable":
        return "not_applicable"
    return match.group(1) == "true"


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
    row = next((row for row in finalization(store).read()["reviews"].values()
                if row.get("replacement_lease_id") == lease["id"]), None)
    if row is None:
        row = next((row for row in stranded(store).read()["rows"].values()
                    if row["replacement_lease_id"] == lease["id"]), None)
    return row


def _owner_classes(store, lease_ids, caller_nonce):
    """{lease_id: owner class} relative to the caller, reading each store once.

    A rework/recovery replacement names its exact spawning process: the launch
    attempt's nonce, when that attempt is bound to the lease's fork. Any other
    lease is owned by the launcher recorded at reservation.
    """
    leases = store.read()["leases"]
    side = attachments(store)
    try:
        doc = side.read()
    except Exception:
        return {lease_id: "unknown" for lease_id in lease_ids}
    launch_nonce = {}
    try:
        for journal, key in ((finalization(store), "reviews"), (stranded(store), "rows")):
            for row in journal.read()[key].values():
                launch = row.get("launch") or {}
                if row.get("replacement_lease_id") and launch.get("host_nonce") and launch.get("agent_id"):
                    launch_nonce[row["replacement_lease_id"]] = (launch["host_nonce"], launch["agent_id"])
    except Exception:
        # An unreadable launch journal hides which process spawned a
        # replacement: nothing is proven, so every owner reads unknown.
        return {lease_id: "unknown" for lease_id in lease_ids}
    result = {}
    for lease_id in lease_ids:
        lease = leases.get(lease_id) or {}
        nonce, agent = launch_nonce.get(lease_id, (None, None))
        if nonce is not None and agent == lease.get("host_id"):
            result[lease_id] = side.owner_class(nonce, caller_nonce, doc=doc)
        else:
            result[lease_id] = side.lease_owner_class(lease.get("owner"), caller_nonce, doc=doc)
    return result


def _restart_owner_class(store, lease_id, caller_nonce):
    """The owner class of one lease's fork relative to the caller (see _owner_classes)."""
    return _owner_classes(store, [lease_id], caller_nonce)[lease_id]


def _owner_unknown_text():
    from .work_parallel_failures import OWNER_UNKNOWN_TEXT
    return OWNER_UNKNOWN_TEXT


def _record_owner_wait(store, identity, lease_id, code, owner_nonce):
    from .work_parallel_failures import WorkParallelFailureLog
    # The failure sidecar hashes this bounded dedup key again; this digest keeps
    # a full lease id + nonce below that API's 64-character input limit.
    key = "r2:" + hashlib.sha256((str(lease_id or "-") + "\0" + str(owner_nonce or "unknown"))
                                  .encode("utf-8")).hexdigest()[:32]
    WorkParallelFailureLog.beside(store.path).record(identity, code, lease_id=lease_id, dedup_key=key)


def _launch_journal(store, replacement_lease_id):
    """The journal that owns a replacement's launch record: finalization unless stranded-v1."""
    if any(row["replacement_lease_id"] == replacement_lease_id
           for row in stranded(store).read()["rows"].values()):
        return stranded(store)
    return finalization(store)


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


def _merge_handoff_drift(before, after):
    first, second = before.get("changed_before_bind"), after.get("changed_before_bind")
    if True in (first, second):
        return True
    if first is False and second is False and before.get("current_digest") == after.get("current_digest"):
        return False
    if first == "not_applicable" and second == "not_applicable":
        return "not_applicable"
    return "unknown"


def _bind_rework_launch(store, lease, host, handoff_drift="unknown"):
    """Mark a main-review rework launch bound; legacy resume leases are untouched.

    Finalization is a separate CAS domain. Any unrelated review/wake write can
    move its version after the read, so re-read on every bounded attempt. An
    identical replay for an already-bound launch is idempotent.
    """
    if lease["role"] != "resume":
        return
    reviews = _launch_journal(store, lease["id"])
    for _ in range(COORDINATION_BIND_ATTEMPTS):
        fstate = reviews.read()
        if not any(row.get("replacement_lease_id") == lease["id"]
                   for row in (fstate.get("reviews") or fstate.get("rows") or {}).values()):
            return
        try:
            reviews.launch_bound(lease["id"], host.get("host_id"), host.get("agent_name"),
                                 handoff_drift=handoff_drift, expected_version=fstate["version"])
            return
        except WorkParallelConflict:
            continue
    # The ledger and coordination halves may already be durable. This exact
    # wording is retried by parallelEvent; a later idempotent bind repairs the
    # finalization and workspace halves too.
    raise WorkParallelConflict("Work finalization state changed; reread")


MAX_ATTRIBUTION_NAMES = 64
_REWORK_FORK_NAME = re.compile(r"work-rework-(.{1,200})-a([1-9][0-9]{0,2})\Z", re.DOTALL)


def _launch_rows(store, identity, reviews):
    """Every rework launch record of this conversation: schema-5 and stranded-v1."""
    rows = [row for row in reviews["reviews"].values()
            if row["scope"] == identity and row["decision"] == "rework"
            and row["replacement_lease_id"] is not None]
    rows += [row for row in stranded(store).read()["rows"].values()
             if row["scope"] == identity and row["replacement_lease_id"] is not None]
    return rows


def _fork_attribution(store, identity, names):
    """Host-only read: which rework launch of this conversation a fork name belongs to.

    The mod asks this for a native fork it cannot attribute from its own state
    (a hot reload, or a lease that has left its status read). It reads the
    finalization launch rows and the ledger and writes nothing.

    - ``current``: the name is the dispatch name of a launch that is unresolved
      or bound, and its replacement lease is still open.
    - ``stale``: the name belongs to a known launch of this conversation, but to
      an earlier attempt (``earlier_attempt``), to the latest attempt after it
      was resolved (``attempt_resolved``), to a terminal replacement lease
      (``lease_terminal``), or to a launch whose replacement lease is done or
      gone (``lease_ended``).
    - ``unknown``: no evidence. The caller must not stop a fork on this answer.

    An earlier attempt is recognized by its audit row, or by the replacement
    lease id and a lower attempt number in ``work-rework-<lease>-a<N>``: a new
    attempt is recorded only after the one before it was resolved. A name that
    matches more than one launch with different answers is ``unknown``.
    """
    from .work_parallel_finalization import UNRESOLVED_LAUNCH_STATES
    require(type(names) is list and len(names) <= MAX_ATTRIBUTION_NAMES
            and all(text(name, 200) for name in names), "bounded rework fork names required")
    reviews = finalization(store).read()
    leases = store.read()["leases"]
    launches = _launch_rows(store, identity, reviews)

    def judge(name, row):
        launch = row["launch"]
        lease = leases.get(row["replacement_lease_id"])
        if name == launch["dispatch_name"]:
            if launch["state"] == "spawn_denied":
                return "stale", "attempt_resolved"
            if launch["state"] not in UNRESOLVED_LAUNCH_STATES + ("bound",):
                return None
            if lease is None or lease["scope"] != identity or lease["state"] == "done":
                return "stale", "lease_ended"
            if lease["state"] in ("unreported", "orphaned"):
                return "stale", "lease_terminal"
            return "current", None
        if any(entry["dispatch_name"] == name for entry in launch["audit"]):
            return "stale", "earlier_attempt"
        parsed = _REWORK_FORK_NAME.fullmatch(name)
        if (parsed is not None and parsed.group(1) == row["replacement_lease_id"]
                and int(parsed.group(2)) < launch["attempt"]):
            return "stale", "earlier_attempt"
        return None

    forks = []
    for name in names:
        found = [(row, verdict) for row in launches for verdict in (judge(name, row),)
                 if verdict is not None]
        if not found or len({verdict for _, verdict in found}) != 1:
            forks.append({"name": name, "status": "unknown"})
            continue
        row, (status, reason) = found[0]
        answer = {"name": name, "status": status, "lease_id": row["replacement_lease_id"],
                  "attempt": row["launch"]["attempt"]}
        if reason is not None:
            answer["reason"] = reason
        forks.append(answer)
    return {"forks": forks, "finalization_version": reviews["version"]}


def _fork_identity(store, identity, ids, names):
    """Host-only read for the SendMessage revival gate: is this agent a Work fork?

    Matches every ledger lease of this conversation in any state (a lease is
    deleted only while it is an unbound reservation) and every rework launch
    row, including the dispatch names of earlier attempts in its bounded audit.
    Writes nothing. An unreadable store raises, so the caller fails closed.
    Only matched queries are returned; an unmatched one has no durable record,
    which does not prove the agent is not a Work fork (the launch audit is
    bounded and an earlier attempt's agent id is kept only once it bound).
    """
    def bounded(values):
        return values is None or (type(values) is list and len(values) <= MAX_ATTRIBUTION_NAMES
                                  and all(text(value, 200) for value in values))
    require(bounded(ids) and bounded(names) and len(ids or []) + len(names or []) >= 1,
            "bounded fork ids/names required")
    from .work_parallel import ACTIVE_STATES
    ledger_state = store.read()
    reviews = finalization(store).read()
    leases = {key: row for key, row in ledger_state["leases"].items() if row["scope"] == identity}
    launches = _launch_rows(store, identity, reviews)
    matches = []

    def lease_match(query, by, lease):
        if by == "id":
            current = lease.get("host_id") == query
            hit = current
        else:
            agent_name = lease.get("agent_name")
            hit = agent_name == query or lease.get("dispatch_name") == query
            current = agent_name == query or (agent_name is None and lease.get("dispatch_name") == query)
        if hit:
            matches.append({"query": query, "by": by, "owner": "lease", "lease_id": lease["id"],
                            "lease_state": lease["state"],
                            "active": lease["state"] in ACTIVE_STATES and lease.get("host_id") is not None,
                            "current": current})

    def launch_match(query, by, row):
        launch = row["launch"]
        if by == "id":
            current = launch["agent_id"] == query
            hit = current
        else:
            current = launch["dispatch_name"] == query
            hit = current or any(entry["dispatch_name"] == query for entry in launch["audit"])
        if hit:
            replacement = leases.get(row["replacement_lease_id"])
            matches.append({"query": query, "by": by, "owner": "launch",
                            "lease_id": row["replacement_lease_id"],
                            "lease_state": None if replacement is None else replacement["state"],
                            "active": False, "current": current})

    for by, values in (("id", ids or []), ("name", names or [])):
        for query in values:
            for lease in leases.values():
                lease_match(query, by, lease)
            for row in launches:
                launch_match(query, by, row)
    return {"matches": matches, "ledger_version": ledger_state["version"],
            "finalization_version": reviews["version"]}


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
    # Never-launched coordination runs and P0 intents. Outside the lease digest:
    # those device-wide files change on every Work write, and a lease release
    # pinned to this preview must not be refused for that.
    try:
        from .work_parallel_reconcile import preview, public
        body["reconcile"] = public(preview(store))
    except (OSError, ValueError, KeyError, TypeError):
        # Unreadable sidecars never hide the lease preview itself.
        body["reconcile"] = None
    return body


def _frame(*values):
    pieces = []
    for value in values:
        raw = str(value).encode("utf-8")
        pieces.append(len(raw).to_bytes(8, "big") + raw)
    return b"".join(pieces)


def _p0_assignment_token(key, intent, lease, run):
    """Stable 256-bit capability from a dedicated private derivation key."""
    require(type(key) is bytes and len(key) == 32, "P0 assignment key")
    root = intent["root_id"] + "@" + str(intent["root_created_at"])
    payload = _frame(
        "ciel-runtime", "p0-assignment", "1", intent["scope"], root,
        intent["item_id"], intent["intent_id"], lease["id"], run["generation"],
        "worker-report-capability")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def run(name, args, board_store, identity, config, save_config, log=None):
    """``log(level, message)`` is the router log, when the caller has one."""
    store = ledger()
    if name == "work_parallel_status":
        # A model tool call carries no host token. Only the authenticated mod's
        # own read, with no worker identity, gets the host lease fields.
        host_read = _authenticated_host_read(args, config)
        if host_read and args.get("projection") == "version":
            # KOBE hotfix 2026-11-18: an event's expected_version needs the
            # ledger version alone, not the whole (paged) host projection.
            return json.dumps({"version": store.read()["version"], "projection": "version"})
        caller = (args.get("_host") or {}).get("launch_nonce") if host_read else None
        return json.dumps(project(store, identity, _board_or_none(board_store),
                                  host_view=host_read, caller_nonce=caller,
                                  config=config if isinstance(config, dict) else None), ensure_ascii=False)
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
    if isinstance(event, str) and event.startswith("wait_boost_"):
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal wait host required")
        from .work_wait_product_adapter import dispatch_wait_event
        return json.dumps(
            dispatch_wait_event(store, board_store, identity, host, args),
            ensure_ascii=False,
        )
    if event == "p0_launch_assignment":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 launch host required")
        intent_id, lease_id = args.get("intent_id"), args.get("lease_id")
        side = p0_state(store)
        intent = side.read()["intents"].get(intent_id)
        lease = store.read()["leases"].get(lease_id)
        require(intent is not None and lease is not None and intent["lease_id"] == lease_id
                and intent["state"] == "reserved" and lease["scope"] == identity
                and lease["state"] == "reserved" and lease["host_id"] is None
                and intent["dispatch_name"] == args.get("dispatch_name")
                and lease.get("dispatch_name") == intent["dispatch_name"],
                "unknown P0 assignment")
        run_row = coordination(store).read()["runs"].get(lease_id)
        require(run_row is not None and run_row["state"] == "reserved", "P0 coordination missing")
        from .work_p0_assignment_key import P0AssignmentKeyStore, P0AssignmentKeyUnavailable
        try:
            key = P0AssignmentKeyStore.beside_ledger(store.path).read()
        except P0AssignmentKeyUnavailable as error:
            raise WorkParallelError(
                "P0 assignment key recovery required; spawn blocked (" + error.reason + ")") from error
        token = _p0_assignment_token(key, intent, lease, run_row)
        token_hash = hash_worker_token(token)
        for attempt in range(3):
            current = store.read()
            if current["leases"][lease_id].get("report_hash") == token_hash:
                break
            try:
                store.set_p0_reserved_capability(
                    lease_id, token_hash, intent["dispatch_name"], p0_intent=intent,
                    expected_version=current["version"])
                break
            except WorkParallelConflict:
                if attempt == 2:
                    raise
        # What the fork is to do (Andy 2026-11-19: the P0 packet carried no
        # title, note or path, so every P0 fork stopped at once asking for its
        # scope and its leaf stayed fenced by that waiting lease).
        try:
            item = _item_details(board_store.read()).get(lease["item_id"], {})
        except Exception:  # noqa: BLE001 - an unreadable board leaves the packet without the text
            item = {}
        return json.dumps({"intent_id": intent_id, "lease_id": lease_id,
                           "dispatch_name": intent["dispatch_name"], "item_id": lease["item_id"],
                           "priority": "P1", "effective_priority": "P0", "role": lease["role"],
                           "generation": run_row["generation"], "plan_version": lease["plan_version"],
                           "workspace": lease.get("workspace"), "worker_token": token,
                           "item_title": _clip(item.get("item_title"), 300), "item_ref": _clip(item.get("item_ref"), 100),
                           "item_note": _clip(item.get("item_note"), 8000), "goal_title": _clip(item.get("goal_title"), 300),
                           "item_path": [_clip(t, 120) for t in (item.get("item_path") or [])[:8]],
                           "dispatch_without_approval": True}, ensure_ascii=False)
    if event == "p0_launch_spawning":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 launch host required")
        # Spawn is key-dependent even after assignment issuance: recovery status
        # and the actual state transition must share the same readiness fence.
        _p0_key_ready(store, "spawn")
        side = p0_state(store)
        state = side.read()
        intent = state["intents"].get(args.get("intent_id"))
        require(intent is not None and intent["lease_id"] == args.get("lease_id")
                and intent["dispatch_name"] == args.get("dispatch_name"),
                "P0 launch identity")
        state, intent = side.transition_intent(
            intent["intent_id"], "spawning", expected_version=state["version"])
        return json.dumps({"intent_id": intent["intent_id"], "lease_id": intent["lease_id"],
                           "dispatch_name": intent["dispatch_name"], "state": intent["state"]},
                          ensure_ascii=False)
    if event in ("p0_launch_denied", "p0_launch_absent"):
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 launch host required")
        intent_id, lease_id, name = (args.get("intent_id"), args.get("lease_id"),
                                     args.get("dispatch_name"))
        side = p0_state(store)
        side_doc = side.read()
        intent = side_doc["intents"].get(intent_id)
        lease = store.read()["leases"].get(lease_id)
        if intent is not None and intent["state"] == "cancelled" and lease is None:
            return json.dumps({"state": "cancelled", "released": True}, ensure_ascii=False)
        require(intent is not None and lease is not None and intent["lease_id"] == lease_id
                and intent["dispatch_name"] == name and lease.get("dispatch_name") == name
                and intent["state"] == "spawning" and lease["state"] == "reserved"
                and lease["host_id"] is None and lease["scope"] == identity,
                "P0 launch cancellation identity")
        if event == "p0_launch_denied":
            require(text(host.get("proof"), 500), "P0 launch deny proof")
            _p0_note_refusal(store, board_store.read(), identity, lease["item_id"], "spawn_denied", host["proof"])
        else:
            require(host.get("absent_dispatch_name") == name
                    and host.get("absent_from_complete_list") is True,
                    "complete P0 absence proof required")
            owner = lease.get("owner")
            require(type(owner) is dict and type(owner.get("session_pid")) is int,
                    "P0 launch owner unavailable")
            observed = process_identity(owner["session_pid"])
            same_host = (host.get("session_pid") == owner["session_pid"]
                         and (host.get("session_process_identity") is None
                              or host.get("session_process_identity") == owner.get("identity")))
            require(not same_host, "same-process P0 launch absence is not terminal proof")
            require(observed != owner.get("identity"),
                    "another live Work host owns this P0 launch")
        current = store.read()
        store.release_unbound(lease_id, proof="P0 native launch absent",
                              expected_version=current["version"])
        coord = coordination(store)
        if lease_id in coord.read()["runs"]:
            coord.cancel_reserved(lease_id, outcome="P0 native launch absent",
                                  expected_version=coord.read()["version"])
        side.transition_intent(intent_id, "cancelled", expected_version=side_doc["version"])
        return json.dumps({"state": "cancelled", "released": True}, ensure_ascii=False)
    if event == "p0_launch_status":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 launch host required")
        side = p0_state(store)
        side_document = side.read()
        open_rows = [intent for intent in side_document["intents"].values()
                     if intent["scope"] == identity
                     and intent["state"] in ("intent", "reserved", "spawning", "bound")]
        from .work_p0_assignment_key import P0AssignmentKeyStore, P0AssignmentKeyUnavailable
        dependent = _p0_key_required(side_document)
        key_status, key_reason = "not_required", None
        if dependent:
            key_store = P0AssignmentKeyStore.beside_ledger(store.path)
            try:
                key_store.read()  # read-only: status never creates or rotates the key
                key_status = "ready"
            except P0AssignmentKeyUnavailable as error:
                key_status = "recovery_required"
                key_reason = error.reason
        intents = [{key: intent[key] for key in (
            "intent_id", "continuation", "state", "lease_id", "dispatch_name", "item_id",
            "predecessor_lease_id")} for intent in open_rows]
        return json.dumps({"intents": intents,
                           "assignment_key": key_status,
                           "assignment_key_reason": key_reason,
                           "scheduling_blocked": key_status == "recovery_required",
                           "spawn_allowed": key_status != "recovery_required"}, ensure_ascii=False)
    if event == "review_close_resolved":
        # Host pump: end reviews whose Work item main already completed or
        # removed on the board (user decision 2026-11-15).
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal review host required")
        return json.dumps({"closed": close_resolved_reviews(store, board_store, identity)}, ensure_ascii=False)
    if event == "review_roll_forward":
        # Host pump retries a durable decision (awaiting_capacity or a blocked
        # stage) from the journal alone. Main never needs to resend its body.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal review host required")
        if _active_stranded(store, identity, args.get("lease_id")) is not None:
            return json.dumps(_stranded_roll_forward(
                store, board_store, identity, host, args.get("lease_id")), ensure_ascii=False)
        return json.dumps(_roll_forward(store, board_store, identity, host, args.get("lease_id")),
                          ensure_ascii=False)
    if event == "review_wake":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal review host required")
        reviews = (stranded(store) if _active_stranded(store, identity, args.get("lease_id")) is not None
                   else finalization(store))
        operation = args.get("operation")
        if operation == "prepare":
            state, wake = _finalization_write(reviews, lambda version: reviews.wake_prepare(
                args.get("lease_id"), expected_version=version))
        elif operation == "record":
            state, wake = _finalization_write(reviews, lambda version: reviews.wake_record(
                args.get("lease_id"), args.get("attempt_id"), args.get("state"),
                expected_version=version))
        else:
            raise WorkParallelError("review wake operation must be prepare or record")
        return json.dumps({"lease_id": args.get("lease_id"), **wake,
                           "finalization_version": state["version"]}, ensure_ascii=False)
    if event == "parked_notice_claim":
        # Host-only: main is told once that a cleared parked worker needs its resume.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal parked notice host required")
        return json.dumps({"claimed": _parked_notice_claim(store, args.get("lease_id"),
                                                           release=args.get("release") is True)})
    if event == "p0_outcome_claim":
        # Host-only: the one notice to main per outcome of one P0 activation.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 outcome host required")
        from .work_parallel_p0_outcomes import P0Outcomes
        require(text(args.get("root_id"), 200) and text(args.get("outcome"), 80), "P0 outcome")
        found = _p0_outcome_key(store, board_store.read(), identity, args["root_id"])
        if found is None:
            return json.dumps({"claimed": False, "reason": "not_effective_p0"})
        claimed = P0Outcomes(store.path).claim(found[0], args["outcome"], release=args.get("release") is True)
        return json.dumps({"claimed": claimed})
    if event == "failure_record":
        # Host-only durable failure log: fixed codes, short ids and times only.
        # The model's forwarded events never carry _host.internal.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal failure host required")
        from .work_parallel_failures import WorkParallelFailureLog
        return json.dumps(WorkParallelFailureLog.beside(store.path).record(
            identity, args.get("code"), lease_id=args.get("lease_id"), dedup_key=args.get("dedup_key")),
            ensure_ascii=False)
    if event == "gate_presence":
        # G5: the mod's live probe result, reported on process start and on a
        # change of (state, version). Display only: it never authorizes or
        # refuses a reservation.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal gate presence host required")
        from .work_parallel_gate_presence import WorkParallelGatePresence
        return json.dumps(WorkParallelGatePresence.beside(store.path).record(
            identity, args.get("state"), gate_version=args.get("gate_version"), probe_ref=args.get("probe_ref")),
            ensure_ascii=False)
    if event == "host_attach":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal Work host attach required")
        result = attachments(store).attach(identity, host.get("launcher_pid"), host.get("launch_nonce"))
        # Make this caller available to this response's host projection without
        # changing the persisted ledger. A fresh status call sends launch_nonce.
        return json.dumps({**result, "session_attachments": attachments(store).summary(identity)}, ensure_ascii=False)
    if event in ("rework_launch_status", "rework_launch_manual_request", "rework_fork_attribution",
                 "fork_identity"):
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal launch host required")
        if event == "rework_fork_attribution":
            return json.dumps(_fork_attribution(store, identity, args.get("names")), ensure_ascii=False)
        if event == "fork_identity":
            return json.dumps(_fork_identity(store, identity, args.get("ids"), args.get("names")),
                              ensure_ascii=False)
        if event == "rework_launch_status":
            status = finalization(store).launch_status(identity, host.get("launch_nonce"))
            recovery = stranded(store).launch_status(identity, host.get("launch_nonce"))
            merged = sorted(status["launches"] + recovery["launches"],
                            key=lambda value: (value["updated_at"], value["lease_id"]))
            return json.dumps({**status, "launches": merged[:64],
                               "truncated": status["truncated"] or recovery["truncated"] or len(merged) > 64,
                               "stranded_version": recovery["stranded_version"]}, ensure_ascii=False)
        # The launch reference pins the exact attempt the person saw; the
        # version here is only this handler's own read.
        journal = (stranded(store) if _active_stranded(store, identity, args.get("lease_id")) is not None
                   else finalization(store))
        return json.dumps(_finalization_write(
            journal, lambda version: request_rework_launch_manual(
                store, identity, args.get("lease_id"), args.get("launch_ref"),
                confirmed=host.get("user_confirmed") is True, expected_version=version,
                journal=journal)),
            ensure_ascii=False)
    if event in ("rework_launch_intent", "rework_launch_denied", "rework_spawn_observed",
                 "rework_launch_absent", "rework_launch_manual_resolve", "rework_launch_present"):
        # The mod forwards a main-model work_parallel_event with only
        # _host.agent_id=null, which is also actor None. Launch facts change
        # whether a fresh worker capability may be minted, so they are accepted
        # only from the mod's own internal calls, which carry _host.internal.
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal launch host required")
        reviews = _launch_journal(store, args.get("lease_id"))
        replacement = store.read()["leases"].get(args.get("lease_id"))
        if replacement is None or replacement["scope"] != identity:
            raise WorkParallelError("unknown rework replacement")
        lease_id = args.get("lease_id")
        if event in ("rework_launch_absent", "rework_launch_manual_resolve"):
            asserted = host.get("absent_dispatch_name")
            if asserted is None or asserted != args.get("dispatch_name"):
                raise WorkParallelError("host must assert this exact launch name absent from its agent list")
            journal_state = reviews.read()
            owner_nonce = next((row["launch"]["host_nonce"] for row in
                                journal_state.get("reviews", journal_state.get("rows", {})).values()
                                if row.get("replacement_lease_id") == lease_id), None)
            from .work_parallel_attachments import OTHER_LIVE, UNKNOWN
            owner_class = attachments(store).owner_class(owner_nonce, host.get("launch_nonce"))
            if owner_class == OTHER_LIVE:
                _record_owner_wait(store, identity, lease_id, "owner_live", owner_nonce)
                raise WorkParallelError("Another live Work host owns this launch attempt; waiting for that host.")
            if event == "rework_launch_absent":
                if owner_class == UNKNOWN:
                    _record_owner_wait(store, identity, lease_id, "owner_unknown", owner_nonce)
                    raise WorkParallelError(_owner_unknown_text())
                def write(version):
                    return reviews.launch_absent(lease_id, asserted, host.get("launch_nonce"),
                                                 absent_agent_id=host.get("absent_agent_id"),
                                                 expected_version=version)
            else:
                # Lead ruling: a person-confirmed manual resolution is allowed
                # for unknown owners; only a different live host refuses it.
                def write(version):
                    return reviews.launch_manual(lease_id, asserted, host.get("launch_nonce"),
                                                 absent_agent_id=host.get("absent_agent_id"),
                                                 launch_ref=args.get("launch_ref"),
                                                 expected_version=version)
        elif event == "rework_launch_present":
            asserted = host.get("present_dispatch_name")
            if asserted is None or asserted != args.get("dispatch_name"):
                raise WorkParallelError("host must assert this exact launch name present in its agent list")
            if host.get("present_agent_id") is None:
                raise WorkParallelError("present launch agent required")

            def write(version):
                return reviews.launch_present(lease_id, asserted, host.get("present_agent_id"),
                                              launch_ref=args.get("launch_ref"), expected_version=version)
        elif event == "rework_launch_intent":
            def write(version):
                return reviews.launch_intent(lease_id, args.get("dispatch_name"), args.get("generation"),
                                             host_nonce=host.get("launch_nonce"), expected_version=version)
        elif event == "rework_launch_denied":
            def write(version):
                return reviews.launch_denied(lease_id, args.get("dispatch_name"),
                                             host.get("proof") or args.get("proof"),
                                             expected_version=version)
        else:
            def write(version):
                return reviews.spawn_observed(lease_id, args.get("dispatch_name"), args.get("agent_id"),
                                              expected_version=version)
        # Each of these reads the finalization version and writes with it. An
        # unrelated write in between is not a refusal of this launch fact.
        state, launch = _finalization_write(reviews, write)
        if event == "rework_launch_intent":
            # The exact name is durable on the reserved lease before any spawn.
            ledger_now = store.read()
            if ledger_now["leases"][lease_id].get("dispatch_name") != launch["dispatch_name"]:
                store.name_reserved_resume(lease_id, launch["dispatch_name"],
                                           expected_version=ledger_now["version"])
        return json.dumps({"lease_id": lease_id, **_launch_view(launch),
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
        if source is None:
            source = next((row for row in stranded(store).read()["rows"].values()
                           if row["replacement_lease_id"] == replacement_id
                           and row["state"] == "recovered"), None)
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
    if event in ("reserve", "reserve_next", "p0_reserve", "p0_reserve_next"):
        if actor is not None or host.get("fork") is not True or not ready:
            raise WorkParallelError("verified coordinator fork capability required")
        p0_only = event.startswith("p0_")
        explicit = event in ("reserve", "p0_reserve")
        p0_board = board_store.read()
        p0_next = (args.get("item_id") if explicit else
                   next((row["item_id"] for row in _p0_next_rows(store, p0_board, identity,
                                                                 include_refused=p0_only)), None))
        try:
            assignment = _reserve_assignment(
                store, board_store, identity, host, version,
                item_id=args.get("item_id") if explicit else None,
                role=args.get("role", "worker"), p0_only=p0_only, log=log,
            )
        except WorkParallelError as error:
            message = str(error)
            if p0_next is not None and not re.search(r"no eligible candidate|lease bound|state changed", message):
                from .work_parallel_p0_outcomes import refusal_code
                _p0_note_refusal(store, p0_board, identity, p0_next, refusal_code(message), message)
            raise
        lease = store.read()["leases"][assignment["lease_id"]]
        intent = p0_state(store).approval_intent(
            lease["id"], scope=identity, item_id=lease["item_id"])
        if p0_only:
            require(intent is not None, "P0 assignment intent missing")
        if intent is not None:
            _p0_clear_refusal(store, p0_board, identity, lease["item_id"])
            # An ordinary reserve that picked an effective-P0 root returns the
            # same P0 launch shape, so the host launches it under the exact
            # deterministic dispatch name with its P0 assignment capability.
            return json.dumps({"dispatch": "ready", "intent_id": intent["intent_id"],
                               "continuation": "initial", "intent_state": intent["state"],
                               "dispatch_name": intent["dispatch_name"],
                               "effective_priority": "P0", "dispatch_without_approval": True,
                               **{key: assignment.get(key) for key in (
                                   "lease_id", "item_id", "priority", "role", "generation",
                                   "plan_version", "workspace")}}, ensure_ascii=False)
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
            host.get("absent_agent_id"), host.get("absent_from_complete_list"),
            caller_nonce=host.get("launch_nonce")), ensure_ascii=False)
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
        if "generation" in args or "receipt_digest" in args:
            generation, receipt = args.get("generation"), args.get("receipt_digest")
            require(host.get("internal") is True and args.get("complete") is False
                    and len(valid) == 1 and not skipped and integer(generation, 1) and digest(receipt),
                    "exact host recovery observation required")
            row = valid[0]
            lease = leases[row["lease_id"]]
            run = coordination(store).read().get("runs", {}).get(lease["id"])
            require(lease["state"] == "submitted" and lease["slot"] is False
                    and row["status"] in NATIVE_IDLE_STATUSES and run is not None
                    and run["generation"] == generation and run["agent_id"] == row["agent_id"]
                    and run["exit"] == {"kind": "completion", "digest": receipt},
                    "exact durable completion receipt required for review recovery")
            require(_restart_owner_class(store, lease["id"], host.get("launch_nonce"))
                    in ("own", "earlier_child", "gone"), "current Work host ownership required")
        if skipped and not valid:
            raise WorkParallelError("native observation names no bound fork of this conversation: "
                                    + skipped[0]["reason"])
        # A skipped row hides its lease from this list, so it is not complete.
        complete = args.get("complete") is True and not skipped
        if complete:
            from .work_parallel import ACTIVE_STATES
            bound = [key for key, lease in leases.items() if lease["state"] in ACTIVE_STATES and lease.get("host_id")]
            if any(value in ("other_live", "unknown")
                   for value in _owner_classes(store, bound, host.get("launch_nonce")).values()):
                # This list cannot prove another live/unknown process's forks
                # absent. Healthy rows are still recorded, but completeness is
                # downgraded and no other process's record gets missing_since.
                complete = False
        state, changed = evidence.observe(identity, leases, valid, complete=complete,
                                          expected_version=expected)
        observed_terminal, published = [], []
        for row in valid:
            outcome = _observe_coordination_row(store, identity, leases[row["lease_id"]], row)
            if outcome == "observed_terminal":
                observed_terminal.append(row["lease_id"])
                continue
            if outcome:
                skipped.append({"lease_id": row["lease_id"], "reason": outcome})
            # A refused native update (KOBE n878: "idle" after a recorded
            # "failed" is immutable) does not void the listed idle row: the
            # publication checks the lease, run, agent and receipt itself.
            outcome = _publish_native_idle_review(store, identity, row)
            if outcome == "published":
                published.append(row["lease_id"])
            elif outcome:
                skipped.append({"lease_id": row["lease_id"], "reason": outcome})
        return json.dumps({"evidence_version": state["version"], "changed": changed,
                           "skipped": skipped[:MAX_NATIVE_SKIPPED],
                           "observed_terminal": observed_terminal[:MAX_NATIVE_SKIPPED],
                           "reviews_published": published[:MAX_NATIVE_SKIPPED]}, ensure_ascii=False)
    if event == "host_salvage":
        if actor is not None:
            raise WorkParallelError("coordinator host salvage required")
        d = store.read()
        lease = d["leases"].get(args.get("lease_id"))
        # A lease reserved with no workspace (the session folder is not a git
        # repository) has nothing to observe: its salvage receipt records
        # workspace None. Refusing it kept the slot of a fork that ended without
        # a report forever (KOBE 2026-11-20 n964).
        if not lease or lease["scope"] != identity or (lease.get("workspace") is not None
                                                       and not lease["workspace"]):
            raise WorkParallelError("unknown salvage workspace")
        from .work_parallel_workspace import salvage_observe
        coord = coordination(store)
        _repair_lost_coordination_bind(store, lease)
        run_row = coord.read().get("runs", {}).get(lease["id"])
        if run_row is None:
            raise WorkParallelError("missing salvage coordination record")
        # Native terminal status is positive exit evidence; the lease may still
        # hold its slot until this salvage receipt is committed.
        prior_exit = run_row.get("exit") or {}
        if prior_exit.get("kind") == "host_salvage":
            # Idempotent: a repeated boundary returns the same durable receipt.
            detail = coord.detail(prior_exit["digest"])
            return json.dumps({"lease_id": lease["id"], "receipt_digest": prior_exit["digest"],
                               "salvaged": True,
                               **({"workspace_unavailable": True}
                                  if detail["workspace"] is not None
                                  and str(detail["workspace"]["diff_stat"]).startswith("workspace unavailable: ")
                                  else {})}, ensure_ascii=False)
        if lease.get("workspace") is None:
            detail = {"done": [], "remaining": list(run_row.get("assigned_todos") or [lease["item_id"]]),
                      "blockers": [str(args.get("reason") or "native fork ended without report")[:2000]],
                      "workspace": None,
                      "next_step": "Resume the recorded assignment; the fork ran without a workspace."}
            cstate = coord.read()
            _, receipt = coord.persist_receipt(lease["id"], "host_salvage", detail,
                                               expected_version=cstate["version"])
            return json.dumps({"lease_id": lease["id"], "receipt_digest": receipt, "salvaged": True},
                              ensure_ascii=False)
        from .work_parallel_workspace import WorkspaceError
        unavailable = None
        try:
            observed = salvage_observe(lease["workspace"]["repo"], lease["id"],
                                       generation=str(run_row["generation"]), terminal=True,
                                       slot=False, config_dir=None)
        except WorkspaceError as error:
            # A broken worktree must not hold the slot forever (the mod retries
            # salvage at every boundary). Record what is known: the recorded
            # binding, explicitly marked unavailable. No observed fact is invented.
            unavailable = _workspace_unavailable_reason(lease)
            if unavailable is None:
                raise
            binding = lease["workspace"]
            observed = {"path": binding["path"], "branch": binding["branch"], "head": binding["head"],
                        "dirty": False, "changed_files": [],
                        "diff_stat": "workspace unavailable: " + unavailable[:300]}
            del error
        workspace = {"path": observed["path"], "branch": observed["branch"],
                     "head": observed["head"], "dirty": observed["dirty"],
                     "changed_files": observed["changed_files"], "diff_stat": observed["diff_stat"] or "no tracked diff"}
        detail = {"done": [], "remaining": list(run_row.get("assigned_todos") or [lease["item_id"]]),
                  "blockers": [str(args.get("reason") or "native fork ended without report")[:2000]],
                  "workspace": workspace,
                  "next_step": ("Recover in a fresh workspace; the retained workspace is unavailable."
                                if unavailable else "Resume the recorded assignment from this verified workspace.")}
        cstate = coord.read()
        _, receipt = coord.persist_receipt(lease["id"], "host_salvage", detail,
                                           expected_version=cstate["version"])
        return json.dumps({"lease_id": lease["id"], "receipt_digest": receipt, "salvaged": True,
                           **({"workspace_unavailable": True} if unavailable else {})}, ensure_ascii=False)
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
    if event == "p0_reacquire_complete":
        if actor is not None or host.get("internal") is not True:
            raise WorkParallelError("verified mod-internal P0 reacquire host required")
        side = p0_state(store)
        side_doc = side.read()
        send = side_doc["sends"].get(args.get("send_id"))
        require(send is not None and send["parent_intent_id"] == args.get("parent_intent_id")
                and send["lease_id"] == ident
                and send["child_generation"] == args.get("child_generation")
                and send["send_receipt"] == args.get("send_receipt")
                and send["host_id"] == lease.get("host_id")
                and send["native_name"] == (lease.get("agent_name") or lease.get("dispatch_name")),
                "unknown P0 reacquire completion")
        outcome = args.get("outcome")
        terminal_requested = {"sent": "delivered", "failed": "rolled_back"}.get(outcome)
        if send["state"] in ("delivered", "rolled_back"):
            require(send["state"] == terminal_requested,
                    "P0 reacquire completion outcome changed")
            return json.dumps({"state": send["state"], "outcome": outcome, "terminal": True,
                               "slot_rolled_back": send["state"] == "rolled_back",
                               "recovery_required": False,
                               **({"retry": "new_child"} if send["state"] == "rolled_back" else {})},
                              ensure_ascii=False)
        if outcome == "sent":
            _, row = side.complete_reacquire_send(
                send["send_id"], outcome="sent", expected_version=side_doc["version"])
            return json.dumps({"state": row["state"], "outcome": "sent", "terminal": True,
                               "recovery_required": False}, ensure_ascii=False)
        if outcome == "uncertain":
            _, row = side.complete_reacquire_send(
                send["send_id"], outcome="uncertain", expected_version=side_doc["version"])
            return json.dumps({"state": row["state"], "outcome": None, "terminal": False,
                               "recovery_required": True, "retry": "same_child"}, ensure_ascii=False)
        require(outcome == "failed", "invalid P0 reacquire completion outcome")
        try:
            d, _ = store.rollback_p0_reacquire(
                ident, p0_send=send, expected_version=store.read()["version"])
        except (WorkParallelError, WorkParallelConflict):
            current = side.read()
            _, row = side.complete_reacquire_send(
                send["send_id"], outcome="uncertain", expected_version=current["version"])
            return json.dumps({"state": row["state"], "outcome": None, "terminal": False,
                               "slot_rolled_back": False, "recovery_required": True,
                               "retry": "same_child"}, ensure_ascii=False)
        receipt_digest = hashlib.sha256((send["send_receipt"] + "\0" + str(d["version"])).encode()).hexdigest()
        operation_digest = hashlib.sha256((send["send_id"] + "\0" + receipt_digest).encode()).hexdigest()
        current = side.read()
        _, row = side.complete_reacquire_send(
            send["send_id"], outcome="failed", rollback_receipt_digest=receipt_digest,
            operation_digest=operation_digest, expected_version=current["version"])
        return json.dumps({"state": row["state"], "outcome": "failed", "terminal": True,
                           "slot_rolled_back": True, "recovery_required": False,
                           "retry": "new_child"}, ensure_ascii=False)
    if event in ("bind", "reconcile", "release", "wait", "reacquire", "p0_reacquire", "unreported"):
        if actor is not None:
            raise WorkParallelError("coordinator host observation required")
        if event == "unreported":
            if d["version"] != version:
                raise WorkParallelConflict("Work parallel state changed; reread")
            d, published = _terminate_unreported_with_intent(store, identity, ident, host.get("proof"))
            return json.dumps({"lease_id": ident, "version": d["version"], "state": "unreported",
                               "slot_released": True, "item_kept": True,
                               **({"stranded_review": True} if published else {})}, ensure_ascii=False)
        if event == "wait":
            d, _ = store.wait(ident, proof=host.get("proof"), expected_version=version)
            return json.dumps({"lease_id": ident, "version": d["version"], "waiting": True,
                               "slot_released": True, "item_id": lease["item_id"], "item_kept": True}, ensure_ascii=False)
        if event in ("reacquire", "p0_reacquire"):
            side = p0_state(store)
            if event == "p0_reacquire":
                _p0_key_ready(store, "reacquire")
            open_send = side.open_send_for_lease(ident)
            if open_send is not None and _p0_send_reported_after(store, open_send):
                # The worker's own authenticated report after this child proves
                # the send was delivered (design 6a: definite success). Builds
                # before the host recorded completions left such children open,
                # and every later resume returned the old child with no slot.
                side.complete_reacquire_send(open_send["send_id"], outcome="sent",
                                             expected_version=side.read()["version"])
                side = p0_state(store)
                open_send = None
            if open_send is not None:
                return json.dumps({"lease_id": ident,
                                   "parent_intent_id": open_send["parent_intent_id"],
                                   "send_id": open_send["send_id"],
                                   "child_generation": open_send["child_generation"],
                                   "state": open_send["state"],
                                   "send_receipt": open_send["send_receipt"],
                                   "ledger_before_version": open_send["slot_before_version"],
                                   "ledger_after_version": open_send["slot_after_version"],
                                   "host_id": open_send["host_id"],
                                   "native_name": open_send["native_name"],
                                   "recovery_required": open_send["state"] == "uncertain"},
                                  ensure_ascii=False)
            parent = side.approval_intent(
                ident, scope=identity, item_id=lease["item_id"])
            if event == "p0_reacquire":
                require(parent is not None and parent["state"] == "bound",
                        "lease is not effective bound P0")
            send = None
            if parent is not None and parent["state"] == "bound":
                # Design 8a: key recovery_required blocks P0 reacquire
                # continuations before any child or ledger write.
                _p0_key_ready(store, "reacquire")
                side = p0_state(store)
                _, send = side.prepare_reacquire_send(
                    parent["intent_id"], lease, ledger_before_version=version,
                    expected_version=side.read()["version"])
            d, _ = store.reacquire(
                ident, expected_version=version,
                admission_bypass="p0" if send is not None else None,
                p0_send=send)
            result = {"lease_id": ident, "version": d["version"], "slot_reacquired": True,
                      "item_id": lease["item_id"]}
            if send is None and _wait_lane_consume(
                    store, identity, d["leases"][ident], kind="reacquire", outcome="reacquired",
                    ledger_before=version, ledger_after=d["version"]):
                result["wait_boost"] = "reacquired"
            if send is not None:
                side = p0_state(store)
                _, send = side.mark_reacquire_slot(
                    send["send_id"], d["version"], expected_version=side.read()["version"])
                result.update(parent_intent_id=send["parent_intent_id"],
                              send_id=send["send_id"], child_generation=send["child_generation"],
                              state=send["state"], send_receipt=send["send_receipt"],
                              ledger_before_version=send["slot_before_version"],
                              ledger_after_version=send["slot_after_version"],
                              host_id=send["host_id"], native_name=send["native_name"],
                              recovery_required=False)
            return json.dumps(result, ensure_ascii=False)
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
            # launch_bound is write-once for drift: a bound replay ignores this.
            _bind_rework_launch(store, lease, host)
        elif event == "bind":
            _check_rework_launch(store, lease, host)
            from .work_parallel_workspace import WorkspaceError, handoff_drift
            # Preflight before any ledger/coordination write: an incomplete
            # new-build baseline refuses here and leaves every store untouched.
            preflight = handoff_drift(lease["workspace"]["repo"], ident) if lease.get("workspace") else {
                "changed_before_bind": "not_applicable"}
            agent = host.get("host_id")
            # Check the coordination run before the ledger commit, so a conflict
            # never leaves the ledger bound and the run unbound. A lease minted by
            # an older build has no run at all; like release, it binds ledger-only.
            if _run_bind_refusal(coordination(store).read(), ident, agent):
                raise WorkParallelError("run is not bindable")
            d, _ = store.bind(ident, agent, expected_version=version,
                              agent_name=host.get("agent_name"))
            _bind_coordination_run(store, ident, agent, host.get("agent_name"))
            try:
                after = handoff_drift(lease["workspace"]["repo"], ident) if lease.get("workspace") else {
                    "changed_before_bind": "not_applicable"}
            except WorkspaceError:
                # Ledger and coordination are already committed: never leak the
                # error into a split bind. Record unknown (never false) and leave
                # the bounded diagnostic the frozen coordination schema allows.
                _native_diagnostic(coordination(store), lease["scope"], lease["priority"])
                after = {"changed_before_bind": "unknown"}
            _bind_rework_launch(store, lease, host, _merge_handoff_drift(preflight, after))
        if event == "bind":
            side = p0_state(store)
            intent = side.approval_intent(ident, scope=identity, item_id=lease["item_id"])
            if (intent is not None and intent["continuation"] != "reacquire"
                    and intent["state"] in ("reserved", "spawning")):
                if intent["state"] == "reserved":
                    _, intent = side.transition_intent(
                        intent["intent_id"], "spawning", expected_version=side.read()["version"])
                side.transition_intent(intent["intent_id"], "bound",
                                       expected_version=side.read()["version"])
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
        if event == "resume":
            # Main resumes a parked worker (SendMessage): a scope edit since the
            # assignment does not strand it; a goal no longer approved does.
            if not _root_approved(store, board, lease):
                raise WorkParallelError("Work item " + lease["item_id"] + "'s goal is not approved for execution; "
                                        "approve the goal before resuming the worker")
        elif not _approval_current(store, board, lease):
            raise WorkParallelError("Work approval scope changed; reconcile before continuing")
        if event == "accept" and lease["role"] != "reviewer":
            # Retired reviewer contract: the host selects the one legacy
            # reviewer result; a caller-named id must be exactly that row.
            reviewer = select_legacy_reviewer(d, lease, identity, args.get("reviewer_lease_id"))
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
    if current and current.get("state") in ("unreported", "orphaned", "done"):
        side = p0_state(store)
        side.close_lease_intents(ident, expected_version=side.read()["version"])
    workspace = current.get("workspace")
    if workspace and event in ("bind", "reconcile"):
        from .work_parallel_workspace import WorkspaceError, bind, set_activity
        if event == "bind":
            bind(workspace["repo"], ident, current["host_id"])
        elif host.get("state") == "idle":
            try:
                set_activity(workspace["repo"], ident, current["host_id"], idle=True,
                             proof=current["idle_proof"])
            except WorkspaceError:
                # A submitted result in a broken worktree goes on to the stranded
                # review below; anything else keeps the error.
                if current.get("state") != "submitted" or _workspace_unavailable_reason(current) is None:
                    raise
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


LEGACY_REVIEWER_NONE = "independent reviewer result required before acceptance"
LEGACY_REVIEWER_MULTIPLE = "multiple legacy reviewer results; resolve in the web dashboard or menu"
LEGACY_REVIEWER_MISMATCH = "reviewer_lease_id does not name the single eligible legacy reviewer result"


def _legacy_reviewer_candidates(d, worker, identity):
    """Submitted reviewer rows an older build minted for exactly this worker result."""
    return [row for row in d["leases"].values()
            if row["role"] == "reviewer" and row["scope"] == identity and worker["scope"] == identity
            and row["item_id"] == worker["item_id"] and row["plan_version"] == worker["plan_version"]
            and row["host_id"] != worker["host_id"] and row["state"] == "submitted"]


def select_legacy_reviewer(d, worker, identity, named=None):
    """Host selection of the legacy reviewer a ``reviewing`` worker consumes.

    Reviewer forks are retired and no caller is told reviewer ids. Exactly one
    eligible row is used; none or several are refused before any write.
    """
    candidates = _legacy_reviewer_candidates(d, worker, identity)
    if not candidates:
        raise WorkParallelError(LEGACY_REVIEWER_NONE)
    if len(candidates) > 1:
        raise WorkParallelError(LEGACY_REVIEWER_MULTIPLE)
    if named is not None and named != candidates[0]["id"]:
        raise WorkParallelError(LEGACY_REVIEWER_MISMATCH)
    return candidates[0]


def _legacy_reviews(d, identity, details):
    """Workers an older build left in ``reviewing``: bounded, no reviewer identity."""
    rows = []
    for lease in sorted(d["leases"].values(), key=lambda row: (row["priority"], row["created_at"], row["id"])):
        if lease["scope"] != identity or lease["role"] == "reviewer" or lease["state"] != "reviewing":
            continue
        count = len(_legacy_reviewer_candidates(d, lease, identity))
        rows.append({"lease_id": lease["id"], "item_id": lease["item_id"], "priority": lease["priority"],
                     "reviewers": "none" if count == 0 else "one" if count == 1 else "multiple",
                     "item_title": _clip((details.get(lease["item_id"]) or {}).get("item_title"),
                                         MODEL_TEXT_LIMIT)})
    return rows[:MODEL_LEASE_LIMIT]


def legacy_accept(store, board_store, identity, lease_id, *, expected_version, evidence):
    """Operator drain of one legacy ``reviewing`` worker with the host-selected reviewer."""
    d = store.read()
    lease = d["leases"].get(lease_id)
    if lease is None or lease["scope"] != identity or lease["role"] == "reviewer":
        raise WorkParallelError("unknown conversation lease")
    if lease["state"] != "reviewing":
        raise WorkParallelError("lease is not a legacy reviewed result")
    if not _approval_current(store, board_store.read(), lease):
        raise WorkParallelError("Work approval scope changed; reconcile before continuing")
    reviewer = select_legacy_reviewer(d, lease, identity)
    d, _ = store.accept_with_reviewer(lease_id, reviewer["id"], plan_version=lease["plan_version"],
                                       artifact=lease["artifact"], evidence=evidence,
                                       expected_version=expected_version)
    return {"lease_id": lease_id, "state": d["leases"][lease_id]["state"], "version": d["version"]}


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
            if board.get("workflows") and workflow_gate(board, current["id"]):
                from .work_workflow import gate_text
                return f"Work item {item_id} cannot dispatch: " + gate_text(current["id"], workflow_gate(board, current["id"]))
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
