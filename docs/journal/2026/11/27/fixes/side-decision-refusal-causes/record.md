---
type: fix-record
title: 사이드 "결정 JSON 없음" 거절의 세 원인 구분과, 게이트가 보류한 결정의 유실
description: 사이드 답 거절이 서로 다른 세 원인을 한 문장으로 합치던 문제와, ACK·대기 확인 게이트가 보류한 올바른 결정이 다음 답이 산문이면 invalid_answer로 사라지던 결함을 고친 기록
tags: [side-intake, decision-json, refusal-text, wait-check, ack, andy, fail-first]
timestamp: 2026-11-27T19:50:00Z
---

# 사이드 결정 거절 원인과 보류 결정 유실

## 보고와 관측

- **Andy_Main 세션 메시지(2026-11-27 19:14Z):** Daniel이 화면으로 지적했다. 거절 문구 `side answer carries no decision JSON`은 다음 세 경우를 구분하지 못한다.
  - (a) 결정 후보 객체가 없음
  - (b) JSON 파싱 실패
  - (c) 객체는 있지만 `decision` 객체가 없음
  - 교정 안내도 모든 사유에 같은 템플릿이다.
- **Andy 이벤트 `965d84239d22`(Walkie #163158 pending_reminder, 액터 `agent-a8b2ddf9ca2e582b0`) 경과:**

| 시각 | 액터 답 | 결과 |
| --- | --- | --- |
| 18:26:30Z | 산문 2,347자 | 형식 교정을 받았다 |
| 18:27:13Z | 올바른 결정 JSON(`no_reply:true`, refs 2개, `expected_version 12786`) | 대기 확인 게이트가 n869·n1584 등의 `wait_checks`가 없다며 보류하고 교정을 보냈다 |
| 18:28:36Z | 조회 결과를 산문 303자로 보고 | `side answer carries no decision JSON`으로 실패 |

  - 최종 결과: `invalid_answer`, 격리. 18:28:45Z에 원문을 메인으로 전달했다.
  - **두 번째 답의 결정은 이미 올바랐는데 버려졌다.**

## 원인 (코드)

- **세 원인 합침:** `parseDecisionCandidate()`는 실패하면 무엇이 틀렸든 `null`만 돌려준다. 그래서 `decisionOf()`가 같은 문장을 던진다.
- **보류 결정 유실:**
  - `completeSide()`는 게이트가 보류한 결정을 어디에도 남기지 않는다.
  - 게이트 교정에 답한 다음 답이 산문이면, 최근 메시지 6개 중 결정이 있는지 찾는다(`$.session.messages`).
  - 이 경로에서 결정을 찾지 못하면 실패로 끝난다. 이 경로가 실제 이벤트에서 왜 결정을 찾지 못했는지는 확인하지 못했다(🟡).

## 수정 (`side-intake.js`)

- **원인별 거절 문구:**
  - `parseDecisionCandidate(candidate, why)`가 이유를 모은다.
  - `noDecisionReason()`이 아래 셋 중 하나를 고른다.
    - `a JSON object was found, but it has no decision object (top-level keys: …; decision is a string)`
    - `the JSON did not parse (<JSON.parse 메시지>)`
    - `no JSON object containing "decision" was found in the answer`
  - 앞부분 `side answer carries no decision JSON`은 그대로다. 교정 대상 판정(`CORRECTABLE`)과 기존 로그 검색이 그대로 맞는다.
  - 이 문구는 교정 템플릿 `mod_side_correction`의 `{reason}` 자리에 그대로 들어간다. 템플릿(관리형 텍스트)은 바꾸지 않았다.
- **보류 결정 보존:**
  - 게이트가 결정을 보류하면 그 결정을 `actor.gateHeld`에 둔다.
  - 게이트 교정 뒤 답에서 결정을 찾지 못하면 보류한 결정을 커밋한다. 이 경우를 debug 로그에 남긴다.
  - 라우터는 커밋할 때 이 결정을 다시 검증한다.
  - 게이트 교정이 없었던 형식 오류는 기존 규칙(교정 1회 후 실패)을 그대로 따른다.

## Andy_Main 건의 중 이번에 하지 않은 것

- 교정 템플릿을 사유별 한 줄로 줄이는 것
- 교정 대상이 아닌 사유의 안내문
- 실패한 후보 문자열 앞 200자 출력

모두 관리형 텍스트와 교정 흐름을 바꾸는 별도 변경이라 이번 수정에 넣지 않았다. 다음 작업으로 추적한다.

## 검증

- **fail-first:**
  - `test_side_wait_check.py::test_a_decision_held_back_by_the_wait_gate_commits_when_the_next_answer_is_prose`
    - 시나리오: `prose_gate_prose`, `gate_then_prose`
    - 수정 전: resolve 0회, `invalid_answer` 1회
  - `test_side_intake_mod.py::test_fresh_side_dispatches_without_priority_and_recovers_invalid_answers`
    - 원인별 세 문구와 파싱 실패 문구를 확인한다.
    - 수정 전에는 세 경우 모두 같은 문장이었다.
- **기존 테스트 조정:**
  - `test_side_ack_gate.py`의 `ack_then_prose`는 이제 보류 결정을 커밋하는 시나리오다(교정 1회, resolve 1회).
  - 형식 교정이 따로 남는지는 새 시나리오 `ack_then_bad_summary`가 확인한다. 라우터는 빈 summary를 거절한다.
  - 옛 문장 전체를 고정했던 두 테스트(`test_side_intake_mod.py`, `test_side_actor_failures_hotfix.py`)의 기대 문구를 갱신했다.
- **Windows 임시 격리:** `test_side_ack_gate`, `test_side_wait_check`, `test_side_actor_failures_hotfix` 39 passed. `test_side_intake_mod`의 해당 테스트들도 통과했다.
- 영향 회귀와 배포는 [사이클 8](../../deployments/cycle-8/record.md)에 기록한다.
