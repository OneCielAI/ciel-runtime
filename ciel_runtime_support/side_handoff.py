"""Coordinator acknowledgment of side advice at an explicit safe boundary."""
import time

from . import work_plan_tree as tree


def acknowledge(store, event_id, *, answer='', activate=False, clear_wait=None, expected_version):
    clear_wait = [] if clear_wait is None else clear_wait
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
            target = next((n for n in doc['nodes'] if n['id'] == row['item_id']), None)
            if not target or target['parent_id'] is not None or target['status'] == 'done':
                raise tree.WorkPlanError('handoff target is not an unfinished goal')
            tree._activate(doc, target['id'])
        for node_id in clear_wait:
            tree.update_node(doc, node_id, waiting_on='')
        stamp = time.time()
        wake = dict(row.get('wake') or {})
        wake.update(state='acknowledged', acknowledged_at=stamp)
        row.update(state='answered' if answer else 'acknowledged', answer=answer,
                   activated=activate, clear_wait=list(clear_wait), wake=wake)
        receipt = doc.get('side_decisions', {}).get(event_id)
        if receipt:
            receipt['result']['handoff'] = row['state']
        return dict(row)
    doc, result = store.transaction(mutate, expected_version=expected_version)
    return {**result, 'version': doc['version']}
