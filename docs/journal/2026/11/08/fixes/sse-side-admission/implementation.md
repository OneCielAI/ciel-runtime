---
type: fix
title: SSE side admission no longer blocks behind one permanent frame error
description: Side intake accepts 32,000 characters and identified SSE poison frames take a durable raw-text or content-free side path without entering the main transcript.
tags: [sse, side-intake, durability, cursor, work]
timestamp: 2026-11-08
---

# Confirmed cause

The captured Prody frame was a valid, allowed CloudEvent of 16,055 characters. The former 16,000-character gateway check rejected it before durable queue admission; the receiver therefore did not advance its cursor and repeatedly received the same frame. The source diagnosis is recorded in `docs/journal/2026/11/08/diagnostics/prody-sse-admission-block/finding.md`; its index links back to this fix.

# Contract

- The ordinary side-intake text limit is one shared constant: **32,000 characters**.
- One pending record may occupy up to **256 KiB**, enough for 32,000 four-byte characters plus identity, route and state.
- External side rows keep the exact original in the durable runtime queue and `external-mail` spool. They are marked `side_intake_only` and use only the verified native-session socket.
- The main transcript, router body injector and TTY are not fallback transports for a side-only row.
- A text above 32,000 characters that still fits one side record is delivered unchanged with fallback class `raw_text`.
- A text too large for one record stays unchanged in the queue/spool; the side agent gets a content-free notice with length, UTF-8 byte count and SHA-256.
- A malformed CloudEvent with an SSE transport id gets a stable synthetic identity and is delivered unchanged as untrusted side text. Without a transport id, it remains unidentified and the cursor does not move.
- An operator-filtered type is committed to a content-free dead-letter journal before the cursor moves. If it requested a valid delivery ACK, the queued-stage outcome is `rejected`.
- Queue, spool, dead-letter, ACK-ledger or cursor storage failure retains the earlier cursor.

# Stable identity

The side key remains source plus event id. An identity longer than the inbox's 300-character key budget is deterministically reduced to `sha256:<digest>`. Redelivery with the same identity therefore reaches the same durable side receipt rather than creating a second one.

# Security boundary

The exact raw text is data, not a command. A body beginning with `/goal`, `/clear` or another command prefix is never parsed or typed into the main client. Logs and dead letters contain only bounded identifiers, counts, hashes and error classes.
