---
type: feature-record
title: Idle gate re-check of blocked and waiting Work items by the side actor
description: While main's turn has ended and no Work fork runs, the side actor re-checks every blocked or waiting Work item every N minutes (default 5) and tells main when a gate has cleared; N is managed in the menu, web dashboard, REST and ctl.
timestamp: 2026-11-15T22:10:00Z
tags: [work, side-intake, gate, timer, settings]
---

# Requirement (user, 2026-11-15)

> 메인세션이 턴이 종료했고..포크된세션되 없는 조건이되면,혹시 워크아이템에 블러커가 표시되어있거나 기다리는 조건이있는 아이템들이있으면 싸이드 액터가 5분에 한번씩. 게이트 차단조건이 해제되었는지 확인해서 메인에게 알려줘야하고 여기서 5분은 환경설정 및 웹 대시보드에서 관리가 될수있어야해

Broken down:
- **Condition:** main's turn has ended and no forked session is running.
- **Targets:** Work items marked blocked, or with a recorded wait condition.
- **Action:** the side actor checks once every 5 minutes whether each gate has cleared and tells main.
- **Setting:** the 5 minutes is managed in the settings and in the web dashboard.

# Design

- **Setting `work_gate_check_minutes`** (`ciel_runtime_support/work_gate_check.py`):
  - Default 5; `0`/`off` turns it off; the range is 1-1440.
  - It is stored as `config["work_gate_check"]["minutes"]`, which is per workspace. The default removes the key.
  - It is a row on the shared settings path (`work_live_settings`), so it appears in the prelaunch menu (Work · SSE settings), the web Settings tab, REST `settings`/`settings-update`, MCP and `ciel-runtimectl work`.
  - Menu presets are 0/1/2/5/10/15/30/60 plus Edit, with 4-language descriptions.
- **Gates (`gates(doc)`):**
  - every unfinished node with `waiting_on` or status `blocked` whose goal is not done;
  - the active goal comes first, capped at 24.
- **Timer (ciel-work `register.js`, `scheduleGateCheck`):**
  - Interactive sessions only, while Work is on.
  - Started at main's turn end and at session start; stopped at main's turn start, session end and Work off.
  - Every 60 s it lists native agents. Only when no ciel-work Work fork is pending or running (side-intake forks do not count) and main is not working does it call `work_side_gate_check {idle: true, forks_running: 0}`.
  - The tool is host-only: it is in `HOST_ONLY_SIDE_TOOLS` and the router requires the host token.
- **Router (`work_side_gate_check` in `work_mcp_tools.py`):**
  - Refuses a fork host. Answers `off`, `side_intake_off`, `not_idle`, `no_gates` or `not_due`.
  - Otherwise, under a file lock, `due()` decides:
    - a changed gate set is due at once;
    - an unchanged one is due when the interval has passed.
  - A due check is queued as one durable side event, with key `gate-check:<gate digest>:<slot>:<text digest>` and route `{source_kind: gate_check}`. The last check is recorded in `sessions/<id>/gate-check.json`.
- **Decision by the side fork** (the normal side contract; the event text lists every gate):
  - **Wait proven satisfied:** `update` + `release_waits`. The host clears the wait and tells main, or the waiting worker, at once.
  - **Blocker only main can lift:** `advise`, which reaches main as a handoff.
  - **Nothing changed or unverifiable:** `answer` with kind status. Nobody is woken.
- **No person behind it:**
  - the event has no source reply (`not_applicable`);
  - it writes no side review row for main (`apply(..., gate_check=True)`);
  - the prompt tells the side fork to keep the reply to one line.
- **Work mode off:** the tool is not in the Work-off allow-list, so the router refuses it.

# Verification

## Unit, MCP and harness

- **`tests/test_work_gate_check.py`** (13 tests; the relay ones are recorded in [live-item relay](/docs/journal/2026/11/15/features/live-item-relay/record.md)):
  - interval default, off and bounds;
  - gates: waits and blocks, active goal first, finished goal excluded;
  - due now, after the interval, or on a change only.
  - **MCP handler:**
    - idle/fork/side-off/off gating, and a fork host is refused;
    - one durable `gate-check:` receipt with no source reply;
    - `not_due` on the next tick;
    - claim → update + `release_waits` clears the wait, writes no review row, the reply is `not_applicable` and the main notice is claimed once;
    - a "no change" answer wakes nobody.
  - **Settings row:** set through `settings()` and through MCP `work_live_settings`.
- **`ciel-work/tests/work.test.ts`:** new test "idle gate re-check asks once a minute only while main is idle and no Work fork runs". Its conditions:
  - nothing before 60 s, then one call with `idle/forks_running/session_id`;
  - a main turn stops it, and a child turn end does not restart it;
  - a running Work fork holds it, and a side-intake fork does not;
  - session end stops it, and `-p` never starts it.

  The gate call is answered as a host-phase reply, outside the board-read count, so the existing idle-refresh call counts hold.
- **`claude plugin test`** on ciel-work: 103/103.
  - The first run after the timer was added had 4 failures. All four were call-count assertions that counted the new router call as a board read.
  - `claude plugin validate` passes for ciel-work and ciel-work-gate. ciel-work-gate has no `*.test.ts`; its harness is `tests/work_fork_gate_harness.mjs`.

## Four surfaces (owned, isolated config)

`e2e-source/gate_check_surfaces.py` → `evidence/surfaces-result.json`, 16/16 PASS (`evidence/console.txt`):

| Surface | Checks |
| --- | --- |
| REST | row 5/number with the Korean label "게이트 재확인 주기 (분, 0 = 끔)"; set 10; 2000 refused (422) and 10 kept |
| ctl | `work_gate_check_minutes = 10 [next_idle_check]`; set 15 |
| Menu | presets `0,1,2,5,10,15,30,60,Edit`; range 0-1440; Korean description |
| Web (system Chrome) | shows 15, and Apply 5 stores the default (key removed); `web-desktop-1-row.png`, `web-desktop-2-applied.png`; 390 px row without horizontal scroll, `web-390-1-row.png` |
| MCP | the router call the timer makes: `not_idle` with a fork running; queued one event for the waiting item; the second tick is `not_due`; event text in `gate-check-event.txt` |

## Owned real claude.exe E2E

- **Setup:** `e2e-source/run_gate_relay_e2e.py`, with:
  - Claude Code 2.1.325;
  - the packaged ciel-work and ciel-work-gate;
  - the real Work/side MCP tools on an owned backend;
  - a scripted Messages stand-in;
  - interval 1 min.
- **Guard:** `p0_common.Guard` with `protected-pids.json`, which records live pids at start. It killed 1 owned tree, left 0 owned survivors and had 0 overlap with protected pids. The host token appears 0 times in the evidence.
- **Run 2, `evidence/run-20261115T212007Z/result.json`:** 13/13 checks PASS.
  1. The main turn ended. 60.7 s later the side fork received the gate event. The timer's router call came at 65.7 s from launch, `queued: true, gates: [n3]`.
  2. The board wait on n3 was cleared (`waiting_on` = "").
  3. Main was woken by `Ciel side wake …: a host message beginning "Ciel side released recorded wait"`. That request carried the release notice.
  4. The gate receipt wrote no side review row; only the relay message's row exists.
  5. **Relay part:** the side fork answered update + relay. The router committed `relay: {item_id: n2}`. The relay wake prompt named "Ciel side relay for Work item" with "decide whether it matters to your current work", and main's request carried the original message and the ask. The web reply went out once.
- **Run 1, `evidence/run-20261115T211555Z`:** all 11 checks it had passed. Its console showed the relay wake prompt naming the released-wait notice. That defect was fixed, and run 2 added the two wake-name checks.
- **Recorded but not explained:** each run has a fourth main request after the relay wake. It carries no wake prompt and contains both notices. Its origin was not identified from this evidence.

# Behaviour notes

- A running Work fork holds the check, as the requirement says ("포크된 세션이 없는 조건").
- Timing:
  - With no earlier record, the first check runs at the first tick, 60 s after main's turn ends (measured 60.7 s).
  - Later checks run at the first one-minute tick after each interval, or at once when the gate set changes.
