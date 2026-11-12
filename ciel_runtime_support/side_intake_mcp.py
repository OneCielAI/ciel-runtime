"""Host-scoped claim and decision tools for the independent side actor."""
from .side_intake_inbox import SideIntakeInbox
from .side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS, side_text_fits_pending_record
from .side_intake_processing import claim, fail, fallback, finish, verify_claim
from .side_work_decision import WorkChanged, apply
from .work_plan_tree import WorkPlanError
from .channel_message_repository import exclusive_file_lock

NATIVE_FORK_FINGERPRINT = 'native-fork'
NAMES = ('work_side_claim', 'work_side_resolve', 'work_side_fail', 'work_side_fallback', 'work_side_reply', 'work_side_wake', 'work_side_handoff', 'work_side_review', 'work_side_renew', 'work_side_retry_reply', 'work_side_code_read', 'work_side_code_search')


def schemas():
    return [
        {'name': 'work_side_fail', 'description': 'Host-only generation-fenced failure release with durable backoff or model quarantine.',
         'inputSchema': {'type':'object','properties':{'category':{'type':'string','maxLength':80},
             'model':{'type':'string','maxLength':300},'model_fingerprint':{'type':'string','maxLength':128},
             'agent_id':{'type':'string','maxLength':200},'reason':{'type':'string','maxLength':300}},
             'required':['category']}},
        {'name': 'work_side_fallback', 'description': 'Host-only generation-fenced transition of a side-model failure to exact-once main-session delivery.',
         'inputSchema': {'type':'object','properties':{'state':{'type':'string','enum':['prepare','queued','failed']},
             'reason':{'type':'string','maxLength':160},'agent_id':{'type':'string','maxLength':200}},'required':['state']}},
        {'name': 'work_side_retry_reply', 'description': 'Host UI only: explicitly retry failed or unknown side reply; unknown outcomes require person confirmation.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'expected_state': {'type': 'string'}, 'confirmed': {'type': 'boolean'}},
             'required': ['receipt_id', 'expected_state']}},
        {'name': 'work_side_renew', 'description': 'Host-only heartbeat for one live side actor; never revives an expired generation.',
         'inputSchema': {'type': 'object', 'properties': {}}},
        {'name':'work_side_handoff','description':'Acknowledge side advice at a safe main execution boundary; selecting existing work or clearing a recorded wait never approves new execution.',
         'inputSchema':{'type':'object','properties':{'event_id':{'type':'string'},'answer':{'type':'string'},
             'activate':{'type':'boolean'},'clear_wait':{'type':'array','items':{'type':'string'},'uniqueItems':True},
             'expected_version':{'type':'integer','minimum':0}},'required':['event_id','expected_version']}},
        {'name':'work_side_review','description':'Main: mark external messages the side actor already answered as reviewed. Do not answer or ACK them again; '
             'only when a side reply was wrong, send one correction to the same source, then use verdict=corrected with a note.',
         'inputSchema':{'type':'object','properties':{'receipt_ids':{'type':'array','items':{'type':'string'},'uniqueItems':True,
             'minItems':1,'maxItems':50},'verdict':{'type':'string','enum':['ok','corrected']},'note':{'type':'string','maxLength':2000},
             'expected_version':{'type':'integer','minimum':0}},'required':['receipt_ids','verdict','expected_version']}},
        {'name': 'work_side_reply', 'description': 'Host-only prepare or record a correlated side reply; submission is not proof of human delivery.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'submitted', 'uncertain', 'failed']}}, 'required': ['receipt_id', 'state']}},
        {'name': 'work_side_wake', 'description': 'Host-only prepare or record one durable main wake attempt.',
         'inputSchema': {'type': 'object', 'properties': {'event_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'start', 'reconcile', 'retry',
                                                   'queued', 'appended', 'uncertain', 'failed']},
             'attempt_id': {'type': 'string'}, 'protocol': {'type': 'integer', 'enum': [2]},
             'owner_nonce': {'type': 'string', 'maxLength': 200},
             'channel': {'type': 'string', 'enum': ['prompt', 'append', 'stop']},
             'receipt': {'type': 'string', 'maxLength': 300}, 'seen_receipt': {'type': 'string', 'maxLength': 300},
             'revision': {'type': 'integer', 'minimum': 0}, 'confirmed': {'type': 'boolean'},
             'attempt_ref': {'type': 'string', 'maxLength': 16}},
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
        {'name': 'work_side_resolve', 'description': 'Resolve one host-claimed external event: answer status/info, clarify, or queue a request (create, update or reprioritize Work). Never execution approval.',
         'inputSchema': {'type': 'object', 'properties': {
             'decision': {'type': 'object', 'properties': {
                 'action': {'type': 'string', 'enum': ['answer', 'clarify', 'update', 'create', 'prioritize', 'advise']},
                 'kind': {'type': 'string', 'enum': ['status', 'info', 'request', 'clarify']},
                 'item_id': {'type': 'string'}, 'summary': {'type': 'string'}, 'reply': {'type': 'string'},
                 'note': {'type': 'string'}, 'title': {'type': 'string'},
                 'priority': {'type': 'string', 'enum': ['', 'P1', 'P2', 'P3', 'P4', 'P5']},
                 'queue_position': {'type': 'integer', 'minimum': 1},
                 'release_waits': {'type': 'array', 'maxItems': 16, 'items': {
                     'type': 'object', 'properties': {'id': {'type': 'string'}, 'waiting_on': {'type': 'string'}},
                     'required': ['id', 'waiting_on'], 'additionalProperties': False}},
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
        return {'receipt': retry(state_dir, identity, args.get('receipt_id'),
                                 args.get('expected_state'), args.get('confirmed', False))}
    if name == 'work_side_renew':
        from .side_intake_processing import renew
        return renew(state_dir, identity, host.get('receipt_id'), host.get('generation'))
    if name == 'work_side_fail':
        from .side_request_outcome import take as take_side_outcome
        outcome = take_side_outcome(state_dir, identity, str(args.get('agent_id') or ''),
                                    str(host.get('receipt_id') or ''))
        receipt = fail(state_dir, identity, host.get('receipt_id'), host.get('generation'), {
            'category': args.get('category'), 'model': args.get('model'),
            'model_fingerprint': args.get('model_fingerprint'), 'reason': args.get('reason'), 'outcome': outcome})
        return {'receipt': receipt, 'main_fallback_eligible': bool((receipt.get('side_failure') or {}).get('main_fallback_eligible'))}
    if name == 'work_side_handoff':
        from .side_handoff import acknowledge
        if config.get('work_mode', True) is not True:
            raise WorkPlanError('Work is off; handoff mutation denied')
        if type(args.get('expected_version')) is not int:
            raise WorkPlanError('expected Work version required')
        if args.get('answer'):
            if not isinstance(args['answer'], str):
                raise WorkPlanError('coordinator answer must be text')
            inbox = SideIntakeInbox(state_dir, identity)
            original = inbox._load(args.get('event_id'))
            # Same exact text and route _answer_followup writes, so the check cannot pass here and fail there.
            if (original and _handoff_needs_followup(original)
                    and _followup(args['event_id'], args['answer'], original) is None):
                raise WorkPlanError('coordinator follow-up exceeds inbox record capacity; no acknowledgment committed')
        result = acknowledge(store, args.get('event_id'), answer=args.get('answer', ''),
                             activate=args.get('activate', False), clear_wait=args.get('clear_wait', []),
                             expected_version=args['expected_version'])
        inbox = SideIntakeInbox(state_dir, identity)
        _sync_handoff_receipt(inbox, args['event_id'], result)
        if args.get('answer'):
            _answer_followup(inbox, args['event_id'], args['answer'])
        # Model-visible: never the raw wake attempt, owner fence or receipt.
        from .side_wake import redact_handoff
        public = redact_handoff(args['event_id'], result)
        public.pop('event_id', None)
        return {'event_id': args['event_id'], **public}
    if name == 'work_side_review':
        from .side_review import review
        return review(store, args.get('receipt_ids'), verdict=args.get('verdict'),
                      note=args.get('note', ''), expected_version=args.get('expected_version'))
    if name == 'work_side_reply':
        from .side_reply_outbox import prepare, record
        if args.get('state') == 'prepare':
            return prepare(state_dir, identity, args.get('receipt_id'), config=config)
        return {'receipt': record(state_dir, identity, args.get('receipt_id'), args.get('state'), attempt_id=host.get('attempt_id'))}
    if name == 'work_side_wake':
        from .side_wake import prepare, reconcile, record, retry, start
        state, event_id = args.get('state'), args.get('event_id')
        if state == 'prepare':
            return prepare(store, event_id, protocol=args.get('protocol'),
                           owner_nonce=args.get('owner_nonce'), channel=args.get('channel'))
        if state == 'start':
            return start(store, event_id, args.get('attempt_id'), args.get('owner_nonce'), args.get('channel'))
        if state == 'reconcile':
            return reconcile(store, event_id, args.get('attempt_id'), receipt=args.get('receipt'),
                             seen_receipt=args.get('seen_receipt'))
        if state == 'retry':
            return retry(store, event_id, args.get('attempt_ref'), args.get('revision'),
                         confirmed=args.get('confirmed') is True)
        return record(store, event_id, state, args.get('attempt_id'),
                      owner_nonce=args.get('owner_nonce'), receipt=args.get('receipt'))
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
        active = None if waiting_reason else claim(
            state_dir, identity, args.get('owner'),
            aging_seconds=config.get('work_side_intake',{}).get('aging_seconds',60),
            model_fingerprint=NATIVE_FORK_FINGERPRINT,
            urgent_only=args.get('urgent_only') is True)
        recovered = None
        if active:
            # A board commit can survive a later receipt-write failure. Recover
            # that durable decision, rather than asking AI to decide it again.
            document = store.read_snapshot()
            prior = document.get('side_decisions', {}).get(active['receipt_id'])
            if prior:
                recovered = finish(state_dir, identity, active['receipt_id'], active['generation'],
                                   {**prior['result'], 'duplicate': True, 'version': document['version']})
                active = None
            else:
                from .side_workload import project as project_workload
                from .side_work_decision import unfinished_digest
                active['workload'] = project_workload(document)
                # Host-held: returned in _host at resolve, never taken from the model.
                active['work_digest'] = unfinished_digest(document)
                from .side_reply_connector import source_read
                active['source_read'] = source_read(config, active.get('route', {}), active['text'])
                active['source_context'] = (active['source_read'] or {}).get('classification', 'not_walkie')
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
        # Intent may be adopted (same owner) or superseded after its lease;
        # started is offered only so its owner can settle an append receipt.
        # The host claim keeps the raw attempt id it needs for that; the owner
        # fence stays with its module instance and is never claim data.
        main_wakes = [{'event_id': key, **row,
                       'wake': {k: v for k, v in (row.get('wake') or {}).items() if k != 'owner_nonce'}}
                      for key, row in document.get('side_handoffs', {}).items()
                      if row.get('state') == 'requested'
                      and ((row.get('wake') or {}).get('state') in ('pending', 'failed')
                           or ((row.get('wake') or {}).get('state') == 'submitting'
                               and (row.get('wake') or {}).get('phase') in ('intent', 'started')))]
        from .side_reply_outbox import pending
        settings = {k: v for k, v in runtime_side.items() if k != 'reply_connectors'}
        settings['actor'] = 'native_fork'
        return {'claim': active, 'waiting_reason': waiting_reason, 'recovered_receipt': recovered,
                'settings': settings, 'handoff_replies': replies, 'main_wakes': main_wakes,
                'pending_replies': pending(state_dir, identity)}
    key, generation = host.get('receipt_id'), host.get('generation')
    inbox = SideIntakeInbox(state_dir, identity)
    with exclusive_file_lock(inbox._event_path('locks', key)):
        row = inbox._load(key)
        if row and row.get('stage') == 'processed' and row.get('processing', {}).get('generation') == generation:
            return {'receipt': inbox._receipt(key, row, duplicate=True), 'result': row.get('result')}
        verify_claim(inbox, key, generation)
        decision = args.get('decision')
        if not isinstance(decision, dict):
            raise WorkPlanError('side decision object required')
        if config.get('work_mode', True) is not True and decision.get('action') not in ('answer', 'clarify'):
            raise WorkPlanError('Work is off; answering allowed, queue mutation denied')
        # A handoff follow-up carries main's own answer back to the source. Its
        # decision never asks main again (owned proof 2026-11-08: 4 cycles in 5 min).
        main_reply = str((row or {}).get('event_key') or '').startswith('handoff:')
        # One recheck per event: a second board change commits on the current board.
        observed = host.get('work_digest') if not (row or {}).get('rechecks') else None
        changed = None
        try:
            from .side_intake_inbox import source_identity
            result = apply(store, key, decision, args.get('expected_version'), main_reply=main_reply,
                           observed_digest=observed if isinstance(observed, str) else None,
                           source=source_identity((row or {}).get('text')),
                           source_context=(row or {}).get('source_context'))
        except WorkChanged as error:
            changed = str(error)
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
    receipt = finish(state_dir, identity, key, generation, result)
    return {'receipt': receipt, 'result': result}
