---
type: Incident Analysis
title: Why Prody's side actor claimed SSE events 70-100 s late
description: Measured causes of the received-to-claim delay on Prody (beta 3757675, Claude Code 2.1.322) and the hypotheses that the owned probes ruled out.
timestamp: 2026-11-10T16:40:00Z
tags: [side-intake, sse, latency, prody, claude-code]
---

# Observation

Read-only inbox timings from Prody (`receipts` of session `ef61c539`, lineage `6ecab3e3…`):

| Window | Events | received→claim p50 | p90 | max | min |
|---|---|---|---|---|---|
| 11-09 22Z → 11-10 07:25Z | 108 | 77.6 s | 181.3 s | 868.2 s | 1.0 s |
| after 11-10 07:26Z (beta 3757675 installed) | 12 | 95.6 s | 110.3 s | 148.6 s | 17.5 s |

The delay did not depend on load: with zero actors running and zero events queued ahead, the p50 was still 69-78 s ([numbers](evidence/prody-latency.json)).

# Cause 1: one claim every ~30 s, not every 2 s (measured)

- The claim cursor file `decision-cursor.json` is rewritten on every `work_side_claim`. Watched read-only for 120 s at 16:13Z, it changed at 16:13:53, 16:14:24, 16:14:54, 16:15:25 and 16:15:55: one claim per ~30 s ([watch](evidence/prody-cursor-watch.txt)).
- Prody's host-result page store showed why. An **empty** claim (`"claim": null`) was 361,083 characters, so it was returned as **46 pages**. Each page is a separate MCP round trip ([page listing](evidence/prody-host-pages.txt)).
- The bulk was `handoff_replies`. Each poll returned every acknowledged handoff, which on Prody was 30 answered handoffs, each with its full receipt and original message text. The mod had already shown every one of them.
- In the same tick, `work_side_status` (about 106K characters, 14 pages) ran behind a 2 s `Promise.race`. The race abandoned the fetch but never cancelled it.

# Cause 2: fresh events hidden behind quarantined rows (reproduced)

- Prody's `pending/` held 230 events quarantined as `invalid_answer`. They are never claimable.
- `claim()` reads pending rows in rotating keyset windows of 100. An unprioritized new event is only seen when the window reaches it.
- With the same shape locally, a fresh event needed up to 3 claim calls on the old code. After the fix it needs 1, once the legacy rows are indexed ([before](evidence/claim-rotation-before.json), [after](evidence/claim-rotation-after.json)).
- 3 windows × ~30 s per claim fits the observed p50 of 78-96 s. This is an inference from the two measured parts; it was not measured on Prody as one chain.

# Ruled out by owned Claude Code probes (2.1.321, loopback model)

| Hypothesis | Probe | Result |
|---|---|---|
| The engine delays a session-socket frame while main is busy | [receipt_ready_probe](evidence/receipt_ready_probe.py) | `session.receive` (origin `peer`) fired in 1 ms in all three states: main inside a running Bash tool, main inside an open model stream, and main idle. The model never saw the frame. |
| `$.clock.after(2000)` or `$.mcp.call` stall while main is busy | [clock_mcp_probe](evidence/clock_mcp_probe.py) | Ticks every 2.008-2.027 s, MCP round trip 5-23 ms, in idle, tool and stream states. |
| `pollSide` holds `busy` across a long `$.agent.spawn` | [spawn_latency_probe](evidence/spawn_latency_probe.py) | Spawn resolved in 20-24 ms, before the actor's first model request (0.2-0.3 s) and long before its 20 s turn ended. This held both with main idle and with main busy. |

# Quarantined events (reported, not reprocessed)

- At the reason query, Prody had 231 permanently quarantined events ([reasons](evidence/prody-quarantine-reasons.txt)).
- Of these, 224 were from 11-08 and 11-09, on builds before the decision-tolerance fixes. 167 of the 231 were "side answer carries no decision JSON".
- On 11-10 there were 7, all host refusals of a model decision:
  - no decision JSON: 2;
  - bounded summary/reply: 2;
  - invalid `kind`: 1;
  - invalid `queue_position`: 1;
  - existing identity required: 1.
- They are not retried because a completed turn may already have sent an external reply. `work_side_status` now reports `quarantined_count`. Reprocessing them is an operator decision.
