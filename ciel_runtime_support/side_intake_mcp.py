"""Host-scoped claim and decision tools for the independent side actor."""
import json

from .side_intake_inbox import SideIntakeInbox
from .side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS, side_text_fits_pending_record
from .side_intake_processing import claim, fail, fallback, finish, verify_claim
from .side_work_decision import WorkChanged, apply, validate
from .work_plan_tree import WorkPlanError
from .channel_message_repository import exclusive_file_lock

NATIVE_FORK_FINGERPRINT = 'native-fork'
NAMES = ('work_side_claim', 'work_side_understanding', 'work_side_resolve', 'work_side_fail', 'work_side_fallback', 'work_side_ack', 'work_side_reply', 'work_side_wake', 'work_side_handoff', 'work_side_review', 'work_side_renew', 'work_side_retry_reply', 'work_side_code_read', 'work_side_code_search')


def schemas():
    return [
        {'name': 'work_side_fail', 'description': 'Host-only generation-fenced failure release with durable backoff or model quarantine.',
         'inputSchema': {'type':'object','properties':{'category':{'type':'string','maxLength':80},
             'model':{'type':'string','maxLength':300},'model_fingerprint':{'type':'string','maxLength':128},
             'agent_id':{'type':'string','maxLength':200},'reason':{'type':'string','maxLength':300},
             'retry_after_seconds':{'type':'integer','minimum':5,'maximum':600}},
             'required':['category']}},
        {'name': 'work_side_fallback', 'description': 'Host-only generation-fenced transition of a side-model failure to exact-once main-session delivery.',
         'inputSchema': {'type':'object','properties':{'state':{'type':'string','enum':['prepare','queued','failed']},
             'reason':{'type':'string','maxLength':160},'agent_id':{'type':'string','maxLength':200}},'required':['state']}},
        {'name': 'work_side_retry_reply', 'description': 'Host UI only: explicitly retry failed or unknown side reply; unknown outcomes require person confirmation.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'expected_state': {'type': 'string'}, 'confirmed': {'type': 'boolean'},
             'revision': {'type': 'integer', 'minimum': 0, 'description': "The receipt's reply_revision"},
             'attempt_ref': {'type': 'string', 'maxLength': 16,
                             'description': "The receipt's reply_attempt_ref, when it shows one"}},
             'required': ['receipt_id', 'expected_state', 'revision']}},
        {'name': 'work_side_renew', 'description': 'Host-only heartbeat for one live side actor; revives a lapsed lease only while no other claim took the event.'
                                                   'source_bounded records that the fork got a bounded source thread (older replies left out).',
         'inputSchema': {'type': 'object', 'properties': {'source_bounded': {'type': 'object', 'properties': {
             'omitted_replies': {'type': 'integer', 'minimum': 1}}, 'required': ['omitted_replies']}}}},
        {'name':'work_side_handoff','description':'Acknowledge side advice at a safe main execution boundary; selecting existing work or clearing a recorded wait never approves new execution.',
         'inputSchema':{'type':'object','properties':{'event_id':{'type':'string'},'answer':{'type':'string'},
             'activate':{'type':'boolean'},'clear_wait':{'type':'array','items':{'type':'string'},'uniqueItems':True},
             'expected_version':{'type':'integer','minimum':0}},'required':['event_id','expected_version']}},
        {'name':'work_side_review','description':'Main: mark external messages the side actor already answered as reviewed. Do not answer or ACK them again; '
             'only when a side reply was wrong, send one correction to the same source, then use verdict=corrected with a note.',
         'inputSchema':{'type':'object','properties':{'receipt_ids':{'type':'array','items':{'type':'string'},'uniqueItems':True,
             'minItems':1,'maxItems':50},'verdict':{'type':'string','enum':['ok','corrected']},'note':{'type':'string','maxLength':2000},
             'expected_version':{'type':'integer','minimum':0}},'required':['receipt_ids','verdict','expected_version']}},
        {'name': 'work_side_ack', 'description': 'Host-only prepare or record the immediate ACK/readback sent before side processing.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'submitted', 'uncertain', 'failed']}}, 'required': ['receipt_id', 'state']}},
        {'name': 'work_side_understanding', 'description': 'Host-only: persist the actor proposal and send its own-word understanding before the board commit.',
         'inputSchema': {'type': 'object', 'properties': {
             'understanding': {'type': 'string', 'maxLength': 4000}, 'decision': {'type': 'object'},
             'expected_version': {'type': 'integer', 'minimum': 0},
             'state': {'type': 'string', 'enum': ['prepare', 'submitted', 'uncertain', 'failed']}},
             'required': ['state']}},
        {'name': 'work_side_reply', 'description': 'Host-only prepare or record a correlated side processing-result reply; submission is not proof of human delivery.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'submitted', 'uncertain', 'failed']}}, 'required': ['receipt_id', 'state']}},
        {'name': 'work_side_wake', 'description': 'Host-only prepare or record one durable main wake attempt, '
                                                  'or the at-most-once claim of one released-wait notice.',
         'inputSchema': {'type': 'object', 'properties': {'event_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'start', 'reconcile', 'retry',
                                                   'queued', 'appended', 'uncertain', 'failed', 'read',
                                                   'prompt_prepare', 'prompt_queued', 'prompt_failed',
                                                   'prompt_uncertain', 'reappend_prepare', 'reappend_appended',
                                                   'reappend_failed', 'reappend_uncertain', 'reappend_retry',
                                                   'notice_prepare', 'notice_appended',
                                                   'notice_failed', 'notice_uncertain', 'notice_retry',
                                                   'review_appended', 'review_read', 'review_status',
                                                   'review_reappend_prepare', 'review_reappend_appended',
                                                   'review_reappend_failed', 'review_reappend_uncertain',
                                                   'review_reappend_retry']},
             'text': {'type': 'string', 'maxLength': 8000},
             'attempt_id': {'type': 'string'}, 'protocol': {'type': 'integer', 'enum': [2]},
             'owner_nonce': {'type': 'string', 'maxLength': 200},
             'channel': {'type': 'string', 'enum': ['prompt', 'append', 'stop']},
             'receipt': {'type': 'string', 'maxLength': 300}, 'seen_receipt': {'type': 'string', 'maxLength': 300},
             'revision': {'type': 'integer', 'minimum': 0}, 'confirmed': {'type': 'boolean'},
             'attempt_ref': {'type': 'string', 'maxLength': 16}, 'reappend_ref': {'type': 'string', 'maxLength': 16}},
             'required': ['event_id', 'state']}},
        {'name': 'work_side_code_read', 'description': 'Side-only bounded read of one UTF-8 file inside the session workspace.',
         'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'maxLength': 1000},
             'offset': {'type': 'integer', 'minimum': 1}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 400}},
             'required': ['path']}},
        {'name': 'work_side_code_search', 'description': 'Side-only bounded regex search inside the session workspace.',
         'inputSchema': {'type': 'object', 'properties': {'pattern': {'type': 'string', 'maxLength': 300},
             'glob': {'type': 'string', 'maxLength': 200}}, 'required': ['pattern']}},
        {'name': 'work_side_claim', 'description': 'Host-only: claim one durable request for immediate independent AI processing, without creating Work.',
         'inputSchema': {'type': 'object', 'properties': {'owner': {'type': 'string', 'maxLength': 200},
             'urgent_only': {'type': 'boolean'}}, 'required': ['owner']}},
        {'name': 'work_side_resolve', 'description': 'Commit the persisted proposal of one host-claimed event after its understanding send; never execution approval.',
         'inputSchema': {'type': 'object', 'properties': {
             'decision': {'type': 'object', 'properties': {
                 'action': {'type': 'string', 'enum': ['answer', 'clarify', 'update', 'create', 'prioritize', 'advise']},
                 'kind': {'type': 'string', 'enum': ['status', 'info', 'request', 'clarify']},
                 'item_id': {'type': 'string'}, 'summary': {'type': 'string'}, 'reply': {'type': 'string'},
                 'note': {'type': 'string'}, 'title': {'type': 'string'},
                 'priority': {'type': 'string', 'enum': ['', 'P0', 'P1', 'P2', 'P3', 'P4', 'P5']},
                 'queue_position': {'type': 'integer', 'minimum': 1},
                 'release_waits': {'type': 'array', 'maxItems': 16, 'items': {
                     'type': 'object', 'properties': {'id': {'type': 'string'}, 'waiting_on': {'type': 'string'}},
                     'required': ['id', 'waiting_on'], 'additionalProperties': False}},
                 'relay': {'type': 'boolean'}, 'ask_main': {'type': 'boolean'},
                 'no_reply': {'type': 'boolean'}, 'complaint': {'type': 'boolean'},
                 'refs': {'type': 'array', 'maxItems': 32, 'items': {'type': 'string', 'maxLength': 64}},
                 'time_limit': {'type': 'object', 'properties': {
                     'text': {'type': 'string', 'maxLength': 1000},
                     'due_at': {'type': 'string', 'maxLength': 100},
                     'evidence': {'type': 'string', 'maxLength': 2000}},
                     'required': ['text', 'evidence'], 'additionalProperties': False}},
                 'required': ['action', 'summary', 'reply'], 'additionalProperties': False},
             'expected_version': {'type': 'integer', 'minimum': 0}}, 'required': ['decision', 'expected_version']}}
    ]


def _reported_sends(value):
    """MCP tool names the side AI reports it used to reply; names only."""
    names = []
    for name in value if isinstance(value, list) else []:
        if isinstance(name, str) and name.startswith('mcp__') and len(name) <= 200 and name not in names:
            names.append(name)
        if len(names) == 20:
            break
    return names


def _followup(event_id, answer, original):
    """The exact follow-up text and route, or None when one record cannot hold it.

    Above the ordinary 32K contract it uses the same raw-text route an
    oversized original does, so an answer to a large event is not refused.
    """
    text = ('Coordinator answer to side question ' + event_id + ':\n'
            + answer + '\nOriginal request:\n' + original['text'])
    route = dict(original.get('route', {}))
    if len(text) > SIDE_INTAKE_TEXT_MAX_CHARS:
        if not side_text_fits_pending_record(text):
            return None
        route['side_intake_fallback'] = 'raw_text'
    return text, route


def _handoff_needs_followup(original):
    """Only a side question/advice answer is new side input.

    P1 and resume handoffs merely notify main that side work was committed. Main's
    acknowledgment is durable board history, not another external event to answer.
    """
    result = original.get('result') if isinstance(original, dict) else None
    handoff = result.get('main_handoff') if isinstance(result, dict) else None
    return bool(isinstance(handoff, dict) and
                (handoff.get('kind') == 'advise' or str(handoff.get('question') or '').strip()))


def _answer_followup(inbox, event_id, answer):
    original = inbox._load(event_id)
    if original and _handoff_needs_followup(original):
        followup = _followup(event_id, answer, original)
        if followup is None:
            raise WorkPlanError('coordinator follow-up exceeds inbox record capacity; answer retained in Work')
        inbox.receive(followup[0], 'handoff:' + event_id, route=followup[1])


def _sync_handoff_receipt(inbox, event_id, handoff):
    with exclusive_file_lock(inbox._event_path('locks', event_id)):
        row = inbox._load(event_id)
        if not row or row.get('stage') != 'processed':
            return
        if row['result'].get('handoff') != handoff['state']:
            row['result']['handoff'] = handoff['state']
            inbox._save_file(inbox._event_path('receipts', event_id), row)
        return inbox._receipt(event_id, row, duplicate=False)


def _parked_projection(store, identity, decision):
    """Ledger pre-read for a release or a fork answer; unreadable means no parked preference.

    A fork answer (``fork_answers``) is checked against it: only a parked
    worker of this conversation is answered.
    """
    if not (isinstance(decision, dict) and (decision.get('fork_answers') or decision.get('stall_notices') or (
            decision.get('action') == 'update' and decision.get('release_waits')))):
        return None, None
    try:
        from .work_parallel_mcp import ledger
        from .work_wait_release import parked_projection
        parallel = ledger()
        document, board = parallel.read(), store.read()
        projection = parked_projection(document, board, identity)
        if any(isinstance(row, dict) and row.get('kind') == 'silent' for row in decision.get('stall_notices') or []):
            # A silent-fork notice is told only while the router itself still
            # sees that running fork's transcript stopped (user 2026-11-24).
            try:
                from .work_silent_forks import silent_projection
                silent = silent_projection(document, board, identity, resolve=True)
            except Exception:  # noqa: BLE001 - an unreadable transcript refuses the silent notice
                silent = None
            if silent is not None:
                projection['silent'] = silent
        return projection, parallel.path
    except Exception:
        try:
            from .work_parallel_mcp import ledger
            return None, ledger().path
        except Exception:
            return None, None


def _record_wait_boost(ledger_path, identity, receipt_id, result):
    """Sidecar write after the board commit. A failure keeps the board receipt
    for recovery and never undoes the committed release."""
    try:
        from .work_wait_release import record_released
        recorded = record_released(ledger_path, identity, receipt_id, result)
        return {'created': len(recorded['created']), 'kept': len(recorded['kept']),
                'consuming': len(recorded['consuming']), 'invalid': recorded['invalid']}
    except Exception as error:
        return {'error': type(error).__name__}


def _workflow_on(config):
    from .work_workflow import enabled
    return enabled(config)


def _activate_side_p0(store, identity, receipt_id, root_id):
    """Activate P0 on the goal a side decision stored as canonical P1.

    The existing-root activation REST, web, ctl and the Work tools use. A
    failure is reported in the result and never undoes the committed board.
    """
    try:
        from .work_parallel_mcp import ledger
        from .work_priority_service import mutate_existing_priority
        parallel = ledger()
        for _ in range(3):
            try:
                done = mutate_existing_priority(
                    store, parallel, identity, root_id, 'P0',
                    expected_board_version=store.read()['version'],
                    external_operation_id='side-p0:' + receipt_id[:64],
                    mutate=lambda document: None,
                    satisfied=lambda document: any(node.get('id') == root_id and node.get('priority') == 'P1'
                                                   for node in document.get('nodes', [])),
                    set_by='side_decision')
                return {'root_id': root_id, 'effective_priority': done['effective_priority']}
            except Exception as error:
                if 'plan changed' not in str(error):
                    raise
        return {'root_id': root_id, 'error': 'the plan kept changing; P0 not activated'}
    except Exception as error:
        return {'root_id': root_id, 'error': (type(error).__name__ + ': ' + str(error))[:300]}


def _active_p0_roots(identity):
    """Exact active P0 root identities, read before the board transaction."""
    try:
        from .work_parallel_mcp import p0_state
        roots = p0_state().read().get('roots', {}).values()
        return frozenset((row['root_id'], row['root_created_at']) for row in roots
                         if row.get('scope') == identity and row.get('state') == 'active')
    except Exception:  # noqa: BLE001 - absent or unreadable P0 state cannot invent P0
        return frozenset()


def _complaint_notice(state_dir, receipt_id, result):
    """Host-owned durable notice text for a complaint the actor identified."""
    if result.get('complaint') is not True or result.get('ask_main') is not True:
        return None
    from .work_workflow_prompts import render
    text, _ = render(state_dir, 'side_complaint_notice', item_id=result.get('item_id') or '-',
                     event_ref=receipt_id[:12], summary=' '.join(str(result.get('summary') or '').split())[:600])
    return text[:8000]


def _settle_side_p0(store, identity, receipt_id, result):
    if result.get('p0_root_id'):
        # The receipt-scoped operation also rolls forward a prior attempt.
        result['p0'] = _activate_side_p0(store, identity, receipt_id, result['p0_root_id'])
        _p0_readback(result)
    return result


def _side_model_configured(config):
    """Whether the gate gives side forks a model other than main's (work_fork_models side slot)."""
    try:
        from .work_fork_models import projection
        return bool((projection(config).get('ids') or {}).get('side'))
    except Exception:
        # Unknown: a failure still switches to main's model; main's model is
        # never worse than an unreadable side setting.
        return True


def _distinct_billing_parent(config, outcome):
    """Require a known failed model and a distinct, explicit main route."""
    from .work_fork_models import projection
    routes = projection(config)
    main = routes.get('main') or {}
    side_alias = (routes.get('models') or {}).get('side')
    side_model = (routes.get('upstream') or {}).get('side')
    failed = outcome.get('effective_model')
    return bool(not routes.get('forced_model') and failed and side_model and main.get('upstream')
                and failed in (side_alias, side_model)
                and main['upstream'] not in (side_model, side_alias)
                and main.get('model') not in (side_model, side_alias))


def _p0_readback(result):
    """The reply's last line states the P0 the host activated, or why it did not.

    The board stores P0 as canonical P1, so the committed outcome line read
    "P1" (Walkie 134499, 2026-11-16) even though P0 was active.
    """
    from .side_work_decision import outcome_text
    text = result.get('final_text')
    if not isinstance(text, str):
        return
    line = outcome_text(result)
    if not text.endswith(line):
        return
    p0 = result.get('p0') or {}
    if p0.get('effective_priority') == 'P0':
        result['final_text'] = text[:-len(line)] + outcome_text(result, priority='P0')
    elif p0.get('error'):
        result['final_text'] = text + '\nP0 적용 실패: ' + str(p0['error'])[:200]


def _unsent_to_main(store, state_dir, identity, receipt_id):
    """The person never saw this side reply: main reviews it (and may send it)."""
    from .side_work_decision import record_unsent_review
    from .side_intake_inbox import source_identity
    row = SideIntakeInbox(state_dir, identity)._load(receipt_id) if isinstance(receipt_id, str) else None
    result = (row or {}).get('result')
    if store is None or not isinstance(result, dict) or not result.get('summary'):
        return False
    return record_unsent_review(store, receipt_id, result, source_identity(row.get('text')))


def _person_retry_host(host):
    """A possibly duplicate re-issue that a person confirmed in the host UI.

    Only the mod's own retry call carries ``_host.internal``; host_context has
    already checked the host token, so the marker cannot arrive without it. The
    mod never forwards a model's side-tool call (register.js refuses it).
    """
    if host.get('internal') is not True:
        raise WorkPlanError('person-confirmed retry requires the verified Ciel Work host')


def run(name, args, config, state_dir, identity, store):
    from .work_parallel_mcp import host_context
    from .side_intake_settings import runtime_settings
    host, _ = host_context(args, config)
    runtime_side = runtime_settings(config)
    if host.get('agent_id') is not None:
        raise WorkPlanError('side coordinator bridge required')
    if config.get('work_side_intake', {}).get('enabled') is not True:
        raise WorkPlanError('side processing is disabled')
    if name == 'work_side_fallback':
        from .side_request_outcome import take as take_side_outcome
        outcome = take_side_outcome(state_dir, identity, str(args.get('agent_id') or ''),
                                    str(host.get('receipt_id') or ''))
        reason = args.get('reason', '')
        if outcome:
            reason = outcome.get('terminal_reason') or outcome.get('message') or reason
        if args.get('state') == 'prepare':
            inbox = SideIntakeInbox(state_dir, identity)
            row = inbox._load(host.get('receipt_id'))
            failure = (row or {}).get('side_failure') or {}
            if not failure.get('main_fallback_eligible') and not (row or {}).get('main_fallback'):
                raise WorkPlanError('side model unavailability not verified')
        return {'receipt': fallback(state_dir, identity, host.get('receipt_id'), host.get('generation'),
                                    args.get('state'), reason)}
    if name == 'work_side_retry_reply':
        from .side_reply_outbox import retry
        _person_retry_host(host)
        return {'receipt': retry(state_dir, identity, args.get('receipt_id'),
                                 args.get('expected_state'), args.get('confirmed', False),
                                 attempt_ref=args.get('attempt_ref'), revision=args.get('revision'))}
    if name == 'work_side_renew':
        from .side_intake_processing import renew
        return renew(state_dir, identity, host.get('receipt_id'), host.get('generation'),
                     source_bounded=args.get('source_bounded'))
    if name == 'work_side_fail':
        from .side_request_outcome import peek as peek_side_outcome
        receipt_id = host.get('receipt_id')
        receipt = fail(state_dir, identity, receipt_id, host.get('generation'), {
            'category': args.get('category'), 'model': args.get('model'),
            'model_fingerprint': args.get('model_fingerprint'), 'reason': args.get('reason'),
            'load_outcome': lambda started: peek_side_outcome(
                state_dir, identity, str(args.get('agent_id') or ''), str(receipt_id or ''), not_before=started),
            'retry_after_seconds': args.get('retry_after_seconds'),
            'side_model': _side_model_configured(config),
            'distinct_parent_model': lambda outcome: _distinct_billing_parent(config, outcome)})
        response = {'receipt': receipt, 'main_fallback_eligible': bool((receipt.get('side_failure') or {}).get('main_fallback_eligible'))}
        if (receipt.get('side_failure') or {}).get('quarantined'):
            # Immediate host delivery; claim/status also offer it for crash recovery.
            from .side_forward import text as forward_text
            row = SideIntakeInbox(state_dir, identity)._load(receipt['receipt_id'])
            if row and row.get('main_forward'):
                response['main_forward'] = {'receipt_id': receipt['receipt_id'],
                                            'text': forward_text(receipt['receipt_id'], row)}
        return response
    if name == 'work_side_handoff':
        from .side_handoff import acknowledge
        if config.get('work_mode', True) is not True:
            raise WorkPlanError('Work is off; handoff mutation denied')
        if type(args.get('expected_version')) is not int:
            raise WorkPlanError('expected Work version required')
        raw_event_id = args.get('event_id')
        handoffs = (store.read_snapshot().get('side_handoffs') or {})
        event_id = raw_event_id
        if isinstance(raw_event_id, str) and raw_event_id not in handoffs:
            if 12 <= len(raw_event_id) < 64 and all(c in '0123456789abcdef' for c in raw_event_id):
                matches = [key for key in handoffs if key.startswith(raw_event_id)]
                if len(matches) == 1:
                    event_id = matches[0]
                elif len(matches) > 1:
                    raise WorkPlanError('ambiguous side handoff event_id prefix; supply full 64-char id')
        if not isinstance(event_id, str) or event_id not in handoffs:
            raise WorkPlanError('side handoff not found (ask_main and release notices need no work_side_handoff acknowledgment)')
        if args.get('answer'):
            if not isinstance(args['answer'], str):
                raise WorkPlanError('coordinator answer must be text')
            inbox = SideIntakeInbox(state_dir, identity)
            original = inbox._load(event_id)
            # Same exact text and route _answer_followup writes, so the check cannot pass here and fail there.
            if (original and _handoff_needs_followup(original)
                    and _followup(event_id, args['answer'], original) is None):
                raise WorkPlanError('coordinator follow-up exceeds inbox record capacity; no acknowledgment committed')
        result = acknowledge(store, event_id, answer=args.get('answer', ''),
                             activate=args.get('activate', False), clear_wait=args.get('clear_wait', []),
                             expected_version=args['expected_version'])
        inbox = SideIntakeInbox(state_dir, identity)
        _sync_handoff_receipt(inbox, event_id, result)
        if args.get('answer'):
            _answer_followup(inbox, event_id, args['answer'])
        # Model-visible: never the raw wake attempt, owner fence or receipt.
        from .side_wake import redact_handoff
        public = redact_handoff(event_id, result)
        public.pop('event_id', None)
        return {'event_id': event_id, **public}
    if name == 'work_side_review':
        from .side_review import review
        return review(store, args.get('receipt_ids'), verdict=args.get('verdict'),
                      note=args.get('note', ''), expected_version=args.get('expected_version'))
    if name == 'work_side_ack':
        from .side_ack_outbox import prepare, record
        if args.get('state') == 'prepare':
            return prepare(state_dir, identity, args.get('receipt_id'), config=config)
        return {'receipt': record(state_dir, identity, args.get('receipt_id'), args.get('state'),
                                  attempt_id=host.get('attempt_id'))}
    if name == 'work_side_reply':
        from .side_reply_outbox import prepare, record
        if args.get('state') == 'prepare':
            prepared = prepare(state_dir, identity, args.get('receipt_id'), config=config)
            if prepared.get('state') == 'route_unavailable':
                _unsent_to_main(store, state_dir, identity, args.get('receipt_id'))
            return prepared
        receipt = record(state_dir, identity, args.get('receipt_id'), args.get('state'), attempt_id=host.get('attempt_id'))
        if args.get('state') == 'failed':
            _unsent_to_main(store, state_dir, identity, args.get('receipt_id'))
        return {'receipt': receipt}
    if name == 'work_side_wake':
        from .side_wake import (mark_read, prepare, prepare_prompt, prepare_reappend, reconcile, record,
                                record_prompt, record_reappend, retry, start)
        state, event_id = args.get('state'), args.get('event_id')
        if isinstance(state, str) and state.startswith('notice_'):
            # The released-wait notice of one event receipt: claimed once, on
            # that receipt, before the host appends it (side_notice.py).
            from . import side_notice
            inbox = SideIntakeInbox(state_dir, identity)
            if state == 'notice_prepare':
                return side_notice.prepare(inbox, event_id, args.get('owner_nonce'), text=args.get('text'))
            if state == 'notice_retry':
                # Host UI only: a person confirmed a possibly duplicate re-issue.
                _person_retry_host(host)
                return side_notice.retry(inbox, event_id, attempt_ref=args.get('attempt_ref'),
                                         revision=args.get('revision'),
                                         confirmed=args.get('confirmed') is True)
            return side_notice.record(inbox, event_id, state[len('notice_'):], args.get('attempt_id'),
                                      owner_nonce=args.get('owner_nonce'), receipt=args.get('receipt'))
        if isinstance(state, str) and state.startswith('review_'):
            # Parallel-review delivery sidecar (review_delivery.py): where the
            # review payload was appended and whether main read it. Never the
            # frozen schema-5 finalization journal.
            from .review_delivery import dispatch as review_delivery
            if state == 'review_reappend_retry':
                # Host UI only: a person confirmed a possibly duplicate re-append.
                _person_retry_host(host)
            return review_delivery(state, event_id, args)
        if isinstance(state, str) and state.startswith('prompt_'):
            if state == 'prompt_prepare':
                return prepare_prompt(store, event_id, args.get('owner_nonce'), args.get('receipt'))
            return record_prompt(store, event_id, args.get('attempt_id'), args.get('owner_nonce'),
                                 state[len('prompt_'):])
        if isinstance(state, str) and state.startswith('reappend_'):
            if state == 'reappend_prepare':
                return prepare_reappend(store, event_id, args.get('owner_nonce'), args.get('seen_receipt'))
            if state == 'reappend_retry':
                # Host UI only: a person confirmed a possibly duplicate re-append.
                _person_retry_host(host)
                from .side_wake import retry_reappend
                return retry_reappend(store, event_id, args.get('reappend_ref'), args.get('revision'),
                                      confirmed=args.get('confirmed') is True)
            return record_reappend(store, event_id, args.get('attempt_id'), args.get('owner_nonce'),
                                   state[len('reappend_'):], receipt=args.get('receipt'))
        if state == 'read':
            return mark_read(store, event_id)
        if state == 'prepare':
            return prepare(store, event_id, protocol=args.get('protocol'),
                           owner_nonce=args.get('owner_nonce'), channel=args.get('channel'))
        if state == 'start':
            return start(store, event_id, args.get('attempt_id'), args.get('owner_nonce'), args.get('channel'))
        if state == 'reconcile':
            return reconcile(store, event_id, args.get('attempt_id'), receipt=args.get('receipt'),
                             seen_receipt=args.get('seen_receipt'))
        if state == 'retry':
            _person_retry_host(host)
            return retry(store, event_id, args.get('attempt_ref'), args.get('revision'),
                         confirmed=args.get('confirmed') is True)
        return record(store, event_id, state, args.get('attempt_id'),
                      owner_nonce=args.get('owner_nonce'), receipt=args.get('receipt'),
                      conversation_id=args.get('seen_receipt'))
    if name == 'work_side_claim':
        waiting_reason = None
        document = store.read_snapshot()
        # Acknowledged handoffs are durable history, not a per-poll payload.
        # Prody 2026-11-10: returning all 30 answered rows made every empty claim
        # a 361K/46-page response, so one poll consumed 30-40 seconds. Synchronize
        # and return only rows whose receipt/follow-up still needs recovery.
        import hashlib
        inbox = SideIntakeInbox(state_dir, identity)
        replies = []
        for key, row in document.get('side_handoffs', {}).items():
            if row.get('state') not in ('acknowledged', 'answered'):
                continue
            receipt = inbox._load(key)
            receipt_unsynced = bool(receipt and receipt.get('stage') == 'processed'
                                    and receipt.get('result', {}).get('handoff') != row['state'])
            followup_missing = False
            if row.get('answer') and _handoff_needs_followup(receipt):
                followup_key = hashlib.sha256(('handoff:' + key).encode()).hexdigest()
                followup_missing = inbox._load(followup_key) is None
            if not receipt_unsynced and not followup_missing:
                continue
            from .side_wake import redact_handoff
            reply = redact_handoff(key, row)
            reply['receipt'] = _sync_handoff_receipt(inbox, key, reply) if receipt_unsynced else None
            if followup_missing:
                _answer_followup(inbox, key, row['answer'])
            replies.append(reply)
        # Fresh side mode (KOBE 2026-11-25): enforce strictly 1 active fresh
        # claim at the router boundary as well so an older loaded JS mod or
        # concurrent poll never runs 2-3 side-fresh actors at once.
        fresh_busy = False
        if runtime_side.get('session') == 'fresh':
            import time as _time
            _now = _time.time()
            _owner = args.get('owner')
            for _row in inbox.pending(200, skip=inbox.quarantined_keys()):
                _proc = _row.get('processing') or {}
                if float(_proc.get('lease_until') or 0) > _now and _row.get('stage') == 'received':
                    fresh_busy = True
                    break
        active = None if (waiting_reason or fresh_busy) else claim(
            state_dir, identity, args.get('owner'),
            aging_seconds=config.get('work_side_intake',{}).get('aging_seconds',60),
            model_fingerprint=NATIVE_FORK_FINGERPRINT,
            urgent_only=args.get('urgent_only') is True,
            workflow_events=_workflow_on(config))
        recovered = None
        if active:
            # A board commit can survive a later receipt-write failure. Recover
            # that durable decision, rather than asking AI to decide it again.
            document = store.read_snapshot()
            prior = document.get('side_decisions', {}).get(active['receipt_id'])
            if prior:
                receipt_id = active['receipt_id']
                result = _settle_side_p0(store, identity, receipt_id, {
                    **prior['result'], 'duplicate': True, 'version': document['version']})
                recovered = finish(state_dir, identity, receipt_id, active['generation'], result,
                                   main_notice=_complaint_notice(state_dir, receipt_id, result))
                active = None
            else:
                from .side_workload import project as project_workload, project_active_forks
                from .side_work_decision import unfinished_digest
                active_forks = []
                try:
                    from .work_parallel_mcp import ledger as parallel_ledger
                    active_forks = project_active_forks(parallel_ledger().read(), document, identity)
                except Exception:  # noqa: BLE001 - fork projection is advisory to side claim
                    active_forks = []
                active['workload'] = project_workload(document, active_forks=active_forks)
                # Host-held: returned in _host at resolve, never taken from the model.
                active['work_digest'] = unfinished_digest(document)
                # The decision fields this router accepts: the mod sends no field
                # a router does not list (refs, 2026-11-20, is refused by older ones).
                from .side_work_decision import FIELDS as DECISION_FIELDS
                active['decision_fields'] = sorted(DECISION_FIELDS)
                from .side_reply_connector import source_read
                active['source_read'] = source_read(config, active.get('route', {}), active['text'])
                active['source_context'] = (active['source_read'] or {}).get('classification', 'not_walkie')
                if active['source_context'] == 'thread':
                    from .work_gate_threads import source_bindings
                    active['thread_bindings'] = source_bindings(state_dir, identity, active['source_read'])
                active['source_truncated'] = bool((active['source_read'] or {}).get('event_truncated'))
                # Persist the classification for receipt and Side UI visibility.
                with exclusive_file_lock(inbox._event_path('locks', active['receipt_id'])):
                    source_row = verify_claim(inbox, active['receipt_id'], active['generation'])
                    source_row['source_context'] = active['source_context']
                    if active['source_truncated']:
                        source_row['source_truncated'] = True
                    inbox._save_file(inbox._event_path('pending', active['receipt_id']), source_row)
                # Model-visible (side fork prompt): wake redacted.
                from .side_wake import redact_handoff
                active['handoff_context'] = [redact_handoff(key, row) for key, row in document.get('side_handoffs', {}).items()
                                             if active['event_key'] == 'handoff:' + key]
                # Approval workflow: an approval/rejection of a registered
                # request is settled by host code (work_side_workflow_event),
                # never by a side model (spec 4.4).
                from .work_workflow_tools import claim_workflow_event
                marked = claim_workflow_event(config, document, active.get('text'), store)
                if marked:
                    active['workflow_event'] = marked
        # Intent may be adopted (same owner) or superseded after its lease;
        # started is offered only so its owner can settle an append receipt.
        # The host claim keeps the raw attempt id it needs for that; the owner
        # fence stays with its module instance and is never claim data.
        # An appended wake is offered until main has had a request after it
        # (wake.read_at): the row is in main's conversation but an append
        # starts no turn, so a host may still have to start one. That prompt
        # holds no event data.
        # Nested prompt/reappend leases carry their own owner fences and are
        # backend-only: a claim reader must not be able to record for them.
        main_wakes = [{'event_id': key, **row,
                       'wake': {k: v for k, v in (row.get('wake') or {}).items()
                                if k not in ('owner_nonce', 'prompt', 'reappend')}}
                      for key, row in document.get('side_handoffs', {}).items()
                      if row.get('state') == 'requested'
                      and ((row.get('wake') or {}).get('state') in ('pending', 'failed')
                           or ((row.get('wake') or {}).get('state') == 'submitting'
                               and (row.get('wake') or {}).get('phase') in ('intent', 'started'))
                           or ((row.get('wake') or {}).get('state') == 'appended'
                               and not (row.get('wake') or {}).get('read_at')))]
        from .side_reply_outbox import pending
        settings = {k: v for k, v in runtime_side.items() if k != 'reply_connectors'}
        settings['actor'] = 'native_fork'
        from .work_workflow_tools import side_instruction
        settings['instruction'] = side_instruction(config, state_dir, settings.get('instruction'))
        # The blocker-check actor runs on its own instruction (managed prompt
        # gate_actor), not on the SSE side instruction.
        # It travels only with a gate re-check claim: on every claim it pushed
        # the response over the page size (24 kB) and paged all claims.
        if isinstance(active, dict) and (active.get('route') or {}).get('source_kind') == 'gate_check':
            from .work_workflow_prompts import effective as _prompt_text
            settings['gate_instruction'] = '\n\n'.join(_prompt_text(state_dir, name) for name in
                                                      ('gate_actor', 'gate_verification_contract'))
        # wake_protocol 2: main_wakes lists every appended row main has not read.
        # main_notices: released-wait notices a host may issue once more (a
        # definite failure, or a person-confirmed release); each is claimed
        # again through notice_prepare, so at most one host re-issues it.
        from . import side_notice
        from .side_forward import offered as forwards_offered
        # main_forwards: originals of events the side could not process; each is
        # claimed through notice_prepare, so one host appends it to main once.
        from .side_ack_outbox import pending as acks_owed
        return {'claim': active, 'waiting_reason': waiting_reason, 'recovered_receipt': recovered,
                'settings': settings, 'handoff_replies': replies, 'main_wakes': main_wakes,
                'main_notices': side_notice.offered(inbox),
                'main_forwards': forwards_offered(inbox),
                # Receipt ACKs still owed, including events that wait for a
                # slot; the host sends them before and apart from any claim.
                'pending_acks': acks_owed(state_dir, identity),
                'wake_protocol': 2, 'pending_replies': pending(state_dir, identity)}
    key, generation = host.get('receipt_id'), host.get('generation')
    if name == 'work_side_understanding':
        # Step 2 of an event: persist the actor's proposal (understanding +
        # decision), then send the understanding before the decision commits.
        from .side_understanding_outbox import prepare as understanding_prepare, record as understanding_record
        from .side_understanding_outbox import save_proposal
        if args.get('state') == 'prepare':
            decision = args.get('decision')
            understanding = args.get('understanding')
            # KOBE 2026-11-25 (68f98974, d548fdd3): an older loaded JS mod sends
            # the raw model proposal straight to the router. Recover the two
            # harmless omissions here too: missing reply on no_reply=true, and
            # missing understanding when summary is non-empty (still checked
            # against ACK/echo in save_proposal).
            if isinstance(decision, dict):
                decision = dict(decision)
                summary = decision.get('summary')
                if (decision.get('no_reply') is True
                        and (not isinstance(decision.get('reply'), str) or not decision['reply'].strip())
                        and isinstance(summary, str) and summary.strip()):
                    decision['reply'] = ' '.join(summary.split())[:300]
                if ((not isinstance(understanding, str) or not understanding.strip())
                        and isinstance(summary, str) and summary.strip()):
                    understanding = ' '.join(summary.split())[:400]
            save_proposal(state_dir, identity, key, generation, {
                'understanding': understanding, 'decision': decision,
                'expected_version': args.get('expected_version')})
            return understanding_prepare(state_dir, identity, key, config=config)
        return {'receipt': understanding_record(state_dir, identity, key, args.get('state'),
                                                attempt_id=host.get('attempt_id'))}
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', key)):
        row = inbox._load(key)
        if row and row.get('stage') == 'processed' and row.get('processing', {}).get('generation') == generation:
            return {'receipt': inbox._receipt(key, row, duplicate=True), 'result': row.get('result')}
        verify_claim(inbox, key, generation)
        saved = (row or {}).get('proposal')
        if isinstance(saved, dict):
            # The understanding step ran: commit exactly the saved proposal,
            # whatever this call carries (a resumed claim sends none).
            if not isinstance((row or {}).get('understanding_outbox'), dict):
                raise WorkPlanError('side understanding must be prepared before the decision commits')
            decision = json.loads(json.dumps(saved['decision']))
            args = {**args, 'decision': decision, 'expected_version': saved.get('expected_version')}
        decision = args.get('decision')
        if not isinstance(decision, dict):
            raise WorkPlanError('side decision object required')
        if config.get('work_mode', True) is not True and decision.get('complaint') is True:
            # Work off has no queue to register in; the complaint still reaches main.
            decision = {**{k: v for k, v in decision.items() if k != 'complaint'}, 'ask_main': True}
            args = {**args, 'decision': decision}
        from .side_intake_inbox import source_identity as _resolve_source_identity
        from .work_gate_check import EVENT_PREFIX as GATE_CHECK_PREFIX
        route = (row or {}).get('route') or {}
        gate_check = (route.get('source_kind') == 'gate_check'
                      and str((row or {}).get('event_key') or '').startswith(GATE_CHECK_PREFIX))
        gate_pins = route.get('gate_pins') if gate_check else None
        # A saved proposal passed the refs check when it was saved; one saved by
        # an older router (before refs, 2026-11-20) is final and still commits.
        source_has_ids = (_resolve_source_identity((row or {}).get('text')) is not None and not gate_check
                          and not isinstance(saved, dict))
        validate(decision, source_has_ids=source_has_ids, strict_releases=not isinstance(saved, dict))
        if config.get('work_mode', True) is not True and decision.get('action') not in ('answer', 'clarify'):
            raise WorkPlanError('Work is off; answering allowed, queue mutation denied')
        # A handoff follow-up carries main's own answer back to the source. Its
        # decision never asks main again (owned proof 2026-11-08: 4 cycles in 5 min).
        main_reply = str((row or {}).get('event_key') or '').startswith('handoff:')
        # One recheck per event: a second board change commits on the current board.
        observed = host.get('work_digest') if not (row or {}).get('rechecks') else None
        changed = None
        parked, ledger_path = _parked_projection(store, identity, decision)
        try:
            from .side_intake_inbox import source_identity, source_priority
            from .work_workflow_tools import side_workflow_context
            original = (row or {}).get('text')
            p0_roots = (_active_p0_roots(identity) if decision.get('action') in ('update', 'prioritize')
                        else frozenset())
            result = apply(store, key, decision, args.get('expected_version'), main_reply=main_reply,
                           observed_digest=observed if isinstance(observed, str) else None,
                           source=source_identity(original), source_priority=source_priority(original),
                           p0_roots=p0_roots, source_context=(row or {}).get('source_context'),
                           parked_projection=parked, gate_check=gate_check, gate_pins=gate_pins,
                           source_has_ids=source_has_ids,
                           workflow=side_workflow_context(config, state_dir, row))
        except WorkChanged as error:
            changed = str(error)
        if changed is None:
            _settle_side_p0(store, identity, key, result)
        if changed is None and result.get('wait_boost') and ledger_path is not None:
            # After the board commit; a duplicate replay recreates a missing row.
            result['wait_boost_record'] = _record_wait_boost(ledger_path, identity, key, result)
        if changed is None:
            result['usage'] = args.get('usage') if isinstance(args.get('usage'), dict) else None
            # Legacy decisions only: what the side AI says it sent itself. New
            # actors have no send tool; the host sends final_text.
            result['reported_sends'] = _reported_sends(host.get('reported_sends'))
    if changed is not None:
        # Decided "new" against goals main has since changed: decide again once.
        from .side_intake_processing import release_for_recheck
        return {'receipt': release_for_recheck(state_dir, identity, key, generation, changed),
                'recheck': 'work_changed'}
    # The board records the decision before pending mail is removed. A retry
    # recovers the same application if this receipt write was interrupted.
    main_notice = _complaint_notice(state_dir, key, result)
    receipt = finish(state_dir, identity, key, generation, result, main_notice=main_notice)
    # Gate re-check thread cursors (Andy 2026-11-18): every committed event of a
    # Walkie thread advances that thread's cursor, and a node the decision
    # created or updated is bound to the thread. The thread is the host's own
    # classification of the event, never the model's. Advisory: a failed write
    # only makes the next gate check read like a gate without a thread.
    try:
        from .side_reply_connector import source_read as event_source
        from .work_gate_threads import record_event
        record_event(state_dir, identity, event_source(config, (row or {}).get('route', {}) or {}, (row or {}).get('text')),
                     result)
    except Exception:  # noqa: BLE001
        pass
    return {'receipt': receipt, 'result': result, **({'main_notice': main_notice} if main_notice else {})}
