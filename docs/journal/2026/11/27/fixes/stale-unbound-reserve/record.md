---
type: fix-record
title: 키를 모르는 오래된 미바인딩 예약이 P3 슬롯을 8시간 넘게 잡던 결함
description: 예약 응답 유실 수정(2fe04af)은 이번 모드 생애에 reserve가 던진 키만 풀어서, 그 전에 생긴 a0874c5a(n2923) 예약이 P3 슬롯 하나를 08:20Z부터 계속 잡고 P3 후보 5건을 막았다. 같은 조건의 예약을 15분 관측 후 해제하도록 수정
tags: [work-parallel, register.js, reservation, slot, p3, andy, fail-first]
timestamp: 2026-11-27T17:00:00Z
---

# 오래된 미바인딩 예약 해제

## 사용자 지적과 보고

- 사용자: "아이템이 차단이 해제되면 자동으로 포크가 재가동되어야 하는데 안 되고 있다", "큐의 병렬성은 워크보드의 기계적인 동작으로 … 차단만 해제되면 포크가 동작해야 하는데 안 되고 있다"
- Andy_Main(16:4xZ): free slot이 있는데 포크가 뜨지 않는다. orphan-release는 owner live라 거부됐고, orphan-reconcile은 대상이 0이었다.

## 라이브 근거 (Andy 9515, 읽기 전용, 16:43–16:56Z)

- 우선순위 진단 P3: `limit 1, active 1, candidates 5, fenced_states {reserved: 1}, state full`.
- 리스 `a0874c5a`(n2923): `reserved`, `slot True`, `host_id null`, `dispatch_name work-auto-mwypnx82-0tw2q90vf3z`.
  - coordination run `reserved_at 11-27 08:20:08Z`, `bound_at None`, `native None`.
  - 네이티브 에이전트 목록에 그 이름의 에이전트가 없다.
- 2fe04af의 `releaseLostReserves`는 같은 모듈 생애에 reserve가 던진 키(`lostReserves`)만 대상으로 했다.
  - 그 뒤 모드가 다시 로드됐기 때문에(12:10Z, 13:13Z) 이 예약은 기록된 키가 아니었다.
  - [사이클 4 기록](../../deployments/cycle-4/record.md)의 "남은 항목"에 이미 적혀 있었다.

## 수정 (`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`)

- `releaseLostReserves`는 이제 이 프로세스의 미바인딩 worker 예약을 모두 본다. 조건은 `reserved`, `slot`, `host_id` 없음, `dispatch_name`·`workspace_path` 있음, `owner_process own`이다.
  - 기록된 유실 키는 기존과 같이 바로 대상이 된다.
  - 그 밖의 예약은 다음을 모두 만족할 때 대상이 된다.
    - 이 모듈이 미바인딩 상태로 처음 본 뒤 15분(`UNBOUND_RESERVE_MS`)이 지났다.
    - 그동안 이 모듈의 reserve 호출(`parallelPending`의 키 또는 lease id)이 진행 중이 아니다.
  - 대상이라도 같은 이름의 네이티브 에이전트가 목록에 있으면 풀지 않는다.
  - 해제는 기존 router `release` 이벤트를 쓴다. 증거 문구는 "No fork named … appeared while the Work host saw this reservation unbound for 15 minutes …"이고, 실패 기록 코드는 `reserve-unbound-stale`이다.
- **근거:** 호스트는 reserve 응답을 받은 직후 몇 초 안에 spawn한다. worktree 준비 예산(최대 약 3분)을 넘긴 15분 동안 이름이 나타나지 않고 진행 중인 호출도 없다면, 그 예약 뒤에 포크는 없다.

## 검증

- **fail-first:** `tests/test_work_mod_write_gate.py::test_an_old_unbound_reservation_without_a_fork_is_released`(`parallel-stale-unbound-reserve`).
  - 수정 전 결과는 `stuck True, spawned 0, calls ['reserve_next','reserve_next']`였다.
- **수정 후 통과:**
  - 첫 관측 때는 해제하지 않는다(`early 0`).
  - 16분 뒤에는 `release:L0` 한 번 → 새 예약 → bind → spawn 1 순서로 진행된다.
  - 기존 lost-reserve와 refill 시나리오도 통과한다.
- `claude plugin test ciel-work` 99/0.
- 영향 회귀는 [사이클 6](../../deployments/cycle-6/record.md)에 있다.
