"""Durable model-written understanding sent before a side decision commits.

The side model proposes ``understanding`` and ``decision`` together. The host
persists the validated proposal on the receipt, sends the understanding with
its own connector phase/client_id, then commits the Work decision. A crash
between these steps resumes from the saved proposal without running the model
again. An uncertain send is never silently repeated.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox
from .work_plan_tree import WorkPlanError

MAX_UNDERSTANDING = 4000


def digest(understanding, decision, expected_version):
    body = {'understanding': understanding, 'decision': decision, 'expected_version': expected_version}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def validate_understanding(value, original=''):
    if isinstance(value, str) and len(value) > MAX_UNDERSTANDING:
        # An over-long restatement is cut with a marker, not refused (Andy
        # 2026-11-18); a missing, blank, ACK or echoed one is still refused.
        from .side_work_decision import clamp_text
        value = clamp_text(value, MAX_UNDERSTANDING)
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_UNDERSTANDING:
        raise WorkPlanError('side proposal needs bounded understanding')
    text = value.strip()
    if text.startswith('ACK'):
        raise WorkPlanError('side understanding must not start with ACK')
    # An exact original or its Walkie message text is an echo, not understanding.
    if text == str(original).strip():
        raise WorkPlanError('side understanding must not copy the original event')
    try:
        body = json.loads(original)
        data = body.get('data') if isinstance(body, dict) and isinstance(body.get('data'), dict) else {}
        source = data.get('text')
    except (TypeError, ValueError):
        source = None
    if isinstance(source, str) and text == source.strip():
        raise WorkPlanError('side understanding must restate, not copy, the source message')
    return text


def save_proposal(state_dir, identity, receipt_id, generation, proposal):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        from .side_intake_processing import verify_claim
        row = verify_claim(inbox, receipt_id, generation)
        understanding = validate_understanding(proposal.get('understanding'), row.get('text', ''))
        decision = proposal.get('decision')
        expected = proposal.get('expected_version')
        from .side_work_decision import validate
        if not isinstance(decision, dict):
            raise WorkPlanError('side decision object required')
        validate(decision)
        if expected is not None and type(expected) is not int:
            raise WorkPlanError('expected Work version must be an integer')
        value = {'understanding': understanding, 'decision': decision, 'expected_version': expected,
                 'digest': digest(understanding, decision, expected), 'saved_at': time.time()}
        prior = row.get('proposal')
        if isinstance(prior, dict):
            if prior.get('digest') != value['digest']:
                raise WorkPlanError('side event already has a different proposal')
            return prior
        row['proposal'] = value
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return value


def proposal(state_dir, identity, receipt_id):
    row = SideIntakeInbox(state_dir, identity)._load(receipt_id)
    value = (row or {}).get('proposal')
    return dict(value) if isinstance(value, dict) else None


def _save(inbox, receipt_id, row):
    inbox._save_file(inbox._event_path('receipts' if row.get('stage') == 'processed' else 'pending', receipt_id), row)


def prepare(state_dir, identity, receipt_id, *, config=None):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        value = (row or {}).get('proposal')
        if not row or not isinstance(value, dict):
            raise WorkPlanError('persisted side proposal required')
        previous = row.get('understanding_outbox') if isinstance(row.get('understanding_outbox'), dict) else {}
        if previous.get('state') in ('submitting', 'uncertain', 'submitted', 'not_applicable'):
            return {'state': previous['state'], 'receipt_id': receipt_id}
        route = row.get('route') if isinstance(row.get('route'), dict) else {}
        connector = None
        if route.get('source_kind') == 'external_event':
            from .side_reply_connector import prepare as connector_prepare
            connector = connector_prepare(config or {}, route, receipt_id, value['understanding'], 'status',
                                          original=row['text'], phase='understanding')
        if connector is None:
            row['understanding_outbox'] = {'state': 'not_applicable', 'updated_at': time.time()}
            _save(inbox, receipt_id, row)
            return {'state': 'not_applicable', 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        attempt_id = uuid.uuid4().hex
        row['understanding_outbox'] = {'state': 'submitting', 'started_at': time.time(),
                                       'attempt_id': attempt_id}
        _save(inbox, receipt_id, row)
        return {'state': 'submitting', 'receipt_id': receipt_id, 'attempt_id': attempt_id, **connector}


def record(state_dir, identity, receipt_id, state, *, attempt_id):
    if state not in ('submitted', 'uncertain', 'failed'):
        raise WorkPlanError('invalid side understanding delivery state')
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        previous = row.get('understanding_outbox') if isinstance((row or {}).get('understanding_outbox'), dict) else {}
        if not row or previous.get('state') != 'submitting':
            raise WorkPlanError('side understanding was not prepared')
        if not attempt_id or previous.get('attempt_id') != attempt_id:
            raise WorkPlanError('stale side understanding attempt')
        row['understanding_outbox'] = {'state': state, 'updated_at': time.time(), 'attempt_id': attempt_id}
        _save(inbox, receipt_id, row)
        return inbox._receipt(receipt_id, row, duplicate=False)
