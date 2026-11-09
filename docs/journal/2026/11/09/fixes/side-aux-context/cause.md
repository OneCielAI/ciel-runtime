---
type: Incident Analysis
title: Auxiliary prompt captured as side parent
description: Claude Code prompt-suggestion requests replaced the main transcript snapshot used by Prody side actors.
timestamp: 2026-11-09T11:36:00Z
tags: [side-actor, sse, prody]
---

# Observation

Prody's latest `side-contexts/.../latest.json` ended with Claude Code 2.1.319's
`[SUGGESTION MODE: Suggest what the user might naturally type next ...]` user
message. The captured parent had 401 messages and 96 tools. Side actors inherited
that instruction after their own side-decision prompt.

Read-only correlation over the latest four hours found:

| Projected parent | Valid decision | Invalid |
|---|---:|---:|
| Main conversation | 23 | 7 |
| Prompt suggestion | 1 | 69 |

Examples from the invalid actors were `Status check`, `Silent`, `Silence`, and
`Stay silent`. The last two were reproduced by the released beta in the owned
A/B run; neither event reached Work or the reply outbox.

# Proven cause

Claude Code sends prompt suggestions as an auxiliary fork under the main session
ID and without an agent ID. Before this fix, the router treated every request
that resolved to the main compaction scope as a main-conversation request and
unconditionally replaced `latest.json`.

The old owned run reproduced both failures after two completed main turns. See
[owned A/B summary](evidence/owned-ab.json) and
[released screenshot](evidence/released-invalid-answer.png).
