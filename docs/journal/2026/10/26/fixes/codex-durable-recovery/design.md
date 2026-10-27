---
type: Implementation Decision
title: Codex durable recovery
description: Codex app-server delivery records ownership before wire submission and never replays uncertain requests.
tags: [codex, recovery, json-rpc, durability]
timestamp: 2026-10-26T00:00:00Z
---

# Boundary classification

`CodexAppServerNotSent` is reserved for a proven pre-write failure. `CodexAppServerRejected` carries a structurally valid JSON-RPC error response. `CodexAppServerUncertain` covers a write, flush, timeout, reader failure, malformed response, or missing result whose acceptance cannot be proven.

Only the exact native `turn/steer` rejection `code=-32600` with message `no active turn to steer` permits the race fallback to `turn/start`. Uncertain steer failures never start a second turn.

# Durable ownership

The injector calls the shared status repository's `begin_attempt` before `turn/start` or `turn/steer`. The returned attempt and child epoch are pinned to later transitions. A status row that is `submitted` or `interrupted_uncertain` is never replayed by queue scanning. Restart adoption binds acknowledged native turns to their exact thread; uncertain rows remain quarantined.
