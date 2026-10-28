---
type: journal
title: Coding eval — routing graph, booking calendar, inventory stream
date: 2026-10-26
requests: [REQ-INITIAL, REQ-A0, REQ-A1, REQ-A2, REQ-A3, REQ-B0, REQ-B1, REQ-B2, REQ-B3, REQ-C0, REQ-C1, REQ-C2, REQ-C3, REQ-FINAL]
---

# Journal: three-task coding eval (2026-10-26)

## What was built
- `routing_graph.py` (A0–A3): directed graph, simple-path shortest search,
  blocked/max_hops, frozen snapshots, atomic `apply`, `tie_policy`
  (default `lexical`; `fewest_hops` keeps the A0 order).
- `booking_calendar.py` (B0–B3): cumulative-capacity half-open bookings,
  `reserve_many`/`replace`, priority+FIFO waiting list with `promote`,
  opaque checkpoints and a forward-only `version`.
- `inventory_stream.py` (C0–C3): fragment-safe JSONL parsing, stock/move/batch
  events, first-id-wins idempotency, net-delta `undo` with tombstones,
  checkpoints that include the unfinished buffer, 4096-code-point line bound.

## Verification
- `python -m unittest discover -v` → 100 tests, OK (log: `unittest_discover.log`).
- Randomized cross-checks: graph vs brute-force simple-path enumeration (both
  tie policies), calendar vs per-instant naive model, stream vs event model.

## Decisions on unspecified points
- See `completion.json` → `assumptions` for each request.

## Incidents
- A patch script written with Python text-mode on Windows converted LF to CRLF
  and turned `\r`/`\n` escapes into literal characters; detected via
  SyntaxError, repaired, and all files normalized back to LF.
