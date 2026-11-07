"""Fenced per-event AI processing; receipt commits precede pending removal."""
import time
import uuid
import heapq

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox, source_priority
from .work_plan_tree import WorkPlanError


def claim(state_dir, session_id, owner, *, now=None, lease_seconds=120, aging_seconds=60,
          model_fingerprint=None, urgent_only=False):
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
                if (row.get('processing',{}).get('lease_until',0)>stamp
                        and (row.get('main_fallback') or {}).get('state')!='pending'):
                    continue
                yield (int(priority[1]),row.get('received_at',0),marker.stem,{**row,'receipt_id':marker.stem})
    prioritized=heapq.nsmallest(100,ranked(),key=lambda entry:entry[:3])
    urgent=[entry[3] for entry in prioritized if entry[0]==1]
    ranked_rest=[entry[3] for entry in prioritized if entry[0]!=1]
    aged=sorted((row for row in rows if stamp-row.get('received_at',stamp)>=aging_seconds),key=lambda row:row.get('received_at',0))
    # P1 goes first, ahead of aged backlog: an urgent event waited behind
    # every aged one (observed 2026-11-07, #116793). Aging still keeps older
    # ordinary events ahead of fresh P2..P5. urgent_only is the host's
    # reserved slot while ordinary capacity is full.
    order=urgent if urgent_only else [*urgent,*aged,*ranked_rest,*rows]
    unique={row['receipt_id']:row for row in order}
    for row in unique.values():
        key = row['receipt_id']
        with exclusive_file_lock(inbox._event_path('locks', key)):
            current = inbox._load(key)
            if current is None or current.get('stage') != 'received':
                continue
            processing = current.get('processing') or {}
            fallback_state = current.get('main_fallback') or {}
            if processing.get('lease_until', 0) > stamp and fallback_state.get('state') != 'pending':
                continue
            if fallback_state.get('state') == 'queued':
                continue
            if fallback_state.get('state') == 'pending':
                # Legacy builds prepared raw main-session injection because the
                # side model was unavailable. Reclaim the retained original for
                # the side actor on the main session model instead.
                current.pop('main_fallback', None)
                current.pop('processing', None)
                current['side_failure'] = {
                    'category': 'legacy_main_fallback_reclaimed',
                    'attempt_count': int((current.get('side_failure') or {}).get('attempt_count') or 0),
                    'failed_at': stamp, 'retry_not_before': 0, 'quarantined': False,
                    'retryable': True, 'use_parent_model': True, 'model_fingerprint': model_fingerprint,
                }
                inbox._save_file(inbox._event_path('pending', key), current)
            failure = current.get('side_failure') or {}
            if failure.get('quarantined') and failure.get('category') == 'invalid_answer':
                # A completed turn may already have replied externally; a model
                # change must not replay it either.
                continue
            if failure.get('quarantined') and failure.get('model_fingerprint') == model_fingerprint:
                # Legacy generic model_error had no typed router outcome and may
                # have quarantined a transient 5xx. Re-evaluate once under the
                # new exact-main-fallback lifecycle.
                if failure.get('category') == 'model_error' and not failure.get('typed'):
                    failure['quarantined'] = False
                    failure['retry_not_before'] = 0
                    failure['legacy_recheck'] = True
                    current['side_failure'] = failure
                else:
                    continue
            if not failure.get('quarantined') and failure.get('retry_not_before', 0) > stamp:
                continue
            generation = uuid.uuid4().hex
            current['processing'] = {'owner': owner, 'generation': generation,
                                     'lease_until': stamp + lease_seconds, 'started_at': stamp}
            inbox._save_file(inbox._event_path('pending', key), current)
            return {'receipt_id': key, 'text': current['text'], 'event_key': current['event_key'],
                    'generation': generation, 'lease_until': stamp + lease_seconds,
                    'source_priority': source_priority(current['text']),
                    'use_parent_model': bool(failure.get('use_parent_model')
                                             and failure.get('model_fingerprint') == model_fingerprint),
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


def fail(state_dir, session_id, receipt_id, generation, failure, *, now=None):
    """Release an exact failed claim with durable backoff or quarantine."""
    stamp = time.time() if now is None else now
    if not isinstance(failure, dict):
        raise WorkPlanError('side failure object required')
    category = str(failure.get('category') or 'error')[:80]
    model = str(failure.get('model') or '')[:300]
    reason = str(failure.get('reason') or '')[:300]
    fingerprint = str(failure.get('model_fingerprint') or '')[:128]
    outcome = failure.get('outcome') if isinstance(failure.get('outcome'), dict) else {}
    terminal = str(outcome.get('terminal_reason') or '')[:200]
    code = str(outcome.get('code') or '').lower()
    unavailable = (category in ('model_not_found', 'invalid_model')
                   or code in ('model_not_found', 'invalid_model')
                   or 'no_executable_targets' in terminal)
    # A completed model turn may already have sent an external MCP reply. If its
    # final decision is invalid, retrying can duplicate that side effect.
    permanent = category in ('invalid_request', 'invalid_answer')
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = verify_claim(inbox, receipt_id, generation, now=stamp)
        prior = row.get('side_failure') or {}
        attempts = int(prior.get('attempt_count') or 0) + 1
        delay = min(300, 5 * (2 ** min(attempts - 1, 6)))
        # The selected side model is unavailable: the same side actor retries the
        # event on the main session model (same provider connection). If that
        # retry is unavailable too, quarantine instead of looping.
        parent_retry = unavailable and not prior.get('use_parent_model')
        row['side_failure'] = {
            'category': category,
            'model': model,
            'model_fingerprint': fingerprint,
            'attempt_count': attempts,
            'failed_at': stamp,
            'retry_not_before': stamp if parent_retry else stamp + delay,
            'quarantined': permanent or (unavailable and not parent_retry) or attempts >= 6,
            'typed': bool(outcome),
            'retryable': not permanent,
            # Why the host refused a completed turn (observed 2026-11-07: a
            # valid #116841 decision quarantined with no recorded cause).
            **({'reason': reason} if reason else {}),
            **({k: outcome[k] for k in ('status','code','terminal_reason','pool_size','attempted',
                                        'requested_model','effective_model','message') if k in outcome}),
            'use_parent_model': parent_retry,
            'main_fallback_eligible': False,
            'generation': generation,
        }
        row.pop('processing', None)
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)


def fallback(state_dir, session_id, receipt_id, generation, state, reason='', *, now=None):
    """Generation-fenced exact-once main-session fallback transition."""
    stamp = time.time() if now is None else now
    if state not in ('prepare', 'queued', 'failed'):
        raise WorkPlanError('invalid side fallback state')
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if row is None:
            raise WorkPlanError('side receipt missing')
        current = row.get('main_fallback') or {}
        if state == 'prepare':
            if current.get('state') in ('pending', 'queued'):
                return inbox._receipt(receipt_id, row, duplicate=True)
            failure = row.get('side_failure') or {}
            if not failure.get('main_fallback_eligible') or failure.get('generation') != generation:
                verify_claim(inbox, receipt_id, generation, now=stamp)
            row['main_fallback'] = {'state': 'pending', 'reason': str(reason)[:160],
                                    'prepared_at': stamp, 'generation': generation,
                                    'delivery_id': uuid.uuid4().hex}
            row['processing'] = {'owner':'main-fallback','generation':generation,
                                 'started_at':stamp,'lease_until':stamp + 3600}
        else:
            if current.get('generation') != generation or current.get('state') != 'pending':
                raise WorkPlanError('stale side fallback')
            current['state'] = state
            current['updated_at'] = stamp
            row['main_fallback'] = current
            if state == 'queued':
                row.update(stage='processed', item_id=None, consumed_at=stamp,
                           result={'action':'main_fallback','summary':'Side model unavailable; delivered to main session',
                                   'reply':'','delivery':'main_session','handoff':'main_fallback',
                                   'fallback_reason':current.get('reason','')})
                inbox._save_file(inbox._event_path('receipts', receipt_id), row)
                inbox._event_path('pending', receipt_id).unlink(missing_ok=True)
                inbox._clear_priority(receipt_id)
                return inbox._receipt(receipt_id, row, duplicate=False)
            row.pop('main_fallback', None)
            row.pop('processing', None)
            row['side_failure'] = {'category':'main_fallback_failed','attempt_count':1,
                                   'failed_at':stamp,'retry_not_before':stamp + 5,'quarantined':False}
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)


def fallback_pending(state_dir, session_id, limit=10):
    """Recover prepared fallback intents without reclaiming their side event."""
    inbox = SideIntakeInbox(state_dir, session_id)
    rows = []
    for path in (inbox.directory / 'pending').glob('*/*.json'):
        row = inbox._read_file(path)
        fallback_state = (row or {}).get('main_fallback') or {}
        if row and fallback_state.get('state') == 'pending':
            rows.append({'receipt_id': path.stem, 'generation': fallback_state.get('generation'),
                         'reason': fallback_state.get('reason', ''), 'text': row['text'],
                         'route': row.get('route', {}), 'duplicate': True})
        if len(rows) >= limit:
            break
    return rows


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
