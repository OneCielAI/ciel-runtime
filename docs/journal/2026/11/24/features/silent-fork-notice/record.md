---
type: feature-record
title: 응답 없이 멈춘 running 포크를 메인에게 한 번 알림
description: 슬롯을 잡은 running Work 포크의 전사가 30분 넘게 늘지 않으면 블로커 점검이 그 포크를 싣고, 사이드가 stall_notices kind silent로 알리며, 라우터가 다시 확인한 뒤 메인에게 한 번 알리는 기능의 설계, 테스트, 실엔진 검증
tags: [work, side-actor, gate-check, stall-watch, prody, claude-code-2.1.331]
timestamp: 2026-11-24T13:30:00Z
---

# 응답 없이 멈춘 running 포크를 메인에게 한 번 알림

## 요구

- 사용자 2026-11-24: "답이 없이 멈춘다든지에 대해서 상황을 캐치한뒤에 메인에게 공지".
- 배포된 stall watch(943c3e2, `work_gate_check.stalled_forks`)는 parked(`waiting_input`) 포크만 본다.
  - wait: 대기 문구가 12시간 그대로.
  - question: 질문으로 12시간 멈춤.
- 세 번째 경우가 빠져 있었다: lease는 `running`이고 슬롯을 잡았는데 포크 자신의 전사가 더 늘지 않는 경우.

## 관측 (부모 세션의 Prody 모니터링, 이 포크가 직접 재확인하지 않음)

- Prody n1200, lease 6ebe7e68, P1 running, 포크 a9b9ae4c7e.
- 마지막 기록은 Bash tool_use(10:58:19Z, node playwright 스크립트). 57분 넘게 tool_result가 없었다.
- 그 호출의 node/chromium 프로세스는 없었다. 원장은 10:31Z 이후 그대로였다.
- 이 경우는 게이트도 parked 포크도 아니므로 점검은 `no_gates`로 끝난다. 아래 빨간 테스트가 같은 응답을 재현했다.

## 설계 결정

- 진척 출처: 포크 자신의 전사 파일.
  - 원장에는 상태만 있고 lease에 갱신 시각 필드가 없다(`work_parallel.py` 스키마의 base/optional 필드).
  - 네이티브 관찰은 상태만 기록한다.
  - 경로는 `work_fork_api_retry`가 이미 running 포크에 쓰는 해석기를 그대로 쓴다: `<config>/projects/<slug>/<session>/subagents/agent-<host_id>.jsonl`(`_subagents_for`, 라우터에서는 fork-turn watch의 scope).
- 멈춤 판정(`work_silent_forks._silences`)
  - lease가 이 대화의 것, `state == running`, `slot is True`, host_id가 에이전트 id 형식.
  - 아이템과 목표가 done이 아님.
  - 전사 파일 수정 시각이 30분 넘게 지남.
  - 마지막 assistant/user 기록이 턴을 닫지 않음(`agent_turn_events.claude_record_closing_reason`이 None). 닫힌 턴은 fork_turn_watch의 일이고, API 오류 기록은 `error`로 닫혀 fork API 재시도의 일이다.
  - 전사 파일이 없으면 싣지 않는다(증거 없음).
- 임계값 30분
  - Claude Code 공식 문서(env-vars): `BASH_DEFAULT_TIMEOUT_MS` 기본 120000(2분), `BASH_MAX_TIMEOUT_MS` 기본 600000(10분).
  - 30분은 가장 긴 기본 전경 Bash 호출의 세 배다. 상수 `SILENT_MINUTES = 30`.
- 한 번만 알림
  - 한 멈춤의 표지: `lease <id> silent since <마지막 기록 시각>`.
  - 라우터가 알릴 때 아이템 note에 `Side stall notice (event …): <표지> — <checked>`를 남긴다(기존 `STALL_TOLD` 형식).
  - 이후 점검은 그 표지가 note의 어느 stall 기록에든 있으면 싣지 않는다. 기존 wait/question은 마지막 기록만 보지만, silent는 같은 아이템의 다른 알림 뒤에도 다시 알리지 않도록 전체를 본다.
  - 포크가 다시 기록하고 또 멈추면 마지막 기록 시각이 달라져 새 멈춤이다.
- 메인에게 알리는 길(기존 파이프라인 재사용)
  - 게이트 점검 이벤트의 `stalled` 목록에 kind `silent` 행을 더한다. 이벤트에는 "응답 없이 멈춘 포크" 사실 구역이 붙는다(lease, 아이템, 마지막 기록 시각, 몇 분째, 마지막 기록 종류 `tool_use:Bash`, lease 증거, 아이템 progress).
  - 사이드 결정 `stall_notices`에 kind `silent`(waiting_on "")를 허용한다.
  - 라우터 `_record_stalls`는 resolve 시점에 원장과 전사를 다시 읽은 `silent_projection`으로 확인한다. lease가 여전히 running이고 슬롯을 잡았고 여전히 멈춰 있어야 기록한다.
    - 다시 읽기 실패: `the fork transcript was unreadable`.
    - 더 이상 멈춤이 아님, 다른 아이템: `not a silent running worker of this item`.
    - 이미 알림: `already_notified`.
  - 결과 행에는 라우터가 읽은 `since`, `last`가 붙는다. 모드는 `mod_stall_notice`(문구를 kind 중립으로 바꿈: "the stall still holds", "reports each stall once")와 새 관리 텍스트 `mod_stall_silent`로 메인에게 한 번 알린다. 메인이 idle이면 기존 STALL_LEAD 깨우기가 그대로 쓰인다.
- 모델이 읽는 지시는 관리 프롬프트에만 있다: `gate_actor`, `gate_recheck`에 "응답 없이 멈춘 포크" 규칙, `mod_stall_notice`, `mod_stall_silent`(work_texts → texts.generated.js 재생성). 이벤트의 사실 구역 머리말은 기존 `_stalled_lines`/`_parked_lines`와 같이 코드에 있는 사실 표시다. 지시는 없다.
- 전사 내용은 행과 이벤트에 들어가지 않는다. 마지막 기록의 종류와 도구 이름만 쓴다(테스트에서 도구 입력의 `SECRET` 미노출 확인).
- DEFAULT_SIDE_INSTRUCTION은 건드리지 않았다.

## Betty형 호스트(사이드 intake 꺼짐)

- 게이트 점검은 사이드 이벤트를 만들기 전에 `host_only`로 돌아간다(`work_mcp_tools` gate check). 그래서 silent 행은 Betty에 가지 않는다.
- 943c3e2의 wait/question stall watch도 같은 이유로 Betty에 가지 않는다.
- Betty가 받는 것은 그대로다: 호스트 라운드의 재펌프, 우선순위 진단, 진단이 바뀔 때의 dispatch 공지.
- 사이드 없이 호스트가 직접 알리게 할지는 사용자 결정이 필요하다. 이번 변경에서는 하지 않았다.

## 변경 파일

- 신규 `ciel_runtime_support/work_silent_forks.py`: `SILENT_MINUTES`, `transcript_dir`, `silent_forks`, `silent_projection`, `told`.
- `work_gate_check.py`: `_silent_lines`, `_stalled_lines`가 silent 행을 따로 싣는다.
- `work_mcp_tools.py`: 게이트 점검이 silent 행을 `stalled`에 더한다.
- `side_work_decision.py`: `STALL_KINDS`에 `silent`, `_record_stalls`의 silent 검증과 기록.
- `side_intake_mcp.py`: silent 알림이 있을 때 `_parked_projection`이 `silent` 재확인을 붙인다.
- `work_workflow_prompts.py`: `gate_actor`, `gate_recheck` 규칙.
- `work_texts.py`, `claude_mods/ciel-work/hooks/texts.generated.js`: `mod_stall_notice` 문구, `mod_stall_silent`.
- `claude_mods/ciel-work/hooks/side-intake.js`: silent 행 렌더링.
- 테스트: 신규 `tests/test_side_silent_fork.py`(10), `tests/test_side_fork_answers.py`(+1), `tests/side_fork_answer_harness.mjs`(silent 시나리오), `tests/test_side_background_prompts.py`(+1).

## 검증 (확인됨)

모든 실행은 격리했다. `CIEL_RUNTIME_*`를 지웠고, `CIEL_RUNTIME_TEST_ISOLATED=1`과 시스템 임시 폴더 아래의 새 `CIEL_RUNTIME_CONFIG_DIR`을 썼다. 실제 자격 증명은 쓰지 않았다. 운영 호스트, 라이브 라우터, 세션은 건드리지 않았다.

- 빨간 단계 (`evidence/red-before-fix.json`, `evidence/red-head-plus-module.txt`)
  - 수정 전: `test_side_silent_fork.py`는 모듈이 없어 수집 오류가 났다. 모드 silent 시나리오 1건과 프롬프트 2건이 실패했다.
  - HEAD 2303914에 `work_silent_forks.py`만 더했을 때: 10건 중 5건이 실패했다.
    - 이벤트에 "응답 없이 멈춘 포크"가 없다. 대신 행이 "오래 멈춘 포크 … 0.9시간"으로 잘못 나왔다.
    - `invalid side decision stall_notices` 3건.
    - MCP 게이트 점검이 `reason: no_gates`를 냈다. Prody n1200과 같은 응답이다.
- 첫 전체 회귀에서 잡은 것(수정함)
  - `test_architecture_contracts` import 순환: `work_gate_check` ↔ `work_silent_forks`. 기준 분 수를 행에 싣게 바꿔 순환을 없앴다.
  - `test_work_gate_check` quarantine 테스트: `work_side_fail` 답이 사건 본문을 담는다. 이번에 gate_actor 규칙이 늘어 8,000자 페이지 기준을 넘었고, 라우터가 그 답을 페이지로 나눴다(`{"paged": …, "chars": 8084}`). 모드는 원래 모든 라우터 답을 `hostJson`으로 다시 합친다. 테스트만 페이지를 합치지 않았다. 테스트가 모드와 같이 합치도록 고쳤다.
- Windows 최종(136개 파일, 파일마다 격리): 2732 통과 / 2 실패 / 201 skip (`evidence/tests/windows-final-*.json`).
  - 실패 2건은 `test_router_debug::test_router_recognises_its_own_health_payload`와 `test_unisolated_test_guard::test_cli_asset_paths_follow_isolation`이다.
  - 같은 두 건이 HEAD 2303914 트리에서도 같은 방식으로 실패한다(`evidence/tests/windows-head-2303914-same-failures.json`). 이번 변경이 만든 실패가 아니다. 원인은 조사하지 않았다.
- `test_work_mod_real_backend.py` Windows 단독 실행(2400초 제한): 117 통과 / 0 실패 / 3 skip, 1782초 (`evidence/tests/windows-real-backend.json`). 첫 전체 회귀에서는 900초 제한에 걸려 시간 초과였다.
- Linux(기존 컨테이너, 태그 `silentf.124934`, `current-tag.txt`는 건드리지 않음)
  - `ciel-runtime-test-py310`: 135개 파일 2825 실행 / 0 실패 / 0 오류 / 246 skip. bridge 1/0. real_backend 120/0/4.
  - `ciel-runtime-test-py311`: 135개 파일 2825 실행 / 0 실패 / 0 오류 / 244 skip. bridge 1/0. real_backend 120/0/4.
  - 첫 태그 `silent.122632`에서 `test_wait_boost_pytest_bridge`가 실패했다. 이 테스트는 docs/journal의 증거 파일이 필요하다. 11-23 `stall.225404`도 같은 이유로 실패했고, 증거를 넣은 bridge tar에서 통과했다. 최종 태그에는 그 증거 파일을 넣었고 통과했다.
- 대상 테스트: `test_side_silent_fork` 10, `test_side_stall_watch`, `test_side_fork_answers`(silent 시나리오 포함), `test_side_background_prompts`, `test_work_texts`, `test_work_gate_check`, 아키텍처 2종 모두 통과.
- ruff 0.15.22 `check`(변경한 .py 전부) 통과. compileall 통과. JS 3개는 `node --input-type=module --check` 통과. `npm run check:docs` 통과(30개 Markdown).
- `claude plugin validate`(ciel-work) 통과. `claude plugin test` 94 통과 / 0 실패.
- 실엔진: claude.exe 2.1.331, 소유 scratch, 가짜 Messages API (`evidence/real-run2/`)
  - `passed: true`, 아홉 판정 모두 참.
  - 원장: lease `running`, 슬롯 잡음, 소유자는 살아 있는 드라이버 프로세스. 포크 전사의 마지막 기록은 57분 전 Bash tool_use다. 엔진 자신의 세션 전사도 같은 slug 경로에 생겼다(`engine_session_transcript_at_slug: true`).
  - 63.1초: 호스트 게이트 타이머가 `stalled: [lease]`로 이벤트를 큐에 넣었다. 사이드가 받은 이벤트에 "응답 없이 멈춘 포크 (호스트가 … 30분 넘게 늘지 않음)", "58분째 그대로", "마지막 기록: tool_use:Bash"가 있었다.
  - 65.4초: resolve가 kind silent를 기록했다(`since`, `last`는 라우터가 다시 읽은 값). 보드 note: `Side stall notice (event f7a065b1f714): lease … silent since 2026-11-24T11:53:04Z — …`.
  - 67.2초: 메인 요청에 알림 한 건이 들어왔다. "is running with its slot held, but its transcript has not grown since 2026-11-24T11:53:04Z (last record: tool_use:Bash). Checked: … Needs: …".
  - 이후 점검(65.6초 큐 1건 `stalled: []`, 69.6·124.8·185.6초 `no_gates`)은 그 포크를 다시 싣지 않았다. 알림은 1건이었다. lease는 그대로 running이었다(알림만 하고 바꾸지 않음).
  - scratch를 지웠다. 프로필 해시는 `~/.claude.json`만 바뀌었다. `~/.claude/settings.json`은 그대로다. `~/.claude.json`은 실행 중인 다른 Claude 세션도 쓰는 파일이라 참조 드라이버와 같이 제외했다.
  - 1회차(`evidence/real-run1/`)는 제품 동작이 2회차와 같았다. 그러나 드라이버의 이벤트 판별 문자열이 gate_actor 규칙 머리말 "응답 없이 멈춘 포크 (이벤트의…"와도 맞아서, 증거 구역을 규칙 쪽에서 잘라 `event_text_has_silence_facts` 하나가 거짓이었다. 사실 구역 머리말("응답 없이 멈춘 포크 (호스트가")로 판별을 고쳐 2회차를 돌렸다.
  - 1회차 결과의 두 번째 resolve(67.1초)는 같은 멈춤에 대한 사이드의 두 번째 알림이다. 라우터가 `already_notified`로 거절했다. 중복 거절이 실엔진에서도 동작한 것이다.

## 확인 안 됨, 남은 것

- 운영 호스트의 실효과: 배포·설치하지 않았다(포크 지시). Prody에서의 실제 알림은 관측하지 않았다.
- Betty형 호스트(사이드 꺼짐)는 받지 않는다. 위 절 참고. 사용자 결정 필요.
- Codex 대화(`codex:` scope)는 전사 해석기가 없어 대상이 아니다.
- 전경 호출이 30분을 넘는 정상 작업: `BASH_MAX_TIMEOUT_MS`를 30분 넘게 올린 호스트, 포크 안의 긴 중첩 Agent 호출은 알림이 갈 수 있다. 사이드 확인과 메인 판단에 맡긴다. 각 호스트의 설정값은 확인하지 않았다.
- 전사 파일이 아직 없는 running lease(실행이 기록을 남기지 못함)는 대상이 아니다.
- 호스트에 `gate_actor`/`gate_recheck` 관리 프롬프트 재정의가 저장돼 있으면 새 규칙이 그 액터에 가지 않는다. 각 호스트의 재정의 여부는 확인하지 않았다.

## 배포 (2026-11-24)

- 0947231을 `feature/priority-models`에 cherry-pick했다(9a46b96). 코드는 0947231과 같다. 그 사이 커밋은 문서뿐이다.
  - cherry-pick 뒤 핵심 7개 파일을 다시 돌렸다: 109 통과 / 12 skip.
  - ruff 0.15.22와 `check:docs`도 통과했다.
- `feature/priority-models`와 `beta`를 9a46b96으로 push했다. `git ls-remote`로 확인했다.
- Publish to npm 45404482988: 성공. beta `0.2.51-beta.20261124-133238.9a46b96`, gitHead 9a46b96.
  - tarball의 변경 파일 9개 해시가 오버레이 new와 같다.
- 해시 확인 오버레이 `overlay-9a46b96`(9개 파일, base 4886d62)
  - 설치 전 상태는 모든 루트에서 같았다: 기존 8개는 base, 새 모듈 `work_silent_forks.py`는 없음.
  - 설치 후에는 new 9/9였다.

  | 대상 | 설치 시각 |
  |---|---|
  | 로컬 hotfix5, `.local/share/ciel-runtime`, npm 전역 | 13:38:34Z |
  | Celly(관리자 권한) | 13:38:47Z |
  | KOBE | 13:39:24Z |
  | Prody | 13:39:54Z |
  | Betty | 13:40:27Z |

- 라이브 효과(파일 설치와 구분)
  - Python 변경은 라우터를 다시 시작해야 반영된다. `side-intake.js`·`texts.generated.js`는 세션이 모드를 다시 읽어야 반영된다.
  - 운영 라우터와 세션은 재시작하지 않았다. 그래서 어느 호스트에서도 아직 라이브가 아니다.
  - Prody n1200(13:12Z 기준 135분 무응답)도 라우터 재시작 전에는 이 알림을 받지 않는다.
- 남은 결정(사용자): Betty처럼 side intake가 꺼진 호스트는 이 알림을 받지 않는다. 943c3e2의 wait/question 알림도 같다.

