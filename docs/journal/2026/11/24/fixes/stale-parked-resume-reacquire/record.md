---
type: fix-record
title: 동시 재개 후 낡은 펌프 스냅샷이 중복 재획득을 시도하던 결함
description: parallelResumeReleased가 대기 중 P0 포크를 이미 running으로 재개한 뒤 낡은 hostStatus 스냅샷을 든 parallelPump가 중복 reacquire를 보내 거짓 parked-resume 실패와 5분 재시도 차단을 일으키던 결함의 증거, 수정, 검증
tags: [work-parallel, parked-resume, p0-reacquire, ciel-work]
timestamp: 2026-11-24T16:18:00Z
---

# 동시 재개 후 낡은 펌프 스냅샷이 중복 재획득을 시도하던 결함

## 관측 (Andy, 2026-11-24 14:44–14:45Z, 읽기 전용)

- Andy 작업 n2594(리스 `4f4236f85cbe45398fa1fbb6788659ce`, P0 바운드 의도 `c941dfc83258`)는 14:44:34Z에 재개 메시지를 받았고 `work-parallel-p0.json` 전송 9번이 14:44:39Z에 `delivered`로 기록됐으며, 포크 `a90b7ef18810d8119`는 14:45Z 이후 실제로 도구 호출을 이어갔다.
- 그럼에도 14:45:53Z에 메인 대화에 다음 알림이 남았다.
  - `ciel-work: Work: parked lease 4f4236f85cb… clear_now=true seen=true cleared=true owner=own outcome=no slot: Parallel read failed: P0 reacquire exact waiting lease`

## 원인 (`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`)

- `parallelResumeParked`는 `hostStatus($)`로 읽은 스냅샷을 받아 대기 리스를 순회하며 `parkedResumeInFlight` 집합으로만 동시 재개를 막는다.
- 한 경로(`parallelResumeReleased`)가 이미 재개를 마쳐 `parkedResumeInFlight`를 비우고 리스를 `running`으로 바꾼 뒤, `listed.fresh()` 대기 중 낡은 스냅샷을 들고 있던 `parallelPump`가 같은 리스에 `event: 'reacquire'`를 다시 보낸다.
- 백엔드가 `P0 reacquire exact waiting lease`(일반 리스는 `not a slot-released wait`)로 거부하면, 호스트는 현재 리스 상태를 다시 확인하지 않은 채 `rememberParallelFailure('parked-resume', ...)`와 `parkedResumeTried`(5분 차단)를 기록하고 콘솔 알림을 출력했다.

## 수정

- `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`
  - 리스별 마지막 성공 재개의 `hostWrites` 스탬프(`parkedResumedStamp`)를 기록하고, 낡은 스냅샷(`stateStamp < parkedResumedStamp.get(lease.id)`)을 든 일반 펌프는 중복 `reacquire`를 건너뛴다(`resetParallelHostState`에서 초기화).
  - `answer` 전달 경로(`hasAnswer`)는 이미 실행 중인 포크를 `delivered`로 오인하지 않도록 분기한다.
  - `reacquire`가 `P0 reacquire exact waiting lease` 또는 `not a slot-released wait`로 거부되면 `hostStatus($)`를 1회 다시 읽어 리스가 이미 `running`/슬롯 보유 상태인지 확인한다. 이미 실행 중이면 거짓 `parked-resume` 실패와 5분 차단을 기록하지 않는다. 여전히 `waiting_input`·슬롯 없음이면 기존처럼 실패를 기록한다.

## 검증

- 실패 테스트 먼저(`tests/work_mod_harness.mjs`, `tests/test_work_mod_write_gate.py` 4건): 수정 전 4 실패([red-before-fix.txt](evidence/red-before-fix.txt)), 수정 후 4 통과.
- `claude plugin validate` 통과, `claude plugin test` 94 pass / 0 fail.
- 임시 격리 Windows pytest 영향 29개 파일: 29/29 통과(`WIN_FAIL=0`, `test_work_mod_real_backend.py` 117 passed / 3 skipped, `test_work_mod_write_gate.py` 151 passed / 33 skipped, `test_work_parallel_finalization.py` 114 passed, `test_work_parallel_rework_worktree.py` 80 passed 포함).
- 기존 Linux `ciel-runtime-test-py310`, `ciel-runtime-test-py311` 컨테이너 영향 29개 파일:
  - 28개 파일 실패 0(`test_wait_boost_pytest_bridge.py`는 참조 설계 문서를 tar에 포함해 재확인 후 py310/py311 모두 `RAN 1 failures=0 errors=0`).
  - `test_work_mod_real_backend.py`는 900초 단일 프로세스 한도에 걸려(py310 61건, py311 62건 시점) 4분할 재실행으로 전체 완료를 별도 확인한다.
