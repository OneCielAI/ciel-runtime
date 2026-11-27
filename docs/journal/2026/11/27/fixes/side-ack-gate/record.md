---
type: fix-record
title: 사이드 호스트 Walkie ACK·note 확인 게이트
description: Walkie 안내가 messages.work.update state=ack와 memo를 먼저 요구하는 이벤트에서, 사이드 결정 커밋 전에 에이전트 자신의 상태를 되읽어 memo 있는 ACK가 없으면 "ACK와 note를 먼저 등록"으로 거부하고 한 번 교정
tags: [side-intake, walkie, ack, memo, andy, daniel, mod, fail-first]
timestamp: 2026-11-27T08:20:00Z
---

# 사이드 호스트 Walkie ACK·note 확인 게이트

## 요청과 근거

- Daniel Yun(Walkie DM 162826/162830): 사이드 액터가 ACK와 note, 본문 전송을 계속 실패한다. ACK와 note가 빠지면 실패 메시지가 "ACK와 note를 먼저 등록"을 명시해야 한다. 이 요청은 Andy_Main이 전달했다.
- 영수증: 162787(Daniel 사전질문)의 세 영수증 모두 `sent_via=null`이었다. Walkie `messages.work` 이력에서 Andy의 첫 ACK는 06:33:21Z다(수신 04:45:55Z).
- 이벤트 `data.agent_context.guidance`는 "메시지를 받자마자 … messages.work.update … state=\"ack\", version=0, client_id, memo … 상태 기록을 먼저 남긴다"를 요구한다.
- **코드 확인:** 사이드 호스트에 ACK 확인이 없었다. 지시문(역할 4)에만 "보낼 수 있다"로 적혀 있고, `completeSide`는 되읽기 없이 커밋했다.

## 설계

- `ackRequirement(event)`: 원본 CloudEvent의 guidance에 `messages.work.update`와 `state="ack"`가 있을 때만 적용한다. 대화와 메시지 id는 `data.conversation`·`data.message_id`, 없으면 `source_read`에서 가져오고 형식을 검증한다.
- `ackRecorded()`:
  - `walkie_identity`로 자기 에이전트 id를 얻는다(서버별 캐시).
  - `walkie_call_tool messages.work.get`으로 상태를 읽는다.
  - 자기 행의 현재 상태 또는 `history`에 memo 있는 `ack`가 있어야 통과한다. 다른 에이전트의 ACK, memo 없는 ACK는 인정하지 않는다.
  - Walkie origin/main `message-work.ts`의 `memo`·`history` 필드 기준이다.
- `requireAck()`는 커밋 전(`submitUnderstanding` 이전)에 실행한다.
  - 되읽기가 성공했는데 ACK가 없으면 `side ACK required: ACK와 note를 먼저 등록하십시오 …`로 거부한다.
  - 되읽기 자체가 실패하면 누락을 증명하지 못한 것이므로 막지 않고 debug 로그만 남긴다.
- 교정: `CORRECTABLE`에 포함했고, 교정문은 관리형 텍스트 `mod_side_ack_required`(work_texts.py → texts.generated.js)다. 기존 교정문의 "도구를 부르지 말라" 대신 `messages.work.get` → `messages.work.update(state="ack", version, client_id, memo)` 후 같은 JSON에 `sent_via`를 넣어 다시 답하라고 지시한다. 두 번째에도 없으면 `invalid_answer`로 기록되고, 사유에 "ACK와 note를 먼저 등록"이 들어간다.
- **프롬프트·코드 구분:** 모델이 읽는 새 문장은 관리형 텍스트 하나뿐이다. 판단은 코드다.

## 검증

- **fail-first:** `tests/test_side_ack_gate.py` + `tests/side_ack_gate_harness.mjs`(실제 `side-intake.js`의 `completeSide`, 스크립트 라우터·Walkie). 수정 전 6개 중 3개가 실패했다(교정 없음, 커밋됨).
- **수정 후 6/6:**
  - 누락 → 교정(ACK 문구, 메시지 41, 도구 허용) → ACK 후 커밋 1
  - 두 번 누락 → `invalid_answer`, 사유에 "ACK와 note를 먼저 등록"
  - memo 없는 ACK 거부
  - memo 있는 ACK(이후 processing)는 바로 커밋
  - ACK 안내 없는 이벤트는 되읽지 않음
  - 되읽기 실패는 막지 않음
- `tests/test_work_texts.py` 통과(생성 파일 일치).
- `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` **99 pass / 0 fail**.
- 회귀 결과는 [사이클 4 회귀](../../deployments/cycle-4/record.md)에 있다.
