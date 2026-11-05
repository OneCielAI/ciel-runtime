---
type: Fix
title: Explicit waiting state and independent scheduling
description: Do not confuse evidence updates with clearing waits or hold independent work behind a waiting urgent goal.
tags: [work, waiting, scheduler]
timestamp: 2026-11-05
---

# Verified defects

work_node_update returned Updated(status) even if waiting_on remained set. New response explicitly reports WAITING, actual stored reason, evidence/notes do not clear it, exact waiting_on empty-string call and ancestor/approval caveat. Clearing node wait reports only that node is empty, never implies ancestor holds cleared. Next runnable item included.

_runnable_by_root held every queued goal behind an urgent active root even when all its leaves waited/blocked. User now explicitly requests independent tasks run while waiting. Urgent root retains priority while runnable; when none can run it yields independent queued roots without clearing any waits or approving execution. Queue display held flags follow continuation. Native approval gates unchanged.

# Verification

118 tree/MCP/mod/architecture tests pass. Actual dispatch regression proves evidence-only update leaves review wait and emits explicit warning; explicit empty waiting_on clears it. Urgent-wait fixture selects queued goal with original review wait preserved. Nested urgent queue test updated to new user policy; runnable urgent still holds queue. Release pending.

# Separate interim release

Full100 SSE ordinary fallback82379a8 CI42105740012/Publish42105739983 success. Local205331.82379a8 installed+pin synchronized. Actual installed native d27ef7ab full100 board unchanged, original SSE reaches next model call. Original independent inbox/ACK design remains open.
