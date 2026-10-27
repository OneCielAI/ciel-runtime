# Tool Guard — 툴 필터링 및 검증

> 소스: `ciel-runtime-tool-guard.py`, `ciel_runtime.py` — `_validate_and_fix_tool_input()`, `should_drop_emitted_tool_call()`, `resolve_blocked_tools()`

---

## 개요

Tool Guard는 두 가지 역할을 수행한다:

1. **툴 차단** (`blocked_tools`): 라우터 요청의 호환성 기본 차단 목록 적용 (CLI 권한과 별개)
2. **툴 입력 수정** (`_validate_and_fix_tool_input()`): 비표준 모델이 잘못 생성한 툴 입력 자동 교정

---

## 기본 차단 툴 목록 (비-Anthropic 제공자)

```python
DEFAULT_BLOCKED_TOOLS_NON_ANTHROPIC = (
    "EnterWorktree", "ExitWorktree",
    "TeamCreate", "TeamDelete", "TeammateTool",
    "SendMessageTool",
    "ScheduleWakeup",
    "WaitForMcpServers",
    "WebSearch", "web_search",
    "WebFetch", "web_fetch",
    "RemoteTrigger",
    "PushNotification",
)
```

`SendMessage`는 Claude Code가 로컬에서 실행하는 에이전트 메시지 도구이며 기본 차단하지 않습니다.
Claude Code 2.1.310 + 소유한 루프백 서버에서 CielAiRouter `/v1/messages` 및 vLLM
`/v1/chat/completions` 변환 경로로 자식 에이전트가 메시지를 받아 응답한 것을 확인했습니다.
이는 실제 원격 제공자의 모델 품질/스키마 수용 또는 구형 `SendMessageTool`, 팀 생성/삭제 검증을 의미하지 않습니다.
다른 기본 차단은 유지됩니다.

## 제공자별 차단 목록 설정

런처 메뉴의 **LLM options → Tool blocklist (routed)** 에서 변경합니다.

- `default` / `unset` / `auto`: 해당 제공자의 기본 정책으로 복원.
- `["SendMessage", "WebSearch"]`: 전체 차단 목록을 명시한 배열로 교체 (추가 방식 아님).
- `false` / `[]`: 모든 호환성 차단 해제. 미지원 서버 도구도 노출되므로 주의해야 합니다.
- 빈 입력은 기존 값을 유지하고 잘못된 JSON/타입은 저장하지 않습니다.

저장 위치는 `providers.<name>.blocked_tools` 입니다. 다음 라우터 요청부터 적용하며,
이미 진행 중인 요청을 재생하지 않습니다. native/direct 경로에는 라우터 필터가 없고 CLI 권한은 그대로 유지됩니다.
명시적 기존 목록에 `SendMessage`가 있으면 새 기본값보다 그 목록이 우선합니다 (자동 변경하지 않음).
CLI에서도 `provider-options <provider> 'blocked_tools=["SendMessage"]'` 및
`provider-options <provider> unset:blocked_tools`를 사용할 수 있습니다.

`CLAUDE_SERVER_SIDE_WEB_TOOLS`: `WebSearch`, `WebFetch` — Anthropic 서버사이드 툴로 비-Anthropic 제공자에서 차단.

---

## 툴 이름 정규화

`resolve_emitted_tool_name()` — MCP 툴 이름에서 서버 접두사 제거.  
`_fuzzy_match_tool_name()` — 대소문자/구분자 차이 허용하는 퍼지 매칭.  
`_match_available_tool_name()` — 사용 가능한 툴 목록에서 가장 유사한 이름 탐색.

---

## 툴 입력 자동 교정

`_validate_and_fix_tool_input(tool_name, input_dict)`:

### Claude Code 핵심 툴 스키마

| 툴 | 필수 필드 | 허용 필드 |
|----|----------|---------|
| `Bash` | `command` | `command`, `description`, `timeout`, `run_in_background` |
| `Read` | `file_path` | `file_path`, `offset`, `limit` |
| `Write` | `file_path`, `content` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string` | + `replace_all` |
| `MultiEdit` | `file_path`, `edits` | `file_path`, `edits` |
| `Glob` | `pattern` | `pattern`, `path` |
| `Grep` | `pattern` | `pattern`, `path`, `glob`, `type`, `output_mode`, `-A`, `-B`, `-C`, `head_limit`, `multiline` |
| `LS` | `path` | `path`, `ignore` |
| `TaskList` | — | (없음) |
| `TaskUpdate` | `taskId` | `taskId`, `subject`, `description`, `status`, ... |

### 교정 로직
1. 필드명 별칭 매핑 (`_move_first_present()`)
2. 타입 강제 변환 (`_coerce_value()`)
3. 누락된 필수 필드 기본값 주입 (`_default_for_missing_required()`)
4. 불필요한 `description` 필드 제거 (일부 툴)
5. `TaskUpdate.status` 정규화 (`normalize_task_update_status()`)

---

## 툴 호출 드롭 조건

`should_drop_emitted_tool_call()`:

- 차단 툴 목록에 포함된 경우
- 필수 필드 누락으로 실행 불가한 경우
- Plan Mode에서 허용되지 않은 툴인 경우
- `PlanMode` 자체 툴(`EnterPlanMode`, `ExitPlanMode`) 처리

---

## Side Effect 중복 제거

같은 툴 호출이 짧은 시간 내 중복 실행되는 것을 방지한다.

- `side_effect_tool_call_dedupe_key()` — 중복 감지 키 생성
- `should_drop_duplicate_side_effect_tool_call()` — 중복 여부 판단
- TTL: `_TOOL_SIDE_EFFECT_DEDUP_TTL_SECONDS` = 10분

---

## MCP 알림 대기 툴 캡

MCP WaitForNotification 계열 툴의 timeout을 제한한다.

- `_mcp_notification_wait_timeout_cap_ms()` — 최대 대기 시간 (ms)
- `_mcp_notification_wait_duplicate_cap_ms()` — 중복 대기 최대 시간
- `cap_mcp_notification_wait_tool_input()` — 실제 입력 값 제한 적용

---

## 외부 Tool Guard 스크립트

`ciel-runtime-tool-guard.py`는 독립 실행 스크립트로,  
Claude Code의 `PreToolUse` / `PostToolUse` 훅으로 설치된다.

설치: `install_tool_guard_hooks()`  
위치 탐색: `find_tool_guard_script()`

---

## Plan Mode와 툴 교통

| 상황 | 동작 |
|------|------|
| Plan Mode 활성 (`plan_mode_active()`) | ExitPlanMode 허용, 실행 툴 차단 |
| `has_plan_mode_exit()` 감지 | ExitPlanMode 툴 호출 처리 |
| bypass permissions 활성 시 | ExitPlanMode 자동 승인 |

---

## 관련 문서
- [[Plan-Mode]] — Plan Mode 상세
- [[MCP-Channels]] — MCP 툴 연동
- [[Providers]] — 제공자별 차단 툴
