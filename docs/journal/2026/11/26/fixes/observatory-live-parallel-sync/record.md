---
type: fix-record
title: Observatory 패널의 병렬·검수 상태 스냅샷 자동 동기화
description: 메인 검수 완료 후에도 도킹된 Observatory 패널이 열 당시의 observatoryParallel 스냅샷에 고정되어 Awaiting main review를 계속 표시하던 결함 수정
tags: [ciel-work, observatory, review, register-js]
timestamp: 2026-11-26T07:15:00Z
---

# Observatory 패널의 병렬·검수 상태 스냅샷 자동 동기화

## 관측 (2026-11-26 UTC, Andy 운영)

- Andy 세션 터미널의 도킹된 Work & SSE Observatory 패널이 `06:42Z` 시점에도 `DEVICE 2/6 slots · REVIEW 8 · SIDE 1`, `◆ Awaiting main review · n2866`, `◆ Awaiting main review · n2871`을 계속 표시했다.
- 실제 백엔드(`work-parallel-finalization.json`, `/ca/work/api/v1/parallel`, 보드) 확인 결과:
  - `n2866`: 1차 `801d4dff0f4b`(`05:01:02Z` 전달 → `05:51:00Z` `rework` → `06:02:17Z` `close`), 2차 `17fcb00d7e6e`(`05:57:41Z` 전달 → `06:02:09Z` `approve` → `06:02:15Z` `finalized`, 보드 `done`).
  - `n2871`: 1차 `fec71eb8b364`(`04:53:48Z` 전달 → `05:52:33Z` `rework` → `06:22:14Z` `close`), 2차 `e98670ef41ed`(`06:12:47Z` 전달 → `06:21:33Z` `approve` → `06:21:38Z` `finalized`, 보드 `done`).
  - 라이브 `/ca/work/api/v1/parallel`(`review_version: 3502`)의 `pending` 검수는 0건, 남아 있는 `parallel_reviews`는 과거 `rework_ready` 6건뿐이었다. 두 항목이 동시에 `pending`이던 구간(`05:01Z–05:51Z`, 기존 6 + 2 = `REVIEW 8`)의 스냅샷이 승인 이후에도 갱신되지 않았다.

## 원인 (확인)

- `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`에서 `graphicProps()`(`observatory.client.js`에 `REVIEW` 개수·검수 줄·슬롯·포크·진단을 전달)는 모듈 변수 `observatoryParallel`만 읽는다.
- `observatoryParallel`은 `observatoryRefresh()`에서만 대입되며, `observatoryRefresh()`는 패널을 열 때(`openDetails`)와 수동 `Refresh`(`r`)에서만 호출됐다.
- 검수 결정·턴 종료·Stop·포크 보고 때마다 실행되는 `projectParallelReviews()`와 `hostStatus()`는 최신 `work_parallel_status`를 읽고도 `observatoryParallel`을 갱신하지 않았고, `projectParallelReviews()`가 갱신하는 `observatoryReviews`는 코드 어디에서도 읽히지 않았다. `resetParallelHostState()`도 `observatoryParallel`을 비우지 않았다.

## 수정 (`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`)

- `parallelDisplaySignature(state)`와 `syncObservatoryParallel(state, explicit = false)`를 추가했다.
  - `hostStatus()`(현재 세션 `lifecycle === starts`일 때), `projectParallelReviews()`, `observatoryRefresh()`에서 `syncObservatoryParallel()`을 호출해 최신 `work_parallel_status`를 `observatoryParallel`에 반영한다.
  - 표시 필드가 실제로 바뀐 경우에만 `$.ui.invalidate('ui.render')`를 호출한다(`claude plugin validate` 규칙에 맞춰 `$`에 optional chaining을 쓰지 않고 `try { $.ui.invalidate('ui.render') } catch {}` 사용).
  - 단조 버전 방어: 더 오래된 `state.version`이나 `review.version` 응답이 뒤늦게 도착해도 `observatoryParallel`을 되돌리지 않는다.
  - 동일 `review.version`에서 기존 패널 스냅샷의 보류 검수를 빈 큐가 덮어쓰지 않도록 보호해 기존 `review-reminder-pane-only-row` 계약을 유지한다.
  - `resetParallelHostState()`에서 `observatoryParallel = null`로 초기화해 세션 전환 시 이전 세션 스냅샷이 남지 않게 했다.

## 검증

- **수정 전 실패(`b0cabd1`):**
  - `tests/test_work_observatory.py::test_pane_updates_reviews_and_slots_on_host_status_without_manual_refresh`: `FAIL` (`AssertionError: 1 != 2`, Stop 이후에도 이전 스냅샷 유지).
  - `claude plugin test` (`tests/observatory.test.ts` → `graphical pane clears finalized reviews on host status updates without manual refresh`): `FAIL` (`Expected containing "DEVICE 1/6 slots · REVIEW 1"`, 실제 출력에 `DEVICE 2/6 slots · REVIEW 3` 및 `Awaiting main review · n2866 / n2871` 잔존).
- **수정 후 통과:**
  - `claude plugin validate ciel_runtime_support/claude_mods/ciel-work`: `Validation passed` (rc=0).
  - `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: **96 pass, 0 fail** (3개 파일).
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): `test_work_observatory.py` 10 OK, `test_observatory_stage.py` 2 OK, `test_side_intake_mod.py` 75 passed(89 subtests), `test_work_mod_write_gate.py` 185 ran OK(skipped=33), `test_work_mod_real_backend.py`(`presented_review_lists` + `superseded_stranded_row`) 2 passed.
  - 기존 Linux `ciel-runtime-test-py310`: `test_work_observatory.py` 10 OK, `test_observatory_stage.py` 2 OK, `test_side_intake_mod.py` 75 OK, `test_work_mod_write_gate.py` 185 ran OK(skipped=33), `test_work_mod_real_backend.py`(`presented_review_lists` ok, `superseded_stranded_row` skipped — 임시 스냅샷에 과거 커밋 `57dbfb3` 없음).
  - 기존 Linux `ciel-runtime-test-py311`: `test_work_observatory.py` 10 OK, `test_observatory_stage.py` 2 OK, `test_side_intake_mod.py` 75 OK, `test_work_mod_write_gate.py` 185 ran OK(skipped=33), `test_work_mod_real_backend.py`(`presented_review_lists` ok, `superseded_stranded_row` skipped).

## 발행·설치

- `feature/priority-models`·`beta` 푸시 및 npm `@beta` 확인 후 승인된 7개 루트 해시 확인 설치 예정(실행 중 Claude CLI는 세션 재시작·모드 재로드 전까지 메모리 내 구 `register.js`를 유지하므로 파일 설치와 라이브 효과를 구분한다).
