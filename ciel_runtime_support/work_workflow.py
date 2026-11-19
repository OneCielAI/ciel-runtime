"""Work approval workflow: one router-enforced state machine per side-created goal.

Spec: docs/journal/2026/11/18/features/work-approval-workflow/spec.md. An
external request a side actor registers (``create``) becomes a goal whose
record lives in the board document (``workflows[root_id]``), so every
transition commits in the same board transaction as the goal itself and a
restart or compaction resumes from the stored state.

Order: intake -> proposal_due (main writes the technical proposal) ->
approval_request_due (main sends the Walkie approval buttons and registers the
message) -> awaiting_approval (a person decides) -> approved -> executing
(forks) -> awaiting_review (main reviews QA evidence) -> rework | completed |
escalated. No Work fork of such a goal starts before ``approved``: ``gate``
is checked by the shared execution gate (tree.set_phase/runnable_leaves), the
P0 lane and the reservation path. Approval itself is decided by host code
(work_workflow_approval), never by a model's reading of external text.

Everything here is pure (board document in, board document out); the tools
module adds the session-folder bodies and the MCP/REST surfaces.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping

STATES = ("intake", "proposal_due", "approval_request_due", "awaiting_approval", "changes_requested",
          "approved", "executing", "awaiting_review", "rework", "escalated", "completed", "cancelled")
TERMINAL = ("completed", "cancelled")
# States whose next artifact is main's (spec section 6): main's to-do queue.
MAIN_STATES = ("proposal_due", "approval_request_due", "changes_requested", "awaiting_review", "escalated")
# States in which Work forks of the goal may run.
RUN_STATES = ("approved", "executing", "awaiting_review", "rework")
TRANSITIONS = {
    "intake": ("proposal_due", "cancelled"),
    "proposal_due": ("approval_request_due", "cancelled"),
    "approval_request_due": ("awaiting_approval", "approval_request_due", "cancelled"),
    "awaiting_approval": ("approved", "changes_requested", "approval_request_due", "cancelled"),
    "changes_requested": ("approval_request_due", "cancelled"),
    "approved": ("executing", "approval_request_due", "cancelled"),
    "executing": ("awaiting_review", "approval_request_due", "completed", "cancelled"),
    "awaiting_review": ("rework", "completed", "escalated", "executing", "awaiting_review",
                        "approval_request_due", "cancelled"),
    "rework": ("awaiting_review", "approval_request_due", "escalated", "cancelled"),
    "escalated": ("rework", "completed", "awaiting_review", "approval_request_due", "cancelled"),
    "completed": (),
    "cancelled": (),
}
OWNER = {"intake": "side", "proposal_due": "main", "approval_request_due": "main", "awaiting_approval": "requester",
         "changes_requested": "main", "approved": "host", "executing": "fork", "awaiting_review": "main",
         "rework": "fork", "escalated": "main", "completed": "host", "cancelled": "-"}
GATE_REASON = {"intake": "awaiting_proposal", "proposal_due": "awaiting_proposal",
               "approval_request_due": "awaiting_approval", "awaiting_approval": "awaiting_approval",
               "changes_requested": "awaiting_approval", "escalated": "escalated",
               "completed": "workflow_completed", "cancelled": "workflow_cancelled"}
QA_METHODS = ("command", "e2e", "screenshot", "log", "manual")
QA_STATUSES = ("pass", "fail", "not_run")
EVIDENCE_KINDS = ("screenshot", "log", "output")
QA_ID = re.compile(r"QA-[1-9][0-9]{0,3}\Z")
STEP_ID = re.compile(r"S-[1-9][0-9]{0,3}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_HISTORY = 60
MAX_EVENTS = 64
MAX_TEXT = 20000
CONFIG = "work_workflow"
KEYS = ("work_workflow_enabled", "work_workflow_delegate_pending", "work_workflow_delegate_minutes")
# The settings menu's two workflow rows (work_workflow_menu); their values are observations.
MENU_KEYS = ("work_workflow_actions", "work_workflow_prompts")
DEFAULTS = {"enabled": False, "delegate_pending": 0, "delegate_minutes": 0}


class WorkflowError(ValueError):
    """A workflow operation was refused; nothing changed."""


# ---------------------------------------------------------------- settings

def settings(config: Mapping[str, Any]) -> dict[str, Any]:
    stored = config.get(CONFIG) if isinstance(config.get(CONFIG), dict) else {}
    out = dict(DEFAULTS)
    if stored.get("enabled") is True:
        out["enabled"] = True
    for key in ("delegate_pending", "delegate_minutes"):
        value = stored.get(key)
        if type(value) is int and 0 <= value <= 1440:
            out[key] = value
    return out


def enabled(config: Mapping[str, Any]) -> bool:
    return settings(config)["enabled"]


def set_value(config: dict[str, Any], key: str, raw: Any) -> None:
    if key not in KEYS:
        raise WorkflowError("unknown workflow setting: " + str(key))
    stored = dict(config.get(CONFIG) or {})
    token = str(raw if raw is not None else "").strip().lower()
    field = key[len("work_workflow_"):]
    if field == "enabled":
        if token in ("", "default", "reset", "off", "false", "0", "no"):
            stored.pop("enabled", None)
        elif token in ("on", "true", "1", "yes"):
            stored["enabled"] = True
        else:
            raise WorkflowError("work_workflow_enabled must be on or off")
    else:
        if token in ("", "default", "reset", "off"):
            stored.pop(field, None)
        else:
            try:
                value = int(token)
            except ValueError:
                raise WorkflowError(key + " must be 0-1440") from None
            if not 0 <= value <= 1440:
                raise WorkflowError(key + " must be 0-1440")
            if value:
                stored[field] = value
            else:
                stored.pop(field, None)
    if stored:
        config[CONFIG] = stored
    else:
        config.pop(CONFIG, None)


def setting_value(config: Mapping[str, Any], key: str) -> Any:
    return settings(config)[key[len("work_workflow_"):]]


# ---------------------------------------------------------------- storage shape

def normalize(raw: Any) -> dict[str, Any]:
    """Validate stored workflow records (board normalize). Unknown roots are kept."""
    if not isinstance(raw, dict):
        raise WorkflowError("invalid workflow records")
    for key, row in raw.items():
        if not isinstance(key, str) or not key or len(key) > 64 or not isinstance(row, dict) \
                or row.get("state") not in STATES:
            raise WorkflowError("invalid workflow record")
    return json.loads(json.dumps(raw, ensure_ascii=False))


def record(doc: Mapping[str, Any], root_id: str) -> dict[str, Any] | None:
    row = (doc.get("workflows") or {}).get(root_id)
    return row if isinstance(row, dict) else None


def gate(doc: Mapping[str, Any], root_id: str) -> str:
    """Why no Work fork of this goal may start now, or '' (no workflow / approved)."""
    row = record(doc, root_id)
    if row is None or row["state"] in RUN_STATES:
        return ""
    return GATE_REASON.get(row["state"], "workflow_" + row["state"])


def gate_text(root_id: str, reason: str) -> str:
    return {
        "awaiting_proposal": f"goal {root_id} waits for main's technical proposal (work_proposal_submit)",
        "awaiting_approval": f"goal {root_id} waits for the requester's verified Walkie approval",
        "escalated": f"goal {root_id} exceeded its rework limit; main resolves the escalation",
    }.get(reason, f"goal {root_id} workflow is {reason}")


def _now(now: float | None) -> float:
    return time.time() if now is None else float(now)


def _move(row: dict[str, Any], to: str, actor: str, ref: str, now: float, prompt: str | None = None) -> None:
    state = row["state"]
    if to not in TRANSITIONS.get(state, ()):
        raise WorkflowError(f"workflow cannot move from {state} to {to}")
    entry = {"from": state, "to": to, "actor": str(actor)[:80], "ref": str(ref)[:120], "at": now}
    if prompt:
        entry["prompt"] = prompt[:16]
    row["history"] = (row.get("history") or [])[-(MAX_HISTORY - 1):] + [entry]
    row["state"] = to
    row["updated_at"] = now
    if to in MAIN_STATES:
        row["main_due_at"] = now
    else:
        row.pop("main_due_at", None)


def start(doc: dict[str, Any], root_id: str, *, receipt_id: str, source: Mapping[str, Any] | None = None,
          priority: str = "", understanding: str = "", now: float | None = None,
          prompt: str | None = None) -> dict[str, Any]:
    """intake (request recorded, ACK/recap) then proposal_due, in one board transaction."""
    stamp = _now(now)
    workflows = doc.setdefault("workflows", {})
    if root_id in workflows:
        return workflows[root_id]
    src = dict(source or {})
    row = {"state": "intake", "created_at": stamp, "updated_at": stamp, "priority": str(priority or "")[:2],
           "source": {"receipt_id": str(receipt_id)[:64],
                      **{key: str(src[key])[:200] for key in ("event_key", "conversation_id", "thread_root_id",
                                                              "message_id", "requester_id", "digest")
                         if src.get(key) not in (None, "")},
                      "understanding": str(understanding or "")[:2000], "ack": "host_outbox"},
           "proposal": None, "approval_request": None, "approval": None, "events": [], "steps": {},
           "reworks": 0, "history": [{"from": None, "to": "intake", "actor": "side", "ref": receipt_id[:64],
                                      "at": stamp}]}
    workflows[root_id] = row
    _move(row, "proposal_due", "host", receipt_id[:64], stamp, prompt)
    return row


# ---------------------------------------------------------------- proposal

def _text(value: Any, name: str, *, required: bool = True, limit: int = MAX_TEXT) -> str:
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise WorkflowError(f"proposal {name} is required")
    if len(text) > limit:
        raise WorkflowError(f"proposal {name} exceeds {limit} characters")
    return text


def validate_proposal(proposal: Any) -> dict[str, Any]:
    """The canonical proposal, or WorkflowError naming the first missing/invalid field."""
    if not isinstance(proposal, dict):
        raise WorkflowError("proposal object required")
    out: dict[str, Any] = {}
    for name in ("request_restated", "analysis", "alternatives", "risks", "spec"):
        out[name] = _text(proposal.get(name), name)
    impact = proposal.get("ui_impact")
    if not isinstance(impact, dict) or type(impact.get("changes")) is not bool:
        raise WorkflowError("proposal ui_impact must be {changes: true|false, detail: text}")
    out["ui_impact"] = {"changes": impact["changes"], "detail": _text(impact.get("detail"), "ui_impact.detail")}
    benchmarks = proposal.get("benchmarks") or []
    mockups = proposal.get("mockups") or []
    if not isinstance(benchmarks, list) or not isinstance(mockups, list):
        raise WorkflowError("proposal benchmarks and mockups must be lists")
    if impact["changes"]:
        if not benchmarks:
            raise WorkflowError("proposal changes the UI: benchmarks[] (similar products, screens, sources) required")
        if not mockups:
            raise WorkflowError("proposal changes the UI: mockups[] (main screens and states) required")
    out["benchmarks"] = []
    for index, row in enumerate(benchmarks[:20]):
        if not isinstance(row, dict):
            raise WorkflowError(f"benchmarks[{index}] must be an object")
        out["benchmarks"].append({key: _text(row.get(key), f"benchmarks[{index}].{key}", limit=2000)
                                  for key in ("product", "screen", "source", "lesson", "decision")})
    out["mockups"] = []
    for index, row in enumerate(mockups[:20]):
        if not isinstance(row, dict):
            raise WorkflowError(f"mockups[{index}] must be an object")
        out["mockups"].append({"id": f"M-{index + 1}",
                               **{key: _text(row.get(key), f"mockups[{index}].{key}", limit=2000)
                                  for key in ("file", "state", "description")}})
    qa = proposal.get("qa")
    if not isinstance(qa, list) or not qa:
        raise WorkflowError("proposal qa[] is required")
    out["qa"] = []
    seen: set[str] = set()
    for index, row in enumerate(qa[:100]):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not QA_ID.fullmatch(row["id"]):
            raise WorkflowError(f"qa[{index}].id must look like QA-1")
        if row["id"] in seen:
            raise WorkflowError(f"duplicate QA id {row['id']}")
        seen.add(row["id"])
        if row.get("method") not in QA_METHODS:
            raise WorkflowError(f"qa[{index}].method must be one of " + ", ".join(QA_METHODS))
        if type(row.get("must_fail_before")) is not bool:
            raise WorkflowError(f"qa[{index}].must_fail_before must be true or false")
        out["qa"].append({"id": row["id"], "method": row["method"], "must_fail_before": row["must_fail_before"],
                          **{key: _text(row.get(key), f"qa[{index}].{key}", limit=4000)
                             for key in ("claim", "how", "expected", "evidence")}})
    steps = proposal.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowError("proposal steps[] is required")
    out["steps"] = []
    step_ids: set[str] = set()
    linked: set[str] = set()
    for index, row in enumerate(steps[:50]):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not STEP_ID.fullmatch(row["id"]):
            raise WorkflowError(f"steps[{index}].id must look like S-1")
        if row["id"] in step_ids:
            raise WorkflowError(f"duplicate step id {row['id']}")
        step_ids.add(row["id"])
        links = row.get("qa")
        if not isinstance(links, list) or not links or any(link not in seen for link in links):
            raise WorkflowError(f"steps[{index}].qa must list existing QA ids")
        linked.update(links)
        # User 2026-11-18: the execution steps form a hierarchical to-do list.
        # "phase" names the step's parent group on the board (phase -> steps).
        phase = row.get("phase")
        if phase is not None and (not isinstance(phase, str) or not phase.strip() or len(phase) > 120):
            raise WorkflowError(f"steps[{index}].phase must be a short group title")
        out["steps"].append({"id": row["id"], "qa": list(dict.fromkeys(links)),
                             **({"phase": phase.strip()} if phase else {}),
                             **{key: _text(row.get(key), f"steps[{index}].{key}", limit=4000)
                                for key in ("title", "input", "procedure", "done_when")}})
    unlinked = sorted(seen - linked)
    if unlinked:
        raise WorkflowError("QA not linked to any step: " + ", ".join(unlinked))
    return out


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def confirmation_code(root_id: str, version: int, proposal_digest: str) -> str:
    return f"[proposal {root_id} v{version} {proposal_digest[:12]}]"


def submit_proposal(doc: dict[str, Any], root_id: str, proposal: Any, *, author: Mapping[str, Any],
                    now: float | None = None, prompt: str | None = None) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Record a proposal version: (record, canonical body, changed).

    The same content again is idempotent. New content is a new version; an
    approval of an earlier version no longer counts (spec 4.2), so a goal past
    approval returns to approval_request_due and its forks stop being admitted.
    """
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    if row["state"] in TERMINAL:
        raise WorkflowError(f"goal {root_id} workflow is {row['state']}")
    body = validate_proposal(proposal)
    value = digest(body)
    current = row.get("proposal") or {}
    if current.get("digest") == value:
        return row, body, False
    stamp = _now(now)
    version = int(current.get("version") or 0) + 1
    who = {"kind": str(author.get("kind") or "main")[:10], "id": str(author.get("id") or "main")[:80],
           "model": str(author.get("model") or "")[:120]}
    row["proposal"] = {"version": version, "digest": value, "code": confirmation_code(root_id, version, value),
                       "ui_changes": body["ui_impact"]["changes"], "mockups": [m["id"] for m in body["mockups"]],
                       "qa": [q["id"] for q in body["qa"]], "steps": [s["id"] for s in body["steps"]],
                       "step_qa": {s["id"]: s["qa"] for s in body["steps"]}, "author": who, "submitted_at": stamp}
    row["approval"] = None
    row["approval_request"] = None
    if row["state"] == "approval_request_due":
        row["updated_at"] = stamp
    else:
        # Post-approval revisions need a new approval (spec 5.4).
        _move(row, "approval_request_due", who["kind"], f"proposal v{version}", stamp, prompt)
    return row, body, True


def approval_requested(doc: dict[str, Any], root_id: str, args: Mapping[str, Any], *,
                       now: float | None = None, prompt: str | None = None) -> dict[str, Any]:
    """Register the Walkie approval-button message main sent (spec 4.3)."""
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    proposal = row.get("proposal")
    if not proposal:
        raise WorkflowError("submit the proposal (work_proposal_submit) before requesting approval")
    if row["state"] not in ("approval_request_due", "awaiting_approval"):
        raise WorkflowError(f"goal {root_id} is {row['state']}; an approval request is not due")
    if args.get("proposal_version") != proposal["version"]:
        raise WorkflowError(f"proposal_version must be the current version {proposal['version']}")
    conversation = str(args.get("conversation_id") or "").strip()
    message = str(args.get("message_id") or "").strip()
    if not conversation or len(conversation) > 200 or not re.fullmatch(r"[1-9][0-9]{0,17}", message):
        raise WorkflowError("conversation_id and the sent message_id (Walkie id) are required")
    thread = str(args.get("thread_root_id") or "").strip()
    if thread and not re.fullmatch(r"[1-9][0-9]{0,17}", thread):
        raise WorkflowError("thread_root_id must be a Walkie message id")
    approvers = args.get("approvers")
    if not isinstance(approvers, list) or not approvers or len(approvers) > 20 \
            or not all(isinstance(value, (str, int)) and str(value).strip() for value in approvers):
        raise WorkflowError("approvers[] (the Walkie approver ids the message names) is required")
    if proposal["ui_changes"]:
        attached = args.get("mockups_attached")
        if not isinstance(attached, list) or sorted(set(map(str, attached))) != sorted(proposal["mockups"]):
            raise WorkflowError("the proposal changes the UI: mockups_attached must list every mockup id "
                                + ", ".join(proposal["mockups"]) + " sent with the approval request")
    stamp = _now(now)
    request = {"conversation_id": conversation, "message_id": message, "thread_root_id": thread or None,
               "approvers": [str(value).strip()[:80] for value in approvers],
               "proposal_version": proposal["version"], "proposal_digest": proposal["digest"],
               "code": proposal["code"], "registered_at": stamp,
               "mockups_attached": list(proposal["mockups"]) if proposal["ui_changes"] else []}
    previous = row.get("approval_request") or {}
    if row["state"] == "awaiting_approval" and previous.get("message_id") == message             and previous.get("proposal_version") == proposal["version"]:
        return row
    row["approval_request"] = request
    if row["state"] == "approval_request_due":
        _move(row, "awaiting_approval", "main", "message " + message, stamp, prompt)
    else:
        row["updated_at"] = stamp
    return row


def find_request(doc: Mapping[str, Any], conversation_id: str, message_id: str) -> str | None:
    for root_id, row in (doc.get("workflows") or {}).items():
        request = row.get("approval_request") or {}
        if request.get("message_id") == str(message_id) and request.get("conversation_id") == conversation_id:
            return root_id
    return None


def approvals_due(doc: Mapping[str, Any], effective: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """awaiting_approval requests the periodic check re-reads, P0 then P1 then the rest."""
    rows = []
    order = {node["id"]: index for index, node in enumerate(doc.get("nodes", []))}
    for root_id, row in (doc.get("workflows") or {}).items():
        request = row.get("approval_request") or {}
        if row["state"] != "awaiting_approval" or not request.get("message_id"):
            continue
        priority = (effective or {}).get(root_id) or row.get("priority") or ""
        rows.append({"root_id": root_id, "priority": priority, "conversation_id": request["conversation_id"],
                     "message_id": request["message_id"], "thread_root_id": request.get("thread_root_id"),
                     "proposal_version": request["proposal_version"]})
    rows.sort(key=lambda r: (_rank(r["priority"]), order.get(r["root_id"], 0)))
    return rows


def _rank(priority: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5}.get(priority or "", 6)


def apply_decision(doc: dict[str, Any], root_id: str, verdict: Mapping[str, Any], *,
                   now: float | None = None) -> tuple[dict[str, Any], bool]:
    """Apply one host-verified approval or rejection: (record, applied).

    ``verdict`` comes only from work_workflow_approval.verify. The same event
    id, or a second report of an approval already applied, changes nothing.
    """
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    event_id = str(verdict.get("event_id") or "")[:120]
    seen = row.setdefault("events", [])
    if event_id and event_id in seen:
        return row, False
    decision = verdict.get("decision")
    if decision not in ("approved", "rejected"):
        raise WorkflowError("verdict must be approved or rejected")
    if row["state"] != "awaiting_approval" or verdict.get("proposal_version") != (row.get("proposal") or {}).get("version"):
        # Already decided (SSE and the periodic check both saw it) or stale.
        if event_id:
            row["events"] = (seen + [event_id])[-MAX_EVENTS:]
        return row, False
    stamp = _now(now)
    if event_id:
        row["events"] = (seen + [event_id])[-MAX_EVENTS:]
    detail = {"event_id": event_id, "approver_id": str(verdict.get("approver_id") or "")[:80],
              "via": str(verdict.get("via") or "")[:10], "at": stamp,
              "proposal_version": verdict["proposal_version"], "note": str(verdict.get("note") or "")[:1000]}
    if decision == "approved":
        row["approval"] = detail
        _move(row, "approved", "host", "approval " + (event_id or detail["via"]), stamp)
        _move(row, "executing", "host", "goal executing", stamp)
    else:
        row["rejections"] = ((row.get("rejections") or []) + [detail])[-10:]
        _move(row, "changes_requested", "host", "rejection " + (event_id or detail["via"]), stamp)
    return row, True


# ---------------------------------------------------------------- report and review

def step_for_item(doc: Mapping[str, Any], root_id: str, item_id: str) -> str | None:
    """The proposal step (S-n) a board item belongs to: its own or an ancestor's ``wf:`` ref."""
    by_id = {node["id"]: node for node in doc.get("nodes", [])}
    node = by_id.get(item_id)
    while node is not None:
        match = re.fullmatch(r"wf:" + re.escape(root_id) + r":(S-[1-9][0-9]{0,3})", node.get("ref") or "")
        if match:
            return match.group(1)
        node = by_id.get(node.get("parent_id"))
    return None


def linked_qa(row: Mapping[str, Any], step: str | None) -> list[str]:
    proposal = row.get("proposal") or {}
    if step and step in (proposal.get("step_qa") or {}):
        return list(proposal["step_qa"][step])
    return list(proposal.get("qa") or [])


def validate_report(row: Mapping[str, Any], step: str | None, detail: Any) -> dict[str, Any]:
    """Structure of a completion report (spec 4.6); file checks are the caller's."""
    if not isinstance(detail, dict):
        raise WorkflowError("workflow completion requires detail {summary, changes, qa_results, evidence_files, "
                            "deviations, open_issues}")
    summary = _text(detail.get("summary"), "report summary", limit=8000)
    changes = _text(detail.get("changes"), "report changes", limit=8000)
    files = detail.get("evidence_files")
    if not isinstance(files, list) or not files:
        raise WorkflowError("report evidence_files[] is required: path, kind (screenshot|log|output), sha256")
    clean_files = []
    for index, item in enumerate(files[:50]):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"].strip():
            raise WorkflowError(f"evidence_files[{index}].path required")
        if item.get("kind") not in EVIDENCE_KINDS:
            raise WorkflowError(f"evidence_files[{index}].kind must be screenshot, log or output")
        if not isinstance(item.get("sha256"), str) or not HEX64.fullmatch(item["sha256"]):
            raise WorkflowError(f"evidence_files[{index}].sha256 must be 64 lowercase hex")
        clean_files.append({"path": item["path"].strip()[:1000], "kind": item["kind"], "sha256": item["sha256"]})
    paths = {item["path"] for item in clean_files}
    results = detail.get("qa_results")
    if not isinstance(results, list):
        raise WorkflowError("report qa_results[] is required")
    by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(results[:100]):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not QA_ID.fullmatch(item["id"]):
            raise WorkflowError(f"qa_results[{index}].id must be a QA id")
        if item.get("status") not in QA_STATUSES:
            raise WorkflowError(f"qa_results[{index}].status must be pass, fail or not_run")
        evidence = item.get("evidence_files") or []
        if not isinstance(evidence, list) or any(path not in paths for path in evidence):
            raise WorkflowError(f"qa_results[{index}].evidence_files must name files listed in evidence_files")
        if item["status"] == "pass" and not evidence:
            raise WorkflowError(f"{item['id']} pass requires at least one evidence file")
        code = item.get("exit_code")
        if code is not None and type(code) is not int:
            raise WorkflowError(f"qa_results[{index}].exit_code must be an integer or null")
        by_id[item["id"]] = {"id": item["id"], "status": item["status"], "exit_code": code,
                             "command": _text(item.get("command"), "qa command", required=False, limit=4000),
                             "actual": _text(item.get("actual"), f"{item['id']} actual", limit=4000),
                             "evidence_files": list(evidence)}
    missing = [qa for qa in linked_qa(row, step) if qa not in by_id]
    if missing:
        raise WorkflowError("report misses linked QA: " + ", ".join(missing))
    return {"summary": summary, "changes": changes, "qa_results": list(by_id.values()), "evidence_files": clean_files,
            "deviations": _text(detail.get("deviations"), "deviations", required=False, limit=8000),
            "open_issues": _text(detail.get("open_issues"), "open_issues", required=False, limit=8000),
            "step": step}


def _goal_state(row: dict[str, Any]) -> str:
    states = [step.get("state") for step in (row.get("steps") or {}).values()]
    if "escalated" in states:
        return "escalated"
    if "awaiting_review" in states:
        return "awaiting_review"
    if "rework" in states:
        return "rework"
    return "executing"


def _settle(row: dict[str, Any], actor: str, ref: str, now: float) -> None:
    target = _goal_state(row)
    if target != row["state"]:
        if target not in TRANSITIONS.get(row["state"], ()):
            # e.g. executing -> rework is reached through awaiting_review.
            _move(row, "awaiting_review", actor, ref, now)
            if target == "awaiting_review":
                return
        _move(row, target, actor, ref, now)


def report_accepted(doc: dict[str, Any], root_id: str, item_id: str, lease_id: str, report: Mapping[str, Any],
                    report_digest: str, *, fork: Mapping[str, Any] | None = None,
                    now: float | None = None) -> dict[str, Any]:
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    if row["state"] not in ("executing", "rework", "awaiting_review", "approved"):
        raise WorkflowError(f"goal {root_id} is {row['state']}; no report is due")
    stamp = _now(now)
    steps = row.setdefault("steps", {})
    step = steps.setdefault(item_id, {"state": "executing", "reworks": 0, "reviews": [], "leases": []})
    if report_digest in [r.get("digest") for r in step.get("reports", [])]:
        return row
    step["state"] = "awaiting_review"
    step["lease_id"] = lease_id
    step["leases"] = list(dict.fromkeys((step.get("leases") or []) + [lease_id]))[-12:]
    step["step"] = report.get("step")
    step["reports"] = (step.get("reports") or [])[-9:] + [{
        "digest": report_digest, "lease_id": lease_id, "at": stamp,
        "qa": {r["id"]: r["status"] for r in report.get("qa_results", [])},
        "fork": dict(fork or {})}]
    _settle(row, "fork", "report " + report_digest[:12], stamp)
    return row


def review(doc: dict[str, Any], root_id: str, item_id: str, lease_id: str, decision: str, args: Mapping[str, Any],
           *, max_rework: int, reviewer: Mapping[str, Any] | None = None,
           now: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Main's per-QA review of one step (spec 4.7): (record, recorded review).

    approve: every linked QA has a ``pass`` verdict and ``evidence_checked``
    is true. rework: a structured instruction (failed QA, observed, fix,
    evidence to resubmit). A rework past ``max_rework`` escalates instead.
    """
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    step = (row.get("steps") or {}).get(item_id)
    if step is None or step.get("state") not in ("awaiting_review", "escalated"):
        raise WorkflowError(f"item {item_id} has no workflow report awaiting review")
    report = (step.get("reports") or [{}])[-1]
    linked = list(report.get("qa") or {})
    verdicts = args.get("qa_verdicts")
    if not isinstance(verdicts, list):
        raise WorkflowError("qa_verdicts[] {id, verdict: pass|fail, note} required for a workflow review")
    seen = {}
    for index, item in enumerate(verdicts[:100]):
        if not isinstance(item, dict) or item.get("id") not in linked or item.get("verdict") not in ("pass", "fail"):
            raise WorkflowError(f"qa_verdicts[{index}] must name a reported QA id with verdict pass or fail")
        seen[item["id"]] = {"id": item["id"], "verdict": item["verdict"], "note": str(item.get("note") or "")[:2000]}
    missing = [qa for qa in linked if qa not in seen]
    if missing:
        raise WorkflowError("qa_verdicts misses " + ", ".join(missing))
    stamp = _now(now)
    who = dict(reviewer or {"kind": "main", "id": "main"})
    entry = {"decision": decision, "lease_id": lease_id, "at": stamp, "verdicts": list(seen.values()),
             "reviewer": {k: str(v)[:120] for k, v in who.items()}}
    if decision == "approve":
        failed = [qa for qa, v in seen.items() if v["verdict"] != "pass"]
        if failed:
            raise WorkflowError("approve requires every QA to pass; failed: " + ", ".join(failed))
        if args.get("evidence_checked") is not True:
            raise WorkflowError("approve requires evidence_checked=true: main opened the evidence files itself")
        step["state"] = "approved"
    elif decision == "rework":
        packet = args.get("rework")
        if not isinstance(packet, dict):
            raise WorkflowError("rework {failed_qa[], observed, fix, evidence_required} required")
        failed_qa = packet.get("failed_qa")
        if not isinstance(failed_qa, list) or not failed_qa or any(qa not in linked for qa in failed_qa):
            raise WorkflowError("rework.failed_qa must list reported QA ids")
        entry["rework"] = {"failed_qa": list(dict.fromkeys(failed_qa)),
                           **{key: _text(packet.get(key), "rework." + key, limit=4000)
                              for key in ("observed", "fix", "evidence_required")}}
        if int(step.get("reworks") or 0) >= max_rework and not step.get("extra_rework"):
            step["state"] = "escalated"
            step.setdefault("reviews", []).append({**entry, "decision": "escalated"})
            row["escalation"] = {"item_id": item_id, "lease_id": lease_id, "at": stamp,
                                 "reworks": int(step.get("reworks") or 0), "max_rework": max_rework}
            _settle(row, "main", "rework limit " + item_id, stamp)
            return row, {**entry, "decision": "escalated"}
        step["reworks"] = int(step.get("reworks") or 0) + 1
        step.pop("extra_rework", None)
        row["reworks"] = int(row.get("reworks") or 0) + 1
        step["state"] = "rework"
    else:
        raise WorkflowError("decision must be approve or rework")
    step["reviews"] = (step.get("reviews") or [])[-9:] + [entry]
    _settle(row, str(who.get("kind") or "main"), decision + " " + item_id, stamp)
    return row, entry


def rework_resubmitted(doc: dict[str, Any], root_id: str, item_id: str, now: float | None = None) -> None:
    """A rework fork started on the step (no state change needed beyond bookkeeping)."""
    row = record(doc, root_id)
    if row and item_id in (row.get("steps") or {}):
        row["steps"][item_id]["rework_started_at"] = _now(now)


def complete_if_done(doc: dict[str, Any], root_id: str, now: float | None = None) -> bool:
    """completed once every step main reviewed is approved and no board leaf is left (spec 4.8)."""
    row = record(doc, root_id)
    if row is None or row["state"] in TERMINAL:
        return False
    steps = row.get("steps") or {}
    if not steps or any(step.get("state") != "approved" for step in steps.values()):
        return False
    nodes = [n for n in doc.get("nodes", []) if n.get("parent_id") is not None]
    by_id = {node["id"]: node for node in doc.get("nodes", [])}

    def root_of(node):
        while node.get("parent_id"):
            node = by_id[node["parent_id"]]
        return node["id"]

    children = {n.get("parent_id") for n in nodes}
    open_leaves = [n for n in nodes if n["id"] not in children and root_of(n) == root_id and n["status"] != "done"]
    if open_leaves:
        return False
    stamp = _now(now)
    if row["state"] != "executing" and "completed" not in TRANSITIONS.get(row["state"], ()):
        return False
    _move(row, "completed", "main", "all steps approved", stamp)
    row["final_report"] = {"state": "due", "at": stamp}
    return True


def resolve(doc: dict[str, Any], root_id: str, action: str, args: Mapping[str, Any], *, actor: str,
            now: float | None = None) -> dict[str, Any]:
    """Operator/main actions shared by every surface (spec 9): cancel, resend, escalation choices."""
    row = record(doc, root_id)
    if row is None:
        raise WorkflowError(f"goal {root_id} has no workflow")
    stamp = _now(now)
    reason = str(args.get("reason") or "")[:1000]
    if action == "cancel":
        if row["state"] in TERMINAL:
            raise WorkflowError(f"goal {root_id} workflow is already {row['state']}")
        row["cancel_reason"] = reason
        _move(row, "cancelled", actor, "cancel", stamp)
        return row
    if action == "resend":
        # The approval message never arrived or its id was lost: main sends it again.
        if row["state"] != "awaiting_approval":
            raise WorkflowError("resend applies to a goal awaiting approval")
        row["approval_request"] = None
        _move(row, "approval_request_due", actor, "resend", stamp)
        return row
    if action in ("escalate_model", "escalate_main"):
        if row["state"] != "escalated":
            raise WorkflowError(f"goal {root_id} is not escalated")
        item = (row.get("escalation") or {}).get("item_id")
        step = (row.get("steps") or {}).get(item)
        if step is None:
            raise WorkflowError("escalated step unknown")
        if action == "escalate_model":
            model = str(args.get("model") or "").strip()
            if not model or len(model) > 120:
                raise WorkflowError("escalate_model needs the model id the next rework runs on")
            step["model_override"] = model
            step["extra_rework"] = True
            step["state"] = "awaiting_review"
            row["escalation"] = {**(row.get("escalation") or {}), "choice": "model", "model": model,
                                 "resolved_at": stamp}
            # Main now sends the structured rework (one more is admitted).
        else:
            step["state"] = "awaiting_review"
            step["main_handles"] = True
            row["escalation"] = {**(row.get("escalation") or {}), "choice": "main", "resolved_at": stamp}
        _move(row, "awaiting_review", actor, action, stamp)
        return row
    raise WorkflowError("action must be cancel, resend, escalate_model or escalate_main")


# ---------------------------------------------------------------- main's queue and views

def todo(doc: Mapping[str, Any], effective: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Main's workflow to-do queue: P0 first, then priority, then arrival (spec 6)."""
    rows = []
    titles = {node["id"]: node.get("title", "") for node in doc.get("nodes", [])}
    for root_id, row in (doc.get("workflows") or {}).items():
        if row["state"] not in MAIN_STATES or root_id not in titles:
            continue
        priority = (effective or {}).get(root_id) or row.get("priority") or ""
        rows.append({"root_id": root_id, "title": titles[root_id][:200], "state": row["state"],
                     "priority": priority, "due_at": row.get("main_due_at") or row.get("updated_at") or 0,
                     "delegated": (row.get("delegation") or {}).get("state") == "running"})
    rows.sort(key=lambda r: (_rank(r["priority"]), r["due_at"], r["root_id"]))
    return rows


def view(doc: Mapping[str, Any], root_id: str, effective: Mapping[str, str] | None = None) -> dict[str, Any]:
    row = record(doc, root_id) or {}
    title = next((n.get("title", "") for n in doc.get("nodes", []) if n["id"] == root_id), "")
    proposal = row.get("proposal") or {}
    request = row.get("approval_request") or {}
    blocked = gate(doc, root_id)
    steps = {item: {"state": step.get("state"), "reworks": step.get("reworks", 0),
                    "step": step.get("step"), "qa": ((step.get("reports") or [{}])[-1]).get("qa", {}),
                    "lease_id": step.get("lease_id"), "model_override": step.get("model_override")}
             for item, step in (row.get("steps") or {}).items()}
    return {"root_id": root_id, "title": title[:200], "state": row.get("state"), "owner": OWNER.get(row.get("state"), ""),
            "priority": (effective or {}).get(root_id) or row.get("priority") or "",
            "blocked_reason": blocked, "blocked_text": gate_text(root_id, blocked) if blocked else "",
            "proposal": {key: proposal.get(key) for key in ("version", "digest", "code", "ui_changes", "mockups",
                                                             "qa", "steps", "author", "submitted_at")} if proposal else None,
            "approval_request": {key: request.get(key) for key in ("conversation_id", "thread_root_id", "message_id",
                                                                  "approvers", "proposal_version", "registered_at",
                                                                  "mockups_attached")} if request else None,
            "approval": row.get("approval"), "rejections": (row.get("rejections") or [])[-3:],
            "steps": steps, "reworks": row.get("reworks", 0), "escalation": row.get("escalation"),
            "delegation": row.get("delegation"), "final_report": row.get("final_report"),
            "history": (row.get("history") or [])[-12:], "updated_at": row.get("updated_at")}


def projection(doc: Mapping[str, Any], effective: Mapping[str, str] | None = None, *,
               config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    workflows = doc.get("workflows") or {}
    rows = [view(doc, root_id, effective) for root_id in workflows]
    rows.sort(key=lambda r: (r["state"] in TERMINAL, _rank(r["priority"]), r["root_id"]))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"enabled": enabled(config or {}), "settings": settings(config or {}), "goals": rows[:200],
            "counts": counts, "todo": todo(doc, effective)[:50],
            "awaiting_approval": [r["root_id"] for r in rows if r["state"] == "awaiting_approval"]}


def metrics(doc: Mapping[str, Any]) -> dict[str, Any]:
    """Per-model quality (spec 10): first-pass rate, mean reworks, final pass rate, tokens, cost fields."""
    per: dict[str, dict[str, Any]] = {}
    for root_id, row in (doc.get("workflows") or {}).items():
        for item_id, step in (row.get("steps") or {}).items():
            reports = step.get("reports") or []
            if not reports:
                continue
            fork = reports[0].get("fork") or {}
            model = str(fork.get("model") or "inherit")
            entry = per.setdefault(model, {"model": model, "steps": 0, "first_pass": 0, "reworks": 0,
                                           "approved": 0, "failed_qa": {}, "tokens": 0, "seconds": 0.0,
                                           "main_models": set()})
            entry["steps"] += 1
            entry["reworks"] += int(step.get("reworks") or 0)
            if step.get("state") == "approved":
                entry["approved"] += 1
                if int(step.get("reworks") or 0) == 0:
                    entry["first_pass"] += 1
            for review_row in step.get("reviews") or []:
                for verdict in review_row.get("verdicts") or []:
                    if verdict.get("verdict") == "fail":
                        entry["failed_qa"][verdict["id"]] = entry["failed_qa"].get(verdict["id"], 0) + 1
            for report_row in reports:
                usage = report_row.get("fork") or {}
                entry["tokens"] += int(usage.get("tokens") or 0)
                entry["seconds"] += float(usage.get("seconds") or 0)
                if usage.get("main_model"):
                    entry["main_models"].add(str(usage["main_model"])[:120])
    rows = []
    for entry in per.values():
        steps = entry["steps"] or 1
        rows.append({**entry, "main_models": sorted(entry["main_models"]),
                     "first_pass_rate": round(entry["first_pass"] / steps, 4),
                     "mean_reworks": round(entry["reworks"] / steps, 4),
                     "final_pass_rate": round(entry["approved"] / steps, 4)})
    rows.sort(key=lambda r: r["model"])
    return {"models": rows}


__all__ = ["CONFIG", "KEYS", "MAIN_STATES", "OWNER", "QA_METHODS", "RUN_STATES", "STATES", "TERMINAL", "TRANSITIONS",
           "WorkflowError", "apply_decision", "approval_requested", "approvals_due", "complete_if_done",
           "confirmation_code", "digest", "enabled", "find_request", "gate", "gate_text", "linked_qa", "metrics",
           "normalize", "projection", "record", "report_accepted", "resolve", "review", "set_value",
           "setting_value", "settings", "start", "step_for_item", "submit_proposal", "todo", "validate_proposal",
           "validate_report", "view"]
