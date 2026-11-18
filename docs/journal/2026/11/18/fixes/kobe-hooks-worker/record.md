---
type: fix
title: KOBE hooks worker — light session.start, fewer paged router reads, gate check while main is busy
description: ciel-work's session.start held the hooks worker for 172 s on KOBE (about 360 router calls, mostly work_host_page) and Claude Code 2.1.327 unloaded the mod three times; the gate check never ran while main was busy; oversized-thread lines flooded main; a gateway model was refused without saying it was filtered. session.start now returns at once and its phase runs after it, the host reads the ledger version alone for events, the gate check runs through main's turn (P0 → P1 → rest), the bounded-thread fact moved to the receipt, and the catalog refusal names the missing capability. Whether this ends the unloads is not confirmed; the main session re-measures on KOBE.
timestamp: 2026-11-18T21:00:00Z
tags: [work, ciel-work, register.js, side-intake, hooks-worker, gate-check, kobe, cielairouter, hotfix]
---

# Problem (lead brief, measured on KOBE and this PC, 2026-11-18)

- `ciel-work@inline session.start settled in 172273.9ms`, with about 360 router MCP calls (mostly `work_host_page`, plus `work_parallel_status`, `work_parallel_event`, side claim/ack).
- `/work-mode view` answered after 37977.7 ms; single `tool.call` hooks took 0.7–18 s.
- `ciel-work was unloaded: it crashed the hooks worker`, three times, each about 3 minutes after start. The exact trigger is **not confirmed** 🟡.
- The gate check never ran: `scheduleGateCheck` returned while `working`, `turn.start` stopped it, `session.start` armed it last, and the router answered `not_idle` unless `idle: true`.
- `side-intake.js` logged `source thread too large … N older replies left out` with a plain `$.ui.log` for every event.
- Launch refused `_Walkie_Sol61-Opus55-Fast` as "not in the provider catalog" although the gateway lists it (`tool_calling`, `reasoning`, no `thinking`).

# What the code did (read, then measured)

- `session.start` awaited `refresh`, `startSide`, `parallelHydrate`, `parallelRecoverWorkers` and `parallelPump` before returning ✅ (source).
- Every `parallelEvent` read the whole host status (`work_parallel_status`, paged into `work_host_page` calls) only to take its `version` ✅ (source). In `work_parallel_mcp.run`, 22 event branches never read `expected_version` at all ✅ (source: every branch before `rework_dispatch`).
- The pump read the whole host status at the top of each pass and again after the P0 lane ✅ (source).
- Measured on the real-backend harness with router paging (`start-load`, 6 reviewed leases, 292-node board) ✅ — [evidence/measure](evidence/measure):

| HEAD 720eeb6 | value |
|---|---|
| session.start hook held | 5664.6 ms |
| router calls before the hook returned | 201 (148 `work_host_page`, 24 full `work_parallel_status`, 22 events) |
| `/work-mode view` held | 202.3 ms, 23 router calls before it returned |

# Fix

## A. Gate check ✅

- `register.js`: armed first thing in `session.start` (after identity), not stopped by `turn.start`, reports `idle: !working`; one round at a time (the next tick is armed after this one settled; the router holds a new round while the last one's events are open). The side re-check runs from the first tick; the host's own round (re-pump, diagnosis, notice to main) waits until this start's hydrate and recovery ran (`hostPhaseReady`), so it never pumps an unhydrated host.
- Router (`work_mcp_tools.py`, `work_gate_check.py`): no `not_idle` refusal; `main_idle` reported; gates ordered by the goal's effective priority (P0 from the P0 marker, `effective_priority_for`), then P1, then the rest; each gate line carries `[P0]`/`[P1]`. Event text and tool description say "also while main is busy" and why (lost approvals).
- Setting `work_gate_check_minutes` text updated in four languages. Surfaces re-checked with the 2026-11-15 script: menu, web (desktop and 390 px), REST and ctl all PASS ✅ ([evidence/tests/surfaces-after.txt](evidence/tests/surfaces-after.txt)). Its four last facts fail identically on HEAD ([surfaces-before.txt](evidence/tests/surfaces-before.txt)); they assert the pre-Stage-3 "a running Work fork holds the check" rule retired on 2026-11-17.

## B. Light start path and worker relief ✅ (measured), effect on the unloads 🟡 (unverified)

- `session.start` registers both commands, resets module state, arms the gate check and returns. `sessionStartHost` runs refresh → side start → busy poll → hydrate → recovery → pump after it, stopping when a newer start, a session end or a silent conversation change took over.
- `parallelEvent`: no status read for the 22 self-versioned events; the others read `work_parallel_status` with `projection: "version"` (router returns `{"version": n}`; an older router returns the full status, which carries the same version). The version read carries the process launch nonce like every host status read (R2 B2).
- `hostStatus` reads are stamped with a write counter (every router call except the two status reads counts as a possible write). The start pump reuses hydrate's read and the post-P0 re-read is skipped when nothing was written since. Gate presence is reported before hydrate's status read so that read stays current.
- `turn.complete` runs its pump after the hook returns; `/work-mode view` opens the pane after its board reads and lets the parallel, settings and side projections redraw it when they land.

After, same fixture ✅:

| after | value |
|---|---|
| session.start hook held | 0.5 ms, 0 router calls before it returned |
| whole start phase | 61 router calls (22 `work_host_page`, 3 full + 7 version-only status reads, 22 events) |
| `/work-mode view` held | 76.2 ms, 6 router calls before it returned |

## C. Oversized-thread log ✅

- Each event's line goes to `$.ui.log(text, { to: 'debug' })`; one person-visible line per source thread (identity + conversation + root) per session.
- The fact is recorded on the receipt: `work_side_renew` with `source_bounded: {omitted_replies}` (never shortens the claim lease); `side_inbox_receipts` carry it; the side receipt pane, the terminal SSE inspector and the plain SSE pane show "Source thread bounded: N older replies left out".
- Harness, three P1 events of one oversized thread: HEAD 3 transcript lines and 0 receipt records; after 3 debug lines, 1 transcript line, 3 receipt records.

## D. Filtered-model error text ✅

- `select_agent_models` keeps the same rows (refactored onto `agent_model_exclusion`); `excluded_agent_models` names why each listed row was left out; `select_model_catalog_entries` returns it as `excluded_models`; the model list cache metadata keeps it; `ModelRegistryApi.read_exclusions` reads it back; `ensure_selected` says: `… is not in the provider catalog: the gateway lists it, but ciel-runtime filtered it out (missing capability thinking). The gateway owns its capability flags …`. A model the gateway does not list keeps the old text.
- `ciel_runtime.py` stays at 4980 lines (the port is passed on an existing line).

# Verification

| check | result |
|---|---|
| Fail-before on HEAD product code with the new tests | Python: 15 new/changed tests fail (11 F, 4 E) — [fail-before-python.txt](evidence/tests/fail-before-python.txt); mod suite: 2 fail — [fail-before-plugin-test.txt](evidence/tests/fail-before-plugin-test.txt) |
| `claude plugin test` ciel-work | HEAD 104/104 → after 105/105 ✅ |
| `python -m ruff check . --extend-exclude .local` | All checks passed ✅ |
| touched and mod-referencing files (19 files, 592 tests) | first pass found 28 write-gate harness failures + 1 observatory harness error (observation timing, fixed in the harnesses); then `test_work_mod_write_gate.py` 146/146 (HEAD 146), `test_work_observatory.py` 9/9 ✅ |
| real-backend `test_work_mod_real_backend.py` | first full run 117 with 2 F (both fixed, see below); the fixed tests and `start-load` pass; whole file passes again inside the Windows unit group ✅ |
| Windows groups on a HEAD export + changed files | unit 5091 OK (146 skipped), router 1676 OK, channel 626 OK, runtime 460 OK ✅ — [groups-after.txt](evidence/tests/groups-after.txt) |
| Linux python:3.10-slim, unit shards 1–3 | 1498 / 1671 / 1805 OK ✅ |
| real claude.exe 2.1.327 load | PASS ✅ — [evidence/real-load/result.json](evidence/real-load/result.json) |

Real load (owned scratch under D:/Temp, stub Messages, owned MCP backend running the real Work tools; packaged ciel-work and ciel-work-gate from the snapshot, digests match the tree) ✅:
- Final bytes (second run): `hooks module ciel-work@inline session.start settled in 69.8ms` (first run 101.7 ms; KOBE: 172273.9 ms).
- `ciel-work (user) answered command.run without next() in 67.7ms` for `/work-mode view` (first run 73.6 ms; KOBE: 37977.7 ms).
- Main turn held by the stand-in from +9.3 s to +109.3 s; at +61.1 s the router got `work_side_gate_check` with `idle: false` and queued the round; the side fork received the event at +61.4 s with the P1 gate listed first; `Work gate check (host round)` ran in the same tick ([debug lines](evidence/real-load/debug-ciel-work-lines.txt)).
- No `failed to load`, no unload line, in both runs. Live `~/.claude/settings.json`, `~/.claude.json` and the installed ciel-runtime bin hashes unchanged. No process referencing the scratch folder remained; the driver's own removal failed both times (a file still locked right after the kill) and the folder was removed afterwards, 0 left.
- Not exercised: the ciel-runtime launcher path itself (router process, plugin install) — the engine was started directly with `--plugin-dir`, as the 2026-11-15 gate E2E did.

# Defects the suites caught during the fix

- `test_host_attach_and_only_own_or_gone_leases_are_adopted_or_recovered`: the first version-only read did not carry the launch nonce (10 status calls, 6 with nonce). Fixed: `hostVersion` passes `hostProcess`.
- `test_router_connect_after_the_retry_window_recovers_and_wakes_once`: with the gate timer armed first, its host round woke main before the X2 retry ran the host phase. Fixed: the host round waits for `hostPhaseReady`.
- Harnesses that observed right after `session.start`, `turn.complete` or the view command now wait for that work to land (`work_mod_harness.mjs` settles on router quiet, the observatory harness and the mod suite use a settle). No assertion was removed; the two assertions that encoded the old gate contract ("a main turn stops it", router `not_idle`) were replaced by assertions of the new contract.

# Not confirmed / left open

- 🟡 Whether the KOBE unloads came from the start-phase hold. The engine text "what held the hooks worker is not known" stays unexplained; the main session re-measures on KOBE.
- 🟡 Single `tool.call` hooks of 0.7–18 s were not changed beyond the reduced router load; their own chains were not restructured.
- The 18–20 s MCP round trips on this PC were not measured here.
