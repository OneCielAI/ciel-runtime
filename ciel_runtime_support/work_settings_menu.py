"""Independent first-screen Work and side-intake settings panels."""
from .work_live_settings import settings
from .recent_menu_text import menu_text


def panel_rows(config, *, side=False):
    state = settings(config, {}, None)
    selected = [r for r in state['settings'] if r['key'].startswith('work_side_intake_') == side]
    return ([r['label'] + ': ' + str(r['value']) for r in selected] + [menu_text('state_back', config.get('language', 'en'))],
            [r['key'] for r in selected] + ['back'])


def apply(config, key, value, save):
    return settings(config, {'key': key, 'value': value}, save)
