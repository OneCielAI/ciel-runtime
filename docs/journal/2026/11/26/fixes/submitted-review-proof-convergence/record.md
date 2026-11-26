---
type: fix-record
title: 제출 검수 idle_proof 불일치 방지 및 recover_submitted_review 재수렴
description: submitted 리스의 reconcile이 set_activity 실패 시 원장 idle_proof를 되돌리지 못해 워크스페이스 매니페스트와 불일치하던 결함과, pending 검수에 대해 recover_submitted_review가 조기 반환하며 turn.complete가 직후 상충 reconcile을 보내던 결함 수정
tags: [ciel-work, work-parallel, review, idle-proof, register-js, work-parallel-mcp]
timestamp: 2026-11-26T12:15:00Z
---

# 제출 검수 idle_proof 불일치 방지 및 recover_submitted_review 재수렴

## 관측 (2026-11-26 UTC, Andy 운영 `n2790`)

- `Andy_Main (3)`에서 완료 보고된 `n2790` (`lease_id=c15200e7d0444ef9b85c309185585634`, 포크 `a956a9267063f6f4f`)을 `work_parallel_item_review(decision="approve")`로 승인하려 하자 `"positive matching idle workspace proof required"` 오류가 발생했다.
- 이어 `work_parallel_event(event="recover_submitted_review", lease_id="c15200e7d0444ef9b85c309185585634")`를 호출했으나 `{review_pending: true}`만 반환하고 승인은 계속 같은 오류로 거부됐다.
- Andy 라이브 상태 파일(읽기 전용 확인) 대조 결과:
  - `work-parallel.json`: `lease.state = "submitted"`, `lease.slot = false`, `lease.idle_proof = "Native agent.list row absent"`. (중첩 `lease.workspace.idle = false`는 모든 리스에 남는 생성 시점 정적 스냅샷이며, 실제 검수 검증 `review_observe()`는 워크스페이스 매니페스트 파일을 읽는다.)
  - `work-parallel-workspaces/792e0a512a080ff835fc257a34083a27.json` (mtime `11:25:53Z`): `idle = true`, `idle_proof = "Submitted completion fork absent from agent.list after restart"`.
  - `work-parallel-finalization.json`: `c15200e7d0444ef9b85c309185585634`는 `state = "pending"`, `decision = null`.
  - `work_parallel_workspace.py:659-671` (`review_observe()`)은 `record["idle_proof"] == lease["idle_proof"]` 일치를 요구하므로 두 도메인(`work-parallel.json` vs `work-parallel-workspaces/*.json`)의 `idle_proof` 불일치가 승인 차단의 직접 원인이었다.

## 원인 (확인)

1. **`ciel_runtime_support/work_parallel_mcp.py` (`run()` `reconcile` 경로 & `_observe_idle_and_publish_review`)**
   - `reconcile` 이벤트가 `store.reconcile()`로 원장 `lease["idle_proof"]`를 먼저 덮어쓴 뒤(`L4683`), `submitted` 리스에서 `set_activity()`를 중복 호출(`L4745` + `_observe_idle_and_publish_review` 내부)했다.
   - `set_activity()`가 일시적 `WorkspaceError`(예: git 검증 타임아웃·I/O 오류)로 실패하면 워크스페이스 매니페스트는 갱신되지 않은 채 예외가 전파되었고, 이미 바뀐 원장 `lease["idle_proof"]`는 복구되지 않아 두 도메인의 `idle_proof`가 갈라졌다.
2. **`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` (`recoverSubmittedReview` & `parallelTurnComplete` / `parallelReconcileAtTurnEnd`)**
   - `recoverSubmittedReview()`(`L2983`)는 `reviewed(target)`가 존재하면 `state === "pending"`이어도 즉시 `{lease_id, review_pending: true}`를 반환해 백엔드 `restart_submitted_terminal` / `reconcile` 수렴 복구 경로를 전혀 호출하지 못했다.
   - `parallelTurnComplete()`(`L4889-4895`)는 `parallelRestartSubmitted()`가 `RESTART_TERMINAL_PROOF`로 검수를 발행한 뒤 `s`를 갱신하지 않고 바로 `parallelReconcileAtTurnEnd()`를 실행해, 직전 스냅샷 기준으로 상충하는 `reconcile(proof="Native agent.list row absent")`를 연달아 보냈다.

## 수정

- **`ciel_runtime_support/work_parallel_mcp.py`**
  - `_restore_idle_proof(store, lease_id, *, expected_proof, prior_proof)` 추가: 일시적 `WorkspaceError`로 워크스페이스 매니페스트 쓰기가 실패하면, 리스가 아직 `slot is False`이고 `idle_proof == expected_proof`인 경우에 한해 원장의 `prior_proof`를 CAS로 되돌린다.
  - `reconcile`의 `submitted` 경로에서 `set_activity()`를 2회 실행하던 중복을 제거하고 `_observe_idle_and_publish_review(..., prior_idle_proof=prior_idle_proof)` 1회 수렴으로 통합했다.
  - `_restart_submitted_terminal`에서 `current.get("idle_proof")`가 비어 있는 엣지 케이스에도 안전하게 `RESTART_TERMINAL_PROOF`로 폴백하도록 방어했다.
- **`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`**
  - `recoverSubmittedReview()`: 결정이 완료된 검수(`decided`: `deciding`/`rework_ready`/`finalized`)만 조기 반환하고, `pending` 검수는 포크 미등재 시 `restart_submitted_terminal`, 터미널 상태 등재 시 기존 `idle_proof`로 `reconcile(state='idle')`을 호출해 백엔드에서 두 도메인의 `idle_proof`를 재수렴하도록 변경했다.
  - `parallelTurnComplete()`: `parallelRestartSubmitted()`가 1건 이상 처리하면 `s = await hostStatus($)`로 스냅샷을 갱신한다.
  - `parallelReconcileAtTurnEnd()`: 이미 `submitted`·`slot=false`·`idle_proof` 기록·검수 행 존재·`running` 아님인 리스에 대해 상충하는 `reconcile("Native agent.list row absent")`를 보내지 않도록 가드했다(`stopParallelAgent`는 유지).

## 검증

- **수정 전 실패 재현 (`f9546dd`):**
  - `tests/test_work_parallel_restart_terminal.py::test_late_absent_reconcile_cannot_clobber_published_review_idle_proof`: `FAIL` (`'Native agent.list row absent' != 'Submitted completion fork absent from agent.list after restart'`).
  - `tests/test_work_mod_write_gate.py::test_recover_submitted_review_converges_already_pending_absent_fork`: `FAIL` (`events: []`, `restart_submitted_terminal` 미호출).
  - `tests/test_work_mod_write_gate.py::test_turn_complete_does_not_clobber_restart_published_review`: 1차 구현에서 `row` 부재 시 `idle=false`로 분기해 `reconcile(state='unknown', proof='Native agent.list row absent')`가 발사되는 엣지를 포착 → `existingProof` 판정을 보정 후 통과.
- **수정 후 통과:**
  - `python -X utf8 -m py_compile ciel_runtime_support/work_parallel_mcp.py`: OK.
  - `claude plugin validate ciel_runtime_support/claude_mods/ciel-work`: `Validation passed`.
  - `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: **96 pass, 0 fail** (3개 파일).
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정):
    - `test_work_parallel_restart_terminal.py` + `test_work_parallel_finalization.py` + `test_work_mod_write_gate.py`: **287 passed, 33 skipped, 207 subtests passed**.
    - `test_work_parallel_rework_worktree.py` + `test_work_observatory.py` + 관련 `restart_terminal`: **92 passed, 17 deselected, 54 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py310`:
    - `restart_terminal` + `finalization` + `work_mod_write_gate`: **284 passed, 38 skipped, 197 subtests passed**.
    - `rework_worktree` + `work_observatory`: **68 passed, 41 skipped, 33 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py311`:
    - `restart_terminal` + `finalization` + `work_mod_write_gate`: **282 passed, 38 skipped, 199 subtests passed**.
    - `rework_worktree` + `work_observatory`: **67 passed, 41 skipped, 34 subtests passed**.

## 발행·설치

- 커밋 `537a2ea8236534172c6dc005ee78063147c047f9`, GitHub Actions `Publish to npm` (`45822309530`) 성공, npm `@beta` = `0.2.51-beta.20261126-124338.537a2ea`(`gitHead` 일치).
- npm tarball 내부 2개 파일 SHA-256과 커밋 `537a2ea` 일치 확인:
  - `ciel_runtime_support/work_parallel_mcp.py`: `e79190a73e74` → `5dbe504d5cd4` (279,761 B).
  - `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`: `10fcdb2d0227` → `28b679f54bd3` (373,874 B).
- 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`) 해시 확인 설치 완료(`check` 7/7 `base` → `apply` 7/7 `ok=True`, `.cmd` 래퍼·버전 메타 미변경, 원격 임시 오버레이 폴더/tar 정리 완료).
- **라이브 효과 구분:** 파일 설치만 완료된 상태다. 모니터링 규칙상 실행 중 Claude CLI 세션과 라우터는 재시작하지 않았으므로, 이미 기동된 라우터(`work_parallel_mcp.py`)와 CLI 세션(`register.js`)은 각각 재시작/모드 재로드 전까지 이전 메모리 코드를 유지한다. CLI 세션이 재로드/재시작되면 `parallelRestartSubmitted` 또는 `recover_submitted_review`가 기존 `n2790` 불일치도 자동 수렴한다.

