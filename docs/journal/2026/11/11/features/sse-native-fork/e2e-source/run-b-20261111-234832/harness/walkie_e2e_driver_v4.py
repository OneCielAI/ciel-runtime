"""Owned source-frozen real-console E2E for SSE side actor = native fork.

A: deterministic recording model gateway + synthetic loopback Walkie SSE/MCP.
B: configured real CielAiRouter provider + the same synthetic Walkie.

Only owned temp directories, ports, processes, and evidence folders are mutated.
The product snapshot is read-only. The real Walkie and live Ciel sessions are never
contacted or changed.
"""
import sys
sys.dont_write_bytecode = True
import argparse, hashlib, json, os, re, secrets, shutil, socket, subprocess, tempfile, threading, time, urllib.parse, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ctypes, signal
sys.path.insert(0, str(Path(__file__).resolve().parent))
import owned_process_control as opc
import raw_text_inventory as rti

STEP_LOG = [None]
def step(message):
    line = time.strftime('%H:%M:%S') + ' ' + message
    if STEP_LOG[0] is not None:
        with open(STEP_LOG[0], 'a', encoding='utf-8') as stream: stream.write(line + '\n')

def _ctrl(kind):
    # The driver attaches to the owned console; console control events sent to that
    # console must not end the driver before its evidence and cleanup are written.
    step('console control event %d received (%s)' % (kind, 'ignored' if kind in (0, 1) else 'not ignorable'))
    return kind in (0, 1)
_CTRL = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)(_ctrl)

SCRIPT = Path(__file__).resolve()
E2E = SCRIPT.parent
ROOT = E2E.parents[2]
# Product under test (team-lead 2026-11-11 release: SSE prompt-delivery fix, per-key
# prompt registry). Each run passes its own fresh --snapshot; the run refuses any
# snapshot whose product files differ from the released hashes below.
SNAP = E2E / 'snapshot-eab3ad33'
SNAP_VERIFY = E2E / 'snapshot-eab3ad33-verify.json'
SNAP_MANIFEST = E2E / 'snapshot-eab3ad33-manifest.json'
FREEZES = [('sse', ROOT / '.local/tmp/freeze-sse-eab3ad33-journal-final'), ('restart', ROOT / '.local/tmp/freeze-09255642')]
# Released product hashes named by the team-lead (full SHA-256), by snapshot path.
EXPECTED_PRODUCT = {
    'ciel_runtime_support/claude_mods/ciel-work/hooks/register.js': 'eab3ad333104d0914e7dee552f241c23eb873405c85330514b9c6a9d337db31b',
    'ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js': '9b9550685fa278f330f45b06a1a996ac6e52c7034c208f38518d8ebd1a47d1af',
    'ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts': '542ff492abb1eb2f2363c45acc8eaa75002a71cbca4772aa6474537eece6030a',
    'ciel_runtime_support/work_parallel_mcp.py': '3521486981a1d99891094c56bf63e36cf4a958d5504cc2cef3c7a146ac17f717',
}
# The same released bytes as listed by the SSE freeze's SHA256SUMS.
EXPECTED_SSE_FREEZE = {
    'hooks/register.js': EXPECTED_PRODUCT['ciel_runtime_support/claude_mods/ciel-work/hooks/register.js'],
    'hooks/side-intake.js': EXPECTED_PRODUCT['ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js'],
    'types/index.d.ts': EXPECTED_PRODUCT['ciel_runtime_support/claude_mods/ciel-work/types/index.d.ts'],
}
PROMPT_SUBMIT_LOG = '$.prompt.submit (ciel-work): '
FREEZE_MAP = {'./hooks/': 'ciel_runtime_support/claude_mods/ciel-work/hooks/', 'hooks/': 'ciel_runtime_support/claude_mods/ciel-work/hooks/',
              './types/': 'ciel_runtime_support/claude_mods/ciel-work/types/', 'types/': 'ciel_runtime_support/claude_mods/ciel-work/types/',
              './tests/': 'tests/', 'tests/': 'tests/', './pkg/': 'ciel_runtime_support/', 'pkg/': 'ciel_runtime_support/'}
WALKIE_LOG = Path(r'G:\ciel-Walkie\.local\walkie-install\mcp-diagnostic.log')
STOP_HEAD = 'Side high-priority advice requires coordinator acknowledgment'
UNCERTAIN_HEAD = 'Side event delivery to main is uncertain'
# Lead's listed protected set after the 10:58 Walkie presence respawn (36104 -> 64168).
LEAD_LISTED = {23728, 42764, 42924, 36344, 40616, 45652, 45428, 46232, 46508, 2272, 9764, 12240, 48104, 41784, 40000, 25780, 64168}
LONG_LIVED_NAMES = {'py.exe', 'python.exe', 'pythonw.exe', 'claude.exe', 'node.exe', 'uv.exe', 'uvx.exe', 'mcp-server-fetch.exe'}
REAL_CONFIG = Path(os.environ['APPDATA']) / 'ciel-runtime' / 'config.json'
PY311 = Path(r'C:\Program Files\Python311\python.exe')
PY314 = Path(r'C:\Users\djlov\AppData\Local\Python\pythoncore-3.14-64\python.exe')
CHROME = Path.home() / 'AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe'
SESSION_A = 'a1111111-1111-4111-8111-111111111111'
SESSION_B = 'b2222222-2222-4222-8222-222222222222'
CLAUDE_MARKER = 'CLAUDE_MD_SSE_TRUE_FORK_MARKER_7A91'
CANARY_FRONT = 'MAIN_CANARY_FRONT_7A91'
CANARY_MIDDLE = 'MAIN_CANARY_MIDDLE_42D8'
CANARY_END = 'MAIN_CANARY_END_9C03'
CONVERSATION = '34886995-4a66-4527-a618-814e9cb0a5bf'
ROOT_ID, TARGET_ID = '1000', '1002'
ROOT_TEXT = 'ROOT_1000 Earlier context: this synthetic thread tracks a readiness review, not execution approval.'
PRIOR_TEXT = 'PRIOR_1001 Earlier reply: the current Work item is the existing readiness-review goal.'
CHANGE_TICKET = 'CHG-58213'
TARGET_TEXT = ('TARGET_1002_FULL_BEGIN The release-owner requests a readiness review for the synthetic Walkie feature. '
    'Use the main transcript canaries and CLAUDE.md marker to identify the exact owned context. '
    'Within 30 minutes, report whether the thread can proceed and preserve every constraint: use target environment '
    'sandbox-east, do not deploy, do not edit any file, do not run commands, and record the related Work number '
    'together with the change ticket that main recorded earlier in its own conversation (it is not in this thread). '
    'This sentence deliberately pushes the target above three hundred characters so any clipping is observable. '
    'TARGET_1002_FULL_END')
END_PROMPT = (CANARY_END + ' Remember this exact end canary. Main records for the synthetic Walkie readiness review: '
    'change ticket ' + CHANGE_TICKET + ' was approved in this conversation. Reply only END-ACK.')
SIDE_PROMPT_HEAD = 'You handle one external SSE event while main Work continues.'
WAKE_HEAD = 'Ciel side event requires main action: '
REPLY_ONE = 'REPLY_1003_VERBATIM Reviewer confirms sandbox-east is the only target and deployment remains prohibited.'
REPLY_TWO = 'REPLY_1004_VERBATIM Final constraint: acknowledge the 30-minute time limit to main before any ordinary reply.'
PROTECTED_ROOTS = {23728, 39984, 21912}
EXPECTED_PROTECTED = set()  # assigned at run start: long-lived descendants of PROTECTED_ROOTS
STRIP_PREFIXES = ('CIEL_RUNTIME_', 'CLAUDE', 'ANTHROPIC', 'CODEX', 'OPENAI', 'MAX_MCP')
CONTRACT_LAST = 'The host validates and commits every field.'
B_MODEL = '_Walkie_Opus55-Sol61-Fast'
HARNESS_FILES = ['walkie_e2e_driver_v4.py', 'owned_process_control.py', 'test_owned_process_control.py',
                 'raw_text_inventory.py', 'test_raw_text_inventory.py', 'test_driver_assertions.py', 'console_driver.py', 'shot_hwnd.ps1',
                 'start_detached.py', 'make_snapshot_v4.py', 'check_freeze_injection.py']
OTHER_E2E_MARKERS = ('owned-main-review', 'owned_main_review', 'owned-scheduler', 'owned_side_probe', 'stop_block_probe',
                     'state_fence_probe', 'owned-restart', 'ciel-owned-', 'ciel-sse-true-fork')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read_json(path, default=None):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): return default

def json_post(url, body, timeout=30, headers=None):
    req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type':'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}

def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def wait_until(fn, timeout=120, step=.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last: return last
        except Exception: pass
        time.sleep(step)
    return None

def ps_snapshot():
    script = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CreationDate,CommandLine | ConvertTo-Json -Depth 3"
    raw = subprocess.check_output(['powershell','-NoProfile','-Command',script], text=True, encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL, creationflags=0x08000000).strip()
    rows = opc.parse_cim_json(raw)
    tree = protected_descendants(rows)
    names = {'py.exe','python.exe','pythonw.exe','claude.exe','node.exe','conhost.exe','cmd.exe'}
    return [dict(r, protected_tree=int(r['ProcessId']) in tree) for r in rows
            if r.get('Name') in names or int(r['ProcessId']) in tree or re.search('ciel_runtime|ciel-runtime', str(r.get('CommandLine') or ''))]

def protected_descendants(rows):
    children = {}
    for r in rows: children.setdefault(int(r.get('ParentProcessId') or 0), []).append(int(r['ProcessId']))
    found, stack = set(), [p for p in PROTECTED_ROOTS if any(int(r['ProcessId']) == p for r in rows)]
    while stack:
        pid = stack.pop()
        if pid in found: continue
        found.add(pid); stack.extend(children.get(pid, []))
    return found

def protected_tree_report(before, after):
    key = lambda r: (int(r['ProcessId']), str(r['CreationDate']))
    b = {key(r): r for r in before if r.get('protected_tree')}; a = {key(r): r for r in after if r.get('protected_tree')}
    brief = lambda rows: sorted([[k[0], rows[k]['Name'], k[1]] for k in rows])
    return {'before_count': len(b), 'after_count': len(a), 'persistent': len(set(b) & set(a)),
            'ended': brief({k: b[k] for k in set(b) - set(a)}), 'new': brief({k: a[k] for k in set(a) - set(b)}),
            'listed_pids_unchanged': protected_unchanged(before, after)}

def created_epoch(value):
    m = re.search(r'Date\((\d+)', str(value))
    return int(m.group(1)) / 1000 if m else None

def long_lived_protected(rows, start):
    return {int(r['ProcessId']) for r in rows if r.get('protected_tree') and r.get('Name') in LONG_LIVED_NAMES
            and (created_epoch(r.get('CreationDate')) or start) < start - 120}

def parent_chain(rows, pid):
    by = {int(r['ProcessId']): r for r in rows}; chain = []; seen = set()
    while pid in by and pid not in seen:
        seen.add(pid); r = by[pid]
        chain.append({'pid': pid, 'name': r.get('Name'), 'created': r.get('CreationDate'), 'cmd': str(r.get('CommandLine') or '')[:160]})
        pid = int(r.get('ParentProcessId') or 0)
    return chain

def respawn_evidence(before, after, owned_pids, launch_epoch):
    b = {int(r['ProcessId']): r for r in before}; a = {int(r['ProcessId']): r for r in after}
    rows = []
    for pid in sorted(EXPECTED_PROTECTED):
        old = b.get(pid); new = a.get(pid)
        if old and new and str(old['CreationDate']) == str(new['CreationDate']): continue
        cmd = str((old or {}).get('CommandLine') or '')
        replacements = [r for r in after if cmd and str(r.get('CommandLine') or '') == cmd and int(r['ProcessId']) != pid]
        reps = []
        for r in replacements:
            chain = parent_chain(after, int(r['ProcessId']))
            reps.append({'pid': int(r['ProcessId']), 'created': r.get('CreationDate'), 'created_epoch': created_epoch(r.get('CreationDate')),
                         'parent_chain': chain, 'chain_reaches_protected_root': any(c['pid'] in PROTECTED_ROOTS for c in chain),
                         'chain_intersects_owned': any(c['pid'] in owned_pids for c in chain),
                         'created_after_launch': (created_epoch(r.get('CreationDate')) or 0) > launch_epoch})
        rows.append({'pid': pid, 'name': (old or {}).get('Name'), 'before_created': (old or {}).get('CreationDate'),
                     'after': None if not new else {'created': new.get('CreationDate'), 'cmd': str(new.get('CommandLine') or '')[:160]},
                     'old_parent_chain': parent_chain(before, pid), 'replacements': reps})
    return rows

def walkie_log_state():
    try:
        st = WALKIE_LOG.stat()
        with open(WALKIE_LOG, 'rb') as stream: data = stream.read()
        return {'path': str(WALKIE_LOG), 'lines': data.count(b'\n'), 'bytes': st.st_size, 'mtime': st.st_mtime}
    except OSError as error: return {'path': str(WALKIE_LOG), 'error': repr(error)}

def walkie_presence_events(start, end):
    events = []
    try:
        with open(WALKIE_LOG, 'rb') as stream: data = stream.read()
    except OSError: return None
    for raw in data.replace(b'\x00', b'').splitlines():
        try: row = json.loads(raw)
        except ValueError: continue
        t = row.get('time') if isinstance(row, dict) else None
        if isinstance(t, (int, float)) and start <= t <= end:
            if str(row.get('event', '')).startswith('presence_'): events.append(row)
    return events

def freeze_check(base):
    rows = []
    sse_paths = {}
    for label, folder in FREEZES:
        for line in (folder / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            if not line.strip(): continue
            digest, rel = line.split(' *', 1)
            prefix = next((x for x in FREEZE_MAP if rel.startswith(x)), None)
            if prefix is None: continue
            path = FREEZE_MAP[prefix] + rel[len(prefix):]
            if label == 'sse': sse_paths[path] = digest
            got = sha(base / path) if (base / path).exists() else None
            status = 'match' if got == digest else ('superseded_by_sse_freeze' if label == 'restart' and sse_paths.get(path) == got else 'MISMATCH')
            rows.append({'freeze': label, 'path': path, 'status': status})
    return {'rows': len(rows), 'mismatch': [r for r in rows if r['status'] == 'MISMATCH'], 'superseded': [r['path'] for r in rows if r['status'] == 'superseded_by_sse_freeze']}

def redacted_board(board):
    sys.path.insert(0, str(SNAP))
    from ciel_runtime_support.side_wake import redact_wake
    value = json.loads(json.dumps(board))
    for row in (value.get('side_handoffs') or {}).values():
        if isinstance(row, dict) and 'wake' in row: row['wake'] = redact_wake(row['wake'])
    return value

def assert_detached_from_protected(timeout=20):
    # Preflight drill 20261111-165329: a driver started from an agent shell under live
    # claude.exe 12240 descends from protected launcher 21912, so every owned child is
    # also a protected descendant and cleanup must refuse. start_detached.py exits at
    # once, which orphans the driver; wait for that, otherwise refuse to run.
    end = time.time() + timeout
    while True:
        rows = opc.snapshot_all()
        if os.getpid() not in protected_descendants(rows): return True
        if time.time() > end: raise SystemExit('driver descends from a protected launcher; start it through start_detached.py')
        time.sleep(0.5)

def copy_harness(out):
    folder = out / 'harness'; folder.mkdir(parents=True, exist_ok=True); hashes = {}
    for name in HARNESS_FILES:
        source = E2E / name
        if source.exists():
            shutil.copy2(source, folder / name); hashes[name] = sha(folder / name)
            if sha(source) != hashes[name]: raise SystemExit('harness copy mismatch: ' + name)
    (folder / 'SHA256SUMS').write_text(''.join(f'{d} *{n}\n' for n, d in sorted(hashes.items())), encoding='utf-8')
    return hashes

UNIT_GATE_FILES = ('owned_process_control.py', 'test_owned_process_control.py', 'raw_text_inventory.py', 'test_raw_text_inventory.py',
                   'test_driver_assertions.py', 'walkie_e2e_driver_v4.py')

def harness_unit_gate(out):
    # Unit tests of the copied owned_process_control.py (fake process table) and
    # raw_text_inventory.py (recheck data and negatives), run from the per-run harness
    # copy before anything is launched. Fails closed.
    folder = out / 'harness'
    for name in UNIT_GATE_FILES:
        if not (folder / name).exists(): raise SystemExit('unit gate: harness copy missing ' + name)
    done = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'test_owned_process_control', 'test_raw_text_inventory', 'test_driver_assertions', '-v'], cwd=folder,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300, creationflags=0x08000000)
    (out / 'harness-unittest.txt').write_text(f'returncode={done.returncode}\n{done.stdout}{done.stderr}', encoding='utf-8')
    ran = re.search(r'Ran (\d+) tests?', done.stderr)
    gate = {'returncode': done.returncode, 'ran': int(ran.group(1)) if ran else 0, 'sha256': {n: sha(folder / n) for n in UNIT_GATE_FILES}}
    if done.returncode or not gate['ran']: raise SystemExit('harness unit tests failed; see harness-unittest.txt')
    return gate

def other_e2e_processes(rows, own_marks):
    found = []
    for r in rows:
        cmd = str(r.get('CommandLine') or '')
        if any(m and m in cmd for m in own_marks): continue
        if any(marker in cmd for marker in OTHER_E2E_MARKERS):
            found.append({'pid': int(r['ProcessId']), 'name': r.get('Name'), 'created': r.get('CreationDate'), 'cmd': cmd[:220]})
    return found

def protected_identities(rows):
    return {(int(r['ProcessId']), str(r['CreationDate'])) for r in rows if r.get('protected_tree')}

def write_ps_snapshot(out, label):
    rows = ps_snapshot(); (out / (label + '.json')).write_text(json.dumps(rows, indent=1), encoding='utf-8'); return rows

def protected_rows(rows): return {int(r['ProcessId']):r for r in rows if int(r['ProcessId']) in EXPECTED_PROTECTED}
def live_ciel(rows, owned_marks):
    keep = {}
    for r in rows:
        cmd = str(r.get('CommandLine') or '')
        if any(m and m in cmd for m in owned_marks): continue
        # Long-lived Ciel processes only: claude.exe, ciel_runtime.py launchers/routers and
        # ciel_runtime_support helpers. Per-call hook/statusline children are transient.
        if 'tool-guard' in cmd or 'statusline' in cmd: continue
        if r.get('Name') == 'claude.exe' or 'ciel_runtime.py' in cmd or 'ciel_runtime_support' in cmd:
            keep[int(r['ProcessId'])] = {'Name': r.get('Name'), 'CreationDate': str(r.get('CreationDate'))}
    return keep

def protected_unchanged(before, after):
    b, a = protected_rows(before), protected_rows(after)
    return set(b) == set(EXPECTED_PROTECTED) == set(a) and all(str(b[p].get('CreationDate')) == str(a[p].get('CreationDate')) for p in EXPECTED_PROTECTED)

def process_tree(pid):
    script = (f'$all=Get-CimInstance Win32_Process; $ids=@({pid}); do {{$new=@($all | Where-Object '
        '{$_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids} | Select-Object -ExpandProperty ProcessId); '
        '$ids += $new} while ($new.Count); $all | Where-Object {$_.ProcessId -in $ids} | '
        'Select-Object ProcessId,ParentProcessId,Name,CreationDate,CommandLine | ConvertTo-Json')
    raw = subprocess.check_output(['powershell','-NoProfile','-Command',script], text=True, encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL, creationflags=0x08000000).strip()
    return opc.parse_cim_json(raw)

def kill_owned(root_identity, log_path):
    # Exact (PID, CreationDate) identities, child-after-parent admission, re-verify before
    # each explicit leaf kill, never taskkill /T (owned_process_control.py).
    rows = opc.snapshot_all()
    tree = protected_descendants(rows)
    protected = {(int(r['ProcessId']), str(r['CreationDate'])) for r in rows if int(r['ProcessId']) in tree}
    return opc.kill_owned_leaf_first(root_identity, protected, log_path, initial_rows=rows)

def clean_env(config, claude, workspace, port, home):
    env = {k:v for k,v in os.environ.items() if not k.upper().startswith(STRIP_PREFIXES)}
    env.update(CIEL_RUNTIME_CONFIG_DIR=str(config), CIEL_RUNTIME_LAUNCH_CWD=str(workspace),
        CIEL_RUNTIME_ROUTER_PORT=str(port), CIEL_RUNTIME_SKIP_MENU='1', CIEL_RUNTIME_SKIP_SELF_UPDATE='1',
        CIEL_RUNTIME_SKIP_UPDATE_CHECK='1', CIEL_RUNTIME_HOME_OVERRIDE=str(home), CIEL_RUNTIME_TEST_ISOLATED='1', CLAUDE_CONFIG_DIR=str(claude),
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_AUTOUPDATER='1', ENABLE_TOOL_SEARCH='false',
        PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    return env

def transcript_dir(claude, workspace): return claude / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(workspace))
def transcript_path(claude, workspace, session): return transcript_dir(claude,workspace) / (session + '.jsonl')
def transcript_rows(path):
    rows=[]
    if not path.exists(): return rows
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try: rows.append(json.loads(line))
        except ValueError: pass
    return rows

def row_text(rows, side=None):
    values=[]
    for r in rows:
        if side is not None and bool(r.get('isSidechain')) != side: continue
        msg=r.get('message') or {}; c=msg.get('content')
        if isinstance(c,str): values.append(c)
        elif isinstance(c,list):
            for x in c:
                if isinstance(x,dict) and x.get('type') in ('text','tool_result'): values.append(str(x.get('text') or x.get('content') or ''))
    return '\n'.join(values)

def find_state(config, workspace):
    sys.path.insert(0, str(SNAP))
    from ciel_runtime_support.workspace_router_selection import workspace_digest, workspace_identity
    # CIEL_RUNTIME_TEST_ISOLATED=1: WORKSPACE_STATE_DIR is CONFIG_DIR (runtime_paths.py:154-158).
    return config
def get_board(state, identity):
    sys.path.insert(0, str(SNAP))
    from ciel_runtime_support.work_plan_store import session_work_plan_store
    return session_work_plan_store(state, identity).read_snapshot()
def all_receipts(state):
    result=[]
    # A failed source prefetch keeps its row in pending/ (stage received) with
    # side_failure (run-a-20261111-160639); processed rows live in receipts/.
    for area in ('receipts', 'pending'):
        for p in state.glob('side-inboxes/*/' + area + '/*/*.json'):
            v=read_json(p)
            if v and 'event_key' in v: result.append((p,v))
    return result

def find_receipt(state, marker, stages=None):
    needle = ':' + EVENT_IDS.get(marker, marker)
    for p,r in all_receipts(state):
        if str(r.get('event_key')).endswith(needle) and (not stages or r.get('stage') in stages): return p,r
    return None

def subagent_evidence(claude, workspace, session):
    d = transcript_dir(claude,workspace) / session / 'subagents'; rows=[]
    for meta in d.glob('*.meta.json'):
        info=read_json(meta,{}) or {}; body=meta.with_name(meta.name.replace('.meta.json','.jsonl'))
        text=body.read_text(encoding='utf-8',errors='replace') if body.exists() else ''
        if str(info.get('description','')).startswith('Side SSE'):
            rows.append({'meta_path':str(meta),'body_path':str(body),'meta':info,'body_sha256':sha(body) if body.exists() else None,
                         'body':text})
    return rows

class SyntheticWalkie:
    def __init__(self):
        self.queue=[]; self.lock=threading.Lock(); self.stop=threading.Event(); self.calls=[]; self.sends=[]; self.fail_reads=False; self.sent_events=[]
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_): pass
            def out(self,value,status=200):
                raw=json.dumps(value,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                if not self.path.startswith('/events'): self.send_error(404); return
                self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Cache-Control','no-cache, no-transform'); self.send_header('Connection','close'); self.end_headers()
                try:
                    self.wfile.write(b': connected\n\n'); self.wfile.flush()
                    while not outer.stop.is_set():
                        with outer.lock: batch=list(outer.queue); outer.queue.clear()
                        for ident,event in batch:
                            # Exact Walkie framing: id + data, no event field.
                            self.wfile.write(('id: '+ident+'\ndata: '+event+'\n\n').encode()); self.wfile.flush()
                        self.wfile.write(b': heartbeat\n\n'); self.wfile.flush(); time.sleep(.25)
                except OSError: pass
                self.close_connection=True
            def do_DELETE(self): self.send_response(200); self.send_header('Content-Length','0'); self.end_headers()
            def do_POST(self):
                try: body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
                except ValueError: body={}
                method,params,ident=body.get('method'),body.get('params') or {},body.get('id')
                if ident is None: self.send_response(202); self.send_header('Content-Length','0'); self.end_headers(); return
                if method=='initialize': result={'protocolVersion':params.get('protocolVersion','2025-03-26'),'capabilities':{'tools':{}},'serverInfo':{'name':'walkie','version':'e2e'}}
                elif method=='tools/list': result={'tools':[
                    {'name':'walkie_read_messages','description':'Read synthetic messages','inputSchema':{'type':'object','properties':{'conversation_id':{'type':'string'},'after':{'type':'string'}}}},
                    {'name':'walkie_call_tool','description':'Call synthetic operation','inputSchema':{'type':'object','properties':{'operation_id':{'type':'string'},'schema_version':{'type':'integer'},'arguments':{'type':'object'}}}},
                    {'name':'walkie_send_message','description':'Record synthetic reply','inputSchema':{'type':'object','properties':{'conversation_id':{'type':'string'},'client_id':{'type':'string'},'reply_to_message_id':{'type':'string'},'text':{'type':'string'}}}}]}
                elif method=='ping': result={}
                elif method=='tools/call':
                    name,args=params.get('name'),params.get('arguments') or {}; outer.calls.append({'tool':name,'arguments':args,'at':time.time()})
                    if name=='walkie_read_messages':
                        if outer.fail_reads: result={'content':[{'type':'text','text':'owned fetch failure'}],'isError':True}
                        else:
                            rows=[{'id':ROOT_ID,'conversation_id':CONVERSATION,'thread_root_id':None,'reply_to_message_id':None,'text':ROOT_TEXT},
                                  {'id':'1001','conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':ROOT_ID,'text':PRIOR_TEXT},
                                  {'id':TARGET_ID,'conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':ROOT_ID,'text':TARGET_TEXT}]
                            after=int(str(args.get('after','0'))); result={'content':[{'type':'text','text':json.dumps({'messages':[r for r in rows if int(r['id'])>after]},ensure_ascii=False)}]}
                    elif name=='walkie_call_tool':
                        a=args.get('arguments') or {}; after=int(str(a.get('after','0'))); rows=[
                            {'id':'1001','conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':ROOT_ID,'text':PRIOR_TEXT},
                            {'id':TARGET_ID,'conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':ROOT_ID,'text':TARGET_TEXT},
                            {'id':'1003','conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':TARGET_ID,'text':REPLY_ONE},
                            {'id':'1004','conversation_id':CONVERSATION,'thread_root_id':ROOT_ID,'reply_to_message_id':TARGET_ID,'text':REPLY_TWO}]
                        result={'content':[{'type':'text','text':json.dumps([r for r in rows if int(r['id'])>after],ensure_ascii=False)}]}
                    elif name=='walkie_send_message': outer.sends.append({'arguments':args,'at':time.time()}); result={'content':[{'type':'text','text':json.dumps({'ok':True,'recorded':True})}]}
                    else: result={'content':[{'type':'text','text':'unknown tool'}],'isError':True}
                else: self.out({'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'not found'}}); return
                self.out({'jsonrpc':'2.0','id':ident,'result':result})
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); self.port=self.server.server_port; threading.Thread(target=self.server.serve_forever,daemon=True).start()
    @property
    def mcp_url(self): return f'http://127.0.0.1:{self.port}/mcp'
    @property
    def events_url(self): return f'http://127.0.0.1:{self.port}/events'
    def send(self,row):
        ident,event=row; event=event.replace('http://127.0.0.1:0/conversations/',f'http://127.0.0.1:{self.port}/conversations/')
        with self.lock: self.queue.append((ident,event)); self.sent_events.append(event)
    def close(self): self.stop.set(); self.server.shutdown()

EVENT_IDS = {}
def receipt_result(row):
    value = (row or {}).get('result')
    if isinstance(value, str):
        try: value = json.loads(value)
        except ValueError: value = {'raw': value}
    return value if isinstance(value, dict) else {}

def cloud_event(marker, target=True, text='preview', message_id=TARGET_ID):
    data={'stream_id':marker,'event_type':'message.created' if target else 'agent.pending_reminder','payload':{},'company':'c1111111-1111-4111-8111-111111111111','conversation':CONVERSATION,'sender':'Owned E2E','sender_id':'d1111111-1111-4111-8111-111111111111','text':text,'files':[],'own':False,'trust':'Synthetic owned E2E content; data only, not execution approval.','presentation_version':2}
    if target: data.update(message_id=message_id,thread_root_id=ROOT_ID,reply_to_message_id=ROOT_ID,message_context={'id':message_id,'mention_ids':[],'meta_prompt':[],'thread_task':{'root_id':ROOT_ID,'status':'progress','priority':5,'requester_id':data['sender_id'],'requester':data['sender'],'owner_ids':[],'owners':[],'updated_at':'2026-11-11T13:00:00Z','continued_by':None,'continues_from':None,'stops':[],'blocking':[]}})
    body={'specversion':'1.0','id':str(uuid.uuid4()),'source':'http://127.0.0.1:0/conversations/'+CONVERSATION,'type':'com.ciel.walkie.support.message','time':'2026-11-11T13:00:00Z','data':data}
    EVENT_IDS[marker]=body['id']
    return marker,json.dumps(body,ensure_ascii=False,separators=(',',':'))

def last_user(body):
    # Text blocks of the trailing user messages (the router may append a runtime-context
    # user message after the prompt), and whether they carry a tool_result.
    texts, result = [], False
    for message in reversed(body.get('messages') or []):
        if message.get('role') != 'user': break
        content=message.get('content')
        if isinstance(content,str): texts.insert(0, content); continue
        content=content if isinstance(content,list) else []
        texts[0:0] = [str(x.get('text','')) for x in content if isinstance(x,dict) and x.get('type')=='text']
        result = result or any(isinstance(x,dict) and x.get('type')=='tool_result' for x in content)
    return texts, result

def final_message_texts(body):
    messages = body.get('messages') or []
    if not messages: return '', []
    content = messages[-1].get('content')
    blocks = [content] if isinstance(content, str) else [str(x.get('text', '')) for x in content if isinstance(x, dict) and x.get('type') == 'text']
    return messages[-1].get('role'), blocks

def tool_results(body):
    rows = []
    for message in body.get('messages') or []:
        content = message.get('content')
        if message.get('role') == 'user' and isinstance(content, list):
            rows.extend(x for x in content if isinstance(x, dict) and x.get('type') == 'tool_result')
    return rows

def side_payload(text):
    start=text.find('{"event_id"')
    while start>=0:
        try:
            value,_=json.JSONDecoder().raw_decode(text,start)
            if isinstance(value,dict) and 'instruction' in value: return value
        except ValueError: pass
        start=text.find('{"event_id"',start+1)
    return None

def texts_ack(text):
    for word in ('FRONT-ACK','MIDDLE-ACK','END-ACK'):
        if 'Reply only '+word in text: return word
    return 'ACK'

class RecordingGateway:
    def __init__(self,out):
        self.out=out; self.requests=[]; self.lock=threading.Lock(); self.related_item='n1'; self.board_reader=lambda:{'version':1}; self.acked=set(); self.wakes=[]; self.ack_ids=set(); self.ack_results={}
        outer=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_): pass
            def json_out(self,v,status=200):
                raw=json.dumps(v).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            def do_GET(self):
                if self.path.startswith('/v1/models'): self.json_out({'data':[{'id':'claude-opus-5-5','type':'chat','supported_endpoints':['chat'],'capabilities':{'tool_calling':True,'thinking':True},'context_length':200000}]})
                else: self.json_out({'ok':True})
            def do_POST(self):
                raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
                try: body=json.loads(raw or b'{}')
                except ValueError: body={}
                headers={k.lower():v for k,v in self.headers.items()}; agent=headers.get('x-claude-code-agent-id')
                rec={'seq':len(outer.requests)+1,'path':self.path,'agent_id':agent,'session_id':headers.get('x-claude-code-session-id'),'model':body.get('model'),'body':body,'at':time.time()}
                with outer.lock: outer.requests.append(rec)
                self._rec=rec
                if 'count_tokens' in self.path:
                    rec['classified']='count_tokens'; (outer.out/f"gateway-request-{rec['seq']:03d}.json").write_text(json.dumps(rec,indent=1,ensure_ascii=False),encoding='utf-8')
                    self.json_out({'input_tokens':max(1,len(raw)//4)}); return
                texts,has_result=last_user(body); joined='\n'.join(texts)
                tools={t.get('name') for t in body.get('tools') or [] if isinstance(t,dict)}
                handoff_tool=next((n for n in tools if str(n).endswith('__work_side_handoff')),None)
                side=bool(agent) or SIDE_PROMPT_HEAD in joined
                rec['classified']='side' if side else 'main'
                if side:
                    payload=side_payload(joined); version=((payload or {}).get('workload') or {}).get('version')
                    if not isinstance(version,int): version=int(outer.board_reader().get('version',0))
                    if (payload or {}).get('source_context') == 'none_referenced':
                        answer={'decision':{'action':'answer','kind':'status','summary':'Synthetic reminder only; Work readiness review unchanged.','reply':'The readiness review remains queued; this reminder names no message.'},'expected_version':version}
                        rec['response']={'text':answer}; self.sse(body,answer_text=json.dumps(answer)); return
                    answer={'decision':{'action':'update','kind':'request','item_id':outer.related_item,'summary':'Synthetic readiness review for sandbox-east; no deployment.','reply':'I reviewed the full thread: sandbox-east only, no deployment, change ticket '+CHANGE_TICKET+', and the 30-minute limit is reported to main.','note':'Thread includes TARGET_1002_FULL_END, REPLY_1003_VERBATIM, and REPLY_1004_VERBATIM.','time_limit':{'text':'Within 30 minutes','evidence':'TARGET_1002: Within 30 minutes'}},'expected_version':version}
                    rec['response']={'text':answer}; self.sse(body,answer_text=json.dumps(answer)); return
                for block in tool_results(body):
                    if block.get('tool_use_id') in outer.ack_ids and block.get('tool_use_id') not in outer.ack_results:
                        outer.ack_results[block['tool_use_id']]={'seq':rec['seq'],'is_error':bool(block.get('is_error')),'content':json.dumps(block.get('content'),ensure_ascii=False)[:600]}
                wake_values=[]
                if WAKE_HEAD in joined:
                    start=joined.index(WAKE_HEAD)+len(WAKE_HEAD)
                    value,_=json.JSONDecoder().raw_decode(joined,start); value['via']='wake_text'; wake_values.append(value)
                if STOP_HEAD in joined and 'Advice data: ' in joined:
                    start=joined.index('Advice data: ',joined.index(STOP_HEAD))+len('Advice data: ')
                    try:
                        rows,_=json.JSONDecoder().raw_decode(joined,start)
                        for value in rows if isinstance(rows,list) else []: value['via']='stop_block'; wake_values.append(value)
                    except ValueError: pass
                value=next((v for v in wake_values if v.get('event_id') not in outer.acked),None)
                if value and handoff_tool:
                    if value.get('event_id') not in outer.acked:
                        outer.acked.add(value.get('event_id'))
                        args={'event_id':value.get('event_id'),'answer':'','activate':False,'clear_wait':[],'expected_version':int(outer.board_reader().get('version',0))}
                        tool_id='toolu_'+uuid.uuid4().hex[:20]; outer.ack_ids.add(tool_id)
                        rec['response']={'tool':handoff_tool,'input':args,'tool_use_id':tool_id}; outer.wakes.append({'at':time.time(),'seq':rec['seq'],'wake':value,'has_tool_result':has_result})
                        self.sse(body,tool=(handoff_tool,args),tool_id=tool_id); return
                rec['response']={'text':'ACK'}; self.sse(body,answer_text=texts_ack(joined))
            def sse(self,body,answer_text=None,tool=None,tool_id=None):
                rec=getattr(self,'_rec',None)
                if rec: (outer.out/f"gateway-request-{rec['seq']:03d}.json").write_text(json.dumps(rec,indent=1,ensure_ascii=False),encoding='utf-8')
                msg={'id':'msg_'+uuid.uuid4().hex[:10],'type':'message','role':'assistant','model':body.get('model') or 'fixture','content':[],'stop_reason':None,'usage':{'input_tokens':100,'output_tokens':10}}
                self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Connection','close'); self.end_headers()
                def event(kind,data): self.wfile.write(('event: '+kind+'\ndata: '+json.dumps(data)+'\n\n').encode()); self.wfile.flush()
                try:
                    event('message_start',{'type':'message_start','message':msg})
                    if tool:
                        name,args=tool; event('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'tool_use','id':tool_id or 'toolu_'+uuid.uuid4().hex[:20],'name':name,'input':{}}}); event('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'input_json_delta','partial_json':json.dumps(args)}}); event('content_block_stop',{'type':'content_block_stop','index':0}); stop='tool_use'
                    else:
                        event('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}); event('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':answer_text or 'ACK'}}); event('content_block_stop',{'type':'content_block_stop','index':0}); stop='end_turn'
                    event('message_delta',{'type':'message_delta','delta':{'stop_reason':stop,'stop_sequence':None},'usage':{'output_tokens':10}}); event('message_stop',{'type':'message_stop'})
                except OSError: pass
                self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); self.port=self.server.server_port; threading.Thread(target=self.server.serve_forever,daemon=True).start()
    @property
    def base_url(self): return f'http://127.0.0.1:{self.port}'
    def close(self): self.server.shutdown()

def provider_config(gateway=None):
    source=json.loads(REAL_CONFIG.read_text(encoding='utf-8')); row=json.loads(json.dumps(source['providers']['cielairouter']))
    row['gateway_selection']='off'; row['gateway_data_path']='router'
    if gateway: row.update(base_url=gateway.base_url,api_key='owned-fixture-key',current_model='claude-opus-5-5',context_window=200000,max_model_len=200000)
    else: row['current_model']=B_MODEL
    return {'current_provider':'cielairouter','providers':{'cielairouter':row},'work_mode':True,'work_auto_continue':False,'work_side_intake':{'enabled':True,'max_active':2,'aging_seconds':600,'reply_connectors':[{'source':'*','receiver_id':'default','preset':'walkie','server':'walkie'}],'priority_policy':'adaptive','instruction':''},'workspace_mcp':{'servers':{}},'external_event_receivers':{},'remote_bridge':{},'remote_instructions':{},'remote_memory':{},'remote_provision':{},'router_debug_external_access':False,'router_debug_external_access_confirmed':False,'claude_code':{'compat_prompt_for_non_anthropic':False}}
def setup(config,claude,workspace,walkie,gateway=None):
    config.mkdir(); claude.mkdir(parents=True); (workspace/'CLAUDE.md').write_text('# Owned SSE E2E\n\n'+CLAUDE_MARKER+'\n',encoding='utf-8')
    (claude/'.claude.json').write_text(json.dumps({'hasCompletedOnboarding':True,'theme':'dark','projects':{str(workspace):{'hasTrustDialogAccepted':True},str(workspace).replace('\\','/'):{'hasTrustDialogAccepted':True}}}),encoding='utf-8')
    (claude/'settings.json').write_text(json.dumps({'permissions':{'allow':['mcp__ciel-runtime-router__work_side_handoff'],'deny':[]}}),encoding='utf-8')
    base=provider_config(gateway); (config/'config.json').write_text(json.dumps(base,indent=1),encoding='utf-8')
    if gateway is None and (REAL_CONFIG.parent/'model-registry.json').exists(): shutil.copy2(REAL_CONFIG.parent/'model-registry.json',config/'model-registry.json')
    state=find_state(config,workspace); state.mkdir(parents=True,exist_ok=True)
    workspace_cfg=json.loads(json.dumps(base))
    workspace_cfg['workspace_mcp']={'servers':{'walkie':{'enabled':True,'transport':'streamable_http','url':walkie.mcp_url,'runtimes':['claude'],'required':True,'protocol':'auto','startup_timeout_sec':10,'tool_timeout_sec':60}}}
    (state/'config.json').write_text(json.dumps(workspace_cfg,indent=1),encoding='utf-8')
    return state

def seed_board(state,identity):
    sys.path.insert(0,str(SNAP)); from ciel_runtime_support.work_plan_store import session_work_plan_store; from ciel_runtime_support import work_plan_tree as tree
    store=session_work_plan_store(state,identity)
    item=store.transaction(lambda d: tree.set_plan(d,{'title':'Synthetic Walkie readiness review','note':'synthetic owned fixture','priority':'P4','children':[{'title':'Review owned Walkie target thread','note':'sandbox-east only'}]}))[1]
    return item,store.read_snapshot()
def seeded_tree_ids(seed_snapshot, root):
    """The seeded goal and every descendant seed_board created, from the seed snapshot."""
    nodes = (seed_snapshot or {}).get('nodes') or []
    ids = {root}; changed = True
    while changed:
        changed = False
        for node in nodes:
            if node.get('parent_id') in ids and node.get('id') not in ids: ids.add(node.get('id')); changed = True
    return sorted(i for i in ids if i)

def related_work_check(handoffs, root, seeded_ids):
    """time_limit_handoff_related_work (team-lead 2026-11-11): every time-limit handoff names an
    item of the seeded goal tree (the goal or a seeded descendant) and that goal as root."""
    rows = [{'event': k[:12], 'item_id': v.get('item_id'), 'root_id': v.get('root_id'),
             'item_in_seeded_tree': v.get('item_id') in set(seeded_ids), 'root_is_seeded_goal': v.get('root_id') == root}
            for k, v in (handoffs or {}).items()]
    return bool(rows) and all(r['item_in_seeded_tree'] and r['root_is_seeded_goal'] for r in rows), rows

def launch_file(out,workspace,session,home):
    path=out/'launch.cmd'; path.write_text('@echo off\r\nchcp 65001 >nul\r\ncd /d "'+str(workspace)+'"\r\npy -3 "'+str(home/'ciel_runtime.py')+'" cli -- --session-id '+session+' --debug-file "'+str(out/'claude-debug.txt')+'"\r\necho [probe] ciel-runtime exited with %ERRORLEVEL%\r\n',encoding='utf-8'); return path

def capture_console(console,out,label):
    text=console.screen(); (out/(label+'.txt')).write_text(text,encoding='utf-8'); run=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(E2E/'shot_hwnd.ps1'),str(int(console.hwnd)),str(out/(label+'.png'))],capture_output=True,text=True,stdin=subprocess.DEVNULL,creationflags=0x08000000); (out/(label+'-capture.log')).write_text(run.stdout+run.stderr,encoding='utf-8'); return text

def wait_console_ready(console,out):
    end=time.time()+180; trust=[]
    while time.time()<end:
        text=console.screen()
        if '[probe] ciel-runtime exited' in text or 'Traceback (most recent call last)' in text: raise RuntimeError('console launch exited')
        # Claude Code choice dialogs (workspace trust, Bypass Permissions warning, API key):
        # options are "Yes..."/"No..." lines, numbered or not, and the selection marker is ❯.
        option = r'^\s*(?:❯\s*)?(?:\d+\.\s+)?(Yes|No)\b'
        options=[re.search(option,l).group(1) for l in text.splitlines() if re.search(option,l)]
        if 'Yes' in options and 'No' in options:
            n=len(trust); capture_console(console,out,f'00-dialog-{n}-before')
            def marked_line():
                rows=[l for l in console.screen().splitlines() if re.search(r'^\s*❯\s*(?:\d+\.\s+)?(Yes|No)\b',l)]
                return rows[0] if rows else ''
            # Press Enter only after re-reading the screen with the marker on a Yes option.
            attempt=0
            for attempt in range(4):
                if re.search(r'❯\s*(?:\d+\.\s+)?Yes\b',marked_line()): break
                console.key('down',pause=1)
            selected=marked_line(); capture_console(console,out,f'00-dialog-{n}-selected')
            if not re.search(r'❯\s*(?:\d+\.\s+)?Yes\b',selected): raise RuntimeError('selection marker not observed on Yes: '+repr(selected))
            trust.append({'dialog':[l.strip() for l in text.splitlines() if l.strip()][:14],'selected':selected.strip(),'downs':attempt})
            console.key('enter',pause=3); continue
        if '? for shortcuts' in text or 'bypass permissions' in text: return trust
        time.sleep(.5)
    raise RuntimeError('Claude console not ready')
def prompt(console,text): console.text(text); console.key('enter',pause=1)
def idle(console):
    text=console.screen(); return ('? for shortcuts' in text or 'bypass permissions' in text) and 'esc to interrupt' not in text

def receiver(port,walkie): return json_post(f'http://127.0.0.1:{port}/ca/events/receivers/default',{'enabled':True,'transport':'sse','url':walkie.events_url,'event_types':['com.ciel.walkie.support.message'],'cursor_json_pointer':'/data/stream_id','cursor_query_parameter':'after','input_transport':'auto'})
def admin_token(config):
    for name in ('router-external-token','router-external.token'):
        p=config/name
        if p.exists(): return p.read_text(encoding='ascii').strip()
    return ''
def browser_shots(port,config,out,session):
    from playwright.sync_api import sync_playwright
    results=[]; token=admin_token(config)
    url=f'http://127.0.0.1:{port}/ca/work/ui?session_id='+urllib.parse.quote('claude:'+session)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=str(CHROME))
        for label,w,h in [('desktop',1440,1000),('mobile',390,844)]:
            page=browser.new_page(viewport={'width':w,'height':h},extra_http_headers={'Authorization':'Bearer '+token} if token else {}); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url,wait_until='domcontentloaded'); page.get_by_role('tab',name='Side').click(); page.locator('#side-receipts .receipt-row').first.wait_for(timeout=30000); time.sleep(1.5)
            text=page.locator('#panel-side').inner_text(); rows=page.locator('#side-receipts .receipt-row').count()
            width=page.evaluate('document.documentElement.scrollWidth')
            page.screenshot(path=str(out/f'side-{label}.png'),full_page=True); (out/f'side-{label}.html').write_text(page.content(),encoding='utf-8'); (out/f'side-{label}.txt').write_text(text,encoding='utf-8')
            results.append({'label':label,'viewport':[w,h],'url':url,'receipt_rows':rows,'scroll_width':width,'horizontal_overflow':width>w,'page_errors':errors,'has_receipt':rows>0}); page.close()
        browser.close()
    return results

def fork_payload_from_request(req):
    body=req.get('body') or {}; wire=json.dumps(body,ensure_ascii=False); instruction=''; payload=None
    # Search every text leaf because the Claude API may wrap inherited context.
    stack=[body]
    while stack:
        value=stack.pop()
        if isinstance(value,dict): stack.extend(value.values())
        elif isinstance(value,list): stack.extend(value)
        elif isinstance(value,str) and 'external_event_preview' in value and 'source_context' in value:
            candidate=side_payload(value)
            if isinstance(candidate,dict): payload=candidate; instruction=str(candidate['instruction']); break
    return wire,payload,instruction

RAW_THREAD = (ROOT_TEXT, PRIOR_TEXT, TARGET_TEXT, REPLY_ONE, REPLY_TWO)

def copy_evidence(src, dest):
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir(): shutil.copytree(src, dest, dirs_exist_ok=True)
        else: shutil.copy2(src, dest)

def secret_scan(out, secrets_list):
    hits = []
    for p in out.rglob('*'):
        if not p.is_file(): continue
        try: data = p.read_bytes()
        except OSError: continue
        for index, value in enumerate(secrets_list):
            if value and value.encode() in data: hits.append({'file': str(p), 'secret_index': index})
    return hits

def wake_rows(board):
    return {k: v for k, v in (board.get('side_handoffs') or {}).items() if v.get('kind') == 'time_limit'}

LIVE_FILES = [Path.home() / '.claude' / 'settings.json', Path.home() / '.claude' / 'cache' / 'gateway-models.json',
              Path.home() / '.local' / 'bin' / 'ciel-runtime-statusline.py']
LIVE_DIRS = [Path.home() / '.claude' / 'commands', Path(os.environ['LOCALAPPDATA']) / 'ciel-runtime' / 'bin']
CLAUDE_JSON = Path.home() / '.claude.json'
_LAST_PROJECTS = {}
def owned_projects(workspace):
    """Only what an owned run could add to the live ~/.claude.json: its temp workspace's projects entries."""
    wanted = {str(workspace).replace(chr(92), '/').lower()}
    for attempt in range(5):
        try:
            data = json.loads(CLAUDE_JSON.read_text(encoding='utf-8'))
            rows = {k: v for k, v in (data.get('projects') or {}).items() if k.replace(chr(92), '/').lower() in wanted or 'ciel-sse-true-fork' in k}
            _LAST_PROJECTS[str(workspace)] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() + ':' + str(sorted(rows))
            return _LAST_PROJECTS[str(workspace)]
        except (OSError, ValueError):
            time.sleep(.2)  # live sessions rewrite this file; a torn read is retried, never a change
    return _LAST_PROJECTS.get(str(workspace), 'unreadable')
def live_profile_state(workspace):
    state = {}
    for path in LIVE_FILES: state[str(path)] = sha(path) if path.exists() else None
    for folder in LIVE_DIRS:
        for path in sorted(folder.rglob('*')) if folder.exists() else []:
            if path.is_file(): state[str(path)] = sha(path)
    state['~/.claude.json projects entries for the owned workspace'] = owned_projects(workspace)
    return state

def path_probe(home, env, workspace, scratch):
    code = ('import sys,json; sys.dont_write_bytecode=True; sys.path.insert(0, sys.argv[1]); '
            'import ciel_runtime_support.runtime_paths as p; '
            'print(json.dumps({k: str(getattr(p, k)) for k in ("CONFIG_DIR","WORKSPACE_STATE_DIR","WORKSPACE_CONFIG_PATH","ROUTER_INSTANCE_DIR","LOG_PATH","CLI_ASSET_HOME","CLAUDE_SETTINGS_PATH","CLAUDE_COMMANDS_DIR","CLAUDE_GATEWAY_CACHE","CIEL_RUNTIME_STATUSLINE_PATH")}))')
    raw = subprocess.check_output([str(PY314), '-B', '-c', code, str(home)], env=env, cwd=str(workspace), text=True, stdin=subprocess.DEVNULL, creationflags=0x08000000)
    paths = json.loads(raw.strip().splitlines()[-1])
    root = str(scratch).lower()
    outside = {k: v for k, v in paths.items() if not v.lower().startswith(root)}
    return paths, outside

def classify_copy_path(rel):
    if '/__pycache__/' in '/' + rel and rel.endswith('.pyc'): return 'bytecode'
    if '/.claude-plugin/types/' in '/' + rel: return 'engine_written_types'
    return 'other'

def types_keys(path):
    if not path.exists(): return []
    text = path.read_text(encoding='utf-8', errors='replace')
    return sorted(set(re.findall(r'^    ([A-Za-z_][A-Za-z0-9_]*)\??: \{', text, re.M)))

def types_key_diff(before_path, after_path):
    a, b = types_keys(before_path), types_keys(after_path)
    return {'keys_before': len(a), 'keys_after': len(b), 'removed': sorted(set(a) - set(b)), 'added': sorted(set(b) - set(a)),
            'mcp_server_tool_keys_after': [k for k in b if k.startswith('mcp__')]}

def worktree_freeze_check():
    lines = [l for l in (ROOT / '.local/main-review-ui/FREEZE-SHA256.txt').read_text(encoding='utf-8').splitlines()[1:] if l.strip()]
    bad = []
    for line in lines:
        digest, rel = line.split('  ', 1); rel = rel.strip().lstrip('.' + chr(92)).replace(chr(92), '/')
        if not (ROOT / rel).exists() or sha(ROOT / rel) != digest: bad.append(rel)
    return {'lines': len(lines), 'mismatch': bad}

def run(kind, output):
    session = SESSION_A if kind == 'A' else SESSION_B
    tag = kind.lower()
    out = output / f"run-{tag}-{time.strftime('%Y%m%d-%H%M%S')}"; out.mkdir(parents=True)
    shutil.copy2(E2E / 'console_driver.py', out / 'console_driver.py'); sys.path.insert(0, str(out)); from console_driver import Console
    STEP_LOG[0] = out / 'driver-steps.log'; step('run ' + kind + ' start')
    assert_detached_from_protected(); step('driver is not a protected descendant')
    harness_hashes = copy_harness(out); step('harness ' + json.dumps(harness_hashes))
    unit_gate = harness_unit_gate(out); step('harness unit gate ' + json.dumps(unit_gate))
    global EXPECTED_PROTECTED
    run_start = time.time()
    before = write_ps_snapshot(out, 'processes-before')
    EXPECTED_PROTECTED = long_lived_protected(before, run_start)
    gate_info = {'gate_pids': sorted(EXPECTED_PROTECTED), 'lead_listed_missing_at_start': sorted(LEAD_LISTED - EXPECTED_PROTECTED)}
    walkie_before = walkie_log_state()
    other_before = other_e2e_processes(before, [str(out)])
    if other_before:
        (out / 'other-e2e-before.json').write_text(json.dumps(other_before, indent=1), encoding='utf-8')
        raise SystemExit('another owned E2E is running; refusing a non-exclusive run: ' + json.dumps(other_before)[:600])
    step('protected gate %d long-lived pids; lead-listed missing %s; walkie log %s' % (len(EXPECTED_PROTECTED), gate_info['lead_listed_missing_at_start'], walkie_before.get('lines')))
    walkie = SyntheticWalkie(); gateway = RecordingGateway(out) if kind == 'A' else None
    scratch = Path(tempfile.mkdtemp(prefix=f'ciel-sse-true-fork-{tag}-')); workspace = scratch / 'workspace'; config = scratch / 'config'; claude = config / '.claude'; workspace.mkdir()
    state = setup(config, claude, workspace, walkie, gateway); identity = 'claude:' + session
    item, seed = seed_board(state, identity)
    seeded_ids = seeded_tree_ids(seed, item)
    (out / 'seed-board.json').write_text(json.dumps({'seeded_goal': item, 'seeded_tree_ids': seeded_ids, 'version': seed.get('version'),
        'nodes': [{k: n.get(k) for k in ('id', 'parent_id', 'title', 'priority')} for n in seed.get('nodes') or []]}, indent=1), encoding='utf-8')
    if gateway: gateway.related_item = item; gateway.board_reader = lambda: get_board(state, identity)
    # Launch from a per-run copy; Claude Code and statusline Python write into the
    # launched home (attempt run-a-20261111-102831), so the snapshot itself stays immutable.
    home = E2E / 'run-copies' / out.name
    shutil.copytree(SNAP, home)
    # The inventory's allow-list bounds must still be the product's bounds in this freeze.
    bounds_check = rti.verify_product_bounds(home)
    (out / 'product-bounds-check.json').write_text(json.dumps(bounds_check, indent=1), encoding='utf-8')
    if not bounds_check['passed']: raise SystemExit('product bound expressions changed; inventory bounds need a ruling: ' + json.dumps(bounds_check['missing'])[:600])
    step('product bounds match inventory')
    home_files = {q.relative_to(home).as_posix(): sha(q) for q in home.rglob('*') if q.is_file()}
    home_digest = hashlib.sha256(json.dumps(home_files, sort_keys=True).encode()).hexdigest()
    if home_digest != read_json(SNAP_VERIFY)['snapshot_digest']: raise SystemExit('run copy digest mismatch')
    product_pin = {rel: {'expected': digest, 'run_copy': home_files.get(rel)} for rel, digest in EXPECTED_PRODUCT.items()}
    (out / 'product-pin.json').write_text(json.dumps(product_pin, indent=1), encoding='utf-8')
    if any(v['expected'] != v['run_copy'] for v in product_pin.values()): raise SystemExit('run copy product hashes differ from the release: ' + json.dumps(product_pin)[:600])
    step('run copy product hashes match the release')
    step('run copy ' + str(home) + ' digest ' + home_digest)
    port = free_port(); env = clean_env(config, claude, workspace, port, home)
    owned_vars = {'CIEL_RUNTIME_CONFIG_DIR', 'CIEL_RUNTIME_LAUNCH_CWD', 'CIEL_RUNTIME_ROUTER_PORT', 'CIEL_RUNTIME_SKIP_MENU', 'CIEL_RUNTIME_SKIP_SELF_UPDATE', 'CIEL_RUNTIME_SKIP_UPDATE_CHECK', 'CIEL_RUNTIME_HOME_OVERRIDE', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'}
    launch = launch_file(out, workspace, session, home)
    paths, outside = path_probe(home, env, workspace, scratch)
    (out / 'prelaunch-paths.json').write_text(json.dumps({'paths': paths, 'outside_scratch': outside}, indent=1), encoding='utf-8')
    if outside: raise SystemExit('prelaunch path probe: runtime would write outside the owned scratch: ' + json.dumps(outside))
    profile_before = live_profile_state(workspace); (out / 'live-profile-before.json').write_text(json.dumps(profile_before, indent=1), encoding='utf-8')
    claude_json_before = claude_json_owned_refs([str(scratch), str(home), str(out), out.name, scratch.name, session])
    if claude_json_before['hits']: raise SystemExit('~/.claude.json already names an owned marker before launch: ' + json.dumps(claude_json_before['hits']))
    step('prelaunch paths inside scratch; live profile hashed (%d files)' % len(profile_before))
    proc = subprocess.Popen(['conhost.exe', 'cmd', '/k', str(launch)], cwd=str(workspace), env=env)
    root_row = wait_until(lambda: next((r for r in opc.snapshot_all() if int(r['ProcessId']) == proc.pid), None), 10, .2)
    if not root_row: raise SystemExit('owned root not observed')
    root_identity = opc.identity(root_row)
    owned_ledger, _ = opc.discover_owned(root_identity, opc.snapshot_all())
    cleanup_log = out / 'cleanup.jsonl'
    guard = {'changed': None}
    def watch_profile():
        while guard['changed'] is None and proc.poll() is None:
            now = live_profile_state(workspace)
            if now != profile_before:
                guard['changed'] = {k: [profile_before.get(k), now.get(k)] for k in set(now) | set(profile_before) if now.get(k) != profile_before.get(k)}
                step('LIVE PROFILE CHANGED: ' + json.dumps(guard['changed'])); kill_owned(root_identity, cleanup_log); return
            time.sleep(1)
    threading.Thread(target=watch_profile, daemon=True).start(); step('conhost started pid %d port %d' % (proc.pid, port))
    # Wake state timeline (redacted fields only), recorded on every change, for the
    # "no side wake left started/submitting after its prompt resolved" and "no Stop
    # presentation while the identity is pending" assertions.
    wake_timeline = []; sampler = {'stop': False}
    def sample_wakes():
        last = {}
        while not sampler['stop']:
            try:
                board = get_board(state, identity)
                for event_id, row in (board.get('side_handoffs') or {}).items():
                    wake = (row or {}).get('wake') or {}
                    view = (row.get('state'), row.get('kind'), wake.get('state'), wake.get('phase'), wake.get('channel'), wake.get('revision'))
                    if last.get(event_id) != view:
                        last[event_id] = view
                        wake_timeline.append({'at': time.time(), 'event': event_id[:12], 'handoff_state': view[0], 'kind': view[1],
                                              'wake_state': view[2], 'phase': view[3], 'channel': view[4], 'revision': view[5]})
            except Exception as error:
                wake_timeline.append({'at': time.time(), 'sample_error': repr(error)[:200]})
            time.sleep(0.25)
    threading.Thread(target=sample_wakes, daemon=True).start()
    result = {'run': kind, 'out': str(out), 'outcome': 'timeout', 'snapshot': str(SNAP), 'run_copy': str(home), 'run_copy_digest_before_launch': home_digest,
              'snapshot_digest': read_json(SNAP_VERIFY)['snapshot_digest'],
              'port': port, 'session': session, 'owned_root_pid': proc.pid, 'related_work': item,
              'seed_board_version': seed.get('version'), 'scratch': str(scratch),
              'inherited_vars_left': sorted(k for k in env if k.upper().startswith(STRIP_PREFIXES) and k not in owned_vars),
              'owned_vars_set': sorted(owned_vars), 'model': 'claude-opus-5-5 (recording gateway)' if gateway else B_MODEL,
              'timeline': {}, 'protected_gate': gate_info, 'walkie_log_before': walkie_before, 'run_start': run_start,
              'harness_sha256': harness_hashes, 'driver_sha256': harness_hashes.get('walkie_e2e_driver_v4.py'),
              'harness_unit_gate': unit_gate,
              'owned_root_identity': list(root_identity), 'other_e2e_before': other_before}
    T = result['timeline']
    console = None
    try:
        time.sleep(8)
        cmd_pid = int(next(r['ProcessId'] for r in process_tree(proc.pid) if r['Name'].lower() == 'cmd.exe'))
        step('cmd pid %d' % cmd_pid); console = Console(cmd_pid); step('attached; hwnd %s' % console.hwnd)
        result['trust_flow'] = wait_console_ready(console, out); T['console_ready'] = time.time(); step('console ready')
        owned_ledger, ready_events = opc.discover_owned(root_identity, opc.snapshot_all(), owned_ledger)
        result['owned_ready_identities'] = [[i[0], i[1], owned_ledger[i].get('Name')] for i in sorted(owned_ledger)]
        result['owned_pids_at_ready'] = [[i[0], owned_ledger[i].get('Name')] for i in sorted(owned_ledger)]
        step('owned pids ' + json.dumps(result['owned_pids_at_ready']))
        result['receiver'] = receiver(port, walkie)
        capture_console(console, out, '01-started')
        tpath = transcript_path(claude, workspace, session)
        def assistant_turns():
            return sum(1 for r in transcript_rows(tpath) if not r.get('isSidechain') and (r.get('message') or {}).get('role') == 'assistant')
        for label, text in (('front', CANARY_FRONT + ' Remember this exact front canary. Reply only FRONT-ACK.'),
                            ('middle', CANARY_MIDDLE + ' Remember this exact middle canary. Reply only MIDDLE-ACK.'),
                            ('end', END_PROMPT)):
            count = assistant_turns(); prompt(console, text); T['prompt_' + label] = time.time(); step('prompt ' + label)
            wait_until(lambda: assistant_turns() > count and idle(console), 240 if kind == 'B' else 90, 1)
            time.sleep(2)
        capture_console(console, out, '02-main-canaries')
        pre = transcript_rows(tpath); pre_main = row_text(pre, False)
        result['main_before_event'] = {k: v in pre_main for k, v in (('front', CANARY_FRONT), ('middle', CANARY_MIDDLE), ('end', CANARY_END), ('change_ticket', CHANGE_TICKET), ('claude_marker', CLAUDE_MARKER))}
        copy_evidence(tpath, out / 'evidence' / 'main-transcript-before-event.jsonl')
        T['event_thread_sent'] = time.time(); step('thread event sent')
        walkie.send(cloud_event(f'owned-{tag}-thread-1', True, 'Synthetic target 1002 readiness request; full thread is canonical.'))
        rec = wait_until(lambda: find_receipt(state, f'owned-{tag}-thread-1', ('processed',)), 600 if kind == 'B' else 240, 1)
        if not rec:
            result['thread_receipt_any'] = (find_receipt(state, f'owned-{tag}-thread-1') or [None, None])[1]
            raise RuntimeError('thread receipt not processed')
        T['thread_processed_seen'] = time.time(); step('thread processed')
        result['thread_receipt_path'] = str(rec[0]); result['thread_receipt'] = rec[1]
        wait_until(lambda: wake_rows(get_board(state, identity)), 120, .5); T['handoff_seen'] = time.time()
        wait_until(lambda: any(v.get('wake', {}).get('state') in ('queued', 'appended', 'acknowledged') for v in wake_rows(get_board(state, identity)).values()), 120, .5); T['wake_seen'] = time.time()
        step('wake state seen')
        wait_until(lambda: any(v.get('state') in ('acknowledged', 'answered') for v in wake_rows(get_board(state, identity)).values()), 300 if kind == 'B' else 120, 1); T['ack_seen'] = time.time()
        step('ack wait done')
        wait_until(lambda: walkie.sends, 60, 1)
        time.sleep(3); wait_until(lambda: idle(console), 240 if kind == 'B' else 120, 1)
        capture_console(console, out, '03-wake-and-ack')
        # Observe for duplicate presentations after the first wake was acknowledged.
        time.sleep(25); wait_until(lambda: idle(console), 240 if kind == 'B' else 120, 1)
        step('duplicate observation window done')
        if kind == 'A':
            sends = len(walkie.sends); calls = len(walkie.calls); main_reqs = len([r for r in gateway.requests if r.get('classified') == 'main'])
            T['event_none_sent'] = time.time()
            walkie.send(cloud_event('owned-a-none-1', False, 'Reminder: continue observing the synthetic readiness review; no message is referenced.'))
            none = wait_until(lambda: find_receipt(state, 'owned-a-none-1', ('processed',)), 240, 1)
            if not none: raise RuntimeError('none_referenced receipt not processed')
            T['none_processed_seen'] = time.time()
            time.sleep(15)
            none = find_receipt(state, 'owned-a-none-1', ('processed',))
            result['none_receipt_path'] = str(none[0]); result['none_receipt'] = none[1]
            result['none_send_delta'] = len(walkie.sends) - sends; result['none_mcp_call_delta'] = len(walkie.calls) - calls
            result['none_main_request_delta'] = len([r for r in gateway.requests if r.get('classified') == 'main']) - main_reqs
            side_before = len([r for r in gateway.requests if r.get('classified') == 'side']); sends = len(walkie.sends)
            walkie.fail_reads = True; T['event_fail_sent'] = time.time()
            walkie.send(cloud_event('owned-a-fetch-fail-1', True, 'This target must fail source prefetch.', message_id='1006'))
            fail = wait_until(lambda: (lambda x: x if x and (x[1].get('side_failure') or {}).get('reason') else None)(find_receipt(state, 'owned-a-fetch-fail-1')), 180, 1)
            if not fail: raise RuntimeError('fetch failure not durably recorded')
            T['fail_recorded_seen'] = time.time(); time.sleep(10)
            fail = find_receipt(state, 'owned-a-fetch-fail-1')
            result['fetch_failure_receipt_path'] = str(fail[0]); result['fetch_failure_receipt'] = fail[1]
            result['fetch_failure_spawn_delta'] = len([r for r in gateway.requests if r.get('classified') == 'side']) - side_before
            result['fetch_failure_send_delta'] = len(walkie.sends) - sends
            result['fetch_failure_reads'] = [c for c in walkie.calls if c['at'] >= T['event_fail_sent']]
        capture_console(console, out, '04-after-events')
        try: result['web'] = browser_shots(port, config, out, session)
        except Exception as error: result['web'] = [{'label': 'error', 'error': repr(error)[:500], 'has_receipt': False, 'page_errors': ['shot failed']}] * 2
        # Durable evidence copies.
        ev = out / 'evidence'
        copy_evidence(tpath, ev / 'main-transcript.jsonl')
        copy_evidence(transcript_dir(claude, workspace) / session / 'subagents', ev / 'subagents')
        copy_evidence(state / 'side-inboxes', ev / 'side-inboxes')
        raw_board = get_board(state, identity); board = redacted_board(raw_board)
        (ev / 'board-after.json').write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding='utf-8')
        rows = transcript_rows(tpath); main = row_text(rows, False)
        # Whole-message needles are retained only as one diagnostic signal. The gate is the exact
        # substring inventory below (minimum length, source id, offsets, destination field).
        raw_hits = {name: (value in main) for name, value in (('root', ROOT_TEXT), ('prior', PRIOR_TEXT), ('target', TARGET_TEXT), ('reply1', REPLY_ONE), ('reply2', REPLY_TWO), ('side_prompt', SIDE_PROMPT_HEAD), ('event_preview', 'Synthetic target 1002 readiness request'), ('stream_marker', f'owned-{tag}-thread-1'))}
        source_messages = {'1000': ROOT_TEXT, '1001': PRIOR_TEXT, '1002': TARGET_TEXT, '1003': REPLY_ONE, '1004': REPLY_TWO}
        # Every raw CloudEvent this run sent (thread, none_referenced, fetch failure).
        inventory = rti.inventory_main_rows(rows, source_messages, list(walkie.sent_events))
        inventory['inventory_sha256'] = sha(out / 'harness' / 'raw_text_inventory.py')
        (out / 'raw-text-inventory.json').write_text(json.dumps(inventory, indent=1, ensure_ascii=False), encoding='utf-8')
        injected_inventory = inventory['injected']
        own_read_rows = [r['row'] for r in inventory['observations']['main_own_source_reads']]
        inventory_passed = bool(injected_inventory) and inventory['passed']
        result['main_transcript'] = {'path': str(tpath), 'sha256': sha(tpath), 'rows': len(rows), 'sidechain_rows': sum(1 for r in rows if r.get('isSidechain')), 'whole_message_diagnostic': raw_hits,
                                     'main_own_source_read_rows': own_read_rows,
                                     'wake_prompt_rows': [str(((r.get('message') or {}).get('content')))[:600] for r in rows if WAKE_HEAD in json.dumps(r, ensure_ascii=False)][:5]}
        actors = subagent_evidence(claude, workspace, session)
        result['side_actors'] = [{k: v for k, v in a.items() if k != 'body'} for a in actors]
        times = wake_rows(board)
        result['time_limit_handoffs'] = times
        answer = str(receipt_result(rec[1]).get('reply') or '')
        result['answer'] = answer
        thread_sends = [s for s in walkie.sends if (s['arguments'] or {}).get('reply_to_message_id') == TARGET_ID]
        spawned_ids = [Path(rec[0]).stem] + ([Path(result['none_receipt_path']).stem] if result.get('none_receipt_path') else [])
        fail_id = Path(result['fetch_failure_receipt_path']).stem if result.get('fetch_failure_receipt_path') else None
        result['actor_map'] = {'spawned_receipts': [i[:12] for i in spawned_ids], 'fetch_failure_receipt': (fail_id or '')[:12],
                               'actor_descriptions': sorted(a['meta'].get('description') for a in actors)}
        event_id = next(iter(times), None)
        raw_wake = ((raw_board.get('side_handoffs') or {}).get(event_id) or {}).get('wake') or {}
        raw_row = (raw_board.get('side_handoffs') or {}).get(event_id) or {}
        main_rows = [r for r in rows if not r.get('isSidechain') and r.get('type') != 'api-request-blob']
        def row_has(r, *needles): text = json.dumps(r, ensure_ascii=False); return all(n in text for n in needles)
        debug = (out / 'claude-debug.txt').read_text(encoding='utf-8', errors='replace').splitlines() if (out / 'claude-debug.txt').exists() else []
        eid12 = (event_id or 'none')[:12]
        debug_submits = [l[:240] for l in debug if ('$.prompt.submit' in l or '$.session.append' in l) and 'Ciel side event requires main action' in l and eid12 in l]
        debug_stop_blocks = [l[:240] for l in debug if STOP_HEAD in l and eid12 in l]
        wake_ops = [l[:200] for l in debug if '__work_side_wake' in l and ('$.mcp.call' in l and '(' in l and 'answered' not in l)]
        wake_count = {'transcript_wake_rows': sum(1 for r in main_rows if r.get('type') == 'user' and row_has(r, WAKE_HEAD, event_id or '§')),
                      'transcript_stop_block_rows': sum(1 for r in main_rows if row_has(r, STOP_HEAD, event_id or '§')),
                      'transcript_uncertain_rows': sum(1 for r in main_rows if row_has(r, UNCERTAIN_HEAD)),
                      'debug_prompt_or_append': len(debug_submits), 'debug_lines': debug_submits,
                      'debug_stop_block_lines': len(debug_stop_blocks), 'work_side_wake_calls': wake_ops,
                      'channel': raw_wake.get('channel'), 'state': raw_wake.get('state'), 'phase': raw_wake.get('phase'), 'revision': raw_wake.get('revision')}
        if gateway:
            mains = [r for r in gateway.requests if r.get('classified') == 'main']
            def blocks(r, *needles):
                return sum(1 for m in (r['body'].get('messages') or []) if m.get('role') == 'user' and isinstance(m.get('content'), list)
                           for x in m['content'] if isinstance(x, dict) and x.get('type') == 'text' and all(n in str(x.get('text')) for n in needles))
            wake_count['gateway_max_wake_blocks'] = max([blocks(r, WAKE_HEAD, event_id or '§') for r in mains] or [0])
            wake_count['gateway_max_stop_blocks'] = max([blocks(r, STOP_HEAD, event_id or '§') for r in mains] or [0])
            wake_count['gateway_first_wake_seq'] = next((r['seq'] for r in mains if blocks(r, WAKE_HEAD, event_id or '§') or blocks(r, STOP_HEAD, event_id or '§')), None)
            wake_count['gateway_ack_tool_calls'] = [w for w in gateway.wakes if w['wake'].get('event_id') == event_id]
            wake_count['gateway_ack_results'] = gateway.ack_results
        channel = raw_wake.get('channel')
        if channel == 'stop':
            presentations = wake_count['transcript_stop_block_rows'] if not gateway else wake_count['gateway_max_stop_blocks']
            exactly_one = wake_count['debug_prompt_or_append'] == 0 and wake_count['transcript_wake_rows'] == 0 and presentations == 1
        else:
            exactly_one = wake_count['debug_prompt_or_append'] == 1 and wake_count['transcript_wake_rows'] == 1 and (not gateway or wake_count['gateway_max_wake_blocks'] == 1)
            if channel == 'prompt': exactly_one = exactly_one and (not gateway or wake_count['gateway_max_stop_blocks'] == 0)
        result['wake_count'] = wake_count
        design_report, design_facts = design_assertions(debug, rows, wake_timeline, event_id, raw_board, gateway)
        result['design_assertions'] = design_report
        if channel == 'prompt':
            # A prompt-channel wake must have no Stop presentation of the same event at all.
            design_facts['prompt_wake_has_no_stop_presentation'] = not design_report['stop_presentations_of_event']
        requested_at = raw_row.get('requested_at')
        first_wake_at = next((w['at'] for w in (gateway.wakes if gateway else []) if w['wake'].get('event_id') == event_id), None)
        result['wake_timing'] = {'handoff_requested_at': requested_at, 'wake_started_at': raw_wake.get('started_at'), 'wake_updated_at': raw_wake.get('updated_at'),
                                 'ack_at': raw_wake.get('acknowledged_at') or raw_row.get('acknowledged_at'),
                                 'gateway_first_wake_request_at': first_wake_at,
                                 'commit_to_start_s': (raw_wake.get('started_at') - requested_at) if raw_wake.get('started_at') and requested_at else None,
                                 'commit_to_main_request_s': (first_wake_at - requested_at) if first_wake_at and requested_at else None}
        result['stop_path'] = {'happened': channel == 'stop', 'state': raw_wake.get('state'),
                               'debug_turn_step_after_stop': [l[:200] for l in debug if 'turn.step' in l][-6:] if channel == 'stop' else None}
        ack_ok = bool(times) and all(v.get('state') in ('acknowledged', 'answered') for v in times.values()) and not raw_wake.get('needs_ack_retry')
        if gateway: ack_ok = ack_ok and bool(gateway.ack_results) and not any(v['is_error'] for v in gateway.ack_results.values())
        related_ok, related_rows = related_work_check(times, item, seeded_ids)
        result['related_work_check'] = {'seeded_goal': item, 'seeded_tree_ids': seeded_ids, 'handoffs': related_rows}
        result['priority'] = {'work_root_priority': next((n.get('priority') for n in raw_board.get('nodes') or [] if n.get('id') == item), None),
                              'event_thread_task_priority': 5, 'receipt_priority': rec[1].get('priority')}
        facts = {
            'host_committed': rec[1].get('stage') == 'processed' and bool(receipt_result(rec[1]).get('action')),
            # run-a-20261111-161230: every processed event (thread and none_referenced) spawns
            # its own fork, so the rule is one fork per spawned event, none for the failed prefetch.
            'one_fork_per_spawned_event': sorted(a['meta'].get('description') for a in actors) == sorted('Side SSE ' + i[:12] for i in spawned_ids),
            'fork_type': bool(actors) and all(a['meta'].get('agentType') == 'fork' and a['meta'].get('isFork') is True and a['meta'].get('model') == 'inherit' for a in actors),
            'unnamed_not_registered': bool(actors) and all(a['meta'].get('agentType') != 'ciel-work:side-intake' and not a['meta'].get('name') for a in actors),
            'time_limit_handoff_related_work': related_ok,
            'time_limit_wake_delivered': bool(times) and all((v.get('wake') or {}).get('state') in ('queued', 'appended', 'acknowledged') for v in times.values()),
            'main_acknowledged': bool(times) and all(v.get('state') in ('acknowledged', 'answered') for v in times.values()),
            'exactly_one_main_wake': exactly_one,
            'ack_succeeded_first_try': ack_ok,
            'wake_immediate': result['wake_timing']['commit_to_start_s'] is not None and result['wake_timing']['commit_to_start_s'] <= 10,
            'low_priority_time_limit': result['priority']['work_root_priority'] in ('P4', 'P5'),
            'stop_path_queued_if_used': channel != 'stop' or raw_wake.get('state') in ('queued', 'acknowledged'),
            'host_injected_source_text_within_allowlist': inventory_passed and bool(injected_inventory),
            'outbox_reply_sent_once': len(thread_sends) == 1,
            'web_desktop': bool(result['web'][0].get('has_receipt')) and not result['web'][0].get('page_errors'),
            'web_mobile': bool(result['web'][1].get('has_receipt')) and not result['web'][1].get('page_errors'),
            **design_facts,
        }
        result['walkie'] = {'calls': walkie.calls, 'sends': walkie.sends}
        if kind == 'A':
            side = [r for r in gateway.requests if r.get('classified') == 'side']; mainreq = [r for r in gateway.requests if r.get('classified') == 'main']
            result['gateway'] = {'requests': len(gateway.requests), 'side_requests': len(side), 'main_requests': len(mainreq), 'side_seqs': [r['seq'] for r in side], 'side_agent_headers': [r.get('agent_id') for r in side], 'wakes': gateway.wakes}
            if not side: raise RuntimeError('no side model request recorded')
            wire, payload, instruction = fork_payload_from_request(side[0])
            final_role, last_texts = final_message_texts(side[0]['body'])
            last_joined = '\n'.join(last_texts)
            pidx = last_joined.find('{"event_id"')
            after_payload = ''
            if payload is not None and pidx >= 0:
                _, end = json.JSONDecoder().raw_decode(last_joined, pidx); after_payload = last_joined[end:]
            src = (payload or {}).get('source_message') or {}
            msgs = {str(m.get('id')): m.get('text') for m in src.get('messages') or []}
            result['fork_wire'] = {'request_seq': side[0]['seq'], 'agent_header': side[0].get('agent_id'), 'model': side[0].get('model'),
                'has_front': CANARY_FRONT in wire, 'has_middle': CANARY_MIDDLE in wire, 'has_end': CANARY_END in wire, 'has_change_ticket': CHANGE_TICKET in wire,
                'has_claude_marker': CLAUDE_MARKER in wire, 'payload_found': payload is not None,
                'payload_keys': list((payload or {}).keys()), 'payload_last_key': list((payload or {}).keys())[-1] if payload else None,
                'source_context': (payload or {}).get('source_context'),
                'thread_messages_exact': {k: msgs.get(k) == v for k, v in (('1000', ROOT_TEXT), ('1001', PRIOR_TEXT), ('1002', TARGET_TEXT), ('1003', REPLY_ONE), ('1004', REPLY_TWO))},
                'target_length': len(TARGET_TEXT), 'target_in_payload_length': len(msgs.get('1002') or ''),
                'instruction_ends_with_contract': instruction.rstrip().endswith(CONTRACT_LAST),
                'instruction_has_prohibitions': all(s in instruction for s in ('write, edit or create code, config or any file', 'run Bash, PowerShell or any shell command', 'ACK, reply, SendMessage or send anything to the source or any channel', 'mutate Work')),
                'final_message_role': final_role, 'final_message_head': last_joined[:160],
                'text_after_payload_in_last_user_message': after_payload[:400], 'last_user_text_blocks': len(last_texts),
                'last_user_message_tail': last_joined[-400:], 'tool_names_count': len(side[0]['body'].get('tools') or []),
                'main_tool_names_count': len(mainreq[-1]['body'].get('tools') or []) if mainreq else None,
                'same_model_as_main': bool(mainreq) and side[0].get('model') == mainreq[-1].get('model'),
                'system_equal_main': bool(mainreq) and json.dumps(side[0]['body'].get('system'), sort_keys=True) == json.dumps(mainreq[0]['body'].get('system'), sort_keys=True)}
            facts.update({
                'all_main_canaries_in_fork': all(result['fork_wire'][k] for k in ('has_front', 'has_middle', 'has_end')),
                'claude_marker_in_fork': result['fork_wire']['has_claude_marker'],
                'full_thread_verbatim_in_fork': payload is not None and all(result['fork_wire']['thread_messages_exact'].values()) and TARGET_TEXT in wire and REPLY_ONE in wire and REPLY_TWO in wire,
                'contract_is_last_instruction': final_role == 'user' and result['fork_wire']['payload_last_key'] == 'instruction' and result['fork_wire']['instruction_ends_with_contract'] and result['fork_wire']['instruction_has_prohibitions'] and not after_payload.strip(),
                'none_not_applicable': receipt_result(result['none_receipt']).get('delivery') == 'not_applicable',
                'none_no_send': result['none_send_delta'] == 0,
                'none_no_main_instruction': result['none_main_request_delta'] == 0 and not any(k for k in (board.get('side_handoffs') or {}) if k == result['none_receipt'].get('receipt_id')),
                'fetch_no_spawn': result['fetch_failure_spawn_delta'] == 0 and not any(fail_id and a['meta'].get('description') == 'Side SSE ' + fail_id[:12] for a in actors),
                'fetch_no_reply': result['fetch_failure_send_delta'] == 0,
                'fetch_durable_reason': bool((result['fetch_failure_receipt'].get('side_failure') or {}).get('reason')),
            })
        else:
            # CHG-58213 exists only in main's own END turn, never in the thread or the
            # Work projection, so it proves the fork used inherited main context.
            decision = receipt_result(rec[1])
            combined = '\n'.join(str(decision.get(k) or '') for k in ('reply', 'summary', 'note'))
            fork_bodies = [a.get('body', '') for a in actors]
            result['b_answer'] = {'reply': decision.get('reply'), 'summary': decision.get('summary'), 'action': decision.get('action'),
                                  'kind': decision.get('kind'), 'item_id': decision.get('item_id'), 'time_limit': decision.get('time_limit')}
            result['b_fork_transcript_mentions'] = {k: any(k in b for b in fork_bodies) for k in (CHANGE_TICKET, 'sandbox-east', 'TARGET_1002_FULL_END', 'REPLY_1004_VERBATIM')}
            facts.update({
                'answer_uses_main_context': CHANGE_TICKET in combined,
                'answer_uses_thread': 'sandbox-east' in combined and any(s in combined for s in ('30-minute', '30 minutes', '30분', 'Within 30')),
                'answer_not_event_only': not any(s in combined.lower() for s in ('only see the event', 'only have the event', 'cannot see the thread', 'no access to the thread')),
                'reply_recorded': bool(answer),
            })
        result['facts'] = facts; result['board_after'] = board
        result['outcome'] = 'completed' if all(facts.values()) else 'defect'
        prompt(console, '/exit'); time.sleep(3)
    except BaseException as error:
        step('error ' + type(error).__name__ + ': ' + str(error)[:300])
        result['error'] = {'type': type(error).__name__, 'message': str(error)[:1000]}
        result.setdefault('walkie', {'calls': walkie.calls, 'sends': walkie.sends})
        if gateway: result.setdefault('gateway', {'requests': len(gateway.requests), 'wakes': gateway.wakes, 'ack_results': gateway.ack_results})
        if console:
            try: capture_console(console, out, 'error')
            except Exception: pass
        try:
            ev = out / 'evidence'
            copy_evidence(transcript_path(claude, workspace, session), ev / 'main-transcript.jsonl')
            copy_evidence(transcript_dir(claude, workspace) / session / 'subagents', ev / 'subagents')
            copy_evidence(state / 'side-inboxes', ev / 'side-inboxes')
            (ev / 'board-after.json').write_text(json.dumps(redacted_board(get_board(state, identity)), indent=1, ensure_ascii=False), encoding='utf-8')
        except Exception as e2: result['evidence_copy_error'] = repr(e2)
    finally:
        step('cleanup start')
        sampler['stop'] = True
        try:
            (out / 'wake-timeline.jsonl').write_text(''.join(json.dumps(w, ensure_ascii=False) + '\n' for w in wake_timeline), encoding='utf-8')
            result['wake_timeline_path'] = str(out / 'wake-timeline.jsonl'); result['wake_timeline_rows'] = len(wake_timeline)
        except Exception as error: result['wake_timeline_error'] = repr(error)
        try:
            pre_rows = opc.snapshot_all()
            owned_ledger, final_events = opc.discover_owned(root_identity, pre_rows, owned_ledger)
            result['tree_before_kill'] = [dict(owned_ledger[i], present=opc.row_for(i, pre_rows) is not None) for i in sorted(owned_ledger)]
            cleanup = kill_owned(root_identity, cleanup_log)
            result['cleanup'] = {k: cleanup[k] for k in ('owned_identities', 'dynamic_identities', 'killed', 'rejected', 'protected_intersections', 'final_live', 'completed', 'duration_s', 'log')}
            protected_now = protected_identities(ps_snapshot())
            checked = {tuple(i) for i in result.get('owned_ready_identities', [])}
            checked |= {tuple(i) for i in cleanup['owned_identities']} | {tuple(k['identity']) for k in cleanup['killed']}
            checked = {(int(i[0]), str(i[1])) for i in checked}
            result['owned_intersection_check'] = {'checked_identities': len(checked), 'intersections': sorted([list(i) for i in checked & (protected_now | protected_identities(before))])}
            result['owned_tree_intersects_protected'] = bool(result['owned_intersection_check']['intersections']) or bool(cleanup['protected_intersections'])
            time.sleep(1)
            leftover = [r for r in ps_snapshot() if str(scratch) in str(r.get('CommandLine') or '') or str(home) in str(r.get('CommandLine') or '') or str(SNAP) in str(r.get('CommandLine') or '')]
            result['owned_leftover_after_kill'] = leftover
            # The driver itself necessarily names the snapshot/output in argv and is alive
            # while writing this result. It is expected and not part of the owned product tree.
            result['expected_driver_leftover'] = [{'pid': int(r['ProcessId']), 'name': r.get('Name'), 'created': r.get('CreationDate'),
                                                   'reason': 'current E2E driver is writing its result'} for r in leftover if int(r['ProcessId']) == os.getpid()]
            # No blind kill of path-matching leftovers: they are recorded only. An owned
            # identity that survived is already in cleanup.final_live.
            result['leftover_not_killed'] = [{'pid': int(r['ProcessId']), 'name': r.get('Name'), 'created': r.get('CreationDate')} for r in leftover if int(r['ProcessId']) != os.getpid()]
        except Exception as e: result['cleanup_error'] = repr(e)
        walkie.close()
        if not gateway:
            # The isolated config held the real provider key only for the launch.
            try:
                cfg = read_json(config / 'config.json') or {}
                for row in (cfg.get('providers') or {}).values():
                    if isinstance(row, dict):
                        for key in ('api_key', 'api_keys'): row.pop(key, None)
                (config / 'config.json').write_text(json.dumps(cfg, indent=1), encoding='utf-8')
                result['scratch_key_scrubbed'] = True
            except Exception as error: result['scratch_key_scrubbed'] = repr(error)
        if gateway: gateway.close()
        after = write_ps_snapshot(out, 'processes-after')
        result['protected_processes_unchanged'] = protected_unchanged(before, after)  # diagnostic; live-session children may end normally
        result['protected_before'] = {str(k): {'Name': v['Name'], 'CreationDate': str(v['CreationDate'])} for k, v in protected_rows(before).items()}
        result['protected_after'] = {str(k): {'Name': v['Name'], 'CreationDate': str(v['CreationDate'])} for k, v in protected_rows(after).items()}
        owned_history = {(int(i[0]), str(i[1])) for i in (result.get('owned_ready_identities') or [])}
        owned_history |= {(int(i[0]), str(i[1])) for i in ((result.get('cleanup') or {}).get('owned_identities') or [])}
        killed_history = {(int(k['identity'][0]), str(k['identity'][1])) for k in ((result.get('cleanup') or {}).get('killed') or [])}
        result['protected_change_classification'] = opc.classify_protected_changes(
            before, after, EXPECTED_PROTECTED, PROTECTED_ROOTS, owned_history, killed_history)
        current = {p.relative_to(SNAP).as_posix(): sha(p) for p in SNAP.rglob('*') if p.is_file()}
        manifest = read_json(SNAP_MANIFEST)
        copy_now = {p.relative_to(home).as_posix(): sha(p) for p in home.rglob('*') if p.is_file()}
        diff_paths = sorted(set(copy_now) - set(manifest)) + [r for r, d in manifest.items() if copy_now.get(r) != d]
        result['run_copy_after'] = {'extras': sorted(set(copy_now) - set(manifest)), 'changed': [r for r, d in manifest.items() if copy_now.get(r) != d],
                                    'classified': {r: classify_copy_path(r) for r in diff_paths},
                                    'unexpected': [r for r in diff_paths if classify_copy_path(r) == 'other'],
                                    'types_tool_keys': {r: types_key_diff(SNAP / r, home / r) for r in diff_paths if classify_copy_path(r) == 'engine_written_types'}}
        result['protected_tree'] = protected_tree_report(before, after)
        result['worktree_freeze_after'] = freeze_check(ROOT)
        result['snapshot_freeze_after'] = freeze_check(SNAP)
        owned_pids = {int(r['ProcessId']) for r in result.get('tree_before_kill') or []}
        result['other_e2e_after'] = other_e2e_processes(after, [str(out), str(scratch), str(home)])
        result['exclusive_run'] = not result['other_e2e_before'] and not result['other_e2e_after']
        result['protected_respawn'] = respawn_evidence(before, after, owned_pids, run_start)
        run_end = time.time()
        result['walkie_log_after'] = walkie_log_state()
        result['walkie_presence_events_in_window'] = walkie_presence_events(run_start, run_end)
        result['run_end'] = run_end
        marks = [str(scratch), str(home), str(out)]
        result['live_ciel_before'] = live_ciel(before, marks); after_rows = after; result['live_ciel_after'] = live_ciel(after_rows, marks)
        # Diagnostic only: live sessions create/retire short-lived bash/tool processes.
        # Safety is the exact owned/kill/protected classification, not fleet stillness.
        result['live_ciel_unchanged'] = result['live_ciel_before'] == result['live_ciel_after']
        cmd_of = {int(r['ProcessId']): str(r.get('CommandLine') or '')[:220] for r in before + after}
        result['live_ciel_diff'] = {'ended': {k: [v, cmd_of.get(int(k))] for k, v in result['live_ciel_before'].items() if result['live_ciel_after'].get(k) != v},
                                    'new': {k: [v, cmd_of.get(int(k))] for k, v in result['live_ciel_after'].items() if result['live_ciel_before'].get(k) != v}}
        result['live_profile_changed_during_run'] = guard['changed']; profile_after = live_profile_state(workspace); (out / 'live-profile-after.json').write_text(json.dumps(profile_after, indent=1), encoding='utf-8'); result['live_profile_after_equal'] = profile_after == profile_before; result['live_profile_files_checked'] = len(profile_before)
        # ~/.claude.json is metadata the live sessions rewrite while no test runs; it is not
        # hash-gated. It must name no owned path, run id or owned session id after the run.
        claude_json_after = claude_json_owned_refs([str(scratch), str(home), str(out), out.name, scratch.name, session])
        result['claude_json_before'] = claude_json_before; result['claude_json_after'] = claude_json_after
        result['claude_json_no_owned_refs'] = not claude_json_after['hits']
        result['snapshot_after'] = {'files': len(current), 'extras': sorted(set(current) - set(manifest)), 'changed': [r for r, d in manifest.items() if current.get(r) != d],
                                    'digest': hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()}
        result['snapshot_after']['digest_ok'] = result['snapshot_after']['digest'] == result['snapshot_digest']
        safety_gate = {
            'core_launcher_roots_intact_and_no_protected_owned_or_killed': result['protected_change_classification']['passed'],
            'snapshot_digest': result['snapshot_after']['digest_ok'],
            'worktree_freeze': not result['worktree_freeze_after']['mismatch'],
            'snapshot_freeze': not result['snapshot_freeze_after']['mismatch'],
            'run_copy_expected_mutations_only': not result['run_copy_after']['unexpected'],
            'exclusive_run': bool(result.get('exclusive_run')),
            'owned_tree_disjoint_from_protected': not result.get('owned_tree_intersects_protected'),
            'cleanup_completed': (result.get('cleanup') or {}).get('completed') is True,
            'no_unexpected_leftover': not result.get('leftover_not_killed'),
            'live_profile_equal': result['live_profile_after_equal'] and not result['live_profile_changed_during_run'],
            'claude_json_no_owned_refs': result['claude_json_no_owned_refs'],
        }
        result['safety_gate'] = safety_gate
        if result['outcome'] == 'completed' and not all(safety_gate.values()): result['outcome'] = 'harness_or_safety_check_failed'
        real = read_json(REAL_CONFIG) or {}
        key = ((real.get('providers') or {}).get('cielairouter') or {}).get('api_key') or ''
        result['config'] = str(config); result['workspace'] = str(workspace); result['claude_config'] = str(claude)
        (out / 'result.json').write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding='utf-8')
        hits = secret_scan(out, [key])
        result['secret_scan'] = {'files_scanned': sum(1 for p in out.rglob('*') if p.is_file()), 'hits': hits}
        if hits:
            for h in hits: Path(h['file']).unlink(missing_ok=True)
            result['secret_scan']['removed'] = True
        (out / 'result.json').write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding='utf-8')
        print(json.dumps({'out': str(out), 'outcome': result['outcome'], 'facts': result.get('facts'), 'protected_processes_unchanged': result.get('protected_processes_unchanged'), 'snapshot_digest_ok': result['snapshot_after']['digest_ok'], 'secret_hits': len(hits), 'error': result.get('error')}, indent=1, ensure_ascii=False))
    return result

def iso_epoch(value):
    from datetime import datetime
    try: return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError: return None

def claude_json_owned_refs(markers):
    """~/.claude.json is metadata only; after a run it must name no owned path or run id."""
    if not CLAUDE_JSON.exists(): return {'exists': False, 'hits': []}
    data = CLAUDE_JSON.read_bytes(); text = data.decode('utf-8', errors='replace').lower()
    variants = set()
    for marker in markers:
        if not marker: continue
        for value in (marker, marker.replace(chr(92), '/'), marker.replace(chr(92), chr(92) * 2)): variants.add(value.lower())
    return {'exists': True, 'sha256': hashlib.sha256(data).hexdigest(), 'markers_checked': sorted(variants),
            'hits': sorted(v for v in variants if v in text)}

def design_assertions(debug, rows, wake_timeline, event_id, raw_board, gateway):
    """Final-design checks: one raw submit per identity; no wake left started after its
    prompt resolved; no Stop presentation while the identity's prompt is pending."""
    submits = []
    for line in debug:
        if PROMPT_SUBMIT_LOG in line:
            submits.append({'at': iso_epoch(line[:24]), 'text': line.split(PROMPT_SUBMIT_LOG, 1)[1][:400]})
    counts = {}
    for item in submits: counts[item['text']] = counts.get(item['text'], 0) + 1
    duplicates = {text[:200]: n for text, n in counts.items() if n > 1}
    eid = event_id or '§'
    wake_submits = [s for s in submits if 'Ciel side event requires main action' in s['text'] and eid[:12] in s['text']]
    # Debug records the module's raw submit and then the nested prompt.submit hook's
    # settle. The settle timestamp is the raw $.prompt.submit resolution boundary.
    settle_lines = []
    submit_at = wake_submits[0]['at'] if wake_submits else None
    for line in debug:
        if 'ciel-work@inline prompt.submit settled in ' not in line: continue
        at = iso_epoch(line[:24])
        if submit_at is not None and at is not None and at >= submit_at: settle_lines.append({'at': at, 'line': line[:240]})
    prompt_resolved_at = settle_lines[0]['at'] if settle_lines else None
    main_rows = [r for r in rows if not r.get('isSidechain') and r.get('type') != 'api-request-blob']
    def text_of(r): return json.dumps(r, ensure_ascii=False)
    wake_rows_at = [iso_epoch(r.get('timestamp')) for r in main_rows if r.get('type') == 'user' and WAKE_HEAD in text_of(r) and eid in text_of(r)]
    stops = [{'at': iso_epoch(r.get('timestamp')), 'type': r.get('type'), 'subtype': r.get('subtype'),
              'head': 'stop' if STOP_HEAD in text_of(r) else 'uncertain'}
             for r in main_rows if eid in text_of(r) and (STOP_HEAD in text_of(r) or UNCERTAIN_HEAD in text_of(r))]
    events = [w for w in wake_timeline if w.get('event') == eid[:12]]
    first_submit = min((s['at'] for s in wake_submits if s['at']), default=None)
    left = next((w for w in events if w['wake_state'] not in ('submitting', 'pending', None) and (first_submit is None or w['at'] >= first_submit - 1)), None)
    returned = [w for w in events if left and w['at'] > left['at'] and w['wake_state'] == 'submitting']
    final_wake = (((raw_board.get('side_handoffs') or {}).get(event_id) or {}).get('wake') or {})
    window_end = left['at'] if left else float('inf')
    in_window = [s for s in stops if first_submit is not None and s['at'] is not None and first_submit <= s['at'] <= window_end]
    gateway_in_window = []
    if gateway is not None and first_submit is not None:
        for r in gateway.requests:
            if r.get('classified') != 'main' or not (first_submit <= r['at'] <= window_end): continue
            body = json.dumps(r['body'].get('messages') or [], ensure_ascii=False)
            if eid in body and (STOP_HEAD in body or UNCERTAIN_HEAD in body): gateway_in_window.append(r['seq'])
    report = {
        'prompt_submits': len(submits), 'distinct_prompt_texts': len(counts), 'duplicate_prompt_texts': duplicates,
        'wake_prompt_submits_for_event': len(wake_submits), 'first_wake_submit_at': first_submit,
        'wake_prompt_rows_at': wake_rows_at,
        'wake_timeline_for_event': events, 'left_submitting': left, 'returned_to_submitting_after': returned,
        'final_wake_state': final_wake.get('state'), 'final_wake_phase': final_wake.get('phase'),
        'prompt_hook_settles_after_submit': settle_lines[:3], 'prompt_resolved_at': prompt_resolved_at,
        'prompt_resolved_to_recorded_s': (left['at'] - prompt_resolved_at) if left and prompt_resolved_at else None,
        'prompt_entered_to_recorded_s': (left['at'] - min(wake_rows_at)) if left and wake_rows_at and all(wake_rows_at) else None,
        'stop_presentations_of_event': stops, 'stop_presentations_while_pending': in_window,
        'gateway_stop_requests_while_pending': gateway_in_window,
    }
    facts = {
        'no_same_identity_raw_prompt_submit_twice': not duplicates and len(wake_submits) <= 1,
        # Recorded (left submitting) within 10 s of the raw prompt's resolution, never back
        # to submitting, and not submitting at the end. The 10 s bound covers the 0.25 s
        # sampler, the record call and its tool-guard hooks (about 0.1 s each in run B).
        'no_side_wake_left_started_after_prompt_resolved': (final_wake.get('state') not in ('submitting', 'pending', None) and left is not None
                                                            and not returned and prompt_resolved_at is not None
                                                            and left['at'] - prompt_resolved_at <= 10),
        'no_stop_presentation_while_identity_pending': first_submit is not None and not in_window and not gateway_in_window,
    }
    return report, facts

def verify_snapshot_freeze():
    """Snapshot, its verify report and the SSE freeze must all name the same bytes."""
    verify = read_json(SNAP_VERIFY) or {}
    sums = {}
    for line in (FREEZES[0][1] / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        if line.strip(): digest, rel = line.split(' *', 1); sums[rel.removeprefix('./')] = digest
    problems = []
    for rel, digest in EXPECTED_SSE_FREEZE.items():
        if sums.get(rel) != digest: problems.append('freeze SHA256SUMS ' + rel + ' is ' + str(sums.get(rel)))
    for rel, digest in EXPECTED_PRODUCT.items():
        got = sha(SNAP / rel) if (SNAP / rel).exists() else None
        if got != digest: problems.append('snapshot ' + rel + ' is ' + str(got) + ', released ' + digest)
    recorded = {label: (Path(folder).resolve(), digest) for label, folder, digest in verify.get('freezes') or []}
    for label, folder in FREEZES:
        if label not in recorded or recorded[label][0] != folder.resolve() or recorded[label][1] != sha(folder / 'SHA256SUMS'):
            problems.append('snapshot was not verified against ' + label + ' freeze ' + str(folder))
    if verify.get('freeze_mismatch'): problems.append('snapshot verify report has freeze mismatches')
    if not (verify.get('product_bounds') or {}).get('passed'): problems.append('snapshot verify report lacks passed product bounds')
    live = freeze_check(SNAP)
    if live['mismatch']: problems.append('snapshot bytes differ from freeze: ' + json.dumps(live['mismatch'])[:300])
    bounds = rti.verify_product_bounds(SNAP)
    if not bounds['passed']: problems.append('product bound expressions missing: ' + json.dumps(bounds['missing'])[:300])
    return problems

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN); ctypes.windll.kernel32.SetConsoleCtrlHandler(_CTRL, True)
    global SNAP, SNAP_VERIFY, SNAP_MANIFEST, FREEZES
    p=argparse.ArgumentParser(); p.add_argument('run',choices=('A','B')); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,default=SNAP); p.add_argument('--sse-freeze',type=Path,default=FREEZES[0][1])
    args=p.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    SNAP=args.snapshot.resolve(); SNAP_VERIFY=SNAP.parent/(SNAP.name+'-verify.json'); SNAP_MANIFEST=SNAP.parent/(SNAP.name+'-manifest.json')
    FREEZES=[('sse', args.sse_freeze.resolve()), FREEZES[1]]
    verify=read_json(SNAP_VERIFY); manifest=read_json(SNAP_MANIFEST); changed=[]; extras=[]
    current={p.relative_to(SNAP).as_posix():sha(p) for p in SNAP.rglob('*') if p.is_file()}
    changed=[r for r,d in manifest.items() if current.get(r)!=d]; extras=sorted(set(current)-set(manifest)); digest=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()
    if changed or extras or digest!=verify['snapshot_digest']: raise SystemExit('snapshot verification failed')
    problems=verify_snapshot_freeze()
    if problems: raise SystemExit('snapshot/freeze verification failed: ' + '; '.join(problems))
    result=run(args.run,args.output); raise SystemExit(0 if result.get('outcome')=='completed' else 1)
if __name__=='__main__': main()
