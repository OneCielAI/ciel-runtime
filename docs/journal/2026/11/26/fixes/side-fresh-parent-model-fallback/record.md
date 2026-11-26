---
type: fix-record
title: fresh-session 사이드 액터의 메인 모델 폴백 지문 불일치 수정
description: work_side_intake_session=fresh 모드에서 사이드 모델 실패 시 model_fingerprint='fresh-session'이 work_side_claim의 고정 'native-fork' 비교에서 불일치해 use_parent_model이 항상 False로 내려가던 결함을 수정.
tags: [side-intake, fresh-session, main-model-fallback, andy, fix]
timestamp: 2026-11-26T19:45:00Z
---

# fresh-session 사이드 액터의 메인 모델 폴백 지문 불일치 수정

## 문제 (Andy 라이브 실측 및 코드 확인)

- **라이브 관측 (`2026-11-26 18:29–19:26Z`, Andy `claude:feeac3ad-b623-4b36-abfe-a3dd7b678c20`, 포트 `9505`):**
  - Andy의 사이드 설정은 `work_side_intake_model = _API_KEYS-SSE-1`, `work_side_intake_session = fresh`.
  - `18:35:51Z`부터 로컬 게이트웨이(`127.0.0.1:20128/v1/messages`)가 `_API_KEYS-SSE-1` 요청에 대해 `400 MissingSessionID`를 반환(`43/49`건)해 사이드 턴이 `turn_error`로 실패했다.
  - 원래 설계(커밋 `cadd1a5`)상 사이드 모델이 `turn_error`로 실패하면 1회차 직후 `use_parent_model=True`로 기록되고 2회차부터 즉시 메인 모델(`_Walkie_P1`, `MissingSessionID` 0건)로 재시도되어야 한다.
  - 그러나 Andy 디스크 영수증(`42d3abf9`, `64bdab49`, `fc9c605b`, `4c92655c`, `c26d84d9`)에는 `side_failure.use_parent_model=true`와 `"model_fingerprint": "fresh-session"`이 기록되었음에도, 실제 스폰된 서브에이전트 `.meta.json`(예: `42d3abf9` 1~6회차, `5650fea7` 1~4회차 10건 전원)의 `description`이 `"Side SSE <12hex>"`로 `' main-model'` 접미사가 한 번도 붙지 않았다. 그 결과 2~6회차 전부가 다시 `_API_KEYS-SSE-1`로 들어가 6회 실패 후 격리(`quarantined`)됐다.
- **근본 원인 (`ciel-runtime` 제품 결함):**
  - `ciel_runtime_support/claude_mods/ciel-work/hooks/side-intake.js:2294`는 `fresh` 세션에서 `modelFingerprint = 'fresh-session'`을 기록한다(커밋 `db23560`).
  - `ciel_runtime_support/side_intake_mcp.py:11,556`의 `work_side_claim`은 세션 모드와 무관하게 `model_fingerprint=NATIVE_FORK_FINGERPRINT` (`'native-fork'`)를 고정 전달했다.
  - `ciel_runtime_support/side_intake_processing.py:173-175`는 `failure.get('model_fingerprint') == model_fingerprint`를 단순 동등 비교하므로 `'fresh-session' == 'native-fork'`(및 `'' == 'native-fork'`)가 항상 `False`가 되어, `work_side_claim`이 `use_parent_model: False`를 반환했다.

## 수정

- **`ciel_runtime_support/side_intake_mcp.py`:** `FRESH_SESSION_FINGERPRINT = 'fresh-session'`, `NATIVE_SIDE_FINGERPRINTS = (NATIVE_FORK_FINGERPRINT, FRESH_SESSION_FINGERPRINT)`를 정의하고 `work_side_claim`이 두 네이티브 사이드 지문을 모두 허용하도록 전달.
- **`ciel_runtime_support/side_intake_processing.py`:**
  - `_canonical_fingerprint()`로 레거시 `main_fallback` 회수 기록 시 문자열 지문을 유지.
  - `_fingerprint_matches(stored, current)`로 `current`가 컬렉션이면 멤버십 검사, `stored`가 비어 있으면 호환성상 일치로 처리하고, 별도의 커스텀 지문 불일치(`model-a` vs `model-b`)는 기존대로 `False` 유지.
- **테스트 추가:**
  - `tests/test_side_intake_mcp.py`: `fresh-session`에서 `turn_error`·`model_not_found` 발생 시 즉시 `use_parent_model=True` 반환 및 `fork` 전환 후에도 sticky 유지, `model_fingerprint` 생략 시 호환성, 커스텀 지문 불일치 시 `False` 유지 검증(4건).
  - `tests/work_fork_gate_harness.mjs` & `tests/test_work_fork_gate.py`: `ciel-work:side-fresh` 타입의 `Side SSE <12hex> main-model` 재시도가 `ciel-work-gate`의 `turn.step`에서 메인 모델(설정 모델 또는 `inherit`)로 라우팅됨을 추가 검증.

## 검증

- **수정 전 실패 재현 (fail-first):**
  - `tests/test_side_intake_mcp.py`: 신규 4건 중 3건 `FAIL` (`test_fresh_session_side_model_api_error_retries_at_once_on_main_model_and_stays_there`, `test_fresh_session_model_unavailable_retries_on_main_model_then_quarantines`, `test_omitted_model_fingerprint_still_retries_on_main_model` — 모두 `use_parent_model` `True != False`).
- **수정 후 통과:**
  - `py_compile` (`side_intake_mcp.py`, `side_intake_processing.py`) OK.
  - `claude plugin validate ciel-work` · `ciel-work-gate`: `Validation passed` (exit 0).
  - `claude plugin test ciel-work`: **97 pass, 0 fail** (3 files).
  - 임시 격리 Windows (`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): 영향 18개 스위트 **518 tests run, 0 failures, 46 skipped** (`test_side_intake_mcp` 54, `test_side_actor_failures_hotfix` 20, `test_side_claim_latency` 4, `test_side_discard_failed` 7, `test_side_forward` 7, `test_side_intake_inbox` 5, `test_side_intake_mailbox` 9, `test_side_intake_mod` 75, `test_side_receipt_projection` 11, `test_side_request_outcome` 2, `test_side_understanding_flow` 9, `test_side_work_decision` 46, `test_side_real_backend` 8, `test_work_fork_gate` 34, `test_work_http_side_receipts` 11, `test_work_http_side_drain` 3, `test_work_approval_workflow` 25, `test_work_mod_write_gate` 188).
  - 기존 Linux `ciel-runtime-test-py310` · `ciel-runtime-test-py311`: 동일 18개 스위트 **518 tests run, 0 failures, 46 skipped** (`ALL_LINUX_OK`).
  - 비고: `tests/test_side_queue_starvation.py::test_a_gate_recheck_is_claimed_before_p1_events`는 본 수정과 무관하게 커밋 `a193117`에서 우선순위 레인을 제거한 뒤부터 이미 실패하던 기존 테스트임을 `git show a193117`로 확인(본 건 범위 외로 미변경).
