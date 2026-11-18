"""What the source receives first, on any build: the receipt ACK text of one Walkie event.

    python ack_probe.py <tree>
"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from ciel_runtime_support.side_ack_outbox import prepare  # noqa: E402
from ciel_runtime_support.side_intake_inbox import SideIntakeInbox  # noqa: E402
from ciel_runtime_support.side_work_decision import apply  # noqa: E402
from ciel_runtime_support.work_plan_store import session_work_plan_store  # noqa: E402
CONV = '256a28fd-f1c3-443a-9782-19405288a8d3'
MESSAGE = 'payment retry bug: fix it on staging today'
ROUTE = {'source_kind': 'external_event', 'channel': 'external:default', 'sender_id': 'http://walkie.invalid/c/' + CONV}
with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    inbox = SideIntakeInbox(root, 'claude:probe')
    event = json.dumps({'type': 'com.ciel.walkie.support.message', 'data': {'conversation': CONV, 'message_id': '1',
                        'event_type': 'message.created', 'text': MESSAGE, 'message_context': {'id': '1'}}})
    key = inbox.receive(event, 'walkie:1', route=ROUTE)['receipt_id']
    ack = prepare(root, 'claude:probe', key)['arguments']['text']
    store = session_work_plan_store(root, 'claude:probe')
    result = apply(store, '0' * 63 + '1', {'action': 'create', 'kind': 'request', 'title': 'retry fix',
                   'summary': 'retry fix', 'reply': 'Queued the staging retry fix.', 'priority': 'P3'}, 0)
    review = '0' * 63 + '1' in (store.read().get('side_reviews') or {})
    print(json.dumps({'ack_text': ack, 'ack_echoes_message': MESSAGE in ack,
                      'final_text_starts_with_ACK': result['final_text'].startswith('ACK'),
                      'p3_queue_entry_holds_main_review': review}, ensure_ascii=False, indent=1))
