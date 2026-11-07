"""Read-only, allow-listed snapshot of the live Walkie Work board and side inbox for the mockup.

Every live file is copied into a scratch folder first and read from there.
Never emitted: raw event text, routes, reply tokens, transcript previews, reply bodies.
"""
import glob, json, os, re, shutil, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
snap, state, identity, out, scratch = sys.argv[1:6]
sys.path.insert(0, snap)
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox, source_priority  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402

scratch = Path(scratch)
shutil.rmtree(scratch, ignore_errors=True)
scratch.mkdir(parents=True)


def copy_json(path):
    target = scratch / (str(abs(hash(str(path)))) + '.json')
    shutil.copyfile(path, target)
    return json.loads(target.read_text(encoding='utf8'))


def clip(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


# ---- Work board ----------------------------------------------------------
board = copy_json(session_work_plan_store(Path(state), identity).path)
nodes = board['nodes']
by_parent = {}
for node in nodes:
    by_parent.setdefault(node.get('parent_id'), []).append(node)


def counts(node_id):
    done = total = waiting = blocked = 0
    for child in by_parent.get(node_id, []):
        kids = by_parent.get(child['id'])
        if kids:
            d, t, w, b = counts(child['id'])
            done, total, waiting, blocked = done + d, total + t, waiting + w, blocked + b
        else:
            total += 1
            done += child['status'] == 'done'
            waiting += bool(child.get('waiting_on')) and child['status'] != 'done'
            blocked += child['status'] == 'blocked'
    return done, total, waiting, blocked


def project(node):
    done, total, waiting, blocked = counts(node['id'])
    return {'id': node['id'], 'parent': node.get('parent_id'), 'title': clip(node.get('title'), 72),
            'status': node['status'], 'priority': node.get('priority', '') if node.get('parent_id') is None else '',
            'waiting': bool(node.get('waiting_on')) and node['status'] != 'done', 'done': done, 'total': total,
            'waiting_count': waiting, 'blocked_count': blocked, 'urgent': node.get('urgent') is True}


active = board.get('active_root')
roots = [n for n in nodes if n.get('parent_id') is None]
open_roots = [n for n in roots if n['status'] != 'done']
queue = [n['id'] for n in open_roots if n['id'] != active]
done_roots = [n['id'] for n in roots if n['status'] == 'done']
keep = set()


def keep_tree(node_id, depth):
    keep.add(node_id)
    if depth < 3:
        for child in by_parent.get(node_id, []):
            keep_tree(child['id'], depth + 1)


for root in open_roots:
    keep_tree(root['id'], 0 if root['id'] == active else 1)
for root_id in done_roots[-4:]:
    keep.add(root_id)
work = {'version': board['version'], 'phase': board.get('phase'), 'active_root': active, 'queue': queue,
        'done_roots': done_roots[-4:], 'focus': (board.get('focus') or {}).get('node_ids', []),
        'nodes': [project(n) for n in nodes if n['id'] in keep]}

# ---- Parallel ledger -----------------------------------------------------
parallel = {'policy': {}, 'leases': []}
ledger_path = Path(os.environ['APPDATA']) / 'ciel-runtime' / 'work-parallel.json'
if ledger_path.exists():
    ledger = copy_json(ledger_path)
    parallel['policy'] = {k: ledger['policy'].get(k) for k in ('max_active', 'p1_reserved', 'paused')}
    parallel['leases'] = [{'item_id': v.get('item_id'), 'state': v.get('state'), 'role': v.get('role'),
                           'priority': v.get('priority'), 'slot': v.get('slot')}
                          for v in ledger['leases'].values() if v.get('scope') == identity
                          and v.get('state') not in ('done', 'cleanup_pending')]

# ---- Side inbox ----------------------------------------------------------
inbox = SideIntakeInbox(Path(state), identity)
events = []
for area in ('pending', 'receipts'):
    for path in glob.glob(str(inbox.directory / area / '*' / '*.json')):
        row = copy_json(path)
        try:
            data = (json.loads(row['text']).get('data') or {})
        except (ValueError, AttributeError):
            data = {}
        failure = row.get('side_failure') or {}
        result = row.get('result') or {}
        processing = row.get('processing') or {}
        if row.get('stage') != 'received':
            state_name = 'queued_work' if result.get('action') in ('create', 'update', 'prioritize') else 'answered'
        elif processing.get('lease_until', 0) > time.time():
            state_name = 'processing'
        elif failure.get('quarantined'):
            state_name = 'quarantined'
        elif failure:
            state_name = 'retrying'
        else:
            state_name = 'received'
        events.append({
            'id': Path(path).stem[:12], 'type': data.get('event_type') or 'event',
            'message_id': str((data.get('message_context') or {}).get('id') or ''),
            'priority': source_priority(row['text']) or '', 'state': state_name,
            'received_at': row.get('received_at'), 'settled_at': row.get('consumed_at'),
            'action': result.get('action') or '', 'item_id': result.get('item_id') or '',
            'delivery': result.get('delivery') or '', 'summary': clip(result.get('summary'), 110),
            'sent_via': [s.split('__')[-1] for s in result.get('reported_sends') or []],
            'failure': failure.get('category') or '', 'failed_model': failure.get('model') or '',
            'failure_reason': clip(failure.get('reason'), 140), 'attempts': failure.get('attempt_count') or 0,
            'parent_retry': bool(failure.get('use_parent_model')),
        })
events.sort(key=lambda e: e['received_at'] or 0)

# ---- Side model ----------------------------------------------------------
config = copy_json(Path(state) / 'config.json')
side = config.get('work_side_intake') or {}
body = ''
for name in ('router.log.1', 'router.log'):
    log = Path(os.environ['APPDATA']) / 'ciel-runtime' / 'router-instances' / '9505-aae7db361c7f' / name
    if log.exists():
        shutil.copyfile(log, scratch / name)
        body += (scratch / name).read_text(encoding='utf8', errors='replace')
served = set(re.findall(r'upstream_direct_request provider=cielairouter model=(\S+)', body))
registry = copy_json(Path(os.environ['APPDATA']) / 'ciel-runtime' / 'model-registry.json')
catalog = [m if isinstance(m, str) else m.get('id') for m in registry['providers']['cielairouter'].get('models') or []]
failed_models = {name for name, _ in re.findall(r'upstream_direct_http_error provider=cielairouter model=(\S+) .*?code=(4\d\d)', body)}
ok_models = sorted(served - failed_models)
model = {'configured': side.get('model', 'inherit'), 'failed': sorted(failed_models), 'ok': ok_models,
         'main': config.get('providers', {}).get('cielairouter', {}).get('current_model', ''),
         'fallback_served_by': 'claude-opus-5-5',
         'combos': [m for m in catalog if m.startswith('_')], 'catalog_size': len(catalog),
         'examples': [m for m in catalog if not m.startswith('_')][:6]}

snapshot = {'taken_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'session': identity.split(':')[1][:8],
            'work': work, 'parallel': parallel, 'side': {'events': events, 'model': model,
                                                         'max_active': side.get('max_active', 2)}}
Path(out).write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding='utf8')
dump = json.dumps(snapshot, ensure_ascii=False)
print('nodes', len(work['nodes']), 'queue', len(queue), 'events', len(events), 'leases', len(parallel['leases']))
print('forbidden keys present:', [k for k in ('"original"', '"route"', 'reply_token', '"text"', '"reply"', 'preview') if k in dump])
