"""Real side backend for side_real_backend_harness.mjs.

One JSON request per stdin line, one JSON reply per stdout line. Every router
call goes through the real MCP dispatch (dispatch_work_tool) onto the real
session Work store and side inbox under a temporary directory. Nothing here
scripts a backend answer. The only test control is the clock the wake intent
lease reads (side_wake.time), which `advance` moves forward.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stdin.reconfigure(encoding='utf-8')
sys.path.insert(0, os.environ['SIDE_SOURCE'])
from ciel_runtime_support import side_wake  # noqa: E402
from ciel_runtime_support import work_plan_tree as tree  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.work_mcp_tools import dispatch_work_tool  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402

IDENTITY = 'claude:parent'
TOKEN = 'x' * 40
ROOT = Path(os.environ['SIDE_REAL_ROOT'])
ROOT.mkdir(parents=True, exist_ok=True)
CONFIG = {'work_mode': True, 'work_side_intake': {'enabled': True, 'context_mode': 'isolated'},
          'work_parallel_host': {'token': TOKEN}}
STORE = session_work_plan_store(ROOT, IDENTITY)


class Clock:
    """time.time() plus a skew the harness advances; nothing else of `time` changes."""

    def __init__(self):
        self.skew = 0.0

    def time(self):
        return time.time() + self.skew

    def __getattr__(self, name):
        return getattr(time, name)


CLOCK = Clock()
side_wake.time = CLOCK


def digest(value):
    return None if value is None else hashlib.sha256(str(value).encode()).hexdigest()[:12]


def init():
    doc, goal = STORE.transaction(lambda d: tree.add_node(d, None, 'report'))
    doc, gate = STORE.transaction(lambda d: tree.add_node(d, goal, 'gate', waiting_on='approval 7'))
    return {'token': TOKEN, 'goal': goal, 'gate': gate, 'version': STORE.read()['version']}


def receive(text, key):
    return SideIntakeInbox(ROOT, IDENTITY).receive(text, key)['receipt_id']


def tool(name, args):
    answer = dispatch_work_tool(1, name, dict(args), load_config=lambda: CONFIG, state_dir=ROOT,
                                respond=lambda _id, text, error=False: {'text': text, 'error': error})
    return {'text': answer['text'], 'error': bool(answer['error'])}


def inspect(event):
    doc = STORE.read()
    row = (doc.get('side_handoffs') or {}).get(event) or {}
    wake = dict(row.get('wake') or {})
    receipt = SideIntakeInbox(ROOT, IDENTITY)._load(event) or {}
    notice = receipt.get('release_notice')
    return {'version': doc['version'], 'handoff': row.get('state'),
            # Raw attempt ids, owner fences and receipts never leave this process.
            'wake': {'state': wake.get('state'), 'phase': wake.get('phase'), 'channel': wake.get('channel'),
                     'revision': wake.get('revision'), 'read': bool(wake.get('read_at')),
                     'has_receipt': bool(wake.get('receipt')), 'attempt': digest(wake.get('attempt_id')),
                     'owner': digest(wake.get('owner_nonce')), 'conversation_id': wake.get('conversation_id'),
                     'prompt_state': (wake.get('prompt') or {}).get('state')},
            'notice': None if not isinstance(notice, dict) else {'state': notice.get('state'),
                                                               'has_receipt': bool(notice.get('receipt'))},
            'waiting_on': {node['id']: node.get('waiting_on', '') for node in doc['nodes']}}


def continue_as(identity):
    """A native continuation: `identity` resolves to this session's Work board.

    The lineage marker is the one work_session_lineage itself writes for a
    parked background handoff; the conversation identity changes, the board
    and its side_handoffs stay.
    """
    marker = ROOT / 'work-sessions' / hashlib.sha256(identity.encode()).hexdigest() / 'work-lineage.json'
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'session': identity, 'canonical': IDENTITY, 'authority': 'native-parked-handoff'}),
                      encoding='utf-8')
    return {'same_board': session_work_plan_store(ROOT, identity).path == STORE.path}


def dispatch(request):
    action = request['action']
    if action == 'init':
        return init()
    if action == 'continue':
        return continue_as(request['identity'])
    if action == 'receive':
        return receive(request['text'], request['key'])
    if action == 'tool':
        return tool(request['tool'], request['args'])
    if action == 'inspect':
        return inspect(request['event'])
    if action == 'advance':
        CLOCK.skew += float(request['seconds'])
        return {'skew': CLOCK.skew}
    raise ValueError('unknown action ' + str(action))


for line in sys.stdin:
    try:
        reply = {'ok': True, 'value': dispatch(json.loads(line))}
    except Exception as error:  # Reported to the harness, never swallowed.
        reply = {'ok': False, 'error': str(error), 'trace': traceback.format_exc()}
    print(json.dumps(reply, ensure_ascii=False), flush=True)
