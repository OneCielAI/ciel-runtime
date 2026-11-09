"""Main's review of external messages the side actor already answered."""
import time

from . import work_plan_tree as tree

VERDICTS = ('ok', 'corrected')


def pending(doc, limit=10):
    rows = [{'receipt_id': key, **row} for key, row in (doc.get('side_reviews') or {}).items()
            if row.get('state') == 'pending']
    rows.sort(key=lambda row: row.get('recorded_at', 0))
    return rows[:limit]


def review(store, receipt_ids, *, verdict, note, expected_version):
    """Mark side answers reviewed. 'corrected' records that main sent a correction."""
    if (not isinstance(receipt_ids, list) or not 1 <= len(receipt_ids) <= 50
            or any(not isinstance(key, str) for key in receipt_ids) or len(set(receipt_ids)) != len(receipt_ids)):
        raise tree.WorkPlanError('side review needs 1-50 distinct receipt ids')
    if verdict not in VERDICTS:
        raise tree.WorkPlanError('side review verdict must be ok or corrected')
    if not isinstance(note, str) or len(note) > 2000 or verdict == 'corrected' and not note.strip():
        raise tree.WorkPlanError('a corrected review needs a short note of what was wrong')
    if type(expected_version) is not int:
        raise tree.WorkPlanError('expected Work version required')

    def mutate(doc):
        reviews = doc.get('side_reviews') or {}
        missing = [key for key in receipt_ids if key not in reviews]
        if missing:
            raise tree.WorkPlanError('unknown side review: ' + missing[0])
        stamp = time.time()
        state = 'reviewed' if verdict == 'ok' else 'corrected'
        for key in receipt_ids:
            row = reviews[key]
            if row.get('state') != 'pending':
                continue
            row.update(state=state, reviewed_at=stamp, review_note=note)
        return [{'receipt_id': key, **reviews[key]} for key in receipt_ids]

    doc, rows = store.transaction(mutate, expected_version=expected_version)
    return {'reviews': rows, 'version': doc['version']}
