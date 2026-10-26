---
type: Diagnostic
title: Compaction pre-header client timeout
description: Native compaction blocks the HTTP response while sequential summaries run.
tags: [compaction, timeout, streaming]
timestamp: 2026-10-22
---

# Evidence

Celly release 058ff68 starts compaction at 15:53:27 and 16:08:33;
the CLI records Request timed out at 15:59:19 and 16:14:28 while router
summary requests continue. This is not proof of successful checkpoint completion.

The Messages route calls cap_body synchronously before downstream headers.
The baseline real socket regression reproduces header wait timeout with a
200ms preparation and 40ms client deadline; nonstream behavior remains separate.
Early-start regression tests fail before implementation because no semantic
start callback exists.

# Contract

Open early SSE only when actual local streaming semantic work is admitted.
Only protocol ping frames until real upstream output. Errors after commitment
must be stream errors, never duplicate HTTP status or synthetic success.
Cancellation prevents further summary calls; blocked I/O remains bounded by
its existing timeout. Preserve whole source coverage and no excerpt fallback.

# References

- https://platform.claude.com/docs/en/api/messages-streaming
- https://code.claude.com/docs/en/env-vars (API_TIMEOUT_MS header deadline and five-minute body idle timeout)

# Installed CLI evidence

Native Claude Code 2.1.308 (sha256 c9796ba22a9221586b36371b36f5b66198d66eb4badf6a4cf4a5f7b07becea53),
isolated temp config and loopback fixture ([summary](evidence/long-365s/summary.json)):

- API_TIMEOUT_MS=10000, headers withheld 20 s: exit 1 with a timeout at 10.8 s.
- Same timeout, immediate SSE headers and a ping every 5 s, real message at 365 s:
  exit 0, response returned at 365.6 s, 73 pings, max gap 5.02 s.

The binary also bounds silent-progress heartbeats (`jhr=30` consecutive synthetic
pings); this release does not claim support for arbitrarily long compaction.

# Change

- context_compaction: `_context_compact_on_semantic_start` fires after full map
  preflight, before the first billable summary. Cancellation is checked before
  every map, retry and reduce step and wins over transport-retry classification.
- streaming_anthropic: `SemanticCompactionSSE` commits 200 event-stream once, sends
  documented `ping` frames from a heartbeat sidecar (shared 15 s keepalive policy),
  and hands the writer to the relay after a bounded join.
- claude_router: installs the hook only for local, client-streaming, non-native
  requests. After commitment, HTTP and route errors become one SSE `error` event;
  non-SSE upstream replies fail closed; 429 failover is not replayed. The final
  open after semantic work runs in its own zero-retry scope, because dispatch's
  60 s pre-output retry budget has expired by then (negative control reproduces
  "transient retry deadline reached before open").

# Verification

[Focused run](evidence/focused-tests.txt): 16 exact test files, 234 tests, OK.
New real-socket file `tests/test_claude_compaction_liveness.py` (16 tests) covers
the baseline header timeout, early ping, periodic pings past a short idle timeout,
a real semantic map starting the stream, post-commit HTTP/semantic/JSON errors,
disconnect cancellation before the final open, stale retry budget, and unchanged
nonstream, ordinary-stream, native Anthropic and remote-bridge paths.
Ruff and documentation metadata checks pass.

# Status

[Released and installed](release.md). No live session restarted, injected or mutated. Celly recovery is
not claimed until a completed checkpoint is observed after its next launch.
