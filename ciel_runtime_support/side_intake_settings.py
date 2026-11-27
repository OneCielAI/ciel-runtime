"""Canonical controls for independent side processing."""

from .work_texts import TEXTS

DEFAULT_SIDE_INSTRUCTION = TEXTS['mod_side_default_instruction']


FIELDS = {
    'max_active': (2, None),
    'aging_seconds': (60, None),
    'reply_connectors': ([], None),
    'priority_policy': ('adaptive', ('adaptive', 'explicit')),
    'instruction': ('', None),
    # How a side actor starts (user 2026-11-20): "fork" inherits main's whole
    # conversation; "fresh" is an empty conversation with main's own system
    # prompt and MCP tools, for simple events that need no main context.
    'session': ('fork', ('fork', 'fresh')),
}
# Native forks always inherit main. Historical overrides remain harmless on disk
# but are never projected to runtime or exposed by active settings UI.
RETIRED = ('comparison_enabled', 'comparison_model', 'model', 'context_mode', 'context_percent')
PREFIX = 'work_side_intake_'
# Inclusive integer ranges; the menu, ctl and REST share this one definition.
LIMITS = {'max_active': (1, 16), 'aging_seconds': (1, 3600)}


def runtime_settings(config):
    """Side settings the host receives.

    ``prompt`` is the whole side instruction when a user saved one (it replaces
    the built-in default); ``instruction`` is only a legacy appended suffix.
    """
    stored = config.get('work_side_intake') or {}
    out = {key: value for key, value in stored.items() if key not in RETIRED and key != 'instruction_replace'}
    if stored.get('instruction_replace') is True and str(stored.get('instruction') or '').strip():
        out['prompt'] = str(out.pop('instruction')).strip()
    return out


def values(config):
    stored = config.get('work_side_intake', {})
    rows = {PREFIX + name: stored.get(name, default) for name, (default, _) in FIELDS.items()}
    # The setting value is what the user stored: the whole instruction (it
    # replaces the built-in default) or, from before 2026-11-19, a suffix.
    rows[PREFIX + 'instruction'] = str(stored.get('instruction') or '')
    return rows


def instruction_view(config):
    side = config.get('work_side_intake') or {}
    text = str(side.get('instruction') or '').strip()
    if text and side.get('instruction_replace') is True:
        effective = text
    else:
        effective = DEFAULT_SIDE_INSTRUCTION + (('\n\n## 운영자 지침\n\n' + text) if text else '')
    return {'stored': text, 'effective': effective, 'using_default': not bool(text), 'format': 'markdown',
            'replaces_default': bool(text) and side.get('instruction_replace') is True}


def set_value(config, key, value):
    name = key.removeprefix(PREFIX)
    if key != PREFIX + name or name not in FIELDS:
        raise ValueError('unknown side setting')
    if name == 'reply_connectors':
        import json
        from .side_reply_connector import validate
        try:
            parsed = validate(json.loads(value))
        except (ValueError, TypeError) as error:
            raise ValueError('invalid side reply connectors: ' + str(error)) from None
    elif name in LIMITS:
        from .work_setting_values import integer_in
        low, high = LIMITS[name]
        parsed = integer_in(value, low, high, name)
    elif name == 'instruction':
        # The saved text is the whole side instruction (user 2026-11-18: users
        # edit it directly). Saving the default text, or nothing, stores nothing.
        parsed = value.replace('\r\n', '\n').strip()
        if len(parsed) > 32000 or '\x00' in parsed:
            raise ValueError('side instruction must be at most 32000 characters')
        if parsed == DEFAULT_SIDE_INSTRUCTION.strip():
            parsed = ''
    else:
        allowed = FIELDS[name][1]
        if value not in allowed:
            raise ValueError('use ' + ' or '.join(allowed))
        parsed = value
    side = {**config.get('work_side_intake', {})}
    if name == 'instruction':
        side.pop('instruction', None)
        side.pop('instruction_replace', None)
        if parsed:
            side['instruction'] = parsed
            side['instruction_replace'] = True
    else:
        side[name] = parsed
    config['work_side_intake'] = side


__all__ = ['DEFAULT_SIDE_INSTRUCTION', 'FIELDS', 'LIMITS', 'PREFIX', 'RETIRED', 'instruction_view', 'runtime_settings', 'set_value', 'values']
