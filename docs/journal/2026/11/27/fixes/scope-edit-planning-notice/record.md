---
type: fix-record
title: 범위 편집으로 목표가 planning에 돌아갈 때 work_node_update 응답에 알리기
description: 제목·note 편집이 실행 중 목표를 planning으로 되돌려 포크가 배정되지 않는데, 응답은 "Updated nX"뿐이라 메인이 이유를 몰랐다. 응답에 관리형 문구 scope_planning을 붙인다
tags: [work-board, work_node_update, planning, managed-prompt, andy, fail-first]
timestamp: 2026-11-27T17:00:00Z
---

# 범위 편집의 planning 복귀 알림

## 보고

- Andy_Main(16:0xZ, 3번): 승인된 노드의 note를 고치면 목표가 executing에서 planning으로 돌아간다. 이것은 도구 설명에 적힌 계약이다.
  - 그러나 전이될 때 메인에게 경고가 없었다.
  - 재현: n2976(14:52Z note 수정 뒤 포크 없음).
- 도구 설명은 "Title and note are approved scope: changing either returns only that goal to planning for scope review"이다.
- 응답 본문(`work_mcp_tools._run`)은 "Updated n3 (pending). …"만 돌려줬다. 테스트에서 재현했다.

## 수정

- `work_mcp_tools._run` `work_node_update`:
  - 같은 트랜잭션 안에서 갱신 전후의 목표 단계를 비교한다.
  - 미완료 목표가 `executing`에서 `planning`이 되면 응답에 `scope_planning`을 붙인다.
- `scope_planning`은 `work_workflow_prompts.DEFAULTS`에 둔 관리형 텍스트다(관리 화면에서 편집 가능). 문구는 다음과 같다.
  - `PLANNING: goal {root} returned to the planning phase because its approved scope (a title or note) changed. No Work fork is assigned to it until work_execute approves the plan again. …`
- `_run`에 `state_dir`를 넘겨 관리형 오버라이드를 읽는다.
- **프롬프트·코드 구분:** 모델이 읽는 새 문장은 관리형 텍스트 하나다. 언제 붙일지는 코드가 판정한다.

## 검증

- **fail-first:** `tests/test_work_mcp_tools.py::test_a_scope_edit_that_returns_the_goal_to_planning_says_so`.
  - 수정 전 `'PLANNING:' not found in 'Updated n3 (pending). …'`.
- **수정 후:** `test_work_mcp_tools.py`, `test_work_texts.py`, `test_work_approval_workflow_surfaces.py` 51 passed.
  - progress 갱신은 승인을 유지하고 문구도 붙지 않는다.
- 영향 회귀는 [사이클 6](../../deployments/cycle-6/record.md)에 있다.
