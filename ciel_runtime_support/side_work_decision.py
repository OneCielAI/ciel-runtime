"""Apply a side actor's decision once, without granting execution approval.

A side event is either answered (status/info/clarify, no Work change) or a
request (create/update/prioritize Work). The host, never the model, states
the outcome: whether anything was queued and where the goal now stands.
"""
import hashlib
import json
import re
import time

from . import work_plan_tree as tree

ACTIONS = ('answer', 'clarify', 'update', 'create', 'prioritize', 'advise')
KINDS = ('status', 'info', 'request', 'clarify')
FIELDS = {'action', 'kind', 'item_id', 'summary', 'reply', 'note', 'title', 'priority', 'queue_position', 'release_waits', 'time_limit',
          'relay', 'ask_main', 'no_reply', 'complaint', 'refs'}
NO_QUEUE = '큐에 등록하지 않았습니다.'
MAX_RELEASES = 100
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


SIDE_NOTE_MARKER = '\n\nSide request: '
_TRIMMED = re.compile(r'\n\n\[(\d+) earlier side requests? trimmed for space; their messages stay in the side inbox\]$')


def _trimmed_line(count):
    return '\n\n[%d earlier side request%s trimmed for space; their messages stay in the side inbox]' % (
        count, '' if count == 1 else 's')


def side_note(previous, note):
    """The item note with one more side request, always within MAX_TEXT_CHARS.

    Walkie 2026-11-16: items that kept receiving follow-ups reached the 2000
    character note limit, and every later update about them was refused at
    commit and quarantined (23 events in 12 h): the person's message was lost.
    The note text written by main or the plan stays exactly as it is. To make
    room, the oldest side requests appended here are dropped first (each one's
    original message stays in the side inbox receipt) and counted on one line;
    if the remaining room is still short, the new request is cut to fit.
    Returns (note, compaction) where compaction is None or what was done.
    """
    limit = tree.MAX_TEXT_CHARS
    addition = SIDE_NOTE_MARKER + note
    if len(previous) + len(addition) <= limit:
        return previous + addition, None
    base, *blocks = previous.split(SIDE_NOTE_MARKER)
    earlier = 0
    found = _TRIMMED.search(base)
    if found:
        earlier = int(found.group(1))
        base = base[:found.start()]
    dropped = 0
    while blocks:
        blocks.pop(0)
        dropped += 1
        head = base + _trimmed_line(earlier + dropped) + ''.join(SIDE_NOTE_MARKER + block for block in blocks)
        if len(head) + len(addition) <= limit:
            return head + addition, {'trimmed': dropped}
    head = base + (_trimmed_line(earlier + dropped) if earlier + dropped else '')
    room = limit - len(head) - len(SIDE_NOTE_MARKER) - 1
    if room >= 1:
        return head + SIDE_NOTE_MARKER + note[:room] + '…', {'trimmed': dropped, 'truncated': True}
    # Main's own note fills the item: the decision still commits; the note is
    # unchanged and the request stays in the receipt and the review row.
    return previous, {'trimmed': 0, 'skipped': True}


MAX_SIDE_SUMMARY = 16000


def clamp_text(value, limit):
    """``value`` cut to ``limit`` characters with a visible host marker.

    Andy 2026-11-18: over-long model text was refused and the event retried to
    quarantine. Length is presentation, not safety: the host keeps the head and
    says how much it cut; every other check still applies.
    """
    if not isinstance(value, str) or len(value) <= limit:
        return value
    marker = ' … [host cut %d characters]' % (len(value) - limit)
    keep = max(0, limit - len(marker))
    marker = ' … [host cut %d characters]' % (len(value) - keep)
    return (value[:keep] + marker)[:limit]


def validate(decision, *, source_has_ids=False):
    if not isinstance(decision, dict) or set(decision) - FIELDS:
        raise tree.WorkPlanError('invalid side decision fields')
    for key, limit in (('summary', MAX_SIDE_SUMMARY), ('reply', MAX_SIDE_SUMMARY), ('note', tree.MAX_TEXT_CHARS),
                       ('title', tree.MAX_TITLE_CHARS)):
        if isinstance(decision.get(key), str) and len(decision[key]) > limit:
            decision[key] = clamp_text(decision[key], limit)
    # Live side models sometimes put the classification in both fields
    # (action="status"/"info") instead of mapping it to action="answer".
    if decision.get('action') in ('status', 'info') and decision.get('kind') == decision['action']:
        decision['action'] = 'answer'
    # ... or swap the two fields: the classification in action and the action in
    # kind (Walkie 2026-11-16: action "status"/"info" with kind "answer", 4
    # events quarantined). Each value is valid only in the other field, so the
    # pair is unambiguous; the kind/action consistency check below still holds.
    if (decision.get('action') in KINDS and decision.get('action') not in ACTIONS
            and decision.get('kind') in ACTIONS and decision.get('kind') not in KINDS):
        decision['action'], decision['kind'] = decision['kind'], decision['action']
    if decision.get('action') not in ACTIONS:
        raise tree.WorkPlanError('invalid side decision action')
    if decision.get('kind', 'request') not in KINDS:
        raise tree.WorkPlanError('invalid side decision kind')
    for key in ('summary', 'reply'):
        if not isinstance(decision.get(key), str) or not decision[key].strip() or len(decision[key]) > MAX_SIDE_SUMMARY:
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
    refs = decision.get('refs')
    if refs is not None:
        # Every external id the actor found in the source or in its lookups:
        # the message, its thread root, its parent, its conversation. Some
        # sources carry ids that are shaped differently and longer than the node
        # ref limit. Those are dropped, exactly like accumulated refs, so the
        # request itself is never lost for one oversized id.
        if not isinstance(refs, list) or not all(isinstance(row, str) for row in refs):
            raise tree.WorkPlanError('invalid side decision refs')
        decision['refs'] = tree._refs(refs, strict=False)
    if 'relay' in decision and type(decision['relay']) is not bool:
        raise tree.WorkPlanError('invalid side decision relay')
    for flag in ('ask_main', 'complaint'):
        if flag in decision and type(decision[flag]) is not bool:
            raise tree.WorkPlanError('invalid side decision ' + flag)
        if decision.get(flag) is False:
            decision.pop(flag, None)
    if decision.get('complaint') is True:
        # A complaint the actor identified must never settle as an answer or
        # a bare priority change. Preserve a known target and the actual issue.
        decision['action'] = 'update' if decision.get('item_id') else 'create'
        decision['kind'] = 'request'
        decision['ask_main'] = True
    # no_reply (user 2026-11-19): an answer that would only repeat what the
    # thread already says is recorded and not posted. Andy and Celly answered
    # each other's status echoes for hours (1,700 events in 6 h on 4 threads).
    if 'no_reply' in decision and type(decision['no_reply']) is not bool:
        raise tree.WorkPlanError('invalid side decision no_reply')
    if decision.get('no_reply') is not True or decision.get('action') != 'answer' or decision.get('ask_main') is True:
        decision.pop('no_reply', None)
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
    if action in ('create', 'update', 'prioritize') and not decision.get('refs'):
        # User 2026-11-20: main cannot find the exact message it must answer when
        # the queued item does not carry the source ids. Events whose source has
        # no message or thread to point at (a stored receipt with none referenced)
        # are not refused; a source that names an id must register every related
        # one here, so nothing is hidden behind partial support.
        if source_has_ids:
            raise tree.WorkPlanError(
                'decision refs required: give the source ids in refs (target message, thread root, parent, conversation)'
            )
    if action in ('update', 'prioritize', 'advise') and not decision.get('item_id'):
        raise tree.WorkPlanError('existing Work identity required')
    if action == 'create' and decision.get('item_id'):
        raise tree.WorkPlanError('new Work cannot overwrite an existing identity')
    return decision


def outcome_text(result, priority=None):
    """The host-owned last line of the source reply, from committed state only.

    ``priority`` is the effective priority when it differs from the canonical
    board value: an activated P0 is stored as P1 (Walkie 134499, 2026-11-16:
    the reply read "P1" for a P0 the host had just activated).
    """

    if result['action'] in ('answer', 'clarify'):
        return NO_QUEUE
    queue = result.get('queue') or {}
    verb = '등록' if result['action'] == 'create' else '갱신'
    shown = priority or queue.get('priority')
    head = f"{result['item_id']} {verb}" + (f" · {shown}" if shown else '')
    if queue.get('state') == 'active':
        return head + ' · 현재 진행 중인 항목입니다.'
    if queue.get('state') == 'queued':
        return head + f" · 대기 {queue['position']}번째 (앞 {queue['ahead']}건)."
    return head + '.'


MAX_SIDE_REVIEWS = 50


def needs_main_review(result, decision):
    """Whether main must look at this side answer (user requirement 2026-11-17).

    Main is interrupted only for P0/P1, a time limit, a released wait, a relay
    to running work, a clarification question or anything that already needs a
    handoff. A routine status/info answer or a P2-P5 queue entry stays in the
    side history (receipts, Side pane, web, REST, ctl) and does not hold main.
    A reply the host then cannot send is recorded for main by
    ``record_unsent_review`` (the reply outbox), independent of this rule.
    """
    # User 2026-11-18: the side actor talks to main only when it escalates
    # (truly urgent, or the person wants main's accountable answer). Released
    # waits, priorities and queue changes run through the board on their own.
    return bool(result['action'] == 'advise' or result.get('handoff') == 'requested')


def record_unsent_review(store, receipt_id, result, source=None):
    """A side reply the host could not send reaches main once (failure path).

    A routine answer has no review row (needs_main_review); when its send is
    refused or has no route, the person never saw it, so main gets the row.
    Idempotent: an existing row is left as it is.
    """
    def mutate(doc):
        if receipt_id in (doc.get('side_reviews') or {}):
            return False
        _record_review(doc, receipt_id, result, source)
        return True
    try:
        _, added = store.transaction(mutate)
        return added
    except (tree.WorkPlanError, OSError, ValueError):
        return False


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
          source_context=None, parked_projection=None, gate_check=False, workflow=None,
          source_priority=None, p0_roots=frozenset(), source_has_ids=False):
    """``workflow`` (approval workflow on): {'source': {...}, 'prompt': digest}.

    A create then opens the goal's workflow record (intake -> proposal_due) and
    ``ask_main`` reaches main. A side update on an executing goal goes to
    ``progress`` (outside the approved scope digest, technical review P2).

    ``source_priority`` is the host's reading of the event. ``p0_roots`` are the
    exact active P0 root identities (id, created_at), read before the board lock.
    ``source_has_ids`` says the event's source names a concrete message or
    thread (the host's own reading, mcp), so a queued decision must register
    every related id in refs. Gate re-checks carry board ids only and are exempt.
    """
    validate(decision, source_has_ids=source_has_ids and not gate_check)
    # User 2026-11-18: ask_main is the side's one way to reach main (truly
    # urgent, or the person wants main's accountable answer), workflow or not.
    asks_main = decision.get('ask_main') is True
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
        complaint = decision.get('complaint') is True
        if complaint and action == 'update' and (item not in by_id or by_id[item]['status'] == 'done'):
            # A completed or removed target cannot receive new work. Queue the
            # complaint separately rather than reopening it or silently answering.
            action, item = 'create', None
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
                # A gate re-check covers every goal's gates in one event, so its
                # releases are not limited to the goal of item_id (2026-11-19).
                if node is None or node['status'] == 'done' or (
                        not gate_check and tree._root_of(by_id, node['id']) != goal):
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
        # P0 is stored as canonical P1 and activated after the board commit.
        # Source urgency is a host fact, not an optional model decision.
        queued = action in ('create', 'update', 'prioritize')
        root = by_id[tree._root_of(by_id, item)] if queued and item else None
        already_p0 = bool(root and root['status'] != 'done' and (root['id'], root.get('created_at')) in p0_roots)
        wants_p0 = queued and (source_priority == 'P0' or decision.get('priority') == 'P0' or already_p0)
        board_priority = 'P1' if wants_p0 else decision.get('priority', '')
        compacted = None
        side_note_field = None
        if action == 'create':
            title = decision.get('title', '').strip()
            if not title:
                # The actor already supplied a bounded restatement. Preserve the
                # request instead of losing it solely because it omitted a short
                # display title (Walkie live failure 2026-11-09).
                title = decision['summary'].strip().splitlines()[0][:tree.MAX_TITLE_CHARS].strip()
            if not title:
                raise tree.WorkPlanError('new Work title required')
            note = decision.get('note', '') or (decision['summary'] if complaint else '')
            refs = decision.get('refs') or []
            item = tree.add_node(doc, None, title, note=note, priority=board_priority,
                                  ref=refs[0] if refs else 'side:' + receipt_id[:40], refs=refs)
            if workflow is not None and not main_reply and not gate_check:
                from .work_workflow import start as workflow_start
                workflow_start(doc, item, receipt_id=receipt_id, source=workflow.get('source'),
                               priority='P0' if wants_p0 else board_priority,
                               understanding=decision['summary'], prompt=workflow.get('prompt'))
            # add_node inserts priority-ordered roots behind the active goal. P1
            # therefore becomes the first pending root and is immediately visible
            # to the main parallel scheduler without stealing the active slot.
        elif action == 'update':
            note = decision.get('note', '').strip() or decision['summary'].strip()
            if not note:
                raise tree.WorkPlanError('Work update note required')
            # Side observations are input for main to review, not a scope change
            # approved by main. Preserve the existing execution phase.
            # Andy 2026-11-18: an approved (executing) goal's notes are part of its
            # approval scope digest (work_parallel_mcp._root_scope_digest), so a
            # side note there would change the fingerprint running forks report
            # against. On an executing goal the observation goes to ``progress``
            # (runtime narrative, outside the digest), with the same bounded
            # compaction; a planning goal keeps it in ``note``.
            item_root = tree._root_of(by_id, item)
            field = 'progress' if tree.root_phase(doc, item_root) == tree.PHASE_EXECUTING else 'note'
            by_id[item][field], compacted = side_note(by_id[item].get(field, ''), note)
            if compacted is not None and field == 'progress':
                compacted = {**compacted, 'field': field}
            side_note_field = field
            by_id[item]['updated_at'] = time.time()
        if queued:
            # Every follow-up of a request carries its own source ids; a later
            # message must not replace the earlier ones main still needs to answer.
            tree.append_refs(doc, item, decision.get('refs') or [])
        if root and not already_p0 and (wants_p0 or decision.get('priority')):
            # Priority belongs to the root. A follow-up must not rewrite an
            # already active P0's canonical backing or its ordinary queue order.
            item_root = root['id']
            tree.update_node(doc, item_root, priority=board_priority)
            if (action == 'prioritize' and place is None and board_priority == 'P1' and not wants_p0
                    and item_root != doc.get('active_root')):
                tree.reorder_root(doc, item_root, 2 if doc.get('active_root') else 1)
        if action == 'prioritize':
            if not wants_p0 and not decision.get('priority') and place is None:
                raise tree.WorkPlanError('explicit priority or queue_position required')
            item = tree._root_of({node['id']: node for node in doc['nodes']}, item)
        if place is not None and not wants_p0:
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
        direct_p0 = wants_p0 and not already_p0
        # User 2026-11-18: only the side's own escalation reaches main.
        needs_handoff = not main_reply and action == 'advise'
        workflow_goal = (workflow is not None and action == 'create'
                         and item in (doc.get('workflows') or {}))
        if workflow_goal and not gates and time_limit is None:
            # Main hears of a workflow goal through its to-do queue (proposal
            # due), not a P1 scheduling handoff (spec 6).
            needs_handoff = False
        # ask_main reaches main as one host notice (side-intake.js), with or
        # without the workflow: a handoff was never presented once Stop stopped
        # messaging main (real E2E on 96f5b3f).
        if released and not gates and action == 'update' and time_limit is None:
            # The waiter is woken with the release itself (no acknowledgment);
            # a time limit still needs main's immediate durable notice.
            needs_handoff = False
        if needs_handoff:
            stamp = time.time()
            kind = (
                    'time_limit' if time_limit is not None else 'advise' if action == 'advise'
                    else 'resume' if gates else 'p1' if priority == 'P1' else 'priority')
            handoffs = doc.setdefault('side_handoffs', {})
            if kind == 'question' and item not in by_id:
                item_node, root_id, position = {'title': ''}, None, None
            else:
                item_node = by_id.get(item) or next(node for node in doc['nodes'] if node['id'] == item)
                root_id = tree._root_of(by_id, item)
                position = tree.queue_position(doc, item)
            handoffs[receipt_id] = {'item_id': item, 'root_id': root_id,
                                     'title': item_node.get('title', '')[:tree.MAX_TITLE_CHARS],
                                     'summary': decision['summary'][:1000],
                                     'question': (decision['reply'][:2000] if action == 'advise' else
                                                  decision['summary'][:2000] if kind == 'question' else ''),
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
        if compacted:
            result['note_compacted'] = compacted
        if side_note_field == 'progress':
            result['side_note_field'] = 'progress'
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
            if wants_p0:
                result['priority'] = 'P0'
            if direct_p0:
                result['p0_root_id'] = tree._root_of(by_id, item)
        if effective_kind:
            # The receipt ACK and the understanding were already sent (user
            # requirement 2026-11-17); the final reply is the result alone, with
            # no second "ACK —". The committed outcome, never the model's guess,
            # closes it.
            text = decision['reply'].rstrip()
            result['final_text'] = text + '\n\n' + outcome_text(result, priority='P0' if already_p0 else None)
        if asks_main and not main_reply:
            result['ask_main'] = True
            if complaint:
                result['complaint'] = True
        if decision.get('no_reply') is True and time_limit is None:
            result['no_reply'] = True
        receipts[receipt_id] = {'decision_digest': digest, 'applied_at': time.time(),
                                'item_id': item, 'recap': decision['summary'], 'result': result}
        if decision.get('relay') and action == 'update':
            # The host forwards the message to main and to the fork working on
            # this item; each decides whether it matters to its current work.
            result['relay'] = {'item_id': item}
        if not main_reply and not gate_check and needs_main_review(result, decision):
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
