# Plan Mode — Plan Mode 처리

> 소스: `ciel_runtime.py` — `plan_mode_active()`, `should_auto_enter_plan_mode()`, `plan_mode_tool_name_for_emit()`

---

## 개요

Claude Code의 **Plan Mode**는 실행 전에 계획을 작성하고 승인받는 워크플로우다.  
Router는 Plan Mode 진입/탈출 툴을 가로채어 비-Anthropic 제공자에서도 작동하도록 처리한다.

---

## Plan Mode 감지

### `plan_mode_active(body)`
현재 요청이 Plan Mode 활성 상태인지 판단:
- 시스템 프롬프트에 plan 마커 포함 여부 확인
- 첨부파일에 plan 파일 경로 포함 여부

### `has_plan_mode_exit(body)`
응답에 ExitPlanMode 툴 호출이 포함되어 있는지 감지.

---

## Plan Mode 자체 툴

```python
PLAN_MODE_SELF_TOOLS = ("EnterPlanMode", "ExitPlanMode")
```

이 툴들은 Anthropic 전용이며 비-Anthropic 모델의 툴 목록에서 처리 방식이 다름.

---

## Plan Guard 마커 (Stop 가드 제거됨)

```python
PLAN_GUARD_MARKER = "[ciel-runtime-plan-guard]"
```

예전 tool-guard `Stop` 훅은 Plan Mode에서 짧은 답만 하고 멈춘 턴을 막고, 이 마커가 붙은
"Ciel Runtime plan guard" 지시를 메인 컨텍스트에 넣어 턴을 이어 갔다. 이 훅은 2026-11-17에
제거됐다. 이유는 두 가지다. 집중해야 할 일과 무관한 지시가 메인 컨텍스트에 들어갔고,
메인의 유휴 상태는 Work 사이드 액터가 살핀다.
- 실행 시 tool-guard를 `Stop`에 등록하지 않는다. 기존 `settings.json`에 남은 tool-guard `Stop`
  항목은 다음 실행 때 지워지고, 다른 `Stop` 훅은 그대로 남는다.
- 이전 빌드의 설정이 아직 `Stop`에서 이 스크립트를 부르더라도 아무 결정도 내놓지 않는다.
- 마커는 이전 대화에 남은 지시를 사용자 의도에서 걸러 내는 데만 쓴다(`conversation_turn_policy`).
- ExitPlanMode 자동 승인(bypass 시 `PermissionRequest`·`PreToolUse`)은 그대로다.

---

## ExitPlanMode 처리 흐름

```
1. 비-Anthropic 모델이 ExitPlanMode 호출
2. plan_mode_tool_name_for_emit() — 호출 가능 여부 판단
3. allowed_prompt_tools_for_exit_plan_mode() — 허용 툴 목록
4. backfill_exit_plan_mode_allowed_prompts() — 허용 프롬프트 추가
5. bypass permissions 활성 시 자동 승인
```

---

## 자동 Plan Mode 진입

`should_auto_enter_plan_mode(body, response_text, tool_calls)`:

구현 계획 요청처럼 보이는 경우 자동으로 Plan Mode 진입:
- `likely_implementation_planning_request(text)` — 구현 계획 요청 감지
- `non_actionable_short_response(text)` — 비실행 단답 응답 감지
- 최근 사용자 의도 분석

---

## 계획 파일 처리

`latest_plan_attachment(body)` — 최신 plan 첨부파일 추출.  
`plan_file_written_in_body(body, plan_file_path)` — plan 파일 작성 여부 확인.

---

## ROUTED_COMPAT_PROMPT

비-Anthropic 제공자에 주입되는 시스템 프롬프트 일부:

```
...
In Plan Mode, first explore/read as needed, write or update the plan file named
by the plan_mode attachment, and only then call ExitPlanMode to leave Plan Mode;
when bypass permissions is active, ciel-runtime auto-approves that plan exit,
so do not ask the user separately and do not call EnterPlanMode again.
...
```

---

## 관련 문서
- [[Tool-Guard]] — 툴 차단 및 ExitPlanMode 처리
- [[Router]] — 시스템 프롬프트 주입
- [[Thinking-Passthrough]] — Plan Mode와 thinking
