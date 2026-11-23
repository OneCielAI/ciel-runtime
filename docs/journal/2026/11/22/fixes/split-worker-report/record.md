---
type: fix-record
title: 증거 없는 포크 보고가 남긴 반쪽 상태
description: 포크 보고가 coordination 영수증을 먼저 쓰고 원장에서 거부되어, 리스는 running으로 슬롯을 잡은 채 run만 끝난 상태로 남던 Prody n1076 문제의 증거, 수정, 검증 기록.
tags: [work-mode, worker-report, coordination, prody]
timestamp: 2026-11-22T21:40:00Z
---

## 운영 관측 (읽기 전용, Prody, 2026-11-22)

- 모니터 finding: `slot held, fork absent since 20:24: n1076 8e0625bf P4 running`.
- lease `8e0625bf…`: `running`, `slot=True`, host `ad9140a0f8c12230b`.
- coordination run: `state=handed_off`, `exit={kind: handoff, digest: 9b45050815a1…}`.
- 수신 기록: question `85cdfd561c69`(20:08:22), question `35083ca22335`(20:08:36), handoff `9b45050815a1`(20:18:50).
- native: `idle` 상태가 20:23:59에 기록됐고, `missing_since`는 20:24:18이다.
- 포크 transcript의 `work_parallel_report` 결과:
  - `20:08:22 question` → `question evidence required`. 그런데 이 시각에 question 영수증 `85cdfd561c69`가 생겼다.
  - `20:08:36 question` → ok. 리스는 `waiting_input`이 됐다.
  - `20:18:50 handoff` → `handoff proof required`. 그런데 이 시각에 handoff 영수증 `9b45050815a1`이 생겼다.
  - `20:19:02 handoff` → 같은 오류. 영수증은 이미 있어 그대로 반환됐다.
  - 이후 포크는 끝났다(idle 20:23:59).

## 원인

- `_worker_event`는 coordination 영수증(`persist_receipt`)을 먼저 쓰고, 그다음 원장 전환(`report_question` / `report_completion` / `report_handoff`)을 한다.
- 원장 전환이 요구하는 `evidence`/proof 텍스트는 원장 쪽에서만 검사된다. 그래서 증거가 빠지면 영수증만 남고 원장은 거부된다.
- handoff의 경우: run은 `handed_off`로 끝났지만 리스는 `running`과 슬롯을 그대로 유지한다. 호스트(`parallelEnsureExitContract`)는 이 영수증을 종료로 보고(`parallelHasReceipt`) 포크만 정지하므로, 슬롯을 푸는 경로가 없다.

## 수정 (`work_parallel_mcp.py`)

1. **원인 차단:** `_worker_event`가 영수증을 쓰기 전에 원장과 같은 규칙으로 증거를 검사한다. handoff는 `text(evidence, 500)`, 나머지는 `text(evidence)`를 확인하고, 실패하면 같은 오류 문구로 거부한다.
2. **이미 생긴 반쪽 handoff 정리:** `_settle_split_handoffs`를 추가하고 호스트의 `native_observation`(약 15초마다)에서 호출한다. 아래 조건을 모두 만족하면 그 영수증으로 `report_handoff`를 커밋해 리스를 `unreported`로 바꾸고 슬롯을 해제한다. P0 intent도 닫는다.
   - 리스가 `running`이고 슬롯을 잡고 있다.
   - run의 exit가 같은 리스의 handoff 영수증이다.
   - 같은 agent의 native 상태가 idle 계열(`idle`/`completed`/`failed`/`killed`)이고, 그 상태가 영수증 이후에 바뀌었다. 즉 포크의 보고 호출이 끝났다는 증거가 있다.
   - 상태가 `running`이거나, idle을 영수증보다 먼저 본 경우에는 정리하지 않는다.
   - completion 반쪽은 영수증에 artifact가 없어 이 방식으로 정리할 수 없다. 1번 수정으로 새로 생기지 않는다.

## 검증

- 실패 테스트를 먼저 썼다(`tests/test_work_parallel_native.py::SplitReportTests`):
  - `test_a_report_missing_its_evidence_writes_no_receipt`: 수정 전에는 handoff, question, completion 세 경우 모두 영수증이 남아 실패했다. 수정 후 통과한다.
  - `test_a_split_handoff_is_settled_once_the_fork_turn_ended_after_it`: 수정 전에는 리스가 `running`으로 남아 실패했다. 수정 후 통과한다.
  - `test_an_idle_seen_before_the_receipt_does_not_settle_it`: 수정 전후 모두 통과한다.
- `tests/test_work_parallel_native.py` 15건 통과.
- Celly·Andy 실제 상태 임시 복사본에 정리 함수를 적용했다. 해당하는 리스가 0건이었고 변경도 0건이었다(오탐 없음).
- Prody 상태 복사는 SSH 연결이 끊겨(`Connection reset`) 하지 못했다. 원격 임시 파일은 정리됐다.

## 남은 항목

- Prody n1076은 설치 후 라우터가 새로 떠야 정리된다. 그 전에는 리스가 슬롯을 계속 잡는다.
- completion 반쪽이 이미 남아 있는지는 Celly·Andy에서 0건을 확인했다. KOBE·Prody는 확인하지 못했다.
