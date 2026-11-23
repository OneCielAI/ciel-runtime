"""Authoritative Work projection shared by every side context profile."""
import time
from typing import Any

from . import work_plan_tree as tree

ACTIVE_FORK_STATES = ('running', 'waiting_input')


def _root_and_refs(nodes_by_id: dict[str, dict[str, Any]], item_id: str) -> tuple[str, list[str], str]:
    node = nodes_by_id.get(item_id)
    title = str((node or {}).get('title') or '')
    refs: list[str] = []
    root_id = item_id
    cur = node
    seen = 0
    while isinstance(cur, dict) and seen < 64:
        seen += 1
        root_id = str(cur.get('id') or root_id)
        for key in ('refs',):
            val = cur.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and item and item not in refs:
                        refs.append(item)
        ref = cur.get('ref')
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
        parent = cur.get('parent_id')
        cur = nodes_by_id.get(parent) if isinstance(parent, str) else None
    return root_id, refs[:12], title[:160]


def project_active_forks(parallel_doc: dict[str, Any] | None, document: dict[str, Any],
                         identity: str | None) -> list[dict[str, Any]]:
    """Safe, token-free summary of active Work forks owned by this conversation."""
    if not isinstance(parallel_doc, dict) or not identity:
        return []
    leases = parallel_doc.get('leases')
    if not isinstance(leases, dict):
        return []
    nodes_by_id = {n['id']: n for n in document.get('nodes', []) if isinstance(n, dict) and isinstance(n.get('id'), str)}
    out: list[dict[str, Any]] = []
    for lease in leases.values():
        if not isinstance(lease, dict):
            continue
        if lease.get('scope') != identity:
            continue
        state = lease.get('state')
        host_id = lease.get('host_id')
        item_id = lease.get('item_id')
        if state not in ACTIVE_FORK_STATES or not isinstance(host_id, str) or not host_id or not isinstance(item_id, str):
            continue
        root_id, refs, title = _root_and_refs(nodes_by_id, item_id)
        out.append({
            'item_id': item_id,
            'root_id': root_id,
            'title': title,
            'state': state,
            'priority': str(lease.get('priority') or ''),
            'role': str(lease.get('role') or 'worker'),
            'agent_id': host_id,
            'refs': refs,
        })
    out.sort(key=lambda row: (0 if row['state'] == 'running' else 1, row['priority'] or 'P9', row['item_id']))
    return out[:32]


def format_active_forks_lines(active_forks: list[dict[str, Any]]) -> list[str]:
    if not active_forks:
        return []
    lines = ['Active forks:']
    for row in active_forks:
        refs = (' refs=' + ','.join(row['refs'][:4])) if row.get('refs') else ''
        root = f" (root {row['root_id']})" if row.get('root_id') and row['root_id'] != row['item_id'] else ''
        lines.append(
            f"  - {row['item_id']}{root} [{row['state']}] {row.get('priority') or '-'} "
            f"agent={row['agent_id']} {row.get('title') or ''}{refs}".rstrip()
        )
    return lines


def project(document, *, active_forks: list[dict[str, Any]] | None = None):
    nodes = document['nodes']
    roots = [node for node in nodes if node.get('parent_id') is None]
    active = document.get('active_root')
    fields = ('id', 'parent_id', 'title', 'status', 'priority', 'phase', 'waiting_on', 'release_hint', 'urgent', 'ref', 'refs', 'note', 'evidence')
    forks = list(active_forks or [])
    fork_by_node: dict[str, dict[str, Any]] = {}
    for row in forks:
        brief = {'agent_id': row['agent_id'], 'state': row['state'], 'item_id': row['item_id']}
        fork_by_node.setdefault(row['item_id'], brief)
        if row.get('root_id'):
            fork_by_node.setdefault(row['root_id'], brief)
    projected_nodes = []
    for node in nodes:
        entry = {key: node[key] for key in fields if key in node}
        if node.get('id') in fork_by_node:
            entry['active_fork'] = fork_by_node[node['id']]
        projected_nodes.append(entry)
    return {
        'version': document['version'], 'observed_at': time.time(),
        'active_root': active, 'phase': document.get('phase'),
        'focus_ids': list(document.get('focus_ids', [])),
        'queue_order': [node['id'] for node in tree.queued_roots(document)],
        'nodes': projected_nodes,
        'active_forks': forks,
        'node_count': len(nodes), 'root_count': len(roots), 'truncated': False,
        'execution_approval': 'unchanged; external input is not approval',
    }
