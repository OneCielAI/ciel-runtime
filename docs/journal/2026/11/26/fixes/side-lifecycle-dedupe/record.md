---
type: fix-record
title: 사이드 중복 판정에서 후속 라이프사이클 이벤트 분리
description: message.renotify·thread.eta_requested·task.status_changed 등이 최초 메시지의 duplicate_source로 버려지던 결함 수정
tags: [side-sse, deduplication, walkie, renotify]
timestamp: 2026-11-26T05:56:00Z
---

# 사이드 중복 판정에서 후속 라이프사이클 이벤트 분리

## 관측 (2026-11-26 UTC, Andy 운영)

- Andy 최신 100건 중 `duplicate_source`로 종결된 8건 가운데 6건이 최초 중복 전달이 아닌 후속 이벤트였다.
  - `#161594` `388c998e1b15` (`message.renotify`, 05:44:09Z, P2, 수정 본문에 MCP/REST 요구 추가) → 선행 `c5dba7917010` (`task.auto_assigned`, 05:19:27Z, P3)에 막혀 05:44:13Z `duplicate_source` 종결.
  - `#161501` `message.renotify` 3건(`a5f85da97c36`, `c76a0077953c`, `f5373aaf2578`)과 `thread.eta_requested` 1건(`ffb60ebc4101`, 03:40:40Z) → 선행 `task.auto_assigned`에 막혀 종결.
  - `#161412` `task.status_changed` 1건(`7631f426c6dd`, 23:55:06Z) → 선행 `task.auto_assigned`에 막혀 종결.
- Walkie 서버(`notification-delivery.ts`)는 `message.renotify`, `message.edited`, `message.approved`·`message.rejected`, `task.status_changed`, `task.owner_changed`에 서로 다른 CloudEvents `id`를 부여한다.

## 원인 (확인)

- `ciel_runtime_support/side_intake_processing.py`의 `_source_holder`가 `source_identity()`(`message:<conversation_id>:<message_id>`)만으로 중복 락을 잡았다. `data.event_type`을 보지 않아 최초 메시지(`message.created`/`task.auto_assigned`)가 처리된 뒤 같은 메시지에 도착한 후속 이벤트도 모두 `duplicate_source`로 종결됐다.

## 수정 (`ciel_runtime_support/side_intake_processing.py`)

- `INITIAL_SOURCE_EVENTS = frozenset({'', 'message.created', 'task.auto_assigned'})`와 `_source_event_type()`을 추가했다.
- `_source_holder()`는 최초 전달 이벤트(`message.created`, `task.auto_assigned`, 또는 `data.event_type` 없음)에만 메시지 단위 중복 락을 적용한다. 그 외 후속 라이프사이클 이벤트(`message.renotify`, `thread.eta_requested`, `task.status_changed`, `message.edited`, `message.approved` 등)는 각각의 `event_key`로 접수·클레임된다.
- 같은 이벤트의 재전달은 기존처럼 `SideIntakeInbox.receive()`가 `sha256(event_key)`로 중복 제거한다.
- 부수 시험 보정(`tests/test_work_approval_workflow.py`): `test_p3_no_receipt_ack_for_a_registered_approval_event`가 앞선 미클레임 `walkie:ack`를 남긴 채 두 번째 `evt-ack2`가 먼저 클레임되길 기대하던 순서 의존을, 같은 `walkie:ack` 이벤트를 클레임·정산하도록 고쳤다(`a193117` 도착순 클레임에서 드러남).

## 검증

- **수정 전 실패(`a193117`):** 새 시험 `test_followup_lifecycle_events_are_not_deduplicated_against_initial_message`가 `message.renotify`에서 `AssertionError: unexpectedly None`으로 실패.
- **수정 후 통과:** `test_side_work_decision.py`, `test_side_intake_mcp.py`, `test_work_approval_workflow.py`, `test_side_actor_failures_hotfix.py`
  - 격리 Windows(`CIEL_RUNTIME_*` 제거, `CIEL_RUNTIME_TEST_ISOLATED=1`, 임시 설정): 128 통과, 13 스킵, 0 실패(35 서브테스트).
  - 기존 Linux `ciel-runtime-test-py310`: 128 통과, 13 스킵, 0 실패.
  - 기존 Linux `ciel-runtime-test-py311`: 128 통과, 13 스킵, 0 실패.
  - `py_compile` 통과.

## 발행·설치

- 커밋 `b0cabd17f6d6726b1e2c016007884bd5b0a1b441`, npm `@beta` = `0.2.51-beta.20261126-060930.b0cabd1`(gitHead 일치).
- tarball 안 `ciel_runtime_support/side_intake_processing.py`가 커밋과 SHA-256 일치(`bfc20b7b3d0f` → `5ad087dfeb7c`).
- 승인된 7개 루트(로컬 4곳, KOBE `u24`, Prody `u41`, Betty `u42`)에 해시 확인 설치(`check` 7/7 `base` → `apply` 7/7 `ok=True`). `.cmd` 래퍼와 버전 메타데이터는 건드리지 않았다.
- **라이브 효과 구분:** 이 수정은 라우터 Python 코드이므로 파일 설치만 완료된 상태다. 모니터링 규칙에 따라 라우터 재시작을 하지 않아 실행 중 라우터에는 아직 반영되지 않았다(재시작 후 라이브 반영).
