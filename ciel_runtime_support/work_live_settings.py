"""Host-command Work settings, shared with existing prelaunch mutations.

One path for every surface: the prelaunch menu, ``/work-mode settings``, the
web Settings tab (REST settings_update), the MCP work_live_settings tool and
``ciel-runtimectl work``. Keys are validated before anything is saved, and the
accepted words come from work_setting_values.
"""
from .work_parallel_menu import KEYS, UNREADABLE, apply, values as parallel_values
from .work_plan_tree import WorkPlanError
from .work_setting_values import (INJECTION_MODES, SettingValueError, injection_mode, injection_settings,
                                  switch)
from .recent_menu_text import menu_text

BASIC = ('work_mode', 'work_plan_approval_ask', 'work_auto_continue', 'work_completed_auto_delete')
INJECTION_KEY = 'sse_injection_mode'
SIDE_KEYS = ('work_side_intake_enabled', 'work_side_intake_type')
DISCOVERY_FILE_KEY = 'work_discovery_file_path'
PARALLEL_KINDS = {
    **{key: 'boolean' for key in ('work_parallel_enabled', 'work_parallel_paused', 'work_discovery_enabled',
                                  'work_discovery_test_default_override')},
    **{key: 'number' for key in (*(f'work_parallel_p{n}_limit' for n in range(1, 6)),
                                 'work_parallel_launches_per_minute', 'work_parallel_max_rework')},
}


def known_keys():
    """Every settable key, independent of whether the ledger is readable now."""
    from .claude_user_settings import KEYS as CLAUDE_KEYS
    from .side_intake_settings import PREFIX, FIELDS
    from .work_fork_models import KEYS as MODEL_KEYS
    from .work_gate_check import KEY as GATE_KEY
    return (*BASIC, INJECTION_KEY, *CLAUDE_KEYS, *KEYS, *MODEL_KEYS, DISCOVERY_FILE_KEY, *SIDE_KEYS, GATE_KEY,
            *(PREFIX + name for name in FIELDS))


def _basic(config, key):
    from .work_mode_launch import work_auto_continue_enabled, work_mode_enabled, work_plan_approval_ask
    from .work_retention import retention_enabled
    return {'work_mode': work_mode_enabled, 'work_plan_approval_ask': work_plan_approval_ask,
            'work_auto_continue': work_auto_continue_enabled,
            'work_completed_auto_delete': retention_enabled}[key](config)


def _set(config, key, value, save):
    from .side_intake_settings import values as side_values, set_value
    if key not in known_keys():
        raise WorkPlanError('unknown Work setting: ' + key)
    from .work_fork_models import KEYS as MODEL_KEYS, set_value as set_model
    try:
        if key in MODEL_KEYS:
            try:
                set_model(config, key, value)
            except ValueError as error:
                raise WorkPlanError(str(error)) from None
            save(config)
        elif key == 'work_gate_check_minutes':
            from .work_gate_check import set_minutes
            try:
                set_minutes(config, value)
            except ValueError as error:
                raise WorkPlanError(str(error)) from None
            save(config)
        elif key in side_values(config):
            set_value(config, key, value)
            save(config)
        elif key == INJECTION_KEY:
            config['work_side_intake'] = injection_settings(config, value)
            save(config)
        elif key == DISCOVERY_FILE_KEY:
            if len(value) > 4096 or '\x00' in value:
                raise WorkPlanError('invalid discovery instruction file path')
            discovery = dict(config.get('work_discovery', {}))
            if value.strip():
                discovery['file_path'] = value.strip()
            else:
                discovery.pop('file_path', None)
            config['work_discovery'] = discovery
            save(config)
        elif key == 'work_side_intake_type':
            if value not in INJECTION_MODES:
                raise WorkPlanError('use session or btw_with_mcp')
            config['work_side_intake'] = {**config.get('work_side_intake', {}), 'type': value}
            save(config)
        elif key in KEYS:
            apply(config, key, value, save)
        elif key.startswith('claude_'):
            from .claude_user_settings import set_value as set_claude_value
            set_claude_value(key, 'on' if switch(value, reset=False) else 'off')
        else:
            # BASIC or work_side_intake_enabled; a reset word restores the default.
            enabled = switch(value, approval=key == 'work_plan_approval_ask')
            if key == 'work_side_intake_enabled':
                side = dict(config.get('work_side_intake', {}))
                if enabled is None:
                    side.pop('enabled', None)
                else:
                    side['enabled'] = enabled
                side.setdefault('type', 'btw_with_mcp')
                config['work_side_intake'] = side
            elif enabled is None:
                config.pop(key, None)
            else:
                config[key] = enabled
            save(config)
    except SettingValueError as error:
        raise WorkPlanError(str(error)) from None


FINGERPRINT_FIELD = 'expected_settings_fingerprint'
# A runtime observation (owner liveness), not a stored setting: it would change
# the fingerprint without any settings write.
FINGERPRINT_EXCLUDED = ('work_parallel_orphans',)


class SettingsConflict(WorkPlanError):
    """The settings changed after the caller read them; nothing was saved."""


def settings_fingerprint(config):
    """64 lowercase hex: sha256 of the projected Work/SSE setting values.

    Only keys, values and the unreadable flag are hashed; labels, language and
    descriptions are not. The value itself never leaves this function.
    """
    return settings(config, {}, None)['settings_fingerprint']


def _fingerprint_rows(rows):
    import hashlib
    import json
    values = {row['key']: [row.get('value'), row.get('status') == UNREADABLE]
              for row in rows if row['key'] not in FINGERPRINT_EXCLUDED}
    raw = json.dumps(values, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _settings_lock():
    """One lock for every settings() write of this workspace, in any process."""
    from pathlib import Path
    from .durable_file_lock import exclusive_file_lock
    from .runtime_paths import CONFIG_PATH
    return exclusive_file_lock(Path(CONFIG_PATH).with_name(Path(CONFIG_PATH).name + '.work-settings'))


def _write(config, key, value, save, *, expected=None, load=None):
    """Apply one setting under the settings lock; with ``expected``, as a CAS.

    With ``load`` the configuration is read again inside the lock and the
    change is applied to that fresh copy, so a concurrent settings write of
    another key is never overwritten by a stale copy; ``config`` is updated in
    place to what was saved. With ``expected`` the save happens only when the
    fresh copy's fingerprint still equals it.
    """
    if expected is not None:
        import re
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise WorkPlanError(FINGERPRINT_FIELD + ' must be 64 lowercase hex')
        if load is None:
            raise WorkPlanError('a settings fingerprint check needs the configuration reader')
    if load is None:
        with _settings_lock():
            _set(config, key, value, save)
        return
    with _settings_lock():
        current = load()
        if expected is not None and settings_fingerprint(current) != expected:
            raise SettingsConflict('settings changed elsewhere; reread')
        _set(current, key, value, save)
    if current is not config:
        config.clear()
        config.update(current)


def settings(config, args, save, *, load=None):
    """Project every Work/SSE setting; with ``args['key']``, set one first.

    ``args[FINGERPRINT_FIELD]`` (from ``settings_fingerprint``) makes the write
    a compare-and-set against the stored configuration, read again through
    ``load`` under the settings lock; a mismatch raises SettingsConflict.
    """
    language = config.get('language', 'en')
    key = args.get('key')
    if key:
        value = args.get('value')
        if not isinstance(value, str) or save is None:
            raise WorkPlanError('string value and writable configuration required')
        _write(config, key, value, save, expected=args.get(FINGERPRINT_FIELD), load=load)
        language = config.get('language', 'en')
    from .claude_user_settings import LEFT_ARROW, BACKGROUND, values as claude_values
    claude_labels = {
        'en': ('Left arrow opens agents view', 'Background agents / agent view enabled'),
        'ko': ('왼쪽 화살표로 에이전트 화면 열기', '백그라운드 에이전트 / 에이전트 화면 사용'),
        'ja': ('左矢印でエージェント画面を開く', 'バックグラウンドエージェント / エージェント画面'),
        'zh': ('左箭头打开代理视图', '后台代理 / 代理视图'),
    }.get(language, ('Left arrow opens agents view', 'Background agents / agent view enabled'))
    projected = [{'key': k, 'label': menu_text(k, language),
                  'value': ('ask' if _basic(config, k) else 'auto') if k == 'work_plan_approval_ask'
                      else _basic(config, k),
                  'kind': 'enum' if k == 'work_plan_approval_ask' else 'boolean',
                  **({'choices': ['auto', 'ask']} if k == 'work_plan_approval_ask' else {})} for k in BASIC]
    # One effective SSE mode (work_setting_values.injection_mode), settable by
    # every surface; the two raw side keys below stay for compatibility.
    projected.insert(1, {'key': INJECTION_KEY, 'label': menu_text(INJECTION_KEY, language),
                         'value': injection_mode(config), 'kind': 'enum', 'choices': list(INJECTION_MODES)})
    # Claude Code's own preferences. A running Claude session kept opening the
    # agents view 17 s after leftArrowOpensAgents was turned off (real-console
    # probe, 2.1.310), so both apply to Claude sessions started afterwards.
    claude = claude_values()
    projected += [{'key': key, 'label': label, 'value': claude[key], 'kind': 'boolean',
                   'status': 'unreadable' if claude[key] is None else 'next_claude_session'}
                  for key, label in zip((LEFT_ARROW, BACKGROUND), claude_labels)]
    projected += [{'key': k, 'label': menu_text(k, language), 'value': entry['value'],
                   'kind': PARALLEL_KINDS.get(k, 'text'),
                   **({'status': entry['status']} if entry.get('status') else {})}
                  for k, entry in parallel_values(config, language).items()]
    # Per-priority Work-fork models and the side-fork model (user requirement
    # 2026-11-14): current provider's models only, inherit by default.
    from .work_fork_models import KEYS as MODEL_KEYS, model_ids, status as model_status, value as model_value
    known_models = model_ids(config)
    model_choices = ['inherit', *known_models]
    projected += [{'key': k, 'label': menu_text(k, language), 'value': model_value(config, k),
                   'kind': 'enum', 'choices': model_choices,
                   'status': model_status(config, k, known_models)} for k in MODEL_KEYS]
    projected.append({'key': DISCOVERY_FILE_KEY,
                      'label': menu_text(DISCOVERY_FILE_KEY, language),
                      'value': config.get('work_discovery', {}).get('file_path', ''), 'kind': 'text'})
    projected.append({'key': 'work_side_intake_type', 'label': menu_text('work_side_intake_type', language),
                      'value': config.get('work_side_intake', {}).get('type', 'btw_with_mcp'), 'kind': 'enum',
                      'choices': list(INJECTION_MODES)})
    projected.append({'key': 'work_side_intake_enabled', 'label': menu_text('work_side_intake_enabled', language),
                      'value': config.get('work_side_intake', {}).get('enabled') is True, 'kind': 'boolean'})
    from .work_gate_check import minutes as gate_minutes
    projected.append({'key': 'work_gate_check_minutes', 'label': menu_text('work_gate_check_minutes', language),
                      'value': gate_minutes(config), 'kind': 'number', 'status': 'next_idle_check'})
    from .side_intake_settings import DEFAULT_SIDE_INSTRUCTION, instruction_view, values
    instruction = instruction_view(config)
    side_enums = {
        'work_side_intake_priority_policy': ['adaptive', 'explicit'],
    }
    side_numbers = {'work_side_intake_max_active', 'work_side_intake_aging_seconds'}
    # Side events run as native forks of main (context, tools, permissions);
    # their model is work_side_intake_model above, applied by ciel-work-gate.
    projected += [{'key': k, 'label': menu_text(k, language), 'value': v,
                   'kind': 'enum' if k in side_enums
                       else 'number' if k in side_numbers else 'json' if k == 'work_side_intake_reply_connectors'
                       else 'text',
                   **({'choices': side_enums[k]} if k in side_enums else {}),
                   'status': 'next_side_event',
                   **({'editor': 'markdown', 'stored_value': instruction['stored'],
                       'effective_value': instruction['effective'], 'default_value': DEFAULT_SIDE_INSTRUCTION,
                       'using_default': instruction['using_default']}
                      if k == 'work_side_intake_instruction' else {})}
                  for k, v in values(config).items()]
    provider = str(config.get('current_provider') or '')
    ui = {
        'en': ('Work settings', 'Next', 'Back', 'Cancel', 'Enter a number or instruction using Other.'),
        'ko': ('Work 설정', '다음', '뒤로', '취소', '숫자 또는 지시는 기타에 입력하세요.'),
        'ja': ('Work 設定', '次へ', '戻る', 'キャンセル', '数値または指示は「その他」に入力してください。'),
        'zh': ('Work 设置', '下一页', '返回', '取消', '请在“其他”中输入数字或指令。'),
    }.get(language)
    if ui is None:
        ui = ('Work settings', 'Next', 'Back', 'Cancel', 'Enter a number or instruction using Other.')
    return {'settings': projected, 'settings_fingerprint': _fingerprint_rows(projected),
            'language': language, 'provider': provider,
            'ui': dict(zip(('title', 'next', 'back', 'cancel', 'input_hint'), ui)),
            'applies': 'live; existing workers are not terminated; side settings apply to the next side event, '
                       'which runs as a native fork inheriting main context, tools and permissions; '
                       'Work-fork and side-fork models apply from the next model request of each fork; '
                       'the main session model applies from the next main turn'}


__all__ = ['BASIC', 'FINGERPRINT_FIELD', 'INJECTION_KEY', 'SettingsConflict', 'UNREADABLE', 'known_keys',
           'settings', 'settings_fingerprint']
