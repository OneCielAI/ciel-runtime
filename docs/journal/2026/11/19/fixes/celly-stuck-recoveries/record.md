---
type: fix
title: Stuck stranded recoveries and reworks roll forward after their reservation was swept
description: Celly n1069 n1070 n1074 n1097 held four P1 slots in deciding and n1108 stayed stuck; every host pump raised KeyError. Also main's work_side_status call, the fork denial after a Work mode toggle, and the side wake for after-turn notices.
timestamp: 2026-11-20T03:20:00Z
tags: [work, parallel, p0, stranded, rework, side]
---

# Report

Celly's main session reported four problems on 2026-11-20 (UTC):

1. n1069, n1070, n1074 and n1097 stayed `deciding` with `stage_failed: multiple original P0 intents for one lease`. Their four replacement reservations stayed `reserved`, `slot: true`, and P1 showed 4 of 2 active.
2. n1108 stayed `deciding` with `content baseline entry bound exceeded: 20001/20000`.
3. `Parallel read failed: transition` and `P0 reacquire parent intent invalid` (fixed earlier in db23560; not changed here).
4. Side gate `invalid_answer` repeats, `work_side_status` answered `conversation identity is unavailable`, and forks were denied after `work_mode_set(false)` then `(true)` in one session.

# Evidence

Celly's state files were copied read-only at 02:28Z. Its router log was tailed.

- Every host pump after the 02:16Z relaunch raised an uncaught router error.
  - Stranded rows: `KeyError: 'workspace'` at `_stranded_roll_forward_stages`.
  - n1108's rework: `KeyError: '<replacement lease>'` at `_roll_forward_stages`.
- The P0 file has two `stranded_recovery` intents per predecessor.
  - A `cancelled` intent carries the replacement lease id.
  - An `intent` row carries no lease.
  - The four leases were released by the launch orphan sweep at 20:10:15 local (`work_orphan_released ... state=reserved owner=dead`) and later reserved again under the same deterministic id.
- n1097's goal n1006 is P0 and in `planning` (coordination root generation 6). Its replacement run was refused as `stale root approval`.
  - The initial P0 launch passes its pinned intent to `record_assignment`.
  - Replacement runs passed none.
- In the 02:10Z session the model had turned Work mode off itself at 02:03:21Z (`work_mode_set {"enabled": false}`), then on again at 02:14:23Z. That session was launched with the Work tools absent. The four fork spawns were denied at 02:15:08Z with `no connected MCP tool "work_parallel_status"`. The model then called `restart_session`.
  - The child exit with code 1 at 20:15:36 local is that restart, not a separate crash.
- The repeated `invalid_answer` gate rounds were native forks that answered main's last user question instead of returning the decision JSON. Source: the transcript of side actor 3a7c2f65cdea.
  - This change does not alter the fork mode.
  - `fresh` session mode is the user's chosen test for it.

# Change

- `work_parallel_p0.validate`: a `cancelled` intent no longer counts toward "one original P0 intent per lease". It never launched, and a deterministic replacement lease reuses its id. Two live intents on one lease are still refused.
- Stranded roll-forward:
  - Re-runs reservation for a replacement lease that was released, or that is reserved but has no adopted P0 intent.
  - Adopts the intent for an existing reservation instead of reserving twice.
  - Re-attaches a missing workspace before recording coordination.
  - A missing workspace is now a bounded `stage_failed`, not an uncaught KeyError.
- Rework roll-forward: the same recovery. That covers a replacement released before its coordination run, an unadopted P0 intent and a missing workspace.
- `_replacement_p0_authorization`: rework and stranded replacement runs carry their adopted P0 intent to `record_assignment`, exactly as the initial P0 launch does.
- Mod changes (register.js and side-intake.js):
  - `work_side_status` is host-only, so main is refused before the engine with the host-only wording.
  - A fork spawn whose Work tools are not connected now says to restart the session. It used to pass on the raw MCP error.
  - An after-turn side notice gets its own wake text instead of "released recorded wait".

# Verification

- Celly copy, before and after ([evidence/celly-copy-before-after.txt](/docs/journal/2026/11/19/fixes/celly-stuck-recoveries/evidence/celly-copy-before-after.txt)).
  - db23560: all five raise the same KeyError the router logged.
  - Fixed tree: all five reach `rework_ready` with the P0 intent `reserved` and the workspace attached.
  - Copy-only stubs: a throwaway P0 key, the owner proof, and worktree prepare/rebind. Real worktree creation was not exercised.
- New tests fail on db23560 and pass on the fixed tree ([evidence/new-tests-fail-first.txt](/docs/journal/2026/11/19/fixes/celly-stuck-recoveries/evidence/new-tests-fail-first.txt)).
- Andy copy:
  - n1174 (stranded) reaches `rework_ready` on both trees with stubbed prepare. Its live block is the retained path, which is not fixed here.
  - n1856 (rework) moves from KeyError to `stage_failed: unknown Work item`, because the item was removed from the board. That case is an open defect.

- Affected Windows bundle (p0_events, rework_worktree, content_baseline, finalization, side_intake_mod, work_mod_write_gate): 451 tests.
  - First run: 4 failures, all in side fork proof tests.
  - The same 4 fail on db23560. That commit changed the deny text to `not the side agent type` when adding fresh mode and left two assertions on the old `not subagent_type fork`.
  - The assertions now name the new text. A general-purpose spawn is still denied.
  - Rerun: 451 pass.
- `claude plugin validate` passes for ciel-work and ciel-work-gate. `claude plugin test` on ciel-work: 89 pass, 0 fail. ruff: clean.

# Installed (not yet effective)

- Local: `CIEL_RUNTIME_HOME` points at a copy of the db23560 home with the four changed files.
- Celly: elevated overlay with per-file SHA-256 checks and a backup.
- KOBE: scp package, hash-checked overlay with a backup.
- No process was restarted. A running router keeps the old modules: Celly's log still shows old-code KeyErrors from 21:05 to 21:08 local, after the overlay. The fixes take effect only after each session restarts, and that is the user's decision.

# Side session setting

`work_side_intake.session=fresh` was earlier written to the root `config.json`. The router reads settings from `workspaces/<digest>/config.json`, so that write had no effect: Celly REST still read `fork`.

- Celly: set through its router (`POST /ca/work/api/v1/commands/settings-update`) and read back as `fresh`.
- Andy and local: the workspace files were backed up and set to `fresh`.

# Follow-up: stale pane rows in the review reminder (Andy)

## Report

A peer reported "포크 결과 20건이 메인의 검수를 기다립니다(n2154, n688, n702 …)" repeating, although all 20 were finalized (finalization version 1108) and review.version was 1126 with no pinned pending row.

## Cause

Confirmed in source:

- `remindPendingReviews` merges the projection rows with `observatoryParallel`, the pane's snapshot.
- The pane snapshot is refreshed only when the pane opens or is refreshed (`observatoryRefresh`).
- 8641941 added the pane rows (Celly: five results waited, the projection list was empty). From then on, a pane opened before main's decisions kept every decided row in the reminder.

## Change

The pane's rows are used only when the pane's review version is not older than the projection's. With equal versions the pane-only case still reminds.

## Verification

- New scenario `review-reminder-ignores-a-stale-pane-snapshot`: pane opened with L1 pending at review version 4, L1 then decided (version 9), 21 minutes, Stop. The reminder names L1 once on ad6eb74 and zero times after the change.
- The pane-only and first-wake reminder tests still pass.

# Observation, not fixed: n2471 lease stays running after an API block (Andy)

- Fork ac0700a845c395a75 (P0 n2471) answered "API Error: This request was blocked by our safety systems" at 03:17:02Z.
- The host's first exit-contract message reached it at 03:26:25Z and was blocked again at 03:26:56Z. Native status failed at 03:27:24Z.
- At 03:35Z the lease was still `running` with `slot: true`. The coordination run is `terminal` with `exit: null`, and the P0 intent is `bound`.
- The router logged main turn ends at 21:27 and 21:32 local. No second exit-contract message appears in the fork transcript.
- Per the code, the exit contract salvages the lease only at a turn end that sees the fork terminal in `$.agent.list`. A fork absent from that list goes to `reconcile unknown`, which keeps the slot.
- Whether that is what happened here is not confirmed. The mod's in-memory failure list was not read.

# Open

- n1856: a decided rework whose item was removed from the board has no closing path.
- n1174: the retained path blocks workspace preparation.
- Andy n1953 `owner_failure`, 22 submitted results left in validating, and n2040's 8-minute delay between receipt and review: not investigated.
- No live session has run these fixes yet.
