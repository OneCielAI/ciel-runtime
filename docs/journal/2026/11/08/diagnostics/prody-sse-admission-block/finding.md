---
type: diagnosis
title: Prody SSE receiver blocked at admission
description: "Prody u41's SSE receiver connects, then rejects the first frame because the complete CloudEvent is 16,055 characters, exceeding the side-intake 16,000-character limit by 55. The cursor never advances, so the same event blocks all later SSE events."
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

# Captured exact cause (04:14 PST)

After the Prody session was stopped, a one-shot, metadata-only capture was installed before the next launch. On resume, the blocked frame was captured immediately ([evidence](evidence/admission-capture.txt)):

| Field | Captured value |
| --- | --- |
| transport event id | `13492` |
| raw length | **16,055 characters / 23,934 UTF-8 bytes** |
| raw SHA-256 | `b6e5bd21599925d4adb453f1f7a8694da734003ad4bc3168e8934e2cf0bbdf4f` |
| JSON / CloudEvent | valid object, `specversion: 1.0`, id/source/type present |
| type | `com.ciel.walkie.support.message`, allowed |
| stream id | present |
| `injection_type` | absent (normal) |
| exception | **`btw_with_mcp requires 1-16000 characters`** |

So the event is structurally valid. It exceeds `runtime_input_gateway._append`'s side-intake limit by **55 characters**. The limit measures the entire serialized CloudEvent (`raw_text`), not only `data.text`.

The capture did not store the message body, URLs, credentials, route or token. It stored only length, hash, field names and validation facts.

The package file was restored after capture:
- original and restored SHA-256: `06171FFF9360D2AB5D8257A5E463D58CAEABEF4A206B689CBCDFC390316D8528`;
- backup removed;
- helper scripts removed;
- the 1,042-byte capture file remains while the current router is alive so the in-memory diagnostic code cannot recreate it.

# Why one bad frame blocks the whole stream (code, confirmed)

1. `external_event_receiver.py:981-997`: for each frame, the receiver runs `validate_cloud_event`, then `_admit_sse_frame`. Only after both succeed does it write the cursor (`cursor_write`).
2. Any exception goes to backoff (`:1003-1026`) and reconnects from the old cursor. The server sends the same frame again, and it fails again.

The receiver status keeps only the exception class. These are the `ValueError`s that can be raised at this phase:

| Source | Message |
| --- | --- |
| `validate_cloud_event` (`:264-281`) | body not JSON or not an object; `specversion` ≠ 1.0; missing `id`/`source`/`type` |
| `_admit` (`:707-711`) | `CloudEvent type is not admitted by this receiver` (type outside `com.ciel.walkie.support.message`) |
| `runtime_input_gateway._append` (`:37-48`) | `injection_type must be session or btw_with_mcp`, `btw_with_mcp requires stable meta.event_id…`, or **`btw_with_mcp requires 1-16000 characters`** (side lane is on, so external events take it) |

The one-shot capture proves it is the 16,000-character side-intake limit, not the other candidates.

# The `runtime_queued` rows in the side pane

- `work_status` labels every entry of the board's legacy `side_intake_receipts` map as `stage: runtime_queued` (`work_mcp_tools.py:435-439`). Those entries were written by the old board-intake path (`work_side_intake.enqueue`).
- The pane then shows them as "Received; AI has not processed this event yet" because they carry no result fields (`register.js:1711`).
- `n173` is not on Prody's current board: version 1610, 31 nodes, no `External request` roots ([evidence](evidence/prody-board.txt)).

**These rows are stale history, not a live queue.**

# Not determined

- The four `received` rows in the same pane are durable side-inbox events admitted earlier but not claimed. Why they were not claimed was not determined.
- The side inbox cannot be read from SYSTEM: `workspaces\c53b7c787223` returns `UnauthorizedAccessException`.
- This also corrects the earlier note "no side-inboxes directory": it is access denied, not absent.
