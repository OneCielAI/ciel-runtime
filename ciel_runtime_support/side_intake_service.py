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
    inbox = SideIntakeInbox(state_dir, session_id)
    if admit:
        for row in inbox.pending(limit):
            result = receive(state_dir, session_id, row['text'], row['event_key'])
            if result.get('board_pending'):
                break
    return inbox.summaries()
