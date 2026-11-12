"""Durable, fenced main-wake delivery state for side handoffs."""
from __future__ import annotations

import hashlib
import time
import uuid

from . import work_plan_tree as tree

_CHANNELS = ('prompt', 'append', 'stop', 'legacy')
_OUTCOMES = ('queued', 'appended', 'uncertain', 'failed')
# A fresh intent belongs to its owner for this long. Another owner may
# supersede it only after expiry, so two live instances cannot alternately
# replace each other's intent forever; a crashed owner's intent recovers.
INTENT_LEASE_SECONDS = 5.0
# The only wake fields any model, status, REST or browser projection may see.
_PUBLIC_WAKE = ('state', 'phase', 'channel', 'revision', 'needs_ack_retry', 'requested_at',
                'intent_at', 'intent_until', 'started_at', 'updated_at', 'acknowledged_at')


def _row(doc, event_id):
    row = doc.get('side_handoffs', {}).get(event_id)
    if not row or row.get('state') != 'requested':
        raise tree.WorkPlanError('requested side handoff required')
    wake = row.setdefault('wake', {'state': 'pending', 'requested_at': time.time(), 'revision': 0})
    wake.setdefault('revision', 0)
    return wake


def _bump(wake):
    wake['revision'] = int(wake.get('revision') or 0) + 1
    return wake


def _result(event_id, wake, *, state=None, attempt=False):
    # protocol:2 is echoed on every result so a new host can tell this router
    # from an older one (side_wake 4b85569d) that ignores protocol fields.
    value = {'event_id': event_id, 'protocol': 2, 'state': state or wake.get('state'),
             'wake_state': wake.get('state'), 'phase': wake.get('phase'),
             'revision': wake.get('revision'), 'requested_at': wake.get('requested_at'),
             'channel': wake.get('channel')}
    if attempt:
        value.update(attempt_id=wake.get('attempt_id'), owner_nonce=wake.get('owner_nonce'))
    return value


def redact_wake(wake):
    """Shared projection of one nested wake for every non-host reader.

    Never includes attempt_id, owner_nonce, a marker or a receipt uuid. The
    exact attempt is named only by its one-way attempt_ref.
    """
    wake = wake if isinstance(wake, dict) else {}
    public = {key: wake[key] for key in _PUBLIC_WAKE if wake.get(key) is not None}
    reference = attempt_ref(wake.get('attempt_id'))
    if reference:
        public['attempt_ref'] = reference
    public['delivery_uncertain'] = delivery_uncertain(wake)
    return public


def redact_handoff(event_id, row):
    """A handoff row for model/status/REST/browser output, with its wake redacted."""
    value = {'event_id': event_id, **{key: item for key, item in (row or {}).items() if key != 'wake'}}
    if isinstance(row, dict) and 'wake' in row:
        value['wake'] = redact_wake(row['wake'])
    return value


def prepare(store, event_id, *, protocol=None, owner_nonce=None, channel=None, now=None):
    """Elect one wake intent; protocol 1 remains a non-supersedable legacy start."""
    modern = protocol == 2
    if modern and (not isinstance(owner_nonce, str) or not owner_nonce or channel not in _CHANNELS[:-1]):
        raise tree.WorkPlanError('protocol 2 wake owner and channel required')

    def mutate(doc):
        stamp = time.time() if now is None else now
        wake = _row(doc, event_id)
        current, phase = wake.get('state'), wake.get('phase')
        if current in ('pending', 'failed'):
            attempt_id = uuid.uuid4().hex
            wake.update(state='submitting', phase='intent' if modern else 'started',
                        attempt_id=attempt_id, owner_nonce=owner_nonce if modern else None,
                        channel=channel if modern else 'legacy', intent_at=stamp,
                        intent_until=stamp + INTENT_LEASE_SECONDS if modern else None)
            if not modern:
                wake['started_at'] = stamp
            _bump(wake)
            return _result(event_id, wake, attempt=True)
        if modern and current == 'submitting' and phase == 'intent':
            if wake.get('owner_nonce') == owner_nonce:
                return _result(event_id, wake, attempt=True)
            if stamp < float(wake.get('intent_until') or 0):
                # Another owner's intent is still within its lease.
                return _result(event_id, wake, state='already_intent')
            # An expired intent never authorized an external effect. A module
            # reload or another process may supersede it; the old start is fenced.
            wake.update(attempt_id=uuid.uuid4().hex, owner_nonce=owner_nonce, channel=channel,
                        intent_at=stamp, intent_until=stamp + INTENT_LEASE_SECONDS)
            _bump(wake)
            return _result(event_id, wake, attempt=True)
        label = f"already_{current}"
        return _result(event_id, wake, state=label)

    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}


def start(store, event_id, attempt_id, owner_nonce, channel):
    """Fence intent -> started before any external delivery side effect.

    The intent owner may start at any time while it still owns the row, even
    after its lease expired, as long as nobody superseded it.
    """
    if channel not in _CHANNELS[:-1]:
        raise tree.WorkPlanError('invalid side wake channel')

    def mutate(doc):
        wake = _row(doc, event_id)
        if (wake.get('state') == 'submitting' and wake.get('phase') == 'started'
                and wake.get('attempt_id') == attempt_id and wake.get('owner_nonce') == owner_nonce
                and wake.get('channel') == channel):
            return _result(event_id, wake, attempt=True)
        if (wake.get('state') != 'submitting' or wake.get('phase') != 'intent'
                or not attempt_id or wake.get('attempt_id') != attempt_id
                or not owner_nonce or wake.get('owner_nonce') != owner_nonce
                or wake.get('channel') != channel):
            raise tree.WorkPlanError('stale side wake intent')
        wake.update(phase='started', started_at=time.time())
        _bump(wake)
        return _result(event_id, wake, attempt=True)

    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}


def record(store, event_id, state, attempt_id, *, owner_nonce=None, receipt=None):
    """Record one definite/uncertain outcome; an identical retry is idempotent."""
    if state not in _OUTCOMES:
        raise tree.WorkPlanError('invalid side wake state')
    if receipt is not None and (not isinstance(receipt, str) or not receipt or len(receipt) > 300):
        raise tree.WorkPlanError('invalid side wake receipt')

    def mutate(doc):
        wake = _row(doc, event_id)
        same_attempt = bool(attempt_id and wake.get('attempt_id') == attempt_id)
        same_owner = wake.get('channel') == 'legacy' or bool(owner_nonce and wake.get('owner_nonce') == owner_nonce)
        if same_attempt and same_owner and wake.get('state') == state:
            if receipt is not None and wake.get('receipt') not in (None, receipt):
                raise tree.WorkPlanError('side wake outcome already recorded differently')
            if receipt is not None and wake.get('receipt') is None:
                # Settled appended without a receipt; the owner's exact uuid is
                # kept (wake-only write) so later evidence checks can use it.
                wake.update(receipt=receipt, updated_at=time.time())
                _bump(wake)
            return _result(event_id, wake)
        if (wake.get('state') != 'submitting' or wake.get('phase') != 'started'
                or not same_attempt or not same_owner):
            raise tree.WorkPlanError('stale side wake attempt')
        wake.update(state=state, updated_at=time.time())
        if receipt is not None:
            wake['receipt'] = receipt
        _bump(wake)
        return _result(event_id, wake)

    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}


def reconcile(store, event_id, attempt_id, *, receipt, seen_receipt):
    """Settle a started append from the exact receipt uuid its live owner saw.

    Transcript text is never evidence (it can be spoofed by any row). Only an
    append whose own call returned ``receipt`` (kept by that owner) and whose
    message with that exact uuid is present settles. Prompt attempts are never
    reconciled: a prompt result that was not resolved stays uncertain until a
    person retries or main acknowledges.
    """
    def mutate(doc):
        wake = _row(doc, event_id)
        if (wake.get('state') != 'submitting' or wake.get('phase') != 'started'
                or not attempt_id or wake.get('attempt_id') != attempt_id):
            raise tree.WorkPlanError('stale side wake attempt')
        if wake.get('channel') != 'append':
            raise tree.WorkPlanError('only an append receipt can reconcile a side wake')
        if not isinstance(receipt, str) or not receipt or len(receipt) > 300 or seen_receipt != receipt:
            raise tree.WorkPlanError('exact append receipt required')
        wake.update(state='appended', receipt=receipt, reconciled='receipt', updated_at=time.time())
        _bump(wake)
        return _result(event_id, wake)
    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}


def attempt_ref(attempt_id):
    """Non-secret reference to one attempt for status/UI; never the attempt id."""
    if not isinstance(attempt_id, str) or not attempt_id:
        return None
    return hashlib.sha256(attempt_id.encode()).hexdigest()[:16]


def delivery_uncertain(wake):
    """Started-without-outcome and uncertain rows need visible operator attention."""
    wake = wake or {}
    return wake.get('state') == 'uncertain' or (wake.get('state') == 'submitting' and wake.get('phase') == 'started')


def retry(store, event_id, reference, revision, *, confirmed=False):
    """Explicitly release an uncertain attempt for a possibly duplicate retry.

    ``reference`` is the attempt_ref shown in status/UI. The audit row keeps
    the exact internal attempt id.
    """
    if confirmed is not True:
        raise tree.WorkPlanError('wake retry requires explicit duplicate-delivery confirmation')

    def mutate(doc):
        wake = _row(doc, event_id)
        if (not delivery_uncertain(wake) or not reference or attempt_ref(wake.get('attempt_id')) != reference
                or wake.get('revision') != revision):
            raise tree.WorkPlanError('side wake changed; refresh before retry')
        wake.setdefault('retries', []).append({'attempt_id': wake.get('attempt_id'), 'attempt_ref': reference,
                                               'revision': revision, 'previous_state': wake.get('state'),
                                               'previous_phase': wake.get('phase'),
                                               'duplicate_confirmed': True, 'at': time.time()})
        wake.update(state='failed', phase='retry', updated_at=time.time())
        _bump(wake)
        return _result(event_id, wake)

    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}


def mark_ack_retry(store, event_id):
    """Wake-only marker used after a real first-ACK CAS conflict."""
    def mutate(doc):
        wake = _row(doc, event_id)
        wake['needs_ack_retry'] = True
        _bump(wake)
        return _result(event_id, wake)
    doc, result = store.transaction(mutate, semantic=False)
    return {**result, 'version': doc['version']}
