---
type: fix-record
title: work_status parallel 블록이 빈 신원으로 계산되던 결함
description: work_status json 응답의 parallel 요약이 호출한 대화가 아니라 빈 신원으로 투영되어 runtime=codex와 미완료 작업 없음으로 보이던 결함을 호출 신원으로 투영하도록 고친 기록
tags: [work-status, work-parallel, identity, andy, fail-first]
timestamp: 2026-11-27T19:05:00Z
---

# work_status parallel 신원

## 보고

- Andy_Main은 2026-11-27 세션 메시지에서, `work_status`의 parallel 요약이 `runtime=codex`이며 미완료 작업이 없다고 표시된다고 보고했다.

## 원인 (코드)

- `work_mcp_tools.py` `_run()`의 `work_status` json 경로가 `project(ledger(), "")`를 불렀다.
- 그래서 호출한 대화의 신원이 아니라 빈 신원으로 투영됐다.

## 수정

- `project(ledger(), identity or "")`로 바꿨다. 호출 대화의 투영을 돌려준다.

## 검증

- **fail-first:** `tests/test_work_mcp_tools.py::test_status_json_parallel_block_is_this_conversations`. 수정 전에는 실패했고, 수정 후에는 파일 전체 34 passed다.
- **Linux py310·py311:** `test_work_mcp_tools` 34/0.
- 배포와 라이브 효과는 [사이클 7](../../deployments/cycle-7/record.md)에 기록한다.
