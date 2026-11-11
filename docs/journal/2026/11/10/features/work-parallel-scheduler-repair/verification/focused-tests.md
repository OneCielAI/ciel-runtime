---
type: Verification
title: Work parallel scheduler focused verification
description: Source-level verification evidence collected before full groups, owned runtime proof and beta deployment.
tags: [work, parallel, tests, evidence]
timestamp: 2026-11-10T00:00:00Z
---

# Focused tests

Isolated environment (`CIEL_RUNTIME_*` removed, temporary `CIEL_RUNTIME_CONFIG_DIR`):

- Parallel ledger, coordination sidecar, MCP lifecycle, native evidence, workspace salvage/rebind, plan tree, MCP tools, host pages, HTTP controller and Claude mod harness: **352 passed, 144 subtests passed**.
- Python changed modules compile successfully.
- `git diff --check` reports no whitespace errors.

# Covered behavior

- Every priority fills independently; candidate focus order is preserved.
- Multiple reviewers may run; accepting a review retires its reviewer lease.
- Questions/completion/handoff release slots atomically.
- Unrelated board changes preserve the assigned root generation; assigned scope changes invalidate it.
- Missing fork exit reports resume the fork instead of stopping it; salvage is persisted before terminal stop.
- Session-start recovery sends directly to the fork and does not enter main prompt context.
- Fill exceeds the previous 20-start ceiling and retains a trigger-during-fill latch.
- Terminal history does not consume the open-lease admission bound.
- Status is bounded and omits private terminal workspace/evidence history.
- Legacy unbound reservation repair and owner-identity-unknown behavior are covered.
- Workspace handoff keeps dirty partial work and prevents the prior generation from mutating or deleting the replacement workspace.
- Host page pruning removes expired orphan `.io.lock` sidecars.

# Isolated repository groups

All inherited `CIEL_RUNTIME_*` variables were removed and every group used a new temporary `CIEL_RUNTIME_CONFIG_DIR`:

- unit: **3,641 passed, 52 skipped**
- router: **1,552 passed**
- channel: **607 passed, 80 skipped**
- runtime: **458 passed, 19 skipped**

All four groups exited 0. Logs: `.local/groups-parallel-scheduler-repair/{unit,router,channel,runtime}.txt` (local evidence; release journal will retain the final summarized output).

# Compatibility check

Both live old validators (`4eebfdc`, `5c91ea8`) accepted new serialized lease output.

# Owned source scheduler proof

`evidence/scheduler-run-source-6/result.json` passed every acceptance gate:

- host started four native forks while main was idle;
- four distinct agent IDs occupied P1=2, P2=1, P3=1;
- marker windows overlapped for 46.4 seconds;
- after the short P1 worker released its slot, a new P1 worker started automatically under a new lease/agent;
- 24 seeded terminal-history rows did not block admission;
- fork-only canaries remained out of model-facing main conversation rows;
- desktop and 390px Parallel screenshots both showed the four confirmed cards.

# Owned restart proof

`evidence/restart-run-source-7/result.json` recorded the decisive restart evidence before its screenshot-only probe tail failed:

- the probe terminated only its owned parent/router tree;
- `--continue` resumed the same Work session;
- the same lease, generation and native agent ID were retained;
- the exact fork transcript contained the pre-restart canary and the fork wrote its post-restart marker;
- model-facing main rows contained neither canary nor the host recovery instruction.

The run's acceptance booleans for restart correctness were all true. Desktop/mobile capture was already proven in `scheduler-run-source-6`; the source-7 runner itself exited nonzero only because its detached resumed console could not be attached for duplicate screenshots.

# Beta release and local install

- CI and npm beta workflow passed at commit `8e08b97bf29878a9cf7302c5e945c0307675aa06`.
- Beta `0.2.51-beta.20261111-055322.8e08b97` was verified against registry gitHead and SHA-512 integrity; 521 packaged source files matched the commit.
- `latest` remained `0.2.51`; `nightly` remained `0.2.51-nightly.20261106-020333.c00c8b6`.
- The immutable snapshot contains 575 files; the global exact-archive install matched all 575 files.
- User `CIEL_RUNTIME_HOME` now points future launches to the `8e08b97…` snapshot. No live process was restarted.

# Installed-build scheduler repetition

Both runs used the immutable installed snapshot `ciel-runtime-8e08b97…` with a new temporary config, and all inherited `CIEL_RUNTIME_*` variables were removed.

**Attempt 1 failed.** Evidence is in `evidence/scheduler-run-installed-beta-attempt1/`.

- The forks were first seen at 12.1 s, 40.9 s, 68.2 s and 127.4 s. The 66-second P1 marker ended before the fourth fork started, so the initial four never overlapped.
- Eight gates failed. The automatic-refill and main-isolation gates passed.
- Every measured hop was slower than in source-6. See `verification/installed-run-latency.json`:

  | Hop | Source-6 median | Attempt 1 median |
  |---|---|---|
  | `work_parallel_event` | 0.8 s | 4.3 s |
  | `work_parallel_status` | 0.6 s | 4.5 s |
  | `Agent` tool | 1.3 s | 14.5 s |
  | Unrelated external `orca-agent-hook` | 0.30 s | 1.96 s |

- The root cause of the slowdown was not identified.
- After the run, six probe-owned resumed restart consoles were found still alive. They came from `owned-restart-20261110-225953…232817` and used temporary `%TEMP%/ciel-owned-restart-*` configs. Only those trees were stopped, and the live PIDs 12600, 90872 and 102668 were unchanged. Five of the six were already running during source-6, so they are not a proven cause.
- `owned_restart_probe.py` now also stops every process whose command line names its own run directory or scratch directory.

**Attempt 2 passed.** Evidence is in `evidence/scheduler-run-installed-beta-2/`.

- Every acceptance gate was true.
- Four distinct native forks ran at P1=2 / P2=1 / P3=1, with an initial marker overlap of 23.3 s.
- After P1 released its slot, a new P1 lease and agent (`a5741037…`) started automatically.
- Main-model rows contained no fork canary.
- The terminal, the desktop web view and the 390 px web view all show the four confirmed forks.
- Median latencies: event 1.9 s, status 0.8 s, Agent 3.7 s, external hook 0.32 s.
- No probe process was left afterwards.

**Observed limitation, not changed in this release.** The host pump starts forks one at a time. Each fork needs status, reserve, the spawn hook, the `Agent` call and bind, in order. Initial fill time therefore grows with per-call latency: about 6 s per fork in source-6, about 20 s in attempt 2 and about 30 s in attempt 1.

# Related concepts

- [Scheduler lifecycle](/architecture/scheduler-lifecycle.md)
- [Mixed-build compatibility](/compatibility/mixed-build-ledger.md)
