"""Durable immediate receipt ACK for external side events.

The host sends this as soon as an event is in the inbox, before any side slot,
source prefetch, compaction or AI fork. It says only that the message arrived;
it never repeats the message (user requirement 2026-11-17: an ACK that echoed
the event was not a readback). The actor's own restatement follows as the
``understanding`` message, then the processing result (side_reply_outbox).

It is a separate exact-once outbox: an uncertain ACK is never silently
repeated, and a restart still sends an ACK that was never prepared. A marker
under ``ack-pending/`` lists every event still owed one, so the host can send
it without opening every pending event.
"""
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError


# The connector owns the ACK texts (this build's and the readback prefix that
# builds up to 6cf8805 sent) so this module and the inbound recognizer share
# them without an import cycle. Another Ciel session's receipt ACK arriving here
# is a notice, not a request: answering it starts an ACK loop between two
# agents. Measured on this machine's Walkie session, 2026-11-17 07:49Z-22:00Z:
# 177 of 1,599 inbound Walkie events were another session's legacy receipt ACK,
# and 118 of those were sent a receipt ACK back.
from .side_reply_connector import RECEIPT_ACK_TEXT

PENDING_LIMIT = 20
MAX_ACK_FAILURES = 3


def mark_pending(inbox, receipt_id):
    """Called once at receive: this event is owed a receipt ACK."""
    inbox.mark_ack_owed(receipt_id)


def _clear(inbox, receipt_id):
    inbox.ack_marker(receipt_id).unlink(missing_ok=True)


def pending(state_dir, identity, limit=PENDING_LIMIT):
    """Receipt ids still owed an ACK, oldest marker first."""
    inbox = SideIntakeInbox(state_dir, identity)
    base = inbox.directory / 'ack-pending'
    if not base.is_dir():
        return []
    rows = sorted(base.glob('*/*.json'), key=lambda path: (path.stat().st_mtime, path.stem))
    return [path.stem for path in rows[:limit]]


def _save(inbox, receipt_id, row):
    inbox._save_file(inbox._event_path('receipts' if row.get('stage') in ('processed', 'board_queued')
                                       else 'pending', receipt_id), row)


def prepare(state_dir, identity, receipt_id, *, config=None):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row:
            _clear(inbox, receipt_id)
            raise WorkPlanError('side receipt missing')
        previous = row.get('ack_outbox') if isinstance(row.get('ack_outbox'), dict) else {}
        if (previous.get('state') in ('submitting', 'uncertain', 'submitted', 'not_applicable')
                or int(previous.get('failures') or 0) >= MAX_ACK_FAILURES):
            _clear(inbox, receipt_id)
            return {'state': previous['state'], 'receipt_id': receipt_id}
        route = row.get('route') if isinstance(row.get('route'), dict) else {}
        # Browser replies are one-shot: the final answer owns that reply token.
        # The immediate receipt ACK is the external SSE contract.
        connector = None
        reason = None
        if route.get('source_kind') == 'external_event':
            from .side_reply_connector import is_receipt_ack, prepare as connector_prepare
            if is_receipt_ack(row['text']):
                reason = 'receipt_ack_inbound'
            else:
                connector = connector_prepare(config or {}, route, receipt_id, RECEIPT_ACK_TEXT, 'status',
                                              original=row['text'], phase='ack')
        if connector is None:
            row['external_ack'] = 'not_applicable'
            row['ack_outbox'] = {'state': 'not_applicable', 'updated_at': time.time(),
                                 **({'reason': reason} if reason else {})}
            _save(inbox, receipt_id, row)
            _clear(inbox, receipt_id)
            return {'state': 'not_applicable', 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        attempt_id = uuid.uuid4().hex
        row['external_ack'] = 'submitting'
        row['ack_outbox'] = {'state': 'submitting', 'started_at': time.time(), 'attempt_id': attempt_id,
                             **({'failures': previous['failures']} if previous.get('failures') else {})}
        _save(inbox, receipt_id, row)
        # The send is now owned by this attempt; a crash leaves it submitting,
        # which is never resent (uncertain), so the marker has done its job.
        _clear(inbox, receipt_id)
        return {'state': 'submitting', 'receipt_id': receipt_id, 'attempt_id': attempt_id, **connector}


def record(state_dir, identity, receipt_id, state, *, attempt_id):
    if state not in ('submitted', 'uncertain', 'failed'):
        raise WorkPlanError('invalid side ACK delivery state')
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        previous = row.get('ack_outbox') if isinstance((row or {}).get('ack_outbox'), dict) else {}
        if not row or previous.get('state') != 'submitting':
            raise WorkPlanError('side ACK was not prepared')
        if not attempt_id or previous.get('attempt_id') != attempt_id:
            raise WorkPlanError('stale side ACK attempt')
        failures = int(previous.get('failures') or 0) + (1 if state == 'failed' else 0)
        row['external_ack'] = state
        row['ack_outbox'] = {'state': state, 'updated_at': time.time(), 'attempt_id': attempt_id,
                             **({'failures': failures} if failures else {})}
        _save(inbox, receipt_id, row)
        if state == 'failed' and failures < MAX_ACK_FAILURES:
            # A definite refusal sent nothing: owed again, a bounded number of times.
            mark_pending(inbox, receipt_id)
        return inbox._receipt(receipt_id, row, duplicate=False)
