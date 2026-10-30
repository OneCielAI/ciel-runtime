---
type: Diagnostic
title: Walkie terminal input blocks router wake
description: Durable failed TTY input remained a FIFO barrier in router injection; source fix and owned HTTP witness preserve no-replay safety.
tags: [channel, fifo, tty, recovery]
timestamp: 2026-10-30
---
# Exact observed failure

Walkie router9505 running8b8f88c rejected the11:30:31Z native request with503 `deferred wake body injection behind TTY message id=15918`. Native session57d7eb89 records a typed human dot at11:30:30.722Z followed by the503. The exact native request body is not retained, so why the dot was classified as wake remains unknown; the ordinary policy classifies a standalone dot as non-wake.

Input15918 durable history: queued06:18:30, submitted06:19:03, failed06:19:07 local with prompt_not_submitted. Claim file was empty and LLM cursor remained15917. At06:30:31 router injection still deferred behind15918. Earlier same-class503s for15907/15910 exist from02:47 local, before the8b8f88c release published08:48Z/03:48local. Therefore the broader arbitration defect predates the latest release; timing alone does not establish causation by8b8f88c. No arbitration code changed betweenb79701c and8b8f88c.

# Reproduced defect and correction

Terminal-side polling already checks durable terminal statuses. Router-side `ChannelWakeContext.stdin_skip_reason` checked only process-local delivered state and live claims, ignoring durable failed/skipped/replied/interrupted_uncertain. A failed input could therefore continue owning FIFO router admission and block later wake input indefinitely.

Corrected router skip policy to recognize these durable terminal statuses before transport ownership. It skips without replaying body or changing status. Queued/submitted/unknown TTY input still holds FIFO; no uncertain replay or ownership bypass is introduced.

# Verification

- Baseline two tests: four terminal-state subcases fail, no test errors. Baseline retained.
-99focusedtests pass (context injection, lifecycle, pending injection, claim store, durable input status).
- Owned real HTTP witness uses actual durable queued→submitted→failed history15918, real lifecycle lookup, fixed context skip and router injection. POST returns200, injects only15919, failed body not replayed, durable status bytes unchanged. This is not a full native CLI replay of the user's dot.
- Ruff and diff whitespace checks run for changed files. Logs and payload-free exact status are retained locally. No live queue, cursor, claims, sandbox settings or process were changed.

# Remaining boundary

Source fix is not yet installed/published. Exact dot→wake classification and original socket disappearance remain unproven; native records showed bg successors with different sockets while the launch compaction scope session was empty. Those are observations, not established causes. The user session is not declared repaired until actual installed execution is observed.
