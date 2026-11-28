"""Durable reply submission state; uncertain sends are never silently repeated."""
import time
import uuid

from .channel_message_repository import exclusive_file_lock
from .side_intake_inbox import SideIntakeInbox, reply_retry_pin as retry_pin
from .side_notice import PIN_REF
from .stat_verdict_cache import StatVerdictCache
from .work_plan_tree import WorkPlanError
import heapq


FAILURE_TEXT = ('이 메시지는 사이드 처리에서 자동으로 처리하지 못했습니다. 원문을 메인 세션에 그대로 전달했고, '
                '메인 세션이 직접 확인한 뒤 답변드리겠습니다.')


_RECEIPT_VERDICTS = StatVerdictCache()


def _owed_reply_state(path):
    """The outbox state of a processed receipt that still owes its reply, else None."""
    row = SideIntakeInbox._read_file(path)
    if row and row.get('source_status', {}).get('state') in ('pending', 'submitting', 'uncertain'):
        return 'source_status'
    if (row and row.get('stage')=='processed' and row.get('result',{}).get('delivery') not in ('route_unavailable','side_mcp','duplicate_source','not_applicable')
            and row.get('outbox',{}).get('state') not in ('submitted','uncertain','submitting','failed')):
        return row.get('outbox',{}).get('state','pending')
    return None


def pending(state_dir, identity, limit=10):
    directory=SideIntakeInbox(state_dir, identity).directory / 'receipts'
    # Andy 2026-11-22: reading every receipt on every claim (5,459 files,
    # ~9 s, none owed) took ~41% of the router's CPU samples. Unchanged
    # receipts keep their last verdict; new or rewritten ones are read.
    def eligible():
        for path, state in _RECEIPT_VERDICTS.scan(str(directory), directory.glob('*/*.json'), _owed_reply_state):
            if state is not None:
                yield {'receipt_id':path.stem,'state':state}
    rows = heapq.nsmallest(limit,eligible(),key=lambda row:row['receipt_id'])
    # A quarantined event never reaches 'processed'; its truthful failure reply
    # is owed from the moment its original was handed to main.
    from .side_forward import FIELD as FORWARD
    pending_dir = SideIntakeInbox(state_dir, identity).directory / 'pending'
    for path in pending_dir.glob('*/*.json') if len(rows) < limit else ():
        row = SideIntakeInbox._read_file(path)
        if (row and (row.get('side_failure') or {}).get('quarantined') and row.get(FORWARD)
                and (row.get('route') or {}).get('source_kind') == 'external_event'
                and (row.get('outbox') or {}).get('state') not in ('submitted','uncertain','submitting','failed','not_applicable')):
            rows.append({'receipt_id': path.stem, 'state': 'failure_reply'})
            if len(rows) >= limit:
                break
    return rows


def _prepare_failure(inbox, receipt_id, row, config):
    """The source learns the truth: not processed, handed to main (no ACK prefix)."""
    from .side_reply_connector import prepare as connector_prepare
    connector = connector_prepare(config or {}, row.get('route') or {}, receipt_id, FAILURE_TEXT, 'status',
                                  original=row.get('text', ''))
    if connector is None:
        row['outbox'] = {'state': 'not_applicable', 'updated_at': time.time(), 'failure_reply': True}
        inbox._save_file(inbox._event_path('pending', receipt_id), row)
        return {'state': 'not_applicable', 'receipt_id': receipt_id}
    attempt_id = uuid.uuid4().hex
    row['outbox'] = {'state': 'submitting', 'started_at': time.time(), 'attempt_id': attempt_id,
                     'failure_reply': True}
    inbox._save_file(inbox._event_path('pending', receipt_id), row)
    return {'state': 'submitting', 'receipt_id': receipt_id, 'attempt_id': attempt_id, **connector}


def outbound_text(row):
    """The one message an event's source gets: the understanding, then the result.

    User 2026-11-19: the source got a receipt ACK, the understanding and the
    result as three messages; it now gets this one. A row with no saved
    understanding (a coordinator follow-up, an older build) sends the result alone.
    """
    result = row['result'].get('final_text') or row['result']['reply']
    proposal = row.get('proposal') if isinstance(row.get('proposal'), dict) else {}
    understanding = proposal.get('understanding')
    already = (row.get('understanding_outbox') or {}).get('state') in ('submitting', 'uncertain', 'submitted')
    if not isinstance(understanding, str) or not understanding.strip() or already:
        return result
    understanding = understanding.strip()
    if understanding in result:
        return result
    return understanding + '\n\n' + result


def _drop_pending_outbox(row):
    # A retry left outbox {'state': 'pending'}. An outcome with no send attempt
    # must not keep it: the receipt shows result.delivery, while the retry and
    # review rows read outbox.state first, so it could never be retried again.
    if (row.get('outbox') or {}).get('state') == 'pending':
        row.pop('outbox', None)


def prepare(state_dir, identity, receipt_id, *, config=None, source_status=False):
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if source_status:
            job = (row or {}).get('source_status', {})
            state = job.get('state')
            if state not in ('pending', 'submitting', 'uncertain'):
                return {'state': state or 'not_applicable', 'receipt_id': receipt_id}
            checks = int(job.get('checks') or 0)
            if checks >= 3:
                job['state'] = 'unconfirmed'
                inbox._save_file(inbox._event_path('receipts', receipt_id), row)
                return {'state': 'unconfirmed', 'receipt_id': receipt_id}
            if state == 'pending':
                job.update(state='submitting', attempt_id=uuid.uuid4().hex)
            job['checks'] = checks + 1
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return {'state': 'submitting', 'attempt_id': job['attempt_id'],
                    'source_status': {**job['plan'], 'phase': job['phase'], 'send': state == 'pending'}}
        previous = (row or {}).get('outbox', {})
        if row and row.get('stage') != 'processed' and (row.get('side_failure') or {}).get('quarantined'):
            if previous.get('state') in ('submitting', 'uncertain', 'submitted', 'failed', 'not_applicable'):
                return {'state': previous['state'], 'receipt_id': receipt_id}
            return _prepare_failure(inbox, receipt_id, row, config)
        if not row or row.get('stage') != 'processed':
            raise WorkPlanError('processed side decision required')
        route = row.get('route', {})
        if previous.get('state') in ('submitting', 'uncertain', 'submitted'):
            return {'state': previous['state'], 'receipt_id': receipt_id}
        from .side_intake_inbox import unaddressed_reply
        if (row['result'].get('action') == 'answer' and row['result'].get('kind') == 'status'
                and route.get('source_kind') == 'external_event' and unaddressed_reply(row.get('text'))):
            # The side read it and found nothing to answer: a status answer to an
            # unaddressed reply is recorded, not posted. Posting it was the
            # other agent's next event (Andy and Celly, 2026-11-19).
            row['result']['no_reply'] = True
        if row['result'].get('no_reply') is True:
            # The side judged its answer a repeat of the thread: recorded, not posted.
            row['result']['delivery'] = 'not_applicable'
            _drop_pending_outbox(row)
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return {'state': 'not_applicable', 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        kind = 'status' if row['result'].get('action') == 'advise' else 'reply'
        # final_text closes the model's reply with the committed queue outcome;
        # the understanding leads it in the same message.
        message = outbound_text(row)
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
                                  or row['result'].get('source_context') == 'none_referenced'
                                  or route.get('source_kind') == 'gate_check'):
            row['result']['delivery'] = 'not_applicable'
            _drop_pending_outbox(row)
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return {'state': 'not_applicable', 'receipt_id': receipt_id,
                    'receipt': inbox._receipt(receipt_id, row, duplicate=False)}
        # External receiver channel names alone are not outbound connectors.
        if connector is None and (route.get('source_kind') != 'web_chat' or not route.get('reply_token') or not route.get('channel')):
            # Legacy actors may have answered through the source MCP themselves
            # (reported by the side AI, not host-observed). New actors cannot send.
            row['result']['delivery'] = 'side_mcp' if row['result'].get('reported_sends') else 'route_unavailable'
            _drop_pending_outbox(row)
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


def retry(state_dir, identity, receipt_id, expected_state, confirmed=False, *, attempt_ref=None, revision=None):
    """Explicit operator retry; ambiguous outcomes require duplicate-send consent.

    Pinned to the exact attempt the person saw (reply_attempt_ref plus
    reply_revision, from retry_pin): after a retry went pending and uncertain
    again, a late replay of the first click cannot resend the newer attempt.
    """
    needs = ("side reply retry needs the receipt's reply_revision and reply_attempt_ref; "
             'refresh before retry')
    if (type(revision) is not int or revision < 0
            or attempt_ref is not None and (not isinstance(attempt_ref, str) or PIN_REF.fullmatch(attempt_ref) is None)):
        raise WorkPlanError(needs)
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if not row or row.get('stage') != 'processed':
            raise WorkPlanError('processed side decision required')
        pin = retry_pin(row)
        if attempt_ref is None and 'reply_attempt_ref' in pin:
            raise WorkPlanError(needs)
        current = row.get('outbox', {}).get('state') or row.get('result', {}).get('delivery', 'pending')
        if current != expected_state or pin != {'reply_revision': revision, **({'reply_attempt_ref': attempt_ref} if attempt_ref else {})}:
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
        # Operator surfaces (REST, web, ctl, the pane): the bounded redacted row
        # GET /side lists, with the new delivery and pins; never the raw record.
        return SideIntakeInbox._public_summary(receipt_id, row, now=time.time())


def record(state_dir, identity, receipt_id, state, *, attempt_id, source_status=False):
    if state not in ('submitted', 'uncertain', 'failed'):
        raise WorkPlanError('invalid side delivery state')
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', receipt_id)):
        row = inbox._load(receipt_id)
        if source_status:
            job = (row or {}).get('source_status', {})
            if not attempt_id or job.get('attempt_id') != attempt_id or job.get('state') not in ('submitting', 'uncertain'):
                raise WorkPlanError('stale source status attempt')
            if state == 'submitted' and job['phase'] == 'ack':
                job.update(phase='completed', state='pending', checks=0)
                job.pop('attempt_id')
            else:
                job['state'] = state
            inbox._save_file(inbox._event_path('receipts', receipt_id), row)
            return inbox._receipt(receipt_id, row, duplicate=False)
        if not row or row.get('outbox', {}).get('state') != 'submitting':
            raise WorkPlanError('side send was not prepared')
        if not attempt_id or row['outbox'].get('attempt_id') != attempt_id:
            raise WorkPlanError('stale side reply attempt')
        failure_reply = row['outbox'].get('failure_reply') is True
        row['outbox'] = {'state': state, 'updated_at': time.time(), 'attempt_id': attempt_id,
                         **({'failure_reply': True} if failure_reply else {})}
        if failure_reply:
            inbox._save_file(inbox._event_path('pending', receipt_id), row)
            return inbox._receipt(receipt_id, row, duplicate=False)
        row['result']['delivery'] = state
        inbox._save_file(inbox._event_path('receipts', receipt_id), row)
        return inbox._receipt(receipt_id, row, duplicate=False)
