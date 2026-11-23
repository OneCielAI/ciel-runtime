---
type: fix-record
title: P0 레인 정지·재시작 후 P0 소실·Work 도구 로그 누락
description: Celly에서 P0 intent 상한 포화로 P0 레인이 멈춘 결함(A), 세션 계보가 바뀌면 P0 marker가 따라오지 않던 결함(B), 라우터 로그가 Work 도구에 전달되지 않던 결함(C)의 증거, 수정, 검증, 미배포 상태.
tags: [work-mode, p0, durability, celly, andy, router-log]
timestamp: 2026-11-23T08:45:00Z
---

# 배경

사용자 지적(2026-11-23): P0는 긴급이고 그 데이터는 재시작에도 반드시 보존돼야 한다. 땜질 코딩은 금지.
P0를 보드상 P1과 사이드카 marker(`work-parallel-p0.json`, 키 `scope(세션 id)+root_id`)로 구현한 것은 2026-11-14 에이전트 설계(d69ca83·f045f24)였고, 사용자 지시가 아니었다(`docs/journal/2026/11/14/features/priority-p0-fork-models/requirement.md:96-99`).

# A. P0 intent 상한 포화로 새 P0 불가 (Celly)

- 관측(읽기 전용): Celly `work-parallel-p0.json` intents 256건 = `MAX_INTENTS`. 구성 terminal 137, bound 106, intent 13, cancelled 0. 모두 이전 세션 scope였고 현재 scope 4ee83c21은 0건(07:06Z, 08:13Z 재확인 동일).
- 원인: `_create_intent_mutation`은 상한에서 cancelled만 지웠다. cancelled가 0이면 새 P0 intent는 매번 `P0 intent bound`로 거부됐다. 실효 P0 아이템(n1350·n1360·n1361·n1363)은 P1 `work-auto-*` 리스로 떨어져 P1 슬롯 한도에 막혔다(`active slot capacity reached for P1`, 06:45Z~08:06Z 반복).
- 수정: `work_parallel_p0.evictable_intents`(`:57`). 상한에서 cancelled 다음으로, ledger상 리스가 없거나 `done`·`orphaned`(`RETIRED_LEASE_STATES`)인 intent를 오래된 순으로 축출한다. 조건: `updated_at < observed_at`(읽은 원장 스냅샷 이전에 바뀐 행만), 열린 reacquire send의 부모 제외, unreported 보존. `create_intent`가 원장 상태를 읽어 넘긴다(`_ledger_lease_states`).
- 실패 테스트 먼저: `test_full_p0_journal_of_retired_launches_still_admits_a_new_p0`(수정 전 `P0 intent bound`), `test_full_p0_journal_keeps_evidence_that_can_still_authorize`, `test_retired_intent_selection_is_exact`.
- Celly 상태 임시 사본 건식 적용: 구 코드 `P0 intent bound`, 신 코드 생성 성공, 축출 후보 181건.

# B. 세션이 바뀌면 P0가 P1로 떨어짐 (Celly 보고)

- 관측: Celly marker 43개(active 42, cleared 1) 중 39개가 현재 보드 루트(id·created_at 동일)와 일치하지만 세션 id 5개에 흩어져 있다. 보드는 native parked 계보로 이어지지만 marker 키는 세션 scope라서 새 세션에서는 보이지 않았다. 예: n1179·n1209는 이전 세션에서 P0였고 현재는 P1. marker는 삭제되지 않았다 — 데이터는 남았지만 새 세션이 읽지 못했다.
- 수정:
  - `work_session_lineage.lineage_scopes`(`:56`): 같은 보드를 잇는 정체성 집합. marker는 자기 세션 digest 폴더에 있고 authority가 `native-parked-handoff`일 때만 신뢰한다.
  - `WorkParallelP0Store.adopt_lineage_markers`(`:573`): 계보 내 다른 정체성의 결정 중 최신(active는 `set_at`, ended는 `ended_at`)을 가져온다. 같은 생성 세대의 미완료 루트만, 현재 세션 자신의 행이 우선, 원래 `set_at`/`set_by` 유지, 완료된 op 행(id `sha256("ciel.p0.lineage-adopt.v1"…)[:32]`)을 남긴다.
  - `work_parallel_mcp._adopt_lineage_p0`(`:1551`): 호스트 검증된 `work_parallel_status` 읽기에서 호출(`:4131`).
- 테스트(`tests/test_work_p0_lineage.py`): 계보 경계, 최신 결정·자기 행 우선, 단독 세션은 채택 없음, 호스트 상태 읽기 후 P0 레인이 실제로 발사.
- Celly 사본 건식 적용: n1179·n1209 채택.
- 미확인: `sweep_roots`·`recover`에는 운영 호출자가 없다. 이번 수정 범위 밖이며 별도로 확인하지 않았다.

# C. 라우터 로그가 Work 도구에 전달되지 않음 (Andy)

- 관측: Andy n2626·n2633 첫 P0 예약이 `workspace preparation failed before launch`로 끝났지만 router.log에 Git 원문이 0건([andy-p0-launch-delay](/docs/journal/2026/11/23/operations/andy-p0-launch-delay/record.md)).
- 원인: `channel_mcp_tools._work_tool`이 `dispatch_work_tool`에 `log`를 넘기지 않았다. `_workspace_failure`의 `work_workspace_failed`와 P0 계보 로그가 라우터 MCP 경로에서 버려졌다.
- 수정: `ChannelMcpRuntimeServices.log`(`:30`), `ciel_runtime.py`가 `router_log` 전달. 테스트 `test_work_tools_receive_the_router_log`.

# 검증

- 격리 회귀 `qwait-p0c.015539`(A·B·C와 질문 대기 계약 포함, 24개 테스트 파일): Windows·py310·py311 각각 663 실행, 실패·오류 0, skip 54. Linux는 기존 컨테이너 `ciel-runtime-test-py310`·`ciel-runtime-test-py311` 재사용.
- `claude plugin test ciel-work`: 94 통과.

# 배포·운영 상태

- 미커밋·미배포. 실제 `claude.exe` 격리 로드와 Side Fresh 실CLI 검증이 끝나야 베타·설치한다.
- 설치하더라도 운영 반영에는 라우터·세션 재시작이 필요하다. 재시작은 금지 범위이므로 Celly P0 정지는 그때까지 계속된다. 파일 설치와 라이브 반영은 따로 보고한다.
