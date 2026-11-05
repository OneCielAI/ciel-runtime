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
        if key == 'work_discovery_file_path':
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
    projected = [{'key': k, 'label': menu_text(k, language),
                  'value': config.get(k, k != 'work_plan_approval_ask')} for k in BASIC]
    projected += [{'key': k, 'label': label, 'value': value} for label, k, value in rows(config)]
    projected.append({'key': 'work_discovery_file_path',
                      'label': menu_text('work_discovery_file_path', language),
                      'value': config.get('work_discovery', {}).get('file_path', '')})
    projected.append({'key': 'work_side_intake_type', 'label': menu_text('work_side_intake_type', language),
                      'value': config.get('work_side_intake', {}).get('type', 'btw_with_mcp')})
    projected.append({'key': 'work_side_intake_enabled', 'label': menu_text('work_side_intake_enabled', language),
                      'value': config.get('work_side_intake', {}).get('enabled') is True})
    ui = {
        'en': ('Work settings', 'Next', 'Back', 'Cancel', 'Enter a number or instruction using Other.'),
        'ko': ('Work 설정', '다음', '뒤로', '취소', '숫자 또는 지시는 기타에 입력하세요.'),
        'ja': ('Work 設定', '次へ', '戻る', 'キャンセル', '数値または指示は「その他」に入力してください。'),
        'zh': ('Work 设置', '下一页', '返回', '取消', '请在“其他”中输入数字或指令。'),
    }.get(language)
    if ui is None:
        ui = ('Work settings', 'Next', 'Back', 'Cancel', 'Enter a number or instruction using Other.')
    return {'settings': projected, 'language': language,
            'ui': dict(zip(('title', 'next', 'back', 'cancel', 'input_hint'), ui)),
            'applies': 'live; existing workers are not terminated'}
