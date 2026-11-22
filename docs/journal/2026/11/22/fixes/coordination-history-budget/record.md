---
type: fix-record
title: Work coordination 파일 2 MiB 한도 교착
description: 끝난 실행의 기록이 지워지지 않아 coordination 파일이 한도에 닿았고, 그 뒤로 reserve와 포크 보고가 모두 거부된 문제의 증거, 수정, 검증 기록.
tags: [work-mode, coordination, byte-limit, celly, andy]
timestamp: 2026-11-22T19:10:00Z
---

## 운영 관측 (읽기 전용, 2026-11-22)

- Celly `G:\ciel-bridge-service\AppData\Roaming\ciel-runtime\work-parallel-coordination.json`: **2,097,109 B**, 한도 `MAX_BYTES` 2,097,152 B (`work_parallel_receipts.py:22`). 구성: archive 873,548 B(1000행, `MAX_ARCHIVE` 상한, 그중 742행은 115시간 전 한 번의 reconcile로 들어온 "ledger lease absent, never launched"), details 907,517 B(405개), receipts 98,204 B, runs 189,418 B(184개).
- Celly 메인 transcript: `18:25:39Z`, `18:47:34Z` `work_parallel_event (reserve) failed: coordination persistence failed; reservation released: WorkParallelError: coordination byte limit`. 포크 n1332의 question/completion 보고도 같은 오류로 거부(18:35Z~18:36Z 포크 보고문). 메인 보고(18:43Z)의 n1209 `launch_refused`, n1326 예약 후 미연결이 같은 시간대.
- 다른 호스트: Andy **2,066,586 B**(한도까지 약 30 KB), KOBE 1,320,719 B, Prody 249,904 B.
- 코드: `details`/`receipts`를 지우는 경로가 없음(`persist_receipt`만 추가). archive는 30일·1000행 상한만 있고 바이트 예산은 없음. `_change`는 새 상태가 한도를 넘으면 `require(len(raw) <= MAX_BYTES, "coordination byte limit")`로 거부 → 한도에 닿으면 정리 동작을 포함한 모든 쓰기가 막힘.

## 원인

coordination 파일의 수 상한(MAX_ARCHIVE 1000, MAX_DETAILS 4000, MAX_RECEIPTS 8000)이 바이트 상한(2 MiB)과 맞지 않고, 끝난 실행의 영수증 본문을 지우는 경로가 없었다. 파일이 단조 증가해 한도에 닿으면 그 뒤의 모든 쓰기가 거부된다.

Celly 메인의 추정("남은 n1314 실행 기록이 n1209 실행을 막음")은 거부 원문(`coordination byte limit`)과 맞지 않는다. 리스가 orphaned/done인데 실행 기록이 active/waiting으로 남는 행(Celly 93개)은 파일 크기에 기여하지만 별도 추적 항목이다.

## 수정

`ciel_runtime_support/work_parallel_receipts.py`:
- `_change`에서 새 상태가 `MAX_BYTES`를 넘으면 `_trim_history`로 가장 오래된 끝난 기록부터 지워 `HISTORY_TRIM_TARGET`(MAX_BYTES의 3/4)까지 줄인 뒤 다시 검증·측정한다. 그래도 넘으면 기존처럼 `coordination byte limit`으로 거부(파일 불변).
- 지우는 대상: archive 행과, 실행이 `runs`에 없는 receipt/detail 쌍. 둘을 시간순(오래된 것 먼저)으로 섞어 지운다. receipt와 detail은 항상 함께 지워 `validate`의 `receipt key in details` 조건을 유지(스키마 불변 → 이전 빌드도 읽을 수 있음).
- 지우지 않는 것: live run과 그 영수증, `HISTORY_PROTECT_SECONDS`(24시간) 이내 기록. 지운 archive 행은 기존 수·나이 상한과 같이 `archive_dropped`에 더한다.

## 검증

- 실패 테스트 먼저(HEAD `002189d`): `CoordinationHistoryBudgetTests.test_full_history_yields_to_a_new_reservation_and_keeps_live_and_recent_rows`가 `WorkParallelError: coordination byte limit`로 실패(Celly와 같은 오류). 수정 후 통과. 함께 넣은 `test_live_runs_alone_over_the_limit_still_refuse_and_change_nothing`은 수정 전후 모두 통과(fail-closed 유지).
- 실제 데이터 복사본 재현(임시 디렉터리, 운영 파일 불변): Celly 2,097,109 → 1,572,860 B, runs 184개·live 영수증 163개 모두 유지, archive 1000 → 257행. Andy 2,066,878 → 1,572,595 B, runs 238개·live 영수증 191개 유지.
- 영향 회귀(coordination을 쓰는 37개 파일, 643건): 임시 격리 Windows(`CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 config), Linux `ciel-runtime-test-py310`·`py311` 모두 `RAN 643 failures=0 errors=1`. 오류 1건 `test_work_parallel_legacy_reviewer…test_model_schemas_no_longer_advertise_the_reviewer_contract (tool='work_discovery_pickup') KeyError: 'role'`는 수정 전 HEAD `002189d`(`git archive`) py311에서도 같은 오류 → 기존 결함, 이번 변경과 무관(별도 추적).

## 남은 항목

- 24시간 넘게 멈춘 finalization이 지워진 archive 행/영수증을 다시 찾는 경우(`_closed_rework_already_archived`, 교체 경로)는 기존처럼 명시적 오류로 멈춘다(fail-closed). 운영에서 관측된 적은 없다.
- 리스가 끝났는데 coordination run이 active/waiting으로 남는 행(Celly 93개): 별도 추적.
- 다른 2 MiB 사이드카(finalization 1,113,162 B 등)는 같은 구조인지 아직 확인하지 않았다.
