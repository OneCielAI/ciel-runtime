"""Router-side parent prefix projection; native child still executes its tools."""
import copy
import json
import re
import urllib.parse

from .channel_message_repository import exclusive_file_lock
from .durable_json_file import save
from .side_context_projection import SideContextUnavailable, select
from .side_context_store import SideContextStore, bound_session
from .side_intake_inbox import SideIntakeInbox
from .side_intake_processing import verify_claim

_HANDLE = re.compile(r'^[0-9a-f]{32}$')


def request_context(body):
    """Only parse the host's standalone event envelope, not quoted source text."""
    found = None
    for message in body.get('messages', []):
        if not isinstance(message, dict) or message.get('role') != 'user':
            continue
        content = message.get('content')
        blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get('type') != 'text':
                continue
            text = block.get('text', '')
            # Claude Code 2.1.310 teammate mail may end with a trailing newline
            # (observed 2026-11-07 after --continue); only surrounding
            # whitespace is ignored, the wrapper shape stays exact.
            text = text.strip() if isinstance(text, str) else text
            prefix = 'The coordinator sent a message while you were working:\n'
            suffix = '\n\nAddress this before completing your current task.'
            if isinstance(text, str) and text.startswith(prefix) and text.endswith(suffix.strip()):
                text = text[len(prefix):-len(suffix.strip())].strip()
            # Plugin-spawned teammates wrap the exact JSON in a verified
            # <teammate-message> envelope. Parse only that whole wrapper shape;
            # arbitrary quoted tags inside external text never qualify.
            if isinstance(text, str) and text.startswith('<teammate-message ') and text.endswith('\n</teammate-message>'):
                first = text.find('\n')
                if first > 0 and 'teammate_id="team-lead"' in text[:first]:
                    text = text[first + 1:-len('\n</teammate-message>')]
            try:
                value = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and value.get('side_context_key'):
                found = value
    return found


def upstream_counter(services, handler, provider, config, model):
    """Call the provider count endpoint, never the router's estimated count API."""
    resolved = services.routing.resolve_model(provider, config, model)
    if services.routing.select_protocol(provider, config, 'anthropic_messages', resolved) != 'anthropic_messages':
        raise SideContextUnavailable('recent token selection requires a provider Messages token counter')
    transport = services.transport
    endpoint = transport.provider_endpoint(provider, config, 'anthropic_messages')
    url = urllib.parse.urlsplit(endpoint)
    count_url = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path.rstrip('/') + '/count_tokens', url.query, ''))
    headers = transport.provider_headers(provider, config, handler.headers, 'anthropic_messages')
    def count(body):
        request = {**body, 'model': resolved}
        response = transport.open_request(count_url, request, headers, 30.0, provider, config, resolved, stream=False)
        try:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise SideContextUnavailable('provider token count response exceeds limit')
            value = json.loads(raw)
        finally:
            response.close()
        tokens = value.get('input_tokens') if isinstance(value, dict) else None
        if type(tokens) is not int or tokens <= 0:
            raise SideContextUnavailable('provider did not return input_tokens')
        return tokens
    return count


MAX_CACHE_MARKS = 4


def _marked_blocks(items):
    """Every block carrying cache_control, including blocks nested in tool results."""
    for block in items if isinstance(items, list) else []:
        if not isinstance(block, dict):
            continue
        if 'cache_control' in block:
            yield block
        yield from _marked_blocks(block.get('content'))


def _request_marks(request):
    marks = list(_marked_blocks(request.get('system'))) + list(_marked_blocks(request.get('tools')))
    for message in request.get('messages') or []:
        if isinstance(message, dict):
            marks += list(_marked_blocks(message.get('content')))
    return marks


def _fit_parent_cache_marks(request, parent_count):
    """Keep the assembled request within the API's four cache breakpoints.

    The parent boundary is marked only when a breakpoint is free. When parent and
    child already carry more than four, the parent messages' own marks go first,
    oldest first; system, tools and the child's marks are left as sent.
    """
    total = len(_request_marks(request))
    parents = request['messages'][:parent_count]
    if total < MAX_CACHE_MARKS:
        _mark_parent_cache_boundary(parents, 0)
        return
    for message in parents:
        for block in list(_marked_blocks(message.get('content'))):
            if total <= MAX_CACHE_MARKS:
                return
            block.pop('cache_control', None)
            total -= 1


def _mark_parent_cache_boundary(messages, existing=0):
    """Mark the last parent text block without exceeding four request breakpoints."""
    if existing >= MAX_CACHE_MARKS:
        return
    for message in reversed(messages):
        content = message.get('content')
        if isinstance(content, str) and content:
            message['content'] = [{'type': 'text', 'text': content, 'cache_control': {'type': 'ephemeral'}}]
            return
        if not isinstance(content, list):
            continue
        for index in range(len(content) - 1, -1, -1):
            block = content[index]
            if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
                content[index] = {**block, 'cache_control': {'type': 'ephemeral'}}
                return


def project(state_dir, identity, agent_id, body, config, provider, count_tokens):
    envelope = request_context(body)
    if envelope is None:
        return body
    settings = config.get('work_side_intake', {})
    effective_mode = settings.get('context_mode', 'full')
    if settings.get('enabled') is not True:
        raise SideContextUnavailable('side parent-context profile is disabled')
    key = envelope.get('side_context_key')
    if not isinstance(key, str) or not _HANDLE.fullmatch(key) or not isinstance(agent_id, str) or not agent_id:
        raise SideContextUnavailable('native side agent identity and host context handle required')
    store = SideContextStore(state_dir, identity)
    path = store.path(key)
    # Do not hold a Work transaction while contacting the provider's counter.
    with exclusive_file_lock(path):
        value = store.read(path)
        if not value or value.get('provider') != provider or value.get('identity') != identity:
            raise SideContextUnavailable('side context source does not match this request')
        if value.get('epoch') and not bound_session(identity):
            raise SideContextUnavailable('parent conversation is not the bound main session')
        parent_tools = value['parent'].get('tools', [])
        if any(tool.get('type') not in (None, 'custom') for tool in parent_tools):
            # Provider-hosted tools execute outside the native child's permission
            # boundary. Refuse the profile rather than expand its capabilities.
            raise SideContextUnavailable('side context cannot expose provider-hosted execution tools')
        parent_names = {tool.get('name') for tool in parent_tools}
        if any(tool.get('name') not in parent_names for tool in body.get('tools', [])):
            raise SideContextUnavailable('side tools were not present in captured parent; recapture parent first')
        profile = value['profile']
        shadow = profile.get('shadow') is True
        if shadow and settings.get('comparison_enabled') is not True:
            raise SideContextUnavailable('side comparison was disabled')
        if not shadow and (profile['mode'] != effective_mode or profile['percent'] != settings.get('context_percent', 30)):
            raise SideContextUnavailable('context profile changed while side turn was active')
        if value.get('agent_id') not in (None, agent_id):
            raise SideContextUnavailable('side context handle belongs to another native agent')
        if value['receipt_id'] != envelope.get('event_id'):
            raise SideContextUnavailable('side context does not match current event')
        if shadow:
            receipt = SideIntakeInbox(state_dir, identity)._load(value['receipt_id'])
            if not receipt or receipt.get('stage') != 'processed':
                raise SideContextUnavailable('comparison primary decision missing')
        else:
            verify_claim(SideIntakeInbox(state_dir, identity), value['receipt_id'], value['generation'])
        if value.get('projection') is None:
            selected, info = select(value['parent'], profile['mode'], profile['percent'], profile['model'], count_tokens)
            value['projection'] = selected
            value['selection'] = info
        # Token counting may take time. Recheck fencing before exposing data.
        if shadow:
            receipt = SideIntakeInbox(state_dir, identity)._load(value['receipt_id'])
            if not receipt or receipt.get('stage') != 'processed':
                raise SideContextUnavailable('comparison primary decision missing')
        else:
            verify_claim(SideIntakeInbox(state_dir, identity), value['receipt_id'], value['generation'])
        value['agent_id'] = agent_id
        save(path, value)
        selected = copy.deepcopy(value['projection'])
        parent = value['parent']
    result = copy.deepcopy(body)
    envelope = request_context(result)
    if envelope is not None:
        # The native child transcript keeps the readable preview; the model gets
        # one authoritative parent prefix plus the exact durable external event.
        exact = SideIntakeInbox(state_dir, identity)._load(value['receipt_id'])
        clean = {k: v for k, v in envelope.items() if k != 'parent_context'}
        clean['original'] = exact['text'] if exact else ''
        for message in result.get('messages', []):
            content = message.get('content') if isinstance(message, dict) else None
            if (isinstance(content, str) and envelope.get('side_context_key') in content
                    and envelope.get('event_id') in content):
                message['content'] = json.dumps(clean, ensure_ascii=False)
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    text = block.get('text', '')
                    if (isinstance(text, str) and envelope.get('side_context_key') in text
                            and envelope.get('event_id') in text):
                        block['text'] = json.dumps(clean, ensure_ascii=False)
    result['system'] = copy.deepcopy(parent.get('system', []))
    if shadow:
        # Keep custom schema prefix but force no execution. Native shadow
        # AgentSpec also withholds every local and MCP runtime tool.
        result['tools'] = copy.deepcopy(parent.get('tools', []))
        result['tool_choice'] = {'type': 'none'}
    else:
        # The model sees only the tools its AgentSpec can run. Showing parent
        # shell/edit/runtime schemas made side actors attempt them (Prody
        # 2026-11-08). Parent bytes and order are kept for that subset.
        offered = {tool.get('name') for tool in body.get('tools', [])}
        result['tools'] = [copy.deepcopy(tool) for tool in parent.get('tools', []) if tool.get('name') in offered]
    result['messages'] = selected + result.get('messages', [])
    # The full selected parent prefix is identical for consecutive side events
    # on the same model: mark its last text block, never the event. Counted on
    # the assembled request: Claude Code also marks the child's own messages
    # (Prody 2026-11-09, 30b0d70: "A maximum of 4 blocks with cache_control
    # may be provided. Found 5." on anthropic/claude-haiku-5-5-max).
    _fit_parent_cache_marks(result, len(selected))
    return result
