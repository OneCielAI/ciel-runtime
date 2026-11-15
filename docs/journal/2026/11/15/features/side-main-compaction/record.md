---
type: implementation-record
title: Compact main before a side actor whose source thread does not fit
description: Kobe and prody quarantined most recent SSE events because the Walkie source thread did not fit in the context main had left; the host now compacts main first and retries.
timestamp: 2026-11-15T09:10:00Z
tags: [work, side, sse, compaction, walkie]
---

# Measured failure (2026-11-15)

Quarantine counts by host:

| Host | Session | Quarantined, last 3 days | Last 6 hours |
| --- | --- | --- | --- |
| Kobe | `100.64.0.14` u24, router 9780 | All 25 sampled, every one `Source thread exceeds main context budget (… bytes left of window 872000, used 650k–699k)` | 4 of 6 events |
| Prody | `100.64.0.12` u41, router 9592 | 6 of 20 sampled had the same cause | 11 of 15 events |

Before this change the side host read the Walkie thread into a native fork, which inherits main's whole context. When the thread did not fit, the host counted it as a `source_context` failure. That meant 5 backoff retries, then quarantine, with no fork ever spawned.

# Change

`side-intake.js`: when main's remaining context is the limit (`mainContext` on the over-budget error), the event is released as `main_compaction_pending`.

- Main is compacted with `$.session.compact()`. An idle main is compacted at once. A main in a turn is compacted when that turn completes, before main is woken again.
- After one compaction per conversation within 10 minutes, a thread that still does not fit fails as before.

`side_intake_processing.fail`: `main_compaction_pending` handling.

- It never increments `attempt_count`, so it never quarantines.
- It retries after the host's delay: 30 s for an idle main, 60 s for a busy one.
- It is bounded by `MAIN_COMPACTION_WAITS = 12`. Past that bound it is an ordinary, counted `source_context` failure.
- `work_side_fail` accepts `retry_after_seconds` (5–600).

# Evidence

- `tests/test_side_intake_mod.py`, persisted harness scenarios:
  - an idle main compacts at once and the event waits with a 30 s retry;
  - within the cooldown, the same overflow is an ordinary `source_context` failure;
  - a busy main is not compacted mid-turn, and its turn's end compacts it (60 s retry).
- `tests/test_side_intake_mcp.py`: a wait does not count, its retry delay is honored, and the 13th wait becomes a counted failure.
- Side suites: 291 passed, 2 skipped.
- `claude plugin test` ciel-work: 101/101.
