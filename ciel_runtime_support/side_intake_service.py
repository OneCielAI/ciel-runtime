"""Receive before board admission; retries reconcile cross-file commits safely."""
from .side_intake_inbox import SideIntakeInbox
from .work_plan_store import session_work_plan_store
from .work_side_intake import enqueue
from .work_plan_tree import WorkPlanCapacityError


def receive(state_dir, session_id, text, event_key, *, admit=True):
    inbox = SideIntakeInbox(state_dir, session_id)
    receipt = inbox.receive(text, event_key)
    if not admit or receipt['item_id']:
        return receipt
    try:
        store = session_work_plan_store(state_dir, session_id)
        linked = enqueue(store, text, event_key)
    except WorkPlanCapacityError:
        return {**receipt, 'board_pending': True}
    except (OSError, ValueError) as error:
        # Inbox is already committed. Board availability is a separate stage,
        # not grounds to lose or falsely reject the durable receipt.
        return {**receipt, 'board_pending': True, 'board_error': type(error).__name__}
    inbox.link(receipt['receipt_id'], linked['item_id'])
    return {**receipt, 'item_id': linked['item_id'], 'stage': 'board_queued', 'board_pending': False}


def drain(state_dir, session_id, *, admit=True, limit=10):
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError('side drain batch limit must be an integer from 1 to 100')
    inbox = SideIntakeInbox(state_dir, session_id)
    errors = []
    if admit:
        from .channel_message_repository import exclusive_file_lock
        cursor_path = inbox.directory / 'drain-cursor.json'
        with exclusive_file_lock(cursor_path):
            cursor = inbox._read_file(cursor_path) or {}
            rows = inbox.pending(limit, after=str(cursor.get('after') or ''))
            if rows:
                inbox._save_file(cursor_path, {'after': rows[-1]['receipt_id']})
        for row in rows:
            # One unadmittable event must not strand independent mail. Every
            # failure retains its pending file; a later hook can retry it.
            try:
                result = receive(state_dir, session_id, row['text'], row['event_key'])
                if result.get('board_error'):
                    errors.append({**result, 'error': result['board_error']})
            except (OSError, ValueError) as error:
                errors.append({'receipt_id': row['receipt_id'], 'stage': 'received',
                               'recap': row['text'][:500], 'error': type(error).__name__})
    return inbox.summaries() + errors
