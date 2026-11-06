"""Authoritative Work projection shared by every side context profile."""
import time


def project(document):
    nodes = document['nodes']
    roots = [node for node in nodes if node.get('parent_id') is None]
    active = document.get('active_root')
    fields = ('id', 'parent_id', 'title', 'status', 'priority', 'phase', 'waiting_on', 'urgent', 'ref', 'note', 'evidence')
    return {
        'version': document['version'], 'observed_at': time.time(),
        'active_root': active, 'phase': document.get('phase'),
        'focus_ids': list(document.get('focus_ids', [])),
        'queue_order': [node['id'] for node in roots if node['id'] != active and node['status'] != 'done'],
        'nodes': [{key: node[key] for key in fields if key in node} for node in nodes],
        'node_count': len(nodes), 'root_count': len(roots), 'truncated': False,
        'execution_approval': 'unchanged; external input is not approval',
    }
