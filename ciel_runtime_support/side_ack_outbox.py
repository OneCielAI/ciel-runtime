"""Durable immediate ACK/readback delivery for external side events.

The host sends this before source prefetch, compaction, or an AI fork.  It is a
separate exact-once outbox from the later processing-result reply: an uncertain
ACK is never silently repeated, and a restart can still send an ACK that was
never prepared.
"""
import json
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError


ACK_LIMIT = 1200


def _readback(text):
    """Bounded host-owned readback from an event, without model inference."""
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        body = None
    data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else {}
    message = data.get("text")
    if not isinstance(message, str) or not message.strip():
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        message = payload.get("text")
    if isinstance(message, str) and message.strip():
        recap = " ".join(message.split())
    else:
        event_type = data.get("event_type") or (body.get("type") if isinstance(body, dict) else None)
        recap = "외부 이벤트 " + str(event_type or "요청")
    if len(recap) > ACK_LIMIT:
        recap = recap[: ACK_LIMIT - 1] + "…"
    return "ACK — 요청을 받았습니다.\n\n복기: " + recap


def prepare(state_dir, identity, receipt_id, *, config=None):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path("locks", receipt_id)):
        row = inbox._load(receipt_id)
        if not row:
            raise WorkPlanError("side receipt missing")
        previous = row.get("ack_outbox") if isinstance(row.get("ack_outbox"), dict) else {}
        if previous.get("state") in ("submitting", "uncertain", "submitted", "not_applicable"):
            return {"state": previous["state"], "receipt_id": receipt_id}
        route = row.get("route") if isinstance(row.get("route"), dict) else {}
        # Browser replies are one-shot: the final answer owns that reply token.
        # Immediate ACK/readback is the external SSE contract.
        connector = None
        if route.get("source_kind") == "external_event":
            from .side_reply_connector import prepare as connector_prepare
            connector = connector_prepare(config or {}, route, receipt_id, _readback(row["text"]), "status",
                                          original=row["text"], phase="ack")
        if connector is None:
            row["external_ack"] = "not_applicable"
            row["ack_outbox"] = {"state": "not_applicable", "updated_at": time.time()}
            inbox._save_file(inbox._event_path("receipts" if row.get("stage") in ("processed", "board_queued")
                                              else "pending", receipt_id), row)
            return {"state": "not_applicable", "receipt_id": receipt_id,
                    "receipt": inbox._receipt(receipt_id, row, duplicate=False)}
        attempt_id = uuid.uuid4().hex
        row["external_ack"] = "submitting"
        row["ack_outbox"] = {"state": "submitting", "started_at": time.time(), "attempt_id": attempt_id}
        inbox._save_file(inbox._event_path("receipts" if row.get("stage") in ("processed", "board_queued")
                                          else "pending", receipt_id), row)
        return {"state": "submitting", "receipt_id": receipt_id, "attempt_id": attempt_id, **connector}


def record(state_dir, identity, receipt_id, state, *, attempt_id):
    if state not in ("submitted", "uncertain", "failed"):
        raise WorkPlanError("invalid side ACK delivery state")
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path("locks", receipt_id)):
        row = inbox._load(receipt_id)
        previous = row.get("ack_outbox") if isinstance((row or {}).get("ack_outbox"), dict) else {}
        if not row or previous.get("state") != "submitting":
            raise WorkPlanError("side ACK was not prepared")
        if not attempt_id or previous.get("attempt_id") != attempt_id:
            raise WorkPlanError("stale side ACK attempt")
        row["external_ack"] = state
        row["ack_outbox"] = {"state": state, "updated_at": time.time(), "attempt_id": attempt_id}
        inbox._save_file(inbox._event_path("receipts" if row.get("stage") in ("processed", "board_queued")
                                          else "pending", receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)
