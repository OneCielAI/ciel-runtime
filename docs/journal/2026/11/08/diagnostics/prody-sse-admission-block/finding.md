---
type: diagnosis
title: Prody SSE receiver blocked at admission
description: "Prody u41's SSE receiver connects, then rejects the first frame at admission with ValueError, every 30 s. The cursor never advances, so no new SSE event has reached the session since its 03:49 router start. The exact ValueError message is not recorded anywhere readable."
tags: [aap, prody, sse, receiver, admission, side-intake]
timestamp: 2026-11-08
---

# Observed (read-only, 2026-11-08 ~03:55 PST)

## Receiver state

Prody's receiver (`GET http://127.0.0.1:9592/ca/events/receivers`, [evidence](evidence/receivers-full.txt)):

| Field | Value |
| --- | --- |
| receiver | `default` |
| `event_types` | `com.ciel.walkie.support.message` |
| cursor | `/data/stream_id`, query `after` |
| state / phase | `backoff` / `admission` |
| error | `ValueError` (`category: validation`) |
| consecutive failures | 16 |
| retry | 30 s |
| `last_event_id` | empty |
| `connected_at` / `last_failure_at` | the same second (03:54:49) |

Context:
- The router started at 03:49:12 (`router.pid`). In that process no event has been admitted, and every connection fails on its first frame.
- Betty u42 has the same receiver config and is `connected/read`, 0 failures, last event `…:118035` ([evidence](evidence/betty-receiver.txt)).
- Prody's router always logs at level ERROR (every start since 10-27). The `external_event_sse_disconnected` WARN lines that would name the failure are therefore never written.

## Main session

Prody's main session (transcript, [evidence](evidence/prody-main.txt)):
- Since 07:00 UTC it holds only the user's own `계속` turns and Work stop-hook messages. No side forks ran after 07:03 UTC.
- `/ca/work/runtime` reports no errors and a 200 for main's last request at 11:52:34 UTC ([evidence](evidence/prody-router-get.txt)).

# Why one bad frame blocks the whole stream (code, confirmed)

1. `external_event_receiver.py:981-997`: for each frame, the receiver runs `validate_cloud_event`, then `_admit_sse_frame`. Only after both succeed does it write the cursor (`cursor_write`).
2. Any exception goes to backoff (`:1003-1026`) and reconnects from the old cursor. The server sends the same frame again, and it fails again.

The receiver status keeps only the exception class. These are the `ValueError`s that can be raised at this phase:

| Source | Message |
| --- | --- |
| `validate_cloud_event` (`:264-281`) | body not JSON or not an object; `specversion` ≠ 1.0; missing `id`/`source`/`type` |
| `_admit` (`:707-711`) | `CloudEvent type is not admitted by this receiver` (type outside `com.ciel.walkie.support.message`) |
| `runtime_input_gateway._append` (`:37-48`) | `injection_type must be session or btw_with_mcp`, `btw_with_mcp requires stable meta.event_id…`, or **`btw_with_mcp requires 1-16000 characters`** (side lane is on, so external events take it) |

**Which one Prody's frame hits is not known.** Finding out needs one of:
- the message itself, which needs Prody's router at INFO/WARN, a config and restart change not made here;
- or the frame at Prody's stream cursor, found from the Walkie side.

# The `runtime_queued` rows in the side pane

- `work_status` labels every entry of the board's legacy `side_intake_receipts` map as `stage: runtime_queued` (`work_mcp_tools.py:435-439`). Those entries were written by the old board-intake path (`work_side_intake.enqueue`).
- The pane then shows them as "Received; AI has not processed this event yet" because they carry no result fields (`register.js:1711`).
- `n173` is not on Prody's current board: version 1610, 31 nodes, no `External request` roots ([evidence](evidence/prody-board.txt)).

**These rows are stale history, not a live queue.**

# Not determined

- The four `received` rows in the same pane are durable side-inbox events admitted earlier but not claimed. Why they were not claimed was not determined.
- The side inbox cannot be read from SYSTEM: `workspaces\c53b7c787223` returns `UnauthorizedAccessException`.
- This also corrects the earlier note "no side-inboxes directory": it is access denied, not absent.
