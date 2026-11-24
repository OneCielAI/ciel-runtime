---
type: fix-record
title: 재시작 워크트리 스냅샷 WinError 206·장시간 정지, 중복 company-prompt 재기동 및 KOBE reserve KeyError 수정
description: Andy 1,219개 워크트리 재시작에서 발생한 WinError 206·O(N^2) 정지·중복 company-prompt 재기동과 KOBE 동시 orphan sweep 중 발생한 work_parallel_mcp.py:4300 KeyError의 증거, 수정, 검증
tags: [restart-takeover, company-prompt, winerror-206, work-parallel, keyerror]
timestamp: 2026-11-24T16:18:00Z
---

# 재시작 워크트리 스냅샷 WinError 206·장시간 정지, 중복 company-prompt 재기동 및 KOBE reserve KeyError 수정

## 관측 (읽기 전용)

1. **Andy 재시작 스냅샷 실패 및 11.4분 이중 재기동 (2026-11-24 14:49–15:31Z)**
   - 14:49:24Z `company-prompt` v30 안내로 재기동 `cb53f7b84aca`가 `wait_turn=true`로 큐에 들어가 `restart-hold`가 시작됐다.
   - 15:19:43Z 턴 종료 후 15:19:44Z CLI 종료 → `restart_takeover_snapshot` 완료까지 **5분 42초**(15:25:26Z)가 걸렸고, 로그에 `worktrees=1219 snapshots=0 errors=1219`가 기록됐다.
   - `restart-takeover.json` 매니페스트의 1,219개 오류가 전부 `FileNotFoundError: [WinError 206] The filename or extension is too long`이었다.
   - 읽기 전용 검증: 매니페스트 경로 전체를 `-c safe.directory=`로 넘긴 `git --version`은 명령줄 96,562자로 `WinError 206`(1개 46자는 성공), 루트의 하위 워크트리 제외 인자만도 63,812자였다.
   - 15:23:17Z(14:49:24Z 이후 `RETRY_SECONDS=1800` 경과, 첫 재기동이 아직 `prepare_restart` 전)에 동일 v30 안내로 두 번째 재기동 `b1435d267b48`이 큐에 들어갔다.
   - 15:25:27Z 첫 재기동으로 올라온 CLI(pid 60332)가 즉시 `b1435d267b48`을 수령해 1.5초 만에 종료하고 다시 5분 36초간 스냅샷을 반복했으며, 두 번째 `restart-hold`가 시작 시각을 덮어쓰면서 `runtime_input_launch_backlog_expired`로 외부 이벤트 24417(1건)·24418–24425(8건)가 만료됐다.
2. **KOBE `/ca/mcp` 미처리 `KeyError` (2026-11-24 16:05Z)**
   - 16:05:38Z `work_orphan_released lease=eef23c1d7e9f priority=P1 state=reserved owner=legacy_no_owner`.
   - 16:05:49Z `router_post_uncaught path=/ca/mcp error=KeyError: 'eef23c1d7e9f4eb49b590c949b5f697a'` (`work_parallel_mcp.py:4300`, `lease = store.read()["leases"][assignment["lease_id"]]`).

## 원인 및 수정

- **`ciel_runtime_support/restart_takeover.py`**
  - `_git`: 전체 워크트리 목록 대신 현재 대상 경로(와 다를 경우 기본 저장소 루트 1개)만 `-c safe.directory=`로 전달한다.
  - `_excludes` / `snapshot_all`: 모든 워크트리·제외 경로를 루프 전에 한 번만 `resolve()`(O(N))하고, 상위 디렉토리가 이미 제외되면 하위 중복 경로를 접는다.
  - `_status` / `snapshot_worktree`: Windows에서 제외 인자가 24,000자를 넘으면 `status`에는 명시적 상태 제외 경로(`.claude` 등)만 CLI로 넘기고 `--no-renames` 결과를 Python에서 필터링하며, `git add -A`는 `--pathspec-from-file --pathspec-file-nul`을 사용한다.
  - `MAX_TOTAL_SECONDS = 30.0`: `session_worktree`를 먼저 계산해 최우선으로 스냅샷하고(그다음 메인 체크아웃, 나머지는 최근 수정 순), 예산 경과 시 **추가** 워크트리 순회를 멈추고 `skipped: "time budget reached (30s)"`로 기록한다(이미 실행 중인 단일 `git` 호출이나 재기동 전체 시간의 절대 상한이 아니라 다음 워크트리 순회 시작 기준이다).
- **`ciel_runtime_support/runtime_session_restart.py`, `company_prompt_reload.py`, `ciel_runtime.py`**
  - `RuntimeSessionRestartService.restart_in_progress()`: 대기 중 재기동 요청 또는 살아 있는 런처의 `restart-hold` 존재 여부를 확인한다.
  - `CompanyPromptReloader`: `restart_active()`가 참이면 `RETRY_SECONDS` 이후에도 중복 재기동을 큐에 넣지 않고 1회 `company_prompt_reload_deferred`를 기록한다(`ciel_runtime.py` 라우터 리스너에 연결).
- **`ciel_runtime_support/work_parallel_mcp.py`**
  - line 4300에서 `store.read()["leases"].get(assignment["lease_id"])`로 조회하고 동시 회수 등으로 사라진 경우 미처리 `KeyError` 대신 `WorkParallelError("reserved lease is no longer open; retry dispatch")`를 반환한다. (KOBE에서 새 예약이 `legacy_no_owner`로 분류된 이유와 회수 정책 자체는 별도 미결로 추적한다.)

## 검증

- 실패 테스트 먼저:
  - 결함 동작 직접 재현(수정 전 실패): `test_git_trust_for_many_linked_worktrees_does_not_prevent_spawn`(`WinError 206`), `test_many_nested_exclusions_do_not_prevent_status_or_snapshot`(`WinError 206`), `test_reserve_raises_work_parallel_error_if_lease_disappears_before_return`(`KeyError` at `work_parallel_mcp.py:4300`).
  - 신규 인터페이스·가드 계약 검증([red-before-fix.txt](evidence/red-before-fix.txt)): `test_snapshot_all_prioritizes_session_worktree_and_respects_time_budget`, `test_snapshot_all_keeps_explicit_state_excludes_even_with_many_nested_worktrees`, `test_restart_in_progress_defers_duplicate_company_prompt_restart_past_retry_window`, `test_restart_in_progress_checks_pending_request_and_live_restart_hold`.
- 임시 격리 Windows pytest(7개 영향 파일): 194 passed, 17 subtests passed, 0 failed.
- 기존 Linux `ciel-runtime-test-py310`, `ciel-runtime-test-py311` 컨테이너(10개 영향 파일 각 492건 실행): 실패 0, 오류 0, skip 35(Windows 전용 2건 포함).
- `py_compile`, `ruff check`, `npm run check:docs`, `npm pack --dry-run` 통과.
- 실엔진 재기동 통합 검증은 아직 수행하지 않았다.
