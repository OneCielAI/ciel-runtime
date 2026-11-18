"""Independent first-screen Work and side-intake selection panels."""
from .work_live_settings import UNREADABLE, settings
from .work_parallel_menu import RANGES as _PARALLEL_RANGES
from .side_intake_settings import LIMITS as _SIDE_LIMITS, PREFIX as _SIDE_PREFIX
from .recent_menu_text import menu_text


BOOLEANS = {'work_mode', 'work_plan_approval_ask', 'work_auto_continue',
            'work_completed_auto_delete', 'work_parallel_enabled', 'work_parallel_paused',
            'work_discovery_enabled', 'work_discovery_test_default_override', 'work_side_intake_enabled'}
# Numeric settings accept any integer in the shared validator range through
# Other (Edit). A fixed list alone made valid values such as P1=6 and
# aging_seconds=17 unreachable from the menu while ctl/REST accepted them.
# The bounds come from the write authorities (work_parallel_menu.RANGES,
# side_intake_settings.LIMITS) and only phrase the hint here.
NUMBER_RANGES = {**_PARALLEL_RANGES, **{_SIDE_PREFIX + name: bounds for name, bounds in _SIDE_LIMITS.items()},
                 'work_gate_check_minutes': (0, 1440)}
NUMBER_PRESETS = {
    **{f'work_parallel_p{n}_limit': (1, 2, 3, 4, 5, 8, 10, 16) for n in range(1, 6)},
    'work_parallel_launches_per_minute': (1, 2, 4, 8, 16, 30, 60),
    'work_parallel_max_rework': (0, 1, 2, 3, 5, 10),
    'work_side_intake_max_active': (1, 2, 3, 4, 5, 8, 16),
    'work_side_intake_aging_seconds': (10, 30, 60, 120, 300, 600),
    'work_gate_check_minutes': (0, 1, 2, 5, 10, 15, 30, 60),
}
RANGE_HINT = {
    'en': 'Other: any integer from {low} to {high}.',
    'ko': '기타: {low}~{high} 사이의 정수를 입력할 수 있습니다.',
    'ja': 'その他: {low}〜{high} の整数を入力できます。',
    'zh': '其他：可输入 {low} 到 {high} 之间的整数。',
}
LANGUAGES = ('en', 'ko', 'ja', 'zh')


def panel_rows(config, *, side=False):
    state = settings(config, {}, None)
    # sse_injection_mode is projected by the shared settings path itself. Keep
    # raw compatibility keys out of the first-screen menu, but do not duplicate
    # or synthesize any row here.
    selected = [r for r in state['settings'] if r['key'] not in ('work_side_intake_enabled', 'work_side_intake_type')]
    info = [] if side else fork_watch_rows(config)
    return ([r['label'] + ': ' + shown_value(config, r) for r in selected] + info
            + [menu_text('state_back', config.get('language', 'en'))],
            [r['key'] for r in selected] + ['__info__'] * len(info) + ['back'])


# Read-only router fork watch rows (no setting): per persisted session, the
# counts, last notice time and its key; never transcript text or agent ids.
FORK_WATCH_TEXT = {
    'en': ('Fork watch', 'ended', 'active', 'unknown', 'orphaned', 'last notice', 'none', 'no observed session', 'more'),
    'ko': ('포크 감시', '종료', '진행', '알 수 없음', '고아', '마지막 알림', '없음', '관측된 세션 없음', '더 있음'),
    'ja': ('フォーク監視', '終了', '実行中', '不明', '孤立', '最終通知', 'なし', '観測済みセッションなし', '件以上'),
    'zh': ('分支监视', '已结束', '进行中', '未知', '孤立', '最近通知', '无', '没有已观测的会话', '更多'),
}


def fork_watch_rows(config):
    from pathlib import Path
    from .fork_turn_watch import status_rows
    from .runtime_paths import CONFIG_DIR
    title, ended, active, unknown, orphaned, last, none, empty, more = FORK_WATCH_TEXT[_language(config)]
    try:
        listing = status_rows(Path(CONFIG_DIR))
    except OSError:
        listing = {'sessions': [], 'more': 0}
    rows = []
    for view in listing['sessions']:
        counts = view['counts']
        notice = (f"{view['last_notice_at']} ({view['last_notice_key']})" if view['last_notice_at'] else none)
        rows.append(f"{title} {view['session'][:15]}…: {ended} {counts['ended']} · {active} {counts['active']} · "
                    f"{unknown} {counts['unknown']} · {orphaned} {counts['orphaned']}; {last} {notice}")
    if listing['more']:
        rows.append(f"{title}: +{listing['more']} {more}")
    return rows or [f'{title}: {empty}']


def shown_value(config, row):
    """Readable text for one typed settings row (menus and ctl text output)."""
    if row.get('status') == UNREADABLE:
        return UNREADABLE
    value = row.get('value')
    if isinstance(value, bool):
        return menu_text('state_on' if value else 'state_off', _language(config))
    return '' if value is None else str(value)


# Each row is English, Korean, Japanese and Chinese. Descriptions explain the
# effect of the setting; value-specific text is appended by description().
DESCRIPTIONS = {
    'work_side_intake_enabled': ('Enable durable side intake for external SSE events.', '외부 SSE 이벤트의 내구성 사이드 접수를 켭니다.', '外部 SSE イベントの永続サイド受付を有効にします。', '为外部 SSE 事件启用持久化侧边接收。'),
    'work_side_intake_type': ('session injects into the main conversation; btw_with_mcp admits the event to the durable side inbox.', 'session은 메인 대화에 주입하고 btw_with_mcp는 이벤트를 내구성 사이드 접수함에 등록합니다.', 'session はメイン会話へ注入し、btw_with_mcp はイベントを永続サイド受信箱へ登録します。', 'session 注入主对话；btw_with_mcp 将事件登记到持久化侧边收件箱。'),
    'work_side_intake_reply_connectors': ('Host-owned source reply routes. Walkie preset: {source, receiver_id, preset:"walkie", server}; source "*" covers every conversation of that receiver; other sources use server/tool/arguments. The side model never sends.', '호스트가 소유하는 원문 응답 경로입니다. Walkie 프리셋: {source, receiver_id, preset:"walkie", server}; source "*"는 그 수신기의 모든 대화에 적용됩니다; 다른 소스는 server/tool/arguments를 사용합니다. 사이드 모델은 전송하지 않습니다.', 'ホスト所有の返信経路です。Walkie プリセット: {source, receiver_id, preset:"walkie", server}。source "*" はその受信機の全会話に適用。他は server/tool/arguments。サイドモデルは送信しません。', '主机所有的源回复路径。Walkie 预设：{source, receiver_id, preset:"walkie", server}；source "*" 适用于该接收器的所有会话；其他源使用 server/tool/arguments。侧边模型不发送。'),
    'work_side_intake_aging_seconds': ('After this wait, ordinary side mail gains pickup priority to avoid starvation; Work execution priority is unchanged.', '이 시간 대기한 일반 메시지의 접수 처리 순위를 높여 기아를 방지합니다. Work 실행 우선순위 승격은 아닙니다.', 'この時間待機した通常のサイドメールは、枯渇を避けるため受付優先度が上がります。Work の実行優先度は変わりません。', '普通侧边消息等待此时长后会提高接收优先级以避免饥饿；Work 执行优先级不变。'),
    'work_side_intake_max_active': ('Concurrent side actors only; durable incoming requests are never capped by this value.', '동시 사이드 처리자 수만 제한합니다. 수신 요청의 내구성 등록 개수 제한이 아닙니다.', '同時サイドアクター数だけを制限します。永続化される受信要求数は制限しません。', '仅限制并发侧边处理器数量；不会限制持久化接收的请求数量。'),
    'work_side_intake_priority_policy': ('adaptive considers P1–P5, urgency and workload; explicit follows stated priority.', 'adaptive는 P1~P5·긴급성·작업량을 함께 판단하며 explicit는 명시된 우선순위를 따릅니다.', 'adaptive は P1～P5、緊急度、作業量を考慮し、explicit は指定された優先度に従います。', 'adaptive 综合考虑 P1–P5、紧迫性和工作量；explicit 遵循明确指定的优先级。'),
    'work_side_intake_instruction': ('Optional instruction appended to every native side fork. Each SSE fork inherits the main context, tools and permissions and uses the SSE side fork model (inherit keeps the main model); its no-write/no-run/no-send limits are prompt policy. This text can refine reply and priority policy but cannot grant execution approval or override those limits.', '모든 네이티브 사이드 포크에 추가되는 선택 지침입니다. 각 SSE 포크는 메인 컨텍스트·도구·권한을 그대로 상속하고 모델은 SSE 사이드 포크 모델 설정을 따르며(inherit은 메인 모델), 쓰기·실행·전송 금지는 프롬프트 정책입니다. 이 지침은 응답·우선순위 정책을 구체화할 수 있지만 실행 승인이나 그 금지를 덮어쓸 수 없습니다.', '各ネイティブサイドフォークに追加する任意の指示です。各 SSE フォークはメインのコンテキスト・ツール・権限を継承し、モデルは SSE サイドフォークモデル設定に従い（inherit はメインのモデル）、書き込み・実行・送信の禁止はプロンプト方針です。返信や優先度方針を具体化できますが、実行承認やその禁止は上書きできません。', '追加到每个原生侧边分叉的可选指令。每个 SSE 分叉继承主会话的上下文、工具和权限，模型遵循 SSE 侧边分支模型设置（inherit 沿用主会话模型）；禁止写入、执行和发送属于提示策略。此指令可细化回复和优先级策略，但不能授予执行批准或覆盖这些限制。'),
    'work_mode': ('Plan and execute queued work in this workspace; disabling keeps existing records.', '이 작업공간의 작업 계획·실행을 관리합니다. 꺼도 기존 기록은 보존합니다.', 'このワークスペースのキュー済み作業を計画・実行します。無効にしても既存記録は保持します。', '规划并执行此工作区中的排队任务；关闭后仍保留现有记录。'),
    'sse_injection_mode': ('session injects normal conversation input. btw_with_mcp uses the durable disk inbox without interrupting the main turn; this is not execution approval.', 'session은 일반 대화 입력으로 주입합니다. btw_with_mcp는 메인 응답을 끊지 않고 내구성 디스크 접수함을 사용합니다. 실행 승인은 아닙니다.', 'session は通常の会話入力として注入します。btw_with_mcp はメインターンを中断せず永続ディスク受信箱を使います。実行承認ではありません。', 'session 作为普通对话输入注入；btw_with_mcp 使用持久化磁盘收件箱且不中断主轮次。这不是执行审批。'),
    'work_plan_approval_ask': ('ask requires manual user approval before executing a new plan. auto approves plans automatically; it does not mean “no approval”.', 'ask는 새 계획 실행 전에 사용자의 수동 승인을 요구합니다. auto는 계획을 자동 승인하며 “승인 없음”이라는 뜻이 아닙니다.', 'ask は新しい計画の実行前にユーザーの手動承認を求めます。auto は計画を自動承認し、「承認なし」という意味ではありません。', 'ask 要求用户在执行新计划前手动审批；auto 会自动审批计划，并不表示“无需审批”。'),
    'work_auto_continue': ('Continue runnable approved work automatically; explicit waits remain.', '승인된 실행 가능 작업을 자동으로 이어갑니다. 명시적 대기는 유지합니다.', '実行可能な承認済み作業を自動継続します。明示的な待機は維持します。', '自动继续可运行且已审批的任务；显式等待仍会保留。'),
    'work_completed_auto_delete': ('Expire completed records after 24 hours; unfinished work is preserved.', '완료 기록을 24시간 후 만료시킵니다. 미완료 작업은 보존합니다.', '完了記録を24時間後に失効させます。未完了の作業は保持します。', '已完成记录在 24 小时后过期；未完成任务会保留。'),
    'work_parallel_enabled': ('Allow concurrent workers; this is not a queue registration limit.', '병렬 작업자 실행을 허용합니다. 큐 등록 개수 제한이 아닙니다.', '並行ワーカーを許可します。キュー登録数の制限ではありません。', '允许并发工作器；这不是队列登记数量限制。'),
    'work_parallel_paused': ('Pause new worker launches without terminating running workers.', '새 작업자 실행만 일시정지하며 실행 중 작업자는 종료하지 않습니다.', '新しいワーカーの起動を一時停止し、実行中のワーカーは終了しません。', '暂停启动新的工作器，但不会终止正在运行的工作器。'),
    'work_gate_check_minutes': ('Every this many minutes a side actor re-checks every blocked or waiting Work item, P0 first, then P1, then the rest, also while main is busy and Work forks run, and tells main (or the waiting worker) when a gate has cleared. It backs up approvals lost on the network. 0 turns it off. Needs SSE side intake on for the side actor; without it the host still runs its own round.', '이 주기(분)마다 사이드 액터가 블로커나 대기 조건이 있는 Work 아이템을 P0, P1, 나머지 순으로 다시 확인하고, 조건이 풀렸으면 메인(또는 기다리는 워커)에게 알립니다. 메인이 작업 중이거나 Work 포크가 실행 중이어도 확인합니다. 네트워크 문제로 놓친 승인 메시지를 보완합니다. 0이면 끕니다. 사이드 액터 확인에는 SSE 사이드 접수가 켜져 있어야 하며, 꺼져 있으면 호스트 자체 점검만 실행됩니다.', 'この間隔（分）ごとに、ブロックや待機中の Work 項目をサイドアクターが P0、P1、その他の順に再確認し、条件が解けたらメイン（または待っているワーカー）に知らせます。メインが作業中でも、Work フォークが動いていても確認します。ネットワーク障害で失われた承認メッセージを補います。0 でオフ。サイドアクターの確認には SSE サイド受付がオンである必要があり、オフの場合はホスト自身の点検だけが動きます。', '每隔此分钟数，侧边执行者按 P0、P1、其余的顺序复查被阻塞或等待中的 Work 项，条件解除时通知主会话（或等待的工作者）。主会话忙碌或 Work 分支运行时也会复查，用于弥补因网络问题丢失的批准消息。0 表示关闭。侧边执行者复查需要开启 SSE 侧边接收；关闭时只运行主机自身的检查。'),
    'work_main_model': ('Model for the main session of this workspace, from the current provider. Applies from the next main turn and stays fixed for that whole turn; inherit keeps the model the session was launched with. The P0-P5 Work fork and SSE side fork models are separate settings and are not changed by this one.', '이 워크스페이스의 메인 세션이 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, 다음 메인 턴부터 적용되고 그 턴 동안은 바뀌지 않습니다. inherit은 세션을 시작한 모델을 그대로 씁니다. P0~P5 Work 포크와 SSE 사이드 포크 모델은 별도 설정이며 이 설정으로 바뀌지 않습니다.', 'このワークスペースのメインセッションが使うモデルです。現在のプロバイダーのモデルから選び、次のメインターンから適用され、そのターンの間は変わりません。inherit はセッション起動時のモデルを使います。P0〜P5 Work フォークと SSE サイドフォークのモデルは別の設定で、この設定では変わりません。', '此工作区主会话使用的模型，从当前提供方的模型中选择。从下一次主会话轮次起生效，并在该轮次内保持不变；inherit 沿用启动会话时的模型。P0-P5 Work 分支和 SSE 侧边分支模型是独立设置，不受此项影响。'),
    'work_parallel_model_p0': ('Model for P0 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P0 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P0 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P0 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_parallel_model_p1': ('Model for P1 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P1 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P1 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P1 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_parallel_model_p2': ('Model for P2 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P2 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P2 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P2 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_parallel_model_p3': ('Model for P3 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P3 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P3 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P3 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_parallel_model_p4': ('Model for P4 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P4 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P4 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P4 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_parallel_model_p5': ('Model for P5 Work forks, from the current provider; inherit keeps the main model. Applies from the next model request of each fork.', 'P5 Work 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 각 포크의 다음 모델 요청부터 적용됩니다.', 'P5 Work フォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。各フォークの次のモデル要求から適用されます。', 'P5 Work 分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。从每个分支的下一次模型请求起生效。'),
    'work_side_intake_model': ('Model for SSE side forks, from the current provider; inherit keeps the main model. Context, tools and permissions stay those of the main session.', 'SSE 사이드 포크가 쓸 모델입니다. 현재 프로바이더의 모델 중에서 고르며, inherit은 메인 모델을 그대로 씁니다. 컨텍스트·도구·권한은 메인과 같습니다.', 'SSE サイドフォークのモデルです。現在のプロバイダーのモデルから選び、inherit はメインのモデルを使います。コンテキスト・ツール・権限はメインと同じです。', 'SSE 侧边分支使用的模型，从当前提供方的模型中选择；inherit 沿用主会话模型。上下文、工具和权限仍与主会话相同。'),
    'work_parallel_p1_limit': ('Maximum P1 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P1 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P1 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P1 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p2_limit': ('Maximum P2 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P2 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P2 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P2 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p3_limit': ('Maximum P3 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P3 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P3 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P3 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p4_limit': ('Maximum P4 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P4 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P4 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P4 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_p5_limit': ('Maximum P5 Work items running at once, each in its own fork session; the device maximum is the sum of P1-P5. Queued items are not limited.', 'P5 Work 아이템을 동시에 실행할 최대 포크 세션 수입니다. 기기 전체 최대치는 P1~P5의 합입니다. 대기 아이템 수는 제한하지 않습니다.', 'P5 の Work 項目を同時実行する最大フォークセッション数です。端末全体の上限は P1〜P5 の合計です。キュー済み項目は制限しません。', '同时运行的 P5 Work 项目的最大分支会话数；设备总上限为 P1–P5 之和。不限制排队项目。'),
    'work_parallel_launches_per_minute': ('Worker launch rate per minute; queued requests remain stored.', '분당 작업자 시작 횟수입니다. 대기 요청은 저장된 상태를 유지합니다.', '1分あたりのワーカー起動数です。キュー済み要求は保存されたままです。', '每分钟启动工作器的速率；排队请求会继续保存。'),
    'work_parallel_orphans': ('Slots held by leases whose owning session is gone. Each choice releases one lease from the preview shown now: Release for an owner process verified gone, Legacy for a lease from an older build without an owner record (choose it only after confirming that session is shut down). If anything changed since the preview, nothing is released. Live or unknown owners are never released; the Work item stays queued.', '소유 세션이 사라진 lease가 잡고 있는 슬롯입니다. 각 선택지는 지금 표시된 미리보기의 lease 하나만 해제합니다. Release는 소유 프로세스 종료가 확인된 lease, Legacy는 소유 기록이 없는 이전 빌드의 lease입니다(그 세션이 종료된 것을 확인한 뒤에만 선택). 미리보기 이후 바뀐 것이 있으면 아무것도 해제하지 않습니다. 살아 있거나 확인할 수 없는 소유자는 해제하지 않으며 Work 아이템은 큐에 남습니다.', '所有セッションが消えた lease が保持するスロットです。各選択肢は今表示しているプレビューの lease を一つだけ解放します。Release は所有プロセスの終了が確認された lease、Legacy は所有記録のない旧ビルドの lease です（そのセッションの終了を確認してから選択）。プレビュー後に変化があれば何も解放しません。生存中または不明な所有者は解放せず、Work 項目はキューに残ります。', '所属会话已不存在的 lease 占用的槽位。每个选项只释放当前预览中的一个 lease：Release 用于已确认所属进程结束的 lease，Legacy 用于没有所属记录的旧版本 lease（仅在确认该会话已关闭后选择）。若预览之后有任何变化，则不释放任何内容。存活或无法确认的所属者不会被释放，Work 项目仍在队列中。'),
    '__info__': ('Read only. The router watches every native fork of each session and tells main once when all of their turns have ended. Counts per state, last notice time and its key; no fork content.', '읽기 전용입니다. 라우터가 각 세션의 모든 네이티브 포크를 감시하고, 모든 포크의 턴이 끝나면 메인에 한 번 알립니다. 상태별 개수, 마지막 알림 시각과 키만 표시하며 포크 내용은 없습니다.', '読み取り専用です。ルーターは各セッションの全ネイティブフォークを監視し、全ターンが終わるとメインに一度通知します。状態別の件数、最終通知時刻とキーのみで、フォーク内容は含みません。', '只读。路由器监视每个会话的所有原生分支，所有分支回合结束时通知主会话一次。仅显示各状态数量、最近通知时间及其键，不含分支内容。'),
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
    if key in NUMBER_RANGES:
        low, high = NUMBER_RANGES[key]
        text += ' ' + RANGE_HINT[language].format(low=low, high=high)
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
    elif key == 'work_side_intake_priority_policy':
        values = ('adaptive', 'explicit')
    elif key.startswith('work_parallel_model_') or key in ('work_side_intake_model', 'work_main_model'):
        # The current provider's models only; inherit keeps the main model.
        from .work_fork_models import model_ids
        values = ('inherit', *model_ids(config))
        return (list(values) + [menu_text('state_back', language)], list(values) + ['back'])
    elif key == 'work_parallel_orphans':
        # Each value embeds the digest of the preview that produced it. Apply
        # therefore releases only the exact lease the person selected, against
        # the exact observations they saw; no bulk "apply" exists.
        from .work_parallel_menu import orphan_choices
        try:
            pairs = orphan_choices()[1]
        except (ValueError, OSError):
            pairs = []
        return ([label for label, _ in pairs] + [menu_text('state_back', language)],
                [value for _, value in pairs] + ['back'])
    elif key in NUMBER_RANGES:
        # Common values for one keystroke, plus Other (Edit) that accepts every
        # integer in the shared validator range.
        low, high = NUMBER_RANGES[key]
        values = tuple(str(n) for n in NUMBER_PRESETS[key] if low <= n <= high)
        return (list(values) + [menu_text('settings_edit_text', language), menu_text('state_back', language)],
                list(values) + ['__edit__', 'back'])
    else:
        # File paths and instructions are inherently text; enter only after
        # explicitly choosing Edit, never as a toggle/enum interaction.
        return ([menu_text('settings_edit_text', language), menu_text('state_back', language)], ['__edit__', 'back'])
    localized = {'on', 'off', 'ask', 'auto'}
    return ([menu_text('state_' + v, language) if v in localized else v for v in values]
            + [menu_text('state_back', language)], list(values) + ['back'])


def apply(config, key, value, save, *, expected_fingerprint=None, load=None):
    """One shared write path for menu, ctl and REST (sse_injection_mode included).

    With ``expected_fingerprint`` (the settings_fingerprint read before the
    edit) and ``load``, the write is a compare-and-set: a change made elsewhere
    in between is refused with SettingsConflict and nothing is saved.
    """
    from .work_live_settings import FINGERPRINT_FIELD
    args = {'key': key, 'value': value}
    if expected_fingerprint is not None:
        args[FINGERPRINT_FIELD] = expected_fingerprint
    return settings(config, args, save, load=load)


CONFLICT_RELOADED = {
    'en': 'settings changed elsewhere; reloaded',
    'ko': '다른 곳에서 설정이 바뀌어 다시 불러왔습니다',
    'ja': '別の場所で設定が変更されたため再読み込みしました',
    'zh': '设置已在其他地方更改，已重新加载',
}


def menu_pin(config):
    """The settings_fingerprint of the configuration a menu shows a person."""
    from .work_live_settings import settings_fingerprint
    return settings_fingerprint(config)


def apply_choice(load, save, key, value, pin):
    """The prelaunch menu's write: pinned to the settings the person saw.

    Returns ``(feedback lines, retry value)``. On a change made elsewhere since
    the menu read ``pin`` nothing is saved, the feedback says the settings were
    reloaded, and the retry value is the entered value so the next prompt for
    the same key can offer it again; it is None otherwise.
    """
    from .work_live_settings import SettingsConflict
    config = load()
    try:
        apply(config, key, value, save, expected_fingerprint=pin, load=load)
    except SettingsConflict:
        return [CONFLICT_RELOADED[_language(config)]], value
    except (ValueError, OSError, RuntimeError) as error:
        return [str(error)], None
    return [apply_feedback(config, key, value)], None
