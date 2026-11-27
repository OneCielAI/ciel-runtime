---
type: fix-record
title: ACK·대기 확인 게이트가 사이드 결정을 버리던 회귀와 이벤트 전용 ACK client_id
description: 사이클 4의 ACK 게이트와 대기 확인 게이트가 교정 후에도 부족하면 결정 전체를 invalid_answer로 버려 승인에 따른 대기 해제가 유실되던 회귀를 고치고, 사이드가 지어낸 예시 UUID 대신 이벤트 전용 client_id를 주도록 한 수정
tags: [side-intake, ack, wait-check, release_waits, regression, andy, fail-first]
timestamp: 2026-11-27T16:50:00Z
---

# ACK·대기 확인 게이트의 결정 유실 회귀

## 보고와 사실

- 보고자는 Andy_Main(세션 메시지 2026-11-27 16:0xZ)이다. 오늘 사이드 invalid_answer 3건이 원문 전달로 떨어졌다. 163087 승인은 메인에 11분 53초 늦게 도착했고, 그 사이 15분짜리 위임이 만료됐다.
- 영수증 상세는 [live-failures](evidence/live-failures.txt)에 있다.
  - `55ec25fcc867`(163087 승인):
    - 액터는 두 번 모두 n1584 대기 해제를 결정했다(정답).
    - 다른 대기 항목 n869·n2778·n2967·n2968의 `wait_checks`가 없다는 이유로 [대기 확인 게이트](../side-wait-check/record.md)가 거부했다.
  - `9d0578fb1be6`(163057 승인):
    - 액터는 n2967·n2968 해제를 결정했다.
    - ACK `client_id`로 예시 UUID `a1b2c3d4-e5f6-7890-abcd-ef1234567890`를 써서 Walkie가 409(idempotency key reused)를 돌려줬다.
    - 이어서 [ACK 게이트](../side-ack-gate/record.md)가 거부했다.
  - `b386e8da7270`(pending_reminder):
    - 대기 확인 교정이 교정 기회 1회를 써 버렸다.
    - 다음 답의 요약 길이 오류(`side decision needs bounded summary and reply`)는 교정 없이 실패했다.
- 세 액터가 만든 `client_id`는 모두 교과서 예시 UUID였다. 다른 이벤트끼리 같은 키를 쓰면 충돌한다.

## 원인 (코드, 사이클 4의 제 변경)

- `completeSide()`가 `requireAck`와 `requireWaitCheck`의 거부를 다른 형식 오류와 똑같이 취급했다. 교정 1회 뒤에도 부족하면 결정 전체가 `invalid_answer`가 됐다.
- 두 게이트와 형식 교정이 `actor.corrected` 하나를 함께 썼다.
- ACK 지시문은 "새 UUID client_id"만 요구했고, 이벤트별 값을 주지 않았다.

## 수정

- **`side-intake.js` `completeSide()`:**
  - ACK·대기 확인 부족은 처음 한 번만 교정한다(`actor.gateCorrected`).
  - 교정 뒤에도 부족하면 결정은 그대로 커밋한다. 무엇이 빠졌는지는 debug 로그에 남긴다: `committing <id> without the ACK/the wait check after one correction (...)`.
  - 결정 자체의 형식 오류는 기존 규칙대로 별도 교정 1회 후 실패한다.
- **`correctSide()`:** 게이트 교정(`GATE_SHORTFALL`)과 형식 교정을 따로 센다.
- **ACK `client_id`:**
  - `ackClientId(receipt_id)`는 영수증 id 앞 32 hex를 UUID 모양(버전 4, variant 비트)으로 바꾼 값이다. 이벤트마다 다르고 재전송에는 같다.
  - 프롬프트에 `ack: {conversation_id, message_id, client_id, rule}`를 넣는다. rule은 관리형 텍스트 `mod_side_ack_client`다.
  - 교정문 `mod_side_ack_required`는 그 값을 그대로 쓰라고 지시한다(`{client}`).
- 대기 항목 매칭 규칙(`eventWaitTargets`)은 바꾸지 않았다.
  - 현재 보드로 다시 돌려 보면 163087은 n869("Superseded by n1025")까지 잡는다. 범위가 넓다는 근거다.
  - 다만 결정이 더 이상 버려지지 않으므로, 넓은 매칭의 비용은 교정 1회로 줄어든다.
  - 매칭 축소는 별도 판단으로 남긴다.
- **프롬프트·코드 구분:**
  - 모델이 읽는 새 문장은 관리형 텍스트 `mod_side_ack_client`와 고친 `mod_side_ack_required`다.
  - 커밋·교정 판정은 코드에서 한다.

## 검증

- **fail-first:** [red-before-fix](evidence/red-before-fix.txt), 6 failed.
- **수정 후:** `test_side_ack_gate.py`, `test_side_wait_check.py`, `test_work_texts.py` 26 passed.
  - ACK가 두 번 없으면 커밋되고 부족이 로그에 남는다.
  - memo 없는 ACK도 교정 1회 후 커밋된다.
  - 프롬프트와 교정문에 `eeeeeeee-eeee-4eee-aeee-eeeeeeeeeeee`가 들어간다.
  - ACK 교정 뒤 prose 답은 형식 교정을 따로 받는다.
  - 대기 확인이 두 번 없으면 커밋되고 n2892가 로그에 남는다.
- `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` 99 pass / 0 fail.
- 영향 회귀는 [사이클 6](../../deployments/cycle-6/record.md)에 있다.
