---
type: fix-record
title: 결정 저장 직전 사이드 claim 만료가 올바른 결정을 invalid_answer로 버리던 결함
description: 사이드 액터가 ACK와 결정 JSON까지 낸 뒤 work_side_understanding 준비 호출이 side claim expired로 거절되어 결정이 저장되지 않고 영구 격리되던 결함을, 같은 세대의 리스 갱신 후 한 번 재준비하는 방식으로 고친 기록
tags: [side-intake, lease, claim-expiry, invalid_answer, andy, fail-first]
timestamp: 2026-11-27T19:05:00Z
---

# 결정 저장 직전 claim 만료

## 관측 (Andy `9515`, 2026-11-27)

| 이벤트 | 메시지 | 액터 시작 | 실패 | 사유 |
| --- | --- | --- | --- | --- |
| `9d531bca5657` | Walkie #163167 | 17:44:02Z, 재시도 17:52:04Z | 17:57:26Z | `work_side_understanding: side claim expired` |
| `b3a33f1532ee` | Walkie #163164 | 17:47:05Z | 18:02:08Z | 같음 |

- 두 영수증 모두 `category invalid_answer`, `quarantined True`, `retryable False`다. 원문은 메인으로 전달됐다(17:57:45Z, 18:02:22Z append).
- `b3a33f1532ee` 액터(`agent-af27fcb40259ab539`)는 18:00:16Z에 Walkie ACK에 성공했다(`state ack, version 1`). 18:01:37Z에는 n2981에 관한 결정 JSON을 출력했다. 이벤트 영수증 id에서 만든 client_id도 그대로 썼다.
- 라우터 로그(`router.log`, 로컬 시각)의 `work_side_renew` 간격:
  - 11:47:43–11:52:08
  - 11:52:59–11:58:05
  - 11:58:15–12:03:04
  - 세 간격 모두 기본 리스 120초보다 길다.
  - 로그에는 요청 인자와 응답 상태가 없다. 각 갱신의 성공 여부와 간격을 만든 호출은 확인하지 못했다.
- 두 액터는 사이클 6 라우터 교체(17:37–17:42Z) 이후에 시작됐다. 교체가 원인이라는 증거는 없다.

## 원인 (코드)

- `submitUnderstanding()`은 `work_side_understanding` prepare를 한 번만 부른다.
- 라우터 `verify_claim()`은 리스가 지났으면 `side claim expired`를 던진다(`side_intake_processing.py:320`). 이 시점에는 제안이 아직 저장되지 않았다.
- 예외는 `completeSide()`의 reason이 된다. `correctSide()`의 교정 대상이 아니므로 `recordFailure(..., 'invalid_answer')`로 영구 격리된다.
- 결과적으로 이벤트를 가진 액터가 같은 세대로 낸 올바른 결정이 사라진다.
- 같은 세대의 만료 claim은 renew가 되살린다(`renew()`, 2026-11-23 수정). 다른 claim이 이벤트를 가져가면 세대 불일치로 거절된다.

## 수정

- `side-intake.js` `submitUnderstanding()`:
  - prepare가 정확히 `Side router operation failed: work_side_understanding: side claim expired`로 거절된 경우에 한해, 같은 `receipt_id/generation`으로 `work_side_renew`를 한 번 부른다.
  - 그다음 prepare를 한 번 더 부른다.
  - renew가 거절되면(다른 claim, 정산됨, 격리됨) 그 예외가 그대로 실패가 된다. 두 번째 prepare 실패도 마찬가지다.
  - 세대를 넘어 결정을 쓰지 않고, 무한 재시도도 없다.
- 모델이 읽는 문장은 바꾸지 않았다. 코드만 바뀌었다.

## 검증

- **fail-first:** `tests/side_ack_gate_harness.mjs` `expired_before_prepare`, `test_an_expired_claim_still_owned_by_the_actor_does_not_lose_its_decision`.
  - 수정 전: resolve 0회, `work_side_fail` 1회(`invalid_answer`, `side claim expired`).
  - 수정 후: resolve 1회, 실패 0회.
- **임시 격리 Windows:** `test_side_ack_gate`, `test_side_intake_mod`, `test_side_intake_mcp`, `test_work_mcp_tools` → 171 passed, 1 skipped, 139 subtests passed.
- **Linux py310·py311** (`run_serial.sh`, 파일 단위, [로그](evidence/linux-py310-py311.txt)):
  - `test_side_ack_gate`: 9/0
  - `test_side_intake_mod`: 75/0
  - `test_side_intake_mcp`: 54/0, skip 1
  - `test_work_mcp_tools`: 34/0
- **모드 검사:**
  - `claude plugin validate` ciel-work·ciel-work-gate 통과.
  - `claude plugin test ciel-work` 첫 실행은 98/1이었다. 실패한 테스트는 `workflow.test.ts` "the gate check runs while main is busy…"의 `gateCalls 4` 기대(받은 값 3)다.
  - 이 변경으로 3회, HEAD 파일로 3회 다시 돌린 결과 모두 99/0이었다([plugin-repeat](evidence/cycle10-plugin-repeat.txt)).
  - 이 실패는 재현되지 않았다. 원인은 확인하지 못했다(🟡 타이밍 의존 가능성, 미확인).
- 배포와 라이브 효과는 [사이클 7](../../deployments/cycle-7/record.md)에 기록한다.
