---
type: evidence-note
title: Raw a1 transcript withheld
description: Where the raw rework-a1 subagent transcript of run-20261112-044325 is kept and why it is not in docs/ (it contains a dead owned router worker_token).
status: withheld
timestamp: 2026-11-12T14:50:28Z
tags: [work-parallel, main-review, e2e, evidence, redaction]
---

# Raw a1 transcript withheld

The raw subagent transcript `agent-a70ec164d860b5f7b.jsonl` (sha256 `e702e922c9dfec98b1153df3f29e2be3bc4ebe19ff0effc9e7e9b80d130a9811`) of rework attempt a1 was moved out of docs/.
Reason: it contains the worker_token of this run's owned router. That router is dead and its temp config is gone, but docs/ is committed.

Raw copy location (uncommitted): `.local/tmp/work-parallel/.local/evidence-private/run-20261112-044325/rework-a1-transcript/agent-a70ec164d860b5f7b.jsonl`, relative to the ciel-runtime root.
The redaction script `redact_worker_token.py` is there too, with that folder's own SHA256SUMS.

What remains here:
- `agent-a70ec164d860b5f7b.redacted.jsonl`: the raw transcript with the token value (64 hex, 3 occurrences) replaced by `<redacted worker_token sha256:<16 hex>>`. Nothing else is changed.
- `agent-a70ec164d860b5f7b.meta.json`: the engine's subagent meta, copied verbatim.
