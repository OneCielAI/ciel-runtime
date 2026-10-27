---
type: Diagnosis
title: ARA stops with queued Work and says nothing
description: Read-only .14 evidence shows the Stop push works when an item is runnable; every later stop had only waiting or blocked items and ended silently. The mod now explains such stops.
tags: [ara, work, continuation, stop-hook]
timestamp: 2026-10-27T02:50:00Z
---
# Request

User, 2026-10-26: ".14 ARA: 큐아이템이 있는데도 멈춤. 할일이 남아있는 상태에서 end_turn이나 complete 훅이면 다음아이템을 푸시하거나 안내해야함. 이것도 수정"

# Access

SYSTEM over SSH port 1004, read only, through the Edward wrapper (`../edward-sse-injection/evidence/scripts/remote_read.py.txt`). Scripts: [evidence/scripts](evidence/scripts/). No remote write, install, restart, queue change or test message. Prompts, item titles, wait texts and tokens were not printed; outputs carry ids, statuses, counts and times only. Times are .14 local (PDT).

# Confirmed

- Identity ([p1](evidence/p1.raw.txt)): sandbox u21, profile aapw-a21u21. Runtime 0.2.51-nightly.20261027-010349.e6514c8 (package mtime 18:51), Claude Code 2.1.311. claude pid 8904 started 18:53:46 with `--continue` and `--plugin-dir ciel-work`; router pid 12416, port 9179, workspace fe924fced90d. The global config has no `work_*` keys: Work on, approval auto, auto continue on (defaults).
- The installed mod `register.js` (sha256 957ccfa0…, 28487 bytes) contains `classic.Stop`.
- The Work plan file cannot be read by SYSTEM: the workspace folder is owner-only ([p2](evidence/p2.raw.txt), [p3](evidence/p3.raw.txt)). Plan state was taken from the router's own `work_*` results in the transcript.
- Transcript `ad4b04e9-…` (one file, about 175 MB). The Stop push fired five times, "Ciel Work has 4 actionable": 16:47:40, 16:53:04, 16:57:39, 17:11:44, 17:41:10. Each time the model selected the named next goal ([p5](evidence/p5.raw.txt), [p6](evidence/p6.raw.txt)). The push path works on this install.
- Router `work_status` JSON continuation fields ([p4](evidence/p4.raw.txt), p5):

| time | version | actionable | waiting shown | blocked steps | unfinished goals in queue |
|---|---|---|---|---|---|
| 18:04:34 | 282 | 0 | 5 | 9 | 10 |
| 18:55:20 | 299 | 0 | 5 | 8 | 11 |
| 18:57:23 | 300 | 0 | 5 | 8 | 11 |
| 18:59:57 | 303 | 4 (new items, done by 19:03:13) | 5 | 8 | 13 |
| 19:12:57 | 316 | 0 | 5 | 8 | 13 |

- Every end_turn from 18:04:48 to 19:28:50 has `stop_hook_summary` hookCount 1, hasOutput false, preventedContinuation false. The hook neither continued nor showed anything, while queued goals such as n1 (22/24), n48 (1/4) and n209 (0/2) stayed above the prompt as in progress. Installed code returns the unchanged result when `continuation.actionable` is 0 and has no notice in that branch.
- The stops at 17:01:24 and 17:04:31 had async subagents launched at 16:59:04 and 17:03:55 ("Async agent launched", [p9](evidence/p9.raw.txt)); the first agent's notification arrived 17:02:40 ([p7](evidence/p7.raw.txt), [p8](evidence/p8.raw.txt)). The hook skips stops with background tasks by design; those stops were silent too.
- No `continued-in` record in this transcript: not the Edward background-handoff pattern.

# Inferred, not verified

- Stops between 18:05 and 18:54 and at 19:03:35 likely had actionable 0 as well. The JSON snapshots around them show 0, and the intervening results changed only items that later appear waiting or blocked; no snapshot exists at those exact stops.
- "waiting 5" is capped by `MAX_WAITING_IN_CONTINUATION`; more waiting items may exist.
- Whether each waiting or blocked mark was justified is the model's decision and is not judged here. Goal n42 shows 5/5 steps done with status blocked, so it stays open with no step to offer.
- That the 17:03:55 agent was still running at 17:04:31 rests on its launch time and a queued notification at 17:07:49 (length only; its id was not parsed).

# Cause

Nothing was runnable by the plan's own state, so not continuing was correct. The defect is the silence: a stop that leaves unfinished Work gave the person no reason, so it looked like a stall with queued items.

# Fix in the repository (not installed on .14)

`ciel_runtime_support/claude_mods/ciel-work/hooks/register.js`, `classic.Stop`:

- A runnable item still produces the same re-prompt with the same same-version bound (two continuations, then pause).
- When Work is on, auto continue is on and unfinished goals remain but the hook does not continue, it writes one dim transcript line (`$.ui.log`, not sent to the model) saying why: waits with their ids, blocked step count, held goals, goals with every step done but still open, plan approval pending, background tasks that will wake the session, the no-progress pause (the toast stays), or an unreadable Work status.
- The same line is not repeated for the conversation until the person writes; automated (meta) prompts do not reset it. A read superseded by a newer successful read is not reported as unreadable.
- Work off, auto continue off, finished Work, child threads and other conversations stay silent. Nothing is retried and no approval or block is bypassed.

# Verification so far

- [mod-check-2](evidence/mod-check-2.txt): Claude Code 2.1.310 `plugin validate --strict` passed; `plugin test` 48 pass, 0 fail, isolated HOME and config.
- [mod-check-old-code](evidence/mod-check-old-code.txt): the three new behavior tests fail against HEAD `register.js` (44 pass, 3 fail, all on the missing notice); the push assertions in the same tests pass on old code.
- [mod-check-without-supersede-guard](evidence/mod-check-without-supersede-guard.txt): the superseded-read test fails without the guard.
- [python-work-tests](evidence/python-work-tests.txt): 89 focused Python Work tests pass with isolated state; no Python change was needed.
- Pending: real Claude CLI end_turn check with screenshots and installed-package check (n303). Installing or restarting on .14 is not authorized.
