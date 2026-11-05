"""Independent first-screen Work and side-intake selection panels."""
from .work_live_settings import settings
from .recent_menu_text import menu_text


BOOLEANS = {'work_mode', 'work_plan_approval_ask', 'work_auto_continue',
            'work_completed_auto_delete', 'work_parallel_enabled', 'work_parallel_paused',
            'work_discovery_enabled', 'work_discovery_test_default_override', 'work_side_intake_enabled'}
NUMBERS = {
    'work_parallel_max_active': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_p1_reserved': (0, 1, 2, 3, 4, 5),
    'work_parallel_launches_per_minute': (1, 2, 4, 8, 16, 30, 60),
    'work_parallel_max_rework': (0, 1, 2, 3, 5, 10),
}


def panel_rows(config, *, side=False):
    state = settings(config, {}, None)
    selected = [r for r in state['settings'] if r['key'].startswith('work_side_intake_') == side]
    return ([r['label'] + ': ' + str(r['value']) for r in selected] + [menu_text('state_back', config.get('language', 'en'))],
            [r['key'] for r in selected] + ['back'])


def choices(config, key):
    language = config.get('language', 'en')
    if key in BOOLEANS:
        values = ('on', 'off')
    elif key == 'work_side_intake_type':
        values = ('session', 'btw_with_mcp')
    elif key in NUMBERS:
        values = tuple(str(n) for n in NUMBERS[key])
    else:
        # File paths and instructions are inherently text; enter only after
        # explicitly choosing Edit, never as a toggle/enum interaction.
        return ([menu_text('settings_edit_text', language), menu_text('state_back', language)], ['__edit__', 'back'])
    return ([menu_text('state_' + v, language) if v in ('on', 'off') else v for v in values]
            + [menu_text('state_back', language)], list(values) + ['back'])


def apply(config, key, value, save):
    return settings(config, {'key': key, 'value': value}, save)
