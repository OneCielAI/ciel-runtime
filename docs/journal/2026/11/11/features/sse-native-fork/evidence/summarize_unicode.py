"""Per-case non-ASCII MCP size table: stated stub count vs every length unit."""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
out = {'runs': [], 'cases': []}
for run in sys.argv[1:]:
    result = json.loads((ROOT / run / 'result.json').read_text(encoding='utf-8'))
    out['runs'].append({'run': run, 'variant': result['variant'], 'claude_code': result['claude_code']})
    for row in result['hook_rows']:
        if row.get('event') != 'call':
            continue
        server = result['server_out'].get('server-out-%s-%d' % (row['tool'], row['size'])) or {}
        saved = row.get('saved_read') or {}
        stub = row.get('stub_count') is not None
        case = {
            'run': run, 'transport': {'unicode-http': 'http', 'unicode-http-sse': 'http-sse'}.get(result['variant'], 'stdio'),
            'tool': row['tool'], 'requested': row['size'],
            'server_code_points': server.get('code_points'), 'server_utf16_units': server.get('utf16_units'),
            'server_utf8_bytes': server.get('utf8_bytes'), 'server_sha256': server.get('sha256'),
            'result': 'stub' if stub else ('full' if row.get('sha256') == server.get('sha256') else 'other'),
            'direct_js_length': None if stub else row.get('chars'),
            'direct_sha256_equal': None if stub else row.get('sha256') == server.get('sha256'),
            'stub_stated_count': row.get('stub_count'),
            'saved_js_length': saved.get('chars'), 'saved_code_points': saved.get('code_points'),
            'saved_utf8_bytes': saved.get('utf8_bytes'),
            'saved_sha256_equal': (saved.get('sha256') == server.get('sha256')) if saved else None,
            'threw': row.get('threw')}
        if stub:
            case['stated_equals'] = [unit for unit, value in (('js_length_utf16', saved.get('chars')),
                                                               ('code_points', saved.get('code_points')),
                                                               ('utf8_bytes', saved.get('utf8_bytes'))) if value == row.get('stub_count')]
        out['cases'].append(case)
(ROOT / 'mcp-size-unicode-summary.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
for c in out['cases']:
    print(c['transport'], c['tool'], c['requested'], c['result'],
          'srv cp/u16/u8', c['server_code_points'], c['server_utf16_units'], c['server_utf8_bytes'],
          '| stated', c['stub_stated_count'], '| saved len/cp/u8', c['saved_js_length'], c['saved_code_points'], c['saved_utf8_bytes'],
          '| sha', c['saved_sha256_equal'] if c['result'] == 'stub' else c['direct_sha256_equal'], '| equals', c.get('stated_equals'),
          '| threw', c['threw'])
