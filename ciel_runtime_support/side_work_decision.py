"""Apply a side actor's decision once, without granting execution approval.

A side event is either answered (status/info/clarify, no Work change) or a
request (create/update/prioritize Work). The host, never the model, states
the outcome: whether anything was queued and where the goal now stands.
"""
import hashlib
import json
import time

from . import work_plan_tree as tree

ACTIONS = ('answer', 'clarify', 'update', 'create', 'prioritize', 'advise')
KINDS = ('status', 'info', 'request', 'clarify')
FIELDS = {'action', 'kind', 'item_id', 'summary', 'reply', 'note', 'title', 'priority', 'queue_position'}
NO_QUEUE = '큐에 등록하지 않았습니다.'


def unfinished_digest(document):
    """Fingerprint of the unfinished goals a side create is checked against for duplicates.

    Goals only: a side create adds a goal, and main expands steps and notes
    constantly (Prody: 15 board versions in 70 minutes), which must not force
    a full side re-run each time.
    """

    rows = [[node['id'], node['title'], node.get('ref', '')]
            for node in document['nodes'] if node['parent_id'] is None and node['status'] != 'done']
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


class WorkChanged(tree.WorkPlanError):
    """Unfinished Work changed after the actor read it; a create may now duplicate."""


def validate(decision):
    if not isinstance(decision, dict) or set(decision) - FIELDS:
        raise tree.WorkPlanError('invalid side decision fields')
    if decision.get('action') not in ACTIONS:
        raise tree.WorkPlanError('invalid side decision action')
    if decision.get('kind', 'request') not in KINDS:
        raise tree.WorkPlanError('invalid side decision kind')
    for key in ('summary', 'reply'):
        if not isinstance(decision.get(key), str) or not decision[key].strip() or len(decision[key]) > 16000:
            raise tree.WorkPlanError('side decision needs bounded summary and reply')
    for key, limit in (('note', tree.MAX_TEXT_CHARS), ('title', tree.MAX_TITLE_CHARS), ('item_id', 200)):
        if key in decision and (not isinstance(decision[key], str) or len(decision[key]) > limit):
            raise tree.WorkPlanError('invalid side decision ' + key)
    if decision.get('priority', '') not in ('', *tree.PRIORITIES):
        raise tree.WorkPlanError('invalid side decision priority')
    place = decision.get('queue_position')
    if place is not None and (isinstance(place, bool) or not isinstance(place, int) or place < 1):
        raise tree.WorkPlanError('invalid side decision queue_position')
    action = decision['action']
    kind = decision.get('kind')
    # Models occasionally copy Work-identifying fields into a status/info answer.
    # They are presentation noise: answer/clarify never mutates Work, so discard
    # them instead of failing the entire external message (Walkie 2026-11-09).
    if action in ('answer', 'clarify'):
        for key in ('item_id', 'note', 'title', 'priority', 'queue_position'):
            decision.pop(key, None)
        place = None
    if kind in ('status', 'info') and action != 'answer' or kind == 'clarify' and action != 'clarify':
        raise tree.WorkPlanError('side decision kind does not match its action')
    if kind == 'request' and action not in ('create', 'update', 'prioritize'):
        raise tree.WorkPlanError('a request must create, update or prioritize Work')
    if place is not None and action not in ('create', 'update', 'prioritize'):
        raise tree.WorkPlanError('queue_position applies only to queued Work')
    if action in ('update', 'prioritize', 'advise') and not decision.get('item_id'):
        raise tree.WorkPlanError('existing Work identity required')
    if action == 'create' and decision.get('item_id'):
        raise tree.WorkPlanError('new Work cannot overwrite an existing identity')
    return decision


def outcome_text(result):
    """The host-owned last line of the source reply, from committed state only."""

    if result['action'] in ('answer', 'clarify'):
        return NO_QUEUE
    queue = result.get('queue') or {}
    verb = '등록' if result['action'] == 'create' else '갱신'
    head = f"{result['item_id']} {verb}" + (f" · {queue['priority']}" if queue.get('priority') else '')
    if queue.get('state') == 'active':
        return head + ' · 현재 진행 중인 항목입니다.'
    if queue.get('state') == 'queued':
        return head + f" · 대기 {queue['position']}번째 (앞 {queue['ahead']}건)."
    return head + '.'


def apply(store, receipt_id, decision, expected_version, *, main_reply=False, observed_digest=None):
    validate(decision)
    if not isinstance(receipt_id, str) or len(receipt_id) != 64 or any(c not in '0123456789abcdef' for c in receipt_id):
        raise tree.WorkPlanError('invalid side receipt identity')
    if expected_version is not None and type(expected_version) is not int:
        raise tree.WorkPlanError('expected Work version must be an integer')
    digest = hashlib.sha256(json.dumps(decision, sort_keys=True).encode()).hexdigest()
    def mutate(doc):
        receipts = doc.setdefault('side_decisions', {})
        prior = receipts.get(receipt_id)
        if prior:
            if prior.get('decision_digest') != digest:
                raise tree.WorkPlanError('side event already applied with different decision')
            return {**prior['result'], 'duplicate': True}
        action = decision['action']
        effective_kind = decision.get('kind')
        item = decision.get('item_id')
        by_id = {node['id']: node for node in doc['nodes']}
        if action in ('update', 'prioritize', 'advise') and item not in by_id:
            raise tree.WorkPlanError('side target Work item missing')
        if action in ('update','prioritize') and by_id[item]['status']=='done':
            # A delayed approval/status event may name Work main already finished.
            # Report the current completed state without reopening or editing it.
            action = 'answer'
            effective_kind = 'status'
        if action == 'create' and observed_digest is not None and observed_digest != unfinished_digest(doc):
            # The actor decided "new" against a board main has since changed;
            # the event is re-evaluated against the current board instead.
            raise WorkChanged('unfinished Work changed since the side actor read it')
        target_path = []
        if item in by_id:
            current = by_id[item]
            while current:
                target_path.append(current)
                current = by_id.get(current.get('parent_id'))
        gates = [{'id': node['id'], 'waiting_on': node.get('waiting_on', ''),
                  'blocked': node.get('status') == 'blocked'}
                 for node in target_path
                 if node.get('waiting_on') or node.get('status') == 'blocked']
        place = decision.get('queue_position')
        if action == 'create':
            title = decision.get('title', '').strip()
            if not title:
                # The actor already supplied a bounded restatement. Preserve the
                # request instead of losing it solely because it omitted a short
                # display title (Walkie live failure 2026-11-09).
                title = decision['summary'].strip().splitlines()[0][:tree.MAX_TITLE_CHARS].strip()
            if not title:
                raise tree.WorkPlanError('new Work title required')
            item = tree.add_node(doc, None, title, note=decision.get('note', ''),
                                 priority=decision.get('priority', ''), ref='side:' + receipt_id[:40])
            # add_node inserts priority-ordered roots behind the active goal. P1
            # therefore becomes the first pending root and is immediately visible
            # to the main parallel scheduler without stealing the active slot.
        elif action == 'update':
            note = decision.get('note', '').strip() or decision['summary'].strip()
            if not note:
                raise tree.WorkPlanError('Work update note required')
            previous = by_id[item].get('note', '')
            addition = '\n\nSide request: ' + note
            if len(previous + addition) > tree.MAX_TEXT_CHARS:
                raise tree.WorkPlanError('side update exceeds note capacity; original preserved')
            # Side observations are input for main to review, not a scope change
            # approved by main. Preserve the existing execution phase.
            by_id[item]['note'] = previous + addition
            by_id[item]['updated_at'] = time.time()
        if action in ('update', 'prioritize') and decision.get('priority'):
            # Priority is goal-scoped in the canonical tree. A request naming
            # a detail promotes its existing goal, never creates a replacement.
            item_root = tree._root_of(by_id, item)
            tree.update_node(doc, item_root, priority=decision['priority'])
            if (action == 'prioritize' and place is None and decision['priority'] == 'P1'
                    and item_root != doc.get('active_root')):
                tree.reorder_root(doc, item_root, 2 if doc.get('active_root') else 1)
        if action == 'prioritize':
            if not decision.get('priority') and place is None:
                raise tree.WorkPlanError('explicit priority or queue_position required')
            item = tree._root_of({node['id']: node for node in doc['nodes']}, item)
        if place is not None:
            root = tree._root_of({node['id']: node for node in doc['nodes']}, item)
            if root == doc.get('active_root'):
                if action != 'create':
                    raise tree.WorkPlanError('the active goal has no waiting place; it is not moved by a side decision')
                # An empty board makes the new goal active; report that fact
                # instead of inventing or rejecting a waiting position.
            else:
                tree.reorder_queued_root(doc, root, place)
        by_id = {node['id']: node for node in doc['nodes']}
        priority = ''
        if action == 'create':
            priority = decision.get('priority', '')
        elif item in by_id:
            priority = by_id[tree._root_of(by_id, item)].get('priority', '')
        # Answer/clarify are source-facing only, even when they name a P1 or
        # gated item. Otherwise a coordinator follow-up answer creates another
        # handoff forever. Main-action decisions wake only when they actually
        # need its boundary (wait/block, P1, explicit advice/priority).
        needs_handoff = not main_reply and (action in ('prioritize', 'advise')
                                            or action in ('update', 'create') and (bool(gates) or priority == 'P1'))
        if needs_handoff:
            stamp = time.time()
            kind = ('advise' if action == 'advise' else 'resume' if gates
                    else 'p1' if priority == 'P1' else 'priority')
            handoffs = doc.setdefault('side_handoffs', {})
            handoffs[receipt_id] = {'item_id': item, 'summary': decision['summary'],
                                     'question': decision['reply'] if action == 'advise' else '',
                                     'kind': kind, 'gates': gates,
                                     'waits': [{'id': gate['id'], 'waiting_on': gate['waiting_on']}
                                               for gate in gates if gate['waiting_on']],
                                     'state': 'requested', 'requested_at': stamp,
                                     'wake': {'state': 'pending', 'requested_at': stamp}}
        result = {'action': action, 'item_id': item, 'summary': decision['summary'],
                  'reply': decision['reply'], 'delivery': 'pending', 'execution_approved': False,
                  'handoff': 'requested' if needs_handoff else 'not_required',
                  'based_on_version': expected_version}
        if effective_kind:
            result['kind'] = effective_kind
        if needs_handoff:
            result['main_handoff'] = {'kind': kind, 'waits': handoffs[receipt_id]['waits'],
                                      'question': handoffs[receipt_id]['question']}
        if action in ('create', 'update', 'prioritize'):
            node = by_id[item]
            position = tree.queue_position(doc, item)
            position['version'] = doc['version'] + 1
            result.update(title=node['title'], priority=by_id[tree._root_of(by_id, item)].get('priority', ''),
                          queue=position)
        if effective_kind:
            # Requests carry an explicit ACK together with the model's transcript-
            # based restatement. Status/info answer directly without boilerplate.
            text = decision['reply'].rstrip()
            if effective_kind == 'request':
                text = 'ACK — ' + text
            # The committed outcome, never the model's guess, closes the reply.
            result['final_text'] = text + '\n\n' + outcome_text(result)
        receipts[receipt_id] = {'decision_digest': digest, 'applied_at': time.time(),
                                'item_id': item, 'recap': decision['summary'], 'result': result}
        return {**result, 'duplicate': False}
    # An already-committed event may be recovered after a version change.
    current = store.read()
    prior = current.get('side_decisions', {}).get(receipt_id)
    if prior and prior.get('decision_digest') == digest:
        return {**prior['result'], 'duplicate': True, 'version': current['version']}
    # The side actor decides from a board snapshot while main keeps committing
    # (observed 2026-11-07: decisions on 1719-1721 against a board at
    # 1721-1723, each lost as invalid_answer). mutate re-validates its own
    # target (exists, not done) under the board lock, so it applies to the
    # current board; the snapshot it saw stays recorded as based_on_version.
    document, result = store.transaction(mutate)
    return {**result, 'version': document['version']}
