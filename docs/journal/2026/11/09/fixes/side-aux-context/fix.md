---
type: Fix Record
title: Preserve only main-turn parent snapshots
description: Request-class hints and a headerless suggestion guard keep auxiliary prompts out of side context.
timestamp: 2026-11-09T11:36:00Z
tags: [side-actor, claude-code, beta]
---

# Change

- Routed Claude launches now set `CLAUDE_CODE_GATEWAY_HINT_HEADERS=1`.
- The router accepts a parent capture only when
  `x-claude-code-request-class` is `main`.
- For a Claude process launched before that environment flag existed, a bounded
  compatibility guard rejects the observed 2.1.319 suggestion prefix.
- `auxiliary` and `compaction` requests never replace the stored parent.

The setting affects future launches. No live router or Claude process was killed
or restarted.

# Prody configuration

The Prody workspace received one separate, backed-up config update:

```json
{"source":"*","receiver_id":"default","preset":"walkie","server":"walkie"}
```

The backup is timestamped `20261109-031104`. A separate privileged read confirmed
the persisted binding. The live session remained running.
