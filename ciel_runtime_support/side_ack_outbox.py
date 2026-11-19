"""Receipt ACK outbox for external side events: retired 2026-11-19.

The user's decision (2026-11-19): the source gets one message per event, the
actor's understanding and the processing result together (side_reply_outbox).
No receipt ACK is owed at receive any more. ``prepare`` settles a marker an
earlier build left, or a call from an older mod, as ``not_applicable`` and
sends nothing; ``record`` still closes an attempt an earlier build started.

Another Ciel session's receipt ACK arriving here is still recognized and
settled at admission (side_reply_connector.is_receipt_ack), so agents on older
builds never start an ACK loop with this one.
"""
import time

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError

PENDING_LIMIT = 20
RETIRED = 'receipt_ack_retired'


def _clear(inbox, receipt_id):
    inbox.ack_marker(receipt_id).unlink(missing_ok=True)


def pending(state_dir, identity, limit=PENDING_LIMIT):
    """Markers an earlier build left, oldest first: a caller settles each with prepare."""
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
    """Settle one receipt's ACK without a send (the ACK is retired)."""
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        _clear(inbox, receipt_id)
        if not row:
            raise WorkPlanError('side receipt missing')
        previous = row.get('ack_outbox') if isinstance(row.get('ack_outbox'), dict) else {}
        if previous.get('state') in ('submitting', 'uncertain', 'submitted', 'failed', 'not_applicable'):
            return {'state': previous['state'], 'receipt_id': receipt_id}
        row['external_ack'] = 'not_applicable'
        row['ack_outbox'] = {'state': 'not_applicable', 'updated_at': time.time(), 'reason': RETIRED}
        _save(inbox, receipt_id, row)
        return {'state': 'not_applicable', 'receipt_id': receipt_id,
                'receipt': inbox._receipt(receipt_id, row, duplicate=False)}


def record(state_dir, identity, receipt_id, state, *, attempt_id):
    """Close an ACK send an earlier build started; a failure is never owed again."""
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
        row['external_ack'] = state
        row['ack_outbox'] = {'state': state, 'updated_at': time.time(), 'attempt_id': attempt_id}
        _save(inbox, receipt_id, row)
        return inbox._receipt(receipt_id, row, duplicate=False)
