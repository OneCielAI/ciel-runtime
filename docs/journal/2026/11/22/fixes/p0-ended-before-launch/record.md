---
type: fix-record
title: P0가 해제된 뒤 남은 발사 전 P0 예약
description: P0 표시가 해제되기 전에 예약만 된 P0 리스가 포크 없이 슬롯을 계속 잡던 Celly n1326 문제의 증거, 수정, 검증 기록.
tags: [work-mode, p0, reservation, celly]
timestamp: 2026-11-22T20:50:00Z
---

## 운영 관측 (읽기 전용, Celly)

- lease `c58382cab38e…`(n1326, P1): `reserved`, `slot=True`, `host_id=None`. `dispatch_name`은 `work-p0-ca17957c…`이고, 원장 workspace는 준비(ready)됐지만 coordination run의 `workspace`는 `None`이다. run은 `reserved`(18:25:44Z), P0 intent `ca17957c…`는 `reserved`(18:25:44Z)다.
- P0 표시: Celly scope의 루트 n1032 marker가 `18:29:39Z`에 `cleared`(`ended_reason=priority_changed`, `set_by=user`)됐다.
- 그 뒤 2시간 넘게 포크가 붙지 않았다. Celly 메인 보고: 다시 배정하면 `no eligible candidate`가 나오고, 메인 도구로는 예약을 풀 수 없다(19:31Z, 19:36Z, 20:21Z, 20:30Z).

## 원인

- P0 예약 재시도는 P0 레인만 한다. `_p0_next_rows`는 활성 P0 루트의 leaf만 보므로, marker가 해제되면 이 예약은 다시 시도되지 않는다.
- 일반 레인에서는 같은 아이템이 이 활성 리스에 막혀(fenced) `no eligible candidate`가 된다.
- P0 해제(`prepare_clear`)는 열린 intent를 건드리지 않는다. `_close_ended_p0_intents`는 리스가 끝난 경우만 닫는다. `p0_launch_absent`는 `spawning` 상태만 다룬다.
- 그래서 해제 시점에 발사 전(`reserved`)이던 P0 예약은 아무 경로도 풀지 않는다.
- 18:25Z에 예약이 남은 경위는 이렇게 본다(파일 상태로 추정): coordination 파일 한도 결함(`bc1bac5`에서 수정) 때문에 `coord.attach_workspace`가 실패했고, 설계대로 "P0 workspace 결과 불명, 다음 P0 시도에서 재사용"으로 남았다. 원장 workspace는 있고 run workspace는 `None`인 것이 이 경로와 맞는다.

## 수정

`work_parallel_mcp._release_unlaunched_ended_p0`를 추가하고 `_reserve_assignment` 시작에서 호출한다. 일반 reserve와 P0 pump의 `p0_reserve_next`가 모두 여기를 지난다.

- 대상은 이 scope의 intent 중 다음 조건을 모두 만족하는 것이다.
  - intent가 `reserved` 상태다.
  - 리스가 `reserved`이고 `host_id`가 `None`이며, `dispatch_name`이 intent와 같다.
  - 루트에 일치하는 활성 P0 marker가 없다.
- 대상은 기존 `_release_failed_reservation`으로 되돌린다(리스 해제, run 취소, intent `reserved → intent`). 사유는 `P0 ended before launch`다.
- `spawning` intent는 건드리지 않는다. native spawn보다 먼저 `spawning` 전환이 일어나므로, `reserved`면 아직 포크가 시작되지 않았다.

## 검증

- 실패 테스트를 먼저 썼다: `test_unlaunched_p0_reservation_is_released_once_its_root_is_no_longer_p0`. Celly와 같은 경로를 따른다(coordination workspace attach 실패 → 예약 유지 → P0 해제 → 다음 reserve). 수정 전에는 리스가 남아 실패했고, 수정 후 통과한다.
- 함께 추가한 `test_a_spawning_p0_launch_is_not_released_when_its_root_is_cleared`는 수정 전후 모두 통과한다(spawning은 유지).
- `tests/test_work_parallel_p0_events.py` 41건 통과.
- Celly 실제 상태 임시 복사본에 적용: `c58382ca` 리스와 run만 제거됐고, 다른 리스·run 변경은 0건, intent는 `intent/None`이 됐다. Andy 상태 복사본(scope 3개)에서는 해당 0건이었다.

## 운영 경과 (이 수정과 무관하게 해소됨)

- 로컬 머신이 `20:53:36Z`에 재부팅됐다. Celly 런처가 `20:54:44Z`에 `work_orphan_sweep`를 실행했고, `c58382cab38e priority=P1 state=reserved owner=dead`를 해제했다(같은 sweep에서 6건 해제). 이 sweep은 소유 세션 프로세스(16140)가 사라졌을 때 동작하는 기존 경로다.
- intent `ca17957c`는 `cancelled`가 됐다. n1326은 `20:55:13Z`에 새 일반 리스 `800555e16d58`로 실행 중이다.
- 따라서 이번 건은 세션 재부팅으로 풀렸다. 소유 세션이 살아 있는 동안 같은 상태가 생기면 이 수정 없이는 계속 남는다.

## 남은 항목

- 이 수정이 운영에 적용되려면 해시 확인 설치 후 라우터가 새로 시작되어야 한다.
- 메인이 직접 상태를 바꾸는 수단(사용자 요청)은 이번 범위가 아니다.
