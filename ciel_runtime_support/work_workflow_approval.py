"""Host verification of a Walkie approval (spec 4.4): code decides, never a model.

Inputs are only what the host itself holds: the raw CloudEvent the router
received over SSE (read back from the durable side inbox by receipt id) and
the approval-request message as the ciel-work host read it from Walkie
(threads.read, side-intake.js fetchSourceMessage). A model never supplies
either, so external text cannot approve anything.

Measured on Walkie events (technical-review.md, 19 of 19): the approver is
``data.payload.approver_id`` (equal to ``payload.sender_id``) and NOT the top
level ``data.sender_id``; the approved message is ``data.message_id`` =
``message_context.id`` = ``payload.message_id``; the verdict is
``payload.decision`` with ``all_approved``; thread rows carry ``approvals[]``
with ``user_id, decision, superseded_by, withdrawn_at``.

Every condition must hold for ``approved``:
1. the event is message.approved (the periodic check has no event and reads
   the decision from the verified thread instead);
2. the approved message is the registered request message, in its
   conversation and thread;
3. payload.decision is an approval and all_approved is true;
4. the approver (payload.approver_id) is one of the request's approvers;
5. the verified thread's approvals[] holds that approval, not withdrawn or
   superseded;
6. the request message text carries the current proposal confirmation code.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

APPROVE_WORDS = ("approved", "approve", "approval", "accept", "accepted", "yes")
REJECT_WORDS = ("rejected", "reject", "rejection", "declined", "decline", "deny", "denied", "no")


def parse_event(text: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def event_kind(event: Mapping[str, Any] | None) -> str:
    """'approved', 'rejected' or '' from the CloudEvent type (or data.event_type)."""
    if not isinstance(event, Mapping):
        return ""
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    names = [str(event.get("type") or ""), str(data.get("event_type") or ""), str(data.get("type") or "")]
    for name in names:
        tail = name.rsplit(".", 2)[-2:] if name else []
        joined = ".".join(tail)
        if joined == "message.approved":
            return "approved"
        if joined == "message.rejected":
            return "rejected"
    return ""


def event_target(event: Mapping[str, Any]) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    payload = data.get("payload") if isinstance(data.get("payload"), Mapping) else {}
    context = data.get("message_context") if isinstance(data.get("message_context"), Mapping) else {}
    ids = [value for value in (data.get("message_id"), payload.get("message_id"), context.get("id"))
           if value not in (None, "")]
    conversation = data.get("conversation_id") or data.get("conversation") or payload.get("conversation_id")
    return {"message_ids": [str(value) for value in ids], "conversation_id": str(conversation or ""),
            "payload": payload, "data": data}


def _decision_word(value: Any) -> str:
    word = str(value or "").strip().lower()
    if word in APPROVE_WORDS:
        return "approved"
    if word in REJECT_WORDS:
        return "rejected"
    return ""


def _message_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") if row.get("id") is not None else row.get("message_id") or "")


def _text_of(row: Mapping[str, Any]) -> str:
    parts = []
    for key in ("text", "content", "body", "message"):
        value = row.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item.get("text") or "") for item in value if isinstance(item, Mapping))
    return "\n".join(parts)


def _row_approvers(row: Mapping[str, Any]) -> list[str] | None:
    raw = row.get("approvers")
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if isinstance(item, Mapping):
            item = item.get("user_id") or item.get("id")
        if isinstance(item, (str, int)) and str(item).strip():
            out.append(str(item).strip())
    return out


def _live_approvals(row: Mapping[str, Any]) -> list[dict[str, str]]:
    """approvals[] entries that are neither withdrawn nor superseded."""
    out = []
    for item in row.get("approvals") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("withdrawn_at") not in (None, "") or item.get("superseded_by") not in (None, ""):
            continue
        user = item.get("user_id")
        decision = _decision_word(item.get("decision"))
        if user in (None, "") or not decision:
            continue
        out.append({"user_id": str(user), "decision": decision, "id": str(item.get("id") or ""),
                    "note": str(item.get("note") or item.get("choice") or "")})
    return out


def _request_row(request: Mapping[str, Any], read: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The periodic check's host read of the request message alone (2026-11-18).

    The first row is the request; any further rows are thread replies after the
    request's reply cursor. Lineage is taken from the row itself, never from a
    field of the read: a row outside the registered thread is refused.
    """
    if str(read.get("conversation_id") or "") != request["conversation_id"]:
        return None, "read is another conversation"
    rows = [row for row in read.get("messages") or [] if isinstance(row, Mapping)]
    if not rows or _message_id(rows[0]) != request["message_id"]:
        return None, "request message not read"
    row = dict(rows[0])
    conversation = row.get("conversation_id", row.get("conversation"))
    if conversation not in (None, "") and str(conversation) != request["conversation_id"]:
        return None, "request message is in another conversation"
    lineage = row.get("thread_root_id")
    lineage = None if lineage in (None, "") else str(lineage)
    registered = request.get("thread_root_id")
    outside = (lineage != registered and request["message_id"] != registered) if registered else lineage is not None
    if outside:
        return None, "request message is in another thread"
    if not isinstance(row.get("approvals"), list):
        # Decisions recorded as replies to the request (fields unmeasured on Walkie):
        # only replies in the same thread that answer this exact message count.
        root = registered or request["message_id"]
        merged = []
        for reply in rows[1:]:
            if (str(reply.get("thread_root_id") or "") == root
                    and str(reply.get("reply_to_message_id") or "") == request["message_id"]
                    and isinstance(reply.get("approvals"), list)):
                merged.extend(reply["approvals"])
        if merged:
            row["approvals"] = merged
    return row, ""


def _target_row(request: Mapping[str, Any], thread: Any) -> tuple[dict[str, Any] | None, str]:
    if isinstance(thread, Mapping) and thread.get("source_context") == "verified_request":
        return _request_row(request, thread)
    if not isinstance(thread, Mapping) or thread.get("source_context") != "verified_thread":
        return None, "verified thread unavailable"
    if str(thread.get("conversation_id") or "") != request["conversation_id"]:
        return None, "thread is another conversation"
    rows = [row for row in thread.get("messages") or [] if isinstance(row, Mapping)
            and _message_id(row) == request["message_id"]]
    if len(rows) != 1:
        return None, "request message not in the verified thread"
    row = dict(rows[0])
    registered_thread = request.get("thread_root_id")
    if registered_thread:
        own = row.get("thread_root_id")
        root = str(thread.get("root_id") or "")
        if str(own or root) != registered_thread and root != registered_thread:
            return None, "request message is in another thread"
    return row, ""


def verify(record: Mapping[str, Any], *, event: Mapping[str, Any] | None, thread: Any,
           event_id: str = "") -> dict[str, Any]:
    """The host verdict: decision approved | rejected | pending | invalid, with the reason."""
    request = record.get("approval_request") or {}
    proposal = record.get("proposal") or {}
    via = "sse" if event is not None else "check"
    out = {"decision": "invalid", "reason": "", "event_id": event_id, "via": via,
           "proposal_version": request.get("proposal_version"), "approver_id": ""}
    if record.get("state") != "awaiting_approval" or not request:
        return {**out, "reason": "no approval request is pending"}
    if request.get("proposal_version") != proposal.get("version") or request.get("proposal_digest") != proposal.get("digest"):
        return {**out, "reason": "approval request is for an earlier proposal version"}
    row, problem = _target_row(request, thread)
    if row is None:
        return {**out, "decision": "pending" if via == "check" else "invalid", "reason": problem}
    # 6. the request message carries the current confirmation code.
    if proposal.get("code") not in _text_of(row):
        return {**out, "reason": "request message lacks the current proposal code " + str(proposal.get("code"))}
    allowed = list(request.get("approvers") or [])
    named = _row_approvers(row)
    if named is not None:
        allowed = [value for value in allowed if value in named]
    live = _live_approvals(row)
    if event is None:
        # Periodic check: the verified thread is the only input.
        rejected = [a for a in live if a["decision"] == "rejected" and a["user_id"] in allowed]
        if rejected:
            pick = rejected[-1]
            return {**out, "decision": "rejected", "reason": "rejected in the verified thread",
                    "approver_id": pick["user_id"], "note": pick["note"],
                    "event_id": event_id or "check:" + _fingerprint(row, pick)}
        approved_users = {a["user_id"] for a in live if a["decision"] == "approved"}
        if allowed and all(user in approved_users for user in allowed):
            pick = [a for a in live if a["decision"] == "approved" and a["user_id"] in allowed][-1]
            return {**out, "decision": "approved", "reason": "all approvers approved in the verified thread",
                    "approver_id": pick["user_id"], "note": pick["note"],
                    "event_id": event_id or "check:" + _fingerprint(row, pick)}
        return {**out, "decision": "pending", "reason": "no complete approval in the verified thread"}
    # 1. event kind.
    kind = event_kind(event)
    if not kind:
        return {**out, "reason": "event is not message.approved or message.rejected"}
    target = event_target(event)
    # 2. the decided message is the registered request in its conversation.
    if not target["message_ids"] or any(value != request["message_id"] for value in target["message_ids"]):
        return {**out, "reason": "event decides another message"}
    if target["conversation_id"] and target["conversation_id"] != request["conversation_id"]:
        return {**out, "reason": "event is in another conversation"}
    payload = target["payload"]
    # 4. approver from payload.approver_id only (never data.sender_id).
    approver = str(payload.get("approver_id") or "").strip()
    if not approver:
        return {**out, "reason": "payload.approver_id missing"}
    if approver not in allowed:
        return {**out, "reason": "approver is not a designated approver", "approver_id": approver}
    decision = _decision_word(payload.get("decision")) or kind
    if decision != kind:
        return {**out, "reason": "payload.decision contradicts the event type", "approver_id": approver}
    note = str(payload.get("note") or payload.get("choice") or "")[:1000]
    entries = [a for a in live if a["user_id"] == approver]
    if kind == "approved":
        # 3. decision and all_approved.
        if payload.get("all_approved") is not True:
            return {**out, "reason": "all_approved is not true", "approver_id": approver}
        # 5. the verified thread holds this approval, live.
        if not entries or entries[-1]["decision"] != "approved":
            return {**out, "reason": "approval not in the verified thread (withdrawn, superseded or absent)",
                    "approver_id": approver}
        return {**out, "decision": "approved", "reason": "verified", "approver_id": approver, "note": note}
    if not entries or entries[-1]["decision"] != "rejected":
        return {**out, "reason": "rejection not in the verified thread", "approver_id": approver}
    return {**out, "decision": "rejected", "reason": "verified", "approver_id": approver, "note": note}


def _fingerprint(row: Mapping[str, Any], pick: Mapping[str, Any]) -> str:
    material = json.dumps([_message_id(row), pick.get("user_id"), pick.get("decision"), pick.get("id")])
    return hashlib.sha256(material.encode()).hexdigest()[:24]


def is_workflow_event(text: Any, doc: Mapping[str, Any]) -> str | None:
    """Root id when this raw event decides a registered approval request (P1/P3)."""
    event = parse_event(text)
    if not event_kind(event):
        return None
    target = event_target(event)
    from .work_workflow import find_request
    for message_id in target["message_ids"][:1]:
        conversation = target["conversation_id"]
        if conversation:
            found = find_request(doc, conversation, message_id)
            if found:
                return found
        for root_id, row in (doc.get("workflows") or {}).items():
            if (row.get("approval_request") or {}).get("message_id") == message_id:
                return root_id
    return None


__all__ = ["event_kind", "event_target", "is_workflow_event", "parse_event", "verify"]
