"""Coordinator acknowledgment of side advice at an explicit safe boundary."""
import time

from . import work_plan_tree as tree
from .work_plan_store import WorkPlanConflict


def acknowledge(store, event_id, *, answer='', activate=False, clear_wait=None, expected_version):
    clear_wait = [] if clear_wait is None else clear_wait
    # An identical settled ACK is idempotent even when the caller still carries
    # the pre-ACK Work version. A different payload remains an error.
    snapshot = store.read_snapshot()
    existing = snapshot.get('side_handoffs', {}).get(event_id)
    if not existing:
        raise tree.WorkPlanError('side handoff not found')
    if existing.get('state') != 'requested':
        if (existing.get('answer', '') != answer or existing.get('activated', False) != activate
                or existing.get('clear_wait', []) != clear_wait):
            raise tree.WorkPlanError('handoff already acknowledged with different result')
        return {**existing, 'version': snapshot['version']}

    def mutate(doc):
        row = doc.get('side_handoffs', {}).get(event_id)
        if not row:
            raise tree.WorkPlanError('side handoff not found')
        if (not isinstance(answer, str) or len(answer) > 16000 or type(activate) is not bool
                or not isinstance(clear_wait, list) or any(not isinstance(value, str) for value in clear_wait)
                or len(clear_wait) != len(set(clear_wait))):
            raise tree.WorkPlanError('invalid side handoff acknowledgment')
        if row['state'] != 'requested':
            if (row.get('answer', '') != answer or row.get('activated', False) != activate
                    or row.get('clear_wait', []) != clear_wait):
                raise tree.WorkPlanError('handoff already acknowledged with different result')
            return dict(row)
        recorded = {gate['id']: gate.get('waiting_on', '') for gate in row.get('gates', [])
                    if gate.get('waiting_on')}
        by_id = {node['id']: node for node in doc['nodes']}
        for node_id in clear_wait:
            if node_id not in recorded or node_id not in by_id:
                raise tree.WorkPlanError('handoff cannot clear an unrecorded wait')
            if by_id[node_id].get('waiting_on', '') != recorded[node_id]:
                raise tree.WorkPlanError('handoff wait changed since side observation')
        if activate:
            # A wait gate or step handoff names the child it concerns; activating
            # means selecting the goal that owns it. ciel-walkie 2026-11-10: a
            # P1 gate handoff (n1235) stayed requested because only roots passed.
            goal = None
            if row.get('item_id') in by_id:
                try:
                    goal = by_id[tree._root_of(by_id, row['item_id'])]
                except KeyError:
                    goal = None
            if not goal or goal['status'] == 'done':
                raise tree.WorkPlanError('handoff target is not an unfinished goal')
            tree._activate(doc, goal['id'])
        for node_id in clear_wait:
            tree.update_node(doc, node_id, waiting_on='')
        stamp = time.time()
        wake = dict(row.get('wake') or {})
        wake.update(state='acknowledged', acknowledged_at=stamp, needs_ack_retry=False)
        row.update(state='answered' if answer else 'acknowledged', answer=answer,
                   activated=activate, clear_wait=list(clear_wait), wake=wake)
        receipt = doc.get('side_decisions', {}).get(event_id)
        if receipt:
            receipt['result']['handoff'] = row['state']
        return dict(row)
    try:
        doc, result = store.transaction(mutate, expected_version=expected_version)
    except WorkPlanConflict:
        latest = store.read_snapshot().get('side_handoffs', {}).get(event_id) or {}
        if latest.get('state') != 'requested':
            if (latest.get('answer', '') == answer and latest.get('activated', False) == activate
                    and latest.get('clear_wait', []) == clear_wait):
                return {**latest, 'version': store.read_snapshot()['version']}
            raise tree.WorkPlanError('handoff already acknowledged with different result')
        from .side_wake import mark_ack_retry
        try:
            mark_ack_retry(store, event_id)
        except tree.WorkPlanError:
            pass
        raise
    return {**result, 'version': doc['version']}
