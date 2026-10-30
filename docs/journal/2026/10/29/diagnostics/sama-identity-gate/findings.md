---
type: Diagnostic
title: SAMA reported identity gate hold
description: Separates reported identity hold from accessible runtime observations and unverified cause.
tags: [sse, identity, admission, sama]
timestamp: 2026-10-29
---
# Report

User reports grant session SSE gate on: held(identity), with a manual /sse-gate on action. Exact spelling/action/time not independently confirmed.

# Observed

Local host is SAMA. Accessible router8872reports workspace E:/aap/u22/home/ganti-ai, account aapw-a20u22, version0.2.51-nightly.20261030-015514.9077265. This is a candidate, not proven grant identity. Direct read of its profile/router.log and E:/aap/u22denied by Windows ACL; no bypass attempted. Read-only delivery API currently says admission not blocked, one queued record; this does not explain the earlier displayed hold. Local shortcut event observed; no prompt bodies retained.

# Semantics

On enables policy, not unconditional blocking. Held means admission withheld for its reason; open means that gate is not withholding. Off bypasses known compact recovery reasons only, never identity/handoff/uncertain ownership protections. Local slash returns before ordinary request identity observation, so on is not a repair operation and observed recovery cannot be attributed to it without timeline evidence.

# Limits

Exact historical cause not established. Need exact grant session association and readable scope/gate status, matching native session record metadata, and identity-transition log lines. No source change based on speculation, no live restart/config/queue/cursor mutation. Screenshot16Bettylaunch timeout is a separate existing Work goal n358.

# Isolated witness

One synthetic published idle identity produces open; two matching candidate records produce held(native_handoff_pending). This is expected fail-closed ambiguity, not the reported exact identity_pending and not proof of a dead process incident. No selection weakening implemented. [Witness](evidence/candidate-ambiguity-witness.json).

# Read-only API timeline

Candidate8872shows initial generation at2026-10-29T21:50:19-05:00, one external input queued at21:51:08and a local shortcut event later. Current API does not expose the underlying exact identity evidence. Workspace name is ganti-ai, not independently linked to the user-reported grant label. Exact incident correlation remains blocked by ACL and association limits.

# Confirmed wording improvement

The command previously displayed policy on/off beside a machine-state label without saying explicitly whether external-message injection was held. User directly asked whether on meant blocked. Command responses now distinguish policy from actual hold/open/bypass/unknown and state that on/off does not toggle SSE reception. No identity-admission relaxation was made.118focused tests pass, including all mode×effective-state combinations and unchanged identity protections; actualCLI proof passed: off/status/on latest reply, one baseline upstream only,0foregroundhits/804samples,3PNG retained.

# Harness-only failure

The longer explanatory response scrolled an older command out of the fixed viewport. A command-count-increase assertion timed out despite latest `SSE gate on; open` response being rendered and config true. Switched to matching exact latest command plus response; rerun passed. No production behavior changed to make the test pass.
