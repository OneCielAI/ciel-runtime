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

## 회귀 (최종 트리)

결과는 실행 후 아래에 채운다.
