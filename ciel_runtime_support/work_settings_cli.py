"""Work configuration CLI using the same settings projection as the menus."""
import json

from .work_settings_menu import apply, description, panel_rows
from .work_live_settings import settings


def command(load_config, save_config, output=print):
    def run(args):
        config = load_config()
        action = args.action
        key = args.key
        if action == 'set':
            if not key or args.value is None:
                raise ValueError('use work set KEY VALUE')
            apply(config, key, args.value, save_config)
        state = settings(config, {}, None)
        _, keys = panel_rows(config)
        values = {row['key']: row for row in state['settings']}
        values['sse_injection_mode'] = {
            'key': 'sse_injection_mode',
            'value': 'btw_with_mcp' if config.get('work_side_intake', {}).get('enabled') is True
            and config.get('work_side_intake', {}).get('type', 'btw_with_mcp') == 'btw_with_mcp' else 'session',
        }
        # Include compatibility keys as well as every visible settings key.
        keys = list(dict.fromkeys([k for k in keys if k not in ('back', '__info__')] + list(values)))
        if key:
            if key not in values:
                raise ValueError('unknown Work setting: ' + key)
            keys = [key]
        elif action in ('get', 'set'):
            raise ValueError('Work setting key required')
        result = {'settings': [{**values[k], 'description': description(config, k)} for k in keys if k in values],
                  'applies': state['applies']}
        if args.json:
            output(json.dumps(result, ensure_ascii=False))
        else:
            for row in result['settings']:
                output(row['key'] + ' = ' + str(row['value']) + (' ['+row['status']+']' if row.get('status') else ''))
                output('  ' + row['description'])
            output(result['applies'])
        return result
    return run
