"""Owned reproduction: claims needed before a fresh event is claimable.

Prody's inbox held 230 permanently quarantined (invalid_answer) events in
pending/. claim() scans pending rows in keyset windows of 100, so an ordinary
(unprioritized) new event is only seen when the rotating window reaches it.
This builds the same shape in a temp state dir and counts claim() calls until
the fresh event is returned. Prints numbers only.
"""
import json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[8]))
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox
from ciel_runtime_support.side_intake_processing import claim, fail

QUARANTINED = int(sys.argv[1]) if len(sys.argv) > 1 else 230
TRIALS = int(sys.argv[2]) if len(sys.argv) > 2 else 20
results = []
with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    inbox = SideIntakeInbox(root, 'claude:repro')
    def quarantine(key):
        # The exact pending row shape fail() leaves for a completed turn whose
        # decision was invalid (Prody: 230 such rows).
        row = inbox._load(key)
        row['side_failure'] = {'category': 'invalid_answer', 'attempt_count': 1, 'failed_at': time.time(),
                               'retry_not_before': time.time() + 5, 'quarantined': True, 'retryable': False}
        row.pop('processing', None)
        inbox._save_file(inbox._event_path('pending', key), row)
    for index in range(QUARANTINED):
        quarantine(inbox.receive('old event %d' % index, 'old-%d' % index)['receipt_id'])
    for trial in range(TRIALS):
        receipt = inbox.receive('fresh event %d' % trial, 'fresh-%d' % trial)
        calls, started = 0, time.perf_counter()
        while True:
            calls += 1
            event = claim(root, 'claude:repro', 'actor')
            if event and event['receipt_id'] == receipt['receipt_id']:
                break
            if calls > 50:
                break
        results.append({'calls': calls, 'seconds': round(time.perf_counter() - started, 3)})
        # Settle the fresh event so the next trial starts from a like backlog.
        if event:
            fail(root, 'claude:repro', event['receipt_id'], event['generation'], {'category': 'invalid_answer'})
calls = sorted(r['calls'] for r in results)
print(json.dumps({'quarantined': QUARANTINED, 'trials': TRIALS, 'calls_in_trial_order': [r['calls'] for r in results],
                  'seconds_in_trial_order': [r['seconds'] for r in results], 'calls_until_claimed': calls,
                  'max_calls': calls[-1], 'per_call_seconds_max': max(r['seconds'] / r['calls'] for r in results)}))
