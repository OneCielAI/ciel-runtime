"""Canonical controls for independent side processing."""

FIELDS = {
    'max_active': (2, None),
    'aging_seconds': (60, None),
    'reply_connectors': ([], None),
    'priority_policy': ('adaptive', ('adaptive', 'explicit')),
    'instruction': ('', None),
}
# Native forks always inherit main. Historical overrides remain harmless on disk
# but are never projected to runtime or exposed by active settings UI.
RETIRED = ('comparison_enabled', 'comparison_model', 'model', 'context_mode', 'context_percent')
PREFIX = 'work_side_intake_'
# Inclusive integer ranges; the menu, ctl and REST share this one definition.
LIMITS = {'max_active': (1, 16), 'aging_seconds': (1, 3600)}


def runtime_settings(config):
    """Return active controls; old model/context keys are intentionally ignored."""
    return {key: value for key, value in (config.get('work_side_intake') or {}).items()
            if key not in RETIRED}


def values(config):
    stored = config.get('work_side_intake', {})
    return {PREFIX + name: stored.get(name, default) for name, (default, _) in FIELDS.items()}


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
        parsed = value.strip()
        if len(parsed) > 16000 or '\x00' in parsed:
            raise ValueError('side instruction must be at most 16000 characters')
    else:
        allowed = FIELDS[name][1]
        if value not in allowed:
            raise ValueError('use ' + ' or '.join(allowed))
        parsed = value
    config['work_side_intake'] = {**config.get('work_side_intake', {}), name: parsed}
