---
type: operations
title: Live monitoring of Andy, Celly and KOBE after the native idle review fix
description: Read-only periodic checks of the three Work hosts; what each pass found and which findings are product defects.
timestamp: 2026-11-20T11:25:00Z
tags: [work, parallel, monitoring, review, native-observation]
---

# Scope

The user asked for periodic monitoring of Andy, Celly and KOBE after the c955630/92ef95b updates, checking for errors and improvements.

Each pass is read-only:
- Andy and Celly: state files, router log tail and main transcript tail on this machine.
- KOBE: the same over `ssh -p 1004 ara-ai@100.64.0.14`. The script is copied to `C:\Windows\Temp\monitor_host.py`, because the inline form exceeds the Windows OpenSSH command-line limit. It reads only.
- Tool: `.local/tmp/monitor/run.sh` and `monitor_host.py`. It reports a host's main scope from its router's `compaction-scope.json`, open lease counts, findings, failure-journal events, error-shaped router log lines and main transcript tool errors since the last pass.

Versions at the first pass (11:21Z):
- Andy router: c955630.
- Celly router: 92ef95b (restarted 11:14Z).
- KOBE install: c955630 (session started 10:21Z).

# Findings

## Defect: a submitted fork that leaves the native list without an idle row never gets its review (confirmed on Celly and KOBE)

Celly n1070 (lease 78d4b4ba):
- 10:42:48Z completion receipt.
- The native list showed the fork `running` until 10:40:44Z, then it was absent from 10:47:14Z. There was no idle row in between.
- No review row was written. Main's approve at 10:57:18Z was refused with `positive matching idle workspace proof required`. Its SendMessage was refused as a revival (10:58:33Z).
- After Celly's restart (11:14Z), `restart_submitted_terminal` published the review, and main approved it to finalized.
- n1135 (lease 4ac18b2a) followed the same path: receipt 10:38:08Z, absent 10:39:05Z, finalized after the restart.

KOBE n878 (lease a78fac3f):
- The run's `native_status` became `failed` at 11:01:34Z, the completion receipt landed at 11:09:00Z, and the native record shows `idle` at 11:09:48Z, absent from 11:10:20Z.
- No review row exists at 11:21Z.

Cause, confirmed in source (c955630):
- `parallelRestartSubmitted` skips every fork in `processNativeAgents`. A fork this process spawned or listed is never observed terminal, even when it is absent from a complete list and its run holds the exact completion receipt. Only a later process can publish it.
- The turn-end reconcile only runs for a fork listed at that boundary. An absent row reconciles `unknown`.
- The c955630 native-idle publication runs only when `_observe_coordination_row` succeeds. `update_native` refuses `failed` → `idle` (`terminal native status is immutable`). So KOBE's idle row was skipped, and the review was never published.

## Residuals, known

- Andy: 34 leases are `orphaned` (startup sweep) while their coordination runs stay `active` or `waiting`. Celly has 25 such leases and KOBE 0. The sweep releases the ledger lease and does not close a bound run.
- Andy n1856: review `deciding`, blocked `stage_failed: unknown Work item`. Already open in [celly-stuck-recoveries](/docs/journal/2026/11/19/fixes/celly-stuck-recoveries/record.md).
- Celly n1128 (lease e0aeb12b): its run was bound at 11:12:51Z, and the lease was orphaned by the startup sweep at Celly's 11:14Z relaunch. The run is still `bound`.

## Observed, not yet investigated

- Celly: `router_post_uncaught ... ChannelFileLockBusy` on a side-inbox lock (3 times, 10:44–10:46Z).
- Celly: `channel_stdin_proxy_blocked ... prior_submission_failed` (11:18:39Z).
- Celly: "Work gate check (host round): pump failed" (11:06:32Z).
- KOBE main: "auto continue could not read the Work status" (10:57:03Z).
- KOBE main: a `worker token` tool error (10:51:06Z).
- Volume, Celly router log tail (05:13–05:21 local): `work_host_page` calls outnumber every other tool. Andy's host status projection was 298 KB (38 pages), and `coordination.runs` (132 rows, 138 KB) is about half of it.
