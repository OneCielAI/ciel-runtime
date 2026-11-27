---
type: fix-record
title: 응답 없는 라우터를 already_down으로 남겨 두던 kill guarantee 결함
description: /health가 답하지 않지만 리스너가 살아 있는 라우터를 claude 종료 시 already_down으로 판정해 종료하지 않던 stop_with_guarantee를, 리스너를 보고 pid 파일 라우터를 멈추도록 수정
tags: [router, lifecycle, kill-guarantee, memoryerror, andy, fail-first]
timestamp: 2026-11-27T08:20:00Z
---

# 응답 없는 라우터를 already_down으로 남겨 두던 kill guarantee 결함

## 라이브 근거 (Andy 9505)

- 05:39:06–15Z에 라우터 `95176`이 `MemoryError` 3건을 낸 뒤 `/health`에 답하지 않았다. 슈퍼바이저는 `listener_reachable_health_busy`로 keep-alive만 반복했다.
- 05:54:50Z Claude CLI 종료 시 로그 순서는 `router_managed_lifetime_shutdown reason=idle_no_clients` → `router_kill_guarantee reason=claude_exit state=already_down` → `router_lifetime_stopped stopped=False`였다.
- 05:55:57Z에도 `netstat`에서 `127.0.0.1:9505 LISTENING 95176`이 보였다. 새 런처는 포트 `9515`로 떴고, 옛 프로세스는 수동으로 종료됐다.

## 원인 (코드)

- `router_process_lifecycle.stop_with_guarantee()`는 첫 `state.health()`가 `None`이면 리스너를 보지 않고 바로 `already_down`으로 `False`를 돌려줬다.
- 응답 없는 health는 "꺼짐"과 "바쁨·정지"를 구분하지 못하는데, 같은 모듈의 `ensure_port_available`과 슈퍼바이저는 `listener_pids()`/`listener_reachable()`로 둘을 구분한다.

## 수정

- health가 없고 리스너도 없을 때만 `already_down`으로 판정한다.
- health가 없는데 리스너가 있으면 `state=listener_without_health`를 남기고 `stop_processes()`를 부른다. 이 함수는 이 설정의 pid 파일 라우터만 멈추며, health로 신원을 증명할 수 없는 프로세스는 건드리지 않는다.
- 이후 루프는 health와 리스너가 모두 사라질 때 `killed`로 판정한다. 데드라인까지 남아 있으면 기존과 같이 `still_up`으로 오류를 낸다.
- health가 응답하는 기존 경로는 바뀌지 않는다.

## 검증

- **fail-first:** `tests/test_claude_native_provider.py::StopRouterGuaranteeTests`
  - `test_stops_a_listening_router_whose_health_does_not_answer`: 수정 전 `False is not true`(stop 미호출, `already_down`).
  - `test_listener_that_outlives_the_stop_is_reported_not_called_down`: 수정 전 `RuntimeError not raised`.
  - 수정 후 두 테스트와 기존 5개가 모두 통과한다(`StopRouterGuaranteeTests` 20 passed).
- 회귀 결과는 [사이클 4 회귀](../../deployments/cycle-4/record.md)에 있다.
