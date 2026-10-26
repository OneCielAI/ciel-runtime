---
type: Fix
title: Compaction admission gates that starved external input
description: A manual /compact and an unproven launch identity both held external channel delivery until a routed request, which an idle session never sends.
tags: [compaction, admission, channel, sse, regina, celly]
timestamp: 2026-10-23
---

# Symptoms

- Regina (agent-aap), 2026-10-23: after a manual `/compact` at 05:37:40 UTC the
  turn never resumed; external messages 254-257 (06:06-06:36) never entered the
  conversation. A relaunch at 06:53 fast-forwarded the cursor past them.
- Celly (local sandbox), 2026-10-23: after the 01:35:05 relaunch, external input
  was held until the user typed "222"; the first routed request followed at
  01:35:57 and message 6363 was injected one second later.

# Root cause (source at origin/nightly 8b6114b)

`runtime_compaction_control.py:457-468` pauses external admission whenever the
stored phase is not `verified`. Two states could only leave that phase through a
request the idle client never sends:

1. **`awaiting_shrink` after a manual compaction.** `_release` (414-441)
   accepted only a later smaller *request* (`shrink_observed`) or a post-checkpoint
   assistant usage record (`last_input_tokens`). A user-run `/compact` has no
   pending turn to retry, so neither exists. Regina's boundary carried the
   client's own measurement — `trigger=manual`, `postTokens=15,117` against a
   716,750 window ([evidence](evidence/regina-state.txt)) — and the phase still
   read `awaiting_shrink` an hour later.
2. **`request_identity_pending` after a launch.** `runtime_compaction_binding.py:458-483`
   held external input until the launch's first routed request. A relaunched
   session that stays idle never sends one ([evidence](evidence/celly-launch-gate.txt)).

# Change (dc7711a)

- `runtime_compaction_control.py`: `NativeCompactionCheckpoint` carries the
  boundary `trigger`; `_release` accepts a manual boundary's own `postTokens`
  when it is below both the window and the pre-compaction baseline, and logs
  `evidence=manual_checkpoint_post_tokens`. Automatic boundaries keep the
  existing retry-based release.
- `runtime_compaction_binding.py`: the identity hold ends
  `REQUEST_IDENTITY_GRACE_SECONDS = 120` after the identity was bound
  (`identity_since`, reset on every session change), logging
  `reason=request_identity_expired` or `identity_expired`. A routed request
  still opens the gate immediately.

Both paths stay self-correcting: an over-window request re-arms compaction
admission (`observe_request` pressure path), and every routed request re-binds
the exact scope.

# Verification

- 109 tests in the six compaction/channel files pass, including two manual
  boundary releases, one manual boundary that is still full (stays bounded),
  and three identity-grace cases ([output](evidence/focused-tests.txt)).
- Not verified here: the production effect on a live session. Claude processes
  keep the previous build until relaunch.

# Boundaries

- The Celly logs cannot show when 6363 arrived: the workspace `runtime-inputs.jsonl`
  returned `PermissionError` for this account, and the router first mentions the
  message at 01:35:58. The pause itself is logged, and the code returns before
  reading the inbox while paused.
- The Regina relaunch dropped 254-257 through
  `channel_llm_cursor_fast_forward_on_launch last_id=257 previous_cursor=253`.
  That is launch behaviour, not changed here.
- `postTokens` is the client's own count. The same value was already used as a
  *blocking* condition; using it as release evidence keeps one scale (client
  window vs client count).
