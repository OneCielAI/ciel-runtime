---
type: fix-record
title: 채널 커서 복구의 종결 메시지 무한 되감기 방지
description: 이미 영구 종결(replied/skipped/failed/interrupted_uncertain)된 메시지가 8MB 전사 꼬리에 queued_command로 남아 있을 때 ChannelCursorRecoveryService가 5초마다 커서를 되감아 수천 건의 중복 스캔·로그를 일으키던 결함 수정
tags: [channel, cursor-recovery, celly, wake-delivery]
timestamp: 2026-11-26T13:42:00Z
---

# 채널 커서 복구의 종결 메시지 무한 되감기 방지

## 관측 (2026-11-26 UTC, Celly 운영 `message_id=16799`)

- `13:30:13Z` 모니터링 틱에서 Celly 라우터(`9385-52ea0f8a2296`) 로그에 `channel_stdin_proxy_recover_queued_only message_id=16799 cursor=16851 recovered_cursor=16798` 200건(12:57:56Z부터) 및 `cursor=16852` 14건(13:22:08Z부터), 총 **214회**가 관측됐다.
- Celly `router.log`와 메인 전사(`9430f5e8-08fb-4fb7-a3f8-bcbe56ca2425.jsonl`) 읽기 전용 확인 결과:
  - `16799`는 약 5시간 전(`08:04:16Z`) 수신·응답 완료되어 영속 라이프사이클에 `durable_replied`로 종결된 메시지였으나, 전사 8 MB 꼬리 안에 `attachment.type="queued_command"` 행이 남아 있었고 응답 턴이 8 MB 창 밖으로 밀리면서 `wake_states([16799], text)`가 `"missing"`을 반환했다.
  - `12:57:56Z`–`13:23:33Z` 약 25분간 5초 캐시 만료·파일 크기 갱신 때마다 커서를 `16851→16798`로 되감았고, `channel_pending_injection`이 `16799..16852` 50여 건을 매번 재스캔하며 `channel_stdin_proxy_skipped_noise(reason=durable_replied)` 및 과거 실패 메시지 `channel_stdin_proxy_blocked`(`16801`·`16806`·`16838`·`16842`·`16844`) 등 총 5,581행의 중복 로그와 8 MB 전사 꼬리 반복 읽기를 발생시켰다.

## 원인 (확인)

- `ciel_runtime_support/channel_cursor_recovery.py` (`ChannelCursorRecoveryService._recover_from_text`, `L76–92`)는 `queued_command_ids(text)` 중 `wake_states[mid] == "missing"`인 가장 오래된 메시지로 무조건 커서를 되감았으며, 해당 메시지가 이미 `wake_store.lifecycle`에서 영구 종결(`replied`, `skipped`, `failed`, `interrupted_uncertain`) 상태인지 확인하지 않았다.
- 그 결과 `channel_pending_injection.py:253–279`에서 즉시 스킵할 이미 끝난 메시지로 커서를 반복 되감는 무한 루프(전사 8 MB 꼬리에서 해당 행이 밀려날 때까지 지속)가 발생했다.

## 수정

- `ciel_runtime_support/channel_wake_delivery_repository.py`: `ChannelWakeDeliveryRepository.is_terminal(message_id)` 추가(인메모리 `failed` 또는 영속 상태가 `replied`/`skipped`/`failed`/`interrupted_uncertain`이면 `True`; 필드 수 불변).
- `ciel_runtime_support/channel_cursor_recovery.py`: `ChannelCursorRecoveryPorts`에 선택 포트 `is_terminal: Callable[[int], bool] | None = None` 추가(필드 수 7→8, 아키텍처 상한 10 이내), `_recover_from_text()`가 `states.get(message_id) == "missing"`이라도 `is_terminal(message_id)`가 참이면 건너뛰도록 수정.
- `ciel_runtime_support/channel_wake_context.py`: `ChannelWakeContext.cursor_recovery_service()`에 `is_terminal=self.pending_delivery.lifecycle.is_terminal` 연결(필드 수 10 불변).

## 검증

- **수정 전 실패 재현 (`c926967`):**
  - `tests/test_channel_cursor_recovery.py::test_already_terminal_queued_command_does_not_rewind_cursor`: `FAIL` (`AttributeError: 'ChannelWakeDeliveryRepository' object has no attribute 'is_terminal'`).
- **수정 후 통과:**
  - `py_compile` 3개 모듈 OK.
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): `test_channel_cursor_recovery.py` + `test_channel_wake_delivery_repository.py` + `test_channel_pending_injection.py` + `test_channel_llm_context.py` + `test_channel_injection_architecture.py` + `test_architecture_contracts.py` **321 passed, 42 skipped, 792 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py310` (동일 6개 스위트): **321 passed, 42 skipped, 792 subtests passed**.
  - 기존 Linux `ciel-runtime-test-py311` (동일 6개 스위트): **321 passed, 42 skipped, 792 subtests passed**.

## 발행·설치

- 커밋 `42abda18b0bf4d1f270c8cdaca09238cc335cdb5`, GitHub Actions `Publish to npm` (`45831968493`) 성공, npm `@beta` = `0.2.51-beta.20261126-134400.42abda1`(`gitHead` 일치).
- npm tarball 내부 3개 파일 SHA-256과 커밋 `42abda1` 일치 확인:
  - `ciel_runtime_support/channel_cursor_recovery.py`: `2244d79ef4c7` → `09c23bbd7cef` (3,771 B).
  - `ciel_runtime_support/channel_wake_delivery_repository.py`: `25a8654f6bf4` → `1541371aac5f` (13,496 B).
  - `ciel_runtime_support/channel_wake_context.py`: `5e20730de9b2` → `7f91d3bc02ba` (31,302 B).
- 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`) 해시 확인 설치 완료(`check` 7/7 `base` → `apply` 7/7 `ok=True`, `.cmd` 래퍼·버전 메타 미변경, 원격 임시 오버레이 정리 완료).
- **라이브 효과 구분:** Python 라우터 모듈 3개 파일 설치만 완료된 상태다. 모니터링 규칙상 실행 중 라우터와 CLI 세션을 재시작하지 않았으므로, 기동 중인 라우터 메모리에는 재시작 전까지 이전 코드가 유지된다.

