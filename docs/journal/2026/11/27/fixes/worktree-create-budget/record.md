---
type: fix-record
title: Work 포크 워크트리 생성 Git 예산 분리 및 Observatory 실행 행 애니메이션
description: 고정 30초 Git 예산이 대형 저장소에서 worktree add를 반복 실패시켜 포크가 뜨지 않고 작업이 큐에 쌓이던 결함을 생성 단계 전용 180초 예산으로 수정하고, 실행 중 포크의 워크보드 아이템에 프레임 애니메이션을 추가
tags: [work-parallel, workspace, git-timeout, observatory, animation, andy]
timestamp: 2026-11-27T01:45:00Z
---

# Work 포크 워크트리 생성 Git 예산 분리 및 Observatory 실행 행 애니메이션

## 문제 (Andy 라이브 실측)

- **`workspace_prepare_retained` 반복 (2026-11-26 하루 35건):** 라우터 `9505` 로그의 모든 실패가 `detail="git worktree add timed out after 32-88 s"` 였고, 아이템 `n2859`·`n2914`·`n2908`·`n2898`·`n2763`·`n2904` 순으로 반복됐다. 실패할 때마다 `_release_failed_reservation()`이 예약을 해제하고 아이템을 큐에 돌려보내므로, 화면에는 "작업 54건 처리중, 실행 0"으로 보였다.
- **예산 불일치:** 같은 `git worktree add` 를 손으로 실행하면 1.5–4초다. 저장소 `G:\ciel-Walkie` 는 등록 워크트리 1,482개(work-parallel 982), 추적 파일 1,356개이며, 생성은 워킹 트리 전체를 복사한다. 반면 `_GIT_TIMEOUT_SECONDS = 30.0` 은 `rev-parse` 같은 단일 조회와 생성에 같은 값을 썼다.
- **30초의 원래 목적:** 커밋 `faa2c7e`(2026-11-25)는 partial-clone promisor fetch가 credential-manager 자식 프로세스로 인증을 기다리며 멈춘 사례(Betty `n1815`/`n1816`)를 막으려고 이 타임아웃과 `GIT_TERMINAL_PROMPT=0`/`GCM_INTERACTIVE=never` 환경, 프로세스 트리 kill을 함께 넣었다. 즉 비대화형 환경과 트리 kill이 실제 방어선이고, 30초 숫자는 부수적이었다.

## 수정

- **`ciel_runtime_support/work_parallel_workspace.py`:**
  - `_GIT_CREATE_TIMEOUT_SECONDS = 180.0` 및 `_CREATE_STEPS = {"add","checkout","reset","clone","fetch"}` 추가. `_git()` 이 `worktree add` 등 생성 계열에만 180초를 쓰고 나머지 검증 단계는 기존 30초를 유지한다.
  - 생성은 사용 전에 `_verify()`로 검증되고 재시도는 같은 워크트리를 반환하므로(매니페스트 기반), 예산을 늘려도 임의 경로를 채택할 수 없다. 진짜 멈춤은 여전히 `_kill_process_tree()`가 끊는다.
- **`ciel_runtime_support/claude_mods/ciel-work/hooks/observatory.client.js`:** 실행 중 포크(`node.fork === true`) 행의 글리프가 `◐◓◑◒`로 회전하고 행 배경이 4단계로 호흡한다. 인스턴스 자신의 `surface.state`에 `beat`/`live`/`armed`를 두고 `surface.every(500, …)`로 프레임을 올리며, 실행 중 포크가 없으면 아무것도 다시 그리지 않는다. 두 패널이 프레임을 공유하지 않고, 선택 이동이 박자를 초기화하지 않는다.

## 검증

- **fail-first:** `tests/test_work_parallel_workspace.py::WorkspaceTests::test_a_slow_worktree_add_that_finishes_is_not_a_failed_reservation` — 수정 전 `AttributeError: module ... does not have the attribute '_GIT_CREATE_TIMEOUT_SECONDS'`. 수정 후 PASS(3초 지연 생성이 30초 예산에서 성공).
- **기존 테스트 조정:** `test_git_sets_noninteractive_env_and_kills_grandchild_on_timeout` 은 `worktree add` 가 생성 예산을 쓰게 되어 두 예산을 모두 0.6초로 낮춰 kill 경로를 그대로 검증한다.
- **모드 스위트:** `claude plugin validate ciel-work` 통과, `claude plugin test ciel-work` **99 pass / 0 fail** (2회 연속). 첫 실행에서 1건 실패가 있었고 원인은 Client가 모듈 전역 `beat`/`live`/`currentSurface`를 써서 인스턴스 간 상태를 공유한 것으로, 인스턴스 상태로 옮겨 해결했다.
- **격리 Windows** (`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`): 영향 7개 스위트 **137 tests, 0 fail, 0 skip**.
- **Linux py310 / py311** (`ciel-runtime-test-py310`, `-py311`, `/runner/snap-anim` 스냅샷): 동일 7개 스위트 **각 137 tests, 0 fail, 41 skip**(플랫폼 조건 스킵).
- **스테이지 하네스:** `tests/observatory_stage_harness.mjs` 로 실제 `observatory.client.js` 를 Node에서 렌더 — 실행 포크 행 글리프 `◐` → `◓`, 변경 행 1개, 실행 포크 없음일 때 상태 미기록.

## 발행·설치·라이브 (구분)

- **커밋:** `1ca7dc3` (`feature/priority-models`, `beta` 푸시).
- **GitHub Actions / npm:** `Publish to npm` 런 `45923176837` `completed: success`, `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261127-013303.1ca7dc3`(`gitHead` 일치). 타볼 SHA-256 대조: `work_parallel_workspace.py:08d780edac39`, `observatory.client.js:047df59f819d` 모두 `match`.
- **7개 승인 루트 해시 확인 오버레이 (`overlay-1ca7dc3.tgz`):** 로컬 4개, KOBE `u24`(.14), Prody `u41`·Betty `u42`(.12) **7/7 apply ok=True**. 원격 임시 오버레이 정리 완료.
- **라우터 재시작(사용자 지시):** Andy `9505` pid `13560→95176`, Prody `9592` `11560→7836`, Betty `9564` `6196→11836`, KOBE `9779` `2104→17008`(30초 창 초과 후 기동, 직접 `/health`·Work REST로 확인). Celly `9385` 는 `taskkill` 접근 거부(고권한 프로세스)로 라우터 도구 `restart_session`을 사용해 CLI 재시작을 큐잉했다. 4개 호스트 모두 재시작 후 Work REST 정상.
- **라이브 효과:** Andy·KOBE·Prody·Betty의 새 라우터는 오버레이 설치 이후에 기동됐으므로 `work_parallel_workspace.py` 새 예산을 메모리에 로드했다. Celly는 CLI 재시작이 완료된 뒤 적용된다.
  - **후속(06:05Z):** Celly CLI 재시작은 완료되지 않았고 라우터 `111684`는 61d2f8b 코드를 계속 실행했다. 권한 상승 종료 후 `66068`로 교체해 적용했다. Prody/Betty `/health`의 `61d2f8b` 표기는 이 오버레이가 버전 문자열을 바꾸지 않아서 생긴 것이며, 메모리의 코드는 새 코드다. 상세는 [e9eef86 재배포](../../deployments/e9eef86-redeploy/record.md).

## 함께 관측된 미해결 사항 (수정하지 않음)

- **라우터 인터프리터 손상:** 이번에 교체한 Andy 라우터는 `router_previous_fault`에 **Windows fatal exception: access violation**을 남겼고, 최심 프레임이 `fork_turn_watch.py:827 _poll`(이전 실행은 `:767 _observe`)이었다. 같은 프로세스가 그 전 14시간 동안 `NameError: name 'OSError' is not defined`(빌트인 소실)를 20,211회 남겼다. 빌트인이 사라지는 형태는 코드 결함으로 설명되지 않으며, 8 GB/13,755개 전사 파일을 계속 읽는 이 모듈이 피해자일 가능성이 있다. **재현 실패로 원인 미확정, 코드 수정 없음.**
- **재개 거부 루프:** 이미지 #68의 `Agent "a792b07bc9e95c234" was not resumed: the session was stopped and nobody has sent input since.` + 5분 쿨다운 반복은 재현이 없어 변경하지 않았다.
- **검수 "멈춤":** `stale approval fingerprint: Work approval scope changed` 250건은 2026-11-19 커밋 `8c7a4ef`에서 제거된 문구이며 11-26에는 2건뿐이다. 현재 `pending` 12건 중 6건은 `decision: rework`가 이미 기록된 행이다. 제품 결함 아님.
