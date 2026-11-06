"""Router-side parent prefix projection; native child still executes its tools."""
import copy
import json
import re
import urllib.parse

from .channel_message_repository import exclusive_file_lock
from .durable_json_file import save
from .side_context_projection import SideContextUnavailable, select
from .side_context_store import SideContextStore
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
            prefix = 'The coordinator sent a message while you were working:\n'
            suffix = '\n\nAddress this before completing your current task.'
            if isinstance(text, str) and text.startswith(prefix) and text.endswith(suffix):
                text = text[len(prefix):-len(suffix)]
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


def project(state_dir, identity, agent_id, body, config, provider, count_tokens):
    envelope = request_context(body)
    if envelope is None:
        return body
    settings = config.get('work_side_intake', {})
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
        if value.get('epoch'):
            from .runtime_paths import ROUTER_INSTANCE_DIR
            current = store.read(ROUTER_INSTANCE_DIR / 'compaction-scope.json')
            if not current or current.get('epoch') != value['epoch'] or 'claude:' + str(current.get('session_id')) != identity:
                raise SideContextUnavailable('parent request belongs to an earlier launch epoch')
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
        if not shadow and (profile['mode'] != settings.get('context_mode') or profile['percent'] != settings.get('context_percent', 30)):
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
    result['system'] = copy.deepcopy(parent.get('system', []))
    # Native execution permissions still decide which inherited schemas can run.
    # Preserve parent schema bytes and order to keep its prefix stable.
    result['tools'] = copy.deepcopy(parent.get('tools', []))
    result['messages'] = selected + result.get('messages', [])
    if shadow:
        # Keep custom schema prefix but force no execution. Native shadow
        # AgentSpec also withholds every local and MCP runtime tool.
        result['tool_choice'] = {'type': 'none'}
    return result
