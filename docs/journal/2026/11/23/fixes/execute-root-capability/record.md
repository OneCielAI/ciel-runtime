---
type: fix-record
title: 옛 라우터에서 대기 목표 승인이 활성 목표 승인으로 바뀌던 혼합 버전 결함
description: 5e02ee7 설치 직후 모드는 재로드로 라이브가 되지만 라우터는 재시작 전까지 옛 코드다. 옛 라우터는 work_execute의 root_id를 무시하고 활성 목표를 승인한다. 재현, 실패 테스트, 능력 광고 기반 수정, 검증.
tags: [work-mode, work-execute, mixed-version, capability, rollout]
timestamp: 2026-11-23T14:55:00Z
---

# 발견 경위

- 5e02ee7([work-queue-restart](/docs/journal/2026/11/23/fixes/work-queue-restart/record.md))을 6개 루트에 해시 확인으로 설치했다(14:36–14:37Z).
- 모드는 바로 재로드됐다: Andy 14:36:44Z, KOBE 14:37:14Z(`ciel-work: reloaded (19 hooks`). 라우터 Python은 재시작 전까지 옛 코드(fcce6d7)다. 라우터 재시작은 금지 범위라 하지 않는다.
- 재로드된 모드의 메인 지침(`mod_instructions`)에는 "줄을 선 목표는 work_execute의 root_id로 그 목표만 승인"이 들어 있었다. 모드의 ask 승인 대화상자도 `root_id`의 계획을 보여 주고 그 `root_id`를 실어 보낸다.

# 재현 (fcce6d7 코드, 격리 temp)

- 목표 두 개(활성 n1, 대기 n4)를 만들고 `work_execute(root_id=n4, user_confirmed=true)`를 호출했다.
  - 활성 목표가 planning일 때: 결과는 `n1 executing, n4 planning`, 응답은 "Plan approved". 요청한 목표가 아니라 활성 목표가 승인됐다.
  - 활성 목표가 executing일 때: "Work is already executing this plan.", n4는 planning 그대로.
- 원인(코드): fcce6d7 `work_mcp_tools._execute(store, approval_ask, user_confirmed)`는 root를 받지 않고 `tree.set_phase(doc, EXECUTING)`의 기본값(활성 목표)을 쓴다. 디스패치는 `root_id`를 읽지 않으며, 모르는 인수를 거부하지도 않는다.
- REST `execute`(ctl)는 옛 라우터에서 `_only(args, {"expected_version", "user_confirmed"})`로 `root_id`를 거부한다. 따라서 잘못 승인되는 경로는 MCP `work_execute`다.
- 라이브 피해: 설치 후 14:43:55Z까지 네 호스트 대화에 `work_execute` 호출은 0건이다(Andy 마지막 호출 14:23:08Z, 재로드 전, `root_id` 없음).

# 실패 테스트 먼저

- `test_work_mod_write_gate.py`
  - `test_queued_goal_is_never_sent_to_a_router_that_ignores_root_id`(ask·auto). 수정 전에는 대화상자가 열리고 `root_id=n9`가 라우터로 전달됐다.
  - `test_root_id_instruction_follows_the_router_capability`. 수정 전에는 옛 라우터에서도 지침이 `root_id`를 안내했다.
  - 함께 추가했고 수정 전에도 통과한 것: 새 라우터에서 승인(`test_queued_goal_is_approved_through_a_router_that_reads_root_id`), 활성 목표 id는 옛 라우터에서도 통과(`test_active_goal_root_id_still_works_on_an_old_router`).
- `test_work_parallel_finalization.MainReviewTests.test_parallel_status_advertises_queued_goal_approval`. 수정 전 `capabilities`는 `['question_wait']`였다.

# 수정

`question_wait`와 같은 방식이다. 라우터가 받을 수 있다고 광고한 계약만 모드가 쓴다.
- 라우터: `PARALLEL_CAPABILITIES`에 `execute_root`를 추가했다(`work_parallel_status`의 `capabilities`).
- 모드(`register.js`)
  - 호스트 상태를 읽을 때 광고된 능력 집합을 저장한다(`routerCapabilities`). 재로드 직후에는 비어 있다.
  - `work_execute`의 `root_id`가 활성 목표가 아니면 `routerSupports($, 'execute_root')`로 확인한다. 집합에 없으면 호스트 상태를 한 번 다시 읽는다. 그래도 없거나 읽지 못하면 대화상자도 열지 않고 라우터 호출도 보내지 않는다. 거부 문구는 관리 텍스트 `mod_execute_root_unsupported`다.
  - 메인 지침의 `root_id` 문장을 관리 텍스트 `mod_instructions_execute_root`로 분리했다. 라우터가 `execute_root`를 광고할 때만 덧붙인다.
- 문구는 모두 `work_texts.py`에 두고 `texts.generated.js`를 다시 생성했다.

# 검증

- 새·관련 테스트 8건 통과. Windows 격리 영향 파일 7개(`test_architecture_budget`, `test_architecture_contracts`, `test_side_intake_mod`, `test_work_mod_write_gate`, `test_work_parallel_finalization`, `test_work_queue_dispatch_order`, `test_work_texts`): 635 실행/0 실패.
- `claude plugin validate` 통과(경고는 기존 `ICON_MISSING` 1건), `claude plugin test` 94 통과/0 실패.
- 실엔진 로드(Claude Code 2.1.330, 격리 프로필, 실제 자격 증명 없음): [evidence/real-load-2/result.json](/docs/journal/2026/11/23/fixes/work-queue-restart/evidence/real-load-2/result.json)
  - 15/15 통과, `source_matches_snapshot` true, 스크래치 정리.
  - 해시: `register.js` eb20a3e5…, `side-intake.js` 2c4d1a73…, `texts.generated.js` 1630524d…(현재 워크트리와 같음).
  - `profile_changed`의 `~/.claude.json`은 이 컴퓨터의 라이브 세션이 늘 다시 쓰는 파일이다. 드라이버는 판정에서 뺀다(`profile_unchanged` true).
- 3환경 회귀(`execroot.145415`, 고유 파일 40개, `real_backend` 포함): Windows 격리·py310·py311 각 1428 실행/0 실패(skip 127/129/127).
- CI 상당 py310 unit shard(차례로 실행): shard 1 1781 EXIT 0, shard 3 1833 EXIT 0. shard 2는 1629건 중 `test_work_ctl_parity.SettingsCompareAndSet.test_two_concurrent_pinned_writes_exactly_one_wins` 1건이 실패했다.
  - 트레이스백: 한 스레드의 `settings(self.load(), …)`가 빈 파일을 읽었다(`JSONDecodeError` char 0). 테스트 스레드가 잠금 밖에서 읽는 동안 다른 스레드의 픽스처 `write_file`(`path.write_text`, 잘라낸 뒤 쓰기)이 겹쳤다.
  - 제품 저장(`config_repository.save`)은 임시 파일 뒤 `replace`라서 원자적이다. 이 테스트는 d69ca83부터 있었고 이번 변경과 관계없다.
  - 수정: 기준 설정을 경합 시작(barrier) 전에 읽게 했다. CAS 판정은 그대로 잠금 안의 `load`가 한다. 픽스처를 원자적 교체로 바꾸면 Windows에서 열린 읽기 핸들 때문에 교체가 실패할 수 있어(WinError 5, 2026-11-20 측정) 이 방법을 쓰지 않았다.
  - 확인: Windows 30회, py310 20회 모두 통과. 수정 포함 스냅샷(`cas.151719`)으로 shard 2를 재실행해 1629 EXIT 0.
- CI quality 상당(같은 스냅샷): compileall, ruff 0.15.22, check:docs 통과.
