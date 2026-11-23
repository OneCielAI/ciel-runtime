---
type: feature-record
title: 포크 질문 대기의 보드 기록과 release_hint
description: work_parallel_report(question)가 waiting_on과 구조화된 release_hint를 보드 leaf에 먼저 기록하고, 게이트·사이드가 lookup으로 검증해 원래 리스를 재개하는 계약. 스킬 파일과 /work-mode help 포함.
tags: [work-mode, question-wait, release-hint, side, skill]
timestamp: 2026-11-23T08:45:00Z
---

# 요구 (계획 파일 `starry-brewing-candle.md` 추가 요구)

멈춘 항목에 해제 조건, 확인 방법, 선택적 추적 힌트(`release_hint`)를 보존한다. 힌트는 승인이 아니다. 실제 정지는 기존 `waiting_on`/blocked가 결정한다. P0/P1–P5 운영, 대기 필드, 해제 절차, 원래 포크 재개를 담은 스킬 파일과 `/work-mode help` 갱신.

# 변경

- `work_parallel_receipts._validate_detail` question: `waiting_on`과 `release_hint`는 둘 다 있거나 둘 다 없어야 한다. `waiting_on` 한 줄 1..120자. `release_hint` 키는 {condition, lookup, prompt}만, 600/600/400자. 둘 다 없는 기존 질문은 그대로 동작한다.
- `work_parallel_mcp._worker_question`·`_record_question_wait`: 배정과 형식을 먼저 검증하고, 보드 트랜잭션(CAS 충돌 시 4회 재시도)으로 리스 자신의 leaf에만 `waiting_on`·`release_hint`를 쓴 뒤 receipt를 남기고 리스를 park한다. 거부 조건(어떤 기록보다 먼저): 다른 아이템, 루트·비leaf, 완료, 기존 대기, 배정 없음. 상태와 상위 노드는 바꾸지 않는다.
- 사이드: `side_workload.project`와 `side-intake.js compactReleaseHint`가 유효한 힌트만 workload에 싣는다. 지시문(`side_intake_settings.DEFAULT_SIDE_INSTRUCTION`, JS `PROMPT` 동기)은 `release_hint.lookup`을 읽기 전용으로 조회하고 condition을 엄격 검증하며, 현재 `waiting_on`에 대해 명시적으로 주어진 원래 답으로만 해제하라고 한다. 게이트 프롬프트(`gate_actor`·`gate_recheck`)도 같은 기준.
- `work_texts.mod_fork_rules`: 질문 대기 계약 전문. `register.js` HELP에 P0 별도 레인·P1–P5 슬롯·리스 재사용·submitted/waiting_input·스킬 안내. native dispatch 대체 프롬프트의 보고 도구를 `work_parallel_report`로 정정.
- 스킬 `claude_mods/ciel-work/skills/work-operations/SKILL.md`(신규). 처음 이름은 `work-mode`였고 `claude plugin validate`·`claude plugin test`는 통과했다. 그러나 실제 Claude Code 2.1.330 격리 로드(08:37:35Z)에서 `session.start hook skipped: threw ciel-work: $.command.register: "/work-mode" refused: it is the plugin's /ciel-work:work-mode`가 나왔다. 스킬 이름이 플러그인 명령 `/work-mode`와 겹쳐 session.start 전체가 건너뛰어졌고, `/work-mode` 명령·사이드 시작·폴링이 모두 빠졌다. 실패 테스트 `test_no_bundled_skill_takes_a_registered_command_name`(수정 전 실패: 교집합 `work-mode`)를 추가하고 스킬을 `work-operations`로 바꿨다. HELP 문구도 `ciel-work:work-operations`로 정정.

# 검증

- 실패 테스트 먼저(`tests/test_work_parallel_finalization.py`): 기록 후 원래 리스 재개, 누락·형식 오류 거부, 잘못된 아이템·기존 대기·초과 길이 거부, receipt보다 보드 기록 먼저, 한 번만, 상태·상위 불변, 루트 거부. 그 밖에 `test_question_release_hint_is_a_strict_optional_release_pair`, `test_fork_rules_carry_the_complete_question_wait_contract`, `test_bundled_work_mode_skill_has_the_operating_guardrails`, 사이드 하네스 n21(유효 힌트 포함)·n22(형식 오류 힌트 제외).
- 격리 회귀 `qwait-p0c.015539` Windows·py310·py311 각각 663 실행/0 실패. `claude plugin test ciel-work` 94 통과.

# 배포 순서 호환 (2026-11-23 추가)

- 문제: 모드 파일은 세션 재로드만으로 반영되지만 라우터 Python은 라우터 재시작 전까지 옛 코드다. 옛 라우터(`432c8a8`)의 `_validate_detail` question은 필드 집합이 정확히 `question, blocking_todos, decision_needed`여야 하고, 추가 키는 `question fields: detail needs exactly ...`로 거부된다(`_require_fields`). 새 모드의 `mod_fork_rules`가 포크에 `waiting_on`·`release_hint`를 detail에 넣으라고 지시하면, 옛 라우터 위에서 포크의 질문 보고가 매번 거부된다.
- 수정: 계약을 별도 관리 텍스트 `mod_fork_question_wait`로 분리했다. `mod_fork_rules`는 배포본과 같은 OKF 규칙만 남는다. 라우터는 `work_parallel_status`에 `capabilities: ["question_wait"]`(`PARALLEL_CAPABILITIES`)를 싣고, 모드는 마지막 호스트 상태가 이를 광고할 때만 `forkRules()`에서 계약을 덧붙인다. 기본값은 false다.
- 실패 테스트 먼저: `test_question_wait_contract_reaches_a_fork_only_on_a_router_that_accepts_it`(옛/새 라우터 시나리오). 수정 전에는 새 라우터에서 계약이 없어서 실패했고, 텍스트 회귀 `test_the_mod_keeps_no_literal_copy_of_a_registry_text`도 실패했다. 수정 후 둘 다 통과. 라우터 쪽은 `test_parallel_status_advertises_the_question_wait_contract`.

# 남은 항목

- 이미 receipt는 있고 보드 대기는 없는 기존 `waiting_input` 항목은 임의로 해제하지 않는다. 처리 방침은 미결.
- 사이드가 승인 범위를 넓혀 대기를 푼 사례(Andy n2582, side event 1c420b87a1c6, Andy_Main 08:33Z 보고)는 이 지시문 변경 전 빌드에서 발생했다. 이 변경이 그 오판을 막는지는 검증하지 않았다.
- 미커밋·미배포.
