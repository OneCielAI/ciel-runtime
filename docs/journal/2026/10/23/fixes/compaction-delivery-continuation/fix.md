---
type: Fix
title: Compaction delivery and task continuation
description: Repair retry starvation and post-compaction admission/confirmation behavior without replaying uncertain inputs.
tags: [compaction, continuation, sse, channel, retry]
timestamp: 2026-10-23
---

# Evidence

REGINA .25 received durable notifications while compaction admission held model
delivery. Five socket submissions315-319 followed identity-grace expiry; another
compaction immediately began and failed upstream. After a later successful
checkpoint+shrink, those submitted IDs became stale_unconfirmed; new320 replied.
Receipt, submission and execution remain distinct. See diagnostic node25-sse.

# Implemented repairs

- Default transient budget sized once from effective caller timeout, with
  finite attempt/backoff allowance. Actual300s open timeout retries successfully
  (2opens, HTTP200); injected explicit deadlines unchanged.
- CielAiRouter internal tool-free summary structured502/503/504/529 failures
  join the existing at-most3 failed-chunk attempts; no nested gateway retry.
  Cancellation,900s deadline, output/terminal exclusions remain.
- Fresh matching auto/manual compact boundaries with valid small postTokens
  can release admission without waiting for another model request. Unknown
  trigger/count, wrongscope, staleboundary and full counts fail closed.
- Recovery-exposed inflight confirmation preserves unknown/missing without
  age-only failure or replay, per-message protection across backlog replacement.
  Actual completion and verified exit/queue removal keep their semantics.
- Automatic compact continuation is visible, normal-input queued, exact-scope,
  one durable checkpoint claim. Requires observed unfinished human work,
  settled tools, known nonplan, valid auto boundary, clean fresh terminal,
  no newer input or native progress. Manual compact never resumes.
- Native summary/attachment bookkeeping is not mistaken for resumed work.
  Human-origin records are accepted as authority. POSIX/Windows operator input
  generation acknowledges submitted work but invalidates later cancel/draft;
  stream-error recovery keeps its older conservative terminal gate.
- Independent compact continuation toggle is reachable through recovery menu.
  No live REGINA/CLI recovery claim is made.

# Verification

- [Final exact-file regression](evidence/final-tests.txt):711 tests, OK with42
  existing retired architecture skips. Whole local test groups were not run.
- [Real300second timeout](../cielairouter-timeout-budget/evidence/long-timeout.txt):
  300.016seconds,2opens,1retry,HTTP200,one statusblock.
- Owned named-pipe/HTTP transport fixture: one auto checkpoint produces two
  real session-socket frames(auth/user), one HTTP request, HTTP200; duplicate
  checkpoint produces no replay. This validates owned transport plumbing, not
  an actual Claude executable's autonomous task execution.
- Five-ID real injector/status/transcript validator tests preserve recovery
  uncertainty and later completion; definitive queue-removal/exit unchanged.
- Actual recovery menu selection persists independent compact toggle.
- Ruff entire repository and diff checks pass.

# Boundaries

No asyncRewake activation, no blind JSON/4 preemptive compaction patch, no live
remote setting/process/history/queue/cursor edits. User explicitly authorized
local/nightly deployment after verification. Runtime-owned tests use temporary
homes/configuration and loopback; full suites belong to CI only.
