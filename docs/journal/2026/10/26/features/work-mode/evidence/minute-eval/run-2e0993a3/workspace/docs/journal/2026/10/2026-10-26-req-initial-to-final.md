---
type: journal
title: REQ-INITIAL through REQ-FINAL implementation journal
date: 2026-10-26
tags: [routing_graph, booking_calendar, inventory_stream]
---

# Journal 2026-10-26

Requests were handled in arrival order; each amendment got its own node in the Work plan
under its task's branch (A / B / C).

| Request | Change | Verification |
|---------|--------|--------------|
| REQ-A0 | `routing_graph.py`: Graph, versioning, simple-path search | `test_routing_graph` |
| REQ-B0 | `booking_calendar.py`: cumulative half-open capacity sweep | `test_booking_calendar` |
| REQ-C0 | `inventory_stream.py`: fragment buffering, idempotency, conflicts | `test_inventory_stream` |
| REQ-A1 | `blocked`, `max_hops` enforced during search, not afterwards | cheap 3-hop vs costly 1-hop test |
| REQ-B1 | `reserve_many`, `replace` via candidate copy then commit | rollback tests |
| REQ-C1 | `batch` kind applied to a candidate balance map | rollback / retry / empty batch tests |
| REQ-A2 | `snapshot()` (read-only `GraphSnapshot`), atomic `apply()` | version-once, ConflictError tests |
| REQ-B2 | `wait`/`waiting`/`promote`, waiting IDs reserved | priority + FIFO + skip tests |
| REQ-C2 | `undo()` using stored per-event net SKU delta, tombstones | negative-refusal then retry test |
| REQ-A3 | `tie_policy` (`lexical` default, `fewest_hops` explicit) | 300 randomized brute-force cases |
| REQ-B3 | `checkpoint`/`restore`, `Calendar.version` | exact FIFO restore, foreign ValueError |
| REQ-C3 | stream `checkpoint`/`restore`, 4096 code-point line bound | whole-feed rollback test |

## Decisions recorded

- A3 supersedes A0's equal-cost ordering only for the default policy; `fewest_hops` keeps A0.
- C3 length excludes the LF terminator and one CR directly before it (or pending at the end
  of the buffer, since it may become a CRLF). This interpretation is mine; the request did
  not define CR handling.
- C3 bound is checked over the whole buffer before any line of that call is applied, so an
  overlong line rejects the entire call with no partial effect.

## Known limitations

- Route search enumerates simple paths best-first without dominance pruning; correct
  (checked against brute force) but worst-case exponential on dense graphs.
- `git status` could not run: git refuses `C:/` as a repository owned by TrustedInstaller
  (dubious ownership). Git configuration was not changed.
