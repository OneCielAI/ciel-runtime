"""Durable reply submission state; uncertain sends are never silently repeated."""
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError
import heapq


def pending(state_dir, identity, limit=10):
    directory=SideIntakeInbox(state_dir, identity).directory / 'receipts'
    def eligible():
        for path in directory.glob('*/*.json'):
            row=SideIntakeInbox._read_file(path)
            if (row and row.get('stage')=='processed' and row.get('result',{}).get('delivery') not in ('route_unavailable','side_mcp','duplicate_source','not_applicable')
                    and row.get('outbox',{}).get('state') not in ('submitted','uncertain','submitting','failed')):
                yield {'receipt_id':path.stem,'state':row.get('outbox',{}).get('state','pending')}
    return heapq.nsmallest(limit,eligible(),key=lambda row:row['receipt_id'])


def prepare(state_dir, identity, receipt_id, *, config=None):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row or row.get('stage') != 'processed':
            raise WorkPlanError('processed side decision required')
        route = row.get('route', {})
        previous = row.get('outbox', {})
        if previous.get('state') in ('submitting', 'uncertain', 'submitted'):
            return {'state': previous['state'], 'receipt_id': receipt_id}
        kind = 'status' if row['result'].get('action') == 'advise' else 'reply'
        # final_text closes the model's reply with the committed queue outcome.
        message = row['result'].get('final_text') or row['result']['reply']
        connector = None
        if route.get('source_kind') == 'external_event':
            from .side_reply_connector import prepare as connector_prepare
            original = row.get('text', '')
            event_key = str(row.get('event_key') or '')
            if event_key.startswith('handoff:'):
                # A coordinator follow-up answers the event that asked; its own
                # text is the answer, so the reply target is that event's.
                source = inbox._load(event_key[len('handoff:'):])
                original = source.get('text', '') if source else ''
            connector = connector_prepare(config or {}, route, receipt_id, message, kind, original=original)
        # An event that referenced no message or thread has no source to answer:
        # not_applicable, never route_unavailable (which asks main to send it).
        if connector is None and (row.get('source_context') == 'none_referenced'
                                  or row['result'].get('source_context') == 'none_referenced'):
            row['result']['delivery'] = 'not_applicable'
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return {'state': 'not_applicable', 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        # External receiver channel names alone are not outbound connectors.
        if connector is None and (route.get('source_kind') != 'web_chat' or not route.get('reply_token') or not route.get('channel')):
            # Legacy actors may have answered through the source MCP themselves
            # (reported by the side AI, not host-observed). New actors cannot send.
            row['result']['delivery'] = 'side_mcp' if row['result'].get('reported_sends') else 'route_unavailable'
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return {'state': row['result']['delivery'], 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        attempt_id = uuid.uuid4().hex
        row['outbox'] = {'state': 'submitting', 'started_at': time.time(), 'attempt_id': attempt_id}
        row['result']['delivery'] = 'submitting'
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        if connector is not None:
            return {'state': 'submitting', 'receipt_id': receipt_id, 'attempt_id': attempt_id, **connector}
        arguments = {key: route[key] for key in ('channel', 'thread_id', 'parent_id', 'recipients', 'reply_token') if key in route}
        # Advice is an interim status, not the browser's one allowed final
        # reply. Its correlated coordinator follow-up can then send that reply.
        return {'state': 'submitting', 'receipt_id': receipt_id, 'attempt_id': attempt_id,
                'arguments': {**arguments, 'message': message,
                              'kind': kind, 'delivery': ['web']}}


def retry(state_dir, identity, receipt_id, expected_state, confirmed=False):
    """Explicit operator retry; ambiguous outcomes require duplicate-send consent."""
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row or row.get('stage') != 'processed':
            raise WorkPlanError('processed side decision required')
        current = row.get('outbox', {}).get('state') or row.get('result', {}).get('delivery', 'pending')
        if current != expected_state:
            raise WorkPlanError('side delivery changed; refresh before retry')
        if current not in ('failed', 'route_unavailable', 'uncertain', 'submitting'):
            raise WorkPlanError('this delivery cannot be retried')
        if current in ('uncertain', 'submitting') and confirmed is not True:
            raise WorkPlanError('unknown send outcome: explicit duplicate-send confirmation required')
        row.setdefault('delivery_retries', []).append({'previous': dict(row.get('outbox', {})),
            'previous_delivery': current, 'duplicate_send_confirmed': confirmed is True, 'at': time.time()})
        row['outbox'] = {'state': 'pending'}
        row['result']['delivery'] = 'pending'
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)


def record(state_dir, identity, receipt_id, state, *, attempt_id):
    if state not in ('submitted', 'uncertain', 'failed'):
        raise WorkPlanError('invalid side delivery state')
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row or row.get('outbox', {}).get('state') != 'submitting':
            raise WorkPlanError('side send was not prepared')
        if not attempt_id or row['outbox'].get('attempt_id') != attempt_id:
            raise WorkPlanError('stale side reply attempt')
        row['outbox'] = {'state': state, 'updated_at': time.time(), 'attempt_id': attempt_id}
        row['result']['delivery'] = state
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)
