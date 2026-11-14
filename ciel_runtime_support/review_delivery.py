"""review_delivery: where a parallel-review payload was appended, and whether read.

The finalization journal (``work-parallel-finalization.json``, schema 5) is
frozen: it records only the review wake attempt and its outcome. It cannot tell
a review read in this conversation from one appended only to a conversation
that /clear or --continue left behind. This separate versioned sidecar records
that, per review attempt, beside the journal. Schema-5 readers never open it;
it only reads the journal to check the current attempt and decision.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path

from .channel_message_repository import exclusive_file_lock
from .work_plan_tree import WorkPlanError, WorkStateUnavailable

PRODUCER = 'ciel-runtime/review_delivery'
SCHEMA = 1
FILE_NAME = 'work-parallel-review-delivery.json'
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 500
_REAPPEND_OUTCOMES = ('appended', 'failed', 'uncertain')
# Same grace and audit bound as side_wake's conversation re-append.
REAPPEND_UNCERTAIN_SECONDS = 60.0
REAPPEND_RETRIES_MAX = 16


def reappend_uncertain(row, now=None):
    """A re-append nobody can know the outcome of (recorded uncertain, or no
    record from its holder within REAPPEND_UNCERTAIN_SECONDS)."""
    lease = (row or {}).get('reappend') if isinstance(row, dict) else None
    if not isinstance(lease, dict) or row.get('read_at'):
        return False
    if lease.get('state') == 'uncertain':
        return True
    started = lease.get('started_at')
    return (lease.get('state') == 'submitting' and isinstance(started, (int, float))
            and (time.time() if now is None else now) - started >= REAPPEND_UNCERTAIN_SECONDS)


def attempt_ref(attempt_id):
    return hashlib.sha256(str(attempt_id).encode()).hexdigest()[:16] if attempt_id else None


def _text(value, name, limit=300):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise WorkPlanError('review delivery ' + name + ' required')
    return value


def _empty():
    return {'producer': PRODUCER, 'schema': SCHEMA, 'version': 0, 'deliveries': {}}


class ReviewDeliveryStore:
    def __init__(self, path, finalization):
        self.path = Path(path)
        self.finalization = finalization

    @classmethod
    def beside(cls, finalization):
        return cls(Path(finalization.path).with_name(FILE_NAME), finalization)

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return _empty()
        # Stored state this build cannot read: WorkStateUnavailable (a
        # WorkPlanError), so operator surfaces answer 503 "Work state unreadable".
        if len(raw) > MAX_BYTES:
            raise WorkStateUnavailable('review delivery sidecar exceeds its limit')
        try:
            doc = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkStateUnavailable('review delivery sidecar is unreadable; left unchanged') from error
        # Another producer or a newer schema is never written over.
        if (not isinstance(doc, dict) or doc.get('producer') != PRODUCER or doc.get('schema') != SCHEMA
                or not isinstance(doc.get('deliveries'), dict) or type(doc.get('version')) is not int):
            raise WorkStateUnavailable('review delivery sidecar has another producer or schema; left unchanged')
        return doc

    def _change(self, fn):
        from .durable_json_file import save
        with exclusive_file_lock(self.path):
            doc = self._read()
            before = json.dumps(doc, sort_keys=True)
            result = fn(doc)
            if json.dumps(doc, sort_keys=True) != before:
                doc['version'] += 1
                rows = doc['deliveries']
                for key in sorted(rows, key=lambda k: rows[k].get('appended_at') or 0)[:max(0, len(rows) - MAX_ROWS)]:
                    rows.pop(key)
                save(self.path, doc)
            return result

    def _review(self, lease_id):
        """The journal's current undecided review attempt, read only."""
        row = (self.finalization.read().get('reviews') or {}).get(lease_id)
        if row is None:
            return None, None
        wake = row.get('wake') or {}
        return row, attempt_ref(wake.get('attempt_id'))

    def _current(self, doc, lease_id):
        review, ref = self._review(lease_id)
        if review is None or review.get('decision') is not None:
            return review, None
        row = doc['deliveries'].get(lease_id)
        return review, (row if isinstance(row, dict) and row.get('attempt_ref') == ref and ref else None)

    def appended(self, lease_id, attempt_id, conversation_id, receipt=None):
        _text(lease_id, "lease", 200)
        _text(attempt_id, "attempt", 200)
        _text(conversation_id, "conversation")

        def apply(doc):
            review, ref = self._review(lease_id)
            if review is None or review.get('decision') is not None:
                raise WorkPlanError('only an undecided review is delivered')
            if ref != attempt_ref(attempt_id):
                raise WorkPlanError('stale review wake attempt')
            # Only a journal attempt recorded appended put the payload in a conversation.
            if (review.get('wake') or {}).get('state') != 'appended':
                raise WorkPlanError('review wake attempt is not appended')
            row = doc['deliveries'].get(lease_id)
            if isinstance(row, dict) and row.get('attempt_ref') == ref:
                return {'lease_id': lease_id, 'state': 'appended'}
            doc['deliveries'][lease_id] = {'attempt_ref': ref, 'conversation_id': conversation_id,
                                           'appended_at': time.time(),
                                           **({'receipt': receipt[:300]} if isinstance(receipt, str) and receipt else {})}
            return {'lease_id': lease_id, 'state': 'appended'}
        return self._change(apply)

    def read(self, lease_id):
        def apply(doc):
            _, row = self._current(doc, lease_id)
            if row is None:
                return {'lease_id': lease_id, 'state': 'unknown'}
            if not row.get('read_at'):
                row['read_at'] = time.time()
                row.pop('reappend', None)
            return {'lease_id': lease_id, 'state': 'read'}
        return self._change(apply)

    def status(self, lease_id, conversation_id):
        try:
            doc = self._read()
        except WorkPlanError:
            return {'lease_id': lease_id, 'state': 'unknown'}
        review, row = self._current(doc, lease_id)
        if review is not None and review.get('decision') is not None:
            state = 'decided'
        elif row is None:
            state = 'unknown'
        elif row.get('read_at'):
            state = 'read'
        else:
            state = 'here' if row.get('conversation_id') == conversation_id else 'elsewhere'
        return {'lease_id': lease_id, 'state': state}

    def reappend_prepare(self, lease_id, owner_nonce, conversation_id):
        _text(owner_nonce, "owner", 200)
        _text(conversation_id, "conversation")

        def apply(doc):
            _, row = self._current(doc, lease_id)
            if row is None:
                return {'lease_id': lease_id, 'state': 'unknown'}
            if row.get('read_at'):
                return {'lease_id': lease_id, 'state': 'already_read'}
            if row.get('conversation_id') == conversation_id:
                return {'lease_id': lease_id, 'state': 'already_appended'}
            lease = row.get('reappend')
            # A definite failure (denied append) stored nothing: one new lease.
            if (isinstance(lease, dict) and lease.get('conversation_id') == conversation_id
                    and lease.get('state') != 'failed'):
                return {'lease_id': lease_id, 'state': 'already_' + str(lease.get('state'))}
            attempt = uuid.uuid4().hex
            row['reappend'] = {'state': 'submitting', 'attempt_id': attempt, 'owner_nonce': owner_nonce,
                               'conversation_id': conversation_id, 'started_at': time.time()}
            return {'lease_id': lease_id, 'state': 'submitting', 'attempt_id': attempt, 'owner_nonce': owner_nonce}
        return self._change(apply)

    def reappend_record(self, lease_id, attempt_id, owner_nonce, state, receipt=None):
        if state not in _REAPPEND_OUTCOMES:
            raise WorkPlanError('invalid review reappend state')

        def apply(doc):
            _, row = self._current(doc, lease_id)
            lease = (row or {}).get('reappend')
            if (not isinstance(lease, dict) or not attempt_id or lease.get('attempt_id') != attempt_id
                    or not owner_nonce or lease.get('owner_nonce') != owner_nonce):
                raise WorkPlanError('stale review reappend attempt')
            if lease.get('state') == state:
                return {'lease_id': lease_id, 'state': state}
            if lease.get('state') != 'submitting':
                raise WorkPlanError('review reappend outcome already recorded differently')
            lease.update(state=state, updated_at=time.time())
            if state == 'appended':
                row['conversation_id'] = lease['conversation_id']
                if isinstance(receipt, str) and receipt:
                    row['receipt'] = receipt[:300]
                # Public facts only (public_view): how often, and when last.
                row['reappends'] = int(row.get('reappends') or 0) + 1
                row['reappended_at'] = time.time()
                row.pop('reappend', None)
            return {'lease_id': lease_id, 'state': state}
        return self._change(apply)

    def reappend_retry(self, lease_id, reference, *, confirmed=False):
        """A person releases an unknown-outcome re-append for one possibly duplicate re-append."""
        if confirmed is not True:
            raise WorkPlanError('review re-append retry requires explicit duplicate-delivery confirmation')

        def apply(doc):
            _, row = self._current(doc, lease_id)
            now = time.time()
            lease = (row or {}).get('reappend')
            if (row is None or not reappend_uncertain(row, now) or not reference
                    or attempt_ref(lease.get('attempt_id')) != reference):
                raise WorkPlanError('review re-append changed; refresh before retry')
            row['reappend_retries'] = list(row.get('reappend_retries') or [])[-(REAPPEND_RETRIES_MAX - 1):] + [{
                'attempt_ref': reference, 'previous_state': lease.get('state'), 'duplicate_confirmed': True,
                'at': now}]
            # A fresh attempt id fences the vanished holder's late record; a
            # released failure admits exactly one new lease (reappend_prepare).
            lease.update(state='failed', released=True, attempt_id=uuid.uuid4().hex, updated_at=now)
            lease.pop('owner_nonce', None)
            return {'lease_id': lease_id, 'state': 'released'}
        return self._change(apply)


_UNAVAILABLE = {'available': False, 'rows': [], 'truncated': False}


def public_view(store, lease_ids, identity, limit=100):
    """Read-only delivery state of the given reviews for REST and the browser.

    Bounded fixed words, times and counts only. The conversation is reported as
    one boolean against the asking session; no conversation id, attempt,
    attempt_ref, owner nonce, receipt or text leaves this function.
    """
    try:
        doc = store._read()
        reviews = store.finalization.read().get('reviews') or {}
    except (OSError, ValueError, WorkPlanError):
        return dict(_UNAVAILABLE)
    leases = [lease for lease in dict.fromkeys(lease_ids) if isinstance(lease, str) and lease][:limit + 1]
    rows = []
    for lease_id in leases[:limit]:
        review = reviews.get(lease_id) if isinstance(reviews.get(lease_id), dict) else None
        row = doc['deliveries'].get(lease_id)
        current = (review is not None and isinstance(row, dict) and row.get('attempt_ref')
                   and row.get('attempt_ref') == attempt_ref((review.get('wake') or {}).get('attempt_id')))
        if review is not None and review.get('decision') is not None:
            rows.append({'lease_id': lease_id, 'state': 'decided'})
            continue
        if not current:
            # No record for the journal's current attempt: an older host, a new
            # attempt not appended yet, or a sidecar row of a replaced attempt.
            rows.append({'lease_id': lease_id, 'state': 'unknown'})
            continue
        lease = row.get('reappend') if isinstance(row.get('reappend'), dict) else None
        reappends = row.get('reappends') if type(row.get('reappends')) is int else 0
        state = ('read' if row.get('read_at') else
                 'reappending' if lease and lease.get('state') == 'submitting' else
                 'reappended' if reappends else 'appended')
        public = {'lease_id': lease_id, 'state': state,
                  'in_this_conversation': row.get('conversation_id') == identity, 'reappends': reappends}
        for key in ('appended_at', 'reappended_at', 'read_at'):
            if isinstance(row.get(key), (int, float)):
                public[key] = float(row[key])
        if lease and lease.get('state') in ('submitting', 'uncertain', 'failed'):
            public['reappend_state'] = lease['state']
            lease_ref = attempt_ref(lease.get('attempt_id'))
            if lease_ref:
                public['reappend_ref'] = lease_ref
            public['reappend_uncertain'] = reappend_uncertain(row)
        rows.append(public)
    return {'available': True, 'rows': rows, 'truncated': len(leases) > limit}


def default_store():
    from .work_parallel_mcp import finalization
    return ReviewDeliveryStore.beside(finalization())


def dispatch(state, lease_id, args, store=None):
    """work_side_wake review_* operations (host-only). Every answer echoes protocol 2."""
    store = store or default_store()
    _text(lease_id, 'lease', 200)
    if state == 'review_appended':
        result = store.appended(lease_id, args.get('attempt_id'), args.get('seen_receipt'), args.get('receipt'))
    elif state == 'review_read':
        result = store.read(lease_id)
    elif state == 'review_status':
        result = store.status(lease_id, args.get('seen_receipt'))
    elif state == 'review_reappend_prepare':
        result = store.reappend_prepare(lease_id, args.get('owner_nonce'), args.get('seen_receipt'))
    elif state == 'review_reappend_retry':
        result = store.reappend_retry(lease_id, args.get('reappend_ref'), confirmed=args.get('confirmed') is True)
    elif state.startswith('review_reappend_'):
        result = store.reappend_record(lease_id, args.get('attempt_id'), args.get('owner_nonce'),
                                       state[len('review_reappend_'):], receipt=args.get('receipt'))
    else:
        raise WorkPlanError('invalid side wake state')
    return {'protocol': 2, **result}
