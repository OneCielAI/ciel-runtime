"""Host-scoped claim and decision tools for the independent side actor."""
from .side_intake_inbox import SideIntakeInbox
from .side_intake_processing import claim, finish, verify_claim
from .side_work_decision import apply
from .work_plan_tree import WorkPlanError
from .channel_message_repository import exclusive_file_lock

NAMES = ('work_side_claim', 'work_side_resolve', 'work_side_reply', 'work_side_handoff', 'work_side_renew', 'work_side_retry_reply', 'work_side_context', 'work_side_compare')


def schemas():
    return [
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
        {'name':'work_side_handoff','description':'Acknowledge side advice at a safe main execution boundary; selecting an existing goal never approves it.',
         'inputSchema':{'type':'object','properties':{'event_id':{'type':'string'},'answer':{'type':'string'},
             'activate':{'type':'boolean'},'expected_version':{'type':'integer','minimum':0}},'required':['event_id','expected_version']}},
        {'name': 'work_side_reply', 'description': 'Host-only prepare or record a correlated side reply; submission is not proof of human delivery.',
         'inputSchema': {'type': 'object', 'properties': {'receipt_id': {'type': 'string'},
             'state': {'type': 'string', 'enum': ['prepare', 'submitted', 'uncertain', 'failed']}}, 'required': ['receipt_id', 'state']}},
        {'name': 'work_side_claim', 'description': 'Host-only: claim one durable request for immediate independent AI processing, without creating Work.',
         'inputSchema': {'type': 'object', 'properties': {'owner': {'type': 'string', 'maxLength': 200}}, 'required': ['owner']}},
        {'name': 'work_side_resolve', 'description': 'Resolve one host-claimed external event: answer, clarify, update existing Work, create new Work, prioritize or advise. Never execution approval.',
         'inputSchema': {'type': 'object', 'properties': {
             'decision': {'type': 'object', 'properties': {
                 'action': {'type': 'string', 'enum': ['answer', 'clarify', 'update', 'create', 'prioritize', 'advise']},
                 'item_id': {'type': 'string'}, 'summary': {'type': 'string'}, 'reply': {'type': 'string'},
                 'note': {'type': 'string'}, 'title': {'type': 'string'},
                 'priority': {'type': 'string', 'enum': ['', 'P1', 'P2', 'P3', 'P4', 'P5']}},
                 'required': ['action', 'summary', 'reply'], 'additionalProperties': False},
             'expected_version': {'type': 'integer', 'minimum': 0}}, 'required': ['decision', 'expected_version']}}
    ]


def _answer_followup(inbox, event_id, answer):
    original = inbox._load(event_id)
    if original:
        followup = ('Coordinator answer to side question ' + event_id + ':\n'
                    + answer + '\nOriginal request:\n' + original['text'])
        if len(followup) > 16000:
            raise WorkPlanError('coordinator follow-up exceeds inbox text capacity; answer retained in Work')
        inbox.receive(followup, 'handoff:' + event_id, route=original.get('route', {}))


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
    host, _ = host_context(args, config)
    if host.get('agent_id') is not None:
        raise WorkPlanError('side coordinator bridge required')
    if config.get('work_side_intake', {}).get('enabled') is not True:
        raise WorkPlanError('side processing is disabled')
    if name == 'work_side_compare':
        from . import side_comparison
        settings = config['work_side_intake']
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
        return SideContextStore(state_dir, identity).prepare(
            {'receipt_id': key, 'generation': generation}, config['work_side_intake'],
            args.get('model'), key=args.get('context_key'))
    if name == 'work_side_retry_reply':
        from .side_reply_outbox import retry
        return {'receipt': retry(state_dir, identity, args.get('receipt_id'),
                                 args.get('expected_state'), args.get('confirmed', False))}
    if name == 'work_side_renew':
        from .side_intake_processing import renew
        return renew(state_dir, identity, host.get('receipt_id'), host.get('generation'))
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
            if original and len(args['answer']) + len(original['text']) + 150 > 16000:
                raise WorkPlanError('coordinator follow-up exceeds inbox text capacity; no acknowledgment committed')
        result = acknowledge(store, args.get('event_id'), answer=args.get('answer', ''),
                             activate=args.get('activate', False), expected_version=args['expected_version'])
        inbox = SideIntakeInbox(state_dir, identity)
        _sync_handoff_receipt(inbox, args['event_id'], result)
        if args.get('answer'):
            _answer_followup(inbox, args['event_id'], args['answer'])
        return result
    if name == 'work_side_reply':
        from .side_reply_outbox import prepare, record
        if args.get('state') == 'prepare':
            return prepare(state_dir, identity, args.get('receipt_id'), config=config)
        return {'receipt': record(state_dir, identity, args.get('receipt_id'), args.get('state'), attempt_id=host.get('attempt_id'))}
    if name == 'work_side_claim':
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
        active = claim(state_dir, identity, args.get('owner'),
                       aging_seconds=config.get('work_side_intake',{}).get('aging_seconds',60))
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
                active['workload'] = project_workload(document)
                active['handoff_context'] = [{'event_id': key, **row} for key, row in document.get('side_handoffs', {}).items()
                                             if active['event_key'] == 'handoff:' + key]
        from .side_reply_outbox import pending
        return {'claim': active, 'recovered_receipt': recovered,
                'settings': {k: v for k, v in config.get('work_side_intake', {}).items() if k != 'reply_connectors'}, 'handoff_replies': replies,
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
        result = apply(store, key, decision, args.get('expected_version'))
        if context_info is not None:
            result['context'] = context_info
        result['usage'] = args.get('usage') if isinstance(args.get('usage'), dict) else None
    # The board records the decision before pending mail is removed. A retry
    # recovers the same application if this receipt write was interrupted.
    receipt = finish(state_dir, identity, key, generation, result)
    return {'receipt': receipt, 'result': result}
