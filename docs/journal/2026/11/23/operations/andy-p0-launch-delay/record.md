---
type: diagnostic-record
title: Andy n2626·n2633 P0 첫 예약→native 지연
description: 읽기 전용 원장 대조. 첫 예약 두 건의 작업공간 준비 실패와 이후 리스, MAX_LEASES 근거.
tags: [work-parallel, p0, workspace, andy]
timestamp: 2026-11-23T08:20:00Z
---

# 증거

payload·토큰 없는 발췌: [evidence.json](evidence.json). 원본 경로와 필드는 그 파일의 `source_files`에 있다.

| 항목 | 첫 예약(coordination archive) | 작업공간 실패 이벤트(item 기준 id) | 이후 리스(coordination run) | P0 intent | native 기록 |
|---|---|---|---|---|---|
| n2626 | 331d6d5c reserved 05:49:55Z, agent 없음, `workspace preparation failed before launch` | retained 05:52:53–05:58:08Z ×4, failed 05:58:07Z ×1 | a6104923 reserved 05:58:34Z, bound 05:58:39Z | dc0ac784 생성 05:49:55Z → bound 05:58:39Z | first_seen 05:59:15Z |
| n2633 | 1d272611 reserved 06:52:26Z, agent 없음, 같은 결과 | retained 06:55:11–06:55:12Z ×2 | 432aac52 reserved 06:55:15Z, bound 06:55:23Z | 12e4f631 생성 06:52:26Z → bound 06:55:23Z | first_seen 07:03:49Z |

- 작업공간 실패 이벤트의 `lease_id`는 비어 있다. 귀속은 시각이 아니라 id 재계산으로 했다: `id = sha256(scope\0code\0("ws:"+sha256(code\0item)[:32]))[:16]` (`work_parallel_failures.py:141`, `work_parallel_mcp.py:1654`).
- 원장 `leases[].created_at`은 예약 시각이 아니라 후보(아이템)의 created_at이다(`work_parallel.py:408`). 예약 시각은 coordination `runs[].reserved_at`에 있다.
- 작업공간 manifest: 331d6d5c → `work-parallel-workspaces\3c15f39c…json` status `preparing`(worktree 05:49:59Z). 1d272611 → `c77c47ba…json` status `ready`(worktree 06:53:56Z, manifest 06:55:12Z). 둘 다 리스는 해제됐다.
- 실패의 Git 원문은 router.log에 없다. 라우터가 Work 도구에 로그를 넘기지 않았다(결함 C, 격리 워크트리에서 수정·미배포). Git 실패 원인은 미확인이다.
- 같은 P0 intent가 첫 예약 리스를 먼저 가리켰는지는 확인하지 못했다. intent 행은 현재 lease_id만 보관한다.

# MAX_LEASES

- `MAX_LEASES = 100`(`work_parallel.py:28`). `lease_bound_reached` = 비보류 열린 리스 ≥ 100, 또는 기록 ≥ 100,000(`:52-54`). 비보류 = ACTIVE 상태 중 `waiting_input`·`submitted`이면서 slot=false가 아닌 것(`:42-49`).
- P0 우회 경로에도 적용된다: 예약 `:388-389`, 복구 `:656`, rework `:726`, reacquire `:887`. 메시지는 "lease bound reached (100); P0 waits for a slot"이다. 우선순위별 슬롯·발사율·일시정지는 P0에서 무시된다.
- 따라서 "보관 상한"만은 아니다. 동시에 열린 비보류 리스(P0 포함) 100개가 상한이다. 설계 문서는 이를 저장 백스톱으로 유지한다고 적었다(`docs/journal/2026/11/14/features/priority-p0-fork-models/requirement.md:94`).
- Andy 현재(읽기 전용): 비보류 열린 리스 1/100, 기록 543.
- 검증 범위: 코드 읽기 + 기존 테스트(`tests/test_work_parallel.py:319,427-431`, `tests/test_work_parallel_parked_bound.py:67,74`, `tests/test_work_parallel_p0_outcomes.py:172`). P0 100개 동시 실행을 실측한 별도 검증은 하지 않았다.
