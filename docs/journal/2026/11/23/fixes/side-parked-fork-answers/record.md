---
type: fix-record
title: 사이드 점검이 질문으로 멈춘 포크를 보지 못하고 답할 수단도 없던 결함
description: Celly에서 포크 5개가 질문을 남기고 멈췄는데 10분 사이드 점검은 waiting_on·blocked 노드만 봐서 아무도 답하지 않았고, 메인이 보드를 손으로 돌았다. 원인 확인, 실패 테스트, 점검 대상·지침·답 전달·재개 수정, 검증.
tags: [side-fresh, gate-check, parked-fork, waiting_input, celly]
timestamp: 2026-11-23T16:20:00Z
---

# 사용자 보고 (2026-11-23, Celly 메인의 분석 포함)

- 메인이 보드를 손으로 확인한 이유: 질문이나 인계를 남기고 멈춘 할당을 찾아 답을 주고 다시 돌리기 위해서였다. side-fresh는 이 일을 하지 않았다.
- 분석 A: 점검 대상에 멈춘 포크가 없다. B: 지침에 그 일이 없다. C: 다시 돌리는 수단이 없다.

# 확인 (코드와 Celly 원장, 읽기 전용, 15:5xZ)

- ✅ A 확인
  - `work_gate_check.gates()`는 `waiting_on`, `status=blocked`, `release_hint`가 있는 노드만 고른다.
  - 질문 보고(`_worker_question`)는 `waiting_on`이 없으면 보드에 아무것도 적지 않고 리스만 `waiting_input`으로 바꾼다. 그래서 이 포크들은 점검 이벤트에 들어간 적이 없다.
  - 대기 기록이 없으면 사이드 이벤트도 생기지 않는다(`tick_rows`: 게이트가 없으면 None).
  - Celly 현재 세션의 `waiting_input` 7개
    - 대기 기록 없음 5개: n1236(11-21부터), n1341, n1380, n1382, n1384
    - `waiting_on`이 있어 게이트로 보이는 것 2개: n1320, n1343
- ✅ B 확인: `gate_actor`·`gate_recheck` 관리 프롬프트에는 `release_waits`, 검수 대기 알림(`ask_main`)만 있다. 포크 질문에 답하라는 지시가 없다.
- ✅ C 일부 확인: 사이드 결정 필드(`side_work_decision.FIELDS`)에는 대기 중인 포크에 답을 전달하는 필드가 없었다. 호스트의 재개(`parallelResumeParked`)도 보드에서 지워진 대기만 다뤘다(`wait_cleared`). 그래서 질문으로 멈춘 포크는 메인의 SendMessage로만 재개될 수 있었다.
- ❌ C 중 "인계·unreported로 끝난 할당을 다시 배정하는 수단이 없다"는 재현되지 않았다.
  - `unreported`는 후보를 막는 열린 리스 상태(`FENCE_STATES`)가 아니다. 그래서 인계로 끝난 아이템은 다시 후보가 되고 펌프가 새 포크를 배정한다.
  - 원장 이력: n1374는 같은 아이템에 리스 7개가 차례로 배정됐다(인계 4, host_salvage 2, 지금 P0 `running` 1). n1382도 인계 3개 뒤 네 번째 리스가 질문으로 멈췄다.
  - 이 아이템들이 멈춘 이유는 마지막 리스가 질문(`waiting_input`)으로 멈춘 것이었다. 이것이 A·B·C 일부와 같은 원인이다.

# 실패 테스트 먼저 (수정 전 모두 실패 확인)

- `tests/test_work_gate_parked_forks.py` 9건
  - 멈춘 포크 목록(`parked_forks`), 멈춘 포크만 있는 보드의 점검, 이벤트에 질문과 아이템 메모가 들어감
  - `fork_answers` 필드와 형식 검증, 다른 대화·원장을 읽지 못할 때의 거부
  - 실제 MCP 경로(`work_side_gate_check` → `work_side_claim` → `work_side_resolve`)
  - 두 관리 프롬프트의 지시
- `tests/test_side_fork_answers.py` 4건: 수정 전 모드는 `fork_answers`를 버렸고 아무도 재개하지 않았다.
  - 라우터가 필드를 광고할 때만 전송
  - 전달된 답으로 재개하고 메인에는 알리지 않음
  - 전달 실패는 사유와 답을 메인에게 알림
  - 거부된 답은 `ask_main` 알림 하나에 합침
- `test_work_mod_write_gate.test_a_side_answer_resumes_the_question_parked_worker_with_it`
  - register.js가 같은 리스의 슬롯을 다시 잡고, 답을 보내고, `running`으로 되돌리는지 확인한다.
  - 기존 펌프는 질문으로 멈춘 포크를 재개하지 않는다(그대로 유지).

# 수정

- 라우터
  - `work_gate_check.parked_forks`
    - 이 대화의 `waiting_input` 리스 중 슬롯이 없고, 아이템과 조상에 대기·차단 기록이 없는 것을 모은다. 기록이 있으면 이미 게이트다.
    - 행마다 질문(리스 evidence 800자)과 아이템의 최신 progress·note(각 600자)를 담는다.
    - 순서는 P0 → P1 → 나머지, 그다음 활성 목표 먼저, 그다음 오래 기다린 순이다.
  - 점검 이벤트에 "질문으로 멈춘 포크" 절을 넣는다. 한 라운드에 최대 6개이고, 다음 라운드는 이어서 돌아간다(`offsets.parked`).
  - 멈춘 포크만 있어도 점검이 돌고, 다이제스트에 리스 목록이 들어간다.
  - 사이드 결정 필드 `fork_answers` [{`lease_id`, `answer`, `source`}](최대 8개, 답 4000자, 출처 300자)
    - 라우터가 커밋 전에 원장을 읽어 이 대화의 대기 리스인지 확인한다.
    - 아니면 `unanswered_forks`에 사유를 남긴다.
    - 답이 있는 결정은 조용히 넘기는 결정(`no_reply`)이 될 수 없다.
- 관리 프롬프트(`gate_actor`, `gate_recheck`)
  - 질문의 답이 progress·note의 메인 결정, 승인 원문, 원본 스레드 답글에 기록돼 있으면 인용해 `fork_answers`로 답한다.
  - 포크가 스스로 만든 조건에서 멈췄고 보드에 그 조건을 넘는 결정이 있으면 같은 방식으로 답한다.
  - 새로 결정하지 않는다. 답이 기록돼 있지 않으면 `ask_main`으로 질문과 필요한 결정 한 줄을 보낸다.
- 모드
  - `side-intake.js answerParkedForks`가 register.js의 재개 경로로 리스마다 답 텍스트를 넘긴다.
  - 전달된 답은 로그만 남긴다.
  - 전달 실패나 거부는 그 receipt의 메인 알림 하나에 붙인다. `side_notice`는 receipt마다 한 번만 받기 때문이다. 다른 알림이 없으면 그것만 따로 보낸다.
  - `register.js`
    - `parallelResumeParked`가 `texts`를 받아 그 답으로 재개한다.
    - 슬롯 재획득과 P0 재획득 기록 경로는 그대로다.
    - 재개 증거 문구를 구분한다.
  - 보드가 아이템을 막고 있을 때의 거부 사유 문구를 사실대로 고쳤다. 실제로는 대기·차단 기록이 있거나 목표가 실행 단계가 아닌 경우다.
- 문구는 모두 관리 텍스트다: `mod_parked_answer`, `mod_parked_answer_undelivered`, `gate_actor`, `gate_recheck`.

# 혼합 버전

- 새 모드 + 옛 라우터: 옛 라우터는 `decision_fields`에 `fork_answers`가 없으므로 모드가 보내지 않는다(`routerDecision`). 점검 대상과 지침도 라우터 쪽이라 옛 동작 그대로다.
- 옛 모드 + 새 라우터(라우터만 먼저 재시작한 경우): 옛 모드의 `DECISION_FIELDS`가 `fork_answers`를 버린다. 사이드가 답을 골라도 전달되지 않고 메인에게도 알리지 않는다. 설치는 모드와 라우터를 함께 바꾸고 모드는 설치 즉시 재로드되므로, 이 상태는 재로드 전에 라우터만 재시작할 때만 생긴다.

# 검증

- Windows 격리 영향 테스트(10개 파일): 385 실행/0 실패(skip 49). relay·아키텍처 3개 파일: 275/0.
- 영향 회귀 `parked.161401`(20개 파일, `real_backend` 포함): Windows 격리·py310·py311 각 964/0(skip 101).
- CI `python-minimum` 상당(py310 unit shard, 차례로)
  - shard 2 1532 EXIT 0, shard 3 2037 EXIT 0.
  - shard 1은 첫 실행에서 `test_side_queue_starvation.test_the_blocker_check_actor_has_its_own_managed_instruction` 1건이 실패했다.
    - 원인: 이 테스트가 `gate_actor`의 옛 문구 "ask_main은 메인 검수 대기를 알릴 때만 씁니다"를 고정하고 있었다.
    - 이번 요구로 `ask_main` 사용처에 "답이 기록되지 않은 포크 질문"을 더했다. "제한된 경우에만 쓴다"는 테스트의 의도는 그대로 두고 문구만 새 지침에 맞췄다.
    - 수정 뒤 shard 1(`parkedfinal2.165009`) 1688 EXIT 0.
- CI quality 상당(같은 스냅샷): compileall, ruff 0.15.22, check:docs, npm pack 통과.
- `claude plugin validate` 통과, `claude plugin test` 94/0.
- 실엔진 로드(Claude Code 2.1.330, 격리 프로필, 실제 자격 증명 없음)
  - real-load-3: 14/15. `corrected_answer_committed`가 실패했다.
    - 원인: 드라이버 한계였다. 게이트 지침이 길어져 그 claim이 페이지로 나뉘었는데(`work_host_page` 2회), 드라이버의 MCP 기록은 페이지 응답에서 receipt id를 읽지 못했다(`claim: null`).
    - 제품은 그 이벤트를 처리했다: `work_side_resolve` 72.6초, 화면 `Ciel side wake a740d1d5b00d … "Ciel side (after your turn):"`.
  - 드라이버 수정: 페이지 응답이면 모드가 읽는 것과 같은 페이지를 다시 조립해 기록한다. 페이지는 1시간 동안 보관되는 다이제스트 주소 파일이라 읽어도 소모되지 않는다(`host_result_pages`).
  - [real-load-4](/docs/journal/2026/11/23/fixes/work-queue-restart/evidence/real-load-4/result.json): 15/15. 교정 이벤트 `ff1f57110f1a` processed. 해시는 `register.js` e3c6df33…, `side-intake.js` 4d69e279…, `texts.generated.js` 457e7126…(real-load-3과 같은 모드).
- 린트: 새 테스트 파일의 ruff 2건(F401, E731)을 고쳤다.
