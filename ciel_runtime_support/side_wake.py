"""Durable main-wake submission state for side handoffs."""
import time
import uuid

from . import work_plan_tree as tree


def prepare(store, event_id):
    def mutate(doc):
        row = doc.get('side_handoffs', {}).get(event_id)
        if not row or row.get('state') != 'requested':
            raise tree.WorkPlanError('requested side handoff required')
        wake = row.setdefault('wake', {'state': 'pending', 'requested_at': time.time()})
        if wake.get('state') in ('submitting', 'queued', 'appended', 'uncertain'):
            return {'event_id': event_id, **wake}
        attempt_id = uuid.uuid4().hex
        wake.update(state='submitting', attempt_id=attempt_id, started_at=time.time())
        return {'event_id': event_id, **wake}
    doc, result = store.transaction(mutate)
    return {**result, 'version': doc['version']}


def record(store, event_id, state, attempt_id):
    # appended: written into main's running turn, read at its next model
    # request. Unlike queued, no prompt follows at turn end, so the Stop gate
    # still presents the handoff if main has not acknowledged it by then.
    if state not in ('queued', 'appended', 'uncertain', 'failed'):
        raise tree.WorkPlanError('invalid side wake state')
    def mutate(doc):
        row = doc.get('side_handoffs', {}).get(event_id)
        if not row or row.get('state') != 'requested':
            raise tree.WorkPlanError('requested side handoff required')
        wake = row.get('wake', {})
        if wake.get('state') != 'submitting' or not attempt_id or wake.get('attempt_id') != attempt_id:
            raise tree.WorkPlanError('stale side wake attempt')
        wake.update(state=state, updated_at=time.time())
        return {'event_id': event_id, **wake}
    doc, result = store.transaction(mutate)
    return {**result, 'version': doc['version']}
