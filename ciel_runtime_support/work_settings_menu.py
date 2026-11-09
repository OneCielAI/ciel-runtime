"""Independent first-screen Work and side-intake selection panels."""
from .work_live_settings import settings
from .recent_menu_text import menu_text


BOOLEANS = {'work_mode', 'work_plan_approval_ask', 'work_auto_continue',
            'work_completed_auto_delete', 'work_parallel_enabled', 'work_parallel_paused',
            'work_discovery_enabled', 'work_discovery_test_default_override', 'work_side_intake_enabled',
            'work_side_intake_comparison_enabled'}
NUMBERS = {
    'work_parallel_p1_limit': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_p2_limit': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_p3_limit': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_p4_limit': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_p5_limit': (1, 2, 3, 4, 5, 8, 10, 16),
    'work_parallel_launches_per_minute': (1, 2, 4, 8, 16, 30, 60),
    'work_parallel_max_rework': (0, 1, 2, 3, 5, 10),
    'work_side_intake_context_percent': (10, 20, 30, 50, 75, 100),
    'work_side_intake_max_active': (1, 2, 3, 4, 5, 8, 16),
    'work_side_intake_aging_seconds': (10, 30, 60, 120, 300, 600),
}
LANGUAGES = ('en', 'ko', 'ja', 'zh')


def panel_rows(config, *, side=False):
    state = settings(config, {}, None)
    selected = [r for r in state['settings'] if r['key'] not in ('work_side_intake_enabled', 'work_side_intake_type')]
    selected.insert(1, {'key': 'sse_injection_mode', 'label': menu_text('sse_injection_mode', config.get('language', 'en')),
                        'value': 'btw_with_mcp' if config.get('work_side_intake', {}).get('enabled') is True
                        and config.get('work_side_intake', {}).get('type', 'btw_with_mcp') == 'btw_with_mcp' else 'session'})
    return ([r['label'] + ': ' + str(r['value']) for r in selected] + [menu_text('state_back', config.get('language', 'en'))],
            [r['key'] for r in selected] + ['back'])


# Each row is English, Korean, Japanese and Chinese. Descriptions explain the
# effect of the setting; value-specific text is appended by description().
DESCRIPTIONS = {
    'work_side_intake_enabled': ('Enable durable side intake for external SSE events.', '외부 SSE 이벤트의 내구성 사이드 접수를 켭니다.', '外部 SSE イベントの永続サイド受付を有効にします。', '为外部 SSE 事件启用持久化侧边接收。'),
    'work_side_intake_type': ('session injects into the main conversation; btw_with_mcp admits the event to the durable side inbox.', 'session은 메인 대화에 주입하고 btw_with_mcp는 이벤트를 내구성 사이드 접수함에 등록합니다.', 'session はメイン会話へ注入し、btw_with_mcp はイベントを永続サイド受信箱へ登録します。', 'session 注入主对话；btw_with_mcp 将事件登记到持久化侧边收件箱。'),
    'work_side_intake_reply_connectors': ('Host-owned source reply routes. Walkie preset: {source, receiver_id, preset:"walkie", server}; source "*" covers every conversation of that receiver; other sources use server/tool/arguments. The side model never sends.', '호스트가 소유하는 원문 응답 경로입니다. Walkie 프리셋: {source, receiver_id, preset:"walkie", server}; source "*"는 그 수신기의 모든 대화에 적용됩니다; 다른 소스는 server/tool/arguments를 사용합니다. 사이드 모델은 전송하지 않습니다.', 'ホスト所有の返信経路です。Walkie プリセット: {source, receiver_id, preset:"walkie", server}。source "*" はその受信機の全会話に適用。他は server/tool/arguments。サイドモデルは送信しません。', '主机所有的源回复路径。Walkie 预设：{source, receiver_id, preset:"walkie", server}；source "*" 适用于该接收器的所有会话；其他源使用 server/tool/arguments。侧边模型不发送。'),
    'work_side_intake_aging_seconds': ('After this wait, ordinary side mail gains pickup priority to avoid starvation; Work execution priority is unchanged.', '이 시간 대기한 일반 메시지의 접수 처리 순위를 높여 기아를 방지합니다. Work 실행 우선순위 승격은 아닙니다.', 'この時間待機した通常のサイドメールは、枯渇を避けるため受付優先度が上がります。Work の実行優先度は変わりません。', '普通侧边消息等待此时长后会提高接收优先级以避免饥饿；Work 执行优先级不变。'),
    'work_side_intake_max_active': ('Concurrent side actors only; durable incoming requests are never capped by this value.', '동시 사이드 처리자 수만 제한합니다. 수신 요청의 내구성 등록 개수 제한이 아닙니다.', '同時サイドアクター数だけを制限します。永続化される受信要求数は制限しません。', '仅限制并发侧边处理器数量；不会限制持久化接收的请求数量。'),
    'work_side_intake_context_mode': ('full (default): exact router-captured parent system/history. recent: provider-counted recent history. isolated: Work only. Rebase at event boundaries; no direct-gateway fallback; Work is always whole.', 'full(기본): 라우터가 관측한 메인 시스템·대화 전체. recent: 공급자 계산 최근 대화. isolated: Work만. 이벤트 경계에서 rebase하며 direct gateway fallback은 없습니다. Work 보드는 항상 전체입니다.', 'full（既定）：ルーターが取得した親の system/history をそのまま使用。recent：プロバイダー計測の最近の履歴。isolated：Work のみ。イベント境界で rebase し、direct-gateway fallback はありません。Work は常に全体を保持します。', 'full（默认）：路由器捕获的完整父 system/history；recent：由提供商计数的最近历史；isolated：仅 Work。在事件边界 rebase，无 direct-gateway fallback；Work 始终完整保留。'),
    'work_side_intake_context_percent': ('Provider-counted history share; complete tool pairs. System/tools/Work stay whole. Recent/model switch records omitted parent reasoning.', '대화 토큰 비율입니다. 공급자 count_tokens가 필요하며 도구 쌍 경계까지 확장합니다. 시스템·도구·Work는 제외. 최근/모델 전환 시 부모 서명 추론을 제외하고 기록합니다.', 'プロバイダー計測による履歴の割合で、tool の組は完全に保ちます。System/tools/Work は全体を保持し、recent/モデル切替記録では親の reasoning を除外します。', '按提供商计数的历史占比，并保留完整工具调用对。System/tools/Work 始终完整；recent/模型切换记录会省略父 reasoning。'),
    'work_side_intake_model': ('inherit follows the main model; or pick another model of the same provider, sent over the main session connection and authentication. If it is unavailable, the side retries that event on the main model; SSE never enters the main transcript.', 'inherit는 메인 모델을 따르고, 같은 프로바이더의 다른 모델을 고르면 메인 세션의 접속·인증으로 호출합니다. 선택 모델을 쓸 수 없으면 사이드가 그 이벤트를 메인 모델로 다시 처리하며 SSE는 메인 대화에 들어가지 않습니다.', 'inherit はメインモデルに従い、同じプロバイダーの別モデルを選ぶとメインセッションの接続と認証で呼び出します。利用できない場合はサイドがそのイベントをメインモデルで再処理し、SSE はメイン会話に入りません。', 'inherit 跟随主模型；也可选择同一提供商的其他模型，并通过主会话的连接和认证调用。所选模型不可用时，侧边会用主模型重新处理该事件，SSE 不会进入主对话。'),
    'work_side_intake_priority_policy': ('adaptive considers P1–P5, urgency and workload; explicit follows stated priority.', 'adaptive는 P1~P5·긴급성·작업량을 함께 판단하며 explicit는 명시된 우선순위를 따릅니다.', 'adaptive は P1～P5、緊急度、作業量を考慮し、explicit は指定された優先度に従います。', 'adaptive 综合考虑 P1–P5、紧迫性和工作量；explicit 遵循明确指定的优先级。'),
    'work_side_intake_instruction': ('Optional instruction appended to every forked side run. It can refine ACK, reply and priority policy but cannot grant execution approval or override safety.', '모든 포크 사이드 실행에 추가되는 선택 지침입니다. ACK·응답·우선순위 정책을 구체화할 수 있지만 실행 승인이나 안전 규칙을 덮어쓸 수 없습니다.', '各フォーク側処理に追加する任意の指示です。ACK、返信、優先度方針を具体化できますが、実行承認や安全規則は上書きできません。', '追加到每次分叉侧边运行的可选指令。可细化 ACK、回复和优先级策略，但不能授予执行批准或覆盖安全规则。'),
    'work_side_intake_comparison_enabled': ('After the primary reply, run up to four read-only full/recent × main/comparison-model profiles. These are billed model calls with no tools, Work writes or replies.', '주 응답 후 full/recent × 메인/비교 모델을 최대4개 읽기 전용 비교합니다. 유료 호출이며 도구·Work 변경·응답 전송은 없습니다.', '主返信後、full/recent × main/比較モデルを最大4件、読み取り専用で比較します。課金対象のモデル呼び出しで、tool、Work 書込み、返信は行いません。', '主回复后最多运行四个只读 full/recent × 主模型/对比模型配置。这些是计费模型调用，不使用工具、不写入 Work、不发送回复。'),
    'work_side_intake_comparison_model': ('Second model for read-only comparison; inherit compares only context modes. Requires router capture and provider token counting for recent.', '읽기 전용 비교용 두 번째 모델입니다. inherit는 문맥 모드만 비교하며 recent는 라우터 관측과 공급자 토큰 계산이 필요합니다.', '読み取り専用比較の第2モデルです。inherit は文脈モードだけを比較します。recent にはルーター取得とプロバイダーの token 計測が必要です。', '用于只读对比的第二模型；inherit 仅比较上下文模式。recent 需要路由器捕获和提供商 token 计数。'),
    'work_mode': ('Plan and execute queued work in this workspace; disabling keeps existing records.', '이 작업공간의 작업 계획·실행을 관리합니다. 꺼도 기존 기록은 보존합니다.', 'このワークスペースのキュー済み作業を計画・実行します。無効にしても既存記録は保持します。', '规划并执行此工作区中的排队任务；关闭后仍保留现有记录。'),
    'sse_injection_mode': ('session injects normal conversation input. btw_with_mcp uses the durable disk inbox without interrupting the main turn; this is not execution approval.', 'session은 일반 대화 입력으로 주입합니다. btw_with_mcp는 메인 응답을 끊지 않고 내구성 디스크 접수함을 사용합니다. 실행 승인은 아닙니다.', 'session は通常の会話入力として注入します。btw_with_mcp はメインターンを中断せず永続ディスク受信箱を使います。実行承認ではありません。', 'session 作为普通对话输入注入；btw_with_mcp 使用持久化磁盘收件箱且不中断主轮次。这不是执行审批。'),
    'work_plan_approval_ask': ('ask requires manual user approval before executing a new plan. auto approves plans automatically; it does not mean “no approval”.', 'ask는 새 계획 실행 전에 사용자의 수동 승인을 요구합니다. auto는 계획을 자동 승인하며 “승인 없음”이라는 뜻이 아닙니다.', 'ask は新しい計画の実行前にユーザーの手動承認を求めます。auto は計画を自動承認し、「承認なし」という意味ではありません。', 'ask 要求用户在执行新计划前手动审批；auto 会自动审批计划，并不表示“无需审批”。'),
    'work_auto_continue': ('Continue runnable approved work automatically; explicit waits remain.', '승인된 실행 가능 작업을 자동으로 이어갑니다. 명시적 대기는 유지합니다.', '実行可能な承認済み作業を自動継続します。明示的な待機は維持します。', '自动继续可运行且已审批的任务；显式等待仍会保留。'),
    'work_completed_auto_delete': ('Expire completed records after 24 hours; unfinished work is preserved.', '완료 기록을 24시간 후 만료시킵니다. 미완료 작업은 보존합니다.', '完了記録を24時間後に失効させます。未完了の作業は保持します。', '已完成记录在 24 小时后过期；未完成任务会保留。'),
    'work_parallel_enabled': ('Allow concurrent workers; this is not a queue registration limit.', '병렬 작업자 실행을 허용합니다. 큐 등록 개수 제한이 아닙니다.', '並行ワーカーを許可します。キュー登録数の制限ではありません。', '允许并发工作器；这不是队列登记数量限制。'),
    'work_parallel_paused': ('Pause new worker launches without terminating running workers.', '새 작업자 실행만 일시정지하며 실행 중 작업자는 종료하지 않습니다.', '新しいワーカーの起動を一時停止し、実行中のワーカーは終了しません。', '暂停启动新的工作器，但不会终止正在运行的工作器。'),
    'work_parallel_p1_limit': ('Maximum P1 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P1 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P1 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P1 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p2_limit': ('Maximum P2 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P2 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P2 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P2 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p3_limit': ('Maximum P3 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P3 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P3 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P3 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p4_limit': ('Maximum P4 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P4 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P4 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P4 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p5_limit': ('Maximum P5 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P5 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P5 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P5 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_launches_per_minute': ('Worker launch rate per minute; queued requests remain stored.', '분당 작업자 시작 횟수입니다. 대기 요청은 저장된 상태를 유지합니다.', '1分あたりのワーカー起動数です。キュー済み要求は保存されたままです。', '每分钟启动工作器的速率；排队请求会继续保存。'),
    'work_parallel_orphans': ('Slots held by leases whose owning session is gone. apply releases leases whose owner process is verified gone; a lease from an older build has no owner record, so after confirming that session is shut down type legacy <lease id>. Live or unknown owners are never released; the Work item stays queued.', '소유 세션이 사라진 lease가 잡고 있는 슬롯입니다. apply는 소유 프로세스 종료가 확인된 lease만 해제합니다. 이전 빌드의 lease는 소유 기록이 없으므로 그 세션이 종료된 것을 확인한 뒤 legacy <lease id>를 입력하세요. 살아 있거나 확인할 수 없는 소유자는 해제하지 않으며 Work 아이템은 큐에 남습니다.', '所有セッションが消えた lease が保持するスロットです。apply は所有プロセスの終了が確認された lease だけを解放します。旧ビルドの lease は所有記録がないため、そのセッションの終了を確認してから legacy <lease id> を入力してください。生存中または不明な所有者は解放せず、Work 項目はキューに残ります。', '所属会话已不存在的 lease 占用的槽位。apply 仅释放已确认所属进程结束的 lease；旧版本的 lease 没有所属记录，请确认该会话已关闭后输入 legacy <lease id>。存活或无法确认的所属者不会被释放，Work 项目仍在队列中。'),
    'work_parallel_max_rework': ('Automatic rework attempts before review is required.', '검토가 필요해지기 전 자동 재처리 시도 횟수입니다.', 'レビューが必要になるまでの自動再処理回数です。', '需要审核前允许的自动返工次数。'),
    'work_discovery_enabled': ('Look for outstanding work when execution slots are available.', '실행 슬롯에 여유가 생기면 미결 작업을 탐색합니다.', '実行枠が空いたとき未処理の作業を検索します。', '有可用执行名额时查找未完成任务。'),
    'work_discovery_test_default_override': ('Use the default discovery instruction instead of custom text.', '사용자 지정 문구 대신 기본 작업 탐색 지시를 사용합니다.', 'カスタム文ではなく既定の検索指示を使用します。', '使用默认查找指令而不是自定义文本。'),
    'work_discovery_instruction': ('Instruction used to discover outstanding requests; not approval to execute.', '미결 요청 탐색에 사용할 지시입니다. 실행 승인이 아닙니다.', '未処理要求の検索に使う指示であり、実行承認ではありません。', '用于查找未完成请求的指令；不是执行审批。'),
    'work_discovery_file_path': ('Load discovery instructions from this file; empty clears the path.', '이 파일에서 탐색 지시를 읽습니다. 빈 값으로 경로를 해제합니다.', 'このファイルから検索指示を読み込みます。空欄でパスを解除します。', '从此文件加载查找指令；留空会清除路径。'),
}

VALUE_PREFIX = {
    'en': 'Selected value: {value}.',
    'ko': '선택 값: {value}.',
    'ja': '選択値: {value}。',
    'zh': '所选值：{value}。',
}
APPLIED = {
    'en': '{label} set to {value}.',
    'ko': '{label}을(를) {value}(으)로 설정했습니다.',
    'ja': '{label} を {value} に設定しました。',
    'zh': '已将{label}设置为 {value}。',
}


def _language(config):
    language = config.get('language', 'en')
    return language if language in LANGUAGES else 'en'


def description(config, key, value=None):
    language = _language(config)
    texts = DESCRIPTIONS.get(key)
    if texts is None:
        texts = ('Select a value; changes apply to this setting only.', '값을 선택하면 해당 설정에만 반영됩니다.',
                 '値を選択すると、この設定だけに反映されます。', '选择一个值；更改仅应用于此设置。')
    text = texts[LANGUAGES.index(language)]
    if value is not None and value not in ('back', '__edit__', '__refresh_models__'):
        raw = value
        if key == 'work_plan_approval_ask':
            raw = 'ask' if value in (True, 'true', 'on', 'ask') else 'auto'
        elif isinstance(value, bool):
            raw = 'on' if value else 'off'
        shown = menu_text('state_' + str(raw), language) if str(raw) in ('on', 'off', 'ask', 'auto') else str(raw)
        text += ' ' + VALUE_PREFIX[language].format(value=shown)
    return text


def apply_feedback(config, key, value):
    language = _language(config)
    return APPLIED[language].format(label=menu_text(key, language), value=value)


def choices(config, key):
    language = _language(config)
    if key == 'work_plan_approval_ask':
        values = ('ask', 'auto')
    elif key in BOOLEANS:
        values = ('on', 'off')
    elif key in ('sse_injection_mode', 'work_side_intake_type'):
        values = ('session', 'btw_with_mcp')
    elif key == 'work_side_intake_context_mode':
        values = ('isolated', 'full', 'recent')
    elif key == 'work_side_intake_priority_policy':
        values = ('adaptive', 'explicit')
    elif key in NUMBERS:
        values = tuple(str(n) for n in NUMBERS[key])
    else:
        # File paths and instructions are inherently text; enter only after
        # explicitly choosing Edit, never as a toggle/enum interaction.
        return ([menu_text('settings_edit_text', language), menu_text('state_back', language)], ['__edit__', 'back'])
    localized = {'on', 'off', 'ask', 'auto'}
    return ([menu_text('state_' + v, language) if v in localized else v for v in values]
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
