---
type: Fix
title: Betty side intake capacity exception containment
description: Verified remote throw site and pending-preserving local correction.
tags: [betty, startup, side-intake]
timestamp: 2026-11-05
---

# Evidence

Read-only SSH betty-ai@100.64.0.12, supplied sandbox u42 home. Router log C:/Users/aapw-a26u42/AppData/Roaming/ciel-runtime/router-instances/9564-a6276f9b0629/router.log records10:28:03 and10:37:22 pending_poll WorkPlanError through claude_session_socket189->runtime_intake->enqueue->transaction->work_plan_tree236:add_node. Read installed source around225: throw at236 is unfinished-node100 capacity guard. Thus observed exception matches explicit capacity rejection, not guessed network failure. Board directory read access denied; no ACL bypass, board count unverified. Subsequent rows2478-2480 submission_exception/skipped confirm failure handling lost retryability.

# Correction

At direct side callback boundary catch WorkPlanError and OSError, log bounded precise reason and return false before any socket bytes. Existing admitted-side false path defer_unsubmitted retains queue/cursor and does not replay to TTY. Do not delete history, clear board, bypass capacity, claim ACK or auto-complete tasks. This contains startup failure while preserving the explicit capacity rejection. No remote file/config/process changes performed.

# Verification

146 focused tests pass1platform skip; regression rejects capacity callback without exception/socket write and logs retain_pending. Combined with durable queue-retry regression. Included in same forthcoming settings release; not yet deployed at writing.
