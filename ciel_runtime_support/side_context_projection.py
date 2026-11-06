"""Select real API message boundaries using provider-reported token counts."""
import copy
import hashlib
import json


class SideContextUnavailable(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _blocks(message):
    content = message.get('content', [])
    return content if isinstance(content, list) else []


def _boundaries(messages):
    outstanding = set()
    starts = []
    for index, message in enumerate(messages):
        if message.get('role') == 'user' and not outstanding and not any(
            block.get('type') == 'tool_result' for block in _blocks(message)
        ):
            starts.append(index)
        for block in _blocks(message):
            if block.get('type') == 'tool_use':
                outstanding.add(block.get('id'))
            elif block.get('type') == 'tool_result':
                if block.get('tool_use_id') not in outstanding:
                    raise SideContextUnavailable('parent request has an orphan tool result')
                outstanding.remove(block['tool_use_id'])
    if outstanding:
        raise SideContextUnavailable('parent request has unmatched tool calls')
    return starts


def select(parent, mode, percent, model, count_tokens):
    if mode not in ('full', 'recent') or type(percent) is not int or not 1 <= percent <= 100:
        raise SideContextUnavailable('invalid context selection')
    messages = copy.deepcopy(parent.get('messages', []))
    if not messages:
        raise SideContextUnavailable('parent conversation is not available')
    removed = 0
    # A recent-tail branch or model switch cannot replay signed reasoning as if
    # it still had its original prefix. Record this deliberate branch boundary.
    if mode == 'recent' or model != parent.get('model'):
        for message in messages:
            if isinstance(message.get('content'), list):
                kept = []
                for block in message['content']:
                    if block.get('type') in ('thinking', 'redacted_thinking'):
                        removed += 1
                    else:
                        kept.append(block)
                message['content'] = kept
        messages = [message for message in messages if message.get('content')]
    starts = _boundaries(messages)
    if not starts or starts[0] != 0:
        raise SideContextUnavailable('parent conversation has no safe initial user boundary')
    info = {'mode': mode, 'requested_percent': 100 if mode == 'full' else percent,
            'count_source': 'unavailable', 'conversation_tokens': None, 'selected_tokens': None,
            'selected_percent': 100 if mode == 'full' else None, 'start': 0,
            'thinking_blocks_omitted': removed, 'system_digest': digest(parent.get('system')),
            'tools_digest': digest(parent.get('tools', [])), 'work_board_in_percentage': False}
    if mode == 'full':
        return messages, info
    counts = {}
    def count(start):
        if start not in counts:
            # Conversation token share excludes system/tools/Work. Reasoning was
            # explicitly omitted above; tool use/result groups remain complete.
            value = count_tokens({'model': model, 'messages': messages[start:]})
            if type(value) is not int or value <= 0:
                raise SideContextUnavailable('provider token count unavailable')
            counts[start] = value
        return counts[start]
    total = count(0)
    target = total * percent / 100
    left, right = 0, len(starts) - 1
    selected = 0
    # Choose the smallest suffix at a complete user/tool boundary that reaches
    # the requested share. Counts are provider-reported, never character ratios.
    while left <= right:
        middle = (left + right) // 2
        if count(starts[middle]) >= target:
            selected = starts[middle]
            left = middle + 1
        else:
            right = middle - 1
    chosen = count(selected)
    if chosen > total:
        raise SideContextUnavailable('provider token counts are inconsistent')
    info.update(count_source='provider_count_tokens', conversation_tokens=total,
                selected_tokens=chosen, selected_percent=chosen * 100 / total, start=selected)
    return messages[selected:], info
