"""At-most-once claim for the released-wait notice of one side event.

The notice text reaches main through the host. The host's own memory of having
issued it (module state and the engine's session state) is lost by a new
process, by /clear followed by a reload, and by eviction. This claim is stored
on the event's processed receipt, under that receipt's file lock, so it
survives all three: only the first prepare is granted.

The claim also keeps the notice text its issuer appended. That lets a later
host re-issue it after a definite failure, or after a person confirmed a retry
of an outcome nobody can know (an issuer that vanished while ``issuing``, or an
append recorded ``uncertain``). Such receipts are listed in a small index the
side claim reads, so no poll scans every receipt.
"""
from __future__ import annotations

import hashlib
import re
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .work_plan_tree import WorkPlanError

_OUTCOMES = ('appended', 'failed', 'uncertain')
# A definite failure stored nothing; a person-confirmed release accepted a
# possible duplicate. Both may be issued once more; nothing else reopens.
_REOPEN = ('failed', 'released')
FIELD = 'release_notice'
TEXT_MAX = 8000
HISTORY_MAX = 16
INDEX = 'notices-pending.json'
INDEX_MAX = 200
# Live claims (`issuing`) by receipt with their claim time. A claim turns
# delivery-uncertain by age alone, with no write; the web event stream reads
# this small file to signal that moment (work_http_controller._stream).
ISSUING_INDEX = 'notices-issuing.json'
NOTICE_STATES = ('issuing', 'appended', 'failed', 'released', 'uncertain')
# A live issuer claims, appends and records within one hook dispatch. Only an
# `issuing` claim older than this is shown to a person as uncertain; age alone
# never reopens it.
ISSUING_GRACE_SECONDS = 60.0


def _released_row(inbox, receipt_id):
    row = inbox._load(receipt_id)
    from .side_forward import is_forward
    if is_forward(row):
        # (2026-11-16) A quarantined event's original forwarded to main. It
        # never had a decision, so it still lives in pending/.
        return row
    result = row.get('result') if isinstance(row, dict) else None
    # A released wait, or (2026-11-15) a live-item relay: both are one host
    # notice to main per receipt, issued at most once.
    if (not row or row.get('stage') != 'processed' or not isinstance(result, dict)
            or not (result.get('released_waits') or result.get('relay'))):
        raise WorkPlanError('released-wait receipt required')
    return row


def _save(inbox, receipt_id, row):
    """Write the notice to the record where the event lives."""
    area = 'receipts' if row.get('stage') in ('processed', 'board_queued') else 'pending'
    inbox._save_file(inbox._event_path(area, receipt_id), row)


def _text(value):
    return value[:TEXT_MAX] if isinstance(value, str) and value.strip() else None


def _ref(attempt_id):
    return hashlib.sha256(str(attempt_id).encode()).hexdigest()[:16] if attempt_id else None


PIN_REF = re.compile(r'[0-9a-f]{16}')


def retry_pin(notice):
    """The exact attempt a person's retry must name: its one-way ref and the
    notice's retry count. Operator surfaces export these; never the attempt."""
    if not isinstance(notice, dict) or not notice.get('attempt_id'):
        return {}
    return {'notice_attempt_ref': _ref(notice['attempt_id']), 'notice_revision': int(notice.get('retries') or 0)}


def delivery_uncertain(notice, now=None):
    """Bounded operator signal; it carries no attempt, owner or receipt."""
    if not isinstance(notice, dict):
        return False
    if notice.get('state') == 'uncertain':
        return True
    claimed = notice.get('claimed_at')
    return (notice.get('state') == 'issuing' and isinstance(claimed, (int, float))
            and (time.time() if now is None else now) - claimed >= ISSUING_GRACE_SECONDS)


def _index(inbox, receipt_id, present):
    """Add or drop one receipt in the re-issue index (receipt lock held)."""
    path = inbox.directory / INDEX
    with exclusive_file_lock(path):
        try:
            current = inbox._read_file(path, 64 * 1024) or {}
        except WorkPlanError:
            current = {}
        listed = [key for key in current.get('receipts', []) if isinstance(key, str) and key != receipt_id]
        if present:
            listed.append(receipt_id)
        if listed != current.get('receipts'):
            inbox._save_file(path, {'schema': 1, 'receipts': listed[-INDEX_MAX:]})


def _issuing(inbox, receipt_id, claimed_at):
    """Add (claimed_at) or drop (None) one live claim in the issuing index."""
    path = inbox.directory / ISSUING_INDEX
    try:
        with exclusive_file_lock(path):
            try:
                current = (inbox._read_file(path, 64 * 1024) or {}).get('issuing') or {}
            except WorkPlanError:
                current = {}
            listed = {key: at for key, at in current.items()
                      if isinstance(key, str) and key != receipt_id and isinstance(at, (int, float))}
            if claimed_at is not None:
                listed[receipt_id] = claimed_at
            if listed != current:
                kept = dict(sorted(listed.items(), key=lambda item: item[1])[-INDEX_MAX:])
                inbox._save_file(path, {'schema': 1, 'issuing': kept})
    except OSError:
        pass  # A display hint only; the claim on the receipt is authoritative.


def crossed_grace(inbox, now=None):
    """Receipts whose live claim is now delivery-uncertain by age (sorted)."""
    try:
        listed = (inbox._read_file(inbox.directory / ISSUING_INDEX, 64 * 1024) or {}).get('issuing') or {}
    except (OSError, WorkPlanError):
        return []
    now = time.time() if now is None else now
    return sorted(key for key, at in listed.items()
                  if isinstance(at, (int, float)) and now - at >= ISSUING_GRACE_SECONDS)


def public_state(notice):
    """The bounded notice state word for operator surfaces (never text or ids)."""
    state = notice.get('state') if isinstance(notice, dict) else None
    return state if state in NOTICE_STATES else 'unknown'


def prepare(inbox, receipt_id, owner_nonce, text=None):
    """Grant the notice of this receipt once; every later prepare is refused."""
    if not isinstance(owner_nonce, str) or not owner_nonce or len(owner_nonce) > 200:
        raise WorkPlanError('side notice owner required')
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = _released_row(inbox, receipt_id)
        notice = row.get(FIELD)
        if isinstance(notice, dict) and notice.get('state') not in _REOPEN:
            # uncertain/issuing never reopen without evidence or a person: the
            # original issuer may still append late. Never an attempt id: a
            # second caller can neither deliver nor record.
            return {'receipt_id': receipt_id, 'protocol': 2, 'state': 'already_' + str(notice.get('state'))}
        attempt_id = uuid.uuid4().hex
        claim = {'state': 'issuing', 'attempt_id': attempt_id, 'owner_nonce': owner_nonce,
                 'claimed_at': time.time()}
        kept = _text(text) or (notice.get('text') if isinstance(notice, dict) else None)
        if kept:
            claim['text'] = kept
        if isinstance(notice, dict):
            claim['retries'] = int(notice.get('retries') or 0) + 1
            if notice.get('history'):
                claim['history'] = notice['history']
        row[FIELD] = claim
        _save(inbox, receipt_id, row)
        _index(inbox, receipt_id, False)
        _issuing(inbox, receipt_id, claim['claimed_at'])
        return {'receipt_id': receipt_id, 'protocol': 2, 'state': 'claimed', 'attempt_id': attempt_id}


def record(inbox, receipt_id, state, attempt_id, *, owner_nonce=None, receipt=None):
    """Record what the granted attempt did. Only a definite failure reopens."""
    if state not in _OUTCOMES:
        raise WorkPlanError('invalid side notice state')
    if receipt is not None and (not isinstance(receipt, str) or not receipt or len(receipt) > 300):
        raise WorkPlanError('invalid side notice receipt')
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = _released_row(inbox, receipt_id)
        notice = row.get(FIELD)
        if (not isinstance(notice, dict) or not attempt_id or notice.get('attempt_id') != attempt_id
                or not owner_nonce or notice.get('owner_nonce') != owner_nonce):
            raise WorkPlanError('stale side notice attempt')
        if notice.get('state') == state and notice.get('receipt') in (None, receipt):
            if receipt is not None and notice.get('receipt') is None:
                notice.update(receipt=receipt, updated_at=time.time())
                _save(inbox, receipt_id, row)
            return {'receipt_id': receipt_id, 'protocol': 2, 'state': state}
        if notice.get('state') != 'issuing':
            raise WorkPlanError('side notice outcome already recorded differently')
        notice.update(state=state, updated_at=time.time())
        if receipt is not None:
            notice['receipt'] = receipt
        _save(inbox, receipt_id, row)
        _issuing(inbox, receipt_id, None)
        from .side_forward import is_forward
        # A failed forward stays in its own index (side_forward.offered), which
        # rebuilds the full original; the 8,000-character copy is not re-offered.
        if state == 'failed' and notice.get('text') and not is_forward(row):
            _index(inbox, receipt_id, True)
        return {'receipt_id': receipt_id, 'protocol': 2, 'state': state}


def retry(inbox, receipt_id, *, attempt_ref=None, revision=None, confirmed=False):
    """A person releases an unknown-outcome notice for one possibly duplicate re-issue.

    Pinned to the exact attempt the person saw (notice_attempt_ref plus
    notice_revision, from retry_pin): a late replay, a stale page or an
    evicted idempotency key cannot release a later uncertain attempt.
    """
    if confirmed is not True:
        raise WorkPlanError('unknown notice outcome: explicit duplicate-notice confirmation required')
    if (not isinstance(attempt_ref, str) or PIN_REF.fullmatch(attempt_ref) is None
            or type(revision) is not int or revision < 0):
        raise WorkPlanError("side notice retry needs the receipt's notice_attempt_ref and notice_revision; "
                            'refresh before retry')
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = _released_row(inbox, receipt_id)
        notice = row.get(FIELD)
        now = time.time()
        if not delivery_uncertain(notice, now):
            raise WorkPlanError('side notice delivery is not uncertain; refresh before retry')
        if retry_pin(notice) != {'notice_attempt_ref': attempt_ref, 'notice_revision': revision}:
            raise WorkPlanError('side notice changed; refresh before retry')
        from .side_forward import is_forward, readd
        forward = is_forward(row)
        if not notice.get('text') and not forward:
            raise WorkPlanError('side notice text was not kept by its issuer; it cannot be re-issued')
        history = list(notice.get('history') or [])[-(HISTORY_MAX - 1):] + [{
            'previous_state': notice.get('state'), 'previous_attempt_ref': _ref(notice.get('attempt_id')),
            'previous_revision': revision, 'duplicate_confirmed': True, 'at': now}]
        row[FIELD] = {'state': 'released', 'released_at': now,
                      'retries': int(notice.get('retries') or 0), 'history': history,
                      **({'text': notice['text']} if notice.get('text') else {})}
        _save(inbox, receipt_id, row)
        _issuing(inbox, receipt_id, None)
        if forward:
            # A forward's text is rebuilt from the original when offered.
            readd(inbox, receipt_id)
        else:
            _index(inbox, receipt_id, True)
        return {'receipt_id': receipt_id, 'protocol': 2, 'state': 'released', 'notice_delivery_uncertain': False}


def offered(inbox, limit=8):
    """Notices a host may issue once more: definite failure or person release."""
    try:
        listed = (inbox._read_file(inbox.directory / INDEX, 64 * 1024) or {}).get('receipts') or []
    except (OSError, WorkPlanError):
        return []
    rows = []
    for receipt_id in listed:
        try:
            notice = (inbox._load(receipt_id) or {}).get(FIELD)
        except (OSError, WorkPlanError):
            continue
        if isinstance(notice, dict) and notice.get('state') in _REOPEN and notice.get('text'):
            rows.append({'receipt_id': receipt_id, 'text': notice['text']})
            if len(rows) >= limit:
                break
    return rows
