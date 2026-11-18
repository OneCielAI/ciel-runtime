"""Read-only tally of one workspace's side inbox: failures by reason and day,
and the pending backlog. Prints counts only (no event text).

usage: python scan_side_failures.py <side-inboxes dir>
"""
import collections
import datetime as dt
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
reasons = collections.Counter()
by_day = collections.defaultdict(collections.Counter)
pending = collections.Counter()
newest_claim = 0.0
for path in root.glob('*/*/*.json'):
    if path.parent.parent.name not in ('pending', 'events', 'processed', 'received', 'receipts'):
        continue
    try:
        row = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        continue
    if not isinstance(row, dict) or 'receipt_id' in row and False:
        continue
    failure = row.get('side_failure') or {}
    stage = row.get('stage')
    if stage == 'received':
        state = 'quarantined' if failure.get('quarantined') else ('retrying' if failure else 'fresh')
        pending[state] += 1
    started = (row.get('processing') or {}).get('started_at') or 0
    newest_claim = max(newest_claim, float(started or 0))
    if failure:
        reason = str(failure.get('reason') or failure.get('category') or '')
        key = (failure.get('category'), reason[:70])
        reasons[key] += 1
        day = dt.datetime.fromtimestamp(float(failure.get('failed_at') or 0), dt.timezone.utc).strftime('%m-%d')
        by_day[key][day] += 1
print(json.dumps({'pending': pending, 'newest_claim_utc': dt.datetime.fromtimestamp(newest_claim, dt.timezone.utc).isoformat()
                  if newest_claim else None}, default=dict))
for (category, reason), count in reasons.most_common(25):
    print(count, category, '|', reason, '|', dict(sorted(by_day[(category, reason)].items())))
