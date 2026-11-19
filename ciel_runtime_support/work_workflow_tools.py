"""Approval workflow surfaces: MCP tools, router hooks and session-folder bodies.

The state machine is work_workflow (board records); host approval checks are
work_workflow_approval; prompts are work_workflow_prompts. This module wires
them into the router: main's tools (proposal, approval request registration,
status, resolve, delegate), the host-only event tool, the side intake hooks
(P1 claim marking, P3 ACK exclusion, the create context), the fork assignment
packet, the completion-report and review checks, and the immutable bodies in
the session folder ``workflow/<root_id>/`` (spec 7).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import work_plan_tree as tree
from . import work_workflow as wf
from .work_workflow_approval import is_workflow_event, parse_event, verify
from .work_workflow_parallel import (folder, load_body, model_overrides, report_check, report_record,  # noqa: F401
                                     review_apply, review_target, save_body, snapshot, step_approved_after_board)
from .work_workflow_prompts import render

MAIN_TOOLS = ("work_proposal_submit", "work_approval_requested", "work_proposal_status", "work_workflow_resolve",
              "work_workflow_delegate")
HOST_TOOLS = ("work_side_workflow_event",)
NAMES = MAIN_TOOLS + HOST_TOOLS


def schemas() -> list[dict[str, Any]]:
    root = {"type": "string", "description": "Workflow goal (root) id"}
    return [
        {"name": "work_proposal_submit",
         "description": "Approval workflow: submit or revise the technical proposal of a side-registered goal "
                        "(required: request_restated, analysis, alternatives, ui_impact {changes, detail}, risks, "
                        "spec, steps[], qa[]; benchmarks[] and mockups[] when the UI changes). A new version voids "
                        "an earlier approval. Returns the confirmation code and the approval packet main sends.",
         "inputSchema": {"type": "object", "properties": {"root_id": root, "proposal": {"type": "object"}},
                         "required": ["root_id", "proposal"]}},
        {"name": "work_approval_requested",
         "description": "Approval workflow: register the Walkie approval-button message main sent for the current "
                        "proposal version (its body carries the confirmation code). The goal then awaits the "
                        "requester's verified message.approved; a text reply never approves.",
         "inputSchema": {"type": "object", "properties": {
             "root_id": root, "conversation_id": {"type": "string"}, "message_id": {"type": "string"},
             "thread_root_id": {"type": "string"}, "approvers": {"type": "array", "items": {"type": "string"}},
             "proposal_version": {"type": "integer"}, "mockups_attached": {"type": "array", "items": {"type": "string"}}},
             "required": ["root_id", "conversation_id", "message_id", "approvers", "proposal_version"]}},
        {"name": "work_proposal_status",
         "description": "Approval workflow: main's to-do queue and each goal's stage, owner, blocking reason, "
                        "proposal, approval, QA results and reworks; root_id with include_body returns the "
                        "proposal text. A Work fork passes lease_id and worker_token for its assignment packet.",
         "inputSchema": {"type": "object", "properties": {"root_id": root, "include_body": {"type": "boolean"},
                                                          "lease_id": {"type": "string"},
                                                          "worker_token": {"type": "string"}}}},
        {"name": "work_workflow_resolve",
         "description": "Approval workflow: cancel a goal, resend its approval request (resend) or resolve an "
                        "escalation (escalate_model with model, or escalate_main).",
         "inputSchema": {"type": "object", "properties": {
             "root_id": root, "action": {"type": "string", "enum": ["cancel", "resend", "escalate_model",
                                                                    "escalate_main"]},
             "model": {"type": "string"}, "reason": {"type": "string"}}, "required": ["root_id", "action"]}},
        {"name": "work_workflow_delegate",
         "description": "Approval workflow: hand the detailed proposal (role proposal) or the result review (role "
                        "review) of a goal to a new fork on main's own model while main is busy. The review fork is "
                        "never the fork that implemented the step.",
         "inputSchema": {"type": "object", "properties": {"root_id": root,
                                                          "role": {"type": "string", "enum": ["proposal", "review"]},
                                                          "item_id": {"type": "string"}},
                         "required": ["root_id", "role"]}},
        {"name": "work_side_workflow_event",
         "description": "Host-only: verify one Walkie approval/rejection of a registered approval request (SSE "
                        "receipt, or the periodic check with the host-read thread) and apply it once.",
         "inputSchema": {"type": "object", "properties": {
             "receipt_id": {"type": "string"}, "root_id": {"type": "string"}, "check": {"type": "boolean"},
             "source_message": {"type": "object"}}}},
    ]


# ---------------------------------------------------------------- side intake hooks

def _workflow_prompt(state_dir: Path | None, name: str, **values: Any) -> tuple[str, str]:
    try:
        return render(state_dir, name, **values)
    except Exception:
        return "", ""


def side_workflow_context(config: Mapping[str, Any], state_dir: Path, row: Mapping[str, Any] | None) -> dict | None:
    """The ``workflow`` argument of side_work_decision.apply, or None when off."""
    if not wf.enabled(config):
        return None
    event = parse_event((row or {}).get("text")) or {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    context = data.get("message_context") if isinstance(data.get("message_context"), dict) else {}
    source = {"event_key": (row or {}).get("event_key"),
              # Walkie names the conversation data.conversation (side_reply_connector).
              "conversation_id": data.get("conversation") or data.get("conversation_id") or payload.get("conversation_id"),
              "message_id": data.get("message_id") or payload.get("message_id") or context.get("id"),
              "thread_root_id": data.get("thread_root_id") or payload.get("thread_root_id")
              or context.get("thread_root_id"),
              "requester_id": payload.get("sender_id") or data.get("sender_id"),
              "digest": hashlib.sha256(str((row or {}).get("text") or "").encode("utf-8")).hexdigest()}
    _, digest = _workflow_prompt(state_dir, "side_intake")
    return {"source": {k: str(v) for k, v in source.items() if v not in (None, "")}, "prompt": digest}


def claim_workflow_event(config: Mapping[str, Any], document: Mapping[str, Any], text: Any,
                         store: Any = None) -> dict | None:
    if not wf.enabled(config) or not document.get("workflows"):
        return None
    root = is_workflow_event(text, document)
    if not root:
        return None
    from .work_workflow_approval import event_kind
    marked = {"root_id": root, "kind": event_kind(parse_event(text))}
    request = (wf.record(document, root) or {}).get("approval_request") or {}
    if request.get("message_id") and request.get("conversation_id"):
        # The host reads the registered request message once (the reader the
        # periodic re-check uses), never the thread (2026-11-18). No row digest:
        # the event itself says something changed, so replies after the
        # recorded cursor are read when the row carries no approvals[].
        row = {"root_id": root, **request}
        if store is not None:
            row["reply_cursor"] = check_state(store, row).get("reply_cursor")
        marked["request_read"] = {**approval_request_read(config, row), "row_digest": None}
    return marked


def is_settled_workflow_event(config: Mapping[str, Any], state_dir: Path, identity: str, text: Any) -> bool:
    """P3: a registered approval/rejection gets no receipt ACK (nor recap/final answer)."""
    if not wf.enabled(config):
        return False
    try:
        from .work_plan_store import session_work_plan_store
        document = session_work_plan_store(state_dir, identity).read_snapshot()
    except Exception:
        return False
    return bool(document.get("workflows")) and is_workflow_event(text, document) is not None


def side_instruction(config: Mapping[str, Any], state_dir: Path, instruction: Any) -> Any:
    if not wf.enabled(config):
        return instruction
    parts = [str(instruction or "").strip()]
    for name in ("side_intake", "side_answer", "side_approval"):
        text, _ = _workflow_prompt(state_dir, name)
        if text:
            parts.append(text)
    parts.append('Approval workflow is on: a simple question only main can answer is action answer with '
                 '"ask_main": true; the host hands it to main at once and main\'s answer returns to the thread.')
    return "\n".join(part for part in parts if part)


# ---------------------------------------------------------------- views

def _effective(state_dir: Path, identity: str, document: Mapping[str, Any]) -> dict[str, str]:
    """root id -> effective priority (P0 activations included)."""
    out = {}
    try:
        from .work_parallel_mcp import ledger, p0_state
        side = p0_state(ledger())
        state = side.read()
        for node in document.get("nodes", []):
            if node.get("parent_id") is None and side.matching_active_marker(
                    state, identity, node["id"], node.get("created_at")) is not None:
                out[node["id"]] = "P0"
    except Exception:
        pass
    for node in document.get("nodes", []):
        if node.get("parent_id") is None and node["id"] not in out and node.get("priority"):
            out[node["id"]] = node["priority"]
    return out


def status(store: Any, state_dir: Path, identity: str, config: Mapping[str, Any], *, root_id: str | None = None,
           include_body: bool = False) -> dict[str, Any]:
    document = snapshot(store)
    effective = _effective(state_dir, identity, document)
    view = wf.projection(document, effective, config=config)
    if root_id:
        if wf.record(document, root_id) is None:
            raise tree.WorkPlanError(f"goal {root_id} has no workflow")
        view["goal"] = wf.view(document, root_id, effective)
        if include_body:
            proposal = (wf.record(document, root_id) or {}).get("proposal") or {}
            view["goal"]["proposal_body"] = load_body(store, root_id, "proposal", proposal.get("digest", "")) \
                if proposal else None
    view["metrics"] = wf.metrics(document)
    # A step's review is main's only once the ledger published it (the fork's
    # idle observation): before that work_parallel_item_review is refused
    # (measured in the 2026-11-18 E2E), so the item is not offered yet.
    ready = _published_reviews(identity)
    if ready is not None:
        for row in view["todo"]:
            if row["state"] == "awaiting_review":
                steps = (wf.record(document, row["root_id"]) or {}).get("steps") or {}
                row["review_ready"] = any(step.get("state") == "awaiting_review" and step.get("lease_id") in ready
                                          for step in steps.values())
    offered = [row for row in view["todo"] if row.get("review_ready", True)]
    top = offered[0] if offered else None
    if top:
        view["next_for_main"] = main_notice(store, state_dir, document, top)
    return view


def _published_reviews(identity: str) -> set[str] | None:
    """Lease ids with a published, undecided main review; None when unreadable."""
    try:
        from .work_parallel_mcp import finalization
        reviews = finalization().read().get("reviews") or {}
    except Exception:
        return None
    return {key for key, row in reviews.items() if isinstance(row, dict) and row.get("scope") == identity
            and row.get("decision") is None}


def main_notice(store: Any, state_dir: Path, document: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """The one to-do item main is told about (spec 6), with its stage prompt."""
    record = wf.record(document, row["root_id"]) or {}
    proposal = record.get("proposal") or {}
    name = {"proposal_due": "main_proposal", "changes_requested": "main_proposal",
            "approval_request_due": "main_approval_request", "awaiting_review": "main_review",
            "escalated": "main_review"}[row["state"]]
    text, digest = _workflow_prompt(state_dir, name, code=proposal.get("code", ""))
    extra = ""
    if row["state"] == "changes_requested":
        last = (record.get("rejections") or [{}])[-1]
        extra = " The requester rejected proposal v%s: %s" % (last.get("proposal_version"), last.get("note") or "(no note)")
    if row["state"] == "escalated":
        escalation = record.get("escalation") or {}
        extra = (" Rework limit reached on %s (%s/%s). Choose with work_workflow_resolve: escalate_model (model), "
                 "escalate_main (you finish it and approve with your own evidence) or cancel."
                 % (escalation.get("item_id"), escalation.get("reworks"), escalation.get("max_rework")))
    head = (f"Ciel Work workflow: goal {row['root_id']} ({row['title']}) [{row['priority'] or '-'}] is "
            f"{row['state']} and waits for main.")
    # The wake prompt an idle main gets carries host text only (no title or
    # requester note from the external request): in the 2026-11-18 E2E the
    # appended row was not in the next request, so the item itself travels in
    # the wake. Main reads the title with work_proposal_status.
    wake = (f"Ciel Work workflow: goal {row['root_id']} [{row['priority'] or '-'}] is {row['state']} and waits for main. "
            f"Read it with work_proposal_status(root_id={row['root_id']}).\n" + text
            + "\nThis host wake grants no execution approval.")
    return {"root_id": row["root_id"], "state": row["state"], "priority": row["priority"],
            "key": f"{row['root_id']}:{row['state']}:{proposal.get('version', 0)}:{record.get('updated_at', 0)}",
            "text": head + extra + "\n" + text + "\nThis host notice grants no execution approval.",
            "wake_text": wake, "prompt": name, "prompt_digest": digest}


def assignment_packet(store: Any, state_dir: Path, document: Mapping[str, Any], item_id: str,
                      lease_id: str) -> dict[str, Any] | None:
    """What a fork of a workflow goal receives (spec 4.5): spec, its step, QA, rules, report format."""
    by_id = {node["id"]: node for node in document.get("nodes", [])}
    node = by_id.get(item_id)
    if node is None:
        return None
    root = node
    while root.get("parent_id"):
        root = by_id[root["parent_id"]]
    record = wf.record(document, root["id"])
    if record is None or not record.get("proposal"):
        return None
    proposal = record["proposal"]
    body = load_body(store, root["id"], "proposal", proposal["digest"]) or {}
    step_id = wf.step_for_item(document, root["id"], item_id)
    step = next((row for row in body.get("steps", []) if row.get("id") == step_id), None)
    linked = wf.linked_qa(record, step_id)
    qa = [row for row in body.get("qa", []) if row.get("id") in linked]
    execute, execute_digest = _workflow_prompt(state_dir, "worker_execute", digest=proposal["digest"][:12], lease=lease_id)
    report, report_digest = _workflow_prompt(state_dir, "worker_report")
    packet = {"root_id": root["id"], "proposal_version": proposal["version"], "proposal_digest": proposal["digest"],
              "spec": body.get("spec", ""), "step": step, "qa": qa,
              "rules": "Do not change anything the spec lists as out of scope; no completion without QA evidence.",
              "instructions": execute, "report_format": report,
              "evidence_dir": f".work-evidence/{lease_id}/", "prompt_digests": [execute_digest, report_digest]}
    step_row = (record.get("steps") or {}).get(item_id) or {}
    reviews = [row for row in step_row.get("reviews", []) if row.get("rework")]
    if step_row.get("state") == "rework" and reviews:
        rework = reviews[-1]["rework"]
        text, _ = _workflow_prompt(state_dir, "rework", failed_qa=", ".join(rework["failed_qa"]),
                                   observed=rework["observed"], fix=rework["fix"],
                                   evidence_required=rework["evidence_required"])
        packet["rework"] = {**rework, "instructions": text}
    if step_row.get("model_override"):
        packet["model"] = step_row["model_override"]
    return packet


def attach_packet(text: str, board_store: Any, state_dir: Path) -> str:
    """Add the workflow packet to a work_parallel_* reply that hands a fork its assignment."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text
    if not isinstance(value, dict) or not value.get("worker_token") or not value.get("item_id"):
        return text
    try:
        document = snapshot(board_store)
    except Exception:
        return text
    if not document.get("workflows"):
        return text
    packet = assignment_packet(board_store, state_dir, document, value["item_id"], str(value.get("lease_id") or ""))
    if packet is None:
        return text
    value["workflow"] = packet
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- MCP dispatch

def _caller(host: Mapping[str, Any] | None) -> dict[str, str]:
    agent = (host or {}).get("agent_id")
    return {"kind": "fork", "id": str(agent)} if agent else {"kind": "main", "id": "main"}


def _create_steps(doc: dict[str, Any], root_id: str, body: Mapping[str, Any]) -> list[str]:
    refs = {node.get("ref"): node["id"] for node in doc["nodes"]}
    made = []
    for step in body["steps"]:
        ref = f"wf:{root_id}:{step['id']}"
        if ref in refs:
            continue
        # User 2026-11-18: each step shows its pass and test conditions on the board.
        checks = "; ".join(f"{row['id']} test: {row['how']} -> pass: {row['expected']}"
                           for row in body.get("qa", []) if row["id"] in step["qa"])
        note = (f"Done when: {step['done_when']}\nQA: {checks or ', '.join(step['qa'])}"
                f"\n{step['procedure']}\nInput: {step['input']}")
        parent = root_id
        if step.get("phase"):
            # Hierarchical to-do list: goal -> phase -> step (one group per phase).
            group_ref = f"wf:{root_id}:phase:{hashlib.sha256(step['phase'].encode()).hexdigest()[:12]}"
            parent = refs.get(group_ref)
            if parent is None:
                parent = tree.add_node(doc, root_id, step["phase"][:tree.MAX_TITLE_CHARS], ref=group_ref)
                refs[group_ref] = parent
        made.append(tree.add_node(doc, parent, f"{step['id']} {step['title']}"[:tree.MAX_TITLE_CHARS],
                                  note=note[:tree.MAX_TEXT_CHARS], ref=ref))
    return made


def dispatch(name: str, args: Mapping[str, Any], store: Any, state_dir: Path, identity: str,
             config: Mapping[str, Any], host: Mapping[str, Any] | None, *, host_verified: bool) -> dict[str, Any]:
    if name == "work_proposal_status":
        return _status_or_packet(args, store, state_dir, identity, config, host_verified=host_verified)
    if name == "work_side_workflow_event":
        if not host_verified or (host or {}).get("agent_id") is not None:
            raise tree.WorkPlanError("work_side_workflow_event is host-only")
        return workflow_event(args, store, state_dir, identity, config, host or {})
    root_id = args.get("root_id")
    if not isinstance(root_id, str) or not root_id:
        raise tree.WorkPlanError("root_id required")
    who = _caller(host)
    if name == "work_proposal_submit":
        if who["kind"] == "fork":
            # Only the host submits a proposal fork's answer (delegated_event).
            delegation = ((wf.record(snapshot(store), root_id) or {}).get("delegation") or {})
            if not (host or {}).get("_delegated") or delegation.get("role") != "proposal" \
                    or delegation.get("agent_id") != who["id"]:
                raise tree.WorkPlanError("only main or this goal's proposal fork submits its proposal")
            who = {**who, "model": delegation.get("model", "") or "main"}
        _, digest = _workflow_prompt(state_dir, "main_proposal")
        holder = {}

        def mutate(doc):
            try:
                row, body, changed = wf.submit_proposal(doc, root_id, args.get("proposal"), author=who, prompt=digest)
            except wf.WorkflowError as error:
                raise tree.WorkPlanError(str(error)) from None
            holder["body"] = body
            if changed:
                holder["steps"] = _create_steps(doc, root_id, body)
                tree.set_phase(doc, tree.PHASE_PLANNING, root_id)
                if (row.get("delegation") or {}).get("role") == "proposal":
                    row["delegation"] = {**row["delegation"], "state": "done"}
            return {"changed": changed, "proposal": dict(row["proposal"]), "state": row["state"]}

        document, result = store.transaction(mutate)
        save_body(store, root_id, "proposal", holder["body"])
        text, _ = _workflow_prompt(state_dir, "main_approval_request", code=result["proposal"]["code"])
        body = holder["body"]
        return {**result, "root_id": root_id, "version": document["version"],
                "approval_packet": {"code": result["proposal"]["code"], "instructions": text,
                                    "summary": body["request_restated"][:1500],
                                    "mockups": body["mockups"], "benchmarks": [
                                        {k: row[k] for k in ("product", "screen", "lesson")} for row in body["benchmarks"]],
                                    "steps": [{"id": s["id"], "title": s["title"]} for s in body["steps"]],
                                    "qa": [{"id": q["id"], "claim": q["claim"]} for q in body["qa"]]}}
    if who["kind"] == "fork" and name != "work_workflow_delegate":
        raise tree.WorkPlanError(name + " is main's")
    if name == "work_approval_requested":
        _, digest = _workflow_prompt(state_dir, "main_approval_request")

        def mutate(doc):
            try:
                row = wf.approval_requested(doc, root_id, args, prompt=digest)
            except wf.WorkflowError as error:
                raise tree.WorkPlanError(str(error)) from None
            return {"state": row["state"], "approval_request": dict(row["approval_request"])}

        document, result = store.transaction(mutate)
        return {**result, "root_id": root_id, "version": document["version"]}
    if name == "work_workflow_resolve":
        return resolve(store, root_id, str(args.get("action") or ""), args, actor="main")
    if name == "work_workflow_delegate":
        if who["kind"] == "fork":
            raise tree.WorkPlanError("work_workflow_delegate is main's")
        return delegate(store, root_id, str(args.get("role") or ""), args.get("item_id"))
    raise tree.WorkPlanError("unknown workflow tool " + name)


def _notice_file(store) -> Path:
    return Path(store.path).parent / "workflow" / "notified.json"


def _notice_claim(store, key: str, *, release: bool = False) -> bool:
    """One main notice per to-do key (spec 6: the same item is not repeated)."""
    from .channel_message_repository import exclusive_file_lock
    path = _notice_file(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(path):
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            seen = {}
        keys = list(seen.get("keys") or []) if isinstance(seen, dict) else []
        if release:
            if key not in keys:
                return False
            keys.remove(key)
        else:
            if key in keys:
                return False
            keys = (keys + [key])[-200:]
        path.write_text(json.dumps({"keys": keys}), encoding="utf-8")
    return True


def _status_or_packet(args, store, state_dir, identity, config, host_verified=False):
    lease_id = args.get("lease_id")
    if host_verified and isinstance(args.get("release_notice"), str):
        return {"released": _notice_claim(store, args["release_notice"], release=True)}
    if lease_id:
        from .work_parallel import worker_token_matches
        from .work_parallel_mcp import ledger
        lease = ledger().read()["leases"].get(lease_id)
        if not lease or not worker_token_matches(lease, args.get("worker_token")):
            raise tree.WorkPlanError("invalid or revoked worker capability")
        packet = assignment_packet(store, state_dir, snapshot(store), lease["item_id"], lease_id)
        return {"workflow": packet}
    view = status(store, state_dir, identity, config, root_id=args.get("root_id"),
                  include_body=args.get("include_body") is True)
    if host_verified and args.get("claim_notice") is True and view.get("next_for_main"):
        view["next_for_main"]["claimed"] = _notice_claim(store, view["next_for_main"]["key"])
    return view


def resolve(store: Any, root_id: str, action: str, args: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    holder = {}

    def mutate(doc):
        try:
            row = wf.resolve(doc, root_id, action, args, actor=actor)
        except wf.WorkflowError as error:
            raise tree.WorkPlanError(str(error)) from None
        holder["state"] = row["state"]
        return row["state"]

    document, state = store.transaction(mutate)
    result = {"root_id": root_id, "action": action, "state": state, "version": document["version"]}
    if action == "escalate_model":
        result["rework_grant"] = _grant_rework(document, root_id)
    return result


def _grant_rework(document: Mapping[str, Any], root_id: str) -> Any:
    """One more rework past max_rework for the escalated step's submitted lease."""
    item = ((wf.record(document, root_id) or {}).get("escalation") or {}).get("item_id")
    lease_id = (((wf.record(document, root_id) or {}).get("steps") or {}).get(item) or {}).get("lease_id")
    if not lease_id:
        return None
    from .work_parallel_mcp import ledger
    from .work_rework_grants import add
    store = ledger()
    lease = store.read()["leases"].get(lease_id)
    if lease is None:
        return None
    return {"item_id": lease["item_id"], "granted": add(store.path, lease["scope"], lease["item_id"])}


def delegate(store: Any, root_id: str, role: str, item_id: Any = None) -> dict[str, Any]:
    if role not in ("proposal", "review"):
        raise tree.WorkPlanError("role must be proposal or review")

    def mutate(doc):
        row = wf.record(doc, root_id)
        if row is None:
            raise tree.WorkPlanError(f"goal {root_id} has no workflow")
        if role == "proposal" and row["state"] not in ("proposal_due", "changes_requested"):
            raise tree.WorkPlanError("a proposal fork applies while the proposal is due")
        if role == "review" and row["state"] not in ("awaiting_review", "escalated"):
            raise tree.WorkPlanError("a review fork applies while a report awaits review")
        target = item_id
        if role == "review":
            waiting = [key for key, step in (row.get("steps") or {}).items() if step.get("state") == "awaiting_review"]
            target = item_id if item_id in waiting else (waiting[0] if waiting else None)
            if target is None:
                raise tree.WorkPlanError("no step awaits review")
        current = row.get("delegation") or {}
        if current.get("state") in ("requested", "running") and current.get("role") == role:
            return dict(current)
        row["delegation"] = {"role": role, "item_id": target, "state": "requested", "agent_id": None,
                             "implemented_by": [*(((row.get("steps") or {}).get(target) or {}).get("leases") or [])],
                             "requested_at": wf._now(None)}
        return dict(row["delegation"])

    document, delegation = store.transaction(mutate)
    return {"root_id": root_id, "delegation": delegation, "version": document["version"]}


def delegation_bound(store: Any, root_id: str, agent_id: str, model: str = "") -> dict[str, Any]:
    """The host spawned the delegated fork; record which agent it is."""
    def mutate(doc):
        row = wf.record(doc, root_id)
        current = (row or {}).get("delegation") or {}
        if current.get("state") not in ("requested", "spawning"):
            raise tree.WorkPlanError("no delegation awaits a fork")
        row["delegation"] = {**current, "state": "running", "agent_id": str(agent_id)[:120],
                             "model": str(model)[:120], "started_at": wf._now(None)}
        return dict(row["delegation"])
    _, delegation = store.transaction(mutate)
    return delegation


def auto_delegations(document: Mapping[str, Any], config: Mapping[str, Any], *, now: float) -> list[dict[str, Any]]:
    """Main-queue items to delegate automatically (pending count / waiting minutes thresholds)."""
    settings = wf.settings(config)
    if not settings["delegate_pending"] and not settings["delegate_minutes"]:
        return []
    queue = [row for row in wf.todo(document) if row["state"] in ("proposal_due", "awaiting_review")
             and not row["delegated"]]
    out = []
    for index, row in enumerate(queue):
        waited = (now - float(row["due_at"] or now)) / 60
        over_count = settings["delegate_pending"] and len(queue) >= settings["delegate_pending"] and index > 0
        over_time = settings["delegate_minutes"] and waited >= settings["delegate_minutes"]
        if over_count or over_time:
            out.append({"root_id": row["root_id"], "role": "proposal" if row["state"] == "proposal_due" else "review"})
    return out[:4]


def delegated_event(row: Mapping[str, Any], store: Any, state_dir: Path) -> dict[str, Any]:
    """The host reports a delegated planning fork (spec 6.1): bound, its answer, or a failure."""
    root_id, role, agent = row.get("root_id"), row.get("role"), str(row.get("agent_id") or "")
    if not isinstance(root_id, str) or role not in ("proposal", "review") or not agent:
        raise tree.WorkPlanError("delegated needs root_id, role and agent_id")
    state = row.get("state")
    if state == "bound":
        return {"delegation": delegation_bound(store, root_id, agent, "main")}
    if state == "failed":
        def fail(doc):
            record = wf.record(doc, root_id) or {}
            current = record.get("delegation") or {}
            if current.get("agent_id") == agent:
                record["delegation"] = {**current, "state": "failed", "reason": str(row.get("reason") or "")[:300]}
            return dict(record.get("delegation") or {})
        _, delegation = store.transaction(fail)
        return {"delegation": delegation}
    if state == "answer" and role == "proposal":
        current = (wf.record(snapshot(store), root_id) or {}).get("delegation") or {}
        if current.get("role") != "proposal" or current.get("agent_id") != agent or current.get("state") != "running":
            raise tree.WorkPlanError("no running proposal fork with this agent id")
        return dispatch("work_proposal_submit", {"root_id": root_id, "proposal": row.get("proposal")}, store, state_dir,
                        "", {}, {"agent_id": agent, "_delegated": True}, host_verified=True)
    raise tree.WorkPlanError("delegated state must be bound, answer (proposal) or failed")


def delegations_due(store: Any, state_dir: Path, config: Mapping[str, Any], *, now: float) -> list[dict[str, Any]]:
    """Delegations the host should start a fork for, with the fork prompt (spec 6.1)."""
    document = snapshot(store)
    if not document.get("workflows"):
        return []
    for row in auto_delegations(document, config, now=now):
        try:
            delegate(store, row["root_id"], row["role"])
        except tree.WorkPlanError:
            pass

    def mark(doc):
        out = []
        for root_id, record in (doc.get("workflows") or {}).items():
            current = record.get("delegation") or {}
            stale = current.get("state") == "spawning" and now - float(current.get("spawn_at") or 0) > 600
            if (current.get("state") == "requested" or stale) and len(out) < 2:
                record["delegation"] = {**current, "state": "spawning", "spawn_at": now}
                out.append((root_id, dict(record["delegation"])))
        return out

    _, rows = store.transaction(mark)
    offered = []
    for root_id, delegation in rows:
        steps = (wf.record(snapshot(store), root_id) or {}).get("steps") or {}
        offered.append({"root_id": root_id, "role": delegation["role"], "item_id": delegation.get("item_id"),
                        "lease_id": (steps.get(delegation.get("item_id")) or {}).get("lease_id"),
                        "prompt": delegation_prompt(store, state_dir, root_id, delegation)})
    return offered


def delegation_prompt(store: Any, state_dir: Path, root_id: str, delegation: Mapping[str, Any]) -> str:
    document = snapshot(store)
    record = wf.record(document, root_id) or {}
    goal = wf.view(document, root_id)
    node = next((n for n in document["nodes"] if n["id"] == root_id), {})
    proposal = record.get("proposal") or {}
    if delegation["role"] == "proposal":
        head, _ = _workflow_prompt(state_dir, "proposal_fork")
        rules, _ = _workflow_prompt(state_dir, "main_proposal")
        material = {"goal": goal, "request": {"title": node.get("title"), "note": node.get("note"),
                                             "understanding": (record.get("source") or {}).get("understanding")},
                    "previous_proposal": load_body(store, root_id, "proposal", proposal.get("digest", ""))
                    if proposal else None, "rejections": record.get("rejections") or []}
        answer = 'Answer with one JSON object: {"proposal": {...the full proposal...}}.'
    else:
        head, _ = _workflow_prompt(state_dir, "review_fork")
        rules, _ = _workflow_prompt(state_dir, "main_review")
        step = (record.get("steps") or {}).get(delegation.get("item_id")) or {}
        report = (step.get("reports") or [{}])[-1]
        material = {"goal": goal, "item_id": delegation.get("item_id"), "lease_id": step.get("lease_id"),
                    "report": load_body(store, root_id, "report", report.get("digest", "")),
                    "evidence_folder": str(folder(store, root_id) / "evidence" / str(step.get("lease_id") or "")),
                    "proposal": load_body(store, root_id, "proposal", proposal.get("digest", "")) if proposal else None}
        answer = ('Answer with one JSON object: {"lease_id": "...", "decision": "approve" or "rework", "qa_verdicts": '
                  '[{"id": "QA-1", "verdict": "pass" or "fail", "note": "..."}], "evidence_checked": true or false, '
                  '"rework": {"failed_qa": [...], "observed": "...", "fix": "...", "evidence_required": "..."}, '
                  '"evidence": "what you checked"}.')
    return "\n\n".join([head, rules, answer, "Material:\n" + json.dumps(material, ensure_ascii=False)[:60000]])


def _check_file(store: Any, root_id: str) -> Path:
    return folder(store, root_id) / "approval-check.json"


def note_check(store: Any, root_id: str, request: Mapping[str, Any] | None, reply_cursor: Any, row_digest: Any) -> None:
    """What the periodic check last read of a request (session folder, not the board):
    an unchanged request message is read once and nothing else (2026-11-18)."""
    if not isinstance(request, Mapping):
        return
    cursor, value = str(reply_cursor or ""), str(row_digest or "")
    data = {"message_id": request.get("message_id"),
            "reply_cursor": cursor if cursor.isdigit() and len(cursor) <= 18 and cursor[0] != "0" else None,
            "row_digest": value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else None}
    path = _check_file(store, root_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(path)


def check_state(store: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """The reply cursor and row digest the last check recorded for this request message."""
    try:
        data = json.loads(_check_file(store, row["root_id"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("message_id") != row.get("message_id"):
        return {}
    return {"reply_cursor": data.get("reply_cursor"), "row_digest": data.get("row_digest")}


def approval_request_read(config: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """What the host reads for one pending request (register.js, workflow approval read section): the
    request message alone, plus replies after the cursor when it has no approvals."""
    from .side_reply_connector import DEFAULT_WALKIE_SERVER
    server = DEFAULT_WALKIE_SERVER
    for binding in ((config.get("work_side_intake") or {}).get("reply_connectors") or []):
        if isinstance(binding, dict) and binding.get("preset") == "walkie" and isinstance(binding.get("server"), str):
            server = binding["server"]
            break
    return {"server": server, "conversation_id": row["conversation_id"], "message_id": row["message_id"],
            "thread_root_id": row.get("thread_root_id"), "reply_cursor": row.get("reply_cursor"),
            "row_digest": row.get("row_digest")}


def workflow_event(args: Mapping[str, Any], store: Any, state_dir: Path, identity: str, config: Mapping[str, Any],
                   host: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a host-verified approval/rejection once (spec 4.4 / 4.4.1)."""
    thread = args.get("source_message")
    if isinstance(args.get("delegated"), dict):
        return delegated_event(args["delegated"], store, state_dir)
    if args.get("check") is True and isinstance(args.get("checks"), list):
        # One call per tick for every request read (batched).
        results = []
        for item in args["checks"][:32]:
            if not isinstance(item, Mapping) or not isinstance(item.get("root_id"), str):
                continue
            results.append(workflow_event({"check": True, "root_id": item["root_id"],
                                           "source_message": item.get("source_message"),
                                           "reply_cursor": item.get("reply_cursor"),
                                           "row_digest": item.get("row_digest")},
                                          store, state_dir, identity, config, host))
        return {"results": results}
    if args.get("check") is True:
        root_id = args.get("root_id")
        event, event_id, receipt_id = None, "", None
    else:
        from .side_intake_inbox import SideIntakeInbox
        from .side_intake_processing import verify_claim
        receipt_id = host.get("receipt_id") or args.get("receipt_id")
        inbox = SideIntakeInbox(state_dir, identity)
        row = inbox._load(str(receipt_id or ""))
        if not row:
            raise tree.WorkPlanError("unknown side receipt")
        if row.get("stage") != "processed":
            verify_claim(inbox, receipt_id, host.get("generation"))
        event = parse_event(row.get("text"))
        if event is None:
            raise tree.WorkPlanError("side receipt is not a CloudEvent")
        root_id = is_workflow_event(row.get("text"), snapshot(store))
        event_id = "sse:" + str(event.get("id") or hashlib.sha256(row["text"].encode()).hexdigest()[:24])[:100]
    holder: dict[str, Any] = {}

    def mutate(doc):
        record = wf.record(doc, root_id) if root_id else None
        if record is None:
            holder["verdict"] = {"decision": "invalid", "reason": "no workflow request matches this event"}
            return False
        verdict = verify(record, event=event, thread=thread, event_id=event_id)
        holder["verdict"] = verdict
        if verdict["decision"] not in ("approved", "rejected"):
            return False
        _, applied = wf.apply_decision(doc, root_id, verdict)
        if applied and verdict["decision"] == "approved":
            tree.set_phase(doc, tree.PHASE_EXECUTING, root_id, workflow_approved=True)
        holder["state"] = record["state"]
        return applied

    document, applied = store.transaction(mutate)
    verdict = holder["verdict"]
    if root_id and "row_digest" in args and verdict["decision"] in ("pending", "approved", "rejected"):
        note_check(store, root_id, (wf.record(document, root_id) or {}).get("approval_request"),
                   args.get("reply_cursor"), args.get("row_digest"))
    result = {"root_id": root_id, "applied": applied, "decision": verdict["decision"], "reason": verdict["reason"],
              "via": verdict.get("via"), "state": (wf.record(document, root_id) or {}).get("state") if root_id else None,
              "version": document["version"]}
    if receipt_id and args.get("check") is not True:
        result["receipt"] = _settle_receipt(state_dir, identity, receipt_id, host.get("generation"), result, document)
    return result


def _settle_receipt(state_dir, identity, receipt_id, generation, result, document):
    """The side receipt of an approval event is processed by the host: no ACK, recap or answer (P3).

    A verified approval sends one start notice; every other outcome sends nothing.
    """
    from .side_intake_processing import finish
    approved = result["decision"] == "approved" and result["applied"]
    title = next((n.get("title", "") for n in document.get("nodes", []) if n["id"] == result["root_id"]), "")
    row = {"action": "answer", "kind": "status", "item_id": result["root_id"],
           "summary": f"workflow {result['decision']}: {result['reason']}"[:1000],
           "reply": "", "execution_approved": approved, "handoff": "not_required",
           "workflow": {k: result[k] for k in ("root_id", "decision", "reason", "applied", "state")},
           "delivery": "pending" if approved else "not_applicable"}
    if approved:
        row["final_text"] = (f"승인을 확인했습니다. 작업 {result['root_id']} ({title[:120]})을 시작합니다. "
                             "진행 결과와 검수 요청은 이 스레드로 알려 드리겠습니다.")
        row["reply"] = row["final_text"]
    try:
        return finish(state_dir, identity, receipt_id, generation, row)
    except tree.WorkPlanError as error:
        return {"error": str(error)[:200]}


__all__ = ["HOST_TOOLS", "MAIN_TOOLS", "NAMES", "assignment_packet", "attach_packet", "auto_delegations",
           "claim_workflow_event", "delegate", "delegation_bound", "dispatch", "folder", "is_settled_workflow_event",
           "load_body", "main_notice", "model_overrides", "report_check", "report_record", "resolve", "review_apply",
           "review_target", "save_body", "schemas", "side_instruction", "side_workflow_context", "status",
           "workflow_event"]
