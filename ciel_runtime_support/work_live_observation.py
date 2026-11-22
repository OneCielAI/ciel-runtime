"""Read-only Work observations, without interpreting router health as session health."""

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def probe(port, identity, *, open_url=urlopen, timeout=4):
    """Sample explicit loopback Work routes; never replay a mutation."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('invalid router port')
    base = f'http://127.0.0.1:{port}/ca/'

    def request(req):
        with open_url(req, timeout=timeout) as response:
            raw = response.read(8_388_609)
        if len(raw) > 8_388_608:
            raise ValueError('observation response exceeds byte limit')
        return json.loads(raw)

    def read(name):
        return request(Request(base + 'work/api/v1/' + name + '?' + urlencode({'session_id': identity})))

    result = observe(identity, read)
    if not identity:
        return result
    version, method, tool = '2026-07-28', 'tools/call', 'work_status'
    params = {'name': tool, 'arguments': {'format': 'json', 'session_id': identity},
              '_meta': {'io.modelcontextprotocol/protocolVersion': version,
                        'io.modelcontextprotocol/clientCapabilities': {}}}
    started = time.monotonic()
    try:
        req = Request(base + 'mcp', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                              'params': params}).encode(),
                      {'Content-Type': 'application/json', 'Accept': 'application/json',
                       'MCP-Protocol-Version': version, 'Mcp-Method': method, 'Mcp-Name': tool})
        reply = request(req)
        rpc = reply['result']
        if reply.get('id') != 1 or rpc.get('isError') is True:
            raise ValueError('MCP read refused')
        payload = json.loads(rpc['content'][0]['text'])
        if type(payload.get('version')) is not int or type(payload.get('enabled')) is not bool:
            raise ValueError('invalid Work snapshot')
        result['mcp'] = {'state': 'probe_succeeded', 'version': payload['version'],
                         'elapsed_ms': round((time.monotonic() - started) * 1000)}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
        result['mcp'] = {'state': 'probe_failed', 'error': type(error).__name__,
                         'elapsed_ms': round((time.monotonic() - started) * 1000)}
    return result


def fork_errors(projects, identity, since, *, max_entries=2048, max_forks=24, tail_bytes=65536):
    """Bounded sample of owned-session fork transcripts, without printing payloads."""
    if not isinstance(identity, str) or not re.fullmatch(r'claude:[A-Za-z0-9_-]{1,200}', identity):
        return {'state': 'unobserved'}
    directory = Path(projects) / identity.split(':', 1)[1] / 'subagents'
    sources, partial, unreadable = [], False, 0
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index == max_entries:
                    partial = True
                    break
                match = re.fullmatch(r'agent-([A-Za-z0-9_-]{1,200})\.jsonl', entry.name)
                if not match or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    sources.append((entry.stat(follow_symlinks=False).st_mtime_ns, match[1], Path(entry.path)))
                except OSError:
                    unreadable += 1
    except OSError as error:
        return {'state': 'unobserved', 'error': type(error).__name__}
    errors, scanned = [], 0
    for _, agent, path in sorted(sources, reverse=True)[:max_forks]:
        try:
            meta = path.with_suffix('.meta.json')
            if meta.is_symlink():
                unreadable += 1
                continue
            with meta.open('rb') as stream:
                if json.loads(stream.read(32768)).get('isFork') is not True:
                    continue
            with path.open('rb') as stream:
                end = stream.seek(0, 2)
                offset = max(0, end - tail_bytes)
                stream.seek(offset)
                raw = stream.read(tail_bytes)
                partial |= bool(offset)
            lines = raw.split(b'\n')[:-1]
            if offset:
                lines = lines[1:]
            scanned += 1
        except (OSError, ValueError, AttributeError):
            unreadable += 1
            continue
        for line in lines:
            try:
                row = json.loads(line)
                stamp = row.get('timestamp')
                message = row.get('message')
                blocks = message.get('content') if isinstance(message, dict) else None
                if (not isinstance(stamp, str) or not re.fullmatch(r'[0-9T:.Z+-]{19,40}', stamp)
                        or stamp[:19] < since[:19] or not isinstance(blocks, list)):
                    continue
                total = sum(isinstance(b, dict) and b.get('type') == 'tool_result'
                            and b.get('is_error') is True for b in blocks)
                if total:
                    errors.append({'at': stamp, 'agent': agent, 'count': total})
            except (ValueError, AttributeError, RecursionError):
                partial = True
    return {'state': 'partial' if partial or unreadable or len(sources) > max_forks else 'sampled',
            'scanned': scanned, 'unreadable': unreadable, 'error_records': len(errors),
            'recent_errors': sorted(errors, key=lambda row: row['at'])[-20:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--projects', type=Path)
    parser.add_argument('--since', help='UTC ISO timestamp for fork transcript errors')
    args = parser.parse_args()
    if bool(args.projects) != bool(args.since):
        parser.error('--projects and --since must be supplied together')
    result = probe(args.port, args.session_id)
    if args.projects:
        result['forks'] = fork_errors(args.projects, args.session_id, args.since)
    print(json.dumps(result, ensure_ascii=False))


def observe(identity, read):
    result = {'board': {}, 'workflow': {}, 'mcp': 'unobserved', 'terminal': 'unobserved'}
    for name in ('board', 'workflow'):
        target = result[name]
        if not identity:
            target['state'] = 'unobserved'
            continue
        try:
            row = read(name)
        except (OSError, ValueError, KeyError) as error:
            target.update(state='unavailable', error=type(error).__name__)
            continue
        if not isinstance(row, dict) or row.get('ok') is not True:
            target['state'] = 'unavailable'
            continue
        if row.get('session_id') != identity:
            target['state'] = 'different_session'
            continue
        if name == 'board':
            nodes, roots, version = row.get('nodes'), row.get('root_ids'), row.get('version')
            if (not isinstance(nodes, list) or not isinstance(roots, list) or type(version) is not int
                    or any(not isinstance(node, dict) or not isinstance(node.get('id'), str) for node in nodes)
                    or any(not isinstance(root, str) for root in roots)):
                target['state'] = 'unavailable'
                continue
            continuation = row.get('continuation')
            target.update(state='observed' if nodes else 'empty_view', version=version,
                          node_ids=[node['id'] for node in nodes], root_ids=roots,
                          actionable=continuation.get('actionable') if isinstance(continuation, dict) else None)
        else:
            settings, counts = row.get('settings'), row.get('counts')
            enabled = settings.get('enabled') if isinstance(settings, dict) else None
            if type(enabled) is not bool or not isinstance(counts, dict):
                target['state'] = 'unavailable'
                continue
            target.update(state='observed' if enabled else 'disabled', counts=counts)
    return result


if __name__ == '__main__':
    main()
