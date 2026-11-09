---
type: Fix Record
title: Side contract placement and decision tolerance
description: Commits b62fd9b, 034be56, 31e246d and 503be09, plus the Walkie reply connector.
timestamp: 2026-11-09T17:20:00Z
tags: [side-actor, beta]
---

# Changes

| Commit | Change |
|---|---|
| `b62fd9b` | answer/clarify drop copied Work fields; finished-Work updates become status answers; missing create title or update note falls back to the summary |
| `034be56` | `action` equal to `status`/`info` with the same `kind` is normalised to `answer` |
| `31e246d` | the side contract is a separate final block after the event JSON; handoff follow-ups reply to the original Walkie message |
| `503be09` | an assistant turn closes main's unanswered turn before the side event |

None of these lets a side decision mutate Work beyond what its validated
action already allowed. Unknown targets still fail.

# Configuration

The Walkie workspace config received the same wildcard Walkie preset Prody
has: `{"source":"*","receiver_id":"default","preset":"walkie","server":"walkie"}`.
