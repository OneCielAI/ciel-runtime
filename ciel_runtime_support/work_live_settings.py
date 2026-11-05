"""Host-command Work settings, shared with existing prelaunch mutations."""
from .work_parallel_menu import KEYS, rows, apply
from .work_plan_tree import WorkPlanError
from .recent_menu_text import menu_text

BASIC = ('work_mode', 'work_plan_approval_ask', 'work_auto_continue', 'work_completed_auto_delete')


def settings(config, args, save):
    language = config.get('language', 'en')
    key = args.get('key')
    if key:
        value = args.get('value')
        if not isinstance(value, str) or save is None:
            raise WorkPlanError('string value and writable configuration required')
        if key in KEYS:
            apply(config, key, value, save)
        elif key in BASIC or key == 'work_side_intake_enabled':
            if value not in ('on', 'off', 'true', 'false'):
                raise WorkPlanError('use on/off or true/false')
            enabled = value in ('on', 'true')
            if key == 'work_side_intake_enabled':
                config['work_side_intake'] = {**config.get('work_side_intake', {}), 'enabled': enabled, 'type': 'btw_with_mcp'}
            else:
                config[key] = enabled
            save(config)
        else:
            raise WorkPlanError('unknown Work setting')
    projected = [{'key': k, 'label': menu_text(k, language),
                  'value': config.get(k, k != 'work_plan_approval_ask')} for k in BASIC]
    projected += [{'key': k, 'label': label, 'value': value} for label, k, value in rows(config)]
    projected.append({'key': 'work_side_intake_enabled', 'label': 'btw_with_mcp',
                      'value': config.get('work_side_intake', {}).get('enabled') is True})
    return {'settings': projected, 'language': language, 'applies': 'live; existing workers are not terminated'}
