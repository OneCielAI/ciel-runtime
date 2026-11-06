"""Coordinator acknowledgment of side advice at an explicit safe boundary."""
from . import work_plan_tree as tree


def acknowledge(store, event_id, *, answer='', activate=False, expected_version):
    def mutate(doc):
        row = doc.get('side_handoffs', {}).get(event_id)
        if not row:
            raise tree.WorkPlanError('side handoff not found')
        if not isinstance(answer, str) or len(answer) > 16000 or type(activate) is not bool:
            raise tree.WorkPlanError('invalid side handoff acknowledgment')
        if row['state'] != 'requested':
            if row.get('answer', '') != answer or row.get('activated', False) != activate:
                raise tree.WorkPlanError('handoff already acknowledged with different result')
            return dict(row)
        if activate:
            target = next((n for n in doc['nodes'] if n['id'] == row['item_id']), None)
            if not target or target['parent_id'] is not None or target['status'] == 'done':
                raise tree.WorkPlanError('handoff target is not an unfinished goal')
            # The authenticated coordinator reports its safe boundary. This
            # selects existing work but never changes that goal's approval phase.
            tree._activate(doc, target['id'])
        row.update(state='answered' if answer else 'acknowledged', answer=answer, activated=activate)
        receipt = doc.get('side_decisions', {}).get(event_id)
        if receipt:
            receipt['result']['handoff'] = row['state']
        return dict(row)
    doc, result = store.transaction(mutate, expected_version=expected_version)
    return {**result, 'version': doc['version']}
