---
type: fix-record
title: 사이드 relay와 ask_main 동시 발생 시 단일 noticeToMain 경합 수정
description: commitProposal이 relayToItem·notifyReleased와 ask_main을 동일 receipt_id로 병렬 호출해 relay 본문과 파킹 포크 안내가 already로 누락되던 결함 수정 및 배포
tags: [side-intake, relay, ask-main, notice-race, beta-deploy]
timestamp: 2026-11-24T20:56:00Z
---

# 사이드 relay와 ask_main 동시 발생 시 단일 noticeToMain 경합 수정

## 관측 (Andy 라이브, 2026-11-24)

- Walkie `160097` (영수증 `b3c70c902571`) 처리 결과: `action="update"`, `item_id="n2459"`, `relay={"item_id": "n2459"}`, `ask_main=true`, `complaint=true`.
- `n2459 → n2776 → n2777`의 리스 `e37edf1119`(`agent-a332fe7f6513ccb6c`)는 `waiting_input` 파킹 상태였다.
- `commitProposal`(`ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js:2674–2696`)이 `relayToItem`(먼저 `work_parallel_status`·`resumeParked`를 `await`)과 `if (asks) noticeToMain(...)`을 **병렬로 호출하면서 둘 다 같은 `event.receipt_id`를 사용**했다.
- 결과: `20:21:56Z`에 `ask_main`이 먼저 `side_notice`를 선점했고, `20:22:48Z`에 끝난 `relayToItem`은 `ciel-work: Side relay n2459 → main (already)`로 중복 스킵되어 릴레이 본문·원문·파킹 포크(`SendMessage` 안내)가 누락됐다.

## 수정 (커밋 `3438e6e68f0cdf6ddf56a0367f7b605e68e4c17b`)

- `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js`:
  - `commitProposal`에서 `askText`(`value.main_notice` 또는 기본 `Ciel side asks main ...`)를 `withExtra`/`aloneExtra`에 통합해 `relayToItem`, `notifyReleased`, `turn_end`, 단독 `ask_main`이 **영수증당 정확히 하나의 `noticeToMain`만 호출**하도록 변경했다.
  - `askText`를 맨 앞에 두어 유휴 메인의 `Ciel side asks main` 웨이크/컴플레인 헤더와 `relay`/`released_waits`의 포크·파킹 워커 안내를 함께 보존한다.
  - `relayToItem`/`notifyReleased` 예외 시 `.catch(fallbackNotice)`로 `aloneExtra`(`askText`/`forkText`) 폴백 전달을 보장한다.

## 검증

- **실패 테스트 먼저(RED → GREEN):**
  - `tests/side_relay_harness.mjs` (`relay-parked-complaint`), `tests/test_side_relay_notice.py::SideRelayNoticeTests::test_relay_with_ask_main_complaint_keeps_parked_worker_and_avoids_notice_race`: 수정 전 `AssertionError: 'Ciel side relay for Work item n2 (csv writer)' not found in 'Ciel side asks main ...'` 실패 확인, 수정 후 통과.
- **격리 Windows 회귀** (`CIEL_RUNTIME_*` 제거, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`):
  - `test_side_relay_notice.py` 13/13, `test_side_running_fork_relay.py` 3/3, `test_side_intake_mod.py` 72/72, `test_side_notice_claim.py` 19/19, `test_work_texts.py` 8/8 (총 115 pass / 0 fail).
  - `claude plugin validate` 통과, `claude plugin test` **94 pass / 0 fail**.
  - `ruff check`, `npm run check:docs` 통과.
- **기존 Linux 컨테이너 회귀** (`ciel-runtime-test-py310`, `ciel-runtime-test-py311`):
  - `test_side_relay_notice.py` 13, `test_side_running_fork_relay.py` 3, `test_side_intake_mod.py` 72, `test_work_texts.py` 8 — 양쪽 각 96 pass / 0 fail.

## 배포 (2026-11-24)

- `3438e6e68f0cdf6ddf56a0367f7b605e68e4c17b`를 `feature/priority-models`·`beta`에 atomic push.
- `Publish to npm` 실행 `45484103492` 성공:
  - `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261124-204540.3438e6e`
  - `gitHead` = `3438e6e68f0cdf6ddf56a0367f7b605e68e4c17b`
  - tarball `side-intake.js` SHA-256 `88cb9cc51728` 일치.
- 해시 확인 오버레이 `overlay-3438e6e` (`side-intake.js` `124bef849528 → 88cb9cc51728`, `runtime_constants.py` `b993d380f35c → 0b17efe7b50b`, `package.json` `78b8da660d2c → 99fccd2093e0` 존재하는 루트만):
  - 로컬 3루트(`hotfix5`·`.local/share/ciel-runtime`·npm 전역), Celly, KOBE(`u24`), Prody(`u41`), Betty(`u42`) **7개 루트 전부 설치 전 `base` → 설치 후 `new`** 확인(20:55:37–20:56:19Z).
- **라이브 효과(파일 설치와 구분):** 운영 세션과 라우터는 재시작하지 않았으므로 `side-intake.js` 수정은 각 에이전트의 다음 세션/플러그인 재로드 후 라이브 반영된다.
