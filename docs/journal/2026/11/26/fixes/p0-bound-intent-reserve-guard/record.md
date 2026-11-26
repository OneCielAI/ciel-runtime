---
type: fix-record
title: 이미 바인딩된 P0 인텐트에 대한 중복 reserve 매니페스트 오류 방지
description: 호스트 펌프와 메인의 work_parallel_run이 P0 항목에 동시 경합할 때 이미 bound된 P0 인텐트를 reserve가 재사용해 coordination persistence failed / different manifest 오해를 유발하던 결함 수정
tags: [work-parallel, p0, celly, work-parallel-mcp]
timestamp: 2026-11-26T14:07:00Z
---

# 이미 바인딩된 P0 인텐트에 대한 중복 reserve 매니페스트 오류 방지

## 관측 (2026-11-26 UTC, Celly 운영 `n1600`)

- `13:56:37Z` 모니터링 틱에서 Celly 메인 전사에 `13:46:40Z` `work_parallel_event (reserve) failed: coordination persistence failed; reservation released: WorkParallelError: assignment already exists with a different manifest` 오류가 포착됐다.
- Celly 상태 파일(`work-parallel.json`, `work-parallel-coordination.json`, `work-parallel-p0-v1.json`) 및 메인 전사 읽기 전용 대조 결과:
  - `n1600`(루트 `n1595`)은 P0 항목이며, 이전 `unreported` 인텐트 `fe9f85ee`(`d5473fa1`)가 `13:46:28.917Z` `terminal`로 정리됐다.
  - `13:46:26Z` 메인이 `work_parallel_run(item_id="n1600", role="worker")`를 호출하는 사이, 호스트 펌프가 `13:46:28.971Z` P0 인텐트 `23a6be04`와 리스 `39e5560c`를 생성·예약하고 `13:46:35.194Z` `a6d014a697345cb50`에 바인딩(`state="bound"`, `run.state="active"`)했다.
  - 직후 `13:46:40Z` 메인의 `reserve` 요청이 `p0.create_intent()`로 이미 `bound`된 `23a6be04`(`39e5560c`)를 돌려받은 뒤 `coord.record_assignment()`를 호출해 `assignment already exists with a different manifest`로 실패했다.
  - 오류 문구가 `"reservation released"`로 표시돼 Celly 메인은 이미 실행 중인 `39e5560c`를 인지하지 못하고 약 10분간(`13:46:40–13:56:37Z`) `role="resume"`, `recover_handoff`, `recover_submitted_review`, `work_node_update` 등을 반복 시도했다.

## 원인 (확인)

- `ciel_runtime_support/work_parallel_mcp.py` (`_reserve_assignment`, `L1718–1785`)가 `p0_intent["state"] in ("reserved", "spawning", "bound")`에서 `"bound"` 상태(또는 `existing.get("host_id") is not None`)를 미바인딩 예약 재시도와 구분하지 않고 `ident = existing["id"]`로 진행했다.
- 이후 `L1766`의 미바인딩 재시도 가드(`existing_run["state"] == "reserved" and existing_run.get("agent_id") is None`)를 통과하지 못해 `coord.record_assignment()`를 재호출하면서 `assignment already exists with a different manifest` 예외가 발생했고, `except` 블록(`L1785–1799`)이 라이브 인텐트 취소를 시도하며 오해를 부르는 `"coordination persistence failed; reservation released"`를 반환했다.

## 수정

- `ciel_runtime_support/work_parallel_mcp.py`: `L1726` 직후 `p0_intent["state"] == "bound" or existing.get("host_id") is not None`이면 `coord.record_assignment`나 `except` 롤백 경로로 내려가지 않고 즉시 `WorkParallelError(f"Work item {existing['item_id']} already has bound lease {existing['id']} ({existing['state']}; agent {existing.get('host_id') or 'bound'})")`로 거부하도록 가드했다.

## 검증

- **수정 전 실패 재현 (`42abda1`):**
  - `tests/test_work_parallel_p0_events.py::test_reserve_on_already_bound_p0_intent_refuses_cleanly_without_manifest_error`: `FAIL` (`coordination persistence failed; reservation released: WorkParallelError: assignment already exists with a different manifest`와 완전 일치 재현).
- **수정 후 통과:**
  - `py_compile ciel_runtime_support/work_parallel_mcp.py` OK.
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): `test_work_parallel_p0_events.py` + `test_work_parallel_p0_outcomes.py` + `test_work_parallel_mcp.py` **99 passed, 12 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py310` (`p0_events` + `p0_outcomes` + `mcp` + `finalization`): **210 passed, 5 skipped, 137 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py311` (동일 4개 스위트): **208 passed, 5 skipped, 139 subtests passed**.

## 발행·설치

- 커밋 `e6aff62f32f9825d26c1f6daa753186f4a71e5e3`, GitHub Actions `Publish to npm` (`45836068687`) 성공, npm `@beta` = `0.2.51-beta.20261126-140907.e6aff62`(`gitHead` 일치).
- npm tarball 내부 `ciel_runtime_support/work_parallel_mcp.py` SHA-256과 커밋 `e6aff62` 일치 확인(`ee6b1e78f236` → `8b145e914e96`, 280,977 B).
- 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`) 해시 확인 설치 완료(`check` 7/7 `base` → `apply` 7/7 `ok=True`, `.cmd` 래퍼·버전 메타 미변경, 원격 임시 오버레이 정리 완료).
- **라이브 효과 구분:** Python 라우터 파일 설치만 완료된 상태다. 모니터링 규칙상 실행 중 라우터와 CLI 세션을 재시작하지 않았으므로, 기동 중인 라우터 메모리에는 재시작 전까지 이전 코드가 유지된다.

