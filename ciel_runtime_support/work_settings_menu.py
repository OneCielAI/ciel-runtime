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
    selected = [r for r in state['settings'] if not r['key'].startswith('work_side_intake_')]
    selected.insert(1, {'key': 'sse_injection_mode', 'label': menu_text('sse_injection_mode', config.get('language', 'en')),
                        'value': 'btw_with_mcp' if config.get('work_side_intake', {}).get('enabled') is True
                        and config.get('work_side_intake', {}).get('type', 'btw_with_mcp') == 'btw_with_mcp' else 'session'})
    return ([r['label'] + ': ' + str(r['value']) for r in selected] + [menu_text('state_back', config.get('language', 'en'))],
            [r['key'] for r in selected] + ['back'])


DESCRIPTIONS = {
    'work_mode': ('Plan and execute queued work in this workspace; disabling keeps existing records.', '이 작업공간의 작업 계획·실행을 관리합니다. 꺼도 기존 기록은 보존합니다.'),
    'sse_injection_mode': ('session: normal conversation input. btw_with_mcp: durable disk inbox, consumed by the mod without interrupting the main turn; not execution approval.', 'session: 일반 대화로 전달합니다. btw_with_mcp: 디스크 접수함을 mod가 처리하며 메인 응답을 끊지 않습니다. 실행 승인은 아닙니다.'),
    'work_plan_approval_ask': ('Require user approval before executing a new plan.', '새 계획 실행 전에 사용자 승인을 요청합니다.'),
    'work_auto_continue': ('Continue runnable approved work automatically; explicit waits remain.', '승인된 실행 가능 작업을 자동으로 이어갑니다. 명시적 대기는 유지합니다.'),
    'work_completed_auto_delete': ('Expire completed records after 24 hours; unfinished work is preserved.', '완료 기록을 24시간 후 만료시킵니다. 미완료 작업은 보존합니다.'),
    'work_parallel_enabled': ('Allow concurrent workers; this is not a queue registration limit.', '병렬 작업자 실행을 허용합니다. 큐 등록 개수 제한이 아닙니다.'),
    'work_parallel_paused': ('Pause new worker launches without terminating running workers.', '새 작업자 실행만 일시정지하며 실행 중 작업자는 종료하지 않습니다.'),
    'work_parallel_max_active': ('Maximum workers running concurrently, not queued requests.', '동시에 실행할 작업자 수입니다. 대기 요청 수를 제한하지 않습니다.'),
    'work_parallel_p1_reserved': ('Slots reserved for urgent P1 workers.', '긴급 P1 작업자에 예약할 실행 슬롯 수입니다.'),
    'work_parallel_launches_per_minute': ('Worker launch rate per minute; queued requests remain stored.', '분당 작업자 시작 횟수입니다. 대기 요청은 저장된 상태를 유지합니다.'),
    'work_parallel_max_rework': ('Automatic rework attempts before review is required.', '검토가 필요해지기 전 자동 재처리 시도 횟수입니다.'),
    'work_discovery_enabled': ('Look for outstanding work when execution slots are available.', '실행 슬롯에 여유가 생기면 미결 작업을 탐색합니다.'),
    'work_discovery_test_default_override': ('Use the default discovery instruction instead of custom text.', '사용자 지정 문구 대신 기본 작업 탐색 지시를 사용합니다.'),
    'work_discovery_instruction': ('Instruction used to discover outstanding requests; not approval to execute.', '미결 요청 탐색에 사용할 지시입니다. 실행 승인이 아닙니다.'),
    'work_discovery_file_path': ('Load discovery instructions from this file; empty clears the path.', '이 파일에서 탐색 지시를 읽습니다. 빈 값으로 경로를 해제합니다.'),
}


def description(config, key):
    pair = DESCRIPTIONS.get(key, ('Select a value; changes apply to this setting only.', '값을 선택하면 해당 설정에만 반영됩니다.'))
    return pair[1 if config.get('language') == 'ko' else 0]


def choices(config, key):
    language = config.get('language', 'en')
    if key in BOOLEANS:
        values = ('on', 'off')
    elif key in ('sse_injection_mode', 'work_side_intake_type'):
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
    if key == 'sse_injection_mode':
        if value not in ('session', 'btw_with_mcp'):
            raise ValueError('use session or btw_with_mcp')
        config['work_side_intake'] = {**config.get('work_side_intake', {}),
                                     'enabled': value == 'btw_with_mcp', 'type': value}
        save(config)
        return settings(config, {}, None)
    return settings(config, {'key': key, 'value': value}, save)
