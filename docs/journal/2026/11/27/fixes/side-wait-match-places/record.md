---
type: fix-record
title: 대기 항목 매칭이 대화방·회사 UUID로 과하게 잡히던 결함
description: 2fe04af 배포 직후 Andy의 agent.pending_reminder 이벤트에서 대기 확인 게이트가 대화방·회사 UUID만 공유한 항목까지 대상(8건)으로 잡아 사이드 액터가 29회 조회했다. 이벤트가 대화·회사로 쓰는 UUID를 증거에서 빼도록 수정
tags: [side-intake, wait, matching, andy, fail-first, live-observed]
timestamp: 2026-11-27T12:45:00Z
---

# 대기 항목 매칭의 대화방·회사 UUID 과매칭

## 라이브 근거 (Andy, 2fe04af 배포 후 첫 대기 항목 이벤트)

- 12:19:58Z 이벤트 `3556200a0ca7`은 `agent.pending_reminder`이고 `source_context=none_referenced`다.
  - payload에는 루트 63773과 61802가 있고, 대화 `d44ae89f…`·`256a28fd…`와 회사 `9ab76ce4…` UUID가 함께 들어 있다.
- 사이드 액터 `a0b7f5d691`의 프롬프트에 `wait_items` 8건이 들어갔다(n869, n2228, n2554, n2778, n2927, n2953, n2967, n2968).
  - 액터는 도구를 29회 불러 8건 모두 `wait_checks`로 보고했다(전부 `met=false`).
  - 12:25:42Z에 `answer`로 끝났다. 처리 시간은 5분 44초다.
- **게이트 동작:** 조건을 실제로 조회하고 결과를 남겼다. 이것은 의도한 동작이다.
- **과매칭:** 8건 중 5건(n2228, n2554, n2778, n2967, n2968)은 이 이벤트가 가리키는 글이나 요청과 무관하다.
  - n2228·n2554는 lookup이나 condition에 같은 대화방 UUID `256a28fd…`가 들어 있어 잡혔다.
  - n2778·n2967·n2968은 같은 대화방 UUID와, lookup URL 경로의 회사 UUID `9ab76ce4…`로 잡혔다.

## 원인 (코드)

- `eventWaitTargets()`의 `named()`는 waiting_on·condition·lookup에 있는 모든 UUID를 증거로 썼다.
- 이벤트의 대화방은 `source_ref.conversation_id` 하나만 제외했다.
  - 이 이벤트는 source_ref가 없어서 제외 대상이 비어 있었다.
  - 회사 UUID는 처음부터 제외 대상이 아니었다.

## 수정

- 이벤트 원문에서 대화·회사로 쓰인 UUID를 모은다.
  - 해당 필드: `"conversation"`·`"conversation_id"`·`"company"`·`"company_id"`
  - 해당 경로: `/conversations/<uuid>`·`/companies/<uuid>`
- 이 UUID들(`PLACE_ID`)과 source_ref 대화방은 어느 필드에 나오든 증거가 아니다.
- 메시지 id(5자리 이상)와 그 밖의 UUID(요청 id 등)는 계속 증거로 쓴다.

## 검증

- **fail-first:** `tests/test_side_wait_check.py::test_a_room_or_company_id_in_the_event_names_no_waiting_item`
  - 배포된 2fe04af 코드에서는 실패했다. 대화방 lookup 항목과 회사 경로 항목까지 잡아 `['nRoot', 'nRoom', 'nCompany']`를 반환했다.
  - 수정 후에는 `['nRoot', 'nRequest']`로 통과한다. 루트 id와 요청 UUID는 남고, 대화방·회사 공유 항목은 빠진다.
- **라이브 이벤트 재생:** 같은 이벤트와 같은 8개 항목을 수정된 `eventWaitTargets()`에 넣었다.
  - 결과는 `["n869","n2927","n2953"]`다.
  - 셋 다 이벤트가 직접 이름 붙인 루트(61802, 63773)로 잡혔다.
- `tests/test_side_wait_check.py` 8/8.
- 영향 회귀(8개 파일): Windows 8/8 파일 통과, Linux py310·py311 직렬 8/8 `failures=0 errors=0`, `claude plugin test ciel-work` 99/0.
