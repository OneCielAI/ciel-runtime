---
type: fix-record
title: exit-contract salvage 시 두 번째 send로 권한 회수 포크를 깨우던 결함 수정
description: parallelEnsureExitContract가 salvage 패스에서 $.session.send를 다시 호출해 권한이 회수된 포크를 좀비로 깨우던 결함과 관계없는 메인 턴의 30초 유예 우회를 수정·배포
tags: [work-parallel, exit-contract, host-salvage, register-js, beta-deploy]
timestamp: 2026-11-24T22:39:00Z
---

# exit-contract salvage 시 두 번째 send로 권한 회수 포크를 깨우던 결함 수정

## 관측 (Celly 라이브, 2026-11-24 21:02Z)

- `21:02:12.632–21:02:13.651Z`에 Celly 실행 포크 3건(`n1510`/`2220e205`/`ae5774b3`, `n1299`/`a27bf70d`/`a8b0ad35`, `n1420`/`069c03a9`/`a71f327b`)이 계정 한도 HTTP 429로 멈췄고, `21:02:22.557Z`에 모두 native `failed`로 기록됐다.
- 원장에 `host_salvage`(`21:02:27.550Z`, `21:02:34.386Z`, `21:02:47.818Z`) 및 `unreported`(`21:02:28.556Z`, `21:02:37.793Z`, `21:02:49.299Z`)가 기록됐다.
- `ae5774b3`와 `a8b0ad35`는 이미 `unreported`·권한 회수 이후인 `21:02:38.746Z`(+0.95s)·`21:02:30.421Z`(+1.86s)에 `Your native turn ended without a durable Work exit receipt...`를 수신해 깨어났고, 이후 8–9분간 `lease binding` 미기록 및 `invalid or revoked worker capability` 거부를 반복하며 교체 포크와 동시에 실행됐다. (`a71f327b`는 `21:02:46.892Z` 수신 뒤 `21:02:49.760Z` `TaskStop`에 중단됐다.)
- 코드 원인(`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js:3913–3938`):
  1. `parallelEnsureExitContract`가 두 번째 호출(`prior.attempts === 1`)에서도 `if (prior.attempts < 2)`에 진입해 `$.session.send`로 포크를 깨운 직후 `if (current.attempts < 2) return false`를 통과해 같은 호출에서 곧바로 `host_salvage` → `unreported` → `stopParallelAgent`를 실행했다.
  2. `parallelReconcileAtTurnEnd`가 관계없는 턴(`id !== e.agentId`) 종료에서도 `minWaitMs = 0`으로 호출해 첫 보고 요청 뒤 30초 유예를 우회할 수 있었다.

## 수정 (커밋 `fb7394f9a6688a84d102fac043effd3603b9040f`)

- `ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`:
  - `parallelEnsureExitContract`는 첫 미보고(`prior.attempts < 1`)에서만 `$.session.send`를 1회 보내고 `false`를 반환하며, 후속 salvage 패스에서는 `$.session.send` 없이 `host_salvage` → `unreported` → `stopParallelAgent`를 실행한다.
  - `parallelReconcileAtTurnEnd`에서 `id !== e.agentId`인 관계없는 턴 종료에는 `minWaitMs: ABSENT_EXIT_GRACE_MS`(30s)를 적용한다.
  - `$.clock.now()`가 제공되면 이를 우선 사용하고 미지원 환경에서는 `Date.now()`로 폴백한다.

## 검증

- **실패 테스트 먼저(RED → GREEN):**
  - `tests/work_mod_harness.mjs` (`parallel-salvage-does-not-revive-and-main-turn-respects-grace`), `tests/test_work_mod_write_gate.py::WorkModWriteGateTests::test_salvage_does_not_revive_fork_and_main_turn_respects_nudge_grace` 및 `test_second_missing_exit_salvages_before_stop`: 수정 전 `AssertionError: 1 != 2` 실패 확인, 수정 후 통과.
  - `tests/test_work_mod_real_backend.py` (`stranded-no-report` 계열 send 기대치 `2 → 1` 정합화) 및 `ciel_runtime_support/claude_mods/ciel-work/tests/work.test.ts` 신규 테스트 추가.
- **격리 Windows 회귀** (`CIEL_RUNTIME_*` 제거, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`):
  - `test_work_mod_write_gate.py` 185(33 skip), `test_work_mod_real_backend.py` 120(3 skip), `test_work_fork_api_retry.py` 4, `test_side_relay_notice.py` 13, `test_side_intake_mod.py` 72, `test_work_texts.py` 8, `test_work_parallel_mcp.py` 36, `test_work_parallel_finalization.py` 114 — 전부 0 fail.
  - `claude plugin validate` 통과, `claude plugin test` **95 pass / 0 fail**.
  - `ruff check`, `npm run check:docs`, `npm pack --dry-run`, `node --input-type=module --check`, `py_compile` 통과.
- **기존 Linux 컨테이너 회귀** (`ciel-runtime-test-py310`, `ciel-runtime-test-py311`):
  - `py310`: `test_work_mod_write_gate.py` 185(33 skip), `test_work_mod_real_backend.py` 120(4 skip), `test_work_fork_api_retry.py` 4, `test_side_relay_notice.py` 13, `test_work_texts.py` 8, `test_side_intake_mod.py` 72, `test_work_parallel_mcp.py` 36, `test_work_parallel_finalization.py` 114(5 skip) — 전부 0 fail.
  - `py311`: `test_work_mod_write_gate.py` 185(33 skip), `test_work_mod_real_backend.py` 120(4 skip), `test_work_fork_api_retry.py` 4 — 전부 0 fail.

## 배포 (2026-11-24)

- `fb7394f9a6688a84d102fac043effd3603b9040f`를 `feature/priority-models`·`beta`에 atomic push.
- `Publish to npm` 실행 `45500888391` 성공:
  - `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261124-222612.fb7394f`
  - `gitHead` = `fb7394f9a6688a84d102fac043effd3603b9040f`
  - tarball `register.js` SHA-256 `8f311af5b5d6` 일치.
- 해시 확인 오버레이 `overlay-fb7394f` (`register.js` `e62a8c28f783 → 8f311af5b5d6`, `runtime_constants.py` `0b17efe7b50b → 12e37b530067`, `package.json` `99fccd2093e0 → 52bf839e9a54` 존재하는 루트만):
  - 로컬 3루트(`hotfix5`·`.local/share/ciel-runtime`·npm 전역), Celly, KOBE(`u24`), Prody(`u41`), Betty(`u42`) **7개 루트 전부 설치 전 `base` → 설치 후 `new`** 확인(22:37:33–22:38:43Z).
- **라이브 효과(파일 설치와 구분):** 운영 세션과 라우터는 재시작하지 않았으므로 `register.js` 수정은 각 에이전트의 다음 세션/플러그인 재로드 후 라이브 반영된다.
