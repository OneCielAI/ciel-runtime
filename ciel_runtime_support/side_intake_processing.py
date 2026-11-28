"""Fenced per-event AI processing; receipt commits precede pending removal."""
import hashlib
import json
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox, source_identity, source_priority, unaddressed_reply
from .work_plan_tree import WorkPlanError

# Events that represent the initial delivery of a new source message and share
# one per-message deduplication marker (Prody 2026-11-09 #119649:
# message.created + task.auto_assigned). Distinct follow-up events on the same
# message (message.renotify, thread.eta_requested, task.status_changed,
# message.edited, message.approved — observed dropped on Andy 2026-11-26
# #161594/#161501/#161412) carry their own event_key and must not be settled as
# duplicate_source.
INITIAL_SOURCE_EVENTS = frozenset({'', 'message.created', 'task.auto_assigned'})


def _source_event_type(text):
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return ''
    data = body.get('data') if isinstance(body, dict) else None
    kind = data.get('event_type') if isinstance(data, dict) else None
    return kind.strip().lower() if isinstance(kind, str) else ''


def _source_holder(inbox, key, row, stamp, *, by_event=False):
    """Bind one initial source message to one side actor; settle duplicate initial delivery.

    Returns 'claim' when this event may start an actor, 'wait' while the first
    event for the same source is still being handled, or 'settled' when this
    event was recorded as a duplicate of an already handled source.
    """
    if by_event:
        # Approval workflow (technical review P1): an approval or rejection is
        # its own event, identified by its event id, never a duplicate of an
        # earlier event about the same message (the request message, or a
        # second approver's decision).
        from .work_workflow_approval import event_kind, parse_event
        if event_kind(parse_event(row.get('text'))):
            return 'claim'
    if _source_event_type(row.get('text')) not in INITIAL_SOURCE_EVENTS:
        return 'claim'
    source = source_identity(row['text'])
    if not source:
        return 'claim'
    marker = inbox.directory / 'sources' / (hashlib.sha256(source.encode()).hexdigest() + '.json')
    with exclusive_file_lock(marker):
        holder = (inbox._read_file(marker, 4096) or {}).get('receipt_id')
        if holder and holder != key:
            other = inbox._load(holder)
            if other and other.get('stage') == 'processed':
                row.update(stage='processed', item_id=None, consumed_at=stamp, result={
                    'action': 'answer', 'kind': 'status', 'item_id': None, 'summary':
                    'Same source message already handled by side receipt ' + holder,
                    'reply': '', 'delivery': 'duplicate_source', 'duplicate_of': holder,
                    'execution_approved': False, 'handoff': 'not_required'})
                row.pop('processing', None)
                inbox._save_file(inbox._event_path('receipts', key), row)
                inbox._event_path('pending', key).unlink(missing_ok=True)
                inbox._clear_priority(key)
                return 'settled'
            if other and not (other.get('side_failure') or {}).get('quarantined'):
                return 'wait'
        inbox._save_file(marker, {'receipt_id': key, 'source': source, 'bound_at': stamp})
    return 'claim'


def _canonical_fingerprint(model_fingerprint):
    if isinstance(model_fingerprint, (tuple, list, set, frozenset)):
        return next(iter(model_fingerprint), '')
    return model_fingerprint


def _fingerprint_matches(stored, current):
    if not stored:
        return True
    if isinstance(current, (tuple, list, set, frozenset)):
        return stored in current
    return stored == current


def claim(state_dir, session_id, owner, *, now=None, lease_seconds=120, aging_seconds=60,
          model_fingerprint=None, urgent_only=False, workflow_events=False):
    if not isinstance(owner, str) or not owner or len(owner) > 200:
        raise WorkPlanError('side claim owner required')
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise WorkPlanError('invalid side lease duration')
    if type(aging_seconds) is not int or not 1 <= aging_seconds <= 3600:
        raise WorkPlanError('invalid side aging duration')
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    cursor_path = inbox.directory / 'decision-cursor.json'
    # Never-claimable rows do not occupy the rotating window (see
    # SideIntakeInbox.mark_quarantined); a fresh event is seen on this call.
    skip = inbox.quarantined_keys()
    with exclusive_file_lock(cursor_path):
        cursor = inbox._read_file(cursor_path) or {}
        rows = inbox.pending(100, after=str(cursor.get('after') or ''), skip=skip)
        if rows:
            inbox._save_file(cursor_path, {'after': rows[-1]['receipt_id'] if len(rows) == 100 else ''})
            for row in rows:
                inbox._index_priority(row['receipt_id'], row)
    # Source priority is Work decision metadata, not a Side admission rule.
    # The rotating window bounds I/O; choose by arrival within that window.
    ordered = sorted(rows, key=lambda row: (row.get('received_at', 0), row['receipt_id']))
    fresh = [row for row in ordered if not row.get('side_failure')]
    retrying = [row for row in ordered if row.get('side_failure')]
    lane_path = inbox.directory / 'claim-lane.json'
    retry_first = (inbox._read_file(lane_path) or {}).get('next') == 'retry'
    for row in ([*retrying, *fresh] if retry_first else [*fresh, *retrying]):
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
                    'retryable': True, 'use_parent_model': True,
                    'model_fingerprint': _canonical_fingerprint(model_fingerprint),
                }
                inbox._save_file(inbox._event_path('pending', key), current)
            failure = current.get('side_failure') or {}
            if failure.get('quarantined') and failure.get('category') == 'invalid_answer':
                # A completed turn may already have replied externally; a model
                # change must not replay it either. Rows quarantined by an older
                # build are indexed here the first time a window opens them.
                inbox.mark_quarantined(key)
                continue
            if failure.get('quarantined'):
                # A quarantine is terminal whatever its model fingerprint. Native
                # side forks use one constant fingerprint, so lifting a quarantine
                # on a fingerprint change would fork and reply to every legacy
                # row days later (34 such rows on one workspace, 2026-11-11), some
                # of them already answered once. Only an explicit operator retry
                # may release one.
                continue
            if not failure.get('quarantined') and failure.get('retry_not_before', 0) > stamp:
                continue
            if inbox.is_inbound_receipt_ack(current):
                # Rows an older build received are settled here, before any fork.
                inbox.settle_receipt_ack(key, current, stamp)
                continue
            if unaddressed_reply(current.get('text')):
                # An ACK an older build still owed this reply is dropped too.
                inbox.ack_marker(key).unlink(missing_ok=True)
            if _source_holder(inbox, key, current, stamp, by_event=workflow_events) != 'claim':
                continue
            # The same host reclaiming an event whose lease lapsed under it (its
            # pump stalled while the actor kept working) gets its own claim back,
            # so it can see that its actor already holds the event (Andy
            # 2026-11-23: a new generation started a second actor beside it).
            held = current.get('processing') or {}
            previous = held if held.get('owner') == owner and held.get('generation') else {}
            generation = previous.get('generation') or uuid.uuid4().hex
            current['processing'] = {'owner': owner, 'generation': generation,
                                     'lease_until': stamp + lease_seconds,
                                     'started_at': previous.get('started_at', stamp)}
            inbox._save_file(inbox._event_path('pending', key), current)
            inbox._save_file(lane_path, {'next': 'fresh' if current.get('side_failure') else 'retry'})
            saved = current.get('proposal')
            return {**({'proposal': {name: saved.get(name) for name in ('understanding', 'decision', 'expected_version')}}
                       # A proposal saved before a restart is committed as is: the
                       # host resumes it without running the model again.
                       if isinstance(saved, dict) else {}),
                    'receipt_id': key, 'text': current['text'], 'event_key': current['event_key'],
                    'generation': generation, 'lease_until': stamp + lease_seconds,
                    'source_priority': source_priority(current['text']),
                    'use_parent_model': bool(failure.get('use_parent_model') and (
                        failure.get('category') == 'billing_error'
                        or _fingerprint_matches(failure.get('model_fingerprint'), model_fingerprint))),
                    'route': {k: v for k, v in current.get('route', {}).items() if k not in ('reply_token', 'authorization', 'token')}}
    return None


DISCARD_REASON = 'discarded by operator'


def supersede_unclaimed(state_dir, session_id, keys, *, now=None, reason='superseded by a newer gate re-check'):
    """Close host events nobody claimed, so the next round can replace them.

    A gate re-check carries a snapshot of the gates; one still unclaimed when
    the next interval comes is stale, and holding the new round for it stopped
    the re-check altogether (Andy 2026-11-19: 12 events unclaimed for 9.6 h).
    A row with a live lease is being worked on and stays. Nothing is deleted:
    the row is quarantined like an operator discard and kept for audit.
    Returns the keys that are still open (leased), which the caller waits for.
    """
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    still_open = []
    for key in keys:
        with exclusive_file_lock(inbox._event_path('locks', key)):
            row = inbox._load(key)
            if row is None or row.get('stage') != 'received':
                continue
            failure = row.get('side_failure') or {}
            if failure.get('quarantined'):
                continue
            if float((row.get('processing') or {}).get('lease_until') or 0) > stamp:
                still_open.append(key)
                continue
            row['side_failure'] = {**failure, 'category': failure.get('category') or 'superseded', 'quarantined': True,
                                   'retryable': False, 'superseded': True, 'superseded_at': stamp, 'reason': reason}
            row.pop('processing', None)
            inbox._save_file(inbox._event_path('pending', key), row)
        inbox.mark_quarantined(key)
        inbox._clear_priority(key)
    return still_open


def discard_failed(state_dir, session_id, *, category=None, now=None):
    """Discard every failed side receipt (user request 2026-11-18).

    A failed receipt is still pending (stage ``received``) with a
    ``side_failure`` that is not terminal. Each one is quarantined under the
    same per-receipt lock ``fail()`` takes: ``quarantined``/``operator_discarded``
    set, ``retryable`` cleared, the reason appended, and the quarantine index
    updated so no later claim picks it. Nothing is forwarded to main
    (side_forward is not called) and nothing is deleted: the receipt stays for
    audit. A receipt with a live lease is skipped. ``category`` limits it to one
    failure category (for example ``source_context``).
    """
    if category is not None and (not isinstance(category, str) or not 0 < len(category) <= 80):
        raise WorkPlanError('side discard category must be a failure category name')
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    counts = {'discarded': 0, 'skipped_leased': 0, 'already_terminal': 0, 'other_category': 0}
    discarded = []
    for path in sorted((inbox.directory / 'pending').glob('*/*.json')):
        key = path.stem
        try:
            lock = inbox._event_path('locks', key)
        except WorkPlanError:
            continue
        with exclusive_file_lock(lock):
            row = inbox._load(key)
            if row is None or row.get('stage') != 'received':
                continue
            failure = row.get('side_failure')
            if not isinstance(failure, dict) or not failure:
                continue
            if failure.get('quarantined'):
                counts['already_terminal'] += 1
                continue
            if category is not None and failure.get('category') != category:
                counts['other_category'] += 1
                continue
            if float((row.get('processing') or {}).get('lease_until') or 0) > stamp:
                counts['skipped_leased'] += 1
                continue
            reason = str(failure.get('reason') or '')
            row['side_failure'] = {**failure, 'quarantined': True, 'retryable': False, 'operator_discarded': True,
                                   'operator_discarded_at': stamp,
                                   'reason': (reason + ' · ' if reason else '') + DISCARD_REASON}
            row.pop('processing', None)
            inbox._save_file(inbox._event_path('pending', key), row)
        inbox.mark_quarantined(key)
        counts['discarded'] += 1
        discarded.append(key)
    return {**counts, 'category': category, 'receipt_ids': discarded[:200], 'forwarded_to_main': 0}


def discard_failed_workspace(state_dir, *, category=None, now=None):
    """discard_failed for every session side inbox of one workspace state dir.

    Each inbox directory is named by the sha256 of its session identity; the
    identity is taken from a row it holds and checked against that name.
    """
    import hashlib
    import json as _json
    totals = {'discarded': 0, 'skipped_leased': 0, 'already_terminal': 0, 'other_category': 0, 'sessions': 0}
    root = Path(state_dir) / 'side-inboxes'
    for directory in sorted(path for path in root.glob('*') if path.is_dir()) if root.is_dir() else []:
        identity = None
        for path in [*directory.glob('pending/*/*.json'), *directory.glob('receipts/*/*.json')][:50]:
            try:
                value = _json.loads(path.read_text(encoding='utf-8')).get('session_id')
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(value, str) and hashlib.sha256(value.encode()).hexdigest() == directory.name:
                identity = value
                break
        if identity is None:
            continue
        result = discard_failed(state_dir, identity, category=category, now=now)
        totals['sessions'] += 1
        for key in ('discarded', 'skipped_leased', 'already_terminal', 'other_category'):
            totals[key] += result[key]
    return {**totals, 'category': category, 'forwarded_to_main': 0}


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


def renew(state_dir, session_id, receipt_id, generation, *, now=None, lease_seconds=120, source_bounded=None):
    """Extend a claim its actor still holds. ``source_bounded`` ({"omitted_replies": n}) also
    records on the receipt that its fork got a bounded source thread, so the
    side receipt and the SSE pane show it (KOBE hotfix 2026-11-18).

    A lapsed lease is revived when no other claim took the event: the
    generation is still this actor's, the event is still received and not
    quarantined. Andy 2026-11-23: the host's pump did not run for 5-7 minutes,
    the 120 s lease lapsed under working actors, and the next pump stopped them
    and claimed the same events again (336 events ran on two actors). A claim
    after the lapse writes a new generation, so the older actor is refused."""
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise WorkPlanError('invalid side lease duration')
    if source_bounded is not None:
        omitted = source_bounded.get('omitted_replies') if isinstance(source_bounded, dict) else None
        if type(omitted) is not int or omitted < 1:
            raise WorkPlanError('source_bounded needs a positive omitted_replies')
    stamp = time.time() if now is None else now
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if row is None:
            raise WorkPlanError('side receipt missing')
        if (row.get('processing') or {}).get('generation') != generation:
            raise WorkPlanError('stale side claim')
        if row.get('stage') != 'received':
            raise WorkPlanError('side claim already settled')
        if (row.get('side_failure') or {}).get('quarantined'):
            raise WorkPlanError('side claim quarantined')
        until = stamp + lease_seconds
        if source_bounded is not None:
            # A record, not a heartbeat: never shortens the claim's lease.
            until = max(until, float(row['processing'].get('lease_until') or 0))
            row['source_bounded'] = {'omitted_replies': source_bounded['omitted_replies'], 'at': stamp}
        row['processing']['lease_until'] = until
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return {'receipt_id': receipt_id, 'generation': generation, 'lease_until': until}


MAIN_COMPACTION_WAITS = 12


def fail(state_dir, session_id, receipt_id, generation, failure, *, now=None):
    """Release an exact failed claim with durable backoff or quarantine."""
    stamp = time.time() if now is None else now
    if not isinstance(failure, dict):
        raise WorkPlanError('side failure object required')
    category = str(failure.get('category') or 'error')[:80]
    model = str(failure.get('model') or '')[:300]
    reason = str(failure.get('reason') or '')[:300]
    fingerprint = str(failure.get('model_fingerprint') or '')[:128]
    load_outcome = failure.get('load_outcome')
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if row is None:
            raise WorkPlanError('side receipt missing')
        held = row.get('processing') or {}
        prior = row.get('side_failure') or {}
        if not held and generation and prior.get('generation') == generation:
            return inbox._receipt(receipt_id, row, duplicate=True)
        if not generation or held.get('generation') != generation or row.get('stage') != 'received':
            raise WorkPlanError('stale side claim')
        # Like renew, a terminal record may settle a lapsed claim only if no
        # other claimant took over. Reconcile before consuming any API evidence.
        outcome = load_outcome(held.get('started_at', stamp)) if callable(load_outcome) else failure.get('outcome')
        return _record_failure(inbox, receipt_id, generation, row, prior, failure,
                               category, model, reason, fingerprint, outcome, stamp)


def _record_failure(inbox, receipt_id, generation, row, prior, failure,
                    category, model, reason, fingerprint, outcome, stamp):
    outcome = outcome if isinstance(outcome, dict) else {}
    billing = category == 'billing_error' or (category == 'turn_error' and (
        type(outcome.get('status')) is int and outcome['status'] == 402
        or outcome.get('code') == 'billing_error'))
    if billing:
        category = 'billing_error'
        # Do not forward or store raw billing error bodies.
        outcome = {**({'status': outcome['status']} if type(outcome.get('status')) is int else {}),
                   'code': 'billing_error', 'effective_model': outcome.get('effective_model')}
        reason = ''
    terminal = str(outcome.get('terminal_reason') or '')[:200]
    code = str(outcome.get('code') or '').lower()
    unavailable = (category in ('model_not_found', 'invalid_model')
                   or code in ('model_not_found', 'invalid_model')
                   or 'no_executable_targets' in terminal)
    # A completed model turn may already have sent an external MCP reply. If its
    # final decision is invalid, retrying can duplicate that side effect.
    permanent = category in ('invalid_request', 'invalid_answer')
    # The source thread did not fit in the context main has left, and the host
    # compacts main first (2026-11-15). Waiting for that compaction is not a
    # failed attempt: it never counts toward quarantine and is retried after the
    # host's delay, up to MAIN_COMPACTION_WAITS times; after that it is an
    # ordinary source_context failure.
    retry_after = failure.get('retry_after_seconds')
    retry_after = retry_after if type(retry_after) is int and 5 <= retry_after <= 600 else 30
    waits = int(prior.get('compaction_waits') or 0)
    compaction = category == 'main_compaction_pending' and waits < MAIN_COMPACTION_WAITS
    if category == 'main_compaction_pending' and not compaction:
        category = 'source_context'
    attempts = int(prior.get('attempt_count') or 0) + (0 if compaction else 1)
    delay = retry_after if compaction else min(300, 5 * (2 ** min(attempts - 1, 6)))
    on_parent = bool(prior.get('use_parent_model'))
    # The side model failed the request (an API error ended the side fork's
    # turn, or the model is unavailable): the next attempt runs at once on
    # main's own model, and stays on it (user requirement 2026-11-16, "모델
    # 문제로 실패하면 메인과 동일한 모델로 재처리"). Walkie: 154 failures in 3
    # days, 0 retried on main's model — the switch was never wired after the
    # side model moved to the ciel-work-gate turn.step. With no side model
    # configured the fork already runs on main's model; nothing switches.
    side_model = failure.get('side_model') is True
    parent_retry = not on_parent and (unavailable or category == 'turn_error' and side_model)
    if billing:
        # A previous switch already spent the alternative. Once a billing
        # failure triggered a switch, even an untyped second failure forwards.
        parent_retry = (not on_parent and attempts < 6
                        and failure.get('distinct_parent_model', lambda _outcome: False)(outcome))
    hopeless = (unavailable and on_parent or billing and not parent_retry
                or on_parent and prior.get('category') == 'billing_error')
    quarantined = permanent or hopeless or attempts >= 6
    row['side_failure'] = {
        'category': category,
        'model': model,
        'model_fingerprint': fingerprint,
        'attempt_count': attempts,
        'failed_at': stamp,
        'retry_not_before': stamp if parent_retry else stamp + delay,
        'quarantined': quarantined,
        'typed': bool(outcome),
        'retryable': not permanent and not (hopeless and (billing or prior.get('category') == 'billing_error')),
        # Why the host refused a completed turn (observed 2026-11-07: a
        # valid #116841 decision quarantined with no recorded cause).
        **({'reason': reason} if reason else {}),
        **({k: outcome[k] for k in ('status','code','terminal_reason','pool_size','attempted',
                                    'requested_model','effective_model','message') if k in outcome}),
        # Sticky: once on main's model, later retries of this event stay there.
        'use_parent_model': parent_retry or on_parent,
        **({'parent_model_since': stamp} if parent_retry else
           {'parent_model_since': prior['parent_model_since']} if on_parent and prior.get('parent_model_since') else {}),
        'main_fallback_eligible': False,
        'generation': generation,
        'compaction_waits': waits + (1 if compaction else 0),
    }
    row.pop('processing', None)
    if row['side_failure']['quarantined']:
        # Terminal: the original goes to main once instead of being
        # dropped (user requirement 2026-11-16, side_forward.py).
        from .side_forward import mark
        mark(inbox, receipt_id, row)
    inbox._save_file(inbox._event_path('pending', receipt_id), row)
    if row['side_failure']['quarantined'] and category == 'invalid_answer':
        inbox.mark_quarantined(receipt_id)
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


def release_for_recheck(state_dir, session_id, receipt_id, generation, reason):
    """Return an exact claim to pending once, without counting a failure.

    Used when the board changed under a create: the event is decided again
    against the current board. A second change commits rather than looping.
    The saved proposal goes, so the next actor's decision is accepted; the
    understanding already sent stays recorded and is not sent again.
    """
    inbox = SideIntakeInbox(state_dir, session_id)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = verify_claim(inbox, receipt_id, generation)
        row.pop('processing', None)
        row.pop('proposal', None)
        row['rechecks'] = int(row.get('rechecks') or 0) + 1
        row['recheck_reason'] = str(reason)[:200]
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)


def finish(state_dir, session_id, receipt_id, generation, result, *, main_notice=None, now=None, config=None):
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
        if (result.get('action') == 'create' and result.get('item_id')
                and row.get('route', {}).get('source_kind') == 'external_event'):
            from .side_reply_connector import source_status_plan
            plan = source_status_plan(config or {}, row['route'], row.get('text', ''),
                                      receipt_id, result['item_id'])
            if plan:
                row['source_status'] = {'plan': plan, 'phase': 'ack', 'state': 'pending'}
        if main_notice:
            from .side_notice import stage_pending
            stage_pending(inbox, receipt_id, row, main_notice)
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        inbox._event_path('pending', receipt_id).unlink(missing_ok=True)
        inbox._clear_priority(receipt_id)
        return inbox._receipt(receipt_id, row, duplicate=False)
