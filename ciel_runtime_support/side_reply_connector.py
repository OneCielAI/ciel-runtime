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
# Walkie's own id contract (origin/main f59bbe11): positive, no leading zero,
# at most 18 digits. Larger or zero-padded ids are rejected by Walkie itself.
_MESSAGE_ID = re.compile(r'[1-9][0-9]{0,17}')
# Marker Walkie appends to an SSE frame it cut at its size limit.
WALKIE_TRUNCATION_MARKER = '[잘림]'
PRESETS = ('walkie',)
# Walkie support CloudEvents. With no operator route configured, these reply
# through the session's own ``walkie`` MCP server (KOBE 2026-11-09: every side
# answer was committed but silently undelivered as route_unavailable).
WALKIE_EVENT_PREFIX = 'com.ciel.walkie.'
DEFAULT_WALKIE_SERVER = 'walkie'
# Walkie names each conversation as its own CloudEvent source
# (".../conversations/<uuid>", Prody 2026-11-09), so a preset may cover every
# source of one receiver. The preset still reads only pattern-checked fields.
ANY_SOURCE = '*'


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
        if row['source'] == ANY_SOURCE:
            # A template sends wherever its arguments say; only a preset, which
            # reads pattern-checked event fields, may cover every source.
            raise ValueError('source "*" is allowed only for a preset')
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


def _walkie_id(value):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    return value if isinstance(value, str) and _MESSAGE_ID.fullmatch(value) else None


def _walkie_body(original):
    try:
        body = json.loads(original)
    except (ValueError, TypeError):
        return None, None
    data = body.get('data') if isinstance(body, dict) else None
    return body, data if isinstance(data, dict) else None


def walkie_target(original):
    """Validated reply target, preserving explicit-null lineage sentinels."""
    body, data = _walkie_body(original)
    if not isinstance(data, dict):
        return None
    conversation = data.get('conversation')
    if not isinstance(conversation, str) or not _UUID.fullmatch(conversation):
        return None
    context = data.get('message_context') if isinstance(data.get('message_context'), dict) else {}
    raw_target = context.get('id') if context.get('id') is not None else data.get('message_id')
    message = _walkie_id(raw_target)
    if message is None:
        return None
    result = {'conversation_id': conversation, 'message_id': message}
    for field in ('reply_to_message_id', 'thread_root_id'):
        if field not in data:
            continue
        raw = data[field]
        if raw is None:
            result[field] = None
        else:
            value = _walkie_id(raw)
            if value is None:
                return None
            result[field] = value
    return result


def walkie_source_context(original):
    """Classify a Walkie event for canonical full-thread host prefetch.

    ``thread``: a message or thread is referenced; the host must read and verify
    the whole thread before any fork. ``none_referenced``: the event names no
    message and no thread (reminders, notices). ``invalid``: an identifier is
    malformed or candidate identities disagree; never guessed, never retried.
    """
    body, data = _walkie_body(original)
    if not isinstance(body, dict) or not str(body.get('type') or '').startswith(WALKIE_EVENT_PREFIX):
        return None
    # JSON wire text may escape Korean; inspect the parsed body rendered as
    # Unicode rather than relying on the literal wire spelling.
    truncated = WALKIE_TRUNCATION_MARKER in json.dumps(body, ensure_ascii=False)
    flag = {'event_truncated': True} if truncated else {}
    if not isinstance(data, dict):
        return {'classification': 'invalid', 'reason': 'Walkie event data malformed', **flag}
    conversation = data.get('conversation')
    if not isinstance(conversation, str) or not _UUID.fullmatch(conversation):
        return {'classification': 'invalid', 'reason': 'Walkie conversation identity malformed', **flag}
    context = data.get('message_context')
    if context is not None and not isinstance(context, dict):
        return {'classification': 'invalid', 'reason': 'Walkie message_context malformed', **flag}
    context = context or {}
    thread_task = context.get('thread_task')
    if thread_task is not None and not isinstance(thread_task, dict):
        return {'classification': 'invalid', 'reason': 'Walkie thread_task malformed', **flag}
    thread_task = thread_task or {}
    raw = {
        'message_id': data.get('message_id'),
        'context_id': context.get('id'),
        'thread_root_id': data.get('thread_root_id'),
        'context_root_id': thread_task.get('root_id'),
        'reply_to_message_id': data.get('reply_to_message_id'),
    }
    ids = {}
    for key, value in raw.items():
        if value is None:
            continue
        parsed = _walkie_id(value)
        if parsed is None:
            return {'classification': 'invalid', 'reason': 'Walkie ' + key + ' malformed', **flag}
        ids[key] = parsed
    if not ids:
        return {'classification': 'none_referenced', 'conversation_id': conversation, **flag}
    # Every present candidate for one identity must agree (544/544 durable
    # Walkie events agree); a disagreement is corruption, never a choice.
    targets = {ids[key] for key in ('context_id', 'message_id') if key in ids}
    roots = {ids[key] for key in ('thread_root_id', 'context_root_id') if key in ids}
    if len(targets) > 1:
        return {'classification': 'invalid', 'reason': 'Walkie target identities disagree', **flag}
    if len(roots) > 1:
        return {'classification': 'invalid', 'reason': 'Walkie thread root identities disagree', **flag}
    target = next(iter(targets), None)
    root = next(iter(roots), None) or target or ids.get('reply_to_message_id')
    result = {'classification': 'thread', 'conversation_id': conversation,
              'root_id': root, 'target_message_id': target or root, **flag}
    # Presence is meaningful: explicit null proves a root event.
    for field in ('reply_to_message_id', 'thread_root_id'):
        if field in data:
            result[field] = ids.get(field) if data[field] is not None else None
    return result

def _walkie_event(original):
    try:
        body = json.loads(original)
    except (ValueError, TypeError):
        return False
    return isinstance(body, dict) and str(body.get('type') or '').startswith(WALKIE_EVENT_PREFIX)


def binding_for(config, route, original=''):
    bindings = validate(config.get('work_side_intake', {}).get('reply_connectors', []))
    source = route.get('sender_id', '')
    receiver = str(route.get('channel', '')).removeprefix('external:')
    exact = next((row for row in bindings if row['source'] == source and row['receiver_id'] == receiver), None)
    found = exact or next((row for row in bindings if row['source'] == ANY_SOURCE and row['receiver_id'] == receiver
                           and row.get('preset')), None)
    if found or bindings:
        # Operator-configured routes decide; nothing is added implicitly.
        return found
    if (route.get('source_kind') == 'external_event' and _NAME.fullmatch(receiver or '')
            and _walkie_event(original)):
        return {'source': ANY_SOURCE, 'receiver_id': receiver, 'preset': 'walkie',
                'server': DEFAULT_WALKIE_SERVER, 'implicit': True}
    return None


def prepare(config, route, receipt_id, reply, kind, *, original=''):
    binding = binding_for(config, route, original)
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
    """Host-owned canonical Walkie thread context specification."""
    binding = binding_for(config, route, original)
    context = walkie_source_context(original)
    if context is None:
        return None
    if not binding or binding.get('preset') != 'walkie':
        # A Walkie event is still classified, even when no outbound route exists.
        server = DEFAULT_WALKIE_SERVER
    else:
        server = binding['server']
    return {'server': server, **context}
