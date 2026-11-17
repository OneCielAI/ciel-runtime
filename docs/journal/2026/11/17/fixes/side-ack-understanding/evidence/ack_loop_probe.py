"""Does another Ciel session's receipt ACK start a side actor, is it ACKed, and
does a drain queue one that an older build left pending as Work?

    python ack_loop_probe.py <tree>
"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from ciel_runtime_support.side_ack_outbox import prepare  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.side_intake_processing import claim  # noqa: E402
CONV = '256a28fd-f1c3-443a-9782-19405288a8d3'
ROUTE = {'source_kind': 'external_event', 'channel': 'external:default', 'sender_id': 'http://walkie.invalid/c/' + CONV}
TEXTS = {'legacy_template': 'ACK — 요청을 받았습니다.\n\n복기: payment retry bug: fix it on staging today',
         'this_build_template': '메시지를 받았습니다. 내용을 확인한 뒤 이해한 내용과 처리 결과를 이어서 알려 드리겠습니다.'}
out = {}
for index, (name, text) in enumerate(TEXTS.items()):
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        inbox = SideIntakeInbox(root, 'claude:probe')
        event = json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
            'conversation': CONV, 'message_id': str(index + 1), 'event_type': 'message.created', 'text': text,
            'message_context': {'id': str(index + 1)}}}, ensure_ascii=False)
        key = inbox.receive(event, 'walkie:' + str(index + 1), route=ROUTE)['receipt_id']
        ack = prepare(root, 'claude:probe', key)
        claimed = claim(root, 'claude:probe', 'probe-host')
        out[name] = {'receipt_ack_sent_back': ack.get('state') == 'submitting',
                     'side_actor_claimed': bool(claimed and claimed['receipt_id'] == key)}
import hashlib, time  # noqa: E401,E402
from ciel_runtime_support.side_intake_service import drain  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    inbox = SideIntakeInbox(root, 'claude:drain')
    text = json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {
        'conversation': CONV, 'message_id': '9', 'event_type': 'message.created', 'text': TEXTS['legacy_template'],
        'message_context': {'id': '9'}}}, ensure_ascii=False)
    inbox.receive(json.dumps({'type': 'note', 'text': 'creates the inbox'}), 'note:1', route={'source_kind': 'web_chat'})
    key = hashlib.sha256(b'walkie:9').hexdigest()
    inbox._save_file(inbox._event_path('pending', key), {
        'text': text, 'event_key': 'walkie:9', 'digest': hashlib.sha256(text.encode()).hexdigest(),
        'received_at': time.time() - 600, 'stage': 'received', 'item_id': None,
        'external_ack': 'submitted', 'route': dict(ROUTE), 'session_id': 'claude:drain'})
    drain(root, 'claude:drain', admit=True, limit=10)
    titles = [node.get('title') for node in session_work_plan_store(root, 'claude:drain').read()['nodes']]
    out['older_build_pending_legacy_ack'] = {'drain_queued_it_as_work': any('ACK' in str(t) or '요청을 받았습니다' in str(t) for t in titles),
                                             'board_nodes_after_drain': len(titles)}
print(json.dumps(out, indent=1))
