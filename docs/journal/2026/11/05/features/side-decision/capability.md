---
type: verification
title: Tool-enabled side actor during long main stream
description: Owned actual CLI measurements demonstrate independent MCP calls and persistent side-agent follow-up before main completion.
tags: [sse, side-agent, concurrency, mcp, verification]
timestamp: 2026-11-05
---

# Verified

Actual installed Claude2.1.310, isolated config/workspace, owned loopback model/MCP fixture. Main stream intentionally runs30seconds. Opt-in probe plugin registers a background agent, discovers all parent MCP tools via tool.list, starts side after1second, and resumes the same agent after first completion with session.send.

Run `.local/side-actor-bcd0ea34`:
- Main started; side model request begins during main stream.
- Side calls actual fixture work_status and send_message twice (two event rounds), all main_busy=true.
- Same agentId a1360dda141a647bd completes both rounds with reason=answer and SIDE-DONE.
- session.send isDelivered=true is followed by an actual second completion (not merely admission).
- All parent MCP tools in this fixture are offered to side. Fixture only has two tools; this is not a production connector permission/routing proof.
- Side system/tools hashes stay stable across4model calls but differ from main. No parent-prefix sharing claim.
- Fixture returns no cache usage evidence; displayed cache counts0 are not evidence of production cache misses or savings.

# Not verified

Real parent Work board authorization/updates, external connector delivery, P1 scheduling, full/recent30percent modes, selected side model and beta publication remain unimplemented/unverified. Subsequent compact wire probe d29e36cc confirms an actual summary request (last user content contains native compact summary instructions plus the fixture's Preserve instruction) held20seconds while the same side agent resumed, called work_status/send_message and completed. This proves owned fixture overlap, not real production context compaction completion or Work authorization. A plugin invoking $.session.compact does not hear its own session.compact hook, so the successful test sends side input from an independent lifecycle timer; prior hook-only probes failed to schedule that event and must not be treated as engine-side blockage. Probe is not a shipped feature.

# Preserved evidence

Owned probe harness `.local/side_actor_capability.py`; opt-in mod under C:\\Users\\djlov\\.claude\\dev-mods\\f129ddaa-21b3-4513-a939-9e349231c6c8\\side-actor-probe, guarded by SIDE_ACTOR_PROBE_ENABLED=1. Result and terminal source are in the run directory. Earlier run5945b4df had a fixture tool-result detection bug and does not prove persistent follow-up;92574325 and bcd0ea34 corrected it and observed actual completion/resume.
