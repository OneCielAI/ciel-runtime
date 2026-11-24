---
type: fix-record
title: side 액터·side-fresh 중복 실행, 문맥, 대기 해제 개선
description: 리스 만료 때 같은 이벤트를 두 액터가 처리하던 결함 두 경로, fresh 액터가 메인의 결정을 볼 수 없던 문맥 결함, 묶음 노드 대기를 해제하지 못하던 결함, 게이트 판정 규칙을 고친 기록과 검증.
tags: [work-mode, side-intake, side-fresh, gate-check, lease, prompt, andy]
timestamp: 2026-11-23T21:40:00Z
---

# 사용자 요구 (2026-11-23)

- side-fresh가 중복 실행되지 않게 한다.
- side 액터와 side-fresh가 배경을 조사해 대기 해제 조건을 제대로 판단하고, 해제 조건이면 워크보드를 갱신한다. 배경은 원격 Walkie의 관련 스레드, 로컬 관련 id의 워크보드 기록, 최신 대화 기록이다.
- 해결은 프롬프트 강화가 중심이다.

# 관측 (Andy, 읽기 전용, 22:40–23:05Z, `docs/journal/2026/11/20/operations/live-monitoring/log.md`)

- 22:01:24–22:06:15Z 라우터에 `work_side_claim`·`work_side_renew`가 0건이었다. 22:06:17Z에 작업 중이던 side 액터 3개가 `[Request interrupted by user]`로 끝났다. 같은 이벤트를 새 액터가 처음부터 다시 처리했다(40d33d66e413, 51a377278167).
- Andy는 `work_side_intake.session="fresh"`다. fresh 액터는 빈 대화로 시작한다. 그런데 지침은 "메인의 컨텍스트를 그대로 물려받는다"고 했다. workload에는 note·progress·evidence가 없었다.
- 게이트 재확인 305회 중 해제는 4건이었다. 요약에 "미조회"가 반복됐다. n2582는 메인이 progress에 범위를 좁혀 둔 상태에서 더 넓게 해제됐다.
- n2296·n1675: side가 신호를 확인하고 해제를 요청했다. 묶음 노드라 `non_leaf_wait`로 거부됐고, 아무도 메인에게 알리지 않았다.

# 원인과 수정

## 1. 리스 만료 후 같은 이벤트를 두 번 실행 (두 경로)

- 경로 A (renew):
  - `pollSide`는 남은 리스가 없으면 renew를 건너뛰고 액터를 멈췄다(`retire`).
  - 라우터 `renew`도 만료된 리스를 거부했다(`verify_claim(now=stamp)`).
  - 수정: 만료돼도 renew를 먼저 보낸다(`side-intake.js`). 라우터는 다른 claim이 이벤트를 가져가지 않았으면 리스를 되살린다. 조건은 같은 generation, stage `received`, 격리 아님이다(`side_intake_processing.renew`).
- 경로 B (claim):
  - 멈춘 claim 호출이 만료 뒤에 라우터에 닿으면, 라우터는 같은 이벤트를 새 generation으로 다시 내줬다. 호스트는 두 번째 액터를 띄웠다.
  - 수정 1: 같은 owner가 다시 claim하면 같은 generation을 돌려준다(`side_intake_processing.claim`).
  - 수정 2: 호스트는 자기 액터가 이미 그 generation을 들고 있으면 리스만 갱신하고 새 액터를 띄우지 않는다(`side-intake.js pollSide`).
  - 다른 owner의 claim은 지금처럼 새 generation을 받는다.

## 2. fresh 액터의 문맥

- `work_status root_id=<목표> detail=true`는 format=json이 없으면 텍스트 트리를 돌려줬다. 텍스트 트리에는 note, progress, waiting_on이 없다. side 지침과 workload 안내가 바로 이 호출을 쓰라고 했다.
  - 수정: detail=true이면 그 목표의 노드 전체를 JSON으로 돌려준다(`work_mcp_tools.py`). 도구 설명이 이미 약속한 동작이다.
- spawn 프롬프트에 두 필드를 넣었다(`side-intake.js`).
  - `session_mode`: `fork` 또는 `fresh`
  - `main_transcript`: `$.session.info().transcriptPath`
- workload 선택을 고쳤다(`compactWorkload`).
  - 전에는 단수 `ref`만 봤다. 이제 `refs` 배열도 본다.
  - 이벤트의 Walkie id(대상 글, 스레드 루트, 부모, 대화)와 정확히 같은 ref를 고른다.
  - 고른 항목과 포크가 작업 중인 항목(최대 12개)에는 note·progress·evidence 끝부분 400자를 싣는다. 조상 노드는 간단히 둔다.
- 게이트 재확인 이벤트의 게이트마다 세 줄을 추가했다(`work_gate_check.py`): 아이템 progress·note 끝부분 300자, 마지막 갱신 시각.

## 3. 묶음 노드 대기 해제

- side가 목표나 묶음 노드의 대기도 해제한다(`side_work_decision.py`).
- 바뀌는 것은 그 노드뿐이다. 하위 항목의 자기 대기와 상태는 그대로다.
- 스케줄 부스트(wait_boost)는 리프에만 만든다.
- 해제 알림은 그 아래 리프에서 일하는 포크에 간다(`notifyReleased`). 포크가 실행 중이면 메시지를 받고, 대기 중이면 재개된다. 자기 대기가 남은 리프의 포크는 재개하지 않는다.

## 4. 지침 (관리 프롬프트)

side 지침(`DEFAULT_SIDE_INSTRUCTION` = `side-intake.js PROMPT`), `gate_actor`, `gate_recheck`를 고쳤다.

- 세션 모드: fork는 메인 대화를 물려받았고, fresh는 빈 대화로 시작했다고 명시한다. "그대로 물려받습니다", "이미 컨텍스트에 있습니다"는 지웠다.
- 배경 조사(필수):
  - workload의 기록 끝부분을 먼저 본다.
  - 부족하면 `work_status root_id=<목표> detail=true`를 부른다.
  - `main_transcript`를 Grep으로 찾는다. 순서는 count, 그다음 `-o`와 `".{0,300}<id>.{0,300}"`로 마지막 10건 이내다. Read로 통째로 읽지 않는다.
  - 보드·대화 기록이 이벤트와 어긋나면 기록이 우선이다.
- 게이트별 판정: 게이트마다 "해제: 근거", "유지: 조회한 것과 미충족 신호", "확인 불가: 시도한 조회와 이유" 중 하나를 적는다. "미조회"와 근거 없는 "변화 없음"은 판정이 아니다.
- 해제 범위: 승인이 대기 문구의 범위 전체를 충족할 때만 해제한다. 메인이 범위를 좁히거나 보류한 기록이 있으면 유지한다.
- 묶음 노드 대기도 같은 규칙으로 해제한다.

# 검증

## 실패 테스트 먼저 (수정 전 RED)

- `test_side_work_decision`:
  - `test_an_expired_claim_nobody_took_is_revived_by_its_own_actor`
  - `test_the_same_host_reclaiming_its_lapsed_event_gets_its_own_claim_back`: 수정 전 generation이 달랐다.
- `test_side_intake_mod`:
  - `test_a_lapsed_lease_of_a_working_actor_is_renewed_not_claimed_twice`: revived·refused·reoffered 세 경우. reoffered는 수정 전 spawn 2였다.
  - `test_a_side_actor_gets_its_session_mode_transcript_and_the_items_records`: 수정 전 `KeyError: 'session_mode'`.
- `test_work_mcp_tools.test_a_goal_detail_read_carries_notes_and_progress_without_format_json`: 수정 전 JSONDecodeError(텍스트 트리).
- `test_wait_boost_lane`:
  - `test_a_verified_group_wait_is_released_without_touching_descendants`
  - `test_a_released_group_lets_its_unwaited_leaves_run`: 수정 전 non_leaf_wait 거부.
- `test_work_gate_check.test_each_gate_carries_its_latest_progress_note_and_update_time`: 수정 전 `KeyError: 'progress'`.
- `test_side_release_notice.test_a_released_group_wait_reaches_the_forks_of_its_leaves`: 수정 전 resumeCalls `[]`.
- `test_side_background_prompts` (5개): 수정 전 지침에서 실패.
- 출력: `.local/tmp/side-bg/red.txt`, `red-reoffer.txt`(작업 트리 밖 증거는 아래 결과 요약에 옮겼다).

## 실엔진 실행 1 — 하네스가 못 잡은 두 결함

- 드라이버: [run_side_background_real.py](evidence/e2e-source/run_side_background_real.py).
  - 구성: Claude Code 2.1.330 실제 로드, 실제 Work·side 디스패치, 스크립트 대역 Messages API, 격리 설정, 실제 자격 증명 없음, `D:/Temp` 스크래치.
  - 흐름: side 이벤트 하나를 넣는다. 액터 응답을 붙잡아 둔다. claim·renew 호출을 claim 뒤 140초까지 MCP 서버에서 붙잡아 120초 리스를 넘긴다. 그다음 답을 풀어준다.
- 결과: [run1-mcp-timeout-stop](evidence/run1-mcp-timeout-stop/result.json), `passed: false`.
  - 라우터는 같은 generation을 돌려줬다(`claim_kept_its_generation: true`).
  - 그런데 액터가 2개였고 첫 액터가 멈췄다.
- 원인(타이밍으로 확인):
  - Claude Code의 MCP 클라이언트는 응답 없는 호출을 약 60초에 포기한다. 12.1초에 보낸 claim은 약 72초에, 74.1초에 보낸 renew는 약 134초에 실패했다.
  - renew가 실패한 뒤 리스가 지나 있었고, 펌프가 134.4초에 TaskStop으로 일하던 액터를 멈췄다. 같은 이벤트의 다음 claim이 두 번째 액터를 띄웠다.
  - 라이브 Andy의 5분 라우터 정지도 같은 경로를 탈 수 있다.
- 수정 1: 리스가 지난 액터는 라우터가 renew를 명시적으로 거부했을 때만 멈춘다. 거부 문구는 `side receipt missing`, `stale side claim`, `side claim expired`, `side claim already settled`, `side claim quarantined`다. 응답이 없으면 액터를 유지한다. 완료는 generation으로 막혀 있고, 라우터는 같은 owner에게 같은 claim을 돌려준다.
  - 실패 테스트 먼저: `side_lease_revival_harness.mjs timedout`, 수정 전 spawn 2와 `TaskStop:actor1`.
- 수정 2: 결함 하나가 더 있었다. 연결된 리프가 refs·note는 갖고 액터에 갔지만 progress는 없었다. 라우터 workload 투영(`side_workload.project`)에 progress가 없었다. 하네스는 workload를 직접 넣어서 이걸 가렸다.
  - 미완료 노드의 progress 끝 400자를 싣는다. Andy 보드(531 노드)에서 투영은 433,669 → 462,312자(+6.6%)가 된다. progress 전체를 실었다면 +15%였다.
  - 실패 테스트 먼저: `test_the_claimed_workload_carries_mains_progress_records`, 수정 전 progress `None`.

## 실엔진 실행 2·3 — 수정 트리와 수정 전 HEAD

같은 드라이버, 같은 흐름이다. MCP 정지는 claim 뒤 140초까지 걸었다.

| 검사 | 수정 트리 [run2-fixed](evidence/run2-fixed/result.json) | HEAD 072313c [run3-head-072313c](evidence/run3-head-072313c/result.json) |
|---|---|---|
| 이벤트 하나에 액터 수 | 1 | 2 |
| 일하던 액터 정지 | 없음 | 정지됨 |
| 이벤트 claim generation | 3번 claim 모두 같음 | 3번 claim이 모두 다름 |
| 처리 완료 시각 | 165.8초 | 270.9초 |
| spawn 프롬프트 `session_mode`, `main_transcript` | `fresh`, 세션 파일 경로 | 없음 |
| 연결 리프의 progress 전달 | 전달됨 | 없음 |
| 묶음 노드 대기 해제 | 해제(`released_waits` n2) | `non_leaf_wait` 거부 |
| 자유 리프가 실행 후보 | 예 | 아니오 |
| 하위 리프 상태 불변 | 예 | 예 |
| 종합 | `passed: true` (13/13) | `passed: false` (8개 실패) |

- 공통 조건:
  - 라이브 `~/.claude/settings.json`과 ciel-runtime bin은 불변이었다.
  - 스크래치는 삭제했다.
  - 실제 자격 증명은 쓰지 않았다.
- 증거 파일 정리: run1·run2 `result.json`의 `source`에 남은 JSON 이스케이프된 홈 경로를 `<home>`으로 가렸다. 드라이버도 같은 마스킹을 하도록 고쳤다(판정 로직은 그대로다).

## cf55ed2 회귀

- 영향 62개 파일(Windows 격리, 파일마다 격리 프로세스):
  - 981 통과 / 0 실패 / 50 skip.
  - `test_work_mod_real_backend`는 단독 117/0/3, 약 25분 걸렸다(15분 제한에 걸렸던 첫 실행은 시간 초과였지 멈춤이 아니었다).
- py310·py311: 62개 파일 모두 실패 0. `test_work_mod_real_backend` py311 120/0/4.

# 2단계 — 오래 멈춘 포크 알림, 실행 중 포크로의 직접 전달 (사용자 2026-11-24)

## 사용자 요구

- side 액터는 블로커가 있는 상황을 잡아서 메인에게 알린다. 해당 상황은 세 가지다.
  - 에스컬레이션이 필요하다.
  - 메인이나 포크가 특별한 노력 없이 기다리기만 한다.
  - 답 없이 멈췄다.
- 사용자가 특정 상태를 묻거나 사양 변경을 요청했는데 그 항목을 실행 중인 포크가 있으면, 포크에게 바로 넘겨서 포크가 대신 답하거나 조치하게 한다.

## 관측 (Andy, 읽기 전용, 2026-11-24 04:2xZ)

- 열린 `waiting_input` 리스는 4건이다. 그중 2건은 리스가 36–48시간 됐다.
  - n2594: 41.6시간, "Production fix for /api/alerts 57014 deployed"
  - n2577: 48.9시간
- 대기가 있는 미완료 노드는 156개다. 그중 125개는 24시간 넘게 갱신이 없다.
- 최근 외부 이벤트 305건(11-22 이후)의 처리 결과:
  - `relay` 32건
  - 실행 중 포크가 직접 답한 `fork_responds` 10건
  - 나머지 206건은 side가 직접 답했다.

## 수정

- 오래 멈춘 포크 목록(`work_gate_check.stalled_forks`)
  - 이 대화의 포크 중 `waiting_input`인 것을 골라 대기 노드의 `updated_at`을 기준으로 잰다(질문 대기는 리스 생성 시각). 12시간(`STALL_HOURS`) 이상이면 목록에 넣는다.
  - 대기 노드 note에 같은 대기 문구로 "Side stall notice"가 이미 기록돼 있으면 빼고, 대기 문구가 바뀌면 다시 넣는다.
  - 게이트 재점검 이벤트에 "오래 멈춘 포크" 절로 실린다. 한 라운드에 최대 6개이고, 나머지는 다음 라운드에 이어서 싣는다.
  - 다이제스트에 포함되므로, 이 목록만 있는 보드도 점검한다.
- 결정 필드 `stall_notices`(`side_work_decision`)
  - side가 대기를 다시 조회해서 여전히 미충족이면 넣는다.
  - 라우터 검증 조건:
    - 원장 사전 읽기에서 그 아이템에 parked된 이 대화의 포크인지 확인한다.
    - 대기 문구가 지금 그대로인지 확인한다.
    - 이미 알렸으면 `already_notified`로 거부한다.
  - 통과하면 그 노드 note에 한 줄을 기록한다. 대기마다 한 번만 기록된다.
  - 필드를 광고하지 않는 옛 라우터에게는 이 필드를 보내지 않는다. `LEGACY_DECISION_FIELDS`를 고정 목록으로 바꿨다. 전에는 `fork_answers`가 새고 있었다.
- 메인 알림
  - 관리 텍스트 `mod_stall_notice`로 알린다.
  - receipt당 한 번 나가는 기존 알림에 함께 실린다.
  - 메인이 idle이면 깨우기 프롬프트가 이 알림을 가리킨다(`STALL_LEAD`).
- 지침
  - `gate_actor`와 `gate_recheck`에 "오래 멈춘 포크" 규칙을 넣었다: 다시 조회하고, 충족이면 해제, 미충족이면 `stall_notices`. 조회하지 않고는 넣지 않는다.
  - 실행 중 포크로의 직접 전달:
    - side 지침과 `side_running_fork_relay`에서, 실행 중 포크가 있는 항목의 상태·진행·사양 변경 요청은 `relay` + `no_reply`로 그 포크에게 넘긴다.
    - "단순한 상태 질문은 전달하지 않는다"는 문구는 지웠다.

## 실엔진이 잡은 결함 두 개 (하네스는 통과했다)

- 실행 4([run4-stall-notice-refused](evidence/run4-stall-notice-refused/result.json)):
  - 라우터 쪽 네 검사는 모두 통과했다: 이벤트에 포크 표기, 결정 커밋, 보드 기록, 재목록 없음.
  - 그러나 메인 알림이 0건이었다.
  - 원인: 라우터의 알림 claim(`side_notice._released_row`)이 `released_waits`, `relay`, `ask_main`, turn_end만 받고 `released-wait receipt required`로 거부했다. 하네스는 claim을 흉내 내서 이걸 가렸다.
  - 같은 경로에서 `fork_answers`·`unanswered_forks` 단독 알림(전달 실패한 포크 답, 11-23 배포)도 같은 이유로 막혀 있었다.
  - 수정: 두 결과도 claim할 수 있게 했다. 실패 테스트 `test_a_stall_notice_receipt_can_claim_its_one_notice`를 먼저 작성했고, 두 경우 모두 수정 전 거부였다.
- 같은 실행에서 깨우기 프롬프트가 "a host message beginning "Ciel side released recorded wait""를 가리켰다.
  - 그 머리말로 시작하는 메시지는 없다. 11-18에 release 알림이 "Ciel side: released …"로 바뀐 뒤 `RELEASE_LEAD`만 그대로였다.
  - 그래서 idle 메인은 이미 있는 release 알림도 엉뚱한 문구로 안내받고 있었다.
  - 실패 테스트(release·stall 둘 다)를 먼저 작성한 뒤 머리말을 고쳤다.
- 실행 5([run5-stall-notice](evidence/run5-stall-notice/result.json)): `passed: true`, 7/7.
  - 64.1초에 결정이 커밋됐고 65.1초에 메인 요청에 알림이 들어갔다.
  - 알림 원문: "Ciel side: stalled Work fork (event …). The side re-checked it and the wait still holds: - n2 (lease …) waits on "production release 0.4.509 deployed". Checked: release.json says 0.4.508 (isolated check). Needs: deploy 0.4.509 or decide the order …".
- 드라이버 메모: 격리 규칙은 시스템 temp 아래 설정 디렉터리만 받는다(`runtime_paths`). 그래서 드라이버는 `D:/Temp`가 아니라 프로세스가 실제로 고른 `CONFIG_DIR`에 원장을 심는다.

## 문서 링크

- `docs/Module-Map.md`의 removal-waiver 링크가 `/docs/...`로 시작해 `npm run check:docs`가 실패하고 있었다(3d475f9부터).
- 상대 경로로 고쳤다. beta push는 Publish의 quality 잡을 건너뛰기 때문에 배포 전에 직접 돌려서 찾았다.

## 회귀 (최종 트리)

- Windows 격리(파일당 독립 프로세스, `CIEL_RUNTIME_*` 해제, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`):
  - 영향 139개 파일: 137개 통과, 2777 통과 / 2 실패.
  - 실패 2건은 모두 기준 커밋 `072313c`에 이미 있던 pytest 전용 실패다(`test_router_debug.py::RouterDebugAccessTests::test_health_includes_debug_flag`, `test_unisolated_test_guard.py::IsolatedCliAssetTests::test_cli_asset_paths_follow_isolation`). unittest 실행기에서는 둘 다 통과한다.
  - `test_work_mod_real_backend.py`: cf55ed2에서 117/0/3. 최종 트리 재실행은 백그라운드에서 진행 중이다.
- 기존 Linux 컨테이너(격리):
  - `ciel-runtime-test-py310`: 139개 파일 전부 실패 0.
  - `ciel-runtime-test-py311`: 139개 파일 전부 실패 0.
  - `ciel-runtime-test-py311` `test_work_mod_real_backend.py`: 120 통과 / 0 실패 / 4 skip.
- 정적 검사·패키징·모드 검사:
  - `ruff 0.15.22`, `python -m compileall`, `npm run check:docs`, `npm pack --dry-run` 통과.
  - `claude plugin validate` 통과, `claude plugin test` 94 통과 / 0 실패.
