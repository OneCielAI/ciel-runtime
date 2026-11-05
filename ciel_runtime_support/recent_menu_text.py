"""Four-language text for recently added runtime menu controls."""
from .ui_text import UI_TEXT

# Each row is English, Korean, Japanese, Chinese; keys are option identities.
TEXT = {
    "state_back": ("Back", "뒤로", "戻る", "返回"),
    "work_settings_menu": ("Work mode settings", "Work 모드 설정", "Work モード設定", "Work 模式设置"),
    "side_settings_menu": ("btw_with_mcp settings", "btw_with_mcp 설정", "btw_with_mcp 設定", "btw_with_mcp 设置"),
    "work_side_intake_enabled": ("SSE side intake", "SSE 사이드 접수", "SSE サイド受付", "SSE 侧边接收"),
    "work_side_intake_type": ("SSE injection type (session / btw_with_mcp)", "SSE 주입 타입 (session / btw_with_mcp)", "SSE 注入タイプ (session / btw_with_mcp)", "SSE 注入类型 (session / btw_with_mcp)"),
    "work_parallel_enabled": ("Work parallel execution", "Work 병렬 실행", "Work 並列実行", "Work 并行执行"),
    "work_parallel_paused": ("Pause Work parallel execution", "Work 병렬 실행 일시정지", "Work 並列実行を一時停止", "暂停 Work 并行执行"),
    "work_parallel_max_active": ("Work concurrent fork limit", "Work 동시 실행 포크 한도", "Work 同時フォーク上限", "Work 同时运行分支上限"),
    "work_discovery_enabled": ("Discover pending Work", "Work 미결 아이템 탐색", "Work 未処理項目を検索", "查找 Work 待办事项"),
    "work_discovery_test_default_override": ("Force default discovery instruction", "기본 탐색 지시 강제 사용", "既定の検索指示を強制使用", "强制使用默认查找指令"),
    "work_discovery_file_path": ("Discovery instruction file (empty clears)", "탐색 지시 파일 (빈 값으로 해제)", "検索指示ファイル（空欄で解除）", "查找指令文件（留空清除）"),
    "work_discovery_instruction": ("Discovery instruction (also via SSE)", "탐색 지시 (SSE에서도 주입)", "検索指示（SSE でも注入）", "查找指令（也通过 SSE 注入）"),
    "work_parallel_p1_reserved": ("Work P1 reserved slots", "Work P1 예약 슬롯", "Work P1 予約枠", "Work P1 预留名额"),
    "work_parallel_launches_per_minute": ("Work launches per minute", "Work 분당 시작 한도", "Work 毎分起動上限", "Work 每分钟启动上限"),
    "work_parallel_max_rework": ("Work rework limit", "Work 자동 재처리 한도", "Work 再処理上限", "Work 自动返工上限"),
    "work_parallel_unavailable": ("Work parallel state unavailable — automatic execution held", "Work 병렬 실행 상태 오류 — 자동 실행 보류", "Work 並列状態を取得できません — 自動実行を保留", "无法读取 Work 并行状态 — 暂停自动执行"),
    "work_parallel_saved": ("Applies to new work; existing workers are not terminated.", "신규 작업부터 반영합니다. 기존 실행은 강제 종료하지 않습니다.", "新しい作業に適用します。既存の実行は強制終了しません。", "对新任务生效，不会终止现有任务。"),
    "work_parallel_unknown": ("Unknown Work parallel option", "알 수 없는 Work 병렬 실행 옵션", "不明な Work 並列オプション", "未知的 Work 并行选项"),
    "enter_bool": ("Enter on/off or true/false", "on/off 또는 true/false 입력", "on/off または true/false を入力", "请输入 on/off 或 true/false"),
    "instruction_length": ("Instruction must contain 1–4000 characters", "지시는 1~4000자여야 합니다", "指示は1～4000文字で入力してください", "指令长度必须为 1–4000 个字符"),
    "work_mode": ("Work mode", "Work 모드", "Work モード", "Work 模式"),
    "work_plan_approval_ask": ("Work plan approval", "Work 계획 승인", "Work 計画承認", "Work 计划审批"),
    "work_auto_continue": ("Work auto continue", "Work 자동 계속", "Work 自動継続", "Work 自动继续"),
    "work_completed_auto_delete": ("Work completed auto-delete (24h)", "Work 완료 기록 자동 삭제 (24시간)", "Work 完了記録の自動削除（24時間）", "Work 完成记录自动删除（24小时）"),
    "router_debug_external_access": ("Router external debug access", "라우터 외부 디버그 접근", "ルーター外部デバッグアクセス", "路由器外部调试访问"),
    "router_debug_message_preview_chars": ("Event message preview", "이벤트 메시지 미리보기", "イベントメッセージのプレビュー", "事件消息预览"),
    "claude_feedback_drafts": ("Claude feedback drafts", "Claude 피드백 초안", "Claude フィードバック下書き", "Claude 反馈草稿"),
    "blocked_tools": ("Tool blocklist (routed)", "도구 차단 목록 (라우팅)", "ツール拒否リスト（ルーティング）", "工具屏蔽列表（路由模式）"),
    "sse_compact_info": ("SSE pre-compact gate removed; injection ≥90% compacts once", "SSE 사전 compact 게이트 제거; 주입 시 90% 이상이면 1회 compact", "SSE 事前 compact ゲート廃止：注入時90%以上なら1回圧縮", "已移除 SSE 预压缩门控：注入时达到90%则压缩一次"),
    "workflows_enabled": ("Claude Code workflows", "Claude Code 워크플로", "Claude Code ワークフロー", "Claude Code 工作流"),
    "ultracode_enabled": ("Claude Code ultracode", "Claude Code ultracode", "Claude Code ultracode", "Claude Code ultracode"),
    "claude_code_supported_capabilities": ("Claude Code capabilities", "Claude Code 지원 기능", "Claude Code 対応機能", "Claude Code 支持功能"),
    "gateway_selection": ("Automatic gateway", "게이트웨이 자동 선택", "ゲートウェイ自動選択", "自动选择网关"),
    "gateway_status": ("Gateway status", "게이트웨이 상태", "ゲートウェイ状態", "网关状态"),
    "gateway_retries": ("CielAiRouter retries", "CielAiRouter 재시도", "CielAiRouter 再試行", "CielAiRouter 重试"),
    "gateway_data_path": ("Gateway data path", "게이트웨이 데이터 경로", "ゲートウェイデータ経路", "网关数据路径"),
    "default_instruction": ("Default instruction", "기본 지시", "既定の指示", "默认指令"),
    "state_on": ("on", "켜짐", "有効", "开启"),
    "state_off": ("off", "꺼짐", "無効", "关闭"),
    "state_auto_default": ("auto (default)", "자동 (기본값)", "自動（既定）", "自动（默认）"),
    "state_auto": ("auto", "자동", "自動", "自动"),
    "state_ask": ("ask user", "사용자 승인 요청", "ユーザーに確認", "询问用户"),
    "state_default_on": ("on (default)", "켜짐 (기본값)", "有効（既定）", "开启（默认）"),
    "state_default_off": ("off (default)", "꺼짐 (기본값)", "無効（既定）", "关闭（默认）"),
    "state_active": ("active", "적용 중", "適用中", "已启用"),
    "state_auto_none": ("auto/none", "자동/없음", "自動/なし", "自动/无"),
    "gateway_central": ("off (central)", "꺼짐 (중앙)", "無効（中央）", "关闭（中央）"),
    "gateway_readonly": ("read-only /health (Enter)", "읽기 전용 /health (Enter)", "読み取り専用 /health（Enter）", "只读 /health（Enter）"),
    "gateway_retry_value": ("{count} extra; 0 off, max 10; router/direct CLI HTTP", "추가 {count}회; 0 끔, 최대 10회; 라우터/직접 CLI HTTP", "追加{count}回：0は無効、最大10回；ルーター/直接CLI HTTP", "额外{count}次；0关闭，最多10次；路由器/直连CLI HTTP"),
    "menu_navigation": ("Up/Down moves. PgUp/PgDn jumps. Home/End bounds. Enter selects. Esc/Left closes. q quits.", "↑/↓ 이동 · PgUp/PgDn 페이지 · Home/End 처음/끝 · Enter 선택 · Esc/← 닫기 · q 종료", "↑/↓ 移動・PgUp/PgDn ページ・Home/End 先頭/末尾・Enter 選択・Esc/← 閉じる・q 終了", "↑/↓ 移动 · PgUp/PgDn 翻页 · Home/End 首尾 · Enter 选择 · Esc/← 关闭 · q 退出"),
    "main_launch": ("Launch", "실행", "起動", "启动"),
    "main_events": ("External event inputs", "외부 이벤트 입력", "外部イベント入力", "外部事件输入"),
    "main_remote": ("Remote instructions", "원격 지침", "リモート指示", "远程指令"),
    "main_mcp": ("Workspace MCP modules", "워크스페이스 MCP 모듈", "ワークスペース MCP モジュール", "工作区 MCP 模块"),
    "main_limits": ("Request/file limits", "요청/파일 제한", "リクエスト/ファイル制限", "请求/文件限制"),
    "main_oauth": ("OAuth tokens", "OAuth 토큰", "OAuth トークン", "OAuth 令牌"),
    "main_web": ("Web access", "웹 접근", "Web アクセス", "Web 访问"),
    "main_recovery": ("Dangerous delete prompt · Stream recovery", "위험한 삭제 확인 · 스트림 복구", "危険な削除の確認・ストリーム復旧", "危险删除确认 · 流恢复"),
    "main_backup": ("Session backup", "세션 백업", "セッションバックアップ", "会话备份"),
    "main_fallback": ("Provider fallback", "프로바이더 대체", "プロバイダー切替", "提供商回退"),
    "main_restart": ("Unattended restart", "무인 재시작", "無人再起動", "无人值守重启"),
    "launch_scoped": ("launch scoped", "실행별 적용", "起動ごとに適用", "按启动生效"),
    "workspace_scoped": ("workspace scoped", "워크스페이스별 적용", "ワークスペースごとに適用", "按工作区生效"),
    "discovery_enabled": ("Work idle discovery", "Work 유휴 시 미결 탐색", "Work 待機中の検索", "Work 空闲时查找"),
    "discovery_instruction": ("Work discovery instruction (literal or env reference)", "Work 탐색 지시 (직접 입력/환경변수)", "Work 検索指示（直接入力/環境変数）", "Work 查找指令（文本/环境变量）"),
    "discovery_file_path": ("Work discovery file (reloads on change)", "Work 탐색 지시 파일 (변경 시 다시 읽기)", "Work 検索指示ファイル（変更時に再読込）", "Work 查找指令文件（更改时重新加载）"),
    "test_default_override": ("Force default discovery instruction", "기본 탐색 지시 강제 사용", "既定の検索指示を強制使用", "强制使用默认查找指令"),
    "recover_sse_delivery": ("Recover SSE delivery now", "SSE 전달 복구 실행", "SSE 配信を今すぐ復旧", "立即恢复 SSE 传递"),
    "work_retention_notice": ("Completed items expire after 24 hours; focused items remain protected. No files or transcripts are deleted.", "완료 항목은 24시간 후 만료되며 포커스 항목은 보호됩니다. 파일과 대화 기록은 삭제하지 않습니다.", "完了項目は24時間後に失効します。フォーカス項目は保護され、ファイルや会話記録は削除しません。", "已完成事项在24小时后过期，聚焦事项受保护；不删除文件或对话记录。"),
    "work_native_notice": ("Native sessions are unchanged. Codex receives instructions only; this version does not enforce these gates for Codex.", "네이티브 세션은 변경하지 않습니다. Codex에는 지침만 전달하며 해당 게이트를 강제하지 않습니다.", "ネイティブセッションは変更しません。Codex には指示のみ渡し、このゲートは強制しません。", "不改变原生会话。仅向 Codex 提供指令，不强制执行这些门控。"),
    "enter_work_value": ("enter {choices}.", "{choices} 입력.", "{choices} を入力してください。", "请输入 {choices}。"),
    "launch_apply": ("{value} (applies at launch)", "{value} (다음 실행부터 적용)", "{value}（起動時に適用）", "{value}（启动时生效）"),
}
LANGUAGES = ("en", "ko", "ja", "zh")


def menu_text(key, language="en", **values):
    language = language if language in LANGUAGES else "en"
    if key in TEXT:
        return TEXT[key][LANGUAGES.index(language)].format(**values)
    return UI_TEXT.get(language, UI_TEXT["en"]).get(key, UI_TEXT["en"].get(key, key))


def state_text(value, language="en"):
    key = {"on": "state_on", "true": "state_on", "off": "state_off", "false": "state_off",
           "on (default)": "state_default_on", "off (default)": "state_default_off",
           "auto (default)": "state_auto_default", "auto": "state_auto", "ask user": "state_ask", "active": "state_active",
           "auto/none": "state_auto_none"}.get(str(value))
    return menu_text(key, language) if key else value
