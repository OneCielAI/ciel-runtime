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

## Limits

- The ordinary side-intake text limit is one shared constant: **32,000 characters**.
- One pending record may occupy up to **256 KiB**.
  - 64 KiB of that is reserved for fields written before and during processing: key, route, claim lease, context preview, failure and fallback records.
  - That leaves 192 KiB for the text. 32,000 characters fit even at the worst JSON escaping (6 bytes each).

## Side lane and transports

- The side lane is used **only while side intake is on**. With it off, every external event, malformed ones included, takes the ordinary untrusted external-event lane, as before this change.
- Side-lane markers (`side_intake_only`, `side_intake_text`, `side_intake_fallback`, `_side_intake_admitted`) are host decisions. They are stripped from caller metadata.
- External side rows keep the exact original in the durable runtime queue and the `external-mail` spool.
- On Claude Code they use only the verified native-session socket. The main transcript, router body injector and TTY are never fallbacks.
- Codex has no side actor. There, a side row is delivered as the same quoted external message as with side intake off, never as a command.

## Fallbacks

- A text above 32,000 characters that still fits one record is delivered unchanged, with fallback class `raw_text`.
- A text too large for one record stays unchanged in the queue/spool. The side agent gets a content-free notice: length, UTF-8 byte count and SHA-256.
- A coordinator follow-up above 32K takes the same `raw_text` route when it fits one record.
- Work-board drain leaves rows above 32K pending for a side fork instead of reporting an error each pass.
- A malformed CloudEvent with an SSE transport id gets a stable synthetic identity and is delivered unchanged as untrusted text. Any lane its body names is ignored.
  - If it is empty, it is dead-lettered.
  - Without a transport id, it remains unidentified and the cursor does not move.
- An operator-filtered type is written to a content-free dead-letter journal before the cursor moves. If it requested a valid delivery ACK, the queued-stage outcome is `rejected`.

## Cursor and storage

- If the queue, spool, dead-letter journal, ACK ledger or cursor write fails, the earlier cursor is kept.
- A side row that ends a socket batch is not passed by the committed cursor; it is sent alone on the next scan.

## Independent review

A separate review of commit `ede68b1` found six defects in this change and one older bug on the same path:

1. a side-only row created while side intake was off, which nothing consumed;
2. caller-settable side markers;
3. Codex delivery not documented;
4. a record budget too small for the context preview;
5. empty frames still stalling the stream;
6. the drain and follow-up limits at 32K.

The older bug: the cursor passed a side row that ended a socket batch. Every one is fixed. Each of the seven new regression tests fails on `ede68b1` and passes after the fix.

# Stable identity

The side key remains source plus event id. An identity longer than the inbox's 300-character key budget is deterministically reduced to `sha256:<digest>`. Redelivery with the same identity therefore reaches the same durable side receipt rather than creating a second one.

# Security boundary

The exact raw text is data, not a command. A body beginning with `/goal`, `/clear` or another command prefix is never parsed or typed into the main client. Logs and dead letters contain only bounded identifiers, counts, hashes and error classes.
