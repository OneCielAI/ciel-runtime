---
type: fix-record
title: 예약 응답 유실로 우선순위 슬롯이 묶이던 결함
description: 라우터가 예약을 커밋했지만 응답이 모드에 도달하지 못해 포크가 시작되지 않은 예약이 살아 있는 소유 프로세스 아래 무기한 슬롯을 점유하던 경로(Andy a0874c5a, n2923, P3 1/1)를 모드 펌프에서 회수하도록 수정
tags: [work-parallel, reserve, slot, andy, n2923, register.js, fail-first]
timestamp: 2026-11-27T10:40:00Z
---

# 예약 응답 유실로 슬롯이 묶이던 결함

## 라이브 근거 (Andy 9515)

- lease `a0874c5a`(n2923, P3, `work-auto-mwypnx82-0tw2q90vf3z`)의 coordination run:
  - 08:20:08Z에 `reserved`가 됐다. `agent_id`와 `bound_at`은 null이다.
- 작업 트리 준비는 같은 라우터 호출 안에서 진행됐다.
  - `.git` 생성은 08:20:20.97Z, 디렉터리 쓰기는 08:22:02Z까지였다.
  - 리스의 workspace는 `ready`다.
- 08:22:09Z 라우터 로그에 `router_post_client_disconnected path=/ca/mcp ConnectionResetError [WinError 10054]`가 있다. 클라이언트가 응답 전에 연결을 닫은 것이다.
  - Andy의 정상 reserve→bind는 11–23 s다(c4a612a6, c59a9f11, 71cab203). 이 호출은 약 2분이었다.
- 포크는 없다.
  - 그 이름의 subagent 전사나 meta가 없다.
  - native 행이 없다.
  - 그 뒤 durable `parallel_failures` 행도 없다.
- 같은 소유 프로세스(93580)가 09:25:18Z에 다른 리스를 예약하고 바인딩했다. 그래서 펌프 자체는 살아 있었다.
- 10:04Z와 10:18Z 틱 모두 `reserved+slot` 1이다. P3 한도는 1이고 후보 3개가 대기 중이었다.
- 라우터 로그 발췌(로컬 전용, 커밋하지 않음): `.local/tmp/monitor/p3probe-router-0218-0230.txt`

## 원인 (코드)

- `register.js` `parallelDirectSpawn`은 `reserve_next`가 던지면 `parallelPending`만 지운다. 펌프 catch는 메모리 실패만 남긴다.
- 라우터가 이미 커밋한 예약은 그대로 남는다.
- 회수 경로가 이 경우를 덮지 않는다.
  - `work_parallel.py` orphan observe는 소유 프로세스가 죽었을 때만 회수한다.
  - `_release_failed_reservation`은 같은 호출 안에서 준비가 실패했을 때만 동작한다.
  - 모드의 release는 spawn 거부 때만 일어난다.
  - `parallelHydrate`는 같은 이름의 native 에이전트가 있을 때만 bind하고, 나머지는 "unknown reservations are retained"로 둔다.
- 따라서 소유 프로세스가 살아 있으면 미바인딩 비P0 예약에는 회수 경로가 없다.

## 수정

- `parallelDirectSpawn`: `reserve`·`reserve_next` 호출이 던지면, 그 키(= `dispatch_name`)를 `lostReserves`에 기록한다.
  - 라우터가 아무것도 예약하지 않는 거부 문구(no eligible candidate, capacity, launch rate, disabled/paused, lease bound, fork gate missing)는 기록하지 않는다.
  - rework_dispatch는 대상이 아니다.
- 펌프의 `releaseLostReserves`는 P0 처리 뒤, 파킹 재개와 새 실행 전에 실행된다. 대상은 다음 조건을 모두 만족하는 리스다.
  - `reserved`, `slot=true`, `host_id` 없음, `role=worker`
  - `owner_process=own`
  - `workspace_path`가 있다(준비가 끝났다는 뜻)
  - `dispatch_name`이 기록된 키다
- 같은 이름의 native 에이전트가 목록에 없을 때만 `release`를 보낸다.
  - 증거 문구는 "reserve reply … never reached the Work host, so no fork was spawned under that name"이다.
  - 실패는 `rememberParallelFailure('reserve-reply-lost')`로 보인다.
- **근거:** 호스트는 reserve 응답을 받은 뒤에만 spawn한다. 응답을 받지 못한 키로는 이 프로세스가 포크를 만든 적이 없다.
- **이미 묶인 Andy `a0874c5a`:** 그 키가 현재 모드 프로세스 메모리에 없으므로 이 수정으로 자동 해제되지 않는다. 운영 상태 파일은 수정하지 않는다. 해제하려면 사용자나 메인의 명시적 release가 필요하다.

## 검증

- **fail-first:** `tests/test_work_mod_write_gate.py::test_lost_reserve_reply_does_not_pin_the_priority_slot`(`parallel-lost-reserve-reply` 시나리오)
  - HEAD `register.js`: `stuck=True`, `spawned=0`, `calls=['reserve_next','reserve_next']`. 두 번째 reserve가 `active slot capacity reached for P3 (1/1)`로 막혔다.
  - 수정 후: `release:L1`이 `bind:L2`보다 먼저 오고 포크가 1개 시작됐다(통과).
- 회귀 결과는 [사이클 4](../../deployments/cycle-4/record.md)에 있다.
