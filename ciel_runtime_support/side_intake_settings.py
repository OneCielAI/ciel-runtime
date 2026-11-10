"""Canonical controls for independent side processing."""
import hashlib
import json

FIELDS = {
    'max_active': (2, None),
    'aging_seconds': (60, None),
    'context_mode': ('full', ('isolated', 'full', 'recent')),
    'context_percent': (30, None),
    'model': ('inherit', None),
    'reply_connectors': ([], None),
    'priority_policy': ('adaptive', ('adaptive', 'explicit')),
    'instruction': ('', None),
}
# Removed 2026-11-10 with the comparison experiment; ignored if still persisted.
RETIRED = ('comparison_enabled', 'comparison_model')
PREFIX = 'work_side_intake_'


def runtime_settings(config):
    """Project persisted catalog IDs to Claude-facing routed aliases."""
    stored = {key: value for key, value in (config.get('work_side_intake') or {}).items() if key not in RETIRED}
    provider = str(config.get('current_provider') or '')
    from .provider_adapters import PROVIDER_ADAPTERS, PROVIDER_ALIASES, PROVIDER_LABELS
    from .provider_model_identity import ProviderModelIdentityService
    models = ProviderModelIdentityService(PROVIDER_ADAPTERS, PROVIDER_ALIASES, PROVIDER_LABELS)
    raw = str(stored.get('model') or 'inherit')
    stored['model_catalog'] = raw
    stored['model'] = raw if raw == 'inherit' else models.alias_for(provider, models.normalize_model_id(provider, raw))
    stored.setdefault('context_mode', 'full')
    stored['model_fingerprint'] = hashlib.sha256(json.dumps({
        'provider': provider, 'model': stored['model']
    }, sort_keys=True).encode()).hexdigest()[:32]
    return stored


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
    elif name in ('context_percent', 'max_active', 'aging_seconds'):
        upper = {'max_active':16, 'context_percent':100, 'aging_seconds':3600}[name]
        try:
            parsed = int(value)
        except (ValueError, TypeError):
            raise ValueError(f'{name} must be an integer from 1 to {upper}') from None
        if not 1 <= parsed <= upper:
            raise ValueError(f'{name} must be an integer from 1 to {upper}')
    elif name == 'model':
        parsed = value.strip()
        if not parsed or len(parsed) > 300 or any(ord(c) < 32 for c in parsed):
            raise ValueError('use inherit or a catalog model id')
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
