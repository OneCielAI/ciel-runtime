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
        from .side_intake_settings import values, set_value
        if key in values(config):
            set_value(config, key, value)
            save(config)
        elif key == 'work_discovery_file_path':
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
            if value not in ('session', 'btw_with_mcp'):
                raise WorkPlanError('use session or btw_with_mcp')
            config['work_side_intake'] = {**config.get('work_side_intake', {}), 'type': value}
            save(config)
        elif key in KEYS:
            apply(config, key, value, save)
        elif key.startswith('claude_'):
            from .claude_user_settings import set_value as set_claude_value
            set_claude_value(key, value)
        elif key in BASIC or key == 'work_side_intake_enabled':
            if key == 'work_plan_approval_ask':
                value = {'ask': 'on', 'auto': 'off'}.get(value, value)
            if value not in ('on', 'off', 'true', 'false'):
                raise WorkPlanError('use on/off or true/false')
            enabled = value in ('on', 'true')
            if key == 'work_side_intake_enabled':
                config['work_side_intake'] = {**config.get('work_side_intake', {}), 'enabled': enabled, 'type': config.get('work_side_intake', {}).get('type', 'btw_with_mcp')}
            else:
                config[key] = enabled
            save(config)
        else:
            raise WorkPlanError('unknown Work setting')
    from .claude_user_settings import LEFT_ARROW, BACKGROUND, values as claude_values
    claude_labels = {
        'en': ('Left arrow opens agents view', 'Background agents / agent view enabled'),
        'ko': ('왼쪽 화살표로 에이전트 화면 열기', '백그라운드 에이전트 / 에이전트 화면 사용'),
        'ja': ('左矢印でエージェント画面を開く', 'バックグラウンドエージェント / エージェント画面'),
        'zh': ('左箭头打开代理视图', '后台代理 / 代理视图'),
    }.get(language, ('Left arrow opens agents view', 'Background agents / agent view enabled'))
    projected = [{'key': k, 'label': menu_text(k, language),
                  'value': ('ask' if config.get(k, False) else 'auto') if k == 'work_plan_approval_ask'
                      else config.get(k, True),
                  'kind': 'enum' if k == 'work_plan_approval_ask' else 'boolean',
                  **({'choices': ['auto', 'ask']} if k == 'work_plan_approval_ask' else {})} for k in BASIC]
    # Claude Code's own preferences. A running Claude session kept opening the
    # agents view 17 s after leftArrowOpensAgents was turned off (real-console
    # probe, 2.1.310), so both apply to Claude sessions started afterwards.
    claude = claude_values()
    projected += [{'key': key, 'label': label, 'value': claude[key], 'kind': 'boolean',
                   'status': 'unreadable' if claude[key] is None else 'next_claude_session'}
                  for key, label in zip((LEFT_ARROW, BACKGROUND), claude_labels)]
    bool_parallel = {'work_parallel_enabled', 'work_parallel_paused', 'work_discovery_enabled',
                     'work_discovery_test_default_override'}
    projected += [{'key': k, 'label': label, 'value': value,
                   'kind': 'boolean' if k in bool_parallel else 'number' if k in {
                       *(f'work_parallel_p{n}_limit' for n in range(1, 6)),
                       'work_parallel_launches_per_minute', 'work_parallel_max_rework'} else 'text'}
                  for label, k, value in rows(config)]
    projected.append({'key': 'work_discovery_file_path',
                      'label': menu_text('work_discovery_file_path', language),
                      'value': config.get('work_discovery', {}).get('file_path', ''), 'kind': 'text'})
    projected.append({'key': 'work_side_intake_type', 'label': menu_text('work_side_intake_type', language),
                      'value': config.get('work_side_intake', {}).get('type', 'btw_with_mcp'), 'kind': 'enum',
                      'choices': ['session', 'btw_with_mcp']})
    projected.append({'key': 'work_side_intake_enabled', 'label': menu_text('work_side_intake_enabled', language),
                      'value': config.get('work_side_intake', {}).get('enabled') is True, 'kind': 'boolean'})
    from .side_intake_settings import values
    side_enums = {
        'work_side_intake_priority_policy': ['adaptive', 'explicit'],
    }
    side_numbers = {'work_side_intake_max_active', 'work_side_intake_aging_seconds'}
    # Side events always run as native forks of main: model, context, tools and
    # permissions are inherited, so no side model/context row is offered.
    projected += [{'key': k, 'label': menu_text(k, language), 'value': v,
                   'kind': 'enum' if k in side_enums
                       else 'number' if k in side_numbers else 'json' if k == 'work_side_intake_reply_connectors'
                       else 'text',
                   **({'choices': side_enums[k]} if k in side_enums else {}),
                   'status': 'next_side_event'}
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
    return {'settings': projected, 'language': language, 'provider': provider,
            'ui': dict(zip(('title', 'next', 'back', 'cancel', 'input_hint'), ui)),
            'applies': 'live; existing workers are not terminated; side settings apply to the next side event, '
                       'which always runs as a native fork inheriting the main model, context, tools and permissions'}
