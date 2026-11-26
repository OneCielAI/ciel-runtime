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

## 원인

- **확인(소스·로그):** `_write_unlocked()`(`L256–263`)은 `os.replace(temporary, self.path)`를 `20 × 2 ms = 40 ms` 동안만 재시도하고 실패 시 `PermissionError`를 그대로 전파해 `/ca/mcp` 미처리 HTTP 500을 일으켰다(로그의 `WinError 5`·HTTP 500과 일치).
- **확인(소스):** `WorkSessionStore.read_snapshot()`은 매 호출마다 새 `WorkPlanStore` 인스턴스를 만들고, `WorkPlanStore.read_snapshot()`(`L103–124`)은 프로세스 내 경로 락 없이 11.7 MB 파일을 64 KiB 청크로 읽는 동안 핸들을 연다. 같은 초(`14:57:25–27Z`)에 동일 라우터에서 `work_side_gate_check`(스냅샷 읽기)와 `work_side_understanding`(쓰기)이 실행됐다.
- **미확인:** `14:57:27Z`에 대상 파일 핸들을 실제로 잡고 있던 주체가 이 프로세스 내 스냅샷 읽기인지, 다른 프로세스(예: `statusline_script.py`의 `read_snapshot`)인지는 로그로 특정되지 않았다. 수정은 두 경로를 모두 다룬다(프로세스 내 직렬화 + 교차 프로세스 재시도 확대).

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

- 커밋 `0956ebefbf0ed2f4fff313679c78776ed15681e2`, GitHub Actions `Publish to npm` (`45848664333`) 성공, npm `@beta` = `0.2.51-beta.20261126-152357.0956ebe`(`gitHead` 일치).
- npm tarball 내부 `ciel_runtime_support/work_plan_store.py` SHA-256과 커밋 `0956ebe` 일치 확인(`f9aed568b576` → `8f1a353e1b2c`, 15,058 B).
- 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`) 해시 확인 설치 완료(`check` 7/7 `base` → `apply` 7/7 `ok=True`, `.cmd` 래퍼·버전 메타 미변경, 원격 임시 오버레이 정리 완료).
- **라이브 효과 구분:** Python 라우터 파일 설치만 완료된 상태다. 모니터링 규칙상 실행 중 라우터와 CLI 세션을 재시작하지 않았으므로, 기동 중인 라우터 메모리에는 재시작 전까지 이전 코드가 유지된다.

