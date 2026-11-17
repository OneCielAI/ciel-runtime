"""Read-only counts over one session's durable side inbox (no message text printed).

    python -I inbound_walkie_probe.py <workspace state dir>/side-inboxes

Counts unique Walkie events (conversation, message_id): whether any carries a
``client_id`` key, the ``own`` flag, and how many are a Ciel host's legacy
receipt ACK ("ACK — 요청을 받았습니다.\n\n복기: "), by sender display name, with
the external ACK state this session recorded for them.
"""
import collections, json, pathlib, sys
sys.stdout.reconfigure(encoding="utf-8")
LEGACY = 'ACK — 요청을 받았습니다.\n\n복기: '
seen, own, senders, acked, stage = set(), collections.Counter(), collections.Counter(), collections.Counter(), collections.Counter()
client_id_key = client_id_text = 0
received = []


def keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


for path in pathlib.Path(sys.argv[1]).rglob("*.json"):
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        body = json.loads(row["text"])
    except (OSError, ValueError, KeyError, TypeError):
        continue
    if not isinstance(body, dict) or not str(body.get("type", "")).startswith("com.ciel.walkie."):
        continue
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    ident = (str(data.get("conversation")), str(data.get("message_id")))
    if ident in seen:
        continue
    seen.add(ident)
    own[repr(data.get("own"))] += 1
    if "client_id" in set(keys(body)):
        client_id_key += 1
    elif "client_id" in json.dumps(body):
        client_id_text += 1
    if str(data.get("text") or "").startswith(LEGACY):
        sender = data.get("sender")
        senders[str(sender.get("name") if isinstance(sender, dict) else sender)] += 1
        acked[str(row.get("external_ack"))] += 1
        stage[str(row.get("stage"))] += 1
        if isinstance(row.get("received_at"), (int, float)):
            received.append(row["received_at"])
import time
span = [time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(t)) for t in (min(received), max(received))] if received else []
print(json.dumps({"walkie_events": len(seen), "events_with_client_id_key": client_id_key,
                  "events_mentioning_client_id_in_text_only": client_id_text, "own": dict(own),
                  "legacy_receipt_ack_inbound": sum(senders.values()), "legacy_by_sender": dict(senders),
                  "legacy_external_ack_state": dict(acked), "legacy_stage": dict(stage),
                  "legacy_received_span_utc": span}, ensure_ascii=False, indent=1))
