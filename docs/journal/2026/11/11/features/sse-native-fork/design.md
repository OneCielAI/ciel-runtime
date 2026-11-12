---
type: Design
title: SSE native fork contract
description: Every admitted SSE event runs in one native fork inheriting main, while the host alone commits the decision and sends the reply.
timestamp: 2026-11-11T00:00:00Z
tags: [sse, side-intake, native-fork, walkie, work]
---

# Decision

Every admitted side event uses `subagentType: "fork"`. The fork is unnamed (a named fork stays idle; see [probes](probes.md)) and inherits the main session's:

- model;
- complete conversation context;
- tool surface and MCP connections;
- native permission mode.

The registered `ciel-work:side-intake` type and separately selected side model/context projection are retired. Historical `model`, `context_mode`, and `context_percent` values may remain in persisted config but are ignored by runtime and omitted from settings UI/CLI.

# Prompt-only restriction

The fork is not given a technical tool fence. Its event prompt states that the restriction is prompt policy and explicitly prohibits:

- code, config, and file writes;
- Bash, PowerShell, builds, tests, deploys, installs, commits, and pushes;
- runtime/session/model/settings control;
- Work mutation;
- Agent/subagent/fork calls or messaging;
- direct source/channel ACK, reply, `SendMessage`, or other send.

It must return one decision JSON. The durable host remains the sole Work commit authority and the outbox remains the sole source-reply authority.

# Walkie source prefetch

Before the fork is spawned, the host resolves and cross-checks every canonical thread identity from `data.thread_root_id`, `message_context.thread_task.root_id`, `message_context.id`, and `data.message_id`. It reads the root with `walkie_read_messages(after=root-1)` and pages replies through `walkie_call_tool(operation_id="threads.read", schema_version=1)`, 200 ascending rows per page, until a short page. IDs follow Walkie's exact 1–18 digit positive format and JS compares them with `BigInt`. The target must be the root or one verified reply. Explicit null lineage is preserved as a root sentinel. Message text is never truncated; only credential-shaped keys are redacted.

The limits are evidence-based. A read-only local aggregate measured 531 threads / 3,129 unique rows without copying text: thread rows p50/p90/p99/max = 3/13/40/113; serialized bytes = 9,429/37,090/111,564/341,233. Static caps are 8 pages (1,600 replies + root) and 384 KiB. Because the fork inherits main's whole context, the retained thread must also fit `window - used - max(16K, 20% of window)` from `$.session.usage().context`, counting UTF-8 bytes as an upper bound on tokens. Each single host MCP read is capped at 4 MiB. A result Claude Code persisted to a file is read only from this session's exact tool-results file and only when its length equals the stated count ([probes](probes.md)). Anything over a limit fails closed.

Events with no message/thread reference are marked `source_context: none_referenced` and may run from their bounded event body. Malformed identifiers fail closed.

# Fail-closed outcomes

| Condition | Failure category | Fork/reply |
|---|---|---|
| Root/reply read error or missing target | `source_context` | no spawn, no reply; durable receipt retained |
| Malformed UUID/message/lineage or conflicting identities | `invalid_request` | no spawn, no reply; quarantined terminal |
| Target lineage mismatch | `source_context` | no spawn, no reply; durable receipt retained |
| Thread over 8-page/1,601-row/384 KiB or main-context budget; persisted result outside the session file or size-mismatched | `source_context` | no spawn, no reply; receipt retained, 5 retries then terminal quarantine |

# Preserved scheduler behavior

The durable inbox, deduplication, `max_active` slots, P1 reserved slot, Work decision validation, main wakes, wait release, and exact-once outbox flow remain unchanged.

# Delivery without a source reference

An event that references no message or thread (`source_context: none_referenced`) has no source to answer. The outbox records `not_applicable`, never `route_unavailable`; the review row carries the classification so main only reviews the board decision and is never asked to send.

# Spawn proof

`sideSpawnInput` returns `null` for an unrelated spawn, the event unchanged for the exact pending unnamed fork (`fork: true`, `subagentType: fork`, exact description, current lifecycle, no parent agent, plugin `ciel-work`), and `{deny}` for anything that claims a pending side identity but fails one check.

# Quarantine

A quarantined row is terminal whatever its model fingerprint. Native forks use one constant fingerprint; lifting quarantines on a fingerprint change would re-answer legacy rows.
