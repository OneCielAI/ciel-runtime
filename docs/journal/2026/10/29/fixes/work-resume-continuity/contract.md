---
type: Fix Contract
title: Work resume continuity
description: Preserve Work only across authenticated native parked-session handoffs, never by guessing latest session identity.
tags: [work, resume, session, persistence]
timestamp: 2026-10-30
---
# Contract

- Exact unchanged session ID uses the same SHA256 store.
- A Claude background session may use its parked parent board only when native session records prove a unique live `parkedJobId` edge, matching cwd and pid domain, verified process identity/key, bounded handoff, and no competing parent.
- The child gets an atomic lineage marker; later resume reads the canonical parent board even when native records disappear.
- Missing source, corrupt/cyclic alias, dead/reused identity, ambiguous parents, pre-existing child board or unrelated session fail closed without deleting or merging either board.
- No workspace-wide/latest-board fallback.

# Boundary

The observed explicit current Walkie parked edge proves this is a real split mode, but this change is not accepted or deployed until isolated tests and restart/resume E2E pass.

# Review boundary corrections

Immediate edges are authenticated independently; recursive resolution reaches the original board even when intermediate native sessions never read Work. At most8nativeedgesand8persistedaliases;cyclesfailclosed. Savedemptyboardswithnonzero-versionarepreserved. Intermediate splitboardsorjobambiguityremainerrors/isolation,notlatestselection.
