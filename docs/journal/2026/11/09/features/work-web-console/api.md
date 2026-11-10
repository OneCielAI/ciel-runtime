---
type: API Contract
title: Current-session Work REST v1
description: Versioned REST resources and human operator commands used by the Work web console and external services.
timestamp: 2026-11-09T23:55:00Z
tags: [work, rest, queue, cas, idempotency]
---

# Scope

Every `/ca/work/api/v1/*` route resolves the router's currently bound conversation from `compaction-scope.json`. It does not select another stored Work session. A supplied legacy `session_id` is accepted only when it equals that current identity.

The browser console calls only these REST endpoints and the SSE endpoint. External services use the same existing router administrator bearer token; no Work-specific credential exists.

# Read resources

| Route | Projection |
|---|---|
| `GET /ca/work/api/v1/board` | Full current tree, active root, root and waiting queue order, runtime metadata |
| `GET /ca/work/api/v1/state` | Compact Work status |
| `GET /ca/work/api/v1/queue` | Active root and waiting-only one-based `queue_position` |
| `GET /ca/work/api/v1/runtime` | Configured/observed runtime and redacted errors |
| `GET /ca/work/api/v1/settings` | Typed Work settings from `work_live_settings` |
| `GET /ca/work/api/v1/parallel` | Current-session leases and device policy with worker capability hashes removed |
| `GET /ca/work/api/v1/side` | Allow-listed review/handoff state; no original text, route or reply token |
| `GET /ca/work/api/v1/audit` | Bounded REST operation receipts; no request body or credential |
| `GET /ca/work/api/v1/events` | Coalesced invalidation SSE; refetch after every event |
| `GET /ca/work/api/v1/openapi` | Machine-readable route contract |

# Commands

Board commands require `expected_version` and an `Idempotency-Key` header or matching `operation_id`:

- `plan-set`, `node-add`, `node-update`, `node-remove`
- `active-select`, `execute`, `replan`
- `sibling-reorder` (same parent) and `queue-position` (waiting roots only)
- `focus`, `urgent`

Operator commands:

- `settings-update`
- `parallel-configure`
- `parallel-orphans-preview`, followed by digest-bound `parallel-orphans-apply`; apply releases only a positively dead owner
- `side-review`, `side-handoff`, `side-reply-retry`

Internal actor/worker calls (claim, resolve, fail, context, lease bind/report/transition) are not REST operations.

# Compatibility

The legacy `GET /ca/work*` and `POST /ca/work/nodes/{add,update,remove,reorder}` routes remain. Legacy `reorder` keeps its exact one-based sibling/root semantics and active-slot guard. V1 never treats waiting-only `queue_position` as that legacy position.

# Concurrency

- Board writes are locked, atomic CAS operations.
- A repeated operation ID with the same digest returns its original committed result.
- Reusing an operation ID for another body returns 409.
- A stale `expected_version` returns 409 with the current version; clients refetch and do not blindly retry.
