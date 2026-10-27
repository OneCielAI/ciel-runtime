---
type: investigation
title: Strict shared SSE recovery evidence
description: Evidence and disposition for exact retained queue recovery and related SSE observations.
tags: [sse, recovery, delivery]
timestamp: 2026-10-26T00:00:00Z
---

# Scope

The shared recovery change requires a fresh, read-only status/queue snapshot and exact intersection of retained nonterminal queued IDs. It preserves compaction veto, sole-client checks, cooldown reservation, and does not mutate input status, queue, or cursor files.

# Verification

- Original HEAD reproduced the mismatch: historical A + retained unrelated B authorized restart; the new focused regressions produced 7 failing assertions before the fix. See [before-fix regression output](evidence/before-fix-regressions.txt).
- Focused isolated tests: 54 tests passed in 3.383 seconds, including a 13-case real repository/recovery pipeline and existing loopback MCP + real restart-request-file fixture.
- Evidence: [focused test output](evidence/focused-tests.txt).
- EOF evidence: [existing EOF pattern](../walkie-durable-replied/evidence/10-sse-eof-pattern.txt).
- SendMessage evidence: [existing default-block policy](../walkie-durable-replied/evidence/11-sendmessage-blocked.txt).

# Disposition

The receiver implementation classifies clean stream end as `EOFError` during the read phase, records category `eof`, and reconnects with the cursor after bounded backoff. Existing retained logs show EOF disconnect intervals of 61–62 seconds and continued event acknowledgements; no loss was tied to EOF. This establishes that the client saw the response end, not whether the endpoint server or an intervening proxy caused it. No evidence establishes a receiver data-loss bug requiring a behavioral fix. An EOF-versus-error logging policy change would be a separate decision; neither receiver nor external endpoint was changed.

The default non-Anthropic tool policy filters `SendMessage`/`SendMessageTool` for cielairouter. The override exists in provider configuration but is not exposed in the menu. The reason for the product default and non-Anthropic execution behavior are not established by the retained evidence. No tool-exposure or egress files were changed.
