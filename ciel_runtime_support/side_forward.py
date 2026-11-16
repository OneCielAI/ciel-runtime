"""Deliver the original of a side event whose processing finally failed to main.

User requirement 2026-11-16 ("실패한 sse 메시지는 메시지 원문만 메인세션으로
전달해줘야지 그냥 조용히 씹어버림"): an event the side actor could not process
(quarantined: invalid answer, invalid request, or the attempt limit for a turn,
model or source-context failure) is no longer dropped. Its original text goes to
main once, as a host notice (side_notice): claimed on the event's own record,
appended, then an idle main is woken. Main decides what to answer and do.

The text is built from the stored original each time it is offered, never
copied into the notice claim: an original may be up to 32,000 characters.
"""
from __future__ import annotations

from .channel_message_repository import exclusive_file_lock
from .work_plan_tree import WorkPlanError

FIELD = 'main_forward'
INDEX = 'forwards-pending.json'
INDEX_MAX = 500
# Notice states after which the forward needs nothing more from a host.
SETTLED = ('appended', 'uncertain', 'issuing')


def is_forward(row):
    """A quarantined event whose original is to be delivered to main."""
    failure = row.get('side_failure') if isinstance(row, dict) else None
    return bool(isinstance(failure, dict) and failure.get('quarantined') and row.get(FIELD))


def text(receipt_id, row):
    failure = row.get('side_failure') or {}
    category = str(failure.get('category') or 'error')[:80]
    attempts = int(failure.get('attempt_count') or 0)
    return ('[Ciel side 처리 실패 · 원문 전달 · event ' + receipt_id[:12] + ' · ' + category
            + (' · ' + str(attempts) + '회 시도' if attempts else '') + ']\n'
            + '사이드 액터가 이 외부 메시지를 처리하지 못해 원문을 그대로 전달합니다. '
            + '필요한 응답과 작업은 메인에서 직접 판단하세요. 이 전달은 실행 승인이 아닙니다.\n\n'
            + str(row.get('text') or ''))


def _write(inbox, entries):
    inbox._save_file(inbox.directory / INDEX, {'schema': 1, 'receipts': entries[-INDEX_MAX:]})


def mark(inbox, receipt_id, row):
    """Record on a just-quarantined row that main gets its original (lock held)."""
    row[FIELD] = True
    # Commit the marker before publishing its index entry: a concurrent host
    # that sees the index must also see the forwardable record.
    inbox._save_file(inbox._event_path('pending', receipt_id), row)
    path = inbox.directory / INDEX
    with exclusive_file_lock(path):
        try:
            listed = (inbox._read_file(path, 256 * 1024) or {}).get('receipts') or []
        except WorkPlanError:
            listed = []
        listed = [key for key in listed if isinstance(key, str) and key != receipt_id] + [receipt_id]
        _write(inbox, listed)


def _backfill(inbox, listed, budget=32):
    """Add legacy quarantines gradually; no claim poll scans an unbounded inbox.

    Builds before this feature left terminal failures only in pending/. Each
    call opens at most ``budget`` pending rows, after a durable keyset cursor.
    """
    cursor_path = inbox.directory / 'forward-backfill-cursor.json'
    try:
        cursor = (inbox._read_file(cursor_path, 4096) or {}).get('after') or ''
    except (OSError, WorkPlanError):
        cursor = ''
    paths = sorted((p for p in (inbox.directory / 'pending').glob('*/*.json') if p.stem > cursor),
                   key=lambda p: p.stem)[:budget]
    if not paths and cursor:
        # One completed sweep; start over only to find rows newly quarantined by
        # an older live host during a rolling upgrade.
        cursor = ''
        paths = sorted((inbox.directory / 'pending').glob('*/*.json'), key=lambda p: p.stem)[:budget]
    for path in paths:
        try:
            row = inbox._read_file(path)
        except (OSError, WorkPlanError):
            continue
        failure = row.get('side_failure') if isinstance(row, dict) else None
        if not isinstance(failure, dict) or not failure.get('quarantined'):
            continue
        if not row.get(FIELD):
            with exclusive_file_lock(inbox._event_path('locks', path.stem)):
                current = inbox._load(path.stem)
                if current and (current.get('side_failure') or {}).get('quarantined'):
                    current[FIELD] = True
                    inbox._save_file(inbox._event_path('pending', path.stem), current)
        if path.stem not in listed:
            listed.append(path.stem)
    if paths:
        inbox._save_file(cursor_path, {'schema': 1, 'after': paths[-1].stem})
    return listed


def offered(inbox, limit=4):
    """Quarantined originals no host has delivered yet: [{receipt_id, text}]."""
    path = inbox.directory / INDEX
    try:
        listed = (inbox._read_file(path, 256 * 1024) or {}).get('receipts') or []
    except (OSError, WorkPlanError):
        listed = []
    listed = _backfill(inbox, [key for key in listed if isinstance(key, str)])
    rows, keep = [], []
    for receipt_id in listed:
        if not isinstance(receipt_id, str) or len(receipt_id) != 64:
            continue
        try:
            row = inbox._load(receipt_id)
        except (OSError, WorkPlanError):
            keep.append(receipt_id)
            continue
        if not is_forward(row):
            continue
        notice = row.get('release_notice') if isinstance(row.get('release_notice'), dict) else {}
        if notice.get('state') in ('appended', 'uncertain'):
            continue
        keep.append(receipt_id)
        if notice.get('state') != 'issuing' and len(rows) < limit:
            rows.append({'receipt_id': receipt_id, 'text': text(receipt_id, row)})
    if keep != listed:
        try:
            with exclusive_file_lock(path):
                _write(inbox, keep)
        except (OSError, WorkPlanError):
            pass
    return rows


def readd(inbox, receipt_id):
    """A person released an uncertain forward: offer it once more."""
    path = inbox.directory / INDEX
    with exclusive_file_lock(path):
        try:
            listed = (inbox._read_file(path, 256 * 1024) or {}).get('receipts') or []
        except WorkPlanError:
            listed = []
        _write(inbox, [key for key in listed if key != receipt_id] + [receipt_id])
