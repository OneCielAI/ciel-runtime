---
type: fix-record
title: 대기 항목 이벤트의 조건 확인 게이트와 조회 허용 교정문
description: 대기 중인 Work 항목에 관한 SSE 이벤트에서 사이드 액터가 조건을 확인하지 않고 update로 끝내 차단이 하루 종일 남던 결함(#162105, n2892)을, 프롬프트의 wait_items와 답의 wait_checks 확인, 조회를 허용하는 교정문으로 수정
tags: [side-intake, wait, release_waits, release_hint, andy, n2892, fail-first]
timestamp: 2026-11-27T10:30:00Z
---

# 대기 항목 이벤트의 조건 확인 게이트

## 사용자 지적

- "사이드액터가 GIT이나 외부 메시지를 받고, 해당 포크 세션의 차단을 해제를 못해주고, 그냥 무시하는 경우"
- "#162105 이 아이템의 경우 사이드액터가 메시지를 받고 아무런 것도 안 해서 하루 종일 영구 차단되어 있는 거야"
- "모니터링 이전에 사이드 액터가 차단된 아이템에 대해서 보다 깊은 조사를 할 수 있게 해 주는 게 중요"

## 라이브 근거 (Andy 9515)

- 보드 n2892(P0 vmi3610452 rootless Docker)의 waiting_on은 "Daniel Yun SSH passkey approval on IT Hotline 162105 (… req d353d85a)"였다.
- release_hint는 다음과 같았다.
  - condition: `ssh_access.status(request_id='d353d85a-…')` returns approved
  - lookup: 그 조회 또는 메시지 162105 읽기
- 04:58:59Z에 Walkie 162796(Eric, root 162095)이 도착했다. 내용은 "162105 SSH 승인 완료: Daniel Yun님이 2026-11-26 14:21:04 UTC에 이미 승인"이었다.
- 영수증 `87aaf0d668`은 05:10:39Z에 processed됐다. 결정은 `update` n2892였고 `released_waits=[]`였다.
- 사이드 액터 전사 `agent-a9cb02df451031e06.jsonl`:
  - 05:07:40Z 첫 답에는 도구 호출이 0건이었다. 내용은 "결정 JSON을 그대로 출력하지 않습니다 … 조회를 이 세션에서 실행할 수 없고"였다.
  - 호스트 교정문(코드 리터럴)은 "side answer carries no decision JSON … **Do not call tools or send anything.**"였다.
  - 05:10:33Z 두 번째 답은 "이 세션은 도구 호출 금지 지시로 직접 조회·검증을 못 했고 … 대기 해소 여부는 원래 담당 포크/메인이 판정"이었다. 대기를 풀지 않았다.
- 같은 10시간 동안 Andy 사이드 액터 99개 중 26개가 도구 호출 0건이었다. Walkie MCP 도구 자체는 사용 가능했다(`walkie_call_tool` 117회).

## 원인 (코드)

- `side-intake.js` `correctionText()`의 일반 교정문이 모든 교정에서 도구 호출을 금지했다. 그래서 대기 조건을 확인하라는 지시(조사 수칙 2·`release_hint`)를 교정 후에는 따를 수 없었다.
- 대기 항목에 관한 답이 조건을 확인했는지 호스트가 보지 않았다. 확인 없는 `update`가 그대로 커밋됐다.

## 수정

- **프롬프트:** 이벤트가 가리키는 대기 항목을 `wait_items`(id, waiting_on, release_hint)로 넣고, 관리형 규칙 `mod_side_wait_items`를 함께 넣는다.
  - 대상은 미완료이고 waiting_on이 있는 항목 가운데 둘 중 하나에 해당하는 것이다.
    - ref·refs에 이벤트의 대상·루트·부모 메시지 id가 있다.
    - waiting_on·release_hint에 적힌 메시지·요청 id가 이벤트 본문에 있다.
  - 대화 id는 여러 항목이 공유하므로 쓰지 않는다.
  - 결정이 대기 중인 항목에 대한 `update`·`prioritize`이면 그 항목도 대상이다. 메인 에스컬레이션(`advise` 등)은 이 게이트에서 빠진다. 처음에는 결정 항목을 action과 무관하게 넣었는데, 실제 백엔드 테스트의 `advise` 흐름이 교정 대기로 멈췄다(Windows `test_side_real_backend` 10 실패, 180 s 시간 초과). 그래서 범위를 좁혔다.
- **답 확인(`requireWaitCheck`):** 대상마다 다음 중 하나가 있어야 커밋한다.
  - `decision.release_waits`
  - 최상위 `wait_checks`의 `{id, lookup, observed, met}`
- **거부와 교정:**
  - 둘 다 없으면 `side wait check required: <id>`로 거부한다.
  - `met=true`인데 풀지 않았으면 `side wait check met but not released`로 거부한다.
  - 한 번 교정하며, 교정문은 관리형 `mod_side_wait_check`다. 항목, 조건, lookup을 적고 읽기 전용 조회를 지시한다.
  - 두 번째에도 같으면 `invalid_answer`로 기록되고, 기존 실패 경로를 따른다(종료되면 원문이 메인에 간다).
- **일반 교정문:** 관리형 `mod_side_correction`으로 옮겼다. "Do not call tools"를 빼고, 이벤트 프롬프트가 요구하는 읽기 전용 조회는 허용한다. 재전송과 직접 변경은 계속 금지한다.
- **적용 제외:** gate_check(게이트 재검사 액터)는 자체 지시를 따르므로 이 게이트에서 빠진다.
- `wait_checks`는 라우터 결정 필드가 아니다. 커밋하는 결정에는 들어가지 않고 debug 로그에만 남는다.

## 검증

- **fail-first:** `tests/side_wait_check_harness.mjs`를 HEAD(6309706)의 `side-intake.js`로 실행했다([red-head-harness.json](evidence/red-head-harness.json)).
  - 확인 없는 update가 바로 커밋됐다(resolves 1, 교정 0).
  - `wait_items`가 없었다.
  - prose 첫 답에 대한 교정문에 `Do not call tools`가 있었다.
- **수정 후:** `tests/test_side_wait_check.py` 7/7.
  - 프롬프트에 n2892만 있고 lookup이 함께 있다. 같은 대화의 n2771은 들어가지 않는다.
  - 확인 없는 답은 교정 1회 후 커밋된다. 교정문은 n2892와 `ssh_access.status`를 적고, 도구 금지 문구가 없다.
  - 두 번 확인이 없으면 `invalid_answer`로 기록되고, 사유에 `side wait check required: n2892`가 들어간다.
  - `met=true`는 해제로 커밋된다.
  - release 또는 check가 있으면 바로 커밋된다.
  - 다른 스레드에는 대상이 없다.
  - prose 교정문은 읽기 전용 조회를 허용한다.
- `tests/test_side_ack_gate.py` 6/6, `tests/test_work_texts.py` 통과. 회귀 결과는 [사이클 4](../../deployments/cycle-4/record.md)에 있다.
