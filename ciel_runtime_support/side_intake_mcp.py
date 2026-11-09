"""Host-scoped claim and decision tools for the independent side actor."""
from .side_intake_inbox import SideIntakeInbox
from .side_intake_limits import SIDE_INTAKE_TEXT_MAX_CHARS, side_text_fits_pending_record
from .side_intake_processing import claim, fail, fallback, finish, verify_claim
from .side_work_decision import WorkChanged, apply
from .work_plan_tree import WorkPlanError
from .channel_message_repository import exclusive_file_lock

NAMES = ('work_side_claim', 'work_side_resolve', 'work_side_fail', 'work_side_fallback', 'work_side_reply', 'work_side_wake', 'work_side_handoff', 'work_side_review', 'work_side_renew', 'work_side_retry_reply', 'work_side_context', 'work_side_compare', 'work_side_code_read', 'work_side_code_search')


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
        {'name': 'work_side_compare', 'description': 'Host-only read-only profile experiment; never commits Work or sends a reply.',
         'inputSchema': {'type':'object','properties':{'action':{'type':'string','enum':['plan','context','record']},
             'receipt_id':{'type':'string'},'parent_model':{'type':'string'},'profile_id':{'type':'string'},
             'result':{'type':'object'}},'required':['action','receipt_id','parent_model']}},
        {'name': 'work_side_context', 'description': 'Host-only bind a live side event to a captured parent request; full or provider-token-selected history.',
         'inputSchema': {'type': 'object', 'properties': {'model': {'type': 'string'}, 'context_key': {'type': 'string'}}, 'required': ['model']}},
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
             'state': {'type': 'string', 'enum': ['prepare', 'queued', 'uncertain', 'failed']},
             'attempt_id': {'type': 'string'}}, 'required': ['event_id', 'state']}},
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
                 'queue_position': {'type': 'integer', 'minimum': 1}},
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


def _answer_followup(inbox, event_id, answer):
    original = inbox._load(event_id)
    if original:
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
    if name == 'work_side_compare':
        from . import side_comparison
        settings = runtime_side
        profiles = side_comparison.plan(state_dir, identity, args.get('receipt_id'), args.get('parent_model'), settings)
        if args.get('action') == 'plan':
            from .side_workload import project as project_workload
            frozen = side_comparison.freeze(state_dir, identity, args['receipt_id'], project_workload(store.read_snapshot()))
            return {'profiles': profiles, 'workload': frozen['workload']}
        if args.get('profile_id') not in {row['profile_id'] for row in profiles}:
            raise WorkPlanError('unknown side comparison profile')
        if args.get('action') == 'context':
            return side_comparison.context(state_dir, identity, args['receipt_id'], args['profile_id'], args['parent_model'], settings)
        if args.get('action') == 'record':
            return {'receipt': side_comparison.record(state_dir, identity, args['receipt_id'], args['profile_id'], args.get('result'))}
        raise WorkPlanError('invalid comparison action')
    if name == 'work_side_context':
        from .side_context_store import SideContextStore
        key, generation = host.get('receipt_id'), host.get('generation')
        inbox = SideIntakeInbox(state_dir, identity)
        with exclusive_file_lock(inbox._event_path('locks', key)):
            verify_claim(inbox, key, generation)
        prepared = SideContextStore(state_dir, identity).prepare(
            {'receipt_id': key, 'generation': generation}, runtime_side,
            args.get('model'), key=args.get('context_key'))
        with exclusive_file_lock(inbox._event_path('locks', key)):
            row = verify_claim(inbox, key, generation)
            row['context'] = {'mode': prepared['profile']['mode'],
                              'captured_at': prepared['captured_at'],
                              'parent_digest': prepared['parent_digest'],
                              'preview': prepared.get('parent_transcript_preview', [])}
            inbox._save_file(inbox._event_path('pending', key), row)
        return prepared
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
            if original and _followup(args['event_id'], args['answer'], original) is None:
                raise WorkPlanError('coordinator follow-up exceeds inbox record capacity; no acknowledgment committed')
        result = acknowledge(store, args.get('event_id'), answer=args.get('answer', ''),
                             activate=args.get('activate', False), clear_wait=args.get('clear_wait', []),
                             expected_version=args['expected_version'])
        inbox = SideIntakeInbox(state_dir, identity)
        _sync_handoff_receipt(inbox, args['event_id'], result)
        if args.get('answer'):
            _answer_followup(inbox, args['event_id'], args['answer'])
        return result
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
        from .side_wake import prepare, record
        if args.get('state') == 'prepare':
            return prepare(store, args.get('event_id'))
        return record(store, args.get('event_id'), args.get('state'), args.get('attempt_id'))
    if name == 'work_side_claim':
        waiting_reason = None
        context_ready = False
        if runtime_side.get('context_mode', 'full') in ('full', 'recent'):
            from .side_context_store import SideContextStore
            context_ready = SideContextStore(state_dir, identity).ready() is not None
            # A selected model promised an explicit parent projection. Inherit
            # may safely fall back to isolated triage before the first capture.
            if runtime_side.get('model') != 'inherit' and not context_ready:
                waiting_reason = 'parent_context'
        document = store.read_snapshot()
        replies = [{'event_id': key, **row} for key, row in document.get('side_handoffs', {}).items()
                   if row.get('state') in ('acknowledged', 'answered')]
        # The acknowledgment is the durable outbox intent if a crash prevented
        # writing its follow-up event. Repair missing mail before claiming it.
        import hashlib
        inbox = SideIntakeInbox(state_dir, identity)
        for reply in replies:
            reply['receipt'] = _sync_handoff_receipt(inbox, reply['event_id'], reply)
            if reply.get('answer'):
                followup_key = hashlib.sha256(('handoff:' + reply['event_id']).encode()).hexdigest()
                if inbox._load(followup_key) is None:
                    _answer_followup(inbox, reply['event_id'], reply['answer'])
        active = None if waiting_reason else claim(
            state_dir, identity, args.get('owner'),
            aging_seconds=config.get('work_side_intake',{}).get('aging_seconds',60),
            model_fingerprint=runtime_side.get('model_fingerprint'),
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
                active['handoff_context'] = [{'event_id': key, **row} for key, row in document.get('side_handoffs', {}).items()
                                             if active['event_key'] == 'handoff:' + key]
        main_wakes = [{'event_id': key, **row} for key, row in document.get('side_handoffs', {}).items()
                      if row.get('state') == 'requested'
                      and (row.get('wake') or {}).get('state') in ('pending', 'failed')]
        from .side_reply_outbox import pending
        settings = {k: v for k, v in runtime_side.items() if k != 'reply_connectors'}
        settings['context_ready'] = context_ready
        if active and active.get('use_parent_model'):
            # Same provider connection; only this event moves to the main model.
            settings.update(model='inherit', parent_model_retry=True)
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
        context_info = None
        if host.get('context_key'):
            from .side_context_store import SideContextStore
            contexts = SideContextStore(state_dir, identity)
            context = contexts.read(contexts.path(host['context_key']))
            if not context or context.get('receipt_id') != key or context.get('generation') != generation or not context.get('selection'):
                raise WorkPlanError('side context projection not verified for this event')
            context_info = {**context['selection'], 'parent_captured_at': context['captured_at'],
                            'parent_digest': context['digest']}
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
                           source=source_identity((row or {}).get('text')))
        except WorkChanged as error:
            changed = str(error)
        if changed is None:
            if context_info is not None:
                result['context'] = context_info
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
