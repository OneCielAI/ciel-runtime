---
type: fix-record
title: work-plan.json Windows 교체 충돌(WinError 5) 방지 및 재시도 확대
description: 대용량 work-plan.json(11.7 MB)에서 동일 라우터 프로세스의 read_snapshot과 os.replace가 충돌해 40 ms 재시도 한도를 넘겨 /ca/mcp HTTP 500(WinError 5)을 유발하던 결함 수정
tags: [work-plan-store, windows, winerror-5, atomic-replace]
timestamp: 2026-11-26T15:23:00Z
---

# work-plan.json Windows 교체 충돌(WinError 5) 방지 및 재시도 확대

## 관측 (2026-11-26 UTC, Andy 운영)

- `15:12:39Z` 모니터링 틱에서 Andy 라우터(`9505-aae7db361c7f`) 로그에 `14:57:27Z` `[ERROR] router_post_uncaught path=/ca/mcp error=PermissionError: [WinError 5] Access is denied: '...\\work-plan.json.81900.1795705046448352200.tmp' -> '...\\work-plan.json'` 및 HTTP `500`이 기록됐다.
- 직후 `14:57:29Z` `work_side_fail` 및 `14:57:30Z` 메인 전사 `Ciel side: original of failed event 3d1f4f19f000 → main (appended)` 폴백으로 이어졌다.
- Andy `work-plan.json` 파일 크기 확인 결과 **11,726,393 B (11.7 MB)**였으며, `14:57:25–27Z` 동일 초 안에 `work_side_understanding` 쓰기와 `work_side_gate_check` 스냅샷 읽기가 동시 실행됐다.

## 원인 (확인)

- `ciel_runtime_support/work_plan_store.py`에서 `WorkSessionStore.read_snapshot()`은 매 호출마다 새 `WorkPlanStore` 인스턴스를 생성하고, `WorkPlanStore.read_snapshot()`(`L103–124`)은 11.7 MB 파일을 64 KiB 청크(~180회)로 열람하면서 프로세스 내 경로 락 없이 핸들을 유지했다.
- 반면 `_write_unlocked()`(`L256–263`)은 `os.replace(temporary, self.path)`를 `20 × 2 ms = 40 ms` 동안만 재시도하고 실패 시 `PermissionError`를 그대로 전파해 `/ca/mcp` 미처리 HTTP 500을 일으켰다.

## 수정 (`ciel_runtime_support/work_plan_store.py`)

- 모듈 수준 `WeakValueDictionary` 기반 프로세스 내 경로별 재진입 락 `_path_io_lock(path)`을 추가했다(디스크에 파일·디렉터리·`.lock` 사이드카를 생성하지 않아 `read_snapshot()`의 순수 읽기 전용 계약 유지).
- `read_snapshot()`은 파일 열람(`open("rb") → _read_bounded`) 구간만 `_path_io_lock(self.path)`으로 보호하고, `_write_unlocked()`과 `_read_unlocked()` 격리 `os.replace` 구간도 `_path_io_lock(self.path)`으로 직렬화했다.
- 교차 프로세스 읽기(`statusline_script.py`, `work_ctl.py`, 백신 등)에 대비해 `PermissionError` 재시도를 `50`회 점진 백오프(`min(0.05, 0.002 * (attempt + 1))`, 총 ~1.85초)로 확대했다.

## 검증

- **수정 전 실패 재현 (`e6aff62`):**
  - `tests/test_work_plan_store.py::test_write_does_not_collide_with_in_process_read_snapshot_on_separate_store`: `FAIL` (`PermissionError: [WinError 5] Access is denied`).
  - `tests/test_work_plan_store.py::test_write_retries_transient_cross_process_permission_error_past_40ms`: `FAIL` (`PermissionError: [WinError 5] Access is denied`).
- **수정 후 통과:**
  - `py_compile ciel_runtime_support/work_plan_store.py` OK.
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): `test_work_plan_store.py` + `test_work_session_lineage.py` + `test_work_retention.py` + `test_work_state_unavailable.py` + `test_work_http_controller.py` + `test_work_mcp_tools.py` **141 passed, 79 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py310`·`ciel-runtime-test-py311`: 6개 스위트 전 항목 통과(`141 passed, 79 subtests passed`, `ciel_runtime.py` 포함 `work_plan_store`+`work_retention` 49/49 재확인).

## 발행·설치

- 커밋·npm `@beta`·7개 루트 해시 확인 설치 후 결과를 기록한다.
