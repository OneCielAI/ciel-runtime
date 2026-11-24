---
type: fix-record
title: 사이드 잔액 부족(400/402) 실패 복구·MCP 실패 재조정 및 메인 반복 조회 방지
description: 사이드 액터의 Credit balance too low 실패 시 재시도·메인 인계 경로, work_side_fail 멱등성·증거 보존·동일 세대 재조정, 메인 관리 프롬프트 및 work-operations 스킬 지침 보강과 베타 배포 기록
tags: [side-intake, billing-error, work-texts, work-operations, beta-deploy]
timestamp: 2026-11-24T19:06:00Z
---

# 사이드 잔액 부족 실패 복구·MCP 실패 재조정 및 메인 반복 조회 방지

## 배경 및 관측 (2026-11-24)

1. **Prody side 액터 `Credit balance too low` 실패(이미지 #44)**:
   - 격리된 실제 CLI(2.1.329/2.1.331) + 로컬 HTTP 400/402 스텁으로 확인한 결과, 엔진은 `turn.complete`에 `{reason: 'error', answer: ''}`만 전달하고 자식 에이전트의 마지막 assistant 메시지에 `API Error: 400|402 Credit balance too low`를 남긴다.
   - 기존 코드에는 402/결제 전용 분기가 없어 일반 오류 정책(최대 6회)을 돌렸고, `recordFailure()`는 `work_side_fail` MCP 호출 예외를 삼킨 뒤 액터를 종료해 실패 기록 유실·동일 세대 재스폰 위험이 있었으며, `side_intake_mcp.py`의 `take()`가 실패 저장 전에 typed outcome 파일을 삭제했다.
2. **메인 자체 보드 감시·수동 규율 검사(KOBE, 이미지 #45)**:
   - 호스트가 Work 이벤트를 전달함에도 메인이 알림을 기다리며 `work_status`/`work_parallel_status`나 셸로 반복 조회하거나, 기존 읽기 전용 진단(`work capacity`, `work reviews`, `work audit`) 대신 수동 검사에 치우치는 사례가 관측됐다.
3. **기타 관측의 분류(이번 코드 변경 제외)**:
   - **Andy `n1826`(`bc88111e`), `n2773`(`627d9b35`), Celly `n1502`(`abc4cf10`)**: 후속 원장 대조에서 `n1826`·`n2773`은 `finalized/approve`, `n1502`는 `pending` 검수 행 생성 확인(틱 순간의 일시 차이).
   - **Andy `n1166`(`2c02ec6f`)**: 지속 불일치 유지(submitted, generation 1 completion, 마지막 native `running` 후 미관측, 검수 행 없음). terminal/workspace proof 없이 자동 게시하지 않는 기존 가드 유지(미결).
   - **KOBE `159860`(`813c7f3200` + 재알림 2건)**: 유실이 아닌 인박스 대기. 18:25:36Z 최초 `turn_error`와 `use_parent_model=true` 재시도 자격이 기록됨. workspace 직접 읽기는 권한 거부로 소스 마커 미확인(미결).
   - **Betty `unseen_failed` / Prody `stale_unconfirmed`**: 기존 수정 커밋 `4886d62` 범위이며 운영 라우터는 아직 재시작 전 구 프로세스.
   - **KOBE `legacy_no_owner` 원인, Sarah(.25) SSH/SSE, 외부 게이트웨이 503/504/EOF, 기존 재시작 큐·범용 `waiting_on` 감사**: 별도 미결로 유지.

## 수정 (커밋 `fa5b9c1fefc1a3c0ee59376d857e38b77c41d72d`)

- `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js`:
  - `sideTurnError()` 추가: 소유한 side 액터가 `reason === 'error'`로 종료될 때 해당 액터의 마지막 assistant 메시지가 정확히 `API Error: (400|402) Credit balance too low` 형태인 경우에만 `billing_error`로 분류(일반 답변이나 원문 본문 분류 금지).
  - `recordFailure()` → `writeFailure()`: 동일 `(receipt_id, generation)` 펜스로 최대 3회 `work_side_fail` 재시도, 일시 MCP 오류 시 재연결 시도, receipt 응답 검증. 3회 모두 실패하면 `unconfirmedFailures`에 실패 의도를 보관하고 같은 세대가 다시 claim으로 제시될 때 새 액터를 띄우지 않고 실패 기록만 재조정한다.
- `ciel_runtime_support/side_request_outcome.py`:
  - `peek(..., not_before=...)` 추가: 실패 저장 전 증거 파일 삭제를 막고 현재 세대 시작 시각 이후 + 1시간 TTL 범위의 증거만 비파괴 조회한다.
- `ciel_runtime_support/side_intake_mcp.py`, `ciel_runtime_support/side_intake_processing.py`, `ciel_runtime_support/side_forward.py`:
  - `work_side_fail`에 `(session, receipt_id, generation)` 멱등성 적용(이미 같은 세대 실패가 커밋됐으면 중복 집계 없이 기존 영수증 반환, 오래된 세대는 거부).
  - `billing_error`는 공급자 원문 오류 본문을 저장·전달하지 않고 정규화된 사유(`API 크레딧 잔액 부족`)만 남긴다. `_distinct_billing_parent()`로 실패 모델과 다른 명시적 메인 모델 경로가 검증될 때만 1회 재시도하고(`use_parent_model=true`, `fresh-session`/`native-fork` 전환에도 유지), 확인되지 않거나 2차 실패 시 즉시 영수증을 격리해 기존 `side_forward`로 메인에 원문과 정규화 사유를 인계한다.
- `ciel_runtime_support/work_texts.py`, `ciel_runtime_support/claude_mods/ciel-work/hooks/texts.generated.js`:
  - 메인 관리 프롬프트 `mod_instructions`에 호스트 이벤트 대기용 반복 조회·셸 폴링 금지, 실제 이벤트/사용자 요청/현재 판단에 필요한 조회 허용, 독립 작업 없을 때 턴 종료 규칙 추가 후 생성물 동기화.
- `ciel_runtime_support/claude_mods/ciel-work/skills/work-operations/SKILL.md`:
  - 호스트 이벤트 사용·폴링 금지, 수동 규율 검사 전 기존 읽기 전용 진단(`work capacity`, `work reviews`, `work audit`) 우선 사용, 반복 검사의 호스트화 안내 추가.

## 검증

- **실패 테스트 먼저(RED → GREEN)**:
  - 메인 지침(`test_work_texts.py`), 저장 전 오류 증거 유실(`test_side_intake_mcp.py`), 동일 세대 재제시 재스폰(`test_side_intake_mod.py`) 모두 수정 전 실패 확인 후 수정으로 통과.
- **격리 Windows 회귀** (`CIEL_RUNTIME_*` 제거, 임시 `CIEL_RUNTIME_CONFIG_DIR`, `CIEL_RUNTIME_TEST_ISOLATED=1`):
  - `test_side_intake_mcp.py`: 46 ran (1 skip, 0 fail)
  - `test_side_request_outcome.py`: 2 ran (0 fail)
  - `test_side_intake_mod.py`: 72 ran (0 fail)
  - `test_side_forward.py`: 6 ran (0 fail)
  - `test_side_actor_failures_hotfix.py`: 17 ran (0 fail)
  - `test_work_texts.py`: 8 ran (0 fail)
  - `claude plugin validate` 통과, `claude plugin test` 94 pass / 0 fail.
  - `compileall`, `ruff check`, `npm run check:docs`, `npm pack --dry-run` 통과.
- **기존 Linux 컨테이너 회귀** (`ciel-runtime-test-py310`, `ciel-runtime-test-py311`):
  - 위 6개 테스트 파일 모두 양쪽 컨테이너 각 151 ran (1 skip, 0 fail).
- **격리 실제 CLI E2E** (Claude Code 2.1.331, 실제 `ciel-work` 패치 로드, 실제 MCP dispatch, 로컬 HTTP 402 스텁, 실제 자격 증명 없음):
  - 증거: [real-cli-402-result.json](evidence/real-cli-402-result.json)
  - 확인: `category="billing_error"`, `attempt_count=1`, `quarantined=true`, `retryable=false`, `notice="appended"`, `main_read=true`(실제 메인 모델 요청 본문에 원문과 정규화된 실패 사유 포함).
  - 참고: 실패 모델과 다른 명시적 메인 모델로의 1회 재시도는 백엔드 단위 테스트로 검증했으며, 운영 Prody에서는 재현·재시작하지 않았다.

## 배포 (2026-11-24)

- 커밋 `fa5b9c1` (`fa5b9c1fefc1a3c0ee59376d857e38b77c41d72d`)를 `feature/priority-models`와 `beta`에 atomic push.
- `Publish to npm` 실행 `45464502208` 성공:
  - `@one-ciel-ai/ciel-runtime@beta` = `0.2.51-beta.20261124-185426.fa5b9c1`
  - `gitHead` = `fa5b9c1fefc1a3c0ee59376d857e38b77c41d72d`
  - npm tarball 내 변경 파일 8개 SHA-256이 `overlay-fa5b9c1` `.new`와 **8/8 일치**.
- 해시 확인 오버레이 `overlay-fa5b9c1` (8개 파일, base `f7a06ee`) 7개 루트 설치 결과:

| 루트 | 설치 전 | 설치 후 | 시각 (UTC) |
|---|---|---|---|
| 로컬 `ciel-runtime-hotfix5-20261119-210220` | `base 8/8` | `new 8/8` | 19:05:50Z |
| 로컬 `.local/share/ciel-runtime` | `base 8/8` | `new 8/8` | 19:05:50Z |
| 로컬 npm 전역 | `base 8/8` | `new 8/8` | 19:05:50Z |
| Celly (`G:\ciel-bridge-service\.npm-global\...`) | `base 8/8` | `new 8/8` | 19:05:50Z |
| KOBE (`u24`) | `base 8/8` | `new 8/8` | 19:05:50Z–19:05:55Z |
| Prody (`u41`) | `base 8/8` | `new 8/8` | 19:05:55Z–19:05:58Z |
| Betty (`u42`) | `base 8/8` | `new 8/8` | 19:05:55Z–19:05:58Z |

- **라이브 효과(파일 설치와 구분)**: 운영 세션과 라우터는 재시작하지 않았고 운영 상태 파일도 수정하지 않았다. Python 변경(`side_intake_mcp.py`, `side_intake_processing.py`, `side_request_outcome.py`, `side_forward.py`, `work_texts.py`)은 라우터 재시작 후, 모드·스킬 변경(`side-intake.js`, `texts.generated.js`, `SKILL.md`)은 세션/플러그인 재로드 후 라이브 반영된다.
