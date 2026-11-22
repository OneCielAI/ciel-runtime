---
type: fix-record
title: work_side_claim가 매번 전체 수신 기록을 읽던 라우터 CPU 소모
description: 사이드 claim마다 수신(receipt) 파일 전체를 다시 읽어 라우터 CPU 표본의 약 41%를 쓰던 문제의 측정, 수정, 검증 기록.
tags: [work-mode, side-intake, router, performance, andy]
timestamp: 2026-11-22T20:20:00Z
---

## 측정 (읽기 전용, 2026-11-22 19:5xZ)

- 라우터 CPU: Andy 라우터 68036은 10초 동안 CPU 12.2초(약 122%)를 썼다. KOBE 라우터 2684는 10초 동안 9.4초를 썼다.
- `py-spy record --nonblocking`(라우터를 멈추지 않는 샘플링) 30초, Andy 68036, 표본 1,831개:
  - `dispatch_work_tool → side_intake_mcp.run:625 → side_reply_outbox.pending:24 → heapq.nsmallest → eligible → SideIntakeInbox._read_file`가 731 표본(**39.9%**)이었다.
  - `pending()` 전체는 41.0%였다.
  - 다음으로 큰 것은 `ForkTurnWatch._poll`(24.5%)이었다.
- 호출 경로: `work_side_claim` 응답의 `pending_replies`가 `pending(state_dir, identity)`를 부른다(`side_intake_mcp.py:625`). Andy 라우터 로그에는 최근 11분 동안 `work_side_claim`이 35회 있었다.
- 데이터: Andy 수신함(`workspaces/aae7db361c7f/side-inboxes/4bfc87437f28…`)에는 `receipts` 5,459개(108 MB)가 있다. 상태는 `processed/not_applicable` 2,609, `processed/submitted` 2,427, `route_unavailable` 248 등이고, **돌려줄 대상(답장이 남은 것)은 0개**였다.
- 임시 복사본에서 잰 시간: 수정 전 `pending()`은 호출마다 6.5~7.0초가 걸렸다(파일을 미리 읽어 둔 상태). 결과는 1행이었다(pending 디렉터리의 failure reply).

## 원인

`pending()`이 호출될 때마다 `receipts/*/*.json` 전부를 잠금(`durable_json_file.reader` → `_io_lock` → 파일 잠금)과 함께 열어 JSON으로 파싱한 뒤 조건을 확인한다. 끝난 수신 기록은 지워지지 않으므로, 호출당 비용이 이력 크기에 비례해 계속 커진다.

## 수정

- 새 모듈 `ciel_runtime_support/stat_verdict_cache.py`의 `StatVerdictCache`: 파일마다 판정 결과를 stat 식별자 `(st_dev, st_ino, st_size, st_mtime_ns)`와 함께 기억하고, 새 파일이거나 식별자가 바뀐 파일만 다시 읽는다.
  - 원자적 교체(새 파일)는 식별자를 반드시 바꾼다.
  - 50 ms quiet window 안에 수정된 파일은 판정만 하고 기억하지 않는다. `StatJsonCache`와 같은 규칙이다.
  - 스캔 하나를 끝까지 돌면 그 스캔에서 본 파일만 남겨, 메모리가 이력이 아니라 현재 디렉터리 크기를 따른다.
- `side_reply_outbox.pending()`: 판정 조건은 그대로 `_owed_reply_state`로 분리했고, 수신 스캔에 이 캐시를 쓴다. 반환값과 순서(`nsmallest`)는 바뀌지 않는다.

## 검증

- 실패 테스트를 먼저 썼다(HEAD `33543b6` 이후 작업 트리): `test_side_reply_outbox.test_pending_reads_only_new_or_changed_receipts`. 수정 전에는 두 번째 호출에서 바뀌지 않은 수신 3개를 다시 읽어 실패했다. 수정 후 통과한다. 바뀐 수신은 다시 읽고 새 상태를 반영하는 것도 확인했다.
- 실제 데이터 임시 복사본(Andy 5,459개): 수정 전 호출마다 6.5~7.0초, 수정 후 첫 호출 7.33초, 이후 0.42~0.49초. 결과는 같다(1행).
- 영향 회귀: `tests/test_side*.py`와 `work_side_claim`/`side_reply_outbox` 사용 테스트(44개 파일, 671건)를 돌렸다. 임시 격리 Windows, Linux `ciel-runtime-test-py310`·`py311` 모두 failures 0, errors 0.

## 남은 항목

- 이 수정은 라우터 프로세스가 다시 뜬 뒤에 적용된다. 실행 중인 라우터에는 모듈이 이미 import되어 있다.
- `SideIntakeInbox.summaries`(`nlargest`, 표본 3.7%)와 `ForkTurnWatch._poll`(24.5%)은 이번 범위 밖이다. 끝난 수신 파일을 보관·삭제하는 정책도 없다.
- Celly·KOBE·Prody의 수신 파일 수는 이번 조회에서 측정하지 못했다(원격 조회 경로가 출력 없이 끝남).
