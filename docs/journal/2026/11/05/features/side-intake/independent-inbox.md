---
type: Implementation
title: Independent durable side intake inbox
description: Persist request before Work admission and reject empty mutation calls.
tags: [inbox, dedup, work]
timestamp: 2026-11-05
status: implementation-in-progress
---

# Contract

Independent session-keyed inbox.json is atomically replaced after fsync under advisory lock. Full original text and stable event key/digest retained before board admission. Duplicate same key/content returns stored receipt; conflicting content rejected.32MiB bounded document fails closed, corrupt original preserved. Board admission uses existing idempotent enqueue; cross-file crash recovers by same event key. Explicit coordinator work_side_drain retries pending board admission, no automatic approval or wait clearing. Read-only work_status does not drain.

Automatic full-board path preserves user-requested ordinary session fallback while also retaining independent receipt. Work OFF receives into inbox without creating Work board. Side status/schema available with Work OFF, mod launch enabled when side feature on. External ACK remains not_sent until verified connector contract; never conflate durable receipt with delivered ACK. No arbitrary event-provided URL used.

# Betty exact evidence

Read-only remote transcript26697479 at21:42:58,21:43:12,21:43:24 shows actual tool input only id=n888,expected_version=3760, repeatedly omitting every mutation field, despite explicit WAITING warning. Add server rejection NO CHANGE for mutationless calls, exact empty waiting_on instruction and next-runnable guidance; no inferred wait removal. This is verified model no-op behavior, not evidence waiting_on="" was stripped.

# Verification status

Independent full100 inbox receive/restart/dedup/drain test passes, Work OFF no-board receive test added. Existing source76 focused tests pass before final test addition. Release pending. External ACK/semantic triage and PostToolUse historical freeze unresolved; peer contract requested. These are not claimed complete.
