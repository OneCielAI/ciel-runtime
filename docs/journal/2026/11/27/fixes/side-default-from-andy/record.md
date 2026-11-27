---
type: fix-record
title: Andy에 저장된 사이드 액터 지침을 기본값으로 채택
description: 사용자가 Andy 워크스페이스에 저장해 둔 사이드 지침(work_side_intake.instruction, instruction_replace=true)을 DEFAULT_SIDE_INSTRUCTION과 모드 PROMPT의 새 기본값으로 반영
tags: [side-intake, instruction, default, andy, managed-prompt]
timestamp: 2026-11-27T10:30:00Z
---

# Andy 사이드 지침을 기본값으로 채택

## 지시

- 사용자(2026-11-27): "앤디 세션에 있는 사이드 액터 프롬프트는 내가 변경해 둔 내용을 가져다가 기본값으로 업데이트해"

## 원본

- Andy 워크스페이스 `aae7db361c7f/config.json`의 `work_side_intake.instruction`이다. 12,159자이며 `instruction_replace=true`다.
- 이 파일은 읽기만 했고 바꾸지 않았다(상태 파일 수정 금지).
- 기존 기본값과의 차이(사용자 변경):
  - 역할에 "요청된 메시지에 부모의 아이디와 글 루트의 아이디가 포함되어 있습니다. 그것을 이용해서 관련 자료를 검토합니다." 추가
  - "아이디 기준 진행 중인 항목이면" 추가
  - "메인이나 포크 세션에게 메시지를 전달할 때는 … Walkie에서 전체 요약 … 추적할 수 있는 아이디 제공" 문단 추가
  - 조사 수칙 3: 해당 스레드는 필요하면 처음부터 끝까지 읽고, 채널 전체 읽기는 금지하며, 워크 보드는 매치하는 아이디로 먼저 찾는다.
  - 줄바꿈 없는 문단 형식

## 반영

- `side_intake_settings.DEFAULT_SIDE_INSTRUCTION`과 `side-intake.js`의 `PROMPT`를 같은 텍스트로 바꿨다. 두 값이 바이트 단위로 같은지는 테스트가 확인한다.
- 도구: `.local/tmp/monitor/adopt_side_default.py`.
- **사용자 문구 외에 바꾼 것(전부):**
  - 오타 2곳: "아디" → "아이디", "ㅁ큐" → "큐".
  - ACK 문단 끝에 기존 기본값의 스키마 문장을 붙였다. 내용은 "먼저 messages.work.get으로 대상의 현재 version을 확인합니다(상태가 없으면 0) … 새 UUID client_id … 서버 성공을 확인한 경우에만 ACK로 기록합니다"다.
  - 사용자의 저장본은 이 문장이 기본값에 들어가기 전 것이다. 이 문장은 Andy #161497(ACK에 version·client_id 누락)로 입증된 운영 스키마 요건이고 `test_work_texts`가 고정한다. 그래서 빼지 않았다.
- 번호 "2."가 두 번 나오는 것은 사용자 원문 그대로다. "그 결과 회신은 3번대로"라는 참조가 3번을 가리키기 때문이다.
- Andy의 저장 값은 그대로 둔다. 저장 값이 사용 중이므로 Andy의 라이브 동작은 이 반영과 무관하다.
- 다른 호스트(Celly·KOBE·Prody·Betty)는 저장 값이 없으면 설치 후 이 기본값을 쓴다.

## 검증

- `tests/test_side_background_prompts.py` 9 passed(PROMPT와 기본값 바이트 동일).
- `tests/test_side_instruction_view.py` 6 passed.
- `tests/test_work_texts.py` 10 passed.
- `tests/test_work_gate_check.py` 27 passed.
- `tests/test_side_intake_mod.py`는 줄바꿈에 고정돼 있던 두 문구(`operation_id\n   "messages.work.update"`, `따로\n   답하지 않습니다`)를 사용자 문단 형식으로 고쳤다.
- 전체 회귀는 [사이클 4](../../deployments/cycle-4/record.md)에 있다.
