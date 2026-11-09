"""Operator-owned MCP reply bindings; inbound events never choose a tool.

A binding is either a JSON template (``server``, ``tool``, ``arguments``) or a
named preset (``preset``, ``server``). A preset reads only fixed, pattern-checked
fields of the original event; the operator still chooses the server.
"""
import hashlib
import json
import re
import uuid

_FIELDS = {'source', 'event_id', 'receiver_id', 'reply', 'kind', 'receipt_id'}
_NAME = re.compile(r'[A-Za-z0-9_.:-]{1,150}')
_UUID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
_MESSAGE_ID = re.compile(r'[0-9]{1,20}')
PRESETS = ('walkie',)


def _check_source(row, sources):
    if not isinstance(row['source'], str) or not row['source'] or len(row['source']) > 300:
        raise ValueError('connector source must be exact and bounded')
    if not isinstance(row['receiver_id'], str):
        raise ValueError('connector receiver_id must be text')
    if (row['source'], row['receiver_id']) in sources:
        raise ValueError('connector source must be exact, bounded and unique')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}', row['receiver_id']):
        raise ValueError('connector receiver_id must name an exact configured receiver')
    sources.add((row['source'], row['receiver_id']))


def validate(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('reply connectors must be a JSON array of at most 32 entries')
    sources = set()
    for row in value:
        if isinstance(row, dict) and set(row) == {'source', 'receiver_id', 'preset', 'server'}:
            _check_source(row, sources)
            if row['preset'] not in PRESETS:
                raise ValueError('unknown connector preset; use ' + ', '.join(PRESETS))
            if not isinstance(row['server'], str) or not _NAME.fullmatch(row['server']):
                raise ValueError('invalid connector MCP name')
            continue
        if not isinstance(row, dict) or set(row) != {'source', 'receiver_id', 'server', 'tool', 'arguments'}:
            raise ValueError('connector needs source, receiver_id, server, tool and arguments, or source, receiver_id, preset and server')
        _check_source(row, sources)
        for key in ('server', 'tool'):
            if not isinstance(row[key], str) or not _NAME.fullmatch(row[key]):
                raise ValueError('invalid connector MCP name')
        if not isinstance(row['arguments'], dict) or len(json.dumps(row['arguments']).encode()) > 8192:
            raise ValueError('connector arguments must be a bounded JSON object')
        found_reply = False
        def check(item):
            nonlocal found_reply
            if item == '$reply':
                found_reply = True
            if isinstance(item, str) and item.startswith('$') and item[1:] not in _FIELDS:
                raise ValueError('unknown connector field placeholder')
            if isinstance(item, dict):
                for key, part in item.items():
                    if any(word in str(key).lower() for word in ('authorization', 'password', 'secret', 'token', 'api_key')):
                        raise ValueError('connector credentials must stay in the native MCP connection')
                    check(part)
            if isinstance(item, list):
                for part in item:
                    check(part)
        check(row['arguments'])
        if not found_reply:
            raise ValueError('connector arguments must include $reply')
    return value


def walkie_target(original):
    """The Walkie conversation and message an event names, or None.

    Only ``data.conversation`` (UUID) and ``data.message_id`` (digits) are read;
    nothing else in the event can steer the call.
    """
    try:
        data = json.loads(original).get('data')
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    conversation, message = data.get('conversation'), data.get('message_id')
    if not isinstance(conversation, str) or not _UUID.fullmatch(conversation):
        return None
    if isinstance(message, int) and not isinstance(message, bool):
        message = str(message)
    if not isinstance(message, str) or not _MESSAGE_ID.fullmatch(message):
        return None
    return {'conversation_id': conversation, 'message_id': message}


def binding_for(config, route):
    bindings = validate(config.get('work_side_intake', {}).get('reply_connectors', []))
    source = route.get('sender_id', '')
    receiver = str(route.get('channel', '')).removeprefix('external:')
    return next((row for row in bindings if row['source'] == source and row['receiver_id'] == receiver), None)


def prepare(config, route, receipt_id, reply, kind, *, original=''):
    binding = binding_for(config, route)
    if binding is None:
        return None
    if binding.get('preset') == 'walkie':
        target = walkie_target(original)
        if target is None:
            return None
        # A fixed client_id per receipt lets Walkie recognize a repeated send.
        client = str(uuid.UUID(hashlib.sha256((receipt_id + ':final').encode()).hexdigest()[:32]))
        return {'server': binding['server'], 'tool': 'walkie_send_message',
                'arguments': {'conversation_id': target['conversation_id'], 'client_id': client,
                              'reply_to_message_id': target['message_id'], 'text': reply}}
    fields = {'source': route.get('sender_id', ''), 'event_id': route.get('thread_id', ''),
              'receiver_id': str(route.get('channel', '')).removeprefix('external:'),
              'receipt_id': receipt_id, 'reply': reply, 'kind': kind}
    def expand(item):
        if isinstance(item, str) and item.startswith('$'):
            return fields[item[1:]]
        if isinstance(item, dict):
            return {key: expand(value) for key, value in item.items()}
        if isinstance(item, list):
            return [expand(value) for value in item]
        return item
    return {'server': binding['server'], 'tool': binding['tool'], 'arguments': expand(binding['arguments'])}


def source_read(config, route, original):
    """The host read of the message a Walkie event names, before the side actor runs."""
    binding = binding_for(config, route)
    target = walkie_target(original) if binding and binding.get('preset') == 'walkie' else None
    if target is None:
        return None
    return {'server': binding['server'], 'tool': 'walkie_read_messages',
            'arguments': {'conversation_id': target['conversation_id'], 'after': str(int(target['message_id']) - 1)},
            'message_id': target['message_id']}
