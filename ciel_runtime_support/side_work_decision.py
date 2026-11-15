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
FIELDS = {'action', 'kind', 'item_id', 'summary', 'reply', 'note', 'title', 'priority', 'queue_position', 'release_waits', 'time_limit',
          'relay'}
NO_QUEUE = '큐에 등록하지 않았습니다.'
MAX_RELEASES = 16
MAX_TIME_LIMIT_TEXT = 1000
MAX_TIME_LIMIT_EVIDENCE = 2000
MAX_TIME_LIMIT_DUE_AT = 100


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


def wait_digest(waiting_on):
    """SHA-256 of the exact released wait text; the text itself is never copied."""
    return hashlib.sha256(waiting_on.encode('utf-8')).hexdigest()


def _release_leaf(doc, node_id, line, now):
    """Release one exact leaf wait without touching any other node.

    ``update_node`` reopens ancestors, so it is not used: only this node is
    copied and replaced. ``blocked`` becomes ``pending``; every other status is
    kept. Ancestors and descendants stay byte-for-byte unchanged.
    """
    nodes = doc['nodes']
    index = next(i for i, row in enumerate(nodes) if row['id'] == node_id)
    node = dict(nodes[index])
    unblocked = node.get('status') == 'blocked'
    node['waiting_on'] = ''
    if unblocked:
        node['status'] = 'pending'
    if len(node.get('note', '') + line) <= tree.MAX_TEXT_CHARS:
        node['note'] = node.get('note', '') + line
    node['updated_at'] = now
    nodes[index] = node
    return unblocked


def _parked_lease(parked_projection, item_id, root_id):
    """The exact parked lease for this released item, or None.

    The projection is read by the host before the board transaction. A stale
    or unreadable projection only loses parked preference; it never resumes.
    """
    if type(parked_projection) is not dict or type(parked_projection.get('rows')) is not list:
        return None
    matches = [row for row in parked_projection['rows']
               if type(row) is dict and row.get('item_id') == item_id and row.get('root_id') == root_id
               and row.get('state') == 'waiting_input' and row.get('slot') is False
               and isinstance(row.get('lease_id'), str) and 0 < len(row['lease_id']) <= 200]
    return matches[0]['lease_id'] if len(matches) == 1 else None


def validate(decision):
    if not isinstance(decision, dict) or set(decision) - FIELDS:
        raise tree.WorkPlanError('invalid side decision fields')
    # Live side models sometimes put the classification in both fields
    # (action="status"/"info") instead of mapping it to action="answer".
    if decision.get('action') in ('status', 'info') and decision.get('kind') == decision['action']:
        decision['action'] = 'answer'
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
    if decision.get('priority', '') not in ('', 'P0', *tree.PRIORITIES):
        raise tree.WorkPlanError('invalid side decision priority')
    place = decision.get('queue_position')
    if place is not None and (isinstance(place, bool) or not isinstance(place, int) or place < 1):
        raise tree.WorkPlanError('invalid side decision queue_position')
    releases = decision.get('release_waits')
    if releases is not None:
        # Each entry names a recorded wait and the exact text the actor saw, so a
        # wait main changed meanwhile is never released on stale evidence.
        if (not isinstance(releases, list) or len(releases) > MAX_RELEASES
                or any(not isinstance(row, dict) or set(row) != {'id', 'waiting_on'}
                       or not isinstance(row['id'], str) or not 0 < len(row['id']) <= 200
                       or not isinstance(row['waiting_on'], str) or not 0 < len(row['waiting_on']) <= tree.MAX_WAIT_CHARS
                       for row in releases)
                or len({row['id'] for row in releases}) != len(releases)):
            raise tree.WorkPlanError('invalid side decision release_waits')
    if 'relay' in decision and type(decision['relay']) is not bool:
        raise tree.WorkPlanError('invalid side decision relay')
    time_limit = decision.get('time_limit')
    if time_limit is not None:
        if (not isinstance(time_limit, dict) or set(time_limit) - {'text', 'due_at', 'evidence'}
                or not isinstance(time_limit.get('text'), str) or not 0 < len(time_limit['text'].strip()) <= MAX_TIME_LIMIT_TEXT
                or not isinstance(time_limit.get('evidence'), str) or not 0 < len(time_limit['evidence'].strip()) <= MAX_TIME_LIMIT_EVIDENCE
                or ('due_at' in time_limit and (not isinstance(time_limit['due_at'], str)
                    or not 0 < len(time_limit['due_at'].strip()) <= MAX_TIME_LIMIT_DUE_AT))):
            raise tree.WorkPlanError('invalid side decision time_limit')
    action = decision['action']
    kind = decision.get('kind')
    # Models occasionally copy Work-identifying fields into a status/info answer.
    # They are presentation noise: answer/clarify never mutates Work, so discard
    # them instead of failing the entire external message (Walkie 2026-11-09).
    if action in ('answer', 'clarify'):
        # A time-limited answer/clarification must name related Work so main can
        # inspect and acknowledge the durable deadline handoff. Without a limit,
        # copied Work-identifying fields remain presentation noise.
        for key in ('note', 'title', 'priority', 'queue_position', 'release_waits'):
            decision.pop(key, None)
        if time_limit is None:
            decision.pop('item_id', None)
        elif not decision.get('item_id'):
            raise tree.WorkPlanError('time-limited event requires related Work identity')
        place = None
    if action != 'update':
        # Only an update of the waiting Work releases a wait. Elsewhere the
        # field is dropped rather than failing (and losing) the whole event;
        # advise/prioritize still reach main, which may clear the wait itself.
        decision.pop('release_waits', None)
        # Relay forwards a follow-up about one in-progress item to the sessions
        # working on it (user requirement 2026-11-15); only an update names that item.
        decision.pop('relay', None)
    if decision.get('relay') is False:
        decision.pop('relay', None)
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


MAX_SIDE_REVIEWS = 50


def _record_review(doc, receipt_id, result, source):
    """Leave main a review note for what the side already handled and sent.

    Main reads it with Work status and does not repeat the answer or ACK; it
    sends a correction only when the side's reply was wrong.
    """
    reviews = doc.setdefault('side_reviews', {})
    reviews[receipt_id] = {
        'state': 'pending', 'kind': result.get('kind') or result['action'], 'action': result['action'],
        'item_id': result.get('item_id'), 'summary': result['summary'][:1000],
        'reply': str(result.get('final_text') or result['reply'])[:4000],
        'source': source[:300] if isinstance(source, str) else None,
        **({'source_context': result['source_context']} if result.get('source_context') else {}),
        'recorded_at': time.time()}
    finished = [key for key, row in reviews.items() if row.get('state') != 'pending']
    for key in sorted(finished, key=lambda k: reviews[k].get('recorded_at', 0))[:max(0, len(reviews) - MAX_SIDE_REVIEWS)]:
        reviews.pop(key, None)


def apply(store, receipt_id, decision, expected_version, *, main_reply=False, observed_digest=None, source=None,
          source_context=None, parked_projection=None, gate_check=False):
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
        if (action in ('update', 'prioritize', 'advise') or decision.get('time_limit') is not None
                and action in ('answer', 'clarify')) and item not in by_id:
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
        # The side is the key for a recorded wait: when this event is the signal
        # a wait names, it releases it here, atomically with the decision, so the
        # waiting session needs no acknowledgment round trip. Only an unfinished
        # node of the same goal whose wait text is still exactly what the actor
        # saw; anything else stays for main. Execution approval is unchanged.
        released, unreleased, unblocked, wait_boosts = [], [], [], []
        if action == 'update':
            goal = tree._root_of(by_id, item)
            for wait in decision.get('release_waits') or []:
                node = by_id.get(wait['id'])
                if node is None or node['status'] == 'done' or tree._root_of(by_id, node['id']) != goal:
                    unreleased.append({'id': wait['id'], 'reason': 'not an unfinished node of this goal'})
                elif node.get('waiting_on', '') != wait['waiting_on']:
                    unreleased.append({'id': wait['id'], 'reason': 'wait changed since side observation'
                                       if node.get('waiting_on') else 'no recorded wait'})
                elif tree._children(doc, node['id']):
                    # A goal or group wait is main's: releasing it would have to
                    # reopen or skip descendants, so it stays recorded.
                    unreleased.append({'id': wait['id'], 'reason': 'non_leaf_wait'})
                else:
                    released.append({'id': node['id'], 'waiting_on': node['waiting_on']})
            release_at = time.time()
            for wait in released:
                line = ('\n\nSide released wait "' + wait['waiting_on'] + '" (event ' + receipt_id[:12] + '): '
                        + decision['summary'].strip().splitlines()[0][:300])
                if _release_leaf(doc, wait['id'], line, release_at):
                    unblocked.append(wait['id'])
            by_id = {node['id']: node for node in doc['nodes']}
            for wait in released:
                root = by_id[tree._root_of(by_id, wait['id'])]
                wait_boosts.append({
                    'item_id': wait['id'], 'root_id': root['id'],
                    'root_created_at': root.get('created_at', 0),
                    'release_receipt': receipt_id, 'release_at': release_at,
                    'wait_digest': wait_digest(wait['waiting_on']),
                    'parked_lease_id': _parked_lease(parked_projection, wait['id'], root['id']),
                })
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
        # P0 is stored as canonical P1 and activated as P0 right after this
        # commit (side_intake_mcp), like every other P0 entry point.
        wants_p0 = decision.get('priority') == 'P0'
        board_priority = 'P1' if wants_p0 else decision.get('priority', '')
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
                                 priority=board_priority, ref='side:' + receipt_id[:40])
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
            tree.update_node(doc, item_root, priority=board_priority)
            if (action == 'prioritize' and place is None and board_priority == 'P1'
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
            priority = board_priority
        elif item in by_id:
            priority = by_id[tree._root_of(by_id, item)].get('priority', '')
        # Answer/clarify are source-facing only, even when they name a P1 or
        # gated item. Otherwise a coordinator follow-up answer creates another
        # handoff forever. A committed update of an existing P1 is already the
        # side actor's work; priority alone is not another action for main. Main
        # wakes only for an unresolved wait/block, explicit advice/priority, or
        # a newly-created P1 that still needs scheduling at a safe boundary.
        time_limit = decision.get('time_limit')
        # A P0 goal runs in its own fork at once; main is not also asked to
        # schedule it (a recorded wait or a time limit still reaches main).
        direct_p0 = wants_p0 and action in ('create', 'update', 'prioritize')
        needs_handoff = not main_reply and (time_limit is not None
                                            or action == 'advise'
                                            or action == 'prioritize' and not direct_p0
                                            or action == 'update' and bool(gates)
                                            or action == 'create' and (bool(gates) or priority == 'P1' and not direct_p0))
        if released and not gates and action == 'update' and time_limit is None:
            # The waiter is woken with the release itself (no acknowledgment);
            # a time limit still needs main's immediate durable notice.
            needs_handoff = False
        if needs_handoff:
            stamp = time.time()
            kind = ('time_limit' if time_limit is not None else 'advise' if action == 'advise'
                    else 'resume' if gates else 'p1' if priority == 'P1' else 'priority')
            handoffs = doc.setdefault('side_handoffs', {})
            item_node = by_id.get(item) or next(node for node in doc['nodes'] if node['id'] == item)
            root_id = tree._root_of(by_id, item)
            position = tree.queue_position(doc, item)
            handoffs[receipt_id] = {'item_id': item, 'root_id': root_id,
                                     'title': item_node.get('title', '')[:tree.MAX_TITLE_CHARS],
                                     'summary': decision['summary'][:1000],
                                     'question': decision['reply'][:2000] if action == 'advise' else '',
                                     'kind': kind, 'gates': gates,
                                     **({'time_limit': dict(time_limit), 'queue': position} if time_limit is not None else {}),
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
        if time_limit is not None:
            result['time_limit'] = dict(time_limit)
        if isinstance(source_context, str) and source_context:
            # none_referenced: the event named no message/thread, so no source
            # reply exists. Main reviews the board decision only.
            result['source_context'] = source_context[:40]
        if released:
            result['released_waits'] = released
            # Board-first recovery receipt for the scheduling sidecar (design:
            # Board recovery receipt). It carries digests and ids only.
            result['wait_boost'] = wait_boosts
        if unblocked:
            result['unblocked_waits'] = unblocked
        if unreleased:
            result['unreleased_waits'] = unreleased
        if needs_handoff:
            handoff = handoffs[receipt_id]
            result['main_handoff'] = ({'kind': kind, 'item_id': handoff['item_id'],
                                       'root_id': handoff['root_id'], 'title': handoff['title'],
                                       'summary': handoff['summary'], 'queue': handoff.get('queue'),
                                       'time_limit': handoff['time_limit'],
                                       'waits': handoff['waits'], 'question': handoff['question']}
                                      if time_limit is not None else
                                      {'kind': kind, 'waits': handoff['waits'], 'question': handoff['question']})
        if action in ('create', 'update', 'prioritize'):
            node = by_id[item]
            position = tree.queue_position(doc, item)
            position['version'] = doc['version'] + 1
            result.update(title=node['title'], priority=by_id[tree._root_of(by_id, item)].get('priority', ''),
                          queue=position)
            if direct_p0:
                result.update(priority='P0', p0_root_id=tree._root_of(by_id, item))
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
        if decision.get('relay') and action == 'update':
            # The host forwards the message to main and to the fork working on
            # this item; each decides whether it matters to its current work.
            result['relay'] = {'item_id': item}
        if not main_reply and not gate_check:
            # Main's own handoff answers come back as follow-ups; main already knows them.
            # A host gate re-check has no person behind it: a release or a
            # handoff already reaches main, and "no change" is not news.
            _record_review(doc, receipt_id, result, source)
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
