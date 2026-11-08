"""Real-OS side-inbox receive -> claim -> finish at the AAP path depth.

Runs on a host with LongPathsEnabled=0. The state directory is padded so the side inbox priority
marker directory is 166 characters, as under C:\\Users\\<aap-user>\\AppData\\Roaming\\ciel-runtime
\\workspaces\\<12>. Only files under the given owned base are written.

usage: py -3 -I maxpath_real_probe.py <runtime root> <owned base dir> <label>
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

root, base, label = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, root)
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.side_intake_processing import claim, finish  # noqa: E402

SESSION = 'claude:maxpath-probe'
tail = len('\\side-inboxes\\') + 64 + len('\\priority\\P3\\') + 2
work = base / (label + '-' + time.strftime('%H%M%S'))
need = 166 - tail - len(str(work)) - 1
state = work / ('x' * need)
result = {'label': label, 'root': root, 'state_dir_len': len(str(state)), 'events': []}
try:
    inbox = SideIntakeInbox(state, SESSION)
    text = json.dumps({'data': {'priority': 3, 'text': 'real legacy MAX_PATH probe'}})
    try:
        row = inbox.receive(text, 'walkie:maxpath-1')
        result['events'].append({'step': 'receive', 'ok': True, 'receipt': row['receipt_id'][:12]})
        key = row['receipt_id']
        marker = inbox.directory / 'priority' / 'P3' / key[:2] / (key + '.json')
        result['marker_dir_len'] = len(str(marker.parent))
        result['marker_len'] = len(str(marker))
        result['old_temp_len'] = len(str(marker.parent / ('.' + key + '.' + str(os.getpid()) + '.' + str(time.time_ns()) + '.tmp')))
        result['marker_exists'] = marker.exists()
        lease = claim(state, SESSION, 'probe-actor')
        result['events'].append({'step': 'claim', 'ok': bool(lease), 'priority': lease and lease.get('source_priority')})
        receipt = finish(state, SESSION, key, lease['generation'], {'action': 'answer', 'summary': 'ok', 'reply': 'ok'})
        result['events'].append({'step': 'finish', 'ok': True, 'stage': receipt['stage']})
        result['marker_after_finish'] = marker.exists()
    except Exception as error:
        result['events'].append({'step': 'error', 'type': type(error).__name__,
                                 'message': str(error)[:400], 'path_len': len(getattr(error, 'filename', '') or '')})
        result['trace_tail'] = traceback.format_exc().splitlines()[-4:]
    result['files'] = sorted(p.name for p in state.rglob('*') if p.is_file())[:20]
    result['tmp_left'] = [p.name for p in state.rglob('*.tmp')]
finally:
    print(json.dumps(result, indent=2))
