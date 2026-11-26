---
type: fix-record
title: 백엔드 Web UI 및 Mods의 실행 중 포크·P0–P5 우선순위 레인 시각화 개선
description: 백엔드 Web UI(/ca/work/ui)와 ciel-work Mods(Observatory 그래픽/일반 패널·/work-mode parallel)에서 실제로 실행 중인 포크 항목과 P0–P5 전 우선순위 레인 동작 상태를 한눈에 구분해 확인할 수 있도록 시각화 수정
tags: [work-web-ui, ciel-work, observatory, parallel, p0-p5, visualization]
timestamp: 2026-11-26T17:45:00Z
---

# 백엔드 Web UI 및 Mods의 실행 중 포크·P0–P5 우선순위 레인 시각화 개선

## 문제 (라이브 관측 및 코드 확인)

- **백엔드 Web UI (`ciel_runtime_support/work_web_ui.py`):**
  - `loadSnapshot()`이 `/board`만 호출하고 `/parallel`을 함께 읽지 않아 메인 보드(`#queue`, `#work-tree`, `#panel-node`)에 현재 포크가 실행 중인 항목 표시가 전혀 없었다.
  - `confirmedFork`/`compactFork`가 `effective_priority` 대신 `row.priority`를 읽어 P0 포크가 `P1`으로 오표기됐고 `.P0` CSS 토큰도 없었다.
  - `Parallel` 탭은 `Per-priority dispatch`에서 `no_work` 레인을 생략하고, `Device capacity`에서 `P0`를 빼고 `P1–P5`만 표시하며, `Processing now`가 `Goal priorities` 목록(Andy 70개 이상) 아래에 묻혀 있었다.
- **백엔드 포크 투영 (`ciel_runtime_support/work_parallel_mcp.py`):**
  - `_fork_projection()`이 `goal_title`만 포함하고 `goal_id`를 누락해, 하위 리프가 포크 실행 중일 때 Mod의 루트 목표 목록에서 해당 목표가 실행 중임을 역추적할 수 없었다.
- **Mods (`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`, `observatory.client.js`):**
  - `graphicProps()`가 `confirmed` 목록을 `.slice(0, 3)`으로 잘라 4개 이상 포크(P0–P5 동시 가동 시)를 숨겼다.
  - `classedNodes()`가 리프 `item_id`만 `fork=true`로 표시하고 상위/루트 목표에는 전파하지 않았으며, 그래픽/일반 패널 어디에도 `P0–P5` 6개 레인 전체를 한눈에 보여주는 레인 요약 줄이 없었다.
  - `/work-mode parallel` 그룹 행 출력이 `effective_priority` 대신 `r.priority`를 출력해 P0 포크를 P1로 표기했다.

## 수정

- **`ciel_runtime_support/work_parallel_mcp.py`:** `_fork_projection()` 각 행에 `goal_id`를 추가(`_item_details()` 기존 계산값 재사용, 비밀·토큰·경로 미노출).
- **`ciel_runtime_support/work_web_ui.py`:**
  - `loadSnapshot()`이 `/parallel`을 비차단 병렬 로드(`loadParallelSummary`)하여 탭 전환 없이도 메인 화면에서 실시간 포크·레인 상태를 반영하도록 변경.
  - 보드 상단 `#priority-lanes` 및 Parallel 탭 상단 `[data-group="lanes"]`에 **P0–P5 6개 레인 카드**를 상시 표시(슬롯 사용/상한, `confirmed` 실행중 포크 vs `uncertain`/`reservation`/`waiting`을 `native-fork-evidence/1` 계약대로 구분, 클릭 가능한 항목 필, `effective_priority || priority` 및 `.P0` 스타일 적용).
  - `#queue` 카드·`#work-tree` 행에 `●`/`FORK P0..P5`(리프), `FORK ×N`(상위/루트 목표), `uncertain`/`reserved`/`fork waiting` 배지 추가 및 상태 필터에 `fork running` 옵션 추가.
  - `#panel-node` 선택 시 해당 노드(또는 하위 노드)의 실시간 포크 상태 카드를 상단에 표시.
  - Parallel 탭에서 `P0–P5 lanes`·`Processing now`를 상단으로 배치하고, `Goal priorities`를 접이식 `<details>`로 전환, `Device capacity`에 `P0` 행 추가.
- **`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js` & `observatory.client.js`:**
  - `forkNodeIndex()`로 리프 포크뿐 아니라 조상·`goal_id` 루트까지 `forkChildren`/`forkItems`/`forkGroup` 전파; `confirmed` 상한을 `3→12`로 확대하고 `parallelDisplaySignature()`에 `confirmed`/`uncertain` 반영.
  - `parallelLanes()`로 `P0–P5` 6개 레인 전체 요약(확인 포크 수·미확인 수·슬롯/상한·실행 항목 ID)을 생성해 그래픽 Observatory 상단, 일반(Desktop/Mobile) 패널 헤더(`LANES:`), Wide/Narrow 트리 행(`●포크실행`, `◉ ●포크×N`, `?미확인`) 및 인스펙터 메타에 표시.
  - `/work-mode parallel` 그룹 행에 `effective_priority || priority` 적용.

## 검증

- **수정 전 실패 재현 (fail-first):**
  - `tests/test_work_parallel_native.py::test_fork_projection_includes_root_goal_id_for_nested_child_items`: `FAIL` (`AssertionError: 'n1' != None`).
  - `tests/test_work_web_ui.py::test_board_and_parallel_visualize_active_forks_and_all_p0_to_p5_lanes`: `ERROR` (`#priority-lanes` 미존재 타임아웃).
  - `ciel_runtime_support/claude_mods/ciel-work/tests/observatory.test.ts`: `FAIL` (P0–P5 6개 포크·부모 목표 배지 미표시).
- **수정 후 통과:**
  - `py_compile` (`work_web_ui.py`, `work_parallel_mcp.py`) OK.
  - `claude plugin validate ciel_runtime_support/claude_mods/ciel-work`: `Validation passed` (exit 0).
  - `claude plugin test ciel_runtime_support/claude_mods/ciel-work`: **97 pass, 0 fail** (3 files).
  - 격리 Windows (`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정, Playwright Chromium 브라우저 테스트 포함): 13개 영향 스위트 전원 통과 (`test_work_parallel_native` 16, `test_work_observatory` 11, `test_work_web_ui` 43 [브라우저 11건 포함], `test_work_live_refresh` 4, `test_work_parallel_diagnosis` 6, `test_work_parallel_p0_events` 49, `test_work_parallel_mcp` 37, `test_work_http_controller`, `test_work_http_parallel_review` 23, `test_work_http_parallel_gaps` 15, `test_work_mod_write_gate` 188, `test_work_mode_packaging` 5, `test_fork_turn_watch` 61).
  - 기존 Linux `ciel-runtime-test-py310` · `ciel-runtime-test-py311`: 12개 스위트 전원 통과 (`ALL_LINUX_OK`: `test_work_parallel_native` 16, `test_work_observatory` 11, `test_work_web_ui` 43 [browser 11 skip], `test_work_live_refresh` 4, `test_work_parallel_diagnosis` 6, `test_work_parallel_p0_events` 49, `test_work_parallel_mcp` 37, `test_work_http_controller`, `test_work_http_parallel_review` 23, `test_work_http_parallel_gaps` 15, `test_work_mod_write_gate` 188, `test_work_mode_packaging` 5).
- **시각 증거 (Playwright 헤드리스 스크린샷):**
  - 합성 P0–P5 시나리오(Desktop 1440×960 / Mobile 390×1100, 가로 오버플로 0): `evidence/p0p5-board-desktop.png`, `evidence/p0p5-parallel-desktop.png`, `evidence/p0p5-board-mobile.png`, `evidence/p0p5-parallel-mobile.png`.
  - 라이브 스냅샷 렌더링(읽기 전용, 가로 오버플로 0): `evidence/live-andy-board.png`, `evidence/live-andy-parallel.png`, `evidence/live-celly-board.png`, `evidence/live-celly-parallel.png`, `evidence/live-celly-fork-filtered.png` (Celly `n1592` P1 확인 포크가 P1 레인·부모 목표 `FORK ×1`·리프 `FORK P1`·노드 인스펙터 카드에 표시됨을 실측 확인).
