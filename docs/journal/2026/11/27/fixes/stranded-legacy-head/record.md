---
type: fix-record
title: 커밋한 포크 결과의 검수 행이 stranded 이동에서 영구 거부되던 결함
description: 레거시 검수 행을 stranded 사이드카로 옮길 때 예약 시점 HEAD와 제출 시점 스냅샷 HEAD를 같은 사실로 비교해 Celly n1635 검수가 매 결정마다 거부되던 결함을 식별 키 비교로 수정
tags: [work-parallel, stranded, review, celly, n1635, fail-first]
timestamp: 2026-11-27T07:05:00Z
---

# 커밋한 포크 결과의 검수 행이 stranded 이동에서 영구 거부되던 결함

## 라이브 증상 (Celly)

- 메인의 `work_parallel_item_review` 재작업 결정이 같은 lease `ed47d1db…`(아이템 `n1635`)에서 04:56:39Z, 05:34:05Z, 05:55:11Z 세 번 모두 `stranded source facts do not match the legacy review`로 거부됐다. 매번 `decision_id`(`rev-n1635-stranded-recover`, `-2`, `-3`)만 바꿔 다시 시도했다.
- 계기는 04:50:05Z다. 결정 시 워크스페이스 검증 git이 `git -c core.quotePath=false timed out after 39 s`로 실패해 stranded 행(`workspace_unavailable`)이 만들어졌다.
- 상태 파일 읽기 전용 대조에서 13개 사실 필드는 모두 같았고 `binding.head`만 달랐다.
  - stranded 행 `binding.head`는 `03f5dae…`로, lease에 기록된 예약 시점 HEAD다.
  - 레거시 검수 행 `workspace.head`는 `614103d…`로, 포크가 `cc35166e4`·`614103d52`를 커밋한 뒤의 스냅샷 HEAD다.
  - 워크트리 `git log`에서 `03f5dae`는 `614103d`의 조상이다.

## 원인 (코드)

- `_unavailable_review_facts()`의 binding은 `_stranded_binding(lease["workspace"])`이므로 예약 시점 HEAD를 담는다.
- `_review_facts()`가 만든 schema-5 검수 행의 `workspace`는 `review_observe()`의 새 스냅샷이므로 제출 시점 HEAD를 담는다.
- `_terminalize_legacy_review()`는 두 값을 `("repo","path","branch","head")`로 같다고 요구했다. 따라서 포크가 커밋한 결과는 구조적으로 이동할 수 없었고, 이후 모든 결정이 `_converge_legacy_review()` 재실행에서 같은 거부를 받았다.
- stranded `binding.head`는 화면 표시(`workspace.head`)에만 쓰인다. 복구 워크스페이스는 `prepare(binding.repo, …)`로 새로 만든다. 행의 동일성은 이미 `lease_id`·`generation`·`artifact`·`completion_receipt`·`fingerprint`·`summary`·`coverage`·`verification`과 `repo`·`path`·`branch`로 묶여 있다.

## 수정

- `ciel_runtime_support/work_parallel_mcp.py` `_terminalize_legacy_review`: 워크스페이스 비교 키를 `("repo","path","branch")`로 줄였다. HEAD는 식별 사실이 아니라는 근거를 주석으로 남겼다.
- 삭제 단계의 정확성은 그대로다. `row_digest`(검수 행 전체)와 finalization CAS 버전으로 정확한 행만 지우고, 바뀐 행은 충돌로 남긴다.

## 검증

- **fail-first:** `tests/test_work_parallel_rework_worktree.py::BrokenRetainedWorktreeTests::test_committed_result_whose_workspace_breaks_after_publication_still_moves_to_stranded`
  - 시나리오: 재작업 포크가 워크트리에서 커밋하고 보고한 뒤 워크트리가 사라진다. 예약 HEAD와 게시 HEAD가 다른지 먼저 확인한다.
  - 수정 전: `AssertionError: ... does not match "stranded source facts do not match the legacy review"` — 라이브와 같은 문구.
  - 수정 후: PASS. 레거시 행이 제거되고 tombstone이 `removed`가 되며, 재결정은 바이트 변화가 없고 fresh 워크스페이스로 복구된다.
- **라이브 상태 사본 재생:** Celly의 `work-parallel.json`·`-finalization.json`·`-stranded-v1.json`·`-coordination.json`을 임시 디렉터리에 복사해 `_converge_legacy_review(ed47d1db…)`를 실행했다.
  - HEAD 코드: `WorkParallelError stranded source facts do not match the legacy review`, 레거시 행 유지.
  - 수정 코드: 성공, 레거시 행 제거, tombstone `removed`.
  - 임시 디렉터리는 삭제했고 원본은 읽기만 했다.
- **격리 회귀 (영향 12개 스위트, 334 tests):**

| 환경 | 결과 |
| --- | --- |
| Windows 격리 (`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 `CIEL_RUNTIME_CONFIG_DIR`) | 334 passed, 0 fail (rework_worktree 81, finalization 114, mcp 37, http_parallel_review 23, restart_terminal 19, review_pin 15, legacy_reviewer 12, receipt_eviction 10, owner_offline 7, legacy_reviewer_view 6, finalization_retention 6, superseded_visibility 4) |
| Linux py310 (`ciel-runtime-test-py310`, `run_each.sh`) | 334 ran, 0 failures, 0 errors, 63 skipped (git 없는 스냅샷의 57dbfb3 beta 비교 스킵) |
| Linux py311 (`ciel-runtime-test-py311`) | 334 ran, 0 failures, 0 errors, 63 skipped |
| 신규 테스트 단독 (Linux py310·py311, `-rA`) | 양쪽 PASSED |
| `tests/test_architecture_budget.py` | 2 passed (`work_parallel_mcp.py` 4,962줄, 상한 내) |

- 첫 Linux 실행에서 `superseded_visibility` 4 errors는 스냅샷이 `docs/journal`의 고정 증거를 빼서 생긴 `FileNotFoundError`였다. 증거 디렉터리를 포함해 다시 돌려 0 errors가 됐다. 두 컨테이너가 같은 `/runner`를 공유해 로그가 섞인 첫 실행 결과는 쓰지 않았다.

## 추적 (수정하지 않음)

- **일시 git 타임아웃의 `workspace_unavailable` 분류:** n1635의 stranded 행은 `observe()`의 git이 39초 타임아웃된 것만으로 만들어졌다. 이후 같은 워크트리의 `git log`·조상 검사는 정상이었다. `_publish_pending_review` 주석은 "소유권 검증 실패만 stranded"라고 하지만, `_workspace_unavailable_reason()`은 타임아웃을 포함한 모든 `WorkspaceError`를 사유로 돌려준다. 타임아웃을 어떻게 다룰지(재시도인지 stranded인지)는 결정이 필요해 이번에는 바꾸지 않았다.

## 발행·설치·라이브 (구분)

- **커밋:** `ebf972e` (`feature/priority-models`, `beta` fast-forward 푸시).
- **GitHub Actions / npm:** `Publish to npm` 런 `45957614254` `completed: success`. `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261127-071913.ebf972e`, `gitHead` 일치. 타볼의 `work_parallel_mcp.py`는 `git show ebf972e`와 같다(`ce72cdb5971b`).
- **7개 승인 루트 해시 확인 오버레이 (`overlay-ebf972e.tgz`):** check 단계에서 7곳 모두 `base`(mcp `af89b9a26278`, constants `877078f3875d`)였다. apply는 **7/7 `ok=True`** — mcp `ce72cdb5971b`, constants `f906aafd0003`, package.json `aa1e6c0c2aa1`(없는 로컬 2개 루트는 `skipped_absent`). 원격 임시 파일 정리 `clean`.
- **라이브 효과:** 없음. 라우터는 기동 시 모듈을 읽으므로 다섯 라우터 모두 이전 코드로 실행 중이다. 틱 규칙상 재시작하지 않았으며, Celly `n1635`는 Celly 라우터가 다음에 재기동한 뒤의 첫 결정에서 이동된다(라이브 상태 사본 재생으로 확인한 경로).
