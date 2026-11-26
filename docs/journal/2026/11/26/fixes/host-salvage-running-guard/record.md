---
type: fix-record
title: host_salvage 실행 상태 가드 및 parallelHasReceipt 오판 수정
description: waiting_input 리스에 host_salvage가 성공해 run.exit를 오염시키던 백엔드 결함과, parallelHasReceipt가 host_salvage를 워커 종료 보고로 오판해 재개된 포크를 즉시 종료(TaskStop)하고 unreported 없이 슬롯을 누수하던 모드 결함 수정
tags: [ciel-work, work-parallel, host-salvage, slot-leak, register-js, work-parallel-mcp]
timestamp: 2026-11-26T13:15:00Z
---

# host_salvage 실행 상태 가드 및 parallelHasReceipt 오판 수정

## 관측 (2026-11-26 UTC, Andy 운영 `n2728`)

- `12:47:30Z` 모니터링 틱에서 Andy에 신규 finding `slot held, fork absent since 12:37: n2728 521a8d85 P4 running`이 포착됐다.
- Andy 상태 파일(`work-parallel.json`, `work-parallel-coordination.json`) 및 서브에이전트(`a91c2c2c1f058c911`)·메인 전사 읽기 전용 대조 결과:
  - `12:22:41Z`: 포크 `a91c2c2c1f058c911`이 `work_parallel_report(event="question")`으로 정상 파킹(`receipt=3fa91c088a46`, `lease.state="waiting_input"`, `slot=false`).
  - `12:24:22Z`: 이미 `waiting_input` 상태인 `521a8d85`에 `host_salvage`(`blockers=["answer"]`, `receipt=a5627c531c16`)가 실행되어 성공했고, `run.exit`를 `host_salvage`로 덮어쓰고 `run.state="handed_off"`로 바꿨다. 이어진 `unreported`는 `work_parallel.py:480`의 `state == "running"` 가드에 걸려 실패(`register.js:4002` `catch`로 무시)해 원장은 `waiting_input`에 남았다.
  - `12:27:54Z`–`12:29:29Z`: 메인이 정상적으로 `waiting_input` 워커를 재개(`reacquire`→`resume`, `lease.state="running"`, `slot=true`).
  - `12:36:14.982Z`: 백그라운드 QA 실행 중이던 포크 `a91c2c2c1f058c911`이 `[Request interrupted by user]` / `<status>killed</status>`로 강제 종료됐으나, `lease.state="running", slot=true`가 그대로 남아 `12:37Z`부터 P4 슬롯이 점유 상태로 방치됐다(`12:50:53Z` 복구 메시지로 재기동됨).

## 원인 (확인)

1. **`ciel_runtime_support/work_parallel_mcp.py` (`event == "host_salvage"`, `L4429–4458`)**
   - `host_salvage`가 `lease["state"]`를 전혀 검사하지 않았다. 이 때문에 `waiting_input`(또는 `submitted` 등 비실행) 리스에 지연 호출이 도착해도 `coord.persist_receipt(..., "host_salvage", ...)`가 성공해 `run.exit`를 오염시켰다.
2. **`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` (`parallelHasReceipt`, `L3925–3929`)**
   - `parallelHasReceipt(state, lease)`가 `kind !== 'question'`인 모든 영수증(`host_salvage` 포함)을 워커의 종료 보고로 간주해 `true`를 반환했다.
   - `parallelEnsureExitContract` (`L3960–3965`)는 `parallelHasReceipt(state, lease)`가 참이면 넛지·유예와 `unreported` 호출을 모두 건너뛰고 즉시 `stopParallelAgent` (`TaskStop`)만 실행한 뒤 `true`를 반환한다.
   - 결과적으로 (a) 구빌드에서 `waiting_input`에 잘못 남긴 `host_salvage`가 있는 리스를 재개하면 첫 경계에서 살아 있는 포크를 즉시 강제 종료하고 `running+slot=true`를 누수하며, (b) 정상 `host_salvage` 직후 `unreported`가 일시 실패한 경우에도 후속 경계에서 `unreported`를 영구히 재시도하지 못하고 슬롯을 누수했다.

## 수정

- **`ciel_runtime_support/work_parallel_mcp.py`**
  - `host_salvage` 진입부에서 `lease["state"] not in ("running", "unreported")`이면 즉시 `WorkParallelError("worker is not running")`으로 거부하고, 기존 `host_salvage` 멱등 응답 대상이 아닌 경우 `lease["state"] != "running"`도 거부하도록 가드했다.
- **`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`**
  - `parallelHasReceipt(state, lease)`가 `question`과 함께 `host_salvage`도 워커 종료 보고에서 제외(`kind !== 'question' && kind !== 'host_salvage'`)하도록 수정했다.

## 검증

- **수정 전 실패 재현 (`537a2ea`):**
  - `tests/test_work_parallel_mcp.py::test_host_salvage_refuses_parked_waiting_input_and_preserves_question_receipt`: `FAIL` (`WorkParallelError not raised` — `waiting_input`에 `host_salvage`가 성공).
  - `tests/test_work_mod_write_gate.py::test_running_lease_with_host_salvage_exit_is_not_killed_early_and_releases_slot`: `FAIL` (`first.sent=0, tools=[TaskStop], lease.state='running', slot=True` — 첫 턴에 즉시 `TaskStop` 후 슬롯 누수).
- **수정 후 통과:**
  - `python -X utf8 -m py_compile ciel_runtime_support/work_parallel_mcp.py`: OK.
  - `claude plugin validate ciel_runtime_support/claude_mods/ciel-work`: `Validation passed`.
  - `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: **96 pass, 0 fail** (3개 파일).
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): `test_work_parallel_mcp.py` + `test_work_parallel_owner_offline.py` + `test_work_mod_write_gate.py` **199 passed, 33 skipped, 63 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py310` (`mcp` + `owner_offline` + `restart_terminal` + `finalization` + `rework_worktree` + `mod_write_gate`): **386 passed, 80 skipped, 230 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py311` (동일 6개 스위트): **383 passed, 80 skipped, 233 subtests passed**.

## 발행·설치

- 커밋·npm `@beta`·7개 루트 해시 확인 설치 후 결과를 기록한다.
