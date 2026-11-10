---
type: Fix
title: Side answers reach Walkie without a configured route, and main sees real delivery
description: Implicit Walkie reply/prefetch route when no operator route exists; Work status and the review prompt carry each receipt's delivery state.
timestamp: 2026-11-10T05:20:00Z
tags: [side-actor, walkie, reply-connector, review]
---

# Changes

## `side_reply_connector.binding_for(config, route, original)`

- If an operator binding matches, it decides, as before.
- If any operator binding is configured but none matches, there is still no implicit route.
- Otherwise a Walkie support CloudEvent gets the built-in Walkie preset (`server = "walkie"`) for its own receiver. The event must:
  - have a `type` starting with `com.ciel.walkie.`;
  - carry a UUID `data.conversation` and a numeric `data.message_id`;
  - arrive as an `external_event`.
- The reply goes only to the conversation and message that the pattern-checked fields name.
- The host's source prefetch (`walkie_read_messages`) uses the same binding. Without a route, the side actor saw an empty message body and could only ask for clarification (owned run, before the fix).

## `side_review.delivery_state` / `with_delivery`

- Each pending review row gets `delivery` from its inbox receipt: the outbox state first, then `result.delivery`, otherwise `pending` or `unknown`.
- The rows go to `work_status` (format json) and to REST `GET /ca/work/api/v1/side`.

## `ciel-work` mod: `sideReviewText`

Pending reviews are now split by delivery state:

| Delivery | Main is told |
|---|---|
| `submitted`, `side_mcp`, or absent (older router) | Already sent; do not answer or ACK; correct only if wrong. Unchanged. |
| `route_unavailable`, `failed` | Not sent; the person has not seen it. Send it once as the answer, with no ACK and no new investigation; then review. |
| anything else | Still sending or outcome unknown; do not send; review the content only. |

- The notice key includes the delivery state, so a reply that later fails is shown to main again.
