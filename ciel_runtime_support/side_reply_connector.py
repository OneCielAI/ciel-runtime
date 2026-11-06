"""Operator-owned MCP reply bindings; inbound events never choose a tool."""
import json
import re

_FIELDS = {'source', 'event_id', 'receiver_id', 'reply', 'kind', 'receipt_id'}


def validate(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError('reply connectors must be a JSON array of at most 32 entries')
    sources = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != {'source', 'receiver_id', 'server', 'tool', 'arguments'}:
            raise ValueError('connector needs source, receiver_id, server, tool and arguments')
        if not isinstance(row['source'], str) or not row['source'] or len(row['source']) > 300:
            raise ValueError('connector source must be exact and bounded')
        if not isinstance(row['receiver_id'], str):
            raise ValueError('connector receiver_id must be text')
        if (row['source'], row['receiver_id']) in sources:
            raise ValueError('connector source must be exact, bounded and unique')
        if not isinstance(row['receiver_id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}', row['receiver_id']):
            raise ValueError('connector receiver_id must name an exact configured receiver')
        sources.add((row['source'], row['receiver_id']))
        for key in ('server', 'tool'):
            if not isinstance(row[key], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,150}', row[key]):
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


def prepare(config, route, receipt_id, reply, kind):
    bindings = validate(config.get('work_side_intake', {}).get('reply_connectors', []))
    source = route.get('sender_id', '')
    receiver = str(route.get('channel', '')).removeprefix('external:')
    binding = next((row for row in bindings if row['source'] == source and row['receiver_id'] == receiver), None)
    if binding is None:
        return None
    fields = {'source': source, 'event_id': route.get('thread_id', ''),
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
