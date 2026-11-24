---
type: fix-record
title: 게이트 재확인(side-fresh) 10분 주기 엄격 적용
description: 게이트 다이제스트 변경 또는 메인 턴 종료(classic.Stop, 60초) 시 10분 주기를 건너뛰고 게이트 재확인 side-fresh 액터를 즉시 재실행하던 경로 수정
tags: [work-gate-check, side-fresh, interval]
timestamp: 2026-11-24T16:50:00Z
---

# 게이트 재확인(side-fresh) 10분 주기 엄격 적용

## 관측 (읽기 전용, 2026-11-24)

- 사용자 확인 요청: `side-fresh`가 10분에 한 번 동작해야 하는데 너무 자주 동작함.
- 원인 분리(코드 + 라이브 측정):
  1. `side-intake.js:2156`: `work_side_intake.session == "fresh"` 설정(Andy 등)에서는 `ciel-work:side-fresh` 타입이 **주기 게이트 재확인**뿐 아니라 **수신된 모든 외부 Walkie SSE 이벤트**에도 사용된다(Andy 16:00–16:44Z 기준 외부 SSE 접수 20건 ≈ 약 2분당 1건; 이는 설계된 이벤트 기반 처리다).
  2. **실제 결함(`work_gate_check.py:483-485`, `register.js:5065`)**:
     - `if previous.get("digest") == current and stamp - float(previous.get("at") or 0) < window:` 조건 때문에 보드의 대기/차단/parked/stalled 항목 중 하나라도 바뀌어 `digest`가 달라지면 10분 주기를 건너뛰고 다음 1분 틱에 즉시 재확인 액터를 띄웠다.
     - `classic.Stop`에서 `gateCheck.run(true)`(`turn_end=True`)를 호출하면 `window`가 `interval * 60`(600초) 대신 `TURN_END_MIN_SECONDS`(60초)로 줄어들어 메인 턴이 끝날 때마다 1분 간격으로 게이트 재확인을 띄웠다.
     - Celly 라이브 `Gate re-check queued` 기록: `14:30:00Z → 14:36:32Z`(6분 32초), `14:54:47Z → 14:59:57Z`(5분 10초), `16:09:20Z → 16:14:29Z`(5분 9초). Andy도 `14:25:23Z → 14:34:56Z`(9분 33초)로 10분 이내 재실행이 관측됐다.

## 수정

- `ciel_runtime_support/work_gate_check.py`: `tick_rows`에서 `digest` 일치 여부나 `turn_end`에 따른 60초 단축을 제거하고, 설정된 주기(`interval * 60`, 기본 10분)가 지나기 전에는 항상 `None`을 반환한다.

## 검증

- 실패 테스트 먼저: `tests/test_work_gate_check.py::GateCheckUnitTests::test_due_now_then_strictly_after_interval` 수정 전 실패([red-before-fix.txt](evidence/red-before-fix.txt)), 수정 후 통과.
- 임시 격리 Windows pytest 및 기존 Linux `ciel-runtime-test-py310`·`ciel-runtime-test-py311` 컨테이너에서 게이트 재확인·스톨·사일런트 포크·파킹 포크·릴리즈 힌트 영향 파일 전체 통과(12개 파일 각 166건 실패 0).

## 배포 (2026-11-24)

- 커밋 `f7a06ee`(`f7a06ee1a409697d3c38f3b057ee7386083191e8`)를 `feature/priority-models`와 `beta`에 push했다.
- `Publish to npm` 실행 `45442020634` 성공: `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261124-165156.f7a06ee`, tarball 내 변경 7개 파일 SHA-256이 오버레이 `.new`와 7/7 일치.
- 해시 확인 오버레이 `overlay-f7a06ee`(7개 파일, base `9a46b96`)를 7개 루트(로컬 hotfix5·`.local/share/ciel-runtime`·npm 전역, Celly 관리자 권한, KOBE, Prody, Betty) 모두 설치 전 `base 7/7` → 설치 후 `new 7/7`으로 확인했다.
- **라이브 효과(파일 설치와 구분)**: 운영 세션과 라우터는 재시작하지 않았다. `work_gate_check.py`, `restart_takeover.py`, `company_prompt_reload.py`, `runtime_session_restart.py`, `ciel_runtime.py`, `work_parallel_mcp.py`는 라우터/런처 재시작 후 반영되며, `register.js`는 Claude Code 플러그인 재로드 또는 세션 재시작 후 반영된다.
