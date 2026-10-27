---
type: API Contract
title: Work HTTP API
description: Authenticated all-depth Work board query and mutation API with runtime metadata and change invalidations.
tags: [work, http, sse, runtime]
timestamp: 2026-10-26T00:00:00Z
---

# Schema

`GET /ca/work?session_id=claude:<id>` returns the complete board, version, queue order, every node, configured runtime/build metadata, and exact-session error observations. `POST /ca/work/nodes/{add,update,remove,reorder}` requires a nonnegative integer `expected_version`; stale writes return 409.

`GET /ca/work/runtime` returns configured provider/model separately from response-observed model. Unknown values are null. Error rows are allowlisted and correlated to the exact runtime/session/epoch; prompts, bodies, credentials and credential-error text are not returned.

`GET /ca/work/events?session_id=...` sends version invalidations and runtime-error invalidations. Events are coalesced hints; clients refetch after snapshot, change, reset or reconnect.

# Security

Routes stay behind the router's existing loopback/admin-token/web-account policy and POST body admission. External access is not enabled by this API and no CORS policy is added.
