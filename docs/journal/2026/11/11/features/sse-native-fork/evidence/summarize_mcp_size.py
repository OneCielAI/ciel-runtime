"""Summarize owned MCP size probe runs into one evidence table (no payload text)."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
summary = []
for run in sys.argv[1:]:
    result = json.loads((ROOT / run / 'result.json').read_text(encoding='utf-8'))
    expected = {name.removeprefix('server-out-'): row for name, row in result['server_out'].items()}
    for row in result['hook_rows']:
        if row.get('event') != 'call':
            continue
        server = expected.get(row['tool'] + '-' + str(row['size'])) or {}
        summary.append({
            'run': run, 'variant': result['variant'], 'tool': row['tool'], 'requested_chars': row['size'],
            'server_chars': server.get('chars'), 'isError': row.get('isError'), 'received_chars': row.get('chars'),
            'full_text_received': bool(server) and row.get('sha256') == server.get('sha256'),
            'json_parseable': row.get('parsed'), 'stub': (row.get('head') or '').startswith('Error: result ('),
            'stub_names_saved_file': bool(row.get('saved_path')), 'plugin_fs_read_saved': row.get('saved_read'),
            'threw': row.get('threw')})
out = ROOT / 'mcp-size-summary.json'
out.write_text(json.dumps(summary, indent=2), encoding='utf-8')
for row in summary:
    print(row['variant'], row['tool'], row['requested_chars'], 'full' if row['full_text_received'] else
          ('stub' if row['stub'] else 'other'), row['received_chars'], row['plugin_fs_read_saved'] or '', row['threw'] or '')
