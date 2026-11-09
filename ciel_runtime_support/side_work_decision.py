"""Apply a side actor's decision once, without granting execution approval."""
import hashlib
import json
import time

from . import work_plan_tree as tree

ACTIONS = ('answer', 'clarify', 'update', 'create', 'prioritize', 'advise')


def validate(decision):
    if not isinstance(decision, dict) or set(decision) - {'action', 'item_id', 'summary', 'reply', 'note', 'title', 'priority'}:
        raise tree.WorkPlanError('invalid side decision fields')
    if decision.get('action') not in ACTIONS:
        raise tree.WorkPlanError('invalid side decision action')
    for key in ('summary', 'reply'):
        if not isinstance(decision.get(key), str) or not decision[key].strip() or len(decision[key]) > 16000:
            raise tree.WorkPlanError('side decision needs bounded summary and reply')
    for key, limit in (('note', tree.MAX_TEXT_CHARS), ('title', tree.MAX_TITLE_CHARS), ('item_id', 200)):
        if key in decision and (not isinstance(decision[key], str) or len(decision[key]) > limit):
            raise tree.WorkPlanError('invalid side decision ' + key)
    if decision.get('priority', '') not in ('', *tree.PRIORITIES):
        raise tree.WorkPlanError('invalid side decision priority')
    action=decision['action']
    if action in ('answer','clarify') and any(key in decision for key in ('note','title','priority')):
        raise tree.WorkPlanError('answer-only decisions cannot carry Work edits')
    if action in ('update','prioritize','advise') and not decision.get('item_id'):
        raise tree.WorkPlanError('existing Work identity required')
    if action=='create' and decision.get('item_id'):
        raise tree.WorkPlanError('new Work cannot overwrite an existing identity')
    return decision


def apply(store, receipt_id, decision, expected_version, *, main_reply=False):
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
        item = decision.get('item_id')
        by_id = {node['id']: node for node in doc['nodes']}
        if action in ('update', 'prioritize', 'advise') and item not in by_id:
            raise tree.WorkPlanError('side target Work item missing')
        if action in ('update','prioritize') and by_id[item]['status']=='done':
            raise tree.WorkPlanError('completed Work cannot be reopened by side decision')
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
        if action == 'create':
            if not decision.get('title', '').strip():
                raise tree.WorkPlanError('new Work title required')
            item = tree.add_node(doc, None, decision['title'], note=decision.get('note', ''),
                                 priority=decision.get('priority', ''), ref='side:' + receipt_id[:40])
            # add_node inserts priority-ordered roots behind the active goal. P1
            # therefore becomes the first pending root and is immediately visible
            # to the main parallel scheduler without stealing the active slot.
        elif action == 'update':
            if not decision.get('note', '').strip():
                raise tree.WorkPlanError('Work update note required')
            previous = by_id[item].get('note', '')
            addition = '\n\nSide request: ' + decision['note']
            if len(previous + addition) > tree.MAX_TEXT_CHARS:
                raise tree.WorkPlanError('side update exceeds note capacity; original preserved')
            # Side observations are input for main to review, not a scope change
            # approved by main. Preserve the existing execution phase.
            by_id[item]['note'] = previous + addition
            by_id[item]['updated_at'] = time.time()
        elif action == 'prioritize':
            if decision.get('priority') not in tree.PRIORITIES:
                raise tree.WorkPlanError('explicit priority required')
            # Priority is goal-scoped in the canonical tree. A request naming
            # a detail promotes its existing goal, never creates a replacement.
            item = tree._root_of(by_id, item)
            tree.update_node(doc, item, priority=decision['priority'])
            if decision['priority']=='P1' and item != doc.get('active_root'):
                tree.reorder_root(doc, item, 2 if doc.get('active_root') else 1)
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
        if needs_handoff:
            result['main_handoff'] = {'kind': kind, 'waits': handoffs[receipt_id]['waits'],
                                      'question': handoffs[receipt_id]['question']}
        if action in ('create', 'prioritize'):
            node = next(node for node in doc['nodes'] if node['id'] == item)
            result.update(title=node['title'], priority=node.get('priority', ''))
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
