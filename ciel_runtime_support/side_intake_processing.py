"""Fenced per-event AI processing; receipt commits precede pending removal."""
import time
import uuid
import heapq

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError


def claim(state_dir, session_id, owner, *, now=None, lease_seconds=120, aging_seconds=60):
    if not isinstance(owner, str) or not owner or len(owner) > 200:
        raise WorkPlanError('side claim owner required')
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise WorkPlanError('invalid side lease duration')
    if type(aging_seconds) is not int or not 1 <= aging_seconds <= 3600:
        raise WorkPlanError('invalid side aging duration')
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    cursor_path = inbox.directory / 'decision-cursor.json'
    with exclusive_file_lock(cursor_path):
        cursor = inbox._read_file(cursor_path) or {}
        rows = inbox.pending(100, after=str(cursor.get('after') or ''))
        if rows:
            inbox._save_file(cursor_path, {'after': rows[-1]['receipt_id']})
            # Reconcile a crash after durable pending write but before optional
            # priority-index commit; the event remains the authoritative source.
            for row in rows:
                inbox._index_priority(row['receipt_id'],row)
    # Priority markers are a rebuildable index, never authority for a request.
    # Do not open every pending event on each poll of a thousand-event inbox.
    def ranked():
        for priority in ('P1','P2','P3','P4','P5'):
            paths=(inbox.directory/'priority'/priority).glob('*/*.json')
            for marker in heapq.nsmallest(100,paths,key=lambda p:p.stem):
                row=inbox._load(marker.stem)
                if not row or row.get('stage')!='received':
                    marker.unlink(missing_ok=True)
                    continue
                if row.get('processing',{}).get('lease_until',0)>stamp:
                    continue
                yield (int(priority[1]),row.get('received_at',0),marker.stem,{**row,'receipt_id':marker.stem})
    prioritized=[entry[3] for entry in heapq.nsmallest(100,ranked(),key=lambda entry:entry[:3])]
    aged=sorted((row for row in rows if stamp-row.get('received_at',stamp)>=aging_seconds),key=lambda row:row.get('received_at',0))
    unique={row['receipt_id']:row for row in [*aged,*prioritized,*rows]}
    for row in unique.values():
        key = row['receipt_id']
        with exclusive_file_lock(inbox._event_path('locks', key)):
            current = inbox._load(key)
            if current is None or current.get('stage') != 'received':
                continue
            processing = current.get('processing') or {}
            if processing.get('lease_until', 0) > stamp:
                continue
            generation = uuid.uuid4().hex
            current['processing'] = {'owner': owner, 'generation': generation,
                                     'lease_until': stamp + lease_seconds, 'started_at': stamp}
            inbox._save_file(inbox._event_path('pending', key), current)
            return {'receipt_id': key, 'text': current['text'], 'event_key': current['event_key'],
                    'generation': generation, 'lease_until': stamp + lease_seconds,
                    'route': {k: v for k, v in current.get('route', {}).items() if k not in ('reply_token', 'authorization', 'token')}}
    return None


def verify_claim(inbox, receipt_id, generation, *, now=None):
    row = inbox._load(receipt_id)
    if row is None:
        raise WorkPlanError('side receipt missing')
    processing = row.get('processing') or {}
    if processing.get('generation') != generation:
        raise WorkPlanError('stale side claim')
    stamp = time.time() if now is None else now
    if processing.get('lease_until', 0) <= stamp:
        raise WorkPlanError('side claim expired')
    return row


def renew(state_dir, session_id, receipt_id, generation, *, now=None, lease_seconds=120):
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise WorkPlanError('invalid side lease duration')
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = verify_claim(inbox, receipt_id, generation, now=stamp)
        if row.get('stage') != 'received':
            raise WorkPlanError('side claim already settled')
        row['processing']['lease_until'] = stamp + lease_seconds
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return {'receipt_id': receipt_id, 'generation': generation, 'lease_until': stamp + lease_seconds}


def finish(state_dir, session_id, receipt_id, generation, result, *, now=None):
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        existing = inbox._load(receipt_id)
        if existing and existing.get('stage') == 'processed':
            if existing.get('processing', {}).get('generation') != generation:
                raise WorkPlanError('stale side claim')
            return inbox._receipt(receipt_id, existing, duplicate=True)
        row = verify_claim(inbox, receipt_id, generation, now=now)
        row.update(stage='processed', result=result, item_id=result.get('item_id'),
                   consumed_at=time.time() if now is None else now)
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        inbox._event_path('pending', receipt_id).unlink(missing_ok=True)
        inbox._clear_priority(receipt_id)
        return inbox._receipt(receipt_id, row, duplicate=False)
